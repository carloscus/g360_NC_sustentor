"""Modal unificado de sincronización desde la API Go.

Reemplaza el flujo antiguo de "Actualizar hoy" (login intranet → XLS) por un
modal autocontenido que orquesta todo el proceso en 4 pasos:

  0. Detección del servidor (URL desde G360_API_URL o default)
  1. Login intranet → token HMAC
  2. Check frescura (status vs local) → decidir si necesita refresh
  3. Sync incremental (sync_api.aplicar)
  4. Resultado + KPIs actualizados

El modal es reutilizable: se puede invocar desde cualquier botón que necesite
sincronizar. Maneja sus propios hilos, loading overlays y notificaciones.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, List, Optional

import flet as ft


from src.ui.mensajes import mensaje
log = logging.getLogger(__name__)

# Brecha máxima (días) para recomendar sync incremental en vez de reemplazo
# completo. Más allá de esto, traer 2.6 GB por cartucho/USB/base-canonica es
# más rápido y consistente que fetchear cientos de días por HTTP (~8s/día).
UMBRAL_BRECHA_DIAS = 30
# Ventana máxima del sync amplio (el API acepta hasta 1500 días por llamada).
MAX_VENTANA_DIAS = 400


# ---------------------------------------------------------------------------
# Estados del modal
# ---------------------------------------------------------------------------


class _Step(Enum):
    LOGIN = "login"
    FRESHNESS = "freshness"
    SYNC = "sync"
    RESULT = "result"
    ERROR = "error"


@dataclass
class FreshnessInfo:
    api_online: bool = False
    api_url: str = ""
    snapshot_capturado_en: str = ""
    local_fecha_max: str = ""
    desfase_horas: float = 0.0
    dias_desfasados: List[str] = None
    filas_api: int = 0
    filas_local: int = 0
    necesita_refresh: bool = False
    token_valido: bool = False
    brecha_dias: int = 0
    recomienda_reemplazo: bool = False

    def __post_init__(self):
        if self.dias_desfasados is None:
            self.dias_desfasados = []


@dataclass
class SyncResult:
    estado: str = ""
    filas: int = 0
    dias: List[str] = None
    dias_desfasados: List[str] = None
    dias_local_adelantado: List[str] = None
    segundos: float = 0.0
    modo: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------


class ApiSyncModal:
    """Modal autocontenido para sincronización desde la API Go."""

    def __init__(self, app, page):
        self.app = app
        self.page = page
        self._result_callback: Optional[Callable] = None
        self._cli = None
        self._sync = None

    def open(
        self,
        on_result: Optional[Callable[[SyncResult], None]] = None,
        server_url: str = "",
    ) -> None:
        """Abre el modal y comienza el flujo.

        on_result: callback que recibe el SyncResult al terminar.
        server_url: override de G360_API_URL (para tests).
        """
        from src.core.api_auth import default_api_url

        try:
            self._result_callback = on_result
            url = server_url or default_api_url()

            page = self.page
            if page is None:
                self.app.show_snackbar("No hay página disponible", self.app.G360_ERROR)
                return

            # Safe page update wrapper
            # `mounted` evita actualizar antes de page.open(dlg): pedir un update de un
            # que todavia no esta en la pagina lanza "Control must be added to
            # the page first" y llenaba el log con un traceback en cada apertura.
            mounted = [False]

            def safe_update(ctrl=None):
                if not mounted[0]:
                    return
                try:
                    if ctrl:
                        ctrl.update()
                    else:
                        safe_page_update()
                except Exception as e:
                    log.exception("safe_update failed: %s", e)

            def safe_page_update():
                try:
                    page.update()
                except Exception as e:
                    log.exception("safe_page_update failed: %s", e)

            # ── Estado del modal ─────────────────────────────────────────────
            step = [_Step.LOGIN]
            login_result = [None]  # AuthResult
            freshness = [None]  # FreshnessInfo
            sync_res = [None]  # SyncResult

            # ── Controls UI ──────────────────────────────────────────────────
            user_input = ft.TextField(
                label="Usuario intranet",
                hint_text="Tu usuario de intranet",
                width=280,
                dense=True,
                text_size=13,
                border_radius=12,
            )
            pass_input = ft.TextField(
                label="Contraseña",
                hint_text="••••••••",
                width=280,
                dense=True,
                text_size=13,
                border_radius=12,
                password=True,
                can_reveal_password=True,
            )

            def _btn_style(**kwargs):
                return ft.ButtonStyle(**{k: v for k, v in kwargs.items() if v is not None})

            btn_login = ft.ElevatedButton(
                "Verificar conexión",
                height=38,
                width=280,
                style=_btn_style(bgcolor=self.app.G360_ACCENT),
            )

            status_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
            progress_bar = ft.ProgressBar(value=0, visible=False, height=4, border_radius=2)
            log_text = ft.Text("", size=10, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=8)

            # Botones condicionales del paso de frescura
            btn_refresh = ft.TextButton(
                "🔄 Forzar refresh del snapshot",
                visible=False,
                style=ft.ButtonStyle(color=self.app.G360_ACCENT),
            )
            btn_skip_refresh = ft.TextButton(
                "Continuar sin refresh",
                visible=False,
            )

            # Botón de sync
            btn_sync = ft.ElevatedButton(
                "Iniciar sincronización",
                height=38,
                # Mismo ancho que btn_login: si no, la acción primaria cambia de
                # medida al pasar del paso de login al de sincronización.
                width=280,
                visible=False,
                style=_btn_style(bgcolor=self.app.G360_ACCENT),
            )

            # Botones de fallback
            btn_ntfs = ft.TextButton(
                "⬇ Cargar desde NTFS (fuente directa)",
                visible=False,
                style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
            )
            btn_cartucho = ft.TextButton(
                "📦 Importar desde cartucho/USB",
                visible=False,
                style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
            )
            btn_diagnostic = ft.TextButton(
                "ℹ Diagnóstico",
                visible=False,
                style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
            )

            # Área de resultado
            result_box = ft.Column([], visible=False, spacing=4)

            # Footer con opciones avanzadas
            footer_row = ft.Row(
                [btn_ntfs, btn_cartucho, btn_diagnostic],
                spacing=8,
                visible=False,
            )

            def _upd(ctrl):
                safe_update(ctrl)

            def _append_log(msg: str):
                ts = datetime.now().strftime("%H:%M:%S")
                current = log_text.value or ""
                log_text.value = (current + f"\n[{ts}] {msg}")[-1500:]
                _upd(log_text)
                log.info("modal: %s", msg)

            def _set_status(msg: str, color=None):
                status_text.value = msg
                if color:
                    status_text.color = color
                _upd(status_text)

            def _show_step(s: _Step):
                """Alterna la visibilidad de las secciones ya construidas.

                Antes esto vaciaba `content.controls` y re-construia la seccion del
                paso en cada transicion. Eso re-parentaba los MISMOS controles
                (status_text, log_text, btn_sync, btn_ntfs, btn_cartucho) en varias
                filas distintas, y en Flet un control tiene un unico padre: el
                render se rompia de forma intermitente al pasar de LOGIN a
                FRESHNESS. Ahora cada control se crea una vez y solo se togglea
                `visible`; lo unico que se reemplaza es el cuerpo de la seccion de
                frescura, que depende de datos nuevos.
                """
                try:
                    step[0] = s
                    sec_login.visible = s == _Step.LOGIN
                    sec_freshness.visible = s == _Step.FRESHNESS
                    sec_sync.visible = s in (_Step.SYNC, _Step.RESULT)
                    sec_error.visible = s == _Step.ERROR
                    if s == _Step.FRESHNESS:
                        sec_freshness.controls = [_build_freshness_section()]
                    result_box.visible = s == _Step.RESULT
                    if s == _Step.RESULT:
                        result_box.controls = _build_result_lines()
                    progress_bar.visible = s == _Step.SYNC
                    btn_sync.visible = s in (_Step.FRESHNESS, _Step.SYNC)
                    footer_row.visible = s in (_Step.SYNC, _Step.RESULT, _Step.ERROR)
                    _upd(content)
                except Exception as e:
                    log.exception("_show_step failed: %s", e)
                    try:
                        _append_log(f"UI step error: {e}")
                    except Exception:
                        pass

            def _build_login_section():
                # Bloque de 280 px centrado dentro de los 480 del dialog: antes
                # los campos iban pegados a la izquierda y dejaban 200 px de aire
                # a la derecha, lo que se leia como desalineado.
                return ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.KEY_OUTLINED, size=20, color=self.app.G360_ACCENT),
                                ft.Text(
                                    "Sincronización desde API Go",
                                    size=14,
                                    weight=ft.FontWeight.W_700,
                                ),
                            ],
                            spacing=8,
                            width=280,
                        ),
                        ft.Text(
                            "Ingresa tus credenciales de intranet para autenticarte contra la API. "
                            "El token se renueva automáticamente cada 24h.",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            width=280,
                        ),
                        ft.Container(height=8),
                        user_input,
                        pass_input,
                        ft.Container(height=4),
                        btn_login,
                    ],
                    spacing=0,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                )

            def _build_freshness_section():
                from src.core.fechas import fecha_hora_ui, fecha_ui

                controls = [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.THEATER_COMEDY, size=20, color=self.app.G360_ACCENT),
                            ft.Text("Estado del servidor", size=13, weight=ft.FontWeight.W_600),
                        ],
                        spacing=8,
                    ),
                    ft.Container(height=4),
                ]
                fi = freshness[0]
                if fi:
                    icon = "✅" if fi.api_online else "❌"
                    controls.append(ft.Text(f"{icon} API: {fi.api_url}", size=12))
                    if fi.api_online:
                        if fi.filas_local == 0:
                            controls.append(
                                ft.Text(
                                    "Sin datos locales: el sync traerá la ventana reciente. "
                                    "Para el historial completo (2010→hoy) usa Configuración "
                                    "→ Gestión → Importar cartucho/USB.",
                                    size=10,
                                    color=ft.Colors.ORANGE,
                                )
                            )
                        elif fi.recomienda_reemplazo:
                            controls.append(
                                ft.Text(
                                    f"Brecha de {fi.brecha_dias} días sin actualizar: conviene "
                                    "reemplazo completo (cartucho/USB/base-canónica) en vez "
                                    "de sync incremental. Igual puedes continuar, pero tardará.",
                                    size=10,
                                    color=ft.Colors.ORANGE,
                                )
                            )
                        controls.append(
                            ft.Text(
                                # La API manda ISO; en pantalla va dd-mm-yyyy.
                                "Snapshot: %s  |  Local: %s  |  Antigüedad: %.1fh"
                                % (
                                    fecha_hora_ui(fi.snapshot_capturado_en) or "n/a",
                                    fecha_ui(fi.local_fecha_max) or "n/a",
                                    fi.desfase_horas,
                                ),
                                size=10,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            )
                        )
                        if fi.dias_desfasados:
                            controls.append(
                                ft.Text(
                                    f"{len(fi.dias_desfasados)} días con datos pendientes",
                                    size=10,
                                    color=ft.Colors.ORANGE,
                                )
                            )
                        controls.append(ft.Container(height=8))
                        if fi.necesita_refresh:
                            btn_refresh.visible = True
                            btn_skip_refresh.visible = True
                            controls.append(ft.Row([btn_refresh, btn_skip_refresh], spacing=8))
                            controls.append(ft.Container(height=4))
                    else:
                        controls.append(
                            ft.Text(
                                "La API no responde. Verifica que el servidor esté encendido.",
                                size=12,
                                color=self.app.G360_ERROR,
                            )
                        )
                controls.append(ft.Container(height=8))
                _upd(btn_refresh)
                _upd(btn_skip_refresh)
                return ft.Column(controls, spacing=0, tight=True)

            def _build_sync_section():
                return ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.SYNC, size=20, color=self.app.G360_ACCENT),
                                ft.Text("Sincronizando...", size=13, weight=ft.FontWeight.W_600),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=0,
                    tight=True,
                )

            def _build_result_lines():
                """Lineas del resumen final. Antes `result_box` se creaba vacio y
                nunca se llenaba, asi que el paso RESULTADOS no mostraba nada."""
                r = sync_res[0]
                if r is None:
                    return [ft.Text("Sin resultado", size=12, color=ft.Colors.ON_SURFACE_VARIANT)]
                ok = not r.error
                lineas = [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE_OUTLINED if ok else ft.Icons.ERROR_OUTLINE,
                                size=18,
                                color=self.app.G360_SUCCESS if ok else self.app.G360_ERROR,
                            ),
                            ft.Text(
                                "Sincronización completada" if ok else "Sincronización falló",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=self.app.G360_SUCCESS if ok else self.app.G360_ERROR,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Container(height=4),
                ]
                if r.error:
                    lineas.append(ft.Text(r.error, size=12, color=self.app.G360_ERROR))
                    return lineas
                filas = [
                    ("Estado", r.estado or "—"),
                    ("Filas aplicadas", str(r.filas)),
                    ("Modo", r.modo or "—"),
                    ("Duración", f"{r.segundos:.1f} s"),
                    ("Días con datos", str(len(r.dias))),
                    ("Días desfasados", str(len(r.dias_desfasados))),
                ]
                for etiqueta, valor in filas:
                    lineas.append(
                        ft.Row(
                            [
                                ft.Text(etiqueta, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Text(str(valor), size=12, weight=ft.FontWeight.W_600),
                            ],
                            spacing=8,
                        )
                    )
                return lineas

            def _build_error_section():
                return ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.ERROR, size=20, color=self.app.G360_ERROR),
                                ft.Text(
                                    "Error de sincronización", size=13, weight=ft.FontWeight.W_600
                                ),
                            ],
                            spacing=8,
                        ),
                        ft.Container(height=4),
                        ft.Text(
                            sync_res[0].error if sync_res[0] else "Error desconocido",
                            size=12,
                            color=self.app.G360_ERROR,
                        ),
                        ft.Container(height=8),
                    ],
                    spacing=0,
                    tight=True,
                )

            # Secciones construidas una sola vez. Los controles compartidos
            # (status_text, log_text, progress_bar, result_box, btn_sync,
            # footer_row) viven fuera de ellas, en una posicion fija, para que
            # cada uno tenga un unico padre en todo el dialog.
            sec_login = _build_login_section()
            sec_freshness = ft.Column([], spacing=0, tight=True)
            sec_sync = _build_sync_section()
            sec_error = _build_error_section()
            sections_host = ft.Column(
                [sec_login, sec_freshness, sec_sync, sec_error],
                spacing=0,
                tight=True,
            )
            content = ft.Column(
                [
                    sections_host,
                    ft.Container(height=6),
                    status_text,
                    progress_bar,
                    log_text,
                    result_box,
                    ft.Container(height=4),
                    btn_sync,
                    ft.Container(height=8),
                    footer_row,
                ],
                spacing=0,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            )
            _show_step(_Step.LOGIN)

            dlg = ft.AlertDialog(
                title=ft.Row(
                    [
                        ft.Icon(ft.Icons.SETTINGS_OUTLINED, color=self.app.G360_ACCENT),
                        ft.Text("Sincronización desde API", size=16, weight=ft.FontWeight.W_700),
                    ],
                    spacing=8,
                ),
                content=ft.Container(content, height=420, width=480),
                actions=[
                    ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )

            # ── Handlers ─────────────────────────────────────────────────────

            def _on_login(_):
                """Paso 1: verificar credenciales contra la API."""
                user = user_input.value or ""
                pwd = pass_input.value or ""
                if not user or not pwd:
                    _set_status("Usuario y contraseña requeridos", self.app.G360_ERROR)
                    return

                btn_login.disabled = True
                _set_status("Verificando credenciales...", ft.Colors.ON_SURFACE_VARIANT)
                _upd(btn_login)
                _upd(status_text)
                safe_page_update()

                def worker():
                    try:
                        from src.core.api_auth import APIAuthClient
                        from src.core.capture_service import CaptureService

                        # Antes de pegarle a /api/login, asegurarse de que la API esté
                        # arriba. Si WSL está dormido, esto la levanta sin intervención
                        # del usuario y evita el WinError 10061 / ConnectError.
                        try:
                            from src.core.api_wake import asegurar_api

                            wake_info = asegurar_api(url)
                            if not wake_info.get("ok"):
                                detalle = str(wake_info.get("detalle") or "API no disponible")
                                _set_status(
                                    "No se pudo iniciar la API de ventas. "
                                    "Revisá que WSL (Ubuntu) esté corriendo y que "
                                    "g360-ventas-api esté instalado. "
                                    f"Detalle: {detalle}",
                                    self.app.G360_ERROR,
                                )
                                _append_log(f"wake fallo: {detalle}")
                                btn_login.disabled = False
                                return
                            if wake_info.get("arrancada"):
                                _append_log(f"API levantada en {wake_info.get('segundos', 0):.1f}s")
                        except Exception as wake_ex:
                            log.warning("asegurar_api fallo, sigo con login igual: %s", wake_ex)

                        cli = APIAuthClient(url)
                        result = cli.login(user, pwd)
                        login_result[0] = result

                        if result.success:
                            CaptureService.save_api_token(result.token, result.user)
                            _set_status(f"✅ Conectado como {result.user}", self.app.G360_SUCCESS)
                            _append_log(f"Login OK: {result.user}")
                            # Auto-advance to freshness check
                            time.sleep(0.5)
                            _run_freshness_check()
                        else:
                            if result.es_transporte:
                                # Fallo de red, no de credenciales: decirlo asi para
                                # que el usuario no siga reintentando la contraseña.
                                _set_status(
                                    f"✗ {result.texto_credenciales}. El servidor no respondió, "
                                    "así que tu contraseña no llegó a comprobarse.",
                                    self.app.G360_ERROR,
                                )
                            else:
                                _set_status(
                                    f"✗ {result.texto_credenciales}",
                                    self.app.G360_ERROR,
                                )
                            _append_log(f"Login fallo [{result.kind}]: {result.message}")
                            btn_login.disabled = False
                    except Exception as e:
                        _set_status(f"✗ Error de conexión: {e}", self.app.G360_ERROR)
                        _append_log(f"Login exception: {e}")
                        btn_login.disabled = False
                    finally:
                        _upd(btn_login)
                        _upd(status_text)
                        safe_page_update()

                threading.Thread(target=worker, daemon=True).start()

            def _run_freshness_check():
                """Paso 2: verificar frescura del snapshot.

                desfase_horas = antigüedad del snapshot (ahora - capturado_en).
                No se compara contra fecha_max local (mezclaría hora de captura
                con fecha de venta). Los días que le faltan a local van aparte.
                """
                try:
                    from datetime import timezone

                    from src.core import ventas_db
                    from src.core.api_robustness.check import _parse_iso
                    from src.core.capture_service import CaptureService
                    from src.core.sync_api import SyncAPI
                    from src.core.ventas_api_client import VentaAPIClient

                    token = CaptureService.api_token()
                    if not token:
                        _set_status("Sin token — reinicia el login", self.app.G360_ERROR)
                        return

                    cli = VentaAPIClient(base_url=url, api_token=token)
                    fi = FreshnessInfo(api_url=url)

                    # Health check
                    try:
                        h = cli.health()
                        fi.api_online = h.get("status") == "ok"
                    except Exception:
                        fi.api_online = False

                    if not fi.api_online:
                        freshness[0] = fi
                        _show_step(_Step.FRESHNESS)
                        return

                    # Status
                    try:
                        st = cli.status()
                        fi.filas_api = int(st.get("filas") or 0)
                        fi.snapshot_capturado_en = st.get("capturado_en_ultimo", "")
                        fi.token_valido = CaptureService.is_api_token_valid()
                    except Exception as e:
                        _append_log(f"Status falló: {e}")
                        fi.token_valido = False

                    # Local info
                    local_info = ventas_db.db_card_info()
                    fi.filas_local = int(local_info.get("filas") or 0)
                    fi.local_fecha_max = str(local_info.get("fecha_max") or "")

                    # Antigüedad del snapshot en horas (ahora - capturado_en).
                    try:
                        if fi.snapshot_capturado_en:
                            captured = _parse_iso(fi.snapshot_capturado_en)
                            if captured is not None:
                                now = datetime.now(timezone.utc)
                                fi.desfase_horas = round((now - captured).total_seconds() / 3600, 1)
                    except Exception:
                        pass

                    # Brecha local vs hoy: define la ventana del sync y si conviene
                    # reemplazo completo en vez de incremental.
                    try:
                        hoy = datetime.now().date()
                        if fi.local_fecha_max:
                            fmax = _parse_iso(fi.local_fecha_max)
                            if fmax is not None:
                                fi.brecha_dias = max(0, (hoy - fmax.date()).days)
                            else:
                                fi.brecha_dias = MAX_VENTANA_DIAS
                        else:
                            fi.brecha_dias = MAX_VENTANA_DIAS
                    except Exception:
                        fi.brecha_dias = MAX_VENTANA_DIAS
                    fi.recomienda_reemplazo = fi.brecha_dias > UMBRAL_BRECHA_DIAS

                    # Días desfasados: ventana adaptativa que cubre la brecha real
                    # (+2 días de overlap) en vez de 7 días fijos. Tope: 400 días
                    # (el API acepta 1500, pero más de eso conviene reemplazo).
                    try:
                        conn = ventas_db.connect()
                        try:
                            ventana = min(max(7, fi.brecha_dias + 2), MAX_VENTANA_DIAS)
                            desde = (datetime.now() - timedelta(days=ventana)).strftime("%Y-%m-%d")
                            hasta = datetime.now().strftime("%Y-%m-%d")
                            dias_api = cli.day_checksums(desde, hasta).get("dias", [])
                            fi.dias_desfasados = SyncAPI.dias_desfasados(
                                desde, hasta, dias_api, conn
                            )
                        finally:
                            conn.close()
                    except Exception as e:
                        _append_log(f"Checksums: {e}")
                        fi.dias_desfasados = []

                    fi.necesita_refresh = fi.desfase_horas > 2
                    freshness[0] = fi
                    _show_step(_Step.FRESHNESS)

                except Exception as e:
                    _append_log(f"Frescura falló: {e}")
                    _show_step(_Step.ERROR)
                    sync_res[0] = SyncResult(error=str(e)[:300])

            def _on_refresh(_):
                """Dispara el refresh del snapshot vía HTTP y espera con progreso.

                Usa POST /api/admin/refresh + polling de GET /api/admin/refresh-status.
                Si el servidor no tiene esos endpoints (binario viejo), cae al
                mecanismo local (recovery.trigger_refresh_snapshot).
                """
                _append_log("Disparando refresh del snapshot...")
                _set_status("Solicitando refresh al servidor...", ft.Colors.ON_SURFACE_VARIANT)
                progress_bar.visible = True
                progress_bar.value = None
                _upd(progress_bar)
                btn_refresh.disabled = True
                btn_skip_refresh.disabled = True
                _upd(btn_refresh)
                _upd(btn_skip_refresh)

                def worker():
                    try:
                        from src.core.capture_service import CaptureService
                        from src.core.ventas_api_client import VentaAPIClient, VentaAPIError

                        token = CaptureService.api_token()
                        cli = VentaAPIClient(base_url=url, api_token=token)
                        try:
                            resp = cli.admin_refresh()
                        except VentaAPIError as e:
                            # Servidor viejo sin /api/admin/* (404) → fallback local
                            if "404" in str(e):
                                _append_log("Servidor sin endpoint admin; usando fallback local...")
                                from src.core.api_robustness.recovery import (
                                    trigger_refresh_snapshot,
                                )

                                r = trigger_refresh_snapshot(url)
                                if r.success:
                                    _append_log(f"Refresh OK: {r.detail}")
                                    _set_status(
                                        "Snapshot actualizado. Verificando...", ft.Colors.GREEN
                                    )
                                    time.sleep(1)
                                    _run_freshness_check()
                                else:
                                    _append_log(f"Refresh falló: {r.detail}")
                                    _set_status(
                                        f"Refresh no disponible: {r.detail}", self.app.G360_ERROR
                                    )
                                return
                            raise

                        _append_log(f"Refresh aceptado: {resp.get('timestamp', '')}")
                        # Polling con progreso indeterminado (~6 min en WSL por /mnt/c)
                        for i in range(120):
                            time.sleep(5)
                            try:
                                st = cli.admin_refresh_status()
                            except Exception as e:
                                _append_log(f"Poll refresh-status: {e}")
                                continue
                            estado = str(st.get("status", ""))
                            msg = str(st.get("message", ""))[:80]
                            dur = float(st.get("duration_sec", 0) or 0)
                            _set_status(
                                f"Refresh en curso... {dur:.0f}s — {msg}",
                                ft.Colors.ON_SURFACE_VARIANT,
                            )
                            _append_log(f"refresh-status: {estado} ({dur:.0f}s)")
                            safe_page_update()
                            if estado == "ok":
                                _append_log("Refresh OK: snapshot actualizado")
                                _set_status("Snapshot actualizado. Verificando...", ft.Colors.GREEN)
                                time.sleep(1)
                                _run_freshness_check()
                                return
                            if estado == "error":
                                err = str(st.get("error", ""))[:200]
                                _append_log(f"Refresh falló en servidor: {err}")
                                _set_status(f"Refresh falló: {err}", self.app.G360_ERROR)
                                return
                        _append_log("Refresh: timeout esperando al servidor (>10 min)")
                        _set_status(
                            "El refresh sigue en curso en el servidor; revisa en unos minutos.",
                            ft.Colors.ORANGE,
                        )
                    except Exception as e:
                        _append_log(f"Refresh exception: {e}")
                        _set_status(f"Error: {e}", self.app.G360_ERROR)
                    finally:
                        btn_refresh.disabled = False
                        btn_skip_refresh.disabled = False
                        progress_bar.visible = False
                        _upd(btn_refresh)
                        _upd(btn_skip_refresh)
                        _upd(progress_bar)
                        safe_page_update()

                threading.Thread(target=worker, daemon=True).start()

            def _on_skip_refresh(_):
                _show_step(_Step.SYNC)

            def _on_sync(_):
                """Paso 3: ejecutar sync incremental."""
                fi = freshness[0]
                if not fi or not fi.api_online:
                    _set_status("API no disponible", self.app.G360_ERROR)
                    return

                btn_sync.disabled = True
                _set_status("Iniciando sync incremental...", ft.Colors.ON_SURFACE_VARIANT)
                progress_bar.visible = True
                progress_bar.value = 0
                log_text.value = ""
                _upd(btn_sync)
                _upd(progress_bar)
                _upd(status_text)
                safe_page_update()

                def worker():
                    try:
                        from src.core import ventas_db
                        from src.core.sync_api import SyncAPI
                        from src.core.ventas_api_client import VentaAPIClient
                        from src.core.capture_service import CaptureService

                        token = CaptureService.api_token()
                        cli = VentaAPIClient(base_url=url, api_token=token)
                        sync = SyncAPI(cli)
                        conn = ventas_db.connect()

                        try:
                            # Misma ventana adaptativa que el check de frescura:
                            # cubre la brecha real (+2 días overlap), tope 400.
                            fi2 = freshness[0]
                            brecha = fi2.brecha_dias if fi2 else 7
                            ventana = min(max(7, brecha + 2), MAX_VENTANA_DIAS)
                            desde = (datetime.now() - timedelta(days=ventana)).strftime("%Y-%m-%d")
                            hasta = datetime.now().strftime("%Y-%m-%d")

                            def _progress(stage, msg, pct):
                                _set_status(msg or stage, ft.Colors.ON_SURFACE_VARIANT)
                                if pct is not None:
                                    progress_bar.value = pct
                                _upd(progress_bar)
                                _upd(status_text)
                                safe_page_update()

                            _append_log(f"Sync {desde} → {hasta}")
                            try:
                                res = sync.aplicar(desde, hasta, conn=conn, usar_dias=True)
                            except Exception as sync_exc:
                                _append_log(f"Sync exception: {sync_exc}")
                                raise
                            # Convert to plain dict to avoid mappingproxy issues
                            sync_res[0] = SyncResult(
                                estado=str(res.get("estado", "")),
                                filas=int(res.get("filas", 0) or 0),
                                dias=list(res.get("dias", []) or []),
                                dias_desfasados=list(res.get("dias_desfasados", []) or []),
                                dias_local_adelantado=list(
                                    res.get("dias_local_adelantado", []) or []
                                ),
                                segundos=float(res.get("segundos", 0) or 0),
                                modo=str(res.get("modo", "") or ""),
                            )
                        finally:
                            try:
                                conn.close()
                            except Exception:
                                pass
                            try:
                                cli.close()
                            except Exception:
                                pass

                        # Show result
                        _show_step(_Step.RESULT)

                    except Exception as e:
                        _append_log(f"Sync error: {e}")
                        sync_res[0] = SyncResult(error=str(e)[:300])
                        _show_step(_Step.ERROR)
                    finally:
                        try:
                            btn_sync.disabled = False
                            progress_bar.visible = False
                            _upd(btn_sync)
                            _upd(progress_bar)
                            safe_page_update()
                        except Exception:
                            pass

                threading.Thread(target=worker, daemon=True).start()

            def _on_ntfs(_):
                page.close(dlg)
                # Trigger the old NTFS sync path as fallback
                if hasattr(self, "_on_ntfs_callback"):
                    self._on_ntfs_callback()

            def _on_cartucho(_):
                page.close(dlg)
                if hasattr(self, "_on_cartucho_callback"):
                    self._on_cartucho_callback()

            def _on_diagnostic(_):
                page.close(dlg)
                try:
                    from src.core.api_robustness.check import check

                    result = check(server_url=url)
                    msg = f"=== DIAGNÓSTICO [{result.severity.upper()}] ===\n{result.summary}"
                    if result.recommendations:
                        msg += "\n\nRecomendaciones:\n"
                        for i, rec in enumerate(result.recommendations, 1):
                            msg += f"  {i}. {rec}\n"
                    self.app.show_snackbar(
                        msg, ft.Colors.WHITE if result.severity == "ok" else ft.Colors.ORANGE
                    )
                except Exception as e:
                    self.app.show_snackbar(mensaje(e, "correr el diagnóstico"), self.app.G360_ERROR)

            # ── Wire handlers ────────────────────────────────────────────────
            btn_login.on_click = _on_login
            btn_refresh.on_click = _on_refresh
            btn_skip_refresh.on_click = _on_skip_refresh
            btn_sync.on_click = _on_sync
            btn_ntfs.on_click = _on_ntfs
            btn_cartucho.on_click = _on_cartucho
            btn_diagnostic.on_click = _on_diagnostic

            # Actions del dialog (cierra y reporta el resultado si lo hay)
            def _dynamic_close(_):
                try:
                    page.close(dlg)
                except Exception:
                    pass
                try:
                    if self._result_callback and sync_res[0]:
                        self._result_callback(sync_res[0])
                except Exception as cb_exc:
                    log.exception("on_result callback failed: %s", cb_exc)
                    try:
                        self.app.show_snackbar(
                            f"Error al aplicar resultado: {cb_exc}",
                            self.app.G360_ERROR,
                        )
                    except Exception:
                        pass

            dlg.actions = [ft.TextButton("Cerrar", on_click=_dynamic_close)]
            dlg.actions_alignment = ft.MainAxisAlignment.END

            page.open(dlg)
            mounted[0] = True
        except Exception as e:
            log.exception("api_sync_modal.open fallo: %s", e)
            try:
                self.app.show_snackbar(
                    f"No se pudo abrir la sincronizacion: {e}", self.app.G360_ERROR
                )
            except Exception:
                pass

    def set_ntfs_callback(self, cb):
        """Set callback for NTFS fallback button."""
        self._on_ntfs_callback = cb

    def set_cartucho_callback(self, cb):
        """Set callback for cartucho fallback button."""
        self._on_cartucho_callback = cb
