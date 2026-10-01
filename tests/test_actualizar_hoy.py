"""Puesta al día incremental ("Actualizar hoy").

Cubre: planner por niveles (brecha chica vs app meses sin abrirse), watermark
de días (day_state), borrado diario acotado al día, fmax endurecido, re-login
tras SessionLost y detección (no borrado) de solapamientos.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.core import ventas_db
from src.core.capture_service import (
    CaptureService,
    planificar_puesta_al_dia,
)
from src.core.intranet_client import SessionLost

HOY = date(2026, 9, 28)


def _fila(fecha, nro, mes_ref, vendedor="178", sku="02211"):
    from src.core.xls_processor import derivar_campos

    v = {
        "id_articulo": sku,
        "id_linea": "01",
        "id_cliente": "00004884",
        "tpo_doc": "F01",
        "serie_doc": "001",
        "nro_doc": nro,
        "fecha_orig": fecha,
        "mes_ref": mes_ref,
        "cantidad": 1.0,
        "soles": 10.0,
        "anho": 2026,
        "mes": 9,
        "id_vendedor": vendedor,
        "nom_vendedor": "MILCA",
        "folio_unico": "",
    }
    derivar_campos(v)
    return v


def _vender(conn, fecha, nro, mes_ref, **kw):
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
            nro,
            fecha,
            mes_ref,
            1.0,
            10.0,
            2026,
            9,
            kw.get("vendedor", "178"),
            "MILCA",
            kw.get("folio", ""),
        ),
    )


class TestPlanner:
    def test_brecha_chica_todo_diario(self):
        meses, dias = planificar_puesta_al_dia("2026-09-28", HOY)
        assert meses == []
        assert dias[0] == "2026-09-21" and dias[-1] == "2026-09-28"
        assert len(dias) == 8

    def test_brecha_grande_mensual_mas_cola(self):
        meses, dias = planificar_puesta_al_dia("2026-06-25", HOY)
        assert meses == ["2026-06", "2026-07", "2026-08"]
        assert dias[0] == "2026-09-01" and dias[-1] == "2026-09-28"
        assert len(dias) == 28
        # Sin solapamiento: ningún día de la cola está en un mes del backfill.
        assert not any(d.startswith(tuple(meses)) for d in dias)

    def test_corte_siempre_en_borde_de_mes(self):
        meses, dias = planificar_puesta_al_dia("2026-03-10", date(2026, 10, 5))
        assert "2026-09" not in meses
        assert dias[0] == "2026-09-01" and dias[-1] == "2026-10-05"

    def test_fmax_futuro_no_congela(self):
        meses, dias = planificar_puesta_al_dia("2027-05-01", HOY)
        assert meses == []
        assert dias[0] == "2026-09-21" and dias[-1] == "2026-09-28"

    def test_fmax_vacio_usa_cola(self):
        meses, dias = planificar_puesta_al_dia(None, HOY)
        assert meses == []
        assert dias[-1] == "2026-09-28" and len(dias) == 15 + 7 + 1

    def test_fmax_invalido_no_revienta(self):
        meses, dias = planificar_puesta_al_dia("no-fecha", HOY)
        assert meses == []
        assert dias[-1] == "2026-09-28"

    def test_cobertura_continua_sin_huecos(self):
        meses, dias = planificar_puesta_al_dia("2026-06-25", HOY)
        cubiertos = {f"{m}-01" for m in meses} | set(dias)
        # El primer día cubierto es el inicio (fmax - overlap).
        assert min(cubiertos) <= "2026-06-18"
        assert max(cubiertos) == "2026-09-28"


class TestDayState:
    def test_record_deja_provisional(self, tmp_db):
        conn = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        _vender(conn, "2026-09-28", "1", "2026-09-28")
        conn.commit()
        n, tot = ventas_db.record_day_capture(conn, "2026-09-28", est)
        assert (n, tot) == (1, 10.0)
        r = ventas_db.dias_sin_cerrar(est, "2026-09-28", "2026-09-28")
        assert r == ["2026-09-28"]
        est.close()

    def test_cerrar_respeta_umbral(self, tmp_db):
        est = ventas_db.connect_estado()
        for d in ("2026-09-10", "2026-09-13", "2026-09-14"):
            est.execute("INSERT INTO day_state (dia, estado) VALUES (?, 'provisional')", (d,))
        est.commit()
        n = ventas_db.marcar_dias_cerrados(est, 15, "2026-09-28")
        assert n == 1  # solo el 10 (< 28-15=13); el 13 no es < 13
        # El 10 quedó cerrado; el 11 y 12 (nunca descargados) también pendientes.
        assert ventas_db.dias_sin_cerrar(est, "2026-09-10", "2026-09-14") == [
            "2026-09-11",
            "2026-09-12",
            "2026-09-13",
            "2026-09-14",
        ]
        est.close()

    def test_recapturar_no_reabre_cerrado(self, tmp_db):
        conn = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        _vender(conn, "2026-09-10", "1", "2026-09-10")
        est.execute("INSERT INTO day_state (dia, estado) VALUES ('2026-09-10', 'cerrado')")
        est.commit()
        ventas_db.record_day_capture(conn, "2026-09-10", est)
        r = est.execute("SELECT estado FROM day_state WHERE dia = '2026-09-10'").fetchone()[0]
        assert r == "cerrado"
        est.close()

    def test_resumen_captura(self, tmp_db):
        est = ventas_db.connect_estado()
        est.execute(
            "INSERT INTO day_state (dia, estado, filas) VALUES "
            "('2026-09-20', 'cerrado', 50), ('2026-09-28', 'provisional', 184)"
        )
        est.commit()
        r = ventas_db.resumen_captura(est)
        assert r["completado_hasta"] == "2026-09-20"
        assert r["n_abiertos"] == 1 and r["abiertos"][0]["dia"] == "2026-09-28"
        est.close()

    def test_resumen_sin_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "vacia"))
        r = ventas_db.resumen_captura()
        assert r["completado_hasta"] is None and r["abiertos"] == []


class TestBorradoDiario:
    def _dias(self, conn):
        return {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT substr(fecha_orig, 1, 10), COUNT(*) FROM ventas GROUP BY 1"
            ).fetchall()
        }

    def test_diaria_reemplaza_mensual_del_mismo_dia(self, tmp_db):
        """Transición mensual→diaria: el chunk diario absorbe el día sin duplicar."""
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(
            conn, [_fila("2026-09-22", str(i), "2026-09") for i in range(5)], label="2026-09"
        )
        assert self._dias(conn) == {"2026-09-22": 5}
        ventas_db.insert_ventas(
            conn, [_fila("2026-09-22", str(i), "2026-09") for i in range(7)], label="2026-09-22"
        )
        assert self._dias(conn) == {"2026-09-22": 7}
        assert conn.execute("SELECT COUNT(DISTINCT mes_ref) FROM ventas").fetchone()[0] == 1

    def test_diaria_no_toca_otro_dia(self, tmp_db):
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(conn, [_fila("2026-09-22", "a", "2026-09")], label="2026-09-22")
        ventas_db.insert_ventas(conn, [_fila("2026-09-23", "b", "2026-09")], label="2026-09-23")
        ventas_db.insert_ventas(conn, [_fila("2026-09-22", "c", "2026-09")], label="2026-09-22")
        assert self._dias(conn) == {"2026-09-22": 1, "2026-09-23": 1}

    def test_mensual_acumula_dias_y_reemplaza_presentes(self, tmp_db):
        """F6: el mensual ya no borra el mes entero; acumula días y reemplaza
        solo los presentes (un export parcial no se come lo ausente)."""
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(
            conn, [_fila("2026-09-05", str(i), "2026-09") for i in range(3)], label="2026-09"
        )
        ventas_db.insert_ventas(
            conn, [_fila("2026-09-06", str(i), "2026-09") for i in range(2)], label="2026-09"
        )
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 5
        # Re-insertar el día 05 con 1 fila reemplaza solo ese día.
        ventas_db.insert_ventas(conn, [_fila("2026-09-05", "x", "2026-09")], label="2026-09")
        assert self._dias(conn) == {"2026-09-05": 1, "2026-09-06": 2}


class TestSeguridad:
    def test_fmax_segura_topa_futuro(self, tmp_db):
        conn = ventas_db.get_conn()
        _vender(conn, "2027-01-01", "x", "2027-01")
        _vender(conn, "2026-09-28", "y", "2026-09-28")
        conn.commit()
        assert ventas_db.fecha_max_segura(conn) == date.today().isoformat()

    def test_fmax_segura_vacia(self, tmp_db):
        assert ventas_db.fecha_max_segura(ventas_db.get_conn()) is None

    def test_duplicados_cruzados_solo_entre_labels(self, tmp_db):
        conn = ventas_db.get_conn()
        # Misma factura, 38 líneas, un solo label: legítimo, no cuenta.
        for i in range(38):
            _vender(conn, "2022-10-20", f"L{i}", "2022-10", folio="F01/201/194999")
        # Solapamiento mensual/diario: sí cuenta (1 par).
        _vender(conn, "2026-09-22", "m1", "2026-09", folio="F01/001/7")
        _vender(conn, "2026-09-22", "m1", "2026-09-22", folio="F01/001/7")
        conn.commit()
        assert ventas_db.contar_duplicados_cruzados(conn, "2020-01-01", "2030-01-01") == 1
        assert ventas_db.contar_duplicados_cruzados(conn, "2026-09-01", "2026-09-30") == 1
        assert ventas_db.contar_duplicados_cruzados(conn, "2022-01-01", "2022-12-31") == 0


CSV = (
    "ANHO,MES,ID_CLIENTE,DOC_CLIENTE,NOM_CLIENTE,ID_LINEA,NOM_LINEA,"
    "ID_ARTICULO,NOM_ARTICULO,ID_VENDEDOR,NOM_VENDEDOR,TPO_DOC,SERIE_DOC,"
    "NRO_DOC,FECHA_ORIG,CANTIDAD,SOLES\r\n"
    "2026,9,68414,20100047218,CLIENTE DEMO SAC,01,GASEOSAS,"
    "02211,GASEOSA 3L,01178,MILCA,F01,001,457996,28/09/2026,10,250.00\r\n"
)


class TestReintentoSesion:
    def test_capture_chunk_reloguea_una_vez(self, tmp_db, monkeypatch):
        from pathlib import Path

        svc = CaptureService()
        llamadas = {"dl": 0, "login": 0, "proc": 0}

        def _dl(client, chunk):
            llamadas["dl"] += 1
            if llamadas["dl"] == 1:
                raise SessionLost("sesión caída")
            return CSV.encode(), "csv", Path("ventas_2026-09-28.csv")

        class _Cli:
            def login(self):
                llamadas["login"] += 1

        def _proc(conn, chunk, payload, kind, raw_path, conn_estado):
            llamadas["proc"] += 1
            return 7

        monkeypatch.setattr(svc, "_download_chunk", _dl)
        monkeypatch.setattr(svc, "_process_chunk", _proc)
        est = ventas_db.connect_estado()
        n = svc._capture_chunk(
            ventas_db.get_conn(), _Cli(), SimpleNamespace(label="2026-09-28"), est
        )
        assert n == 7
        assert llamadas == {"dl": 2, "login": 1, "proc": 1}
        est.close()

    def test_segunda_caida_propaga(self, tmp_db, monkeypatch):
        svc = CaptureService()

        def _dl(client, chunk):
            raise SessionLost("sesión caída")

        class _Cli:
            def login(self):
                pass

        monkeypatch.setattr(svc, "_download_chunk", _dl)
        est = ventas_db.connect_estado()
        with pytest.raises(SessionLost):
            svc._capture_chunk(
                ventas_db.get_conn(), _Cli(), SimpleNamespace(label="2026-09-28"), est
            )
        est.close()


class TestProcessChunkWatermark:
    def test_chunk_diario_anota_day_state(self, tmp_db):
        from pathlib import Path

        svc = CaptureService()
        conn = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        n = svc._process_chunk(
            conn,
            SimpleNamespace(label="2026-09-28"),
            CSV.encode(),
            "csv",
            Path("ventas_2026-09-28.csv"),
            est,
        )
        assert n == 1
        v = conn.execute("SELECT id_vendedor, mes_ref FROM ventas").fetchone()
        assert tuple(v) == ("178", "2026-09")
        r = est.execute("SELECT estado, filas FROM day_state WHERE dia = '2026-09-28'").fetchone()
        assert tuple(r) == ("provisional", 1)
        est.close()


class TestConexionCacheada:
    def test_get_conn_reabre_si_la_cerraron(self, tmp_db):
        c1 = ventas_db.get_conn()
        c1.execute("SELECT 1").fetchone()
        c1.close()  # simula el planner cerrando sin expulsar (bug real)
        c2 = ventas_db.get_conn()
        # No devuelve la muerta: reabre sola.
        assert c2.execute("SELECT 1").fetchone()[0] == 1

    def test_planner_mas_captura_mismo_hilo(self, tmp_db):
        """update_from_last() y capture_chunks() en el mismo hilo: el segundo
        no debe recibir 'closed database' (regresión del fallo real)."""
        from pathlib import Path

        svc = CaptureService()
        ventas_db.get_conn()
        ventas_db.reset_connections()  # lo que ahora hace el planner al salir
        conn2 = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        n = svc._process_chunk(
            conn2, SimpleNamespace(label="2026-09-28"), CSV.encode(), "csv", Path("x.csv"), est
        )
        assert n == 1
        est.close()


class TestGuardaVolumen:
    """3 corridas el mismo día: cada una borra hoy y recarga (idempotente).
    Si una descarga trae mucho menos de lo guardado, se aborta el chunk
    (queda fallido) en vez de achicar el día."""

    def _csv_n(self, n, fecha="28/09/2026", base_nro=500000):
        filas = "\r\n".join(
            f"2026,9,68414,20100047218,CLIENTE DEMO SAC,01,GASEOSAS,"
            f"02211,GASEOSA 3L,01178,MILCA,F01,001,{base_nro + i},{fecha},10,250.00"
            for i in range(n)
        )
        return (
            "ANHO,MES,ID_CLIENTE,DOC_CLIENTE,NOM_CLIENTE,ID_LINEA,NOM_LINEA,"
            "ID_ARTICULO,NOM_ARTICULO,ID_VENDEDOR,NOM_VENDEDOR,TPO_DOC,SERIE_DOC,"
            f"NRO_DOC,FECHA_ORIG,CANTIDAD,SOLES\r\n{filas}\r\n"
        )

    def test_tres_corridas_convergen(self, tmp_db):
        from pathlib import Path

        svc = CaptureService()
        conn = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        for n in (5, 7, 6):
            r = svc._process_chunk(
                conn,
                SimpleNamespace(label="2026-09-28"),
                self._csv_n(n).encode(),
                "csv",
                Path("ventas_2026-09-28.csv"),
                est,
            )
            assert r == n
        # Gana la última descarga, sin duplicar ni acumular.
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 6
        est.close()

    def test_descarga_parcial_aborta_y_conserva(self, tmp_db):
        from pathlib import Path

        from src.core.intranet_client import IntranetError

        svc = CaptureService()
        conn = ventas_db.get_conn()
        est = ventas_db.connect_estado()
        svc._process_chunk(
            conn,
            SimpleNamespace(label="2026-09-28"),
            self._csv_n(10).encode(),
            "csv",
            Path("ventas_2026-09-28.csv"),
            est,
        )
        with pytest.raises(IntranetError, match="parcial"):
            svc._process_chunk(
                conn,
                SimpleNamespace(label="2026-09-28"),
                self._csv_n(3).encode(),
                "csv",
                Path("ventas_2026-09-28.csv"),
                est,
            )
        # Lo guardado sobrevive intacto.
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 10
        est.close()


class TestUpdateFromLast:
    def _ventana(self):
        h = date.today()
        return [(h - timedelta(days=i)).isoformat() for i in range(7, -1, -1)]

    def _sembrar_ventana(self, conn, dias, label_por_dia=True):
        est = ventas_db.connect_estado()
        for d in dias:
            _vender(conn, d, f"n-{d}", d if label_por_dia else "2026-09")
            est.execute("INSERT OR IGNORE INTO day_state (dia, estado) VALUES (?, 'cerrado')", (d,))
        conn.commit()
        est.commit()
        est.close()

    def test_todo_cerrado_no_toca_red(self, tmp_db):
        conn = ventas_db.get_conn()
        dias = self._ventana()
        self._sembrar_ventana(conn, dias)
        svc = CaptureService()
        r = svc.update_from_last()
        assert r.get("detail") == "DB ya actualizada"
        assert r.get("filas", 0) == 0

    def test_forzar_pasa_la_ventana_completa(self, tmp_db, monkeypatch):
        conn = ventas_db.get_conn()
        dias = self._ventana()
        self._sembrar_ventana(conn, dias)
        visto = {}

        def _fake(chunks):
            visto["labels"] = [c.label for c in chunks]
            return {"filas": 0}

        svc = CaptureService()
        monkeypatch.setattr(svc, "capture_chunks", _fake)
        svc.update_from_last(forzar=True)
        assert visto["labels"] == dias

    def test_abiertos_llegan_a_captura(self, tmp_db, monkeypatch):
        conn = ventas_db.get_conn()
        dias = self._ventana()
        self._sembrar_ventana(conn, dias)
        # Reabrir dos días: deben ser los únicos planificados.
        est = ventas_db.connect_estado()
        est.execute(
            "UPDATE day_state SET estado = 'provisional' WHERE dia IN (?, ?)", (dias[0], dias[-1])
        )
        est.commit()
        est.close()
        visto = {}

        def _fake(chunks):
            visto["labels"] = [c.label for c in chunks]
            return {"filas": 0}

        svc = CaptureService()
        monkeypatch.setattr(svc, "capture_chunks", _fake)
        svc.update_from_last()
        assert visto["labels"] == [dias[0], dias[-1]]
