"""Tests del servicio de captura: lock, credenciales, estrategia de chunks."""

import threading
from datetime import date, timedelta

import pytest

from src.core import ventas_db
from src.core.capture_service import CaptureLockError, CaptureService


class TestLock:
    def test_acquire_y_release(self, tmp_db):
        svc = CaptureService()
        svc.acquire_lock()
        assert svc._lock_path.exists()
        # Segundo acquire debe fallar
        with pytest.raises(CaptureLockError):
            CaptureService().acquire_lock()
        svc.release_lock()
        assert not svc._lock_path.exists()

    def test_stale_lock_se_elimina(self, tmp_db):
        import os
        import time

        svc = CaptureService()
        ventas_db.raw_dir().mkdir(parents=True, exist_ok=True)
        old = time.time() - 4 * 3600
        os.utime(
            svc._lock_path, (old, old)
        ) if svc._lock_path.exists() else svc._lock_path.write_text("1")
        os.utime(svc._lock_path, (old, old))
        svc.acquire_lock()  # no debe lanzar
        svc.release_lock()


class TestCredenciales:
    def test_save_y_credentials(self, tmp_db):
        CaptureService.save_credentials("usr", "pwd")
        user, pwd = CaptureService.credentials()
        assert (user, pwd) == ("usr", "pwd")
        assert CaptureService.has_credentials()

    def test_env_sobre_config(self, tmp_db, monkeypatch):
        CaptureService.save_credentials("usr", "pwd")
        monkeypatch.setenv("G360_INTRANET_USER", "env_user")
        user, _ = CaptureService.credentials()
        assert user == "env_user"

    def test_sin_credenciales(self, tmp_db, monkeypatch):
        monkeypatch.delenv("G360_INTRANET_USER", raising=False)
        monkeypatch.delenv("G360_INTRANET_PASS", raising=False)
        assert not CaptureService.has_credentials()


class TestProgreso:
    def test_callback_recibe_progreso(self, tmp_db, monkeypatch):
        eventos = []

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def ensure_logged_in(self):
                pass

            def close(self):
                pass

        svc = CaptureService(progress_cb=lambda s, m, p: eventos.append((s, m, p)))
        # Sin red real: range vacio no produce chunks, pero si inicio/fin
        falso_fin = date.today() - timedelta(days=1)
        monkeypatch.setattr("src.core.capture_service.IntranetClient", FakeClient)
        try:
            resumen = svc.capture_range(falso_fin, falso_fin)
        finally:
            svc.release_lock()
        assert resumen["filas"] == 0
        stages = [e[0] for e in eventos]
        assert "inicio" in stages
        assert "preparacion" in stages
        assert "fin" in stages
        assert "preparacion_s" in resumen


