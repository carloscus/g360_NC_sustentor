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
_fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
_log_dir = (
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    / "g360-erp-nc-sustentor"
)

# Nivel configurable sin recompilar. INFO por defecto; G360_LOG_LEVEL=DEBUG para
# depurar (el nivel DEBUG activa el volcado de arbol de controles).
_APP_LOG_LEVEL = getattr(logging, os.environ.get("G360_LOG_LEVEL", "INFO").upper(), logging.INFO)


def _build_file_handler() -> logging.Handler:
    """Handler de archivo rotado, o stderr si el disco no deja escribir.

    Antes esto era un `try/except: pass` y luego `_handler` se usaba tal cual
    mas abajo: si mkdir/RotatingFileHandler fallaba (disco lleno, ruta tomada,
    %APPDATA% inexistente) la app moria al importar con NameError y el motivo
    real quedaba tragado. Ahora degrada a consola y sigue arrancando.
    """
    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_file = _log_dir / "app.log"
        handler = RotatingFileHandler(
            _log_file,
            # 2 MB x 3 se quedaba corto: el ruido lo consumia en horas y
            # echaba los diagnostics utiles. 10 MB x 5 da unos dias.
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(_fmt)
        return handler
    except Exception as exc:  # pragma: no cover - depende del entorno
        sys.stderr.write(f"[log] sin archivo de log ({exc}); se registra en consola\n")
        fallback = logging.StreamHandler(sys.stderr)
        fallback.setFormatter(_fmt)
        return fallback


_handler = _build_file_handler()

# Un solo archivo para los loggers de la app: antes solo "g360.app" y
# "src.core.capture_service" escribian en app.log. Todo lo demas
# ("g360.ui", "g360.ui.card_db", "src.core.*", "src.ui.*") se perdia y solo
# llegaba a stderr, asi que un fallo de la card DB era invisible justo donde
# uno mira. Se enganchan los paquetes "g360" y "src" (no la raiz) para no
# arrastrar tambien el ruido de flet/httpx/urllib3.
for _name in ("g360", "src"):
    _lg = logging.getLogger(_name)
    if _handler not in _lg.handlers:
        _lg.addHandler(_handler)
    _lg.setLevel(_APP_LOG_LEVEL)

_logger.info(
    "Logging inicializado (nivel %s) en %s",
    logging.getLevelName(_APP_LOG_LEVEL),
    _log_dir / "app.log",
)

# Log de captura (paso a paso de descargas) a run_log.txt Y a consola:
# la UI muestra la tira de progreso, pero la consola queda como evidencia
# completa (util si se cierra la ventana o para soporte remoto).
_cap_logger = logging.getLogger("src.core.capture_service")
if not any(isinstance(h, logging.StreamHandler) for h in _cap_logger.handlers):
    _cons = logging.StreamHandler(sys.stdout)
    _cons.setFormatter(_fmt)
    _cap_logger.addHandler(_cons)
_cap_logger.setLevel(_APP_LOG_LEVEL)


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
        try:
            self._build_ui()
        except Exception as e:
            _logger.exception("Error construyendo UI principal: %s", e)
            try:
                self._build_fallback_ui(e)
            except Exception:
                _logger.exception("Error construyendo UI de respaldo: %s", e)
        self._aplicar_tema()

    # ── Page Setup ────────────────────────────────────────────────────────

    def _setup_page(self):
        self.page.title = "Reconocimiento Comercial - CIPSA"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0
        self.page.window.resizable = True
        # min_width baja a 950: el reporte de compras es lo que manda, y su
        # tabla mes×línea de 8 filas mide 790px. Con 950 el ancho útil queda en
        # 838 (950 - 64 vista - 32 card - 16 cuerpo), o sea 48px de holgura.
        # Por debajo de ~930 esa holgura desaparece y la tabla empieza a
        # scrollear antes de tiempo. Ver reporte_compras.ANCHO_UTIL_MIN.
        self.page.window.width = 1040
        self.page.window.height = 810
        self.page.window.min_width = 950
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

    def _build_badge_api(self) -> ft.Container:
        """Punto de estado del servidor (API) que se actualiza por polling.

        Lee el último resultado publicado por el health-check en background
        (``api_robustness.state.ultimo_health``), sin llamadas de red propias.
        Tooltip muestra detalle; click abre el modal de sync.
        """
        self.api_badge_dot = ft.Container(
            width=10,
            height=10,
            border_radius=5,
            bgcolor=G360Theme.text_muted_color(),
        )
        self.api_badge_txt = ft.Text(
            "API",
            size=12,
            color=G360Theme.text_muted_color(),
        )
        self.api_badge = ft.Container(
            content=ft.Row([self.api_badge_dot, self.api_badge_txt], spacing=6),
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            border_radius=9,
            bgcolor=G360Theme.surface_variant_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            on_click=self._on_badge_api_click,
            tooltip="API sin chequeo todavía",
        )
        self._actualizar_badge_api()
        return self.api_badge

    def _actualizar_badge_api(self) -> None:
        """Repinta el badge según el último health publicado."""
        try:
            from src.core.api_robustness.state import ultimo_health

            h = ultimo_health()
            dot = self.api_badge_dot
            txt = self.api_badge_txt
            badge = self.api_badge
            if h is None:
                dot.bgcolor = G360Theme.text_muted_color()
                txt.value = "API"
                txt.color = G360Theme.text_muted_color()
                badge.tooltip = "Chequeo de API pendiente"
                return

            color = None
            label = "API"
            if not h.get("api_online"):
                color = G360Theme.error_color()
                label = "API offline"
            else:
                horas = h.get("desfase_horas")
                if horas is None:
                    color = G360Theme.ok_color()
                elif horas > 24:
                    color = G360Theme.error_color()
                    label = f"snap {int(horas)}h"
                elif horas > 2:
                    color = G360Theme.warning_color()
                    label = f"snap {int(horas)}h"
                else:
                    color = G360Theme.ok_color()

            dot.bgcolor = color
            txt.value = label
            txt.color = color

            err = h.get("error")
            horas = h.get("desfase_horas")
            detalle = h.get("url") or ""
            if horas is not None:
                detalle += f"\nsnapshot: hace {horas:.0f}h"
            if err:
                detalle += f"\nerror: {err}"
            badge.tooltip = detalle.strip() or "API ok"
            try:
                badge.update()
            except Exception:
                pass
        except Exception:
            pass

    def _on_badge_api_click(self, e):
        """Clic en el badge del header → abre el modal de sync."""
        try:
            view = getattr(self, "reco_view", None)
            if view is not None and hasattr(view, "_actualizar_hoy"):
                view._actualizar_hoy(e)
        except Exception:
            pass

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
        self._loading_subtitle = ft.Text("Un momento por favor", size=12, color="white70")
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
        if not hasattr(self, "_badge_api"):
            self._badge_api = self._build_badge_api()

        def _align(v):
            if v is None:
                return None
            return getattr(v, "value", v)

        def _btn_style(**kwargs):
            return ft.ButtonStyle(**{k: v for k, v in kwargs.items() if v is not None})

        self._theme_button = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            icon_size=18,
            tooltip="Cambiar tema",
            on_click=lambda _: self._toggle_theme(None),
            style=_btn_style(
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
            style=_btn_style(
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
            style=_btn_style(
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
                                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                            ),
                        ],
                        vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                    ),
                    ft.Container(expand=True),
                    self._badge_api,
                    ft.Container(width=6),
                    plantillas_btn,
                    ft.Container(width=6),
                    reset_btn,
                    ft.Container(width=6),
                    self._theme_button,
                ],
                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
            ),
        )

        self._theme_button = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            icon_size=18,
            tooltip="Cambiar tema",
            on_click=lambda _: self._toggle_theme(None),
            style=_btn_style(
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
            style=_btn_style(
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
            style=_btn_style(
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
                                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                            ),
                        ],
                        vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                    ),
                    ft.Container(expand=True),
                    self._badge_api,
                    ft.Container(width=6),
                    plantillas_btn,
                    ft.Container(width=6),
                    reset_btn,
                    ft.Container(width=6),
                    self._theme_button,
                ],
                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
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
                                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                            ),
                        ],
                        vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
                    ),
                    ft.Container(expand=True),
                    self._badge_api,
                    ft.Container(width=6),
                    plantillas_btn,
                    ft.Container(width=6),
                    reset_btn,
                    ft.Container(width=6),
                    self._theme_button,
                ],
                vertical_alignment=_align(ft.CrossAxisAlignment.CENTER),
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
        try:
            if _logger.isEnabledFor(logging.DEBUG):
                _dump_control_tree(self.body, label="body")
        except Exception:
            pass
        try:
            self.page.add(self.body)
        except Exception as exc:
            _logger.exception("page.add(self.body) fallo: %s", exc)
            try:
                _dump_control_tree(self.body, label="body-after-fail")
            except Exception:
                pass
            raise

    def _build_fallback_ui(self, error: Exception):
        """UI mínima de respaldo cuando la vista principal falla al iniciar."""
        try:
            import traceback

            self._header = self._build_header()
            error_text = ft.Text(
                "No se pudo inicializar la interfaz principal.",
                size=13,
                color=self.G360_ERROR,
            )
            detail_lines = traceback.format_exc().splitlines()
            detail_preview = "\n".join(detail_lines[:8])
            detail_text = ft.Text(
                f"Detalle: {error}\n{detail_preview}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
            body_content = ft.Column(
                [
                    error_text,
                    ft.Container(height=8),
                    detail_text,
                ],
                expand=True,
                spacing=0,
            )
            self.body = ft.Container(
                content=ft.Column(
                    [
                        self._header,
                        body_content,
                        self._build_footer(),
                    ],
                    expand=True,
                    spacing=0,
                ),
                expand=True,
                padding=ft.padding.all(16),
            )
            if self.page.controls:
                self.page.controls.clear()
            self.page.add(self.body)
            self._aplicar_tema()
        except Exception as fallback_error:
            _logger.exception("Error construyendo UI de respaldo: %s", fallback_error)

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

    # Diagnóstico silencioso en segundo plano: cada 5 min verifica el estado
    # de la API y notifica solo en casos críticos (servidor offline o snapshot
    # muy viejo). No bloquea la UI ni genera ruido.
    _start_background_health_check(globals()["_gapp_instance"])


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


def _start_background_health_check(app):
    """Thread de fondo que verifica el estado de la API cada 5 min.

    Solo muestra notificaciones en casos críticos:
    - Servidor completamente offline (no responde health)
    - Snapshot con más de 24h de atraso

    Advertencias normales (snapshot 2-24h viejo) se loguean pero no molestan.
    """
    import time

    def _worker():
        from src.core.api_robustness.check import check, _default_server_url
        from src.core.api_robustness.state import publicar_health

        url = _default_server_url()
        ultimo_warning = {}  # key -> timestamp, para no repetir el mismo warning

        while True:
            time.sleep(300)  # cada 5 min
            try:
                if not url:
                    continue
                r = check(server_url=url)

                # Publica el estado consolidado para la UI sin red adicional.
                c = r.client
                publicar_health(
                    {
                        "api_online": bool(c and c.http_8090_reachable and c.health_ok),
                        "desfase_horas": c.desfase_horas if c else None,
                        "url": url,
                        "error": (None if not c or not c.errors else "; ".join(c.errors)[:200]),
                        "checked_at": time.time(),
                    }
                )
                # Repinta el badge del header desde el hilo UI.
                try:
                    app._actualizar_badge_api()
                except Exception:
                    pass

                if r.severity == "critical":
                    key = "critical"
                    if key not in ultimo_warning or time.time() - ultimo_warning[key] > 600:
                        ultimo_warning[key] = time.time()
                        msg = (
                            "🔴 Servidor de datos no disponible. "
                            "Verifica la red o la URL del servidor."
                        )
                        try:
                            app.show_snackbar(msg, app.G360_ERROR)
                        except Exception:
                            pass
                elif r.severity == "warning" and r.client:
                    key = "snapshot_viejo"
                    if r.client.desfase_horas > 24:
                        if key not in ultimo_warning or time.time() - ultimo_warning[key] > 600:
                            ultimo_warning[key] = time.time()
                            msg = (
                                f"⚠️ Snapshot de la API tiene {r.client.desfase_horas:.0f}h "
                                "de atraso. Configura → Gestión → Forzar refresh."
                            )
                            try:
                                app.show_snackbar(msg, app.G360_WARNING)
                            except Exception:
                                pass
            except Exception:
                pass  # Silenciar cualquier error del diagnóstico

    threading.Thread(target=_worker, daemon=True, name="api-health-poll").start()
    _logger.info("Background health check started (interval=5min)")


def _db_path_str() -> str:
    try:
        from src.core.ventas_db import db_path

        return str(db_path())
    except Exception:
        return "?"


def _dump_control_tree(control, label="", depth=0):
    """Recorre el árbol de controles Flet y loguea tipos y propiedades sospechosas."""
    import logging as _init_logging
    from types import MappingProxyType

    _log = _init_logging.getLogger("g360.app")
    try:
        name = type(control).__name__
        txt = f"{'  ' * depth}{label} {name}"
        suspicious = []
        try:
            props = vars(control)
        except TypeError:
            props = {}
        for k, v in props.items():
            if isinstance(v, MappingProxyType):
                suspicious.append(f"{k}=MappingProxyType")
        if suspicious:
            _log.warning("%s SUSPECT %s", txt, ", ".join(suspicious))
        else:
            _log.debug("%s", txt)
        children = getattr(control, "controls", None)
        if isinstance(children, list):
            for i, c in enumerate(children):
                try:
                    _dump_control_tree(c, label=f"[{i}]", depth=depth + 1)
                except Exception:
                    pass
        content = getattr(control, "content", None)
        if content is not None and not isinstance(children, list):
            try:
                _dump_control_tree(content, label="content:", depth=depth + 1)
            except Exception:
                pass
    except Exception:
        pass


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
