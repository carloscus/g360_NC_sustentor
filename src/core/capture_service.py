"""Servicio de captura intranet -> SQLite — port de src/capture.rs (g360-ventas-db).

Estrategia (v1, sin headless-Chrome ni splits Art):
  - Rango > 7 dias  -> chunks mensuales; <= 7 dias -> chunks diarios.
  - Por chunk: 3 intentos export D0; si falla, 1 intento grid L0 (CSV).
  - Mes que falla por completo -> reintentos diarios de ese mes.
  - Meses fallidos se marcan en data/failed_YYYY-MM.json para reintento manual.
  - Lock exclusivo en data/raw/capture.lock + abort cooperativo.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from src.core import ventas_db

from src.core.intranet_client import (
    IntranetClient,
    IntranetError,
    SessionLost,
    day_chunk,
    day_chunks,
    month_chunk,
    month_chunks,
)
from src.core.xls_processor import load_report_source, parse_report_rows, resolve_nc_nd_cross

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STALE_LOCK_HOURS = 3
LOCK_NAME = "capture.lock"

# Umbral de madurez de un día: pasado este lapso se considera cerrado y deja
# de re-descargarse. Cubre documentos con fecha vieja ingresados tarde.
DIAS_INMADUREZ_DEFAULT = 15
# Ventana de re-descarga alrededor de la última fecha (cubre ingresos tardíos).
DIAS_OVERLAP = 7


def _dias_entre(desde: date, hasta: date) -> list[date]:
    out: list[date] = []
    cur = desde
    while cur <= hasta:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def planificar_puesta_al_dia(
    fmax: str | None,
    hoy: date,
    dias_inmadurez: int = DIAS_INMADUREZ_DEFAULT,
    overlap: int = DIAS_OVERLAP,
) -> tuple[list[str], list[str]]:
    """Plan de puesta al día, puro y testeable. Devuelve (meses, dias).

    - Brecha chica (<= inmadurez + overlap): todo diario, preciso día por día.
    - Brecha grande (app meses sin abrirse): backfill mensual para lo viejo +
      cola diaria desde el 1ro del mes de corte. El corte cae en borde de mes
      para no partir un mes en dos labels (eso duplicaría sin dedup que lo
      limpie).
    - fmax futuro, vacío o inválido no congela ni revienta: se topa a hoy o se
      usa solo la cola reciente.
    """
    if not fmax:
        base = hoy - timedelta(days=dias_inmadurez)
    else:
        try:
            base = min(date.fromisoformat(str(fmax)[:10]), hoy)
        except ValueError:
            base = hoy - timedelta(days=dias_inmadurez)
    inicio = base - timedelta(days=overlap)
    if inicio > hoy:
        return ([], [])
    if (hoy - inicio).days <= dias_inmadurez + overlap:
        return ([], [d.isoformat() for d in _dias_entre(inicio, hoy)])
    corte = hoy - timedelta(days=dias_inmadurez)
    primero_cola = date(corte.year, corte.month, 1)
    dias = [d.isoformat() for d in _dias_entre(max(inicio, primero_cola), hoy)]
    meses: list[str] = []
    m = date(inicio.year, inicio.month, 1)
    while m < primero_cola:
        meses.append(f"{m.year}-{m.month:02d}")
        m = date(m.year + (m.month == 12), (m.month % 12) + 1, 1)
    return (meses, dias)


def _brief(err: Exception | None) -> str:
    """Mensaje de error corto para logs de UI (sin tracebacks)."""
    if err is None:
        return "?"
    s = str(err)
    return s if len(s) <= 120 else s[:117] + "..."


class CaptureStatus:
    """Estado global del proceso de captura, legible desde la UI en cualquier
    momento (aunque el dialog se haya cerrado). Thread-safe. Patron similar
    al get_capture_status de Tauri: la UI hace polling del snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self.service: "CaptureService | None" = None
        self._reset()

    def _reset(self):
        self.running = False
        self.mode = ""
        self.stage = ""
        self.message = ""
        self.message_ts = 0.0
        self.pct = 0.0
        self.current = ""  # chunk en curso (ej. "2024-05")
        self.index = 0
        self.total = 0
        self.filas = 0
        self.ok: list[str] = []
        self.fallidos: list[str] = []
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error = ""
        self.abortado = False

    def begin(self, mode: str, total: int, service=None):
        with self._lock:
            # Conservar el ultimo mensaje (ej. plan de meses omitidos) para la UI
            stage, message, mts = self.stage, self.message, self.message_ts
            self._reset()
            self.stage, self.message, self.message_ts = stage, message, mts
            self.running = True
            self.mode = mode
            self.total = total
            self.started_at = time.time()
            if service is not None:
                self.service = service

    def set_service(self, service):
        with self._lock:
            self.service = service

    def progress(self, stage: str, message: str, pct=None):
        with self._lock:
            self.stage = stage
            self.message = message
            self.message_ts = time.time()
            if pct is not None:
                self.pct = max(0.0, min(1.0, float(pct)))

    def chunk_start(self, label: str, index: int, total: int):
        with self._lock:
            self.current = label
            self.index = index
            self.total = total

    def chunk_ok(self, label: str, filas: int):
        with self._lock:
            self.ok.append(label)
            self.filas += filas
            self.current = ""

    def chunk_fail(self, label: str, err: str = ""):
        with self._lock:
            self.fallidos.append(label)
            self.current = ""
            if err:
                self.error = err

    def finish(self, filas: int, ok: list, fallidos: list, error: str = "", abortado: bool = False):
        with self._lock:
            self.running = False
            self.filas = filas
            self.ok = list(ok)
            self.fallidos = list(fallidos)
            self.error = error
            self.abortado = abortado
            self.finished_at = time.time()
            self.current = ""
            self.service = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "stage": self.stage,
                "message": self.message,
                "message_ts": self.message_ts,
                "pct": self.pct,
                "current": self.current,
                "index": self.index,
                "total": self.total,
                "filas": self.filas,
                "ok": list(self.ok),
                "fallidos": list(self.fallidos),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "abortado": self.abortado,
                "has_service": self.service is not None,
            }

    def abort_active(self) -> bool:
        """Aborta la captura en curso (cierra el socket HTTP)."""
        svc = self.service
        if svc is not None:
            svc.abort()
            return True
        return False


