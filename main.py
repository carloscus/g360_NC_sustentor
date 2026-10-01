from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

import flet as ft

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.core.g360_theme import G360Theme
from src.ui.template_dialog import mostrar_dialogo_plantillas

try:
    from g360.ui.signature import G360Signature
except ImportError:
    G360Signature = None

_logger = logging.getLogger("g360.app")
if not _logger.handlers:
    _fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    # Log a APPDATA para evitar PermissionError cuando el .bat tiene el archivo abierto
    _log_dir = (
        Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        / "g360-erp-nc-sustentor"
    )
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / "app.log"
    _handler = RotatingFileHandler(
        _log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _handler.setFormatter(_fmt)
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)

# Log de captura (paso a paso de descargas) a run_log.txt Y a consola:
# la UI muestra la tira de progreso, pero la consola queda como evidencia
# completa (util si se cierra la ventana o para soporte remoto).
_cap_logger = logging.getLogger("src.core.capture_service")
_cap_logger.addHandler(_handler)
_cons = logging.StreamHandler(sys.stdout)
_cons.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
_cap_logger.addHandler(_cons)
_cap_logger.setLevel(logging.INFO)


# ── Single instance (multi-PC: evitar dobles capturas por abrir 2 veces) ─────

LOCK_FILE = BASE_DIR / ".app_instance.lock"