class TestEstrategia:
    def test_abort_event_detiene(self, tmp_db, monkeypatch):
        ev = threading.Event()
        ev.set()

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def ensure_logged_in(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr("src.core.capture_service.IntranetClient", FakeClient)
        svc = CaptureService(abort_event=ev)
        try:
            resumen = svc.capture_range(date(2024, 1, 1), date(2024, 1, 2))
        finally:
            svc.release_lock()
        assert resumen.get("abortado")

    def test_skip_meses_existentes(self, populated_db, monkeypatch):
        """capture_range con skip_existing omite meses completos ya en SQLite."""
        from src.core.capture_service import CaptureService
        from src.core.intranet_client import month_chunks

        planificados: list = []

        def fake_capture_chunks(self, chunks):
            planificados.extend(c.label for c in chunks)
            return {
                "filas": 0,
                "chunks_ok": [],
                "chunks_fallidos": [],
                "dias_fallidos": [],
                "chunks_omitidos": 0,
            }

        monkeypatch.setattr(CaptureService, "capture_chunks", fake_capture_chunks)
        svc = CaptureService()
        from datetime import date as d

        svc.capture_range(d(2024, 1, 1), d(2024, 3, 31), skip_existing=True)
        # populated_db: 2024-01 (al dia 15) y 2024-02 (al dia 20) son INCOMPLETOS
        # -> se re-planifican; 2024-03 faltante -> planificado.
        assert "2024-03" in planificados
        assert "2024-01" in planificados
        assert "2024-02" in planificados
        # Con skip_existing=False el plan es identico (todos los meses del rango)
        planificados.clear()
        svc.capture_range(d(2024, 1, 1), d(2024, 3, 31), skip_existing=False)
        assert planificados == [c.label for c in month_chunks(d(2024, 1, 1), d(2024, 3, 31))]

    def test_update_planifica_por_niveles(self, populated_db, monkeypatch):
        """update_from_last con brecha grande: backfill mensual + cola diaria.

        populated_db tiene fmax=2024-02-20: la ventana de 7 días ya no alcanza,
        así que el mes de fmax se recubre mensual y la cola reciente va diaria.
        """
        from src.core.capture_service import CaptureService

        planificados: list = []

        def fake_capture_chunks(self, chunks):
            planificados.extend(c.label for c in chunks)
            return {
                "filas": 0,
                "chunks_ok": [],
                "chunks_fallidos": [],
                "dias_fallidos": [],
                "chunks_omitidos": 0,
            }

        monkeypatch.setattr(CaptureService, "capture_chunks", fake_capture_chunks)
        svc = CaptureService()
        svc.update_from_last()
        from datetime import date as d

        mensuales = [l for l in planificados if len(l) == 7]
        diarios = [l for l in planificados if len(l) == 10]
        assert mensuales, "la brecha grande necesita backfill mensual"
        assert "2024-02" in mensuales, "el mes de fmax se recubre"
        assert diarios, "la cola reciente va diaria"
        assert diarios[-1] == d.today().strftime("%Y-%m-%d")
        assert diarios[0].endswith("-01"), "la cola arranca en borde de mes"
        assert not any(x.startswith(tuple(mensuales)) for x in diarios), "sin solape"


class TestActualizarHoy:
    """Escenarios del botón 'Actualizar a hoy' de la card de salud."""

    def _mock(self, monkeypatch):
        planificados: list = []

        def fake(self, chunks):
            planificados.extend(c.label for c in chunks)
            return {
                "filas": 0,
                "chunks_ok": [],
                "chunks_fallidos": [],
                "dias_fallidos": [],
                "chunks_omitidos": 0,
            }

        monkeypatch.setattr(CaptureService, "capture_chunks", fake)
        return planificados

    def _sembrar(self, fmax_iso):
        conn = ventas_db.get_conn()
        conn.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
            "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes, id_vendedor, "
            "nom_vendedor, folio_unico) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "02211",
                "01",
                "00004884",
                "F01",
                "001",
                "1",
                fmax_iso,
                fmax_iso[:7],
                1.0,
                10.0,
                int(fmax_iso[:4]),
                int(fmax_iso[5:7]),
                "178",
                "MILCA",
                "F01/001/1",
            ),
        )
        conn.commit()

    def test_caso_1_brecha_chica_todo_diario(self, tmp_db, monkeypatch):
        """App abierta hace días: solo chunks diarios, sin backfill mensual."""
        from datetime import date as d

        hoy = d.today()
        fmax = (hoy - timedelta(days=3)).isoformat()
        self._sembrar(fmax)
        plan = self._mock(monkeypatch)
        CaptureService().update_from_last()
        diarios = [l for l in plan if len(l) == 10]
        mensuales = [l for l in plan if len(l) == 7]
        assert diarios, "debe haber chunks diarios en la ventana"
        assert not mensuales, "brecha chica: nada mensual"
        assert plan[0] == (date.fromisoformat(fmax) - timedelta(days=7)).isoformat()
        assert plan[-1] == hoy.isoformat()

    def test_caso_2_brecha_grande_niveles(self, tmp_db, monkeypatch):
        """App meses sin abrirse: backfill mensual + cola diaria, sin solape."""
        from datetime import date as d

        hoy = d.today()
        fmax = (hoy - timedelta(days=90)).isoformat()
        self._sembrar(fmax)
        plan = self._mock(monkeypatch)
        CaptureService().update_from_last()
        diarios = [l for l in plan if len(l) == 10]
        mensuales = [l for l in plan if len(l) == 7]
        assert mensuales, "brecha grande: backfill mensual"
        assert diarios and diarios[-1] == hoy.isoformat()
        assert diarios[0].endswith("-01"), "la cola arranca en borde de mes"
        assert not any(x.startswith(tuple(mensuales)) for x in diarios)

    def test_caso_3_todo_cerrado_no_descarga(self, tmp_db, monkeypatch):
        """Días ya cerrados se omiten: si no hay nada pendiente, no toca la red."""
        from datetime import date as d

        hoy = d.today()
        dias = [(hoy - timedelta(days=i)).isoformat() for i in range(7, -1, -1)]
        est = ventas_db.connect_estado()
        for x in dias:
            self._sembrar(x)
            est.execute("INSERT OR IGNORE INTO day_state (dia, estado) VALUES (?, 'cerrado')", (x,))
        est.commit()
        est.close()
        plan = self._mock(monkeypatch)
        r = CaptureService().update_from_last()
        assert plan == []
        assert r.get("detail") == "DB ya actualizada"