CAPTURE_STATUS = CaptureStatus()


class CaptureLockError(RuntimeError):
    """Otra captura esta en curso."""


def _pid_alive(pid: int) -> bool | None:
    """True si el proceso existe (Windows via ctypes). None = no se pudo determinar."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                k32.CloseHandle(h)
        # POSIX: senal 0 = solo existencia
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return None


def _lock_payload(path: Path) -> dict:
    """Lee el lock (JSON nuevo o PID plano legacy)."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("{"):
            return json.loads(text)
        return {"pid": int(text), "ts": None}
    except Exception:
        return {"pid": None, "ts": None}


class CaptureService:
    def __init__(self, progress_cb=None, abort_event: threading.Event | None = None):
        self.progress_cb = progress_cb
        self.abort_event = abort_event or threading.Event()
        self._lock_path = ventas_db.raw_dir() / LOCK_NAME
        self._client: IntranetClient | None = None  # para abort() instantaneo
        self.mode_label = "Descarga"  # etiqueta para CAPTURE_STATUS (la UI la personaliza)

    def abort(self) -> None:
        """Detener ya: marca el flag y cierra el cliente (rompe requests en vuelo)."""
        self.abort_event.set()
        cli = self._client
        if cli is not None:
            try:
                cli.close()
            except Exception:
                pass

    # ── Progreso ─────────────────────────────────────────────────────

    def _progress(self, stage: str, message: str, pct: float | None = None) -> None:
        CAPTURE_STATUS.progress(stage, message, pct)
        if self.progress_cb:
            try:
                self.progress_cb(stage, message, pct)
            except Exception:
                pass
        log.info("[%s] %s", stage, message)

    def _aborted(self) -> bool:
        return self.abort_event.is_set()

    # ── Lock exclusivo ───────────────────────────────────────────────

    def _break_stale_lock(self) -> None:
        p = self._lock_path
        if p.exists():
            age_h = (time.time() - p.stat().st_mtime) / 3600
            if age_h > STALE_LOCK_HOURS:
                log.warning("lock obsoleto (%.1fh) — eliminando", age_h)
                p.unlink(missing_ok=True)

    def acquire_lock(self) -> None:
        ventas_db.raw_dir().mkdir(parents=True, exist_ok=True)
        self._break_stale_lock()
        # Lock existente con PID vivo = captura real en curso
        if self._lock_path.exists():
            payload = _lock_payload(self._lock_path)
            pid = payload.get("pid")
            vivo = _pid_alive(int(pid)) if pid else None
            if vivo:
                raise CaptureLockError(
                    f"Otra captura esta en curso (PID {pid}). Espera a que termine o usa 'Detener'."
                )
            # PID muerto o indeterminado + lock viejo -> romper; indeterminado + reciente -> reintentar mas tarde
            age_h = (time.time() - self._lock_path.stat().st_mtime) / 3600
            if vivo is False or age_h > STALE_LOCK_HOURS:
                log.warning("lock de proceso muerto (pid=%s, %.1fh) — eliminando", pid, age_h)
                self._lock_path.unlink(missing_ok=True)
            else:
                raise CaptureLockError(
                    "Lock reciente sin PID legible — elimina data/raw/capture.lock si confirmas que no hay captura en curso."
                )
        # Crear lock con PID + timestamp (JSON)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(
                fd,
                json.dumps(
                    {"pid": os.getpid(), "ts": datetime.now().isoformat(timespec="seconds")}
                ).encode(),
            )
        finally:
            os.close(fd)

    def release_lock(self) -> None:
        self._lock_path.unlink(missing_ok=True)

    # ── Credenciales ─────────────────────────────────────────────────

    @staticmethod
    def credentials() -> tuple[str, str]:
        user = os.getenv("G360_INTRANET_USER", "")
        password = os.getenv("G360_INTRANET_PASS", "")
        if not user or not password:
            cfg = ventas_db.load_app_config()
            intranet = cfg.get("intranet", {}) or {}
            user = user or intranet.get("user", "")
            password = password or intranet.get("pass", "")
        return user, password

    @staticmethod
    def save_credentials(user: str, password: str) -> None:
        cfg = ventas_db.load_app_config()
        cfg.setdefault("intranet", {})
        cfg["intranet"]["user"] = user
        cfg["intranet"]["pass"] = password
        cfg["last_verified"] = time.time()
        ventas_db.save_app_config(cfg)

    @staticmethod
    def has_credentials() -> bool:
        user, password = CaptureService.credentials()
        return bool(user and password)

    # ── Token de la API Go ──────────────────────────────────────────

    @staticmethod
    def api_token() -> str:
        cfg = ventas_db.load_app_config()
        return str(cfg.get("api_token", "") or "")

    @staticmethod
    def save_api_token(token: str, user: str = "") -> None:
        cfg = ventas_db.load_app_config()
        cfg["api_token"] = token
        if user:
            cfg["api_user"] = user
        cfg["api_token_time"] = time.time()
        ventas_db.save_app_config(cfg)

    @staticmethod
    def is_api_token_valid(max_age_s: float = 86400.0) -> bool:
        cfg = ventas_db.load_app_config()
        if not cfg.get("api_token"):
            return False
        try:
            age = time.time() - float(cfg.get("api_token_time", 0) or 0)
        except (TypeError, ValueError):
            return False
        return age < max_age_s

    @staticmethod
    def refresh_api_token_best_effort(user: str, password: str) -> str:
        """Intenta login contra la API Go; nunca lanza. Devuelve sufijo de estado."""
        try:
            from src.core.api_auth import APIAuthClient, default_api_url
        except Exception:
            return ""
        try:
            result = APIAuthClient(default_api_url()).login(user, password)
        except Exception:
            return ""
        if result.success:
            CaptureService.save_api_token(result.token, result.user)
            return f" · API conectada como {result.user}"
        return f" · API no disponible ({result.message})"

    @staticmethod
    def verify_credentials_fresh(max_age_s: float = 86400.0) -> bool:
        """True si hubo verificacion exitosa reciente (< max_age_s)."""
        if not CaptureService.has_credentials():
            return False
        try:
            cfg = ventas_db.load_app_config()
            age = time.time() - float(cfg.get("last_verified", 0) or 0)
        except (TypeError, ValueError):
            return False
        return age < max_age_s

    # ── Descarga + parseo + insercion de un chunk ────────────────────

    def _download_chunk(self, client: IntranetClient, chunk) -> tuple[bytes, str, Path]:
        """Escalera de descarga para un chunk:
        1) Export D0 completo (2 intentos si mensual, 3 si diario: los timeouts
           de meses pesados se resuelven mejor partiendo, no reintentando)
        2) Split por articulo 2/4/8 partes (timeout del server en rangos pesados)
        3) Grid L0 (CSV) de respaldo
        Devuelve (payload, kind, raw_path) — payload normalizado si hubo split."""
        ventas_db.raw_dir().mkdir(parents=True, exist_ok=True)
        df, dt = chunk.to_url_params()
        es_mensual = len(chunk.label) == 7
        intentos = MAX_ATTEMPTS - 1 if es_mensual else MAX_ATTEMPTS
        last_err: Exception | None = None
        for attempt in range(1, intentos + 1):
            if self._aborted():
                raise IntranetError("abortado por usuario")
            try:
                self._progress("chunk", f"  intento {attempt}/{intentos}: export XLS {df} -> {dt}")
                # Heartbeat: durante un GET/POST largo (los rangos 2024 tardan
                # hasta 6 min en el primer request), avisar cada 60s que el
                # proceso sigue vivo — en consola, run_log y UI.
                t_req = [time.time()]
                hb_corriendo = [True]

                def _hb():
                    lim = "300s" if es_mensual else "420s"
                    while hb_corriendo[0]:
                        time.sleep(60)
                        if not hb_corriendo[0]:
                            break
                        sil = time.time() - t_req[0]
                        if sil >= 55:
                            fase = getattr(client, "fase_actual", "") or "solicitud en curso"
                            self._progress(
                                "heartbeat",
                                f"  {chunk.label}: solicitud en curso {sil:.0f}s ({fase}; timeout {lim})",
                                None,
                            )

                threading.Thread(target=_hb, daemon=True).start()
                # Mensual: timeout POST reducido (los normales toman ~134s; los
                # pesados como 2024-01 no responden ni en 7 min — mejor caer
                # pronto al reintento diario, que SI funciona).
                result = client.download_export(df, dt, timeout_post=300.0 if es_mensual else None)
                hb_corriendo[0] = False
                path = ventas_db.raw_dir() / f"ventas_{chunk.label}.xls"
                path.write_bytes(result.payload)
                self._progress(
                    "chunk",
                    f"  {chunk.label}: OK {len(result.payload):,} bytes "
                    f"(GET {result.t_get:.0f}s + POST {result.t_post:.0f}s)",
                )
                return result.payload, result.kind, path
            except (SessionLost, IntranetError, RuntimeError, OSError) as e:
                # RuntimeError/OSError: cliente cerrado por abort() o socket roto
                hb_corriendo[0] = False
                last_err = e
                timeout = "timeout" in str(e).lower()
                if timeout and attempt < intentos:
                    # Timeout = rango demasiado pesado para el server: reintentar
                    # el mismo tamanio no ayuda — partir YA (escalera de split).
                    self._progress(
                        "chunk",
                        f"  {chunk.label}: timeout ({_brief(e)}) — el rango excede al server, partiendo en lugar de reintentar",
                    )
                    break
                log.warning("D0 intento %d fallo: %s", attempt, e)
                time.sleep(min(2 * attempt, 6))
        # Escalon 2: split por articulo (2/4/8 partes)
        if self._aborted():
            raise IntranetError("abortado por usuario")
        # Timeout en el mes completo => el server no aguanta ni ~1/8 del rango
        # (verificado con 2024-01: splits 2/4/8 tambien dan timeout). El camino
        # fiable es el reintento diario del llamador, no quemar tiempo aqui.
        if es_mensual and "timeout" in str(last_err).lower():
            raise IntranetError(
                f"chunk {chunk.label} excede el timeout del server — reintento diario automatico"
            )
        try:
            self._progress(
                "chunk",
                f"  export completo fallo ({_brief(last_err)}) — split por articulo 2/4/8...",
            )
            parts = client.download_export_split(df, dt)
            if parts:
                kinds = {p.kind for p in parts}
                path = (
                    ventas_db.raw_dir()
                    / f"ventas_{chunk.label}-split{'.csv' if 'xls' in kinds or 'csv' in kinds else '.html'}"
                )
                if kinds == {"xls"}:
                    # XLS no se puede concatenar: recombinar via CSV intermedio
                    import csv as _csv
                    import io as _io

                    buf = _io.StringIO()
                    w = _csv.writer(buf)
                    wrote_header = False
                    for p in parts:
                        rows = load_report_source(p.payload, "xls")
                        if not wrote_header:
                            w.writerows(rows[:1])
                            wrote_header = True
                        w.writerows(rows[1:])
                    path = path.with_suffix(".csv")
                    path.write_text(buf.getvalue(), encoding="utf-8")
                    return buf.getvalue().encode("utf-8"), "csv", path
                if kinds == {"csv"}:
                    body = b"\n".join(p.payload.rstrip(b"\r\n") for p in parts)
                    path.write_text(body.decode("utf-8", "ignore"), encoding="utf-8")
                    return body, "csv", path
                # HTML (o mezcla): concatenar — parse_report_rows descarta headers repetidos
                payloads = b"".join(p.payload for p in parts)
                path.write_bytes(payloads)
                return payloads, "html", path
        except Exception as e:
            last_err = e
            log.warning("split articulos fallo: %s", e)
        # Escalon 3: grid L0 — SOLO para chunks diarios. En mensuales el grid
        # viene truncado (~200 filas de 7,500+) y fingiria una captura OK parcial.
        es_mensual = len(chunk.label) == 7
        if es_mensual:
            raise IntranetError(
                f"chunk {chunk.label} fallo (D0 y split): {_brief(last_err)} — reintento diario automatico"
            )
        try:
            self._progress("chunk", f"  split fallo ({_brief(last_err)}) - probando grid L0")
            csv_text = client.scrape_html(df, dt)
            path = ventas_db.raw_dir() / f"ventas_{chunk.label}.csv"
            path.write_text(csv_text, encoding="utf-8")
            return csv_text.encode("utf-8"), "csv", path
        except Exception as e:
            raise IntranetError(
                f"chunk {chunk.label} fallo (D0/split/L0): {_brief(last_err)} / {_brief(e)}"
            ) from last_err

    def _verificar_volumen(self, conn, ventas: list, label: str) -> None:
        """Guarda anti-achique: si la descarga trae MUCHO menos de lo guardado,
        se aborta el chunk (queda fallido para reintento) en vez de reemplazar.

        Un día/mes solo crece intradía (las correcciones llegan como NCR nuevas,
        no borrando). Si la descarga trae <50% de lo almacenado, es un export
        parcial o una sesión caída a mitad de tabla, no datos reales.
        Primera captura del período (0 guardadas) siempre pasa.
        """
        if not ventas:
            return
        es_diario = len(label) == 10 and label.count("-") == 2
        if es_diario:
            nuevas = sum(1 for v in ventas if str(v.get("fecha_orig", ""))[:10] == label)
            viejas = conn.execute(
                "SELECT COUNT(*) FROM ventas WHERE substr(fecha_orig, 1, 10) = ?", (label,)
            ).fetchone()[0]
            alcance = label
        else:
            nuevas = len(ventas)
            viejas = conn.execute(
                "SELECT COUNT(*) FROM ventas WHERE mes_ref = ?", (label,)
            ).fetchone()[0]
            alcance = label
        log.info("volumen %s: guardadas=%d descargadas=%d", alcance, viejas, nuevas)
        if viejas > 0 and nuevas < viejas * 0.5:
            raise IntranetError(
                f"descarga parcial en {alcance}: trae {nuevas} filas contra "
                f"{viejas} guardadas (<50%). Se conserva lo guardado y el chunk "
                f"queda fallido para reintento."
            )

    def _process_chunk(
        self, conn, chunk, payload: bytes, kind: str, raw_path: Path, conn_estado
    ) -> int:
        rows = load_report_source(payload, kind)
        parsed = parse_report_rows(rows, chunk.label, file_source=raw_path.name)
        resueltas, _ = resolve_nc_nd_cross(conn, parsed.nc_nd_pendientes)
        ventas = parsed.ventas + resueltas
        self._verificar_volumen(conn, ventas, chunk.label)
        n = ventas_db.insert_ventas(conn, ventas, label=chunk.label)
        # Registrar checksum del dia para detectar cambios en proxima actualizacion
        # y anotar el watermark (day_state, sidecar): qué días ya trajimos y cuándo.
        if len(chunk.label) == 10:  # chunks diarios (YYYY-MM-DD)
            ventas_db.record_day_checksum(conn, chunk.label)
            ventas_db.record_day_capture(conn, chunk.label, conn_estado)
        else:  # mensual: anotar cada día traído para que participe del watermark
            for _d in sorted(
                {
                    str(v.get("fecha_orig", ""))[:10]
                    for v in ventas
                    if len(str(v.get("fecha_orig", ""))) >= 10
                }
            ):
                ventas_db.record_day_capture(conn, _d, conn_estado)
        self._progress(
            "chunk",
            f"  {chunk.label}: {n} filas insertadas ({len(parsed.nc_nd_pendientes)} NC/ND cross-mes)",
        )
        return n

    def _capture_chunk(self, conn, client: IntranetClient, chunk, conn_estado) -> int:
        try:
            payload, kind, raw_path = self._download_chunk(client, chunk)
        except SessionLost:
            # La sesión ASP.NET puede caer en corridas largas (puesta al día
            # tras meses sin abrirse): re-login una vez y reintentar el chunk
            # en vez de marcarlo fallido.
            self._progress("chunk", f"  {chunk.label}: sesión caída — re-login y reintento")
            client.login()
            payload, kind, raw_path = self._download_chunk(client, chunk)
        return self._process_chunk(conn, chunk, payload, kind, raw_path, conn_estado)

    # ── Rango completo ───────────────────────────────────────────────

    def capture_range(self, desde: date, hasta: date, skip_existing: bool = True) -> dict:
        """Planifica chunks para [desde, hasta] y captura.
        Con skip_existing (default), los meses ya presentes en la DB se omiten
        salvo que esten incompletos (captura interrumpida) o sea el mes en curso."""
        usar_diario = (hasta - desde).days <= 7
        if usar_diario:
            chunks = day_chunks(desde, hasta)
        else:
            chunks = month_chunks(desde, hasta)
            if skip_existing:
                # month_is_complete cubre: mensual completo, dias que llegan a
                # fin de mes, y excluye el mes en curso (ventana diaria).
                omitir = [c.label for c in chunks if ventas_db.month_is_complete(c.label)]
                if omitir:
                    self._progress(
                        "plan",
                        f"Meses ya en SQLite (se omiten): {len(omitir)} -> {', '.join(omitir[:8])}"
                        + ("..." if len(omitir) > 8 else ""),
                    )
                chunks = [c for c in chunks if c.label not in set(omitir)]
        return self.capture_chunks(chunks)

    def capture_chunks(self, chunks: list) -> dict:
        """Captura una lista de chunks (mensuales o diarios) ya planificada.
        Mes que falla -> reintento diario. Dedup + sync_log al final."""
        t0 = time.time()
        self.acquire_lock()
        try:
            ventas_db.init_db()
            conn = ventas_db.get_conn()
            conn_estado = ventas_db.connect_estado(readonly=False)
            ventas_db.init_estado(conn_estado)
            CAPTURE_STATUS.begin(self.mode_label, len(chunks), service=self)
            resumen = {
                "filas": 0,
                "chunks_ok": [],
                "chunks_fallidos": [],
                "dias_fallidos": [],
                "chunks_omitidos": 0,
            }
            # Un solo login, una sola sesion. Los chunks se procesan secuencialmente
            # para no sobrecargar la intranet. Cada chunk tiene su propia conexion
            # SQLite (thread-safe via WAL mode).
            cli = IntranetClient(*self.credentials())
            self._client = cli  # para abort() instantaneo
            try:
                cli.ensure_logged_in()
                n_meses = sum(1 for c in chunks if len(c.label) == 7)
                self._progress(
                    "inicio",
                    f"Login OK. {len(chunks)} bloque(s) a descargar ({n_meses} mes/es + {len(chunks) - n_meses} dia/s)"
                    " — log por etapa: GET=render del grid, POST=genera/transfer el export",
                )
                total = len(chunks)
                for i, ch in enumerate(chunks, 1):
                    if self._aborted():
                        resumen["abortado"] = True
                        break
                    pct = (i - 1) / total if total else None
                    es_mensual = len(ch.label) == 7
                    CAPTURE_STATUS.chunk_start(ch.label, i, total)
                    try:
                        self._progress(
                            "chunk",
                            f"  [{i}/{total}] {'Mes ' if es_mensual else 'Dia '}{ch.label}",
                            pct,
                        )
                        n = self._capture_chunk(conn, cli, ch, conn_estado)
                        resumen["filas"] += n
                        resumen["chunks_ok"].append(ch.label)
                        CAPTURE_STATUS.chunk_ok(ch.label, n)
                    except Exception as e:
                        if self._aborted():
                            resumen["abortado"] = True
                            break
                        CAPTURE_STATUS.chunk_fail(ch.label, _brief(e))
                        if es_mensual:
                            self._progress(
                                "error",
                                f"  Mes {ch.label} fallo: {_brief(e)} - reintento diario",
                                pct,
                            )
                            ok_dias, fail_dias = self._capture_month_by_days(
                                conn, cli, ch, conn_estado
                            )
                            resumen["filas"] += ok_dias
                            if fail_dias:
                                resumen["dias_fallidos"].extend(fail_dias)
                                resumen["chunks_fallidos"].append(ch.label)
                                self._mark_failed(ch.label, str(e), fail_dias)
                            elif self._aborted():
                                resumen["abortado"] = True
                                break
                            else:
                                resumen["chunks_ok"].append(ch.label)
                        else:
                            self._progress("error", f"  Dia {ch.label} fallo: {_brief(e)}", pct)
                            resumen["dias_fallidos"].append(ch.label)
                            self._mark_failed(ch.label, str(e))
            finally:
                self._client = None
                cli.close()

            dup_cruzados = 0
            prep_s = 0.0
            try:
                self._progress("preparacion", "🗄️ Preparando SQLite: estadísticas...")
                t_prep = time.time()
                # dedup_ventas ya no corre automático: agrupar por
                # (folio_unico, id_articulo) borraba líneas legítimas repetidas
                # de una misma factura. Solo se detecta el solapamiento entre
                # labels (mensual/diario) para revisión manual, sin borrar.
                if chunks:
                    _fmin = min(c.start for c in chunks).isoformat()
                    _fmax = max(c.end for c in chunks).isoformat()
                    dup_cruzados = ventas_db.contar_duplicados_cruzados(conn, _fmin, _fmax)
                ventas_db.refresh_stats_cache(conn)
                ventas_db.populate_nc_asociadas(conn)
                ventas_db.refresh_agg_cliente_mes(conn)
                # Checksum por mes capturado (garantia de integridad de la primera pasada)
                meses_nuevos = sorted(
                    {
                        c.label
                        for c in chunks
                        if len(c.label) == 7 and c.label in resumen["chunks_ok"]
                    }
                )
                for mes in meses_nuevos:
                    n_mes, tot_mes = ventas_db.record_month_checksum(conn, mes)
                    self._progress(
                        "preparacion", f"  checksum {mes}: {n_mes} filas, S/ {tot_mes:,.2f}"
                    )
                huerfanas = ventas_db.nc_nd_huerfanas(conn)
                resumen["nc_nd_huerfanas"] = huerfanas
                if huerfanas:
                    self._progress(
                        "preparacion",
                        f"  {huerfanas} NC/ND con factura fuera del rango descargado (se anclan al extender hacia atras)",
                    )
                prep_s = time.time() - t_prep
                parte_dup = (
                    f"  {dup_cruzados} pares en 2+ labels (revisar manual)"
                    if dup_cruzados
                    else "  sin solapamiento entre labels"
                )
                self._progress(
                    "preparacion", f"  SQLite listo: {parte_dup}, preparación {prep_s:.0f}s"
                )
            except Exception as e:
                self._progress("error", f"  preparación SQLite fallo: {_brief(e)}")
            # Respaldo automatico semanal (checkpoint WAL + copia; salta si ya hay del dia)
            try:
                self._progress("preparacion", "🗄️ Respaldo de seguridad...")
                bpath = ventas_db.backup_db()
                if bpath:
                    self._progress("preparacion", f"  Backup: {bpath.name}")
                else:
                    self._progress("preparacion", "  Backup al dia (no requiere nuevo)")
            except Exception as e:
                self._progress("error", f"  backup fallo: {_brief(e)}")
            # Limpieza de descargas crudas ya validadas (disco)
            try:
                mb = self._limpiar_raw(resumen) / 1e6
                if mb > 0:
                    self._progress(
                        "preparacion", f"  Raw limpiado: {mb:.1f} MB liberados (chunks validados)"
                    )
            except Exception as e:
                self._progress("error", f"  limpieza raw fallo: {_brief(e)}")
            duracion = time.time() - t0
            estado = (
                "abortado"
                if self._aborted()
                else ("ok" if not resumen["chunks_fallidos"] else "parcial")
            )
            ventas_db.register_sync(conn, "capture", estado, resumen["filas"], duracion)
            resumen["dedup_eliminadas"] = 0
            resumen["duplicados_cruzados"] = dup_cruzados
            resumen["preparacion_s"] = round(prep_s, 1)
            resumen["duracion_s"] = round(duracion, 1)
            self._progress(
                "fin",
                f"Captura terminada: {resumen['filas']} filas"
                + (f", {dup_cruzados} pares en 2+ labels (revisar)" if dup_cruzados else "")
                + f", {duracion:.0f}s",
            )
            CAPTURE_STATUS.finish(
                resumen["filas"],
                resumen["chunks_ok"],
                resumen["chunks_fallidos"] + resumen["dias_fallidos"],
                error=resumen.get("chunks_fallidos", [""])[0] if resumen["chunks_fallidos"] else "",
                abortado=bool(resumen.get("abortado")),
            )
            return resumen
        finally:
            # conn viene de get_conn() (cacheada por hilo): no basta .close(),
            # hay que expulsarla del caché o el próximo get_conn() devuelve
            # una conexión muerta ("closed database"). conn_estado es propia.
            ventas_db.reset_connections()
            try:
                conn_estado.close()
            except Exception:
                pass
            self.release_lock()

    def _limpiar_raw(self, resumen: dict) -> int:
        """Borra descargas crudas de chunks ya capturados y validados (los XLS
        pesan ~13MB/mes). Conserva: chunks fallidos (re-parseo manual) y dias
        recientes (<= 7 dias, por si se necesita re-procesar)."""
        import re as _re

        raw = ventas_db.raw_dir()
        liberados = 0
        borrados = 0
        hoy_iso = date.today().strftime("%Y-%m-%d")
        for f in raw.glob("ventas_*"):
            m = _re.match(r"ventas_([\d\-]+?)(?:-split)?\.(?:xls|csv|html)$", f.name)
            if not m:
                continue
            label = m.group(1)
            es_reciente = len(label) == 10 and label >= (date.today() - timedelta(days=7)).strftime(
                "%Y-%m-%d"
            )
            ok = label in resumen.get("chunks_ok", [])
            fallido = label in resumen.get("chunks_fallidos", []) + resumen.get("dias_fallidos", [])
            if ok and not fallido and not es_reciente and label != hoy_iso:
                size = f.stat().st_size
                f.unlink(missing_ok=True)
                liberados += size
                borrados += 1
        if borrados:
            log.info("raw limpiado: %d archivos (%.1f MB)", borrados, liberados / 1e6)
        return liberados

    def _capture_month_by_days(
        self, conn, client: IntranetClient, mc, conn_estado
    ) -> tuple[int, list[str]]:
        """Fallback: captura un mes dia por dia (camino fiable para meses pesados
        como 2024-01). Cada dia se reporta al estado global: la tira muestra el
        avance dia a dia, no un bloque congelado."""
        filas = 0
        fallidos: list[str] = []
        dias = day_chunks(mc.start, min(mc.end, date.today()))
        total = len(dias)
        for i, dc in enumerate(dias, 1):
            if self._aborted():
                break
            try:
                self._progress(
                    "chunk",
                    f"  {mc.label} dia {i}/{total}: {dc.label}",
                    (i - 1) / total if total else None,
                )
                n = self._capture_chunk(conn, client, dc, conn_estado)
                filas += n
                CAPTURE_STATUS.chunk_ok(dc.label, n)
            except Exception as e:
                self._progress(
                    "error",
                    f"  dia {dc.label} fallo: {_brief(e)}",
                    (i - 1) / total if total else None,
                )
                fallidos.append(dc.label)
                CAPTURE_STATUS.chunk_fail(dc.label, _brief(e))
                self._mark_failed(dc.label, str(e))
        return filas, fallidos

    # ── Incremental ──────────────────────────────────────────────────

    def update_from_last(
        self,
        overlap_days: int = DIAS_OVERLAP,
        dias_inmadurez: int = DIAS_INMADUREZ_DEFAULT,
        forzar: bool = False,
    ) -> dict:
        """Sync incremental por niveles.

        La intranet filtra por fecha de documento (fecha_orig), NO por fecha
        de ingreso. Documentos de días anteriores pueden aparecer cuando se
        ingresan tarde (ej: doc del 17/09 ingresado el 22/09).

        - Brecha chica: se re-descarga día por día (DELETE-then-INSERT por día
          es seguro y maneja duplicados). Solo se omiten días ya 'cerrado'.
        - Brecha grande (app meses sin abrirse): backfill mensual para lo
          viejo + cola diaria. 3 meses de atraso son ~3 meses + ~30 días, no
          ~100 días uno por uno.
        - Cada chunk commitea por separado: se puede interrumpir y retomar; el
          próximo 'Actualizar hoy' sigue donde quedó (los días cerrados se
          omiten, los meses completos también).
        """
        hoy = date.today()
        conn = ventas_db.get_conn()
        conn_estado = ventas_db.connect_estado(readonly=False)
        ventas_db.init_estado(conn_estado)
        try:
            ventas_db.marcar_dias_cerrados(conn_estado, dias_inmadurez, hoy.isoformat())
            fmax = ventas_db.fecha_max_segura(conn)
            meses, dias = planificar_puesta_al_dia(fmax, hoy, dias_inmadurez, overlap_days)
            if not forzar:
                meses = [m for m in meses if not ventas_db.month_is_complete(m)]
                if dias:
                    abiertos = set(ventas_db.dias_sin_cerrar(conn_estado, dias[0], dias[-1]))
                    dias = [d for d in dias if d in abiertos]
        finally:
            # Igual que en capture_chunks: expulsar del caché, no solo cerrar.
            ventas_db.reset_connections()
            try:
                conn_estado.close()
            except Exception:
                pass

        if not meses and not dias:
            return {"filas": 0, "archivos": 0, "fallidos": [], "detail": "DB ya actualizada"}

        chunks = []
        for _m in meses:
            _y, _mm = (int(p) for p in _m.split("-"))
            chunks.append(month_chunk(_y, _mm))
        for _d in dias:
            chunks.append(day_chunk(date.fromisoformat(_d)))
        self._progress(
            "plan",
            f"Puesta al día: {len(meses)} mes/es + {len(dias)} día/s "
            f"(fmax {fmax or 'vacía'} -> {hoy.isoformat()})",
        )
        return self.capture_chunks(chunks)

    # ── Marcadores de fallo ──────────────────────────────────────────

    def _mark_failed(self, label: str, error: str, dias: list[str] | None = None) -> None:
        try:
            data_dir = ventas_db.data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / f"failed_{label}.json"
            payload = {
                "label": label,
                "error": error,
                "dias_fallidos": dias or [],
                "marcado_en": datetime.now().isoformat(timespec="seconds"),
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.exception("no se pudo marcar fallo de %s", label)