def _acquire_instance_lock() -> bool:
    """True si somos la unica instancia. Windows: abrir archivo en exclusiva."""
    try:
        import msvcrt

        _fh = open(LOCK_FILE, "a+")  # noqa: SIM115 — se mantiene abierto como lock
        try:
            msvcrt.locking(_fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            _fh.close()
            return False
        globals()["_instance_fh"] = _fh  # mantener abierto mientras vivimos
        return True
    except Exception:
        return True  # si falla el mecanismo, no bloquear el arranque


def _log_inicio():
    _logger.info("=" * 60)
    _logger.info("Arranque de aplicacion (PID %s)", os.getpid())
    _logger.info("Python: %s | flet: %s", sys.version.split()[0], _flet_version())
    _logger.info("DB local: %s (existe=%s)", _db_path_str(), Path(_db_path_str()).exists())


class G360App:
    """App principal G360 — patron pulido:

    * AppBar sin sidebar (maximiza area de trabajo)
    * Dual theme dark/light con G360Theme classmethod dinamico
    * Theme se propaga a todas las cards via reconstruccion controlada
    * Plantillas accesible desde header (sin duplicados)
    * Signature en footer (esquina inferior derecha)
    * Boton reinicio en header
    * Loading overlay centralizado
    """

    def __init__(self, page: ft.Page):
        self.page = page
        self._is_dark = True

        # Colores de marca (siempre iguales, independientes del tema)
        self.G360_SUCCESS = G360Theme.SUCCESS
        self.G360_WARNING = G360Theme.WARNING
        self.G360_ERROR = G360Theme.ERROR
        self.G360_ACCENT = G360Theme.ACCENT
        self.G360_ACCENT_2 = G360Theme.ACCENT_2

        self._setup_page()
        self._loading_count = 0
        self._picker_callback = None
        self._picker_files_callback = None
        self._picking_lock = threading.Lock()

        self._init_components()
        self._build_ui()
        self._aplicar_tema()

    # ── Page Setup ────────────────────────────────────────────────────────

    def _setup_page(self):
        self.page.title = "Reconocimiento Comercial - CIPSA"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0
        self.page.window.resizable = True
        self.page.window.width = 1100
        self.page.window.height = 810
        self.page.window.min_width = 960
        self.page.window.min_height = 640
        self._aplicar_tema()

    def _aplicar_tema(self):
        """Aplica el modo theme a la pagina y propaga colores a toda la UI."""
        self.page.theme_mode = ft.ThemeMode.DARK if self._is_dark else ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(color_scheme_seed="blue")
        G360Theme.set_theme_mode(self._is_dark)
        self.G360_SUCCESS = G360Theme.success_color()
        self.G360_WARNING = G360Theme.warning_color()
        self.G360_ERROR = G360Theme.error_color()
        self.G360_ACCENT = G360Theme.accent_color()
        self.G360_ACCENT_2 = G360Theme.ACCENT_2
        self.page.bgcolor = G360Theme.bg_color()
        self._update_theme_indicators()
        self._propagar_tema()
        if self.page:
            self.page.update()

    def _update_theme_indicators(self):
        """Sincroniza icono del boton tema con modo actual."""
        if hasattr(self, "_theme_button"):
            name = ft.Icons.DARK_MODE_OUTLINED if self._is_dark else ft.Icons.LIGHT_MODE
            self._theme_button.icon = name

    def _toggle_theme(self, e):
        """Alterna dark/light y propaga colores sin reconstruir (preserva estado)."""
        self._is_dark = not self._is_dark
        self._aplicar_tema()

    def _propagar_tema(self):
        """Actualiza colores G360 de body y header in-place (sin perder estado)."""
        if hasattr(self, "body") and self.body is not None:
            G360Theme.apply_theme(self.body)
        if hasattr(self, "_header") and self._header is not None:
            G360Theme.apply_theme(self._header)

    # ── Loading & Feedback ────────────────────────────────────────────────

    def show_loading(self, message: str = "Procesando..."):
        self._loading_count += 1
        self._loading_title.value = message
        self._loading_subtitle.value = "Esto puede tomar unos segundos"
        self.loading_overlay.visible = True
        if self.page:
            self.page.update()

    def hide_loading(self):
        self._loading_count = max(0, self._loading_count - 1)
        if self._loading_count == 0:
            self.loading_overlay.visible = False
            if self.page:
                self.page.update()

    def show_snackbar(
        self,
        message: str,
        color: str | None = None,
        action_text: str | None = None,
        action_callback=None,
    ):
        if color in (self.G360_ERROR, G360Theme.ERROR, G360Theme.ERROR_LIGHT):
            bg_color = "#b91c1c"
        elif color in (self.G360_WARNING, G360Theme.WARNING, G360Theme.WARNING_LIGHT):
            bg_color = "#b45309"
        elif color in (
            self.G360_SUCCESS,
            G360Theme.SUCCESS,
            G360Theme.ACCENT,
            G360Theme.ACCENT_LIGHT,
        ):
            bg_color = G360Theme.primary_color()
        else:
            bg_color = color or G360Theme.primary_color()
        if not hasattr(self, "_snackbar") or self._snackbar is None:
            self._snackbar = ft.SnackBar(
                content=ft.Text(message, size=14, weight=ft.FontWeight.W_500),
                bgcolor=bg_color,
                padding=20,
                duration=4000,
                action=action_text,
                action_color="white",
                on_action=action_callback,
            )
            self.page.overlay.append(self._snackbar)
        else:
            self._snackbar.content = ft.Text(message, size=14, weight=ft.FontWeight.W_500)
            self._snackbar.bgcolor = bg_color
            self._snackbar.action = action_text
            self._snackbar.on_action = action_callback
        self._snackbar.open = True
        self.page.update()

    # ── Components ────────────────────────────────────────────────────────

    def _init_components(self):
        self._loading_title = ft.Text(
            "Procesando...", size=16, color="white", weight=ft.FontWeight.W_600
        )
        self._loading_subtitle = ft.Text("Un momento por favor", size=11, color="white70")
        # Overlay minimalista: glass-morphism + spinner centrado
        self.loading_overlay = ft.Container(
            visible=False,
            expand=True,
            # Scrim oscuro en ambos temas (convención Material): con 0.55 el
            # texto blanco del overlay mantiene ≥4.5:1 también en tema claro.
            bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
            content=ft.Column(
                [
                    ft.Container(expand=True),
                    ft.Row(
                        [
                            ft.Container(expand=True),
                            ft.Column(
                                [
                                    # ProgressRing: nativo, GPU-accelerated, minimo CPU
                                    ft.ProgressRing(
                                        width=44,
                                        height=44,
                                        stroke_width=3,
                                        color=self.G360_ACCENT,
                                    ),
                                    ft.Container(height=14),
                                    self._loading_title,
                                    self._loading_subtitle,
                                ],
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=2,
                            ),
                            ft.Container(expand=True),
                        ]
                    ),
                    ft.Container(expand=True),
                ]
            ),
        )
        self.fp_generic = ft.FilePicker(on_result=self._on_picker_generic)
        self.page.overlay.extend([self.fp_generic, self.loading_overlay])

    # ── Footer ────────────────────────────────────────────────────────────

    def _build_footer(self) -> ft.Container:
        """Footer con signature en esquina inferior derecha."""
        sig = G360Signature(mode="powered", version="2.0", opacity=0.5) if G360Signature else None
        if sig is None:
            return ft.Container(height=0)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    sig,
                ]
            ),
            bgcolor=G360Theme.surface_color(),
            border=ft.border.only(top=ft.border.BorderSide(0.5, G360Theme.border_subtle_color())),
            padding=ft.padding.only(right=20, top=4, bottom=4),
        )

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self) -> ft.Container:
        """Header propio (sin AppBar de Flet) para evitar overflow/superposicion:
        un Container(expand=True) absorbe el espacio sobrante entre titulo y botones."""
        self._theme_button = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            icon_size=18,
            tooltip="Cambiar tema",
            on_click=lambda _: self._toggle_theme(None),
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                bgcolor={"hovered": ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)},
                padding=ft.padding.all(8),
            ),
        )

        plantillas_btn = ft.ElevatedButton(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.PLAYLIST_PLAY_OUTLINED,
                        size=14,
                        color="white",
                    ),
                    ft.Text("Plantillas", size=13, color="white"),
                ],
                spacing=6,
            ),
            height=34,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=14),
                shape=ft.RoundedRectangleBorder(radius=12),
                bgcolor=G360Theme.primary_color(),
                elevation=0,
            ),
            on_click=lambda _: mostrar_dialogo_plantillas(self.page, self),
        )

        reset_btn = ft.IconButton(
            icon=ft.Icons.RESTART_ALT,
            icon_size=18,
            tooltip="Reiniciar",
            on_click=lambda _: self.reset_app(None),
            style=ft.ButtonStyle(
                color=ft.Colors.ON_SURFACE_VARIANT,
                bgcolor={"hovered": ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)},
                padding=ft.padding.all(8),
            ),
        )

        return ft.Container(
            height=64,
            bgcolor=G360Theme.surface_color(),
            border=ft.border.only(bottom=ft.border.BorderSide(1, G360Theme.border_subtle_color())),
            padding=ft.padding.symmetric(horizontal=16),
            content=ft.Row(
                [
                    # Logo + titulo horizontal
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Image(
                                    src="/images/Logo_cipsa_solid.png",
                                    width=44,
                                    height=44,
                                    fit=ft.ImageFit.CONTAIN,
                                ),
                            ),
                            ft.Container(width=12),
                            ft.Row(
                                [
                                    ft.Text(
                                        "NC Sustentor",
                                        size=19,
                                        weight=ft.FontWeight.W_800,
                                        color=ft.Colors.ON_SURFACE,
                                    ),
                                    ft.Container(width=4),
                                    ft.Container(
                                        width=1,
                                        height=20,
                                        bgcolor=G360Theme.border_color(),
                                    ),
                                    ft.Container(width=8),
                                    ft.Text(
                                        "Reconocimiento Comercial",
                                        size=14,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=0,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # Absorbe el espacio sobrante: los botones quedan fijos a la derecha
                    ft.Container(expand=True),
                    plantillas_btn,
                    ft.Container(width=6),
                    reset_btn,
                    ft.Container(width=6),
                    self._theme_button,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        from src.ui.reconocimiento_view import ReconocimientoView

        self._header = self._build_header()
        self.reco_view = ReconocimientoView(self)
        content = self.reco_view.build()
        body_content = ft.Column(
            [
                content,
                self._build_footer(),
            ],
            expand=True,
            spacing=0,
        )
        self.body = ft.Container(
            content=ft.Column(
                [
                    self._header,
                    body_content,
                ],
                expand=True,
                spacing=0,
            ),
            expand=True,
        )
        self.page.add(self.body)

    # ── File Picker ───────────────────────────────────────────────────────

    def _on_picker_generic(self, e: ft.FilePickerResultEvent):
        cb = self._picker_callback
        cb_files = self._picker_files_callback
        self._picker_callback = None
        self._picker_files_callback = None
        if cb:
            ruta = e.files[0].path if e.files else None
            cb(ruta)
        elif cb_files:
            rutas = [f.path for f in e.files if f.path] if e.files else []
            cb_files(rutas)
        if self.page:
            self.page.update()

    def _pick_file(
        self, dialog_title: str = "Seleccionar archivo", allowed_extensions: list | None = None
    ) -> str:
        if not self._picking_lock.acquire(blocking=False):
            return None
        try:
            result = [None]
            event = threading.Event()

            def on_pick(ruta):
                result[0] = ruta
                event.set()

            def schedule_pick():
                self._picker_callback = on_pick
                self.fp_generic.pick_files(
                    allowed_extensions=allowed_extensions or ["xlsx", "xls", "csv"],
                    dialog_title=dialog_title,
                )

            if self.page:
                self.page.run_thread(schedule_pick)

            event.wait(timeout=60)
            return result[0]
        finally:
            self._picking_lock.release()

    def _pick_files(self, dialog_title: str = "Seleccionar archivos") -> list:
        if not self._picking_lock.acquire(blocking=False):
            return []
        try:
            result = [None]
            event = threading.Event()

            def on_pick(rutas):
                result[0] = rutas
                event.set()

            def schedule_pick():
                self._picker_files_callback = on_pick
                self.fp_generic.pick_files(
                    allowed_extensions=["xlsx", "xls", "csv"],
                    dialog_title=dialog_title,
                    allow_multiple=True,
                )

            if self.page:
                self.page.run_thread(schedule_pick)

            event.wait(timeout=60)
            return result[0] if result[0] else []
        finally:
            self._picking_lock.release()

    # ── Reset ─────────────────────────────────────────────────────────────

    def reset_app(self, e=None):
        from src.ui.reconocimiento_view import ReconocimientoView

        view = ReconocimientoView(self)
        view.reset()
        content = view.build()
        # Reemplazar solo el contenido, preservar header y footer
        self.body.content = ft.Column(
            [
                self._header,
                content,
                self._build_footer(),
            ],
            expand=True,
            spacing=0,
        )
        if self.page:
            self.page.update()
        self.show_snackbar("App reiniciado", self.G360_SUCCESS)

    # ── Desktop Path ──────────────────────────────────────────────────────

    def _get_desktop_path(self) -> Path:
        onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
        if onedrive_desktop.exists():
            return onedrive_desktop
        return Path.home() / "Desktop"


def main(page: ft.Page):
    print(
        "Reconocimiento Comercial - CIPSA iniciado. Cierre la ventana de la aplicacion para salir.",
        flush=True,
    )
    _log_inicio()

    # Errores de render/eventos del cliente Flet -> run_log (antes se perdian y
    # la ventana quedaba en blanco sin pista alguna)
    def _on_flet_error(e):
        _logger.error("FLET ERROR: %s", getattr(e, "data", e))

    page.on_error = _on_flet_error

    try:
        globals()["_gapp_instance"] = G360App(page)
        print("G360App creado exitosamente", flush=True)
    except Exception as e:
        print(f"FATAL al crear G360App: {e}", flush=True)
        _logger.exception("FATAL al crear G360App")
        raise

    # Pre-calienta el catalogo de lineas (distinct_lineas tarda ~28s sobre la
    # DB real) para que el modal de config abra al instante. Si la DB local
    # está vacía (PC nueva), busca la DB fuente y propone la primera carga.
    try:
        from src.core import ventas_db

        if ventas_db.db_exists() and ventas_db.db_is_populated():
            ventas_db.refresh_lineas_async()
            ventas_db.refresh_card_info_async()
            ventas_db.refresh_stats_cache_async()
        else:
            _proponer_carga_inicial(page, globals()["_gapp_instance"])
    except Exception:
        _logger.exception("pre-warm de lineas fallo")


def _flet_version() -> str:
    try:
        from importlib.metadata import version

        return version("flet")
    except Exception:
        return "?"


def _proponer_carga_inicial(page, app) -> None:
    """Si la DB local está vacía y existe una DB fuente, sugiere la primera
    carga (modal con ventana 5/10/todos años). Corrida en background para no
    bloquear el arranque (buscar_db_remota comprueba rutas conocidas)."""

    def worker():
        try:
            from src.core import ventas_db

            if ventas_db.db_is_populated():
                return
            from src.core.db_network import buscar_db_remota

            info = buscar_db_remota()
            if not info:
                return
            view = getattr(app, "reco_view", None)
            if view is None or not hasattr(view, "sugerir_carga_inicial"):
                return
            view.sugerir_carga_inicial(page, info)
        except Exception:
            _logger.exception("sugerencia de carga inicial falló")

    threading.Thread(target=worker, daemon=True).start()


def _db_path_str() -> str:
    try:
        from src.core.ventas_db import db_path

        return str(db_path())
    except Exception:
        return "?"


if __name__ == "__main__":
    try:
        if not _acquire_instance_lock():
            msg = (
                "Ya existe una instancia de la aplicacion en ejecucion.\n\n"
                "Cierra la ventana abierta de G360 NC Sustentor (o el proceso "
                "python.exe desde el Administrador de tareas) y vuelve a intentar."
            )
            print(msg, flush=True)
            _logger.warning("Segundo arranque bloqueado (single instance)")
            try:
                # MessageBox grafica: visible aunque la consola este minimizada
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, msg, "G360 NC Sustentor", 0x40)
            except Exception:
                input("Presione Enter para salir...")
            sys.exit(0)
        ft.app(
            target=main,
            view=ft.AppView.FLET_APP,
            assets_dir="assets",
            host="127.0.0.1",
            port=0,
        )
    except Exception as e:
        print(f"\n[FATAL] Error al iniciar la aplicacion: {e}", flush=True)
        _logger.exception("FATAL al iniciar")
        traceback.print_exc()
        input("\nPresione Enter para salir...")