class TestCaptureStatus:
    """Estado global visible desde la UI (estilo Tauri paso a paso)."""

    def setup_method(self):
        from src.core.capture_service import CAPTURE_STATUS

        self.st = CAPTURE_STATUS

    def test_ciclo_completo(self):
        self.st.begin("Primera carga", 3)
        snap = self.st.snapshot()
        assert snap["running"] and snap["total"] == 3 and snap["mode"] == "Primera carga"
        self.st.chunk_start("2024-01", 1, 3)
        self.st.progress("chunk", "descargando...", 0.1)
        snap = self.st.snapshot()
        assert snap["current"] == "2024-01" and abs(snap["pct"] - 0.1) < 1e-9
        self.st.chunk_ok("2024-01", 100)
        self.st.chunk_start("2024-02", 2, 3)
        self.st.chunk_fail("2024-02", "timeout en POST")
        self.st.chunk_ok("2024-03", 50)
        snap = self.st.snapshot()
        assert snap["filas"] == 150
        assert snap["ok"] == ["2024-01", "2024-03"]
        assert snap["fallidos"] == ["2024-02"]
        self.st.finish(150, snap["ok"], snap["fallidos"])
        snap = self.st.snapshot()
        assert not snap["running"] and snap["current"] == ""

    def test_begin_preserva_ultimo_mensaje(self):
        self.st.progress("plan", "Meses ya en SQLite (se omiten): 5")
        self.st.begin("Actualizar", 2)
        snap = self.st.snapshot()
        assert snap["running"] and "se omiten" in snap["message"]

    def test_abort_active_con_servicio(self):
        svc = CaptureService()
        self.st.set_service(svc)
        assert self.st.abort_active()
        assert svc.abort_event.is_set()
        self.st.finish(0, [], [])
        snap = self.st.snapshot()
        assert snap["has_service"] is False

    def test_snapshot_no_comparte_listas(self):
        self.st.begin("t", 1)
        self.st.chunk_ok("a", 1)
        snap = self.st.snapshot()
        snap["ok"].append("x")
        assert self.st.snapshot()["ok"] == ["a"]
        self.st.finish(1, ["a"], [])

    def test_limpiar_raw_borra_validados_y_conserva_fallidos(self, tmp_db):
        from datetime import date, timedelta

        raw = ventas_db.raw_dir()
        raw.mkdir(parents=True, exist_ok=True)
        hoy = date.today()
        hace8 = (hoy - timedelta(days=8)).strftime("%Y-%m-%d")
        hace1 = (hoy - timedelta(days=1)).strftime("%Y-%m-%d")
        f_mes_ok = raw / "ventas_2024-01.xls"
        f_mes_ok.write_bytes(b"x" * 100)
        f_mes_fail = raw / "ventas_2024-02.xls"
        f_mes_fail.write_bytes(b"x" * 100)
        f_dia_ok = raw / f"ventas_{hace8}.xls"
        f_dia_ok.write_bytes(b"x" * 100)
        f_split_ok = raw / "ventas_2024-01-split.csv"
        f_split_ok.write_bytes(b"x" * 100)
        f_reciente = raw / f"ventas_{hace1}.xls"
        f_reciente.write_bytes(b"x" * 100)
        f_hoy = raw / f"ventas_{hoy.strftime('%Y-%m-%d')}.xls"
        f_hoy.write_bytes(b"x" * 100)

        svc = CaptureService()
        resumen = {
            "chunks_ok": ["2024-01", hace8, hace1, hoy.strftime("%Y-%m-%d")],
            "chunks_fallidos": ["2024-02"],
            "dias_fallidos": [],
        }
        liberado = svc._limpiar_raw(resumen)

        assert not f_mes_ok.exists()  # mes validado -> borrado
        assert not f_dia_ok.exists()  # dia validado con 8 dias -> borrado
        assert not f_split_ok.exists()  # split de mes validado -> borrado
        assert f_mes_fail.exists()  # fallido -> conservado (re-parseo manual)
        assert f_reciente.exists()  # reciente (<7d) -> conservado
        assert f_hoy.exists()  # hoy -> conservado
        assert liberado == 300


class TestLockPID:
    def test_pid_muerto_rompe_lock(self, tmp_db):
        import json as _json
        import subprocess

        # PID real de un proceso ya terminado (garantizado muerto en Windows)
        p = subprocess.Popen(["cmd", "/c", "exit"])
        p.wait()
        assert p.poll() is not None

        svc = CaptureService()
        raw = ventas_db.raw_dir()
        raw.mkdir(parents=True, exist_ok=True)
        lock = raw / "capture.lock"
        lock.write_text(_json.dumps({"pid": p.pid, "ts": "2026-01-01T00:00:00"}))
        svc.acquire_lock()  # no debe lanzar: PID muerto -> lock roto
        svc.release_lock()
        assert not lock.exists()
