# -*- coding: utf-8 -*-
import flet as ft
import pandas as pd
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

_logger = logging.getLogger("g360.app")


def _safe_update(page):
    """page.update() seguro para background threads — traga Event loop is closed."""
    try:
        page.update()
    except (RuntimeError, Exception):
        pass


def _cartucho_previas(carpeta_export) -> list:
    """Cartuchos .zip ya generados en la carpeta de export (mas nuevo primero)."""
    try:
        return sorted(
            (p for p in Path(carpeta_export).glob("cartucho-*.zip") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def _msgs_cartucho(carpeta_export, r=None, ex=None) -> tuple[str, str]:
    """Mensajes de estado del export de cartucho.

    Devuelve (texto_status, texto_snackbar) para los tres momentos del flujo:
    creando (r/ex=None), creado (r=dict) y error (ex=Exception). Vive fuera
    del handler anidado para poder testear los textos sin montar la card.
    """
    carpeta = str(carpeta_export)
    if ex is not None:
        if isinstance(ex, FileExistsError):
            return (
                f"✗ Ya existe un cartucho con ese nombre: {ex}\n"
                f"Espera un momento y vuelve a exportar.",
                "✗ Ya existe un cartucho con ese nombre. Reintenta.",
            )
        return (f"✗ No se pudo crear el cartucho: {ex}", f"✗ No se pudo crear el cartucho: {ex}")
    if r is not None:
        filas = r.get("ventas_filas", 0)
        mb = r.get("zip_mb", 0)
        return (
            f"✓ Cartucho creado\n{r.get('zip')}\n"
            f"{filas:,} filas · {mb} MB. "
            f"Copialo a la otra PC (Importar cartucho).",
            f"✓ Cartucho creado ({filas:,} filas, {mb} MB). Está en {carpeta}",
        )
    previas = _cartucho_previas(carpeta_export)
    if previas:
        mb = sum(p.stat().st_size for p in previas) / 1024 / 1024
        info = f"Hay {len(previas)} cartucho(s) anterior(es) ({mb:,.0f} MB) en la misma carpeta."
    else:
        info = "Es el primero en esa carpeta."
    return (
        f"⏳ Creando cartucho…\nCarpeta: {carpeta}\n"
        f"Verifica el contrato de forma, hace checkpoint y comprime "
        f"(puede tardar varios minutos).",
        f"⏳ Creando cartucho… {info}",
    )


def _correr_export_cartucho(
    carpeta_export, status, btn_export, app, page, exportar
) -> threading.Thread:
    """Exporta el cartucho en background avisando cada etapa.

    Deshabilita el boton mientras corre (el doble click crearia dos cartuchos
    y el segundo fallaria por nombre duplicado) y lo rehabilita SIEMPRE, en un
    finally: si solo se hiciera en el camino feliz, un error dejaria el boton
    muerto y habria que recargar la app para reintentar.

    `exportar` se inyecta para poder testear el flujo sin comprimir 3 GB.
    Devuelve el Thread (los tests hacen join).
    """
    btn_export.disabled = True
    status.value, aviso = _msgs_cartucho(carpeta_export)
    status.color = app.G360_ACCENT
    app.show_snackbar(aviso, app.G360_ACCENT)
    _safe_update(page)

    def run():
        try:
            r = exportar()
            texto, aviso = _msgs_cartucho(carpeta_export, r=r)
            status.value = texto
            status.color = app.G360_SUCCESS
            app.show_snackbar(aviso, app.G360_SUCCESS)
        except Exception as ex:  # noqa: BLE001 - se muestra al usuario
            texto, aviso = _msgs_cartucho(carpeta_export, ex=ex)
            status.value = texto
            status.color = app.G360_ERROR
            app.show_snackbar(aviso, app.G360_ERROR)
        finally:
            btn_export.disabled = False
        _safe_update(page)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _rango_sync(dias: int = 90) -> tuple[str, str]:
    """Ventana por defecto del sync: los ultimos `dias` dias.

    Termina en la ultima fecha con data local (no en hoy) para no pedirle al
    servidor un rango lleno de dias vacios, y si la DB esta vacia, hoy.
    """
    from src.core import ventas_db

    info = ventas_db.db_health() or {}
    hasta = str(info.get("fecha_max") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    try:
        f = datetime.strptime(hasta, "%Y-%m-%d")
    except ValueError:
        f = datetime.now()
    return (f - timedelta(days=dias)).strftime("%Y-%m-%d"), f.strftime("%Y-%m-%d")


def _msgs_sync_api(wake=None, res=None, sin_token=False, ex=None) -> tuple[str, bool]:
    """(texto, ok) del estado del sync, para no duplicar los textos en la UI."""
    if ex is not None:
        return f"✗ Sync desde la API falló: {ex}", False
    if sin_token:
        return "✗ Sin token de API: conectate antes con usuario/clave.", False
    if wake is not None and not wake.get("ok"):
        return f"✗ API de ventas no disponible: {wake.get('detalle')}", False
    if res is None:
        return "", True
    if wake and wake.get("arrancada"):
        extra = f" · API despertada en WSL ({wake.get('segundos')}s)"
    else:
        extra = ""
    if res.get("estado") == "sin_cambios":
        return (
            f"✓ API al día: sin cambios entre {res.get('desde')} y {res.get('hasta')} "
            f"({res.get('segundos')}s){extra}",
            True,
        )
    adelante = len(res.get("dias_local_adelantado") or [])
    extra_local = f" · {adelante} días con data local adelantada" if adelante else ""
    return (
        f"✓ API: {res.get('filas', 0):,} filas en {len(res.get('dias') or [])} días "
        f"({len(res.get('dias_desfasados') or [])} días desfasados, "
        f"{res.get('folios_faltantes', 0)} folios nuevos, {res.get('segundos')}s)"
        f"{extra}{extra_local}",
        True,
    )


def _token_valido_para(cli, api_url: str, renovar: bool = False) -> str:
    """Devuelve un token usable, renovandolo si el cacheado ya no sirve.

    El token cacheado caduca (y ademas queda inservible cada vez que el API
    se reinicia con otro secreto), asi que estar cacheado no significa estar
    vigente. Contra un 401 se renueva con usuario/clave y se reintenta UNA vez:
    si el 401 se repite, el problema no es el token.

    `renovar=True` fuerza el login sin mirar el cache (para el reintento).

    Levanta TokenInvalidoError si no hay forma de obtener uno.
    """
    from src.core.capture_service import CaptureService
    from src.core.ventas_api_client import TokenInvalidoError

    def _sin_token(msg):
        return TokenInvalidoError(msg, status=401)

    if renovar:
        nuevo = _renovar_token()
        if not nuevo:
            raise _sin_token("el token de API caduco y no hay usuario/clave para renovarlo")
        cli.set_token(nuevo)
        return nuevo

    tok = CaptureService.api_token() or _renovar_token()
    if not tok:
        raise _sin_token("sin token de API: conectate antes con usuario/clave")

    cli.set_token(tok)
    try:
        cli.health()
    except TokenInvalidoError:
        nuevo = _renovar_token()
        if not nuevo:
            raise _sin_token(
                "el token de API caduco y no hay usuario/clave para renovarlo"
            ) from None
        cli.set_token(nuevo)
        return nuevo
    return tok


def _renovar_token() -> str:
    """Login contra la API Go con las credenciales guardadas. "" si no puede."""
    from src.core.capture_service import CaptureService

    user, pwd = CaptureService.credentials()
    if not (user and pwd):
        return ""
    CaptureService.refresh_api_token_best_effort(user, pwd)
    return CaptureService.api_token()


def _correr_sync_api(
    status,
    btn_sync,
    app,
    page,
    api_url,
    set_busy,
    al_actualizar=None,
    ventana_dias: int = 90,
    wake=None,
    cliente=None,
    sync=None,
    rango=None,
    registrar=None,
    token_valido=None,
    al_final=None,
) -> threading.Thread:
    """Actualiza desde la API, despertando WSL primero si hace falta.

    El servidor vive en WSL y se apaga con la distro, asi que el health check va
    PRIMERO: si no responde, arranca la API y espera a que responda, y recien
    ahi pide token y sincroniza. Todo corre en un thread de fondo con el boton
    deshabilitado y rehabilitado siempre en un finally.

    `wake`, `cliente`, `sync`, `rango`, `registrar` y `token_valido` se inyectan
    para testear el flujo completo sin WSL, API ni credenciales reales.
    Devuelve el Thread (join en tests).
    """
    wake = wake or (lambda url: _asegurar_api(url=url))
    rango = rango or (lambda: _rango_sync(ventana_dias))
    registrar = registrar or (lambda msg: None)
    factory = cliente

    def _nuevo_cliente():
        nonlocal factory
        if factory is None:
            from src.core.ventas_api_client import VentaAPIClient

            factory = lambda: VentaAPIClient(api_url, timeout=120.0)  # noqa: E731
        return factory()

    def _log(msg: str):
        try:
            registrar(msg)
        except Exception:
            pass

    def _estado(texto: str, ok: bool):
        status.value = texto
        status.color = app.G360_SUCCESS if ok else app.G360_ERROR
        _safe_update(page)

    def _cerrar(texto: str, ok: bool):
        """Ultima palabra del flujo: avisa aunque el flujo corte temprano.

        Deja el texto en `status` SIEMPRE (el panel de intranet lo muestra) y
        ademas llama a `al_final` si lo hay (la card de la ventana principal no
        tiene linea de estado y usa snackbar). Sin esto, los `return` temprano
        —API caida, sin token— dejarian al usuario mirando como desaparece el
        overlay de carga sin explicacion.
        """
        _estado(texto, ok)
        if al_final is None:
            return
        try:
            al_final(texto, ok)
        except Exception:
            _logger.exception("al_final del sync API fallo")

    def _sin_token():
        _cerrar(*_msgs_sync_api(sin_token=True))

    def _api_caida(info):
        _cerrar(*_msgs_sync_api(wake=info))

    btn_sync.disabled = True
    status.value = "⏳ Verificando la API de ventas (WSL)..."
    status.color = ft.Colors.ON_SURFACE_VARIANT
    _safe_update(page)

    def run():
        cli = None
        try:
            from src.core.capture_service import CaptureService
            from src.core.ventas_api_client import TokenInvalidoError

            info = wake(api_url)
            if not info.get("ok"):
                _api_caida(info)
                _log(
                    f"[{datetime.now().strftime('%H:%M:%S')}] API no disponible: "
                    f"{info.get('detalle')}"
                )
                return
            if info.get("arrancada"):
                _log(
                    f"[{datetime.now().strftime('%H:%M:%S')}] API de ventas despertada "
                    f"en WSL en {info.get('segundos')}s"
                )

            if token_valido:
                cli = _nuevo_cliente()
                cli.set_token(token_valido)
            elif token_valido is not None:
                # token_valido="" explicito (tests): no hay token, no hay cliente.
                _sin_token()
                return
            else:
                try:
                    tok = CaptureService.api_token()
                except Exception:
                    tok = ""
                if not tok:
                    _sin_token()
                    return
                cli = _nuevo_cliente()
                cli.set_token(_token_valido_para(cli, api_url))

            desde, hasta = rango()
            factory = cliente
            if factory is None:
                from src.core.ventas_api_client import VentaAPIClient

                factory = lambda: VentaAPIClient(api_url, timeout=120.0)  # noqa: E731
            set_busy(True)
            try:
                res = sync(cli, desde, hasta) if sync else _sync_por_defecto(cli, desde, hasta)
            except TokenInvalidoError:
                # El token pudo caducar entre la validacion y la descarga.
                cli.set_token(_token_valido_para(cli, api_url, renovar=True))
                res = sync(cli, desde, hasta) if sync else _sync_por_defecto(cli, desde, hasta)
            finally:
                set_busy(False)
            texto, ok = _msgs_sync_api(wake=info, res=res)
            _cerrar(texto, ok)
            if ok and res.get("estado") != "sin_cambios" and al_actualizar:
                al_actualizar(res)
            _log(
                f"[{datetime.now().strftime('%H:%M:%S')}] sync API {desde}→{hasta}: "
                f"{res.get('filas', 0)} filas, "
                f"{len(res.get('dias_desfasados') or [])} días desfasados, "
                f"modo {res.get('modo')}, estado {res.get('estado')}"
            )
        except Exception as ex:  # noqa: BLE001 - se muestra al usuario
            _cerrar(*_msgs_sync_api(ex=ex))
        finally:
            if cli is not None:
                try:
                    cli.close()
                except Exception:
                    pass
            btn_sync.disabled = False
            _safe_update(page)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _asegurar_api(url: str) -> dict:
    from src.core.api_wake import asegurar_api

    return asegurar_api(url=url)


def _sync_por_defecto(cli, desde: str, hasta: str) -> dict:
    from src.core.sync_api import SyncAPI

    return SyncAPI(cli).aplicar(desde, hasta)


from src.core.g360_theme import G360Theme, safe_handler
from src.core.fechas import fecha_ui
from src.core.utils import cliente_visible
from src.core.ventas_db_client import VentasDbClient
from src.core import ventas_db
from src.ui.catalog import (
    CATALOGO,
    DOC_HISTORICO,
    DOC_HISTORICO_LABEL,
    MODALIDAD_CONSOLIDADO,
    MODALIDAD_INDIVIDUAL,
    ORDEN_CASOS,
    HistorialConfig,
)
from src.ui.widgets.cliente_factura_selector import ClienteFacturaSelector
from src.ui.widgets import control_factory


"""Constructores de UI de ReconocimientoView (mixins).

Construcción de tarjetas, paneles, la tira de captura y la card de estado de
la DB. Heredado por ReconocimientoView (src/ui/reconocimiento_view.py)."""


class _ViewPanels:
    def _init_controls(self):
        # Card de Reportes de compras: estado propio, se construye UNA vez
        # y se reutiliza en cada rebuild del layout (cambiar de caso no la
        # toca; solo el reset general la limpia y colapsa).
        from src.ui.reporte_panel import ReportePanel

        self.reporte_panel = ReportePanel(self.app, self)

        self.tipo_dropdown = control_factory.dropdown(
            "Tipo de caso",
            expand=True,
            value=self.tipo_actual,
            on_change=self._on_tipo_change,
        )
        self._construir_tipo_selector()

        self.lbl_historial = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_lista = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_requerimientos_list = ft.Column([], spacing=4)
        self.lbl_requerimientos_count = ft.Text(
            "Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT
        )

        # Search state (inline)
        self._search_vend_id = None
        self._search_vend_nom = None
        self._search_cli_id = None
        self._search_fac_id = None
        self._sel_clientes: list[tuple] = []  # [(id_cliente, nombre)]
        self._sel_facturas: list[tuple] = []  # [(id_cliente, fac_id)]
        self._sel_pedidos: list[tuple] = []  # [(id_cliente, pedido_id)]
        self._sel_ordenes: list[tuple] = []  # [(id_cliente, oc_id)]
        self._busq_df = None  # ultimo resultado de Buscar
        self._hist_fragmento: dict | None = None  # stats del fragmento aplicado
        self._cli = VentasDbClient()
        # Anclados (favoritos) por usuario: se ven primero en la cascada.
        _pin = ventas_db.load_pinned()
        self._pinned_clientes: list[str] = _pin["clientes"]
        self._pinned_vendedores: list[str] = _pin["vendedores"]
        # Recientes: ultimos clientes/vendedores usados en el picker.
        self._recent_clientes: list[str] = ventas_db.load_recent("clientes")
        self._recent_vendedores: list[str] = ventas_db.load_recent("vendedores")

        self.config_container = ft.Container(padding=10)

        self.modalidad_radio = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(value=MODALIDAD_INDIVIDUAL, label="Individual — por factura"),
                    ft.Radio(value=MODALIDAD_CONSOLIDADO, label="Consolidado — varias facturas"),
                ],
                spacing=12,
            ),
            value=MODALIDAD_INDIVIDUAL,
            on_change=self._on_modalidad_change,
        )
        # Historico config checkboxes (ETapa B)
        self.historico_incluir_ctrls: dict[str, ft.Checkbox] = {}
        self.historico_calc_ctrls: dict[str, ft.Checkbox] = {}
        self.factura_dropdown = control_factory.dropdown(
            "Seleccionar Factura",
            width=control_factory.WIDTH_FILTER,
            on_change=self._on_factura_selected,
        )

        self.selector_ci = ClienteFacturaSelector(
            suffix="ci",
            on_cliente_change=self._on_cliente_change_ci,
            on_factura_change=self._on_factura_change_ci,
            show_factura=True,
        )
        self.cliente_dropdown_ci = self.selector_ci.cliente_dropdown
        self.factura_dropdown_ci = self.selector_ci.factura_dropdown

        self.fecha_desde = control_factory.text_field(
            "Desde (dd-mm-aaaa)", width=control_factory.WIDTH_DATE
        )
        self.fecha_hasta = control_factory.text_field(
            "Hasta (dd-mm-aaaa)", width=control_factory.WIDTH_DATE
        )
        # Fila de período — se muestra solo sin fragmento (sin busqueda segmentada)
        self._periodo_row = ft.Row(
            [
                self.fecha_desde,
                ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                self.fecha_hasta,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.mecanica_dropdown = control_factory.dropdown(
            "Mecánica",
            width=control_factory.WIDTH_FILTER,
            value="12+1",
            on_change=self._on_mecanica_change,
        )
        self.mecanica_dropdown.options = [
            ft.dropdown.Option("12+1"),
            ft.dropdown.Option("24+2"),
            ft.dropdown.Option("48+1"),
            ft.dropdown.Option("personalizado", "Personalizado"),
        ]
        self.mecanica_personalizada = control_factory.text_field(
            "Mecánica (ej: 10+2)",
            width=control_factory.WIDTH_FILTER,
        )
        self.mecanica_personalizada.visible = False

        self.vendedor_dropdown = control_factory.dropdown(
            "Vendedor",
            icon=ft.Icons.PERSON_OUTLINED,
            width=control_factory.WIDTH_FILTER,
            search=True,
            hint="Todos los vendedores…",
            on_change=self._on_vendedor_change,
        )

        self.selector_pd = ClienteFacturaSelector(
            suffix="pd",
            on_cliente_change=self._on_cliente_change_pd,
            show_factura=False,
        )
        self.cliente_dropdown_pd = self.selector_pd.cliente_dropdown
        self.fecha_desde_pd = control_factory.text_field(
            "Desde (dd-mm-aaaa)", width=control_factory.WIDTH_DATE
        )
        self.fecha_hasta_pd = control_factory.text_field(
            "Hasta (dd-mm-aaaa)", width=control_factory.WIDTH_DATE
        )

        self.chk_omitir_sin_dif = ft.Checkbox(
            label="Omitir SKUs sin diferencia (S/ 0.00)",
            value=False,
            active_color=self.app.G360_ACCENT,
            label_style=ft.TextStyle(size=12),
            on_change=self._on_opciones_calculo_change,
        )
        self.chk_gen_nd = ft.Checkbox(
            label="Calcular Nota de Débito para diferencias negativas",
            value=False,
            active_color=G360Theme.accent_color(),
            label_style=ft.TextStyle(size=12),
            on_change=self._on_opciones_calculo_change,
        )

        self.observaciones = ft.TextField(
            label="Observaciones (opcional)",
            width=400,
            border_radius=12,
            text_size=13,
            multiline=True,
            min_lines=2,
            max_lines=4,
            dense=True,
        )

        self.antecedentes = ft.TextField(
            label="Antecedentes / Análisis Comercial *",
            width=400,
            border_radius=12,
            text_size=13,
            multiline=True,
            min_lines=3,
            max_lines=6,
            hint_text="Ej: Acuerdo del 10/09/2025, precio aprobado S/ 2.90...",
            dense=True,
        )

        self.meta_monto = control_factory.text_field("Meta (S/)", width=control_factory.WIDTH_DATE)
        self.rebate_pct = ft.TextField(
            label="% Rebate", width=150, border_radius=12, text_size=13, dense=True
        )

        self.sort_mode_radio = ft.RadioGroup(
            content=ft.Column(
                [
                    ft.Radio(value="fecha_asc", label="FIFO (fecha ascendente)"),
                    ft.Radio(value="fecha_desc", label="LIFO (fecha descendente)"),
                    ft.Radio(value="cantidad_asc", label="Menor cantidad primero"),
                    ft.Radio(value="cantidad_desc", label="Mayor cantidad primero"),
                ],
                spacing=4,
            ),
            value="fecha_asc",
        )
        self.chk_forzar_cant = ft.Checkbox(label="Forzar cantidad solicitada", value=True)

        self.sort_mode_dc_radio = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(value="fecha_asc", label="FIFO (antiguas primero)"),
                    ft.Radio(value="fecha_desc", label="LIFO (nuevas primero)"),
                    ft.Radio(value="cantidad_desc", label="Mayor volumen primero"),
                    ft.Radio(value="cantidad_asc", label="Menor volumen primero"),
                    ft.Radio(value="dif_desc", label="Mayor diferencia primero"),
                ],
                spacing=12,
                wrap=True,
            ),
            value="fecha_desc",
        )

        import datetime as _dt

        default_min = _dt.datetime(2020, 1, 1)
        default_max = _dt.datetime(2030, 12, 31)

        self.selector_fp = ClienteFacturaSelector(
            suffix="fp",
            on_cliente_change=self._on_cliente_change_fp,
            show_factura=False,
            use_build_options=False,
        )
        self.cliente_dropdown_fp = self.selector_fp.cliente_dropdown
        self.fp_desde = ft.DatePicker(
            on_change=self._on_fp_desde_change,
            first_date=default_min,
            last_date=default_max,
        )
        self.fp_hasta = ft.DatePicker(
            on_change=self._on_fp_hasta_change,
            first_date=default_min,
            last_date=default_max,
        )
        self.fecha_desde_fp = ft.OutlinedButton(
            text="Desde: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=self._abrir_fp_desde,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=14)),
        )
        self.fecha_hasta_fp = ft.OutlinedButton(
            text="Hasta: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=self._abrir_fp_hasta,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=14)),
        )

        self.selector_sf = ClienteFacturaSelector(
            suffix="sf",
            on_cliente_change=self._on_cliente_change_sf,
            on_factura_change=self._on_factura_change_sf,
            show_factura=True,
        )
        self.cliente_dropdown_sf = self.selector_sf.cliente_dropdown
        self.factura_dropdown_sf = self.selector_sf.factura_dropdown
        self.alertas_nc_container_sf = ft.Container(
            visible=False,
            padding=12,
            border_radius=12,
            bgcolor=G360Theme.surface_variant_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
        )

        self.selector_df = ClienteFacturaSelector(
            suffix="df",
            on_cliente_change=self._on_cliente_change_df,
            on_factura_change=self._on_factura_change_df,
            show_factura=True,
        )
        self.cliente_dropdown_df = self.selector_df.cliente_dropdown
        self.factura_dropdown_df = self.selector_df.factura_dropdown
        self.descuento_pct = control_factory.text_field(
            "% Descuento",
            width=control_factory.WIDTH_FIELD,
            keyboard=ft.KeyboardType.NUMBER,
            on_change=self._on_descuento_pct_change,
        )
        self.selector_pb = ClienteFacturaSelector(
            suffix="pb",
            on_cliente_change=self._on_cliente_change_pb,
            show_factura=False,
        )
        self.cliente_dropdown_pb = self.selector_pb.cliente_dropdown

        self.linea_checkboxes: dict[str, ft.Checkbox] = {}
        self.linea_checkbox_container = ft.Container(
            content=ft.Column([], spacing=4, scroll=ft.ScrollMode.AUTO),
            height=180,
            border=G360Theme.hr().color,
            border_radius=12,
            padding=10,
        )
        self.linea_select_all_btn = ft.ElevatedButton(
            "Seleccionar todas",
            on_click=self._linea_select_all,
            height=28,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.linea_clear_btn = ft.ElevatedButton(
            "Limpiar",
            on_click=self._linea_clear,
            height=28,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.categoria_nc_checkboxes: dict[str, ft.Checkbox] = {}
        for cat_key, cat_label in [
            ("devolucion", "Devoluciones (NCR)"),
            ("descuento", "Descuentos (NC)"),
            ("cargo", "Cargos (NDB/ND)"),
        ]:
            self.categoria_nc_checkboxes[cat_key] = ft.Checkbox(
                label=cat_label,
                value=(cat_key == "devolucion"),
                label_style=ft.TextStyle(size=12),
            )

        self.sku_filter_path = None
        self.sku_filter_clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=14,
            height=24,
            width=24,
            on_click=self._quitar_sku_filter,
            visible=False,
            tooltip="Quitar filtro SKU",
        )
        self.lbl_sku_filter = ft.Text(
            "Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True
        )

        # Stock file controls (for diferencia_stock): un archivo por SKU con
        # CANTIDAD + PRECIO_BASE + descuentos (opcional: solo cantidades).
        self.lbl_stock_cliente = ft.Text(
            "Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True
        )
        self.stock_cliente_clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=14,
            height=24,
            width=24,
            on_click=self._quitar_stock_cliente,
            visible=False,
            tooltip="Quitar archivo de cantidades",
        )
        self.stock_cliente_path = None
        self.desc_file_path = None
        self.lbl_desc_file = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        self.skus_table_sf = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("SKU", size=10, weight="bold")),
                ft.DataColumn(ft.Text("ARTÍCULO", size=10, weight="bold")),
                ft.DataColumn(ft.Text("CANT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("P.U. FACT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("TOTAL FACT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("INCLUIR", size=10, weight="bold")),
            ],
            column_spacing=12,
            heading_row_height=32,
            heading_row_color=ft.Colors.with_opacity(0.2, self.app.G360_ACCENT),
            border_radius=12,
            horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
        )
        self.skus_table_container = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            G360Theme.section_header(
                                ft.Icons.INVENTORY_OUTLINED, "SKU DE LA FACTURA"
                            ),
                            ft.ElevatedButton(
                                "Marcar todas",
                                on_click=self._marcar_todas_sf,
                                height=26,
                                style=ft.ButtonStyle(
                                    padding=ft.padding.symmetric(horizontal=8),
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                ),
                            ),
                            ft.ElevatedButton(
                                "Desmarcar todas",
                                on_click=self._desmarcar_todas_sf,
                                height=26,
                                style=ft.ButtonStyle(
                                    padding=ft.padding.symmetric(horizontal=8),
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                ),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Row([self.skus_table_sf], scroll=ft.ScrollMode.ALWAYS),
                        border_radius=10,
                    ),
                ],
                spacing=6,
            ),
            visible=False,
            padding=12,
            border_radius=12,
            bgcolor=G360Theme.surface_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
        )

        self.btn_ejecutar = G360Theme.accent_button(
            "Ejecutar reconocimiento",
            ft.Icons.ROCKET_LAUNCH_ROUNDED,
            on_click=self._ejecutar,
            disabled=True,
        )
        self.lbl_ejecutar_hint = ft.Text(
            "Busca y aplica el historial del caso para comenzar.",
            size=10,
            color=G360Theme.text_muted_color(),
        )

        self.lbl_total_nc = ft.Text(
            "S/ 0.00", size=24, weight=ft.FontWeight.W_900, color=self.app.G360_ACCENT
        )
        self.lbl_skus = ft.Text("0 SKU", size=14, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_alertas_count = ft.Text("0 alertas", size=14, color=ft.Colors.ON_SURFACE_VARIANT)
        self.resultados_table = ft.DataTable(
            columns=[],
            rows=[],
            column_spacing=15,
            heading_row_height=35,
            heading_row_color=ft.Colors.with_opacity(0.2, self.app.G360_ACCENT),
            border_radius=G360Theme.RADIUS_CONTROL,
            horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
        )
        self._result_load_more_btn = ft.ElevatedButton(
            "Ver más filas",
            icon=ft.Icons.UNFOLD_MORE,
            style=ft.ButtonStyle(
                bgcolor=G360Theme.with_opacity(0.15, G360Theme.ACCENT),
                color=G360Theme.accent_text_color(),
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=16, vertical=6),
            ),
            visible=False,
            height=32,
            on_click=self._result_load_more,
        )
        self._result_table_wrap = ft.Container(
            content=ft.Row([self.resultados_table], scroll=ft.ScrollMode.ALWAYS),
            padding=15,
            border_radius=14,
            bgcolor=G360Theme.surface_variant_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
        )
        self._result_warn_row = ft.Row(
            [], alignment=ft.MainAxisAlignment.CENTER, spacing=20, visible=False
        )
        self._result_load_more_row = ft.Row(
            [self._result_load_more_btn], alignment=ft.MainAxisAlignment.CENTER, spacing=20
        )
        self._result_sort_col = None
        self._result_sort_asc = False
        self.resultados_container = ft.Container(
            visible=False,
            padding=20,
            border_radius=14,
            bgcolor=G360Theme.surface_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
        )

        self.btn_expediente = G360Theme.accent_button(
            "Generar expediente",
            ft.Icons.FOLDER_SHARED_OUTLINED,
            on_click=self._generar_expediente,
            disabled=True,
        )

        def _toggle_audit_panel(_):
            if not hasattr(self, "_result_summary"):
                return
            pri = self._result_summary
            if not pri or len(getattr(pri, "controls", [])) <= 2:
                return
            panel = pri.controls[2]
            if panel is None:
                return
            panel.visible = not getattr(panel, "visible", True)
            try:
                lbl = self._result_audit_btn.content.controls[-1]
                lbl.value = "Ocultar auditoría" if panel.visible else "Mostrar auditoría"
            except Exception:
                pass
            if self.app.page:
                self.app.page.update()

        self._result_audit_btn = G360Theme.ghost_button(
            "Auditoría NC",
            icon=ft.Icons.REPORT,
            on_click=_toggle_audit_panel,
        )

        self._result_btn_row = ft.Row(
            [self.btn_expediente, self._result_audit_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=20,
        )

        self.alertas_container = ft.Container(visible=False, padding=15, border_radius=18)
        self.aplicar_toggles: dict[str, ft.Checkbox] = {}

    def _construir_tipo_selector(self):
        """Dropdown de los 8 casos canónicos (sin agrupar por familia)."""
        opciones = []
        for key in ORDEN_CASOS:
            caso = CATALOGO[key]
            opciones.append(
                ft.dropdown.Option(
                    key=key,
                    text=f"{key}  ·  {caso.label}",
                )
            )
        self.tipo_dropdown.options = opciones
        self.tipo_dropdown.value = "DC"
        self.tipo_actual = "DC"

    def _renderizar_ui(self):
        # Resolve catalog case from legacy tipo_actual (for backward compat)
        caso_cfg = self._caso_de_tipo()
        self._renderizar_config(caso_cfg)
        layout = self._construir_layout(caso_cfg)
        self.container.content = layout

    def _renderizar_config(self, tipo_cfg):
        config_cols = []
        # La modalidad se presenta junto al selector del caso; aquí se
        # conservan solo los parámetros que modifican el cálculo.
        self.modalidad_radio.content = ft.Row(
            [
                ft.Radio(
                    value=MODALIDAD_INDIVIDUAL,
                    label="Individual — por factura",
                    disabled=MODALIDAD_INDIVIDUAL not in tipo_cfg.get("modalidades", ()),
                ),
                ft.Radio(
                    value=MODALIDAD_CONSOLIDADO,
                    label="Consolidado — varias facturas",
                    disabled=MODALIDAD_CONSOLIDADO not in tipo_cfg.get("modalidades", ()),
                ),
            ],
            spacing=12,
        )
        # Configuración histórica por documento (heredada del caso).
        config_cols.append(self._build_historico_section(tipo_cfg))
        # PERÍODO: solo visible si NO hay fragmento (filtro ya aplicado por busqueda)
        if tipo_cfg.get("tiene_periodo", False):
            self._periodo_row.visible = self._hist_fragmento is None
            config_cols.append(G360Theme.section_header(ft.Icons.DATE_RANGE_OUTLINED, "PERIODO"))
            config_cols.append(self._periodo_row)
        if tipo_cfg.get("tiene_mecanica", False):
            config_cols.append(G360Theme.section_header(ft.Icons.TUNE_OUTLINED, "MECANICA"))
            config_cols.append(
                ft.Row([self.mecanica_dropdown, self.mecanica_personalizada], spacing=10)
            )
        if tipo_cfg.get("tiene_meta", False):
            config_cols.append(
                G360Theme.section_header(ft.Icons.TRENDING_UP_OUTLINED, "META Y REBATE")
            )
            config_cols.append(
                ft.Row(
                    [
                        self.meta_monto,
                        self.rebate_pct,
                    ],
                    spacing=10,
                )
            )

        if self._tipo_incluye("feria_preventa"):
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(
                G360Theme.section_header(ft.Icons.SORT_OUTLINED, "ORDEN DE ASIGNACIÓN")
            )
            config_cols.append(self.sort_mode_radio)
            config_cols.append(self.chk_forzar_cant)
            config_cols.append(self.fp_desde)
            config_cols.append(self.fp_hasta)

        if self._tipo_incluye("rebate_volumen"):
            config_cols.append(ft.Divider(height=10, color="transparent"))
            self._actualizar_lineas()
            config_cols.append(
                G360Theme.section_header(
                    ft.Icons.CATEGORY_OUTLINED, "LÍNEAS DE PRODUCTO (seleccionar una o más)"
                )
            )
            config_cols.append(
                ft.Row(
                    [
                        self.linea_select_all_btn,
                        self.linea_clear_btn,
                    ],
                    spacing=8,
                )
            )
            config_cols.append(self.linea_checkbox_container)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(
                G360Theme.section_header(
                    ft.Icons.REQUEST_QUOTE_OUTLINED, "NC/ND A INCLUIR EN CÁLCULO"
                )
            )
            config_cols.append(
                ft.Row(
                    list(self.categoria_nc_checkboxes.values()),
                    spacing=10,
                    wrap=True,
                )
            )

        if self._tipo_incluye("descuento_precio"):
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(
                G360Theme.section_header(
                    ft.Icons.PERCENT_OUTLINED, "DESCUENTO GLOBAL (alternativo al archivo por SKU)"
                )
            )
            config_cols.append(self.descuento_pct)

        if self._tipo_incluye(
            "diferencia_precio", "diferencia_cantidad", "diferencia_stock", "devolucion_fisica"
        ):
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(
                G360Theme.section_header(
                    ft.Icons.SORT_OUTLINED,
                    "ORDEN DE ASIGNACIÓN (cómo se reparte la cantidad contra las facturas)",
                )
            )
            config_cols.append(self.sort_mode_dc_radio)

        config_cols.append(ft.Divider(height=10, color="transparent"))
        config_cols.append(
            G360Theme.section_header(ft.Icons.PERSON_OUTLINE, "JUSTIFICACIÓN DEL RECONOCIMIENTO")
        )
        config_cols.append(ft.Divider(height=8, color="transparent"))
        config_cols.append(G360Theme.section_header(ft.Icons.TUNE_OUTLINED, "OPCIONES DE CÁLCULO"))
        config_cols.append(self.chk_omitir_sin_dif)
        config_cols.append(self.chk_gen_nd)
        config_cols.append(ft.Divider(height=8, color="transparent"))
        config_cols.append(self.antecedentes)
        config_cols.append(self.observaciones)
        self.config_container.content = (
            ft.Column(config_cols, spacing=8) if config_cols else ft.Text("")
        )

    def _build_historico_section(self, tipo_cfg: dict) -> ft.Container | None:
        """Configura por separado documentos visibles y documentos calculados."""
        hcfg = getattr(self, "_historico_config", None)
        if hcfg is None:
            historico_default = tipo_cfg.get("historico_default", {})
            hcfg = HistorialConfig(**{k: tuple(v) for k, v in historico_default.items()})
        self.historico_incluir_ctrls.clear()
        self.historico_calc_ctrls.clear()
        notas_calculo_soportadas = self._tipo_incluye(
            "diferencia_precio", "diferencia_stock", "descuento_precio", "feria_preventa"
        )
        rows = [
            ft.Row(
                [
                    ft.Text(
                        "Documento",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.text_muted_color(),
                        expand=True,
                    ),
                    ft.Text(
                        "Mostrar",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.text_muted_color(),
                        width=105,
                    ),
                    ft.Text(
                        "Calcular",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.text_muted_color(),
                        width=105,
                    ),
                ],
                spacing=8,
            )
        ]
        for doc in DOC_HISTORICO:
            label = DOC_HISTORICO_LABEL[doc]
            chk_incl = ft.Checkbox(
                label="Incluir",
                value=hcfg.incluir(doc),
                active_color=self.app.G360_ACCENT,
                tooltip=f"Incluir {label} en la vista y exportación",
                on_change=self._guardar_historico_config_desde_checks,
            )
            chk_calc = ft.Checkbox(
                label="Usar",
                value=hcfg.considerar(doc),
                active_color=self.app.G360_SUCCESS,
                tooltip=(
                    f"Considerar {label} en el cálculo del caso"
                    if doc == "facturas" or notas_calculo_soportadas
                    else "Este caso solo muestra y audita NC/NDB; no las aplica al cálculo"
                ),
                disabled=(
                    doc != "facturas"
                    and (
                        not notas_calculo_soportadas
                        or self.modalidad_actual == MODALIDAD_CONSOLIDADO
                    )
                ),
                on_change=self._guardar_historico_config_desde_checks,
            )
            self.historico_incluir_ctrls[doc] = chk_incl
            self.historico_calc_ctrls[doc] = chk_calc
            rows.append(
                ft.Row(
                    [
                        ft.Text(label, size=12, expand=True, color=G360Theme.text_primary_color()),
                        ft.Container(chk_incl, width=105),
                        ft.Container(chk_calc, width=105),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        return ft.Column(
            [
                G360Theme.section_header(ft.Icons.HISTORY_OUTLINED, "DOCUMENTOS DEL HISTORIAL"),
                ft.Text(
                    "Mostrar en el expediente no implica usar en el cálculo.",
                    size=10,
                    color=G360Theme.text_muted_color(),
                ),
                *(
                    [
                        ft.Text(
                            "En consolidado las NC/NDB se detallan para revisión, "
                            "pero no ajustan cantidades ni precios.",
                            size=10,
                            color=G360Theme.text_muted_color(),
                        )
                    ]
                    if self.modalidad_actual == MODALIDAD_CONSOLIDADO
                    else [
                        ft.Text(
                            "En este caso las NC/NDB se muestran y auditan, pero "
                            "no se aplican al cálculo automáticamente.",
                            size=10,
                            color=G360Theme.text_muted_color(),
                        )
                    ]
                    if not notas_calculo_soportadas
                    else []
                ),
                *rows,
            ],
            spacing=4,
        )

    def _construir_layout(self, tipo_cfg):
        from src.ui.widgets.workflow_section import workflow_section

        workflow = G360Theme.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.DESIGN_SERVICES_OUTLINED,
                                size=19,
                                color=G360Theme.accent_color(),
                            ),
                            ft.Column(
                                [
                                    G360Theme.card_title("Preparar expediente"),
                                    G360Theme.subtitle(
                                        "Define el caso, selecciona los datos y completa sus insumos."
                                    ),
                                ],
                                spacing=G360Theme.SPACE_XS,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=G360Theme.border_subtle_color()),
                    workflow_section(
                        "Caso y modalidad",
                        ft.Icons.CATEGORY_OUTLINED,
                        self._contenido_seleccion_caso(tipo_cfg),
                        description="Elige qué reconocimiento generar y cómo se agrupará.",
                    ),
                    workflow_section(
                        "Datos del caso",
                        ft.Icons.DATA_EXPLORATION_OUTLINED,
                        self._construir_seccion_datos(),
                        description="Busca y delimita el historial que sustentará el cálculo.",
                    ),
                    self._construir_seccion_insumos(tipo_cfg),
                    self._construir_seccion_configuracion(),
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(
                                            "¿Todo listo?",
                                            size=12,
                                            weight=ft.FontWeight.W_600,
                                            color=G360Theme.text_primary_color(),
                                        ),
                                        self.lbl_ejecutar_hint,
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                self.btn_ejecutar,
                            ],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.only(top=4),
                    ),
                ],
                spacing=G360Theme.SPACE_MD,
            ),
        )

        return ft.Column(
            [
                self._construir_card_db(),
                self._construir_strip_captura(),
                self.reporte_panel.construir_card(),
                workflow,
                self.resultados_container,
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=14,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _contenido_seleccion_caso(self, tipo_cfg: dict) -> ft.Control:
        """Selector de caso y modalidad, separado del resto de parámetros."""
        resultado = tipo_cfg.get("resultado", "NC")
        badge = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.RECEIPT_LONG_OUTLINED, size=13, color=G360Theme.accent_text_color()
                    ),
                    ft.Text(
                        f"Genera {resultado}",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.accent_text_color(),
                    ),
                ],
                spacing=5,
            ),
            bgcolor=G360Theme.accent_soft_color(0.10),
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            border_radius=10,
        )
        return ft.Column(
            [
                ft.Row(
                    [self.tipo_dropdown, badge],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    tipo_cfg.get("descripcion", "") or tipo_cfg.get("label", ""),
                    size=12,
                    color=G360Theme.text_muted_color(),
                ),
                G360Theme.section_header(ft.Icons.APPS_OUTLINED, "Modalidad"),
                self.modalidad_radio,
            ],
            spacing=10,
        )

    def _construir_seccion_configuracion(self) -> ft.Column:
        """Parámetros de cálculo e historial, fuera del selector del caso."""
        from src.ui.widgets.workflow_section import workflow_section

        return workflow_section(
            "Configuración del caso",
            ft.Icons.TUNE_OUTLINED,
            self.config_container,
            description="Ajusta documentos, período y reglas propias del cálculo.",
        )

    def _actualizar_rango_fechas_fp(self):
        if self.df_historial is None:
            return
        df = self.df_historial
        if "FECHA" not in df.columns or df["FECHA"].dropna().empty:
            return
        min_f = df["FECHA"].min()
        max_f = df["FECHA"].max()
        self.fp_desde.first_date = min_f
        self.fp_desde.last_date = max_f
        self.fp_hasta.first_date = min_f
        self.fp_hasta.last_date = max_f

    @safe_handler
    def _actualizar_lineas(self):
        if self.df_historial is None or "LINEA" not in self.df_historial.columns:
            self.linea_checkbox_container.visible = False
            return
        df = self.df_historial
        cliente = self.cliente_dropdown_pb.value
        if cliente and "CLIENTE" in df.columns:
            df = df[df["CLIENTE"].astype(str).str.strip() == cliente.strip()]
        vendedor_id = self.vendedor_dropdown.value
        if vendedor_id and "COD_VENDEDOR" in df.columns:
            df = df[df["COD_VENDEDOR"].astype(str).str.strip() == vendedor_id.strip()]
        lineas = sorted(df["LINEA"].dropna().unique())
        prev_values = {name: cb.value for name, cb in self.linea_checkboxes.items()}
        checks = []
        self.linea_checkboxes.clear()
        for linea in lineas:
            cb = ft.Checkbox(
                label=linea,
                value=prev_values.get(linea, False),
                label_style=ft.TextStyle(size=12),
                on_change=self._on_linea_toggle,
            )
            self.linea_checkboxes[linea] = cb
            checks.append(cb)
        if checks:
            self.linea_checkbox_container.content = ft.Column(
                checks, spacing=4, scroll=ft.ScrollMode.AUTO
            )
            self.linea_checkbox_container.visible = True
        else:
            self.linea_checkbox_container.visible = False

    def _panel_red(self, page):
        """Construye el contenido de la pestaña 'Red' (escanear/copiar/compartir).

        Retorna (content, on_open): on_open lanza el escaneo tras montar.
        """
        import flet as ft
        from src.core.db_network import scan_network_for_db, copy_db_network
        from src.core import ventas_db

        has_db = ventas_db.db_exists()

        self.lbl_red_status = ft.Text(
            "Escaneando red...", size=12, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.lst_red_results = ft.Column([], spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        self.btn_red_copy = ft.ElevatedButton(
            "Copiar DB seleccionada", icon=ft.Icons.DOWNLOAD, height=36, disabled=True
        )
        self.btn_red_refresh = ft.OutlinedButton("Refrescar", height=36)
        self.btn_red_share = ft.OutlinedButton(
            "📤 Compartir mi DB en red",
            height=36,
            visible=has_db,
            tooltip="Configura un share SMB para que otros usuarios te copien",
        )

        # ── Instrucciones de compartir (solo si tiene DB) ──
        share_help = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINED, size=16, color=G360Theme.ACCENT),
                            ft.Text(
                                "¿Quieres compartir tu historial con un compañero?",
                                size=12,
                                weight=ft.FontWeight.W_600,
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Text(
                        "Para compartir tu DB, tu compañero debe ejecutar estos pasos:\n"
                        "1. Crear un share en tu PC (ejecutar como admin):\n"
                        f'   net share g360-erp-nc-sustentor="{ventas_db.db_path().parent}" /grant:Everyone,FULL\n'
                        "2. Tu compañero abrirá «Fuentes» y escaneará su segmento.",
                        size=10,
                        color=G360Theme.text_muted_color(),
                    ),
                ],
                spacing=4,
            ),
            padding=10,
            bgcolor=G360Theme.with_opacity(0.06, G360Theme.ACCENT),
            border_radius=10,
            visible=has_db,
        )

        # ── Escaneo con spinner global (no bloquea dialog) ──
        import threading

        results: list = []

        def _set_red_status(text: str, color=None):
            """Actualiza el texto de estado."""
            self.lbl_red_status.value = text
            self.lbl_red_status.color = color or G360Theme.text_muted_color()

        def do_scan(_):
            self.lst_red_results.controls.clear()
            self.btn_red_copy.disabled = True
            self.lbl_red_status.value = "Escaneando segmento de red… (puede tardar unos segundos)"
            self.lbl_red_status.color = G360Theme.warning_color()
            results.clear()
            scan_state = {"done": False}

            def scan_thread():
                try:
                    results.extend(scan_network_for_db())
                except Exception as ex:
                    _set_red_status(f"Error: {ex}", G360Theme.error_color())
                finally:
                    scan_state["done"] = True

            threading.Thread(target=scan_thread, daemon=True).start()

            def check_results():
                if not scan_state["done"]:
                    import threading as _th

                    _th.Timer(0.5, check_results).start()
                    return
                n = len(results)
                if n == 0:
                    _set_red_status(
                        "No se encontraron DBs en la red. "
                        "Pide a un compañero que comparta su historial siguiendo las instrucciones arriba.",
                        G360Theme.warning_color(),
                    )
                else:
                    _set_red_status(f"✅ Encontradas {n} base(s) en la red", G360Theme.ok_color())
                self.lst_red_results.controls.clear()
                for entry in results:
                    size_mb = entry.size_bytes / (1024 * 1024)
                    sel = ft.Radio(value=entry.path)
                    row = ft.Row(
                        [
                            sel,
                            ft.Icon(
                                ft.Icons.STORAGE_OUTLINED, size=16, color=G360Theme.accent_2_color()
                            ),
                            ft.Text(entry.ip, size=12, weight=ft.FontWeight.W_600, expand=True),
                            ft.Text(
                                f"{size_mb:.1f} MB", size=10, color=G360Theme.text_muted_color()
                            ),
                            ft.Text(
                                entry.last_write[:10], size=10, color=G360Theme.text_muted_color()
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                    self.lst_red_results.controls.append(row)
                self.btn_red_copy.disabled = False
                page.update()

            page.run_at_every(0.5, check_results)

        def on_copy(_):
            sel = next(
                (
                    r
                    for r in self.lst_red_results.controls
                    if isinstance(r, ft.Row) and r.controls[0].value
                ),
                None,
            )
            if not sel:
                return
            source = sel.controls[0].value
            dest = ventas_db.db_path()
            _set_red_status(f"📥 Copiando desde {source}...", G360Theme.warning_color())
            self.app.show_loading(f"Copiando {source.split('.')[-1]}...")
            page.update()

            def copy_thread():
                try:
                    result = copy_db_network(source, dest)
                    if result["success"]:
                        _set_red_status("✓ DB copiada exitosamente", G360Theme.ok_color())
                        self.app.show_snackbar(
                            f"DB copiada: {result['bytes_copied'] // 1024 // 1024} MB · "
                            f"{result.get('rows', 0):,} filas",
                            G360Theme.SUCCESS,
                        )
                        from src.core import ventas_db as vdb

                        vdb.populate_nc_asociadas()
                        vdb.refresh_agg_cliente_mes()
                        self._refrescar_card_db()
                    else:
                        _set_red_status(
                            f"Error: {result.get('error', 'unknown')}", G360Theme.error_color()
                        )
                except Exception as ex:
                    _set_red_status(f"Error: {ex}", G360Theme.error_color())
                self.app.hide_loading()
                page.update()

            threading.Thread(target=copy_thread, daemon=True).start()

        def on_share(_):
            """Abre cmd para ejecutar el comando net share."""
            import subprocess

            db_file = ventas_db.db_path()
            data_dir = db_file.parent
            cmd = f'net share g360-erp-nc-sustentor="{data_dir}" /grant:Everyone,FULL'
            try:
                subprocess.run(["cmd", "/C", cmd], check=False)
                _set_red_status(
                    "Share configurado. Comparte esta IP con tu compañero:\n"
                    f"  {data_dir}\n\n"
                    "Él debe abrir «Fuentes» y escanear su segmento.",
                    G360Theme.ok_color(),
                )
            except Exception as ex:
                _set_red_status(f"No se pudo abrir CMD: {ex}", G360Theme.error_color())
            page.update()

        content = ft.Column(
            [
                self.lbl_red_status,
                share_help,
                self.lst_red_results,
                ft.Row(
                    [
                        self.btn_red_refresh,
                        self.btn_red_share,
                        self.btn_red_copy,
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.END,
                ),
            ],
            spacing=8,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )
        self.btn_red_refresh.on_click = do_scan
        self.btn_red_copy.on_click = on_copy
        self.btn_red_share.on_click = on_share
        return content, (lambda: do_scan(None))

    def _abrir_dialogo_red(self, e):
        """Dialog independiente de Red (wrapper del panel reutilizable)."""
        import flet as ft

        page = self.app.page
        if page is None:
            self.app.show_snackbar("No hay página disponible", self.app.G360_ERROR)
            return
        content, on_open = self._panel_red(page)
        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.LAN_OUTLINED, color=G360Theme.accent_2_color()),
                    ft.Text("Base de datos en red local", size=14, weight=ft.FontWeight.W_700),
                ],
                spacing=8,
            ),
            content=content,
            actions=[ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
            width=560,
        )
        page.open(dlg)
        on_open()

    def _panel_resumen(self, page):
        """Pestaña Estado: DB local + estado del servidor (API/snapshot/token).

        db_card_info() usa caché serve-stale y no bloquea; el bloque servidor
        lee ``api_robustness.state.ultimo_health`` (publicado por el health-check
        de background): no hay llamadas de red desde el modal.
        """
        import flet as ft
        from src.core import ventas_db

        def _render(info: dict):
            # Bloque servidor: lee el estado publicado por el health-check de
            # fondo (sin red). Así el modal nunca se colga piding HTTP.
            from src.core.api_robustness.state import ultimo_health

            h = ultimo_health()
            if h is None:
                srv_txt = "API: sin chequear todavía"
                srv_color = G360Theme.text_muted_color()
            elif not h.get("api_online"):
                err = h.get("error") or "sin detalle"
                srv_txt = f"API offline · {err[:60]}"
                srv_color = G360Theme.error_color()
            else:
                horas = h.get("desfase_horas")
                url = h.get("url") or ""
                if horas is None:
                    srv_txt = f"API OK · {url}"
                    srv_color = G360Theme.ok_color()
                elif horas > 24:
                    srv_txt = f"API OK · snapshot viejo ({horas:.0f}h) · {url}"
                    srv_color = G360Theme.error_color()
                elif horas > 2:
                    srv_txt = f"API OK · snapshot {horas:.0f}h · {url}"
                    srv_color = G360Theme.warning_color()
                else:
                    srv_txt = f"API OK · snapshot fresco · {url}"
                    srv_color = G360Theme.ok_color()

            srv_block = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.WIFI_OUTLINED, size=16, color=srv_color),
                        ft.Text(srv_txt, size=12, color=srv_color, weight=ft.FontWeight.W_600),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=G360Theme.surface_variant_color(),
                border_radius=10,
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            )
            if not info.get("exists") or not info.get("filas"):
                return ft.Column(
                    [
                        srv_block,
                        ft.Container(height=10),
                        ft.Icon(ft.Icons.STORAGE, size=32, color=G360Theme.text_muted_color()),
                        ft.Text(
                            "Sin base de datos todavía",
                            size=13,
                            color=ft.Colors.ON_SURFACE,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Text(
                            "Usa «Gestión» para importar un cartucho/USB o una DB de la red, "
                            "o «Actualizar hoy» para llenar la ventana reciente desde la API.",
                            size=12,
                            color=G360Theme.text_muted_color(),
                        ),
                    ],
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                )
            fmin, fmax = info.get("fecha_min"), info.get("fecha_max")
            rows = [
                ("Filas", f"{info.get('filas', 0):,}"),
                ("Meses", str(info.get("meses", 0))),
                ("Clientes", f"{info.get('clientes', 0):,}"),
                ("Facturas", f"{info.get('facturas', 0):,}"),
                ("SKUs", f"{info.get('skus', 0):,}"),
                (
                    "Cobertura",
                    (fmin[:10] if fmin else "?") + "  →  " + (fmax[:10] if fmax else "?"),
                ),
                ("Tamaño", f"{info.get('size_mb', 0):.0f} MB"),
            ]
            return ft.Column(
                [
                    srv_block,
                    ft.Container(height=8),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        k,
                                        size=12,
                                        color=G360Theme.text_muted_color(),
                                        width=110,
                                    ),
                                    ft.Text(
                                        v,
                                        size=12,
                                        color=ft.Colors.ON_SURFACE,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                ],
                                spacing=8,
                            )
                            for k, v in rows
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            )

        box = ft.Container(
            content=ft.Row(
                [
                    ft.ProgressRing(width=22, height=22, stroke_width=2),
                    ft.Text("Calculando estado…", size=12, color=G360Theme.text_muted_color()),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=12,
            # `alignment` de ft.Container es un ft.Alignment (x/y), NO un
            # MainAxisAlignment: el enum suelto hace reventar el
            # EmbedJsonEncoder ("'mappingproxy' object has no attribute
            # '__dict__'") y un string llega al cliente Dart como String donde
            # espera Map. ft.alignment.center es Alignment(0, 0).
            alignment=ft.alignment.center,
        )

        def worker():
            try:
                info = ventas_db.db_card_info()
                box.content = _render(info)
                if page:
                    try:
                        page.update()
                    except (RuntimeError, Exception):
                        pass  # Page closed or event loop ended
            except Exception:
                _logger.exception("resumen fallo")

        threading.Thread(target=worker, daemon=True).start()
        return box

    def _abrir_config_db(self, e, tab_inicial: int = 0):
        """Modal 'Configuración de DB': Estado/Intranet/Líneas/Fuentes/Compartir."""
        import flet as ft

        page = self.app.page
        if page is None:
            self.app.show_snackbar("No hay página disponible", self.app.G360_ERROR)
            return

        def _placeholder(msg="Cargando…"):
            return ft.Container(
                content=ft.Row(
                    [
                        ft.ProgressRing(width=18, height=18, stroke_width=2),
                        ft.Text(msg, size=12, color=G360Theme.text_muted_color()),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                padding=24,
                alignment=ft.alignment.center,
            )

        # Tabs: Estado (local+servidor), Filtro XLS (legacy), Campos, Gestión.
        # (El placeholder "Reportes" se eliminó: nunca tuvo contenido real).
        built = {"resumen": False, "lineas": False, "campos": False, "gestion": False}
        tab_estado = ft.Tab(text="Estado", icon=ft.Icons.ASSESSMENT, content=_placeholder())
        tab_lineas = ft.Tab(
            text="Filtro XLS", icon=ft.Icons.CATEGORY_OUTLINED, content=_placeholder()
        )
        tab_campos = ft.Tab(
            text="Campos", icon=ft.Icons.VIEW_COLUMN_OUTLINED, content=_placeholder()
        )
        tab_gestion = ft.Tab(text="Gestión", icon=ft.Icons.SOURCE_OUTLINED, content=_placeholder())

        def _ensure(idx):
            """Construye el panel de la pestaña activa (una sola vez)."""
            try:
                if idx == 0 and not built["resumen"]:
                    built["resumen"] = True
                    tab_estado.content = ft.Container(
                        self._panel_resumen(page), padding=12, width=480
                    )
                elif idx == 1 and not built["lineas"]:
                    built["lineas"] = True
                    tab_lineas.content = ft.Container(
                        self._panel_lineas(page), padding=12, width=380
                    )
                elif idx == 2 and not built["campos"]:
                    built["campos"] = True
                    tab_campos.content = ft.Container(
                        self._panel_campos(page), padding=12, width=560
                    )
                elif idx == 3 and not built["gestion"]:
                    built["gestion"] = True
                    tab_gestion.content = ft.Container(self._panel_gestion(page), padding=12)
                page.update()
            except Exception:
                _logger.exception("config panel %s fallo", idx)

        def _on_tab_change(ev):
            try:
                idx = tabs.selected_index
            except Exception:
                idx = None
            if idx is None:
                return
            _ensure(idx)

        tabs = ft.Tabs(
            selected_index=tab_inicial,
            animation_duration=200,
            expand=True,
            tabs=[tab_estado, tab_lineas, tab_campos, tab_gestion],
            on_change=_on_tab_change,
        )
        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.SETTINGS_OUTLINED, color=self.app.G360_ACCENT),
                    ft.Text("Configuración", size=14, weight=ft.FontWeight.W_700),
                ],
                spacing=8,
            ),
            content=ft.Container(tabs, height=480, width=580),
            actions=[ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.open(dlg)
        # Solo la pestaña activa se construye al abrir; el resto se construye
        # lazy al hacer clic en cada pestaña (_on_tab_change -> _ensure). Esto
        # evita que el build eager de todos los paneles (Gestión hace trabajo
        # síncrono de red/DB) congele la apertura del modal.
        _ensure(tab_inicial)

    def _panel_lineas(self, page):
        """Editor de la allowlist de líneas de producto (filtro de captura intranet).

        Puebla con TODAS las líneas presentes en la DB local
        (``ventas_db.distinct_lineas``) y marca con check las aprobadas
        (sufijo de 2 caracteres en ``allowed_lines``). Guardar persiste
        los sufijos aprobados en config.json -> allowed_lines.
        """
        import re as _re

        import flet as ft

        from src.core import ventas_db

        cfg = ventas_db.load_app_config()
        activas = {str(x).upper() for x in ventas_db.allowed_lines()}
        configuradas = [str(x).upper() for x in cfg.get("allowed_lines") or []]

        def _sufijo(cod: str) -> str:
            return cod[-2:].upper() if len(cod) >= 2 else cod.upper()

        # Entradas: (key, etiqueta, sufijo). Key única por checkbox.
        # Se cargan EN HILO: distinct_lineas() escanea el historial
        # (~1.28 GB, ~4s) y no debe bloquear el hilo UI al abrir el modal.
        entradas: list[tuple[str, str, str]] = []

        cajas: dict[str, tuple[ft.Checkbox, str]] = {}
        status = ft.Text("", size=12, color=G360Theme.text_muted_color())

        def actualizar_suma():
            n_sel = sum(1 for cb, _s in cajas.values() if cb.value)
            n_suf = len({_s for cb, _s in cajas.values() if cb.value})
            status.value = (
                f"{n_sel} de {len(cajas)} líneas marcadas ({n_suf} códigos aprobados para captura)"
            )
            status.color = G360Theme.text_muted_color()
            page.update()

        li_cont = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=1, tight=True, height=300)
        li_cont.controls.append(
            ft.Row(
                [
                    ft.ProgressRing(width=16, height=16, stroke_width=2),
                    ft.Text(
                        "Cargando líneas de la DB…", size=12, color=G360Theme.text_muted_color()
                    ),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            )
        )

        def _crear_checkbox(key: str, etiqueta: str, sufijo: str, activa: bool):
            cb = ft.Checkbox(label=etiqueta, value=activa, data=key)
            cb.on_change = lambda _, __=key: actualizar_suma()
            cajas[key] = (cb, sufijo)
            return cb

        def construir():
            cajas.clear()
            li_cont.controls.clear()
            for key, etiqueta, sufijo in entradas:
                li_cont.controls.append(_crear_checkbox(key, etiqueta, sufijo, sufijo in activas))
            actualizar_suma()

        def _formar_entradas() -> None:
            """Reconstruye la lista desde la DB + extras manuales de config."""
            nonlocal entradas
            V = set()
            nuevas: list[tuple[str, str, str]] = []
            for lin in ventas_db.distinct_lineas():
                cod = str(lin["codigo"]).upper()
                nom = str(lin.get("nombre") or "").strip()
                etiqueta = f"{cod} · {nom}" if nom else cod
                nuevas.append((cod, etiqueta, _sufijo(cod)))
                V.add(cod)
                V.add(_sufijo(cod))
            if not nuevas:
                # Sin DB: lista base de 24 + extras manuales (comportamiento previo)
                base = list(ventas_db.DEFAULT_ALLOWED_LINES)
                nuevas = [(c, c, c) for c in base]
                V.update(base)
            # Extras manuales de config que no aparecen en la DB (ej. ZZ99)
            for cod in configuradas:
                if cod not in V:
                    nuevas.append((cod, f"{cod} (manual)", cod))
                    V.add(cod)
            entradas = nuevas
            construir()
            if page:
                page.update()

        import threading as _th

        def _cargar_async():
            def worker():
                try:
                    _formar_entradas()
                except Exception:
                    _logger.exception("carga de lineas en hilo fallo")
                    construir()
                    if page:
                        page.update()

            _th.Thread(target=worker, daemon=True).start()

        def guardar(_):
            if not entradas:
                status.value = "Aún cargando la lista de líneas… reintenta en un momento."
                status.color = self.app.G360_ACCENT
                page.update()
                return
            sel = sorted({_s for cb, _s in cajas.values() if cb.value})
            if not sel:
                status.value = "✗ Deja al menos una línea aprobada"
                status.color = self.app.G360_ERROR
                page.update()
                return
            cfg2 = ventas_db.load_app_config()
            cfg2["allowed_lines"] = sel
            ventas_db.save_app_config(cfg2)
            activas.clear()
            activas.update(sel)
            status.value = f"✓ Guardados {len(sel)} códigos aprobados"
            status.color = self.app.G360_SUCCESS
            page.update()

        def restaurar(_):
            cfg2 = ventas_db.load_app_config()
            cfg2.pop("allowed_lines", None)
            ventas_db.save_app_config(cfg2)
            activas.clear()
            activas.update(str(x).upper() for x in ventas_db.allowed_lines())
            for _key, (cb, sufijo) in cajas.items():
                cb.value = sufijo in activas
            status.value = f"Restauradas las {len(ventas_db.allowed_lines())} líneas por defecto"
            status.color = self.app.G360_ACCENT
            actualizar_suma()

        def agregar(valor):
            cod = (valor or "").strip().upper()
            if not _re.fullmatch(r"[A-Z0-9]{2}", cod):
                status.value = f"«{(valor or '').strip()}» no es un código válido (ej. AD, 01, CA)"
                status.color = self.app.G360_ERROR
                page.update()
                return
            if cod in cajas:
                status.value = f"La línea {cod} ya está en la lista"
                status.color = self.app.G360_ACCENT
                page.update()
                return
            entradas.append((cod, f"{cod} (manual)", cod))
            li_cont.controls.append(_crear_checkbox(cod, f"{cod} (manual)", cod, True))
            filtro.value = ""
            actualizar_suma()

        filtro = control_factory.text_field(
            "Filtrar / agregar código de línea",
            width=control_factory.WIDTH_FILTER,
            on_submit=lambda e: agregar(e.control.value),
            on_change=lambda e: filtrar_live(e.control.value),
        )

        def filtrar_live(texto):
            t = (texto or "").strip().upper()
            for _key, (cb, _s) in cajas.items():
                etiqueta = str(cb.label or "").upper()
                cb.visible = (not t) or (t in etiqueta)
            page.update()

        row_btns = ft.Row(
            [
                ft.ElevatedButton(
                    "💾 Guardar",
                    height=34,
                    on_click=guardar,
                    style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
                ),
                ft.TextButton("Restaurar por defecto", on_click=restaurar),
            ],
            spacing=8,
        )

        _cargar_async()  # puebla la lista en hilo (spinner hasta que llegue)
        cuerpo = ft.Column(
            [
                G360Theme.section_header(ft.Icons.CATEGORY_OUTLINED, "LÍNEAS DE PRODUCTO ACTIVAS"),
                ft.Text(
                    "Todas las líneas de tu DB local. Marca las aprobadas para "
                    "la captura de intranet (se guardan sus códigos de 2 letras). "
                    "También puedes escribir un código nuevo y dar Enter.",
                    size=10,
                    color=G360Theme.text_muted_color(),
                ),
                ft.Row([filtro], alignment=ft.MainAxisAlignment.CENTER),
                li_cont,
                row_btns,
                status,
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return cuerpo

    def _panel_campos(self, page):
        """Pestaña Campos: cobertura e índice de cada campo crítico + O/C pendientes.

        Red de seguridad anti-O/C: un campo que una captura deja de traer
        no avisa (el filtro devuelve 0 filas y se lee como "no hay datos").
        Acá se ve cobertura, índice y veredicto por campo, el botón
        "Contrastar con origen" (últimos 90 días) y las O/C en colisión
        con sus botones de resolución. Todo lo pesado va en hilos: el
        auditor son ~6 escaneos en frío.
        """
        import threading
        from datetime import date, timedelta

        import flet as ft

        from src.core import ventas_db

        COL_VER = {
            "ok": G360Theme.success_color(),
            "sin_indice": G360Theme.warning_color(),
            "degradado": self.app.G360_WARNING,
            "perdido": G360Theme.error_color(),
        }

        status = ft.Text("Auditando campos…", size=12, color=G360Theme.text_muted_color())
        tabla = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=180)
        contraste_out = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=100)
        oc_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=110)

        def _upd():
            try:
                if page:
                    page.update()
            except Exception:
                pass

        def _pintar_campos(filas):
            tabla.controls.clear()
            tabla.controls.append(
                ft.Row(
                    [
                        ft.Text("Campo", size=10, weight=ft.FontWeight.W_600, width=150),
                        ft.Text("Cobertura", size=10, weight=ft.FontWeight.W_600, width=80),
                        ft.Text("Índice", size=10, weight=ft.FontWeight.W_600, width=150),
                        ft.Text("Estado", size=10, weight=ft.FontWeight.W_600, expand=True),
                    ],
                    spacing=6,
                )
            )
            peor = "ok"
            for r in filas:
                v = r["veredicto"]
                if v == "perdido" or (peor == "ok" and v != "ok"):
                    peor = v
                elif peor == "sin_indice" and v == "degradado":
                    peor = v
                tabla.controls.append(
                    ft.Row(
                        [
                            ft.Text(r["etiqueta"], size=12, width=150),
                            ft.Text(f"{100.0 * r['cobertura']:.1f}%", size=12, width=80),
                            ft.Text(
                                "✓ " + r["indice"] if r["tiene_indice"] else "✗ " + r["indice"],
                                size=10,
                                width=150,
                                color=(
                                    G360Theme.success_color()
                                    if r["tiene_indice"]
                                    else G360Theme.warning_color()
                                ),
                            ),
                            ft.Text(
                                v.upper(),
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=COL_VER.get(v),
                                expand=True,
                            ),
                        ],
                        spacing=6,
                    )
                )
            n_mal = sum(1 for r in filas if r["veredicto"] != "ok")
            status.value = (
                f"{len(filas) - n_mal} de {len(filas)} campos ok"
                if not n_mal
                else f"{n_mal} campo(s) requieren atención (ver Estado)"
            )
            status.color = COL_VER.get(peor, G360Theme.text_muted_color())
            _upd()

        def worker_campos():
            try:
                conn = ventas_db.connect(readonly=True)
                try:
                    filas = ventas_db.auditar_campos_criticos(conn)
                finally:
                    conn.close()
                _pintar_campos(filas)
            except Exception as ex:
                status.value = f"auditoría falló: {ex}"
                status.color = G360Theme.error_color()
                _upd()

        def _pintar_ocs(pends):
            oc_list.controls.clear()
            if not pends:
                oc_list.controls.append(
                    ft.Text("Sin O/C en colisión.", size=12, color=G360Theme.success_color())
                )
            for p in pends:
                cid, norm = p["id_cliente"], p["norm"]
                oc_list.controls.append(
                    ft.Row(
                        [
                            ft.Text(f"{cid} · {norm} ({p['filas']} filas)", size=12,  expand=True),
                            ft.TextButton(
                                "Confirmar",
                                on_click=lambda _, c=cid, n=norm: _resolver(c, n, "confirmado"),
                            ),
                            ft.TextButton(
                                "Separar",
                                on_click=lambda _, c=cid, n=norm: _resolver(c, n, "separado"),
                            ),
                        ],
                        spacing=4,
                    )
                )
            _upd()

        def _recargar_ocs():
            try:
                conn = ventas_db.connect(readonly=True)
                est = ventas_db.connect_estado(readonly=True)
                try:
                    pends = ventas_db.ocs_pendientes(conn, est)
                finally:
                    est.close()
                    conn.close()
                _pintar_ocs(pends)
            except Exception as ex:
                oc_list.controls.clear()
                oc_list.controls.append(
                    ft.Text(
                        f"O/C pendientes no disponibles: {ex}",
                        size=12,
                        color=G360Theme.error_color(),
                    )
                )
                _upd()

        def _resolver(cid, norm, accion):
            def _run():
                try:
                    conn = ventas_db.connect(readonly=False)
                    est = ventas_db.connect_estado(readonly=False)
                    try:
                        n = ventas_db.oc_resolver(conn, cid, norm, accion, est)
                    finally:
                        est.close()
                        conn.close()
                    self.app.show_snackbar(
                        f"O/C {norm} ({cid}): {accion}, {n} filas", self.app.G360_SUCCESS
                    )
                    _recargar_ocs()
                except Exception as ex:
                    from src.ui.mensajes import mensaje

                    self.app.show_snackbar(mensaje(ex, "resolver el cliente"), self.app.G360_ERROR)

            threading.Thread(target=_run, daemon=True).start()

        def _contrastar(_):
            contraste_out.controls.clear()
            contraste_out.controls.append(
                ft.Text(
                    "Contrastando últimos 90 días…", size=12, color=G360Theme.text_muted_color()
                )
            )
            _upd()

            def _run():
                try:
                    from src.core.db_network import contrastar_con_origen

                    hasta = date.today().isoformat()
                    desde = (date.today() - timedelta(days=89)).isoformat()
                    rep = contrastar_con_origen(desde, hasta)
                    contraste_out.controls.clear()
                    if not rep.get("origen"):
                        contraste_out.controls.append(
                            ft.Text(
                                "Sin DB fuente visible: no se pudo contrastar.",
                                size=12,
                                color=G360Theme.warning_color(),
                            )
                        )
                    else:
                        f = rep["filas"]
                        contraste_out.controls.append(
                            ft.Text(
                                f"Origen {rep['origen']}: local {f['local']:,} vs "
                                f"origen {f['origen']:,} filas · "
                                f"solo-local {rep['folios_solo_local']:,} · "
                                f"solo-origen {rep['folios_solo_origen']:,}",
                                size=12,
                                color=G360Theme.text_muted_color(),
                            )
                        )
                        for c, v in rep["columnas"].items():
                            pierde = v.get("pierde")
                            contraste_out.controls.append(
                                ft.Row(
                                    [
                                        ft.Text(c, size=12, width=150),
                                        ft.Text(
                                            f"local {v['local']:,} / origen {v['origen']:,}",
                                            size=12,
                                            expand=True,
                                        ),
                                        ft.Text(
                                            "PIERDE" if pierde else "ok",
                                            size=12,
                                            weight=ft.FontWeight.W_600,
                                            color=(
                                                G360Theme.error_color()
                                                if pierde
                                                else G360Theme.success_color()
                                            ),
                                        ),
                                    ],
                                    spacing=6,
                                )
                            )
                    _upd()
                except Exception as ex:
                    contraste_out.controls.clear()
                    contraste_out.controls.append(
                        ft.Text(f"contraste falló: {ex}", size=12, color=G360Theme.error_color())
                    )
                    _upd()

            threading.Thread(target=_run, daemon=True).start()

        def _contrastar_api(_):
            contraste_out.controls.clear()
            contraste_out.controls.append(
                ft.Text(
                    "Contrastando últimos 90 días contra la API…",
                    size=12,
                    color=G360Theme.text_muted_color(),
                )
            )
            _upd()

            def _run():
                try:
                    from src.core.api_auth import default_api_url
                    from src.core.capture_service import CaptureService
                    from src.core.ventas_api_client import VentaAPIClient

                    token = CaptureService.api_token()
                    if not (token and CaptureService.is_api_token_valid()):
                        contraste_out.controls.clear()
                        contraste_out.controls.append(
                            ft.Text(
                                "Sin token válido contra la API: abrí «Actualizar hoy» "
                                "e iniciá sesión una vez; queda guardado 24h.",
                                size=12,
                                color=G360Theme.warning_color(),
                            )
                        )
                        _upd()
                        return
                    cli = VentaAPIClient(base_url=default_api_url(), api_token=token)
                    try:
                        hasta = date.today().isoformat()
                        desde = (date.today() - timedelta(days=89)).isoformat()
                        rep = cli.contrast(desde, hasta)
                    finally:
                        cli.close()
                    conn = ventas_db.connect(readonly=True)
                    try:
                        cur = conn.execute(
                            "SELECT substr(fecha_orig,1,10), COUNT(*) FROM ventas "
                            "WHERE fecha_orig >= ? AND fecha_orig < date(?, '+1 day') "
                            "GROUP BY substr(fecha_orig,1,10)",
                            (desde, hasta),
                        )
                        local_por_dia = {str(d): int(n) for d, n in cur}
                    finally:
                        conn.close()
                    contraste_out.controls.clear()
                    dias_api = rep.get("dias") or {}
                    total_api = sum(int(v.get("filas", 0) or 0) for v in dias_api.values())
                    total_local = sum(local_por_dia.values())
                    contraste_out.controls.append(
                        ft.Text(
                            f"API vs local (90 días): {total_api:,} vs {total_local:,} filas",
                            size=12,
                            color=G360Theme.text_muted_color(),
                        )
                    )
                    claves = sorted(set(dias_api) | set(local_por_dia))
                    mala = 0
                    for dia in claves:
                        a = int(dias_api.get(dia, {}).get("filas", 0) or 0)
                        l = local_por_dia.get(dia, 0)
                        if a != l:
                            mala += 1
                            contraste_out.controls.append(
                                ft.Row(
                                    [
                                        ft.Text(dia, size=12, width=110),
                                        ft.Text(f"api {a:,} / local {l:,}", size=12, expand=True),
                                        ft.Text(
                                            "DIFERENCIA",
                                            size=12,
                                            weight=ft.FontWeight.W_600,
                                            color=G360Theme.error_color(),
                                        ),
                                    ],
                                    spacing=6,
                                )
                            )
                    if not mala:
                        contraste_out.controls.append(
                            ft.Text("Sin diferencias.", size=12, color=G360Theme.success_color())
                        )
                    _upd()
                except Exception as ex:
                    contraste_out.controls.clear()
                    contraste_out.controls.append(
                        ft.Text(
                            f"contraste con API falló: {ex}", size=12, color=G360Theme.error_color()
                        )
                    )
                    _upd()

            threading.Thread(target=_run, daemon=True).start()

        btn_contraste_api = ft.ElevatedButton(
            "Contrastar con la API (90 días)",
            on_click=_contrastar_api,
            height=34,
            style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
            tooltip="Compara filas por día entre la DB local y la API Go. "
            "Detecta si a la API le falta data sin bajar archivos.",
        )
        btn_contraste_archivo = ft.TextButton(
            "Desde archivo (DB fuente)",
            on_click=_contrastar,
            tooltip="Compara cobertura por columna contra la DB del productor "
            "en disco/red. Usa cuando la API esté offline.",
        )

        threading.Thread(target=worker_campos, daemon=True).start()
        threading.Thread(target=_recargar_ocs, daemon=True).start()

        return ft.Column(
            [
                ft.Text("Campos críticos del sustentor", size=12, weight=ft.FontWeight.W_600),
                status,
                tabla,
                ft.Divider(height=8),
                ft.Row(
                    [btn_contraste_api, btn_contraste_archivo],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=8,
                ),
                contraste_out,
                ft.Divider(height=8),
                ft.Text(
                    "O/C en colisión (pendientes de revisión)", size=12, weight=ft.FontWeight.W_600
                ),
                oc_list,
            ],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
        )

    def _panel_gestion(self, page):
        """Pestaña 'Gestión': cargar DB desde servidor, archivo, red o cartucho."""
        import threading
        from pathlib import Path

        import flet as ft

        from src.core import ventas_db
        from src.core.db_network import get_db_info_quick

        def descargar_base_canonica(_):
            """Descarga la base canónica exportada por la API y valida antes de reemplazar.

            Flujo: export_list() → latest → export_base_canonica(name) →
            archivo temporal → mismo camino de validación/reemplazo que el
            archivo local (mismo botón comp_box/btn_reemplazar).
            """
            status.value = "Listando bases en el servidor…"
            status.color = self.app.G360_ACCENT
            _safe_update(page)

            def worker():
                try:
                    from src.core.api_auth import default_api_url
                    from src.core.capture_service import CaptureService
                    from src.core.ventas_api_client import VentaAPIClient

                    token = CaptureService.api_token()
                    if not (token and CaptureService.is_api_token_valid()):
                        status.value = (
                            "Sin token contra la API: abrí «Actualizar hoy» e iniciá "
                            "sesión una vez; queda guardado 24h."
                        )
                        status.color = self.app.G360_WARNING
                        _safe_update(page)
                        return
                    cli = VentaAPIClient(base_url=default_api_url(), api_token=token)
                    try:
                        listing = cli.export_list()
                        snaps = listing.get("snapshots") or []
                        if not snaps:
                            status.value = (
                                "El servidor no exportó aún ninguna base canónica "
                                "(no hay base_canonica_*.db en export/)."
                            )
                            status.color = self.app.G360_WARNING
                            _safe_update(page)
                            return
                        elegir = snaps[0]
                        nombre = elegir.get("nombre") or elegir.get("Nombre")
                        status.value = f"Descargando {nombre}…"
                        _safe_update(page)
                        data = cli.export_base_canonica(name=nombre)
                    finally:
                        cli.close()
                    if not data:
                        status.value = "Descarga vacía."
                        status.color = self.app.G360_ERROR
                        _safe_update(page)
                        return
                    tmp = ventas_db.data_dir() / "export" / "descargada_base_canonica.db"
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_bytes(data)
                    mb = len(data) / (1024 * 1024)
                    status.value = f"✓ {nombre} descargado ({mb:.1f} MB). Validando…"
                    status.color = self.app.G360_SUCCESS
                    _safe_update(page)
                    _validar_archivo(str(tmp))
                except Exception as ex:
                    status.value = f"✗ Descarga falló: {ex}"
                    status.color = self.app.G360_ERROR
                    _safe_update(page)

            threading.Thread(target=worker, daemon=True).start()

        elegida: dict = {"ruta": None, "info": None}

        comp_box = ft.Column(
            [
                ft.Text(
                    "Selecciona un archivo historial.db.",
                    size=12,
                    color=G360Theme.text_muted_color(),
                ),
            ],
            spacing=4,
        )
        status = ft.Text("", size=12, color=G360Theme.text_muted_color())
        btn_reemplazar = ft.ElevatedButton(
            "Reemplazar DB local",
            height=36,
            disabled=True,
            style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
        )

        def _fmt_info(info: dict) -> dict:
            if not info.get("exists"):
                return {"filas": "—", "fecha": "—", "tam": "—", "integ": "—"}
            if info.get("partial"):
                return {
                    "filas": "— (red lenta)",
                    "fecha": "—",
                    "tam": f"{info.get('size_mb', 0):.0f} MB",
                    "integ": "pendiente",
                }
            return {
                "filas": f"{info.get('rows', 0):,}",
                "fecha": str(info.get("fecha_max") or "—"),
                "tam": f"{info.get('size_mb', 0):.0f} MB",
                "integ": str(info.get("integrity") or info.get("error") or "?"),
            }

        def _pintar_comparativa():
            src_info = elegida["info"]
            if not src_info:
                return
            src = _fmt_info(src_info)
            comp_box.controls.clear()
            comp_box.controls.append(
                ft.Text(f"Archivo: {elegida['ruta']}", size=12, weight=ft.FontWeight.W_600)
            )
            for etiqueta, k in (
                ("Filas", "filas"),
                ("Ultima fecha", "fecha"),
                ("Tamano", "tam"),
                ("Integridad", "integ"),
            ):
                comp_box.controls.append(
                    ft.Row(
                        [
                            ft.Text(
                                etiqueta, size=12, width=90, color=G360Theme.text_muted_color()
                            ),
                            ft.Text(
                                src[k],
                                size=12,
                                expand=True,
                                weight=ft.FontWeight.W_600 if k == "integ" else None,
                            ),
                        ],
                        spacing=6,
                    )
                )
            _safe_update(page)

        def _validar_archivo(ruta: str):
            _logger.info("gestion: validando DB en %s", ruta)
            status.value = "Validando DB..."
            status.color = self.app.G360_ACCENT
            _safe_update(page)

            def _do():
                info = get_db_info_quick(Path(ruta))
                elegida["ruta"] = ruta
                elegida["info"] = info
                if info.get("error"):
                    status.value = f"No valido: {info.get('error')}"
                    status.color = self.app.G360_ERROR
                    btn_reemplazar.disabled = True
                    _logger.warning("gestion: DB no valida: %s", info.get("error"))
                elif info.get("partial"):
                    status.value = (
                        f"Estructura OK ({info.get('size_mb', 0):.0f} MB). "
                        "Conteo omitido por red lenta; se verificara al reemplazar."
                    )
                    status.color = self.app.G360_SUCCESS
                    btn_reemplazar.disabled = False
                    _logger.info(
                        "gestion: DB parcial (red lenta) - %s MB, schema OK", info.get("size_mb", 0)
                    )
                elif not info.get("rows"):
                    status.value = "No valido: sin filas"
                    status.color = self.app.G360_ERROR
                    btn_reemplazar.disabled = True
                    _logger.warning("gestion: DB sin filas: %s", ruta)
                elif info.get("integrity") != "ok":
                    status.value = f"Integridad: {info.get('integrity')}"
                    status.color = self.app.G360_ERROR
                    btn_reemplazar.disabled = True
                    _logger.warning("gestion: DB integridad fallo: %s", info.get("integrity"))
                else:
                    status.value = f"Valido: {info['rows']:,} filas hasta {info.get('fecha_max')}"
                    status.color = self.app.G360_SUCCESS
                    btn_reemplazar.disabled = False
                    _logger.info(
                        "gestion: DB valida - %s filas, %s MB, integridad=%s, fecha_max=%s",
                        info.get("rows", 0),
                        info.get("size_mb", 0),
                        info.get("integrity"),
                        info.get("fecha_max"),
                    )
                _pintar_comparativa()
                try:
                    btn_reemplazar.update()
                except Exception:
                    pass
                _safe_update(page)

            threading.Thread(target=_do, daemon=True).start()

        def elegir_archivo(_):
            def pick():
                ruta = None
                try:
                    ruta = self.app._pick_file(
                        "Elegir historial.db", allowed_extensions=["db", "sqlite"]
                    )
                except Exception:
                    pass
                if ruta:
                    _logger.info("gestion: archivo seleccionado via picker: %s", ruta)
                    _validar_archivo(ruta)

            threading.Thread(target=pick, daemon=True).start()

        def elegir_red(_):
            def _do():
                path_input.value = path_input.value.strip()
                if not path_input.value:
                    status.value = "Escribe una ruta de red primero"
                    status.color = self.app.G360_WARNING
                    _safe_update(page)
                    return
                candidate = Path(path_input.value)
                if candidate.is_dir():
                    candidate = candidate / "historial.db"
                _logger.info("gestion: buscando DB en ruta de red: %s", candidate)
                if not candidate.exists():
                    status.value = f"No encontrado: {candidate}"
                    status.color = self.app.G360_ERROR
                    _logger.warning("gestion: DB no encontrada en %s", candidate)
                    _safe_update(page)
                    return
                _logger.info(
                    "gestion: DB encontrada en %s (%.1f MB)",
                    candidate,
                    candidate.stat().st_size / (1024 * 1024),
                )
                _validar_archivo(str(candidate))

            threading.Thread(target=_do, daemon=True).start()

        def elegir_cartucho(_):
            def pick():
                ruta = None
                try:
                    ruta = self.app._pick_file(
                        "Elegir cartucho (.zip), CARTUCHO.json o historial.db",
                        allowed_extensions=["zip", "json", "db", "sqlite"],
                    )
                except Exception:
                    pass
                if not ruta:
                    return
                p = Path(ruta)
                # .zip va directo; CARTUCHO.json -> su carpeta;
                # historial.db suelto (p.ej. carpeta de ventas-db) -> semilla.
                _importar_cartucho(
                    p
                    if p.suffix.lower() == ".zip"
                    else (p.parent if p.suffix.lower() == ".json" else p)
                )

            threading.Thread(target=pick, daemon=True).start()

        def adoptar_lineas_cartucho(_):
            from src.core.cartucho import adoptar_lineas, leer_allowlist_cartucho

            def pick():
                ruta = None
                try:
                    ruta = self.app._pick_file(
                        "Elegir CARTUCHO.json para ver sus líneas", allowed_extensions=["json"]
                    )
                except Exception:
                    pass
                if not ruta:
                    return
                try:
                    nuevas = leer_allowlist_cartucho(Path(ruta))
                except Exception as ex:
                    status.value = f"✗ No se pudo leer: {ex}"
                    status.color = self.app.G360_ERROR
                    _safe_update(page)
                    return
                if not nuevas:
                    status.value = "Ese cartucho no trae líneas."
                    status.color = self.app.G360_WARNING
                    _safe_update(page)
                    return
                actuales = set(ventas_db.allowed_lines())
                otras = sorted(set(nuevas) - actuales)
                if not otras:
                    status.value = "Ya trabajás con esas líneas."
                    status.color = self.app.G360_SUCCESS
                    _safe_update(page)
                    return
                status.value = (
                    f"El cartucho trae {len(nuevas)} líneas "
                    f"({len(otras)} nuevas para vos: {', '.join(otras[:12])}"
                    f"{'…' if len(otras) > 12 else ''}). "
                    "Confirma abajo para adoptarlas."
                )
                status.color = self.app.G360_WARNING
                _safe_update(page)

                def confirmar(_e):
                    page.close(dlg_lineas)
                    try:
                        r = adoptar_lineas(nuevas)
                        status.value = (
                            f"✓ Líneas adoptadas: {len(r['despues'])} "
                            f"(antes {len(r['antes'])}). Rige al instante."
                        )
                        status.color = self.app.G360_SUCCESS
                    except Exception as ex:
                        status.value = f"✗ No se pudo adoptar: {ex}"
                        status.color = self.app.G360_ERROR
                    _safe_update(page)

                dlg_lineas = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Adoptar líneas del cartucho"),
                    content=ft.Text(
                        f"Tu configuración actual ({len(actuales)}) se reemplaza "
                        f"por la del cartucho ({len(nuevas)}). Solo cambia qué "
                        f"líneas ves; la DB no se toca."
                    ),
                    actions=[
                        ft.TextButton("Cancelar", on_click=lambda _: page.close(dlg_lineas)),
                        ft.TextButton("Adoptar", on_click=confirmar),
                    ],
                )
                page.open(dlg_lineas)

            threading.Thread(target=pick, daemon=True).start()

        def _exportar_cartucho(_):
            from src.core.cartucho import exportar_cartucho

            _correr_export_cartucho(
                carpeta_export=ventas_db.data_dir() / "export",
                status=status,
                btn_export=btn_export,
                app=self.app,
                page=page,
                exportar=exportar_cartucho,
            )

        def _abrir_export(_):
            try:
                import os as _os

                _os.startfile(  # noqa: S606 - solo Windows
                    str(ventas_db.data_dir() / "export")
                )
                status.value = "Carpeta export abierta en el explorador"
                status.color = self.app.G360_SUCCESS
            except Exception as ex:
                status.value = f"✗ No se pudo abrir: {ex}"
                status.color = self.app.G360_ERROR
            _safe_update(page)

        def _importar_cartucho(carpeta: Path):
            from src.core.cartucho import importar_cartucho

            status.value = f"Validando cartucho en {carpeta}…"
            status.color = self.app.G360_ACCENT
            _safe_update(page)

            def run():
                try:
                    r = importar_cartucho(carpeta)
                    modo = r.get("modo", "?")
                    det = ""
                    if modo == "reemplazar":
                        det = (
                            f"{r.get('dias_reconstruidos', 0)} días; "
                            f"alias nuevos {r.get('sidecar', {}).get('alias_nuevos', 0)}"
                        )
                    else:
                        m = r.get("merge", {})
                        det = (
                            f"{m.get('folios', 0)} folios / {m.get('filas', 0)} filas; "
                            f"alias nuevos {r.get('sidecar', {}).get('alias_nuevos', 0)}"
                        )
                    n_conf = len(r.get("sidecar", {}).get("alias_conflictos", []))
                    if n_conf:
                        det += f"; ⚠ {n_conf} conflictos O/C (gana entrante, ver log)"
                    status.value = f"✓ Cartucho aplicado ({modo}): {det}"
                    status.color = self.app.G360_SUCCESS
                    self._refrescar_card_db()
                except Exception as ex:
                    status.value = f"✗ No se pudo importar: {ex}"
                    status.color = self.app.G360_ERROR
                _safe_update(page)

            threading.Thread(target=run, daemon=True).start()

        # Barra de progreso para reemplazo (visible mientras copia)
        replace_progress = ft.ProgressBar(visible=False, width=300, color=self.app.G360_ACCENT)
        replace_spinner = ft.ProgressRing(visible=False, width=16, height=16, stroke_width=2)

        def reemplazar(_):
            ruta = elegida["ruta"]
            if not ruta:
                return

            def ejecutar(_e):
                page.close(dlg_confirm)
                btn_reemplazar.disabled = True
                try:
                    src_size = Path(ruta).stat().st_size
                except Exception:
                    src_size = 0
                dest = ventas_db.db_path()

                # Dialogo modal de progreso (imposible de ignorar)
                prog_status = ft.Text("Iniciando reemplazo...", size=12)
                prog_bar = ft.ProgressBar(value=0, width=440, color=self.app.G360_ACCENT)
                prog_log = ft.Text("", size=10, color=G360Theme.text_muted_color())
                prog_done = threading.Event()
                btn_close = ft.TextButton(
                    "Cerrar", disabled=True, on_click=lambda _: page.close(dlg_prog)
                )

                def _plog(msg):
                    ts = datetime.now().strftime("%H:%M:%S")
                    prog_log.value = (prog_log.value + f"\n[{ts}] {msg}")[-2000:]
                    prog_status.value = msg[:110]
                    _safe_update(page)

                dlg_prog = ft.AlertDialog(
                    modal=True,
                    title=ft.Row(
                        [
                            ft.ProgressRing(width=18, height=18, stroke_width=2),
                            ft.Text("Reemplazando DB local", size=14, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=8,
                    ),
                    content=ft.Column(
                        [prog_status, prog_bar, prog_log],
                        spacing=8,
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    actions=[btn_close],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
                page.open(dlg_prog)
                status.value = "Reemplazando DB local (ver dialogo de progreso)..."
                status.color = self.app.G360_ACCENT
                replace_progress.visible = True
                replace_progress.value = 0
                replace_spinner.visible = True
                _safe_update(page)

                def run():
                    import time as _rt

                    def _poll():
                        # % real: tamano del destino vs tamano del origen
                        while not prog_done.is_set():
                            try:
                                cur = dest.stat().st_size if dest.exists() else 0
                                pct = min(0.99, cur / src_size) if src_size else 0
                                prog_bar.value = pct
                                replace_progress.value = pct
                                try:
                                    prog_bar.update()
                                except Exception:
                                    pass
                                try:
                                    replace_progress.update()
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            _rt.sleep(2)

                    try:
                        _logger.info("gestion: reemplazando DB local con %s", ruta)
                        _plog(f"Origen: {ruta} ({src_size / (1024 * 1024):.0f} MB)")
                        _plog("Paso 1/3: backup automatico de DB actual...")
                        threading.Thread(target=_poll, daemon=True).start()
                        _plog("Paso 2/3: copiando (puede tardar varios minutos)...")
                        stats = ventas_db.import_db(Path(ruta))
                        prog_done.set()
                        prog_bar.value = 1.0
                        replace_progress.value = 1.0
                        _logger.info(
                            "gestion: DB reemplazada OK - %s filas (%s -> %s)",
                            stats.get("filas", 0),
                            stats.get("fecha_min"),
                            stats.get("fecha_max"),
                        )
                        _plog(
                            f"Paso 3/3: verificado - {stats['filas']:,} filas "
                            f"({stats['fecha_min']} -> {stats['fecha_max']})"
                        )
                        prog_status.value = f"DB reemplazada: {stats['filas']:,} filas"
                        try:
                            btn_close.disabled = False
                            btn_close.update()
                        except Exception:
                            pass
                        _rt.sleep(0.3)
                        replace_progress.visible = False
                        replace_spinner.visible = False
                        status.value = (
                            f"DB reemplazada: {stats['filas']:,} filas "
                            f"({stats['fecha_min']} -> {stats['fecha_max']})"
                        )
                        status.color = self.app.G360_SUCCESS
                        elegida["ruta"] = None
                        elegida["info"] = None
                        comp_box.controls.clear()
                        comp_box.controls.append(
                            ft.Text(
                                "Selecciona un archivo historial.db.",
                                size=12,
                                color=G360Theme.text_muted_color(),
                            )
                        )
                        self._refrescar_card_db()
                        _rt.sleep(1.5)
                        try:
                            page.close(dlg_prog)
                        except Exception:
                            pass
                        try:
                            self.app.show_snackbar(
                                f"Historial reemplazado (+{stats['filas']:,} filas)",
                                self.app.G360_SUCCESS,
                            )
                        except Exception:
                            pass
                    except Exception as ex:
                        prog_done.set()
                        replace_progress.visible = False
                        replace_spinner.visible = False
                        _plog(f"ERROR: {ex}")
                        prog_status.color = self.app.G360_ERROR
                        status.value = f"Reemplazo fallo: {ex}"
                        status.color = self.app.G360_ERROR
                        _logger.error("gestion: reemplazo fallo: %s", ex)
                        try:
                            btn_close.disabled = False
                            btn_close.update()
                        except Exception:
                            pass
                    _safe_update(page)

                threading.Thread(target=run, daemon=True).start()

            dlg_confirm = ft.AlertDialog(
                modal=True,
                title=ft.Text("Reemplazar DB local", size=14, weight=ft.FontWeight.BOLD),
                content=ft.Text(
                    "Se guarda backup automatico y luego se reemplaza. Continuar?", size=12
                ),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda __: page.close(dlg_confirm)),
                    ft.ElevatedButton(
                        "Reemplazar",
                        on_click=ejecutar,
                        style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.open(dlg_confirm)

        btn_reemplazar.on_click = reemplazar

        path_input = ft.TextField(
            label="Ruta de red (UNC o mapeada)",
            hint_text="\\\\servidor\\compartido\\historial.db",
            height=40,
            expand=True,
            text_size=12, )

        db_info_text = ft.Text("Cargando...", size=10, color=G360Theme.text_muted_color())

        def _load_db_info():
            try:
                info = (
                    get_db_info_quick(ventas_db.db_path()) if ventas_db.db_path().exists() else {}
                )
                if info.get("exists"):
                    db_info_text.value = (
                        f"DB actual: {info.get('rows', 0):,} filas | "
                        f"{info.get('size_mb', 0):.0f} MB | "
                        f"ultima fecha: {info.get('fecha_max', '?')}"
                    )
                else:
                    db_info_text.value = "No hay DB local."
                _safe_update(page)
            except Exception:
                pass

        threading.Thread(target=_load_db_info, daemon=True).start()

        cuerpo = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(ft.Icons.STORAGE_OUTLINED, "DB ACTUAL"),
                            db_info_text,
                        ],
                        spacing=4,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=4),
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(
                                ft.Icons.CLOUD_DOWNLOAD_OUTLINED, "DEL SERVIDOR"
                            ),
                            ft.Container(height=4),
                            ft.Text(
                                "Sin cartucho a mano: descarga la base canónica que "
                                "la API exportó (online). Útil si pasaron meses sin "
                                "actualizar o quieres una copia limpia.",
                                size=10,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Container(height=6),
                            ft.ElevatedButton(
                                "Descargar base canónica del servidor…",
                                height=32,
                                icon=ft.Icons.CLOUD_DOWNLOAD_OUTLINED,
                                on_click=descargar_base_canonica,
                                style=ft.ButtonStyle(
                                    bgcolor=G360Theme.with_opacity(0.15, self.app.G360_ACCENT),
                                    color=self.app.G360_ACCENT,
                                ),
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=4),
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(ft.Icons.USB_OUTLINED, "ARCHIVO / USB"),
                            ft.Container(height=4),
                            ft.Text(
                                "Busca un historial.db en tu disco o pendrive.",
                                size=10,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Container(height=6),
                            ft.ElevatedButton(
                                "Elegir archivo...",
                                height=32,
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=elegir_archivo,
                                style=ft.ButtonStyle(
                                    bgcolor=G360Theme.with_opacity(0.15, self.app.G360_ACCENT),
                                    color=self.app.G360_ACCENT,
                                ),
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=4),
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(ft.Icons.LAN_OUTLINED, "CARPETA DE RED"),
                            ft.Container(height=4),
                            ft.Text(
                                "Pega la ruta UNC de un historial.db compartido.",
                                size=10,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Container(height=6),
                            ft.Row(
                                [
                                    path_input,
                                    ft.ElevatedButton(
                                        "Buscar",
                                        height=32,
                                        on_click=elegir_red,
                                        style=ft.ButtonStyle(
                                            bgcolor=self.app.G360_ACCENT,
                                        ),
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=4),
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(ft.Icons.INVENTORY_2_OUTLINED, "CARTUCHO"),
                            ft.Container(height=4),
                            ft.Text(
                                "Trae de otra PC o de ventas-db: elige el .zip, "
                                "el CARTUCHO.json o el historial.db suelto. "
                                "Se valida solo, reemplaza si es más nuevo o trae "
                                "lo que falta sin pisar nada.",
                                size=10,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Container(height=6),
                            ft.ElevatedButton(
                                "Importar cartucho...",
                                height=32,
                                icon=ft.Icons.DOWNLOAD_OUTLINED,
                                on_click=elegir_cartucho,
                                style=ft.ButtonStyle(
                                    bgcolor=G360Theme.with_opacity(0.15, self.app.G360_ACCENT),
                                    color=self.app.G360_ACCENT,
                                ),
                            ),
                            ft.Container(height=6),
                            ft.OutlinedButton(
                                "Adoptar líneas del cartucho...",
                                height=32,
                                icon=ft.Icons.CHECKLIST_OUTLINED,
                                on_click=adoptar_lineas_cartucho,
                            ),
                            ft.Container(height=6),
                            ft.Text(
                                "Para llevar a otra PC: genera el .zip único "
                                "(DB + sidecar + manifiesto).",
                                size=10,
                                color=G360Theme.text_muted_color(),
                            ),
                            ft.Container(height=6),
                            ft.Row(
                                [
                                    btn_export := ft.ElevatedButton(
                                        "📦 Exportar cartucho",
                                        height=32,
                                        icon=ft.Icons.UPLOAD_OUTLINED,
                                        on_click=_exportar_cartucho,
                                        style=ft.ButtonStyle(
                                            bgcolor=self.app.G360_ACCENT,
                                        ),
                                    ),
                                    ft.OutlinedButton(
                                        "📂 Abrir export",
                                        height=32,
                                        on_click=_abrir_export,
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=4),
                ft.Container(
                    content=ft.Column(
                        [
                            G360Theme.section_header(
                                ft.Icons.COMPARE_ARROWS, "ARCHIVO SELECCIONADO"
                            ),
                            ft.Container(height=4),
                            comp_box,
                            ft.Container(height=4),
                            ft.Row([btn_reemplazar], alignment=ft.MainAxisAlignment.END),
                            ft.Row(
                                [replace_spinner, replace_progress],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            status,
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=12, vertical=10),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                ),
                ft.Container(height=8),
                # ── Modo avanzado: captura XLS por intranet ──
                # LEGACY: el flujo XLS completo (login intranet + descarga lenta)
                # se mantiene como fallback. Solo visible/promovido acá, fuera
                # del camino principal; la card y «Actualizar hoy» van por API.
                ft.Row(
                    [
                        ft.TextButton(
                            "Modo avanzado (captura XLS por intranet)",
                            icon=ft.Icons.MORE_HORIZ,
                            style=ft.ButtonStyle(color=G360Theme.text_muted_color()),
                            on_click=self._gestionar_datos,
                            tooltip=(
                                "Descargas lentas por intranet (formato XLS). "
                                "Para el día a día usa «Actualizar hoy»."
                            ),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            spacing=6,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )
        return cuerpo

    def _construir_strip_captura(self) -> ft.Container:
        """Tira de estado del proceso de captura en segundo plano (estilo Tauri:
        paso a paso visible aunque se cierre el dialog). Polling de CAPTURE_STATUS."""
        import flet as ft

        self.strip_txt = ft.Text("", size=12, expand=True, weight=ft.FontWeight.W_500)
        self.strip_pasos = ft.Text("", size=10, color=ft.Colors.ON_SURFACE_VARIANT)
        self.strip_bar = ft.ProgressBar(value=0, visible=False, expand=True, height=4)
        self.btn_strip_stop = ft.TextButton(
            "Detener",
            visible=False,
            on_click=self._strip_stop,
            tooltip="Aborta la captura al instante (cierra el socket HTTP en vuelo)",
        )
        self.strip_captura = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [self.strip_txt, self.btn_strip_stop],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    self.strip_bar,
                    self.strip_pasos,
                ],
                spacing=4,
            ),
            padding=10,
            border_radius=12,
            bgcolor=G360Theme.accent_soft_color(0.08),
            visible=False,
        )
        self._refrescar_strip()
        return self.strip_captura

    def _refrescar_strip(self):
        """Sincroniza la tira con CAPTURE_STATUS (llamado por el poll cada 1s)."""
        try:
            from src.core.capture_service import CAPTURE_STATUS

            snap = CAPTURE_STATUS.snapshot()
            strip = self.strip_captura
            if strip is None or self.app.page is None:
                return
            import time as _t

            def _fmt(s: float) -> str:
                s = int(max(0, s))
                if s < 60:
                    return f"{s}s"
                m, ss = divmod(s, 60)
                if m < 60:
                    return f"{m}m {ss:02d}s"
                h, m = divmod(m, 60)
                return f"{h}h {m:02d}m"

            if snap["running"]:
                elapsed = _t.time() - snap["started_at"] if snap["started_at"] else 0
                eta = ""
                if snap["pct"] > 0.02:
                    eta = f" · ETA {_fmt(elapsed / snap['pct'] * (1 - snap['pct']))}"
                self.strip_txt.value = (
                    f"⬇ {snap['mode'] or 'Descarga'} en curso: {snap['index']}/{snap['total']}"
                    f"{' · ' + snap['current'] if snap['current'] else ''}"
                    f" · {snap['filas']:,} filas · {_fmt(elapsed)}{eta}"
                )
                ult = " ".join(f"✓{l}" for l in snap["ok"][-5:])
                if snap["current"]:
                    ult += f"   ⏳{snap['current']}"
                if snap["fallidos"]:
                    ult += f"   ✗{len(snap['fallidos'])} fallido(s)"
                self.strip_pasos.value = (ult or snap["message"][:110]).strip()
                self.strip_bar.visible = True
                self.strip_bar.value = snap["pct"] if snap["pct"] > 0 else None
                self.btn_strip_stop.visible = True
                strip.visible = True
            elif snap["finished_at"] and _t.time() - snap["finished_at"] < 120:
                estado = (
                    "detenida"
                    if snap["abortado"]
                    else ("con fallos" if snap["fallidos"] else "completada")
                )
                self.strip_txt.value = (
                    f"{'⏹' if snap['abortado'] else '✓'} Descarga {estado}: {snap['filas']:,} filas"
                    + (f" · ✗ {len(snap['fallidos'])} fallidos" if snap["fallidos"] else "")
                )
                self.strip_pasos.value = snap["message"][:110]
                self.strip_bar.visible = False
                self.btn_strip_stop.visible = False
                strip.visible = True
            else:
                if strip.visible:
                    strip.visible = False
            # CRITICO: solo update si el control esta montado en la pagina.
            # Un update prematuro (poll corriendo antes del page.add) lanza
            # AssertionError y MATA el render de Flet -> ventana en blanco.
            if getattr(strip, "page", None) is None:
                return
            try:
                strip.update()
            except Exception:
                _logger.exception("strip.update() fallo — pista del render en blanco")
        except Exception:
            _logger.exception("_refrescar_strip fallo")

    def _strip_stop(self, e):
        from src.core.capture_service import CAPTURE_STATUS

        if CAPTURE_STATUS.abort_active():
            self.app.show_snackbar(
                "Detención solicitada — abortando request en curso", ft.Colors.ORANGE
            )

    def _iniciar_poll_captura(self):
        """Hilo de polling (1s) que mantiene la tira sincronizada con el proceso."""
        if getattr(self, "_poll_iniciado", False):
            return
        self._poll_iniciado = True

        def poll():
            import time as _t

            while True:
                _t.sleep(1.0)
                try:
                    self._refrescar_strip()
                except Exception:
                    _logger.exception("poll strip fallo")

        threading.Thread(target=poll, daemon=True).start()

    def _construir_card_db(self, async_: bool = False) -> ft.Container:
        """Card de estado SQLite con KPIs modernos estilo G360."""
        import flet as ft

        try:
            self.card_db_kpis = ft.Column([], spacing=8)
            self.card_db_status = ft.Text(
                "Calculando…",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
            )
            self.card_db_alerta = ft.Text("", size=12, color=ft.Colors.ORANGE_300)

            self.btn_actualizar_hoy = G360Theme.ghost_button(
                "Actualizar hoy",
                icon=ft.Icons.CLOUD_SYNC,
                on_click=self._actualizar_hoy,
            )
            self.btn_config = G360Theme.ghost_button(
                "Configuración de DB",
                icon=ft.Icons.SETTINGS_OUTLINED,
                on_click=self._abrir_config_db,
            )

            self.card_db_kpis.controls = [
                ft.Row(
                    [
                        ft.ProgressRing(
                            width=16, height=16, stroke_width=2, color=G360Theme.accent_color()
                        ),
                        ft.Text(
                            "Calculando estado de la base…",
                            size=12,
                            color=G360Theme.text_muted_color(),
                        ),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            ]

            self._refrescar_card_db(async_=async_)

            return G360Theme.card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.STORAGE_OUTLINED,
                                    size=18,
                                    color=G360Theme.accent_color(),
                                ),
                                G360Theme.card_title("Historial local"),
                            ],
                            spacing=G360Theme.SPACE_SM,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            # El cuerpo de la card ya va centrado (KPIs, alerta y
                            # descripcion); el titulo se alineaba a la izquierda
                            # y los botones a la derecha, y esa mezcla se leia
                            # como descompensada.
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        G360Theme.subtitle(
                            "Estado y actualización de la base del ERP",
                            align=ft.TextAlign.CENTER,
                        ),
                        ft.Container(
                            content=self.card_db_status,
                            visible=bool(self.card_db_status.value),
                        ),
                        ft.Container(
                            content=self.card_db_kpis,
                            padding=ft.padding.only(top=6, bottom=4),
                        ),
                        ft.Container(
                            content=self.card_db_alerta,
                            # Alignment, no MainAxisAlignment: este enum era el
                            # unico motivo real del crash que tumbaba la UI
                            # principal con "'mappingproxy' object has no
                            # attribute '__dict__'".
                            alignment=ft.alignment.center,
                            visible=bool(self.card_db_alerta.value),
                        ),
                        ft.Row(
                            [
                                self.btn_actualizar_hoy,
                                self.btn_config,
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.CENTER,
                            wrap=True,
                        ),
                    ],
                    spacing=G360Theme.SPACE_SM,
                ),
            )
        except Exception as exc:
            import logging

            logging.getLogger("g360.ui").exception("_construir_card_db fallo: %s", exc)
            return ft.Container(
                content=ft.Text(
                    f"Card DB no disponible: {exc}", size=12, color=ft.Colors.ON_SURFACE_VARIANT
                ),
                padding=12,
                border_radius=12,
                bgcolor=G360Theme.surface_variant_color(),
            )

    # LEGADO EN ELIMINACION: `_sincronizar_desde_api` y `_mostrar_resultado_api`
    # ﹣  borradas. El modal `ApiSyncModal` (ver _actualizar_hoy) es el único
    #  camino: login + frescura + sync incremental en un solo flujo sobre
    #  forticor HTTP.
    #
    # El flujo XLS/intranet completo (_panel_intranet, _gestionar_datos,
    # _modal_login_intranet, _iniciar_descarga) se mantiene funcionando pero
    # es accesible solo desde Configuración → Fuentes.

    def _refrescar_card_db(self, async_: bool = False):
        """Actualiza los KPIs de la card DB."""
        import logging

        log = logging.getLogger("g360.ui.card_db")
        if async_:
            threading.Thread(
                target=self._refrescar_card_db, kwargs={"async_": False}, daemon=True
            ).start()
            return

        try:
            from src.core import ventas_db
            from src.core.fechas import rango_ui

            info = ventas_db.db_card_info()
            log.debug("db_card_info=%s", info)
            status_text = ""
            status_color = ft.Colors.ON_SURFACE_VARIANT

            if not info.get("exists"):
                self.card_db_kpis.controls = [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.STORAGE, size=20, color=G360Theme.text_muted_color()),
                            ft.Text(
                                "Sin base de datos local",
                                size=13,
                                color=G360Theme.text_muted_color(),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ]
                self.card_db_status.value = "Primera vez · configura tu base de datos"
                self.card_db_status.color = G360Theme.text_muted_color()
                self.card_db_alerta.value = ""
                self.card_db_alerta.color = ft.Colors.TRANSPARENT
                self.btn_config.visible = True
                self.btn_actualizar_hoy.visible = True
                self.card_db_kpis.update()
                self.card_db_status.update()
                self.card_db_alerta.update()
                self.btn_config.update()
                self.btn_actualizar_hoy.update()
                return

            filas = info.get("filas", 0)
            fmax = info.get("fecha_max")
            fmin = info.get("fecha_min")
            dias_ultimo = info.get("dias_desde_ultimo")
            incompletos = info.get("incompletos", [])
            huecos = info.get("huecos", [])
            nc_count = info.get("nc_asociadas", 0)
            _fila_kpis = []

            def _kpi_chip(icon, valor, color, label):
                # Chip = tile metrica. Comparte radio, fondo, borde y escala
                # de texto con resultados_view._metric_tile para que las dos
                # surfaces de KPI se lean como la misma pieza.
                return ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(icon, size=14, color=color),
                            ft.Text(
                                valor,
                                size=12,
                                weight=ft.FontWeight.W_700,
                                color=color,
                            ),
                            ft.Text(
                                label, size=12, color=G360Theme.text_muted_color()
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=G360Theme.SPACE_XS,
                    ),
                    padding=ft.padding.symmetric(horizontal=18, vertical=10),
                    border_radius=G360Theme.RADIUS_CONTROL,
                    bgcolor=G360Theme.surface_variant_color(),
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                )

            # La DB guarda ISO; en pantalla va dd-mm-yyyy (ver src/core/fechas.py).
            cobertura = rango_ui(fmin, fmax) if fmin and fmax else "—"
            chips = [
                _kpi_chip(
                    ft.Icons.DATE_RANGE_OUTLINED,
                    cobertura,
                    G360Theme.accent_color(),
                    "Cobertura",
                ),
                _kpi_chip(
                    ft.Icons.TABLE_CHART_OUTLINED,
                    f"{filas:,}",
                    G360Theme.text_primary_color(),
                    "Filas",
                ),
            ]
            if nc_count:
                chips.append(
                    _kpi_chip(
                        ft.Icons.NOTE_ADD_OUTLINED,
                        f"{nc_count:,}",
                        G360Theme.error_color(),
                        "NC asoc.",
                    )
                )

            snap_txt, snap_color = self._chip_snapshot_txt()
            chips.append(_kpi_chip(ft.Icons.CLOUD_SYNC_OUTLINED, snap_txt, snap_color, "Snapshot"))

            _fila_kpis.append(
                ft.Row(
                    chips,
                    spacing=10,
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            )

            self.card_db_kpis.controls = _fila_kpis
            self.card_db_kpis.update()

            if incompletos:
                status_text = f"⚠ {len(incompletos)} mes(es) incompleto(s)"
                status_color = G360Theme.warning_color()
            elif huecos:
                status_text = f"⚠ {len(huecos)} mes(es) ausente(s)"
                status_color = G360Theme.warning_color()
            elif dias_ultimo is not None and dias_ultimo <= 1:
                status_text = "✓ Al día"
                status_color = G360Theme.ok_color()
            else:
                status_text = f"Datos: {rango_ui(fmin, fmax, '  ')}"
                status_color = ft.Colors.ON_SURFACE_VARIANT

            self.card_db_status.value = status_text
            self.card_db_status.color = status_color

            alerts = []
            if incompletos:
                alerts.append(f"Incompletos: {', '.join(incompletos[:4])}")
            if huecos:
                alerts.append(f"Ausentes: {', '.join(huecos[:4])}")
            self.card_db_alerta.value = " · ".join(alerts) if alerts else ""
            self.card_db_alerta.color = (
                G360Theme.warning_color() if alerts else ft.Colors.TRANSPARENT
            )

            self.btn_config.visible = True
            self.btn_actualizar_hoy.visible = True

            self.card_db_kpis.update()
            self.card_db_status.update()
            self.card_db_alerta.update()
            self.btn_config.update()
            self.btn_actualizar_hoy.update()
            log.debug("refresh ok status=%s kpis=%s", status_text, len(self.card_db_kpis.controls))
        except AssertionError:
            # Flet lanza esto al pedir update() de un control que todavia no
            # esta en la pagina, y en el build sincrono inicial es lo normal:
            # la card se pinta sola al abrir. Antes se logueaba como
            # exception con traceback en cada arranque.
            log.debug("card DB: controles sin montar aun; se pintaran al abrir")
        except Exception:
            log.exception("refrescar_card_db fallo")

    def _chip_snapshot_txt(self) -> tuple[str, object]:
        """Texto + color del chip "Snapshot" del servidor, según el health-check."""
        import logging

        log = logging.getLogger("g360.ui.card_db")
        try:
            from src.core.api_robustness.state import ultimo_health

            h = ultimo_health()
            log.debug("snapshot health=%s", h)
            if h is None:
                return "—", G360Theme.text_muted_color()
            if not h.get("api_online"):
                return "offline", G360Theme.error_color()
            horas = h.get("desfase_horas")
            if horas is None:
                return "ok", G360Theme.ok_color()
            if horas > 24:
                return f"viejo {int(horas)}h", G360Theme.error_color()
            if horas > 2:
                return f"{horas:.0f}h", G360Theme.warning_color()
            return "ok", G360Theme.ok_color()
        except Exception:
            log.exception("chip snapshot fallo")
            return "—", G360Theme.text_muted_color()

    def _construir_seccion_insumos(self, tipo_cfg: dict) -> ft.Column:
        """Sección de adjuntos externos y estado del historial seleccionado."""
        from src.ui.widgets.workflow_section import workflow_section

        if not hasattr(self, "_insumo_files"):
            self._insumo_files = []  # [{"ruta":..., "tipo":..., "lbl":...}]
            self._preview_threads = set()
        self.insumo_hist_panel = ft.Column([], spacing=6)
        self.insumo_list_wrapper = ft.Container(
            content=ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO),
            border_radius=12,
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            padding=10,
            height=220,
            visible=False,
        )
        self.insumo_list = self.insumo_list_wrapper.content
        self.insumo_empty_state = ft.Row([], spacing=8, visible=False)
        self.insumo_browse_btn = ft.ElevatedButton(
            "Añadir archivos",
            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
            height=36,
            style=ft.ButtonStyle(
                bgcolor=G360Theme.with_opacity(0.12, G360Theme.accent_color()),
                color=G360Theme.accent_text_color(),
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.padding.symmetric(horizontal=20),
            ),
            on_click=self._insumo_browse_files,
        )
        etiquetas = {
            "lista_precios": "Lista de precios",
            "sku": "Lista de SKU",
            "cantidad": "Cantidades / stock",
            "porcentaje": "Porcentajes / descuentos",
            "mecanica": "Mecánica promocional",
        }
        if self._tipo_incluye("diferencia_stock"):
            # VRS: el archivo combinado (cantidad + precio) ocupa el slot
            # de lista; "cantidad" pasa a ser el override opcional.
            etiquetas["lista_precios"] = "Archivo por SKU (cantidad + precio)"
            etiquetas["cantidad"] = "Override de cantidades (opcional)"
        # Línea y objetivo son parámetros de pantalla; historial viene de
        # SQLite. Solo estos cinco insumos se cargan como archivos.
        insumos_xlsx = [i for i in tipo_cfg.get("insumos", ()) if i in etiquetas]
        if self._tipo_incluye("feria_preventa"):
            # FPE: el archivo único es el requerimiento del evento (SKU +
            # cantidad + descuento) y entra al slot de requerimientos; las
            # etiquetas por nombre de insumo confundirían ("Lista de SKU",
            # "Porcentajes") porque no hay slots separados.
            insumos_xlsx = ["mecanica"]
            etiquetas["mecanica"] = "Requerimientos (SKU + cantidad + descuento)"

        self.insumo_browse_btn.visible = bool(insumos_xlsx)
        # Sin expand: la fila usa wrap=True y un hijo expandido en un Wrap
        # hace castear WrapParentData a FlexParentData (render roto).
        self.insumo_hint = ft.Text(
            (
                "Archivos de apoyo: " + " · ".join(etiquetas[i] for i in insumos_xlsx)
                if insumos_xlsx
                else "Este caso usa el historial seleccionado; no requiere archivos externos."
            ),
            size=10,
            color=G360Theme.text_muted_color(),
            selectable=True,
        )
        self._pintar_panel_historial()
        self._renderizar_lista_insumos()

        contenido = ft.Column(
            [
                self.insumo_hist_panel,
                ft.Row(
                    [self.insumo_hint, self.insumo_browse_btn],
                    spacing=12,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.insumo_empty_state,
                self.insumo_list_wrapper,
            ],
            spacing=10,
        )
        return workflow_section(
            "Insumos",
            ft.Icons.INVENTORY_2_OUTLINED,
            contenido,
            description="Historial seleccionado y archivos que alimentan este caso.",
        )

    def _insumo_browse_files(self, e):
        """Abre diálogo para seleccionar archivos XLSX."""
        from src.ui.catalog import CATALOGO

        tipos = CATALOGO.get(self.tipo_actual)
        if not tipos:
            return
        insumos = tipos.insumos
        exts = [
            ext
            for ext, ins in [
                ("xlsx", "lista_precios"),
                ("xlsx", "sku"),
                ("xlsx", "cantidad"),
                ("xlsx", "porcentaje"),
                ("xlsx", "mecanica"),
            ]
            if ins in insumos
        ]
        if not exts:
            exts = ["xlsx"]

        def pick():
            try:
                rutas = self.app._pick_files(f"Seleccionar archivo(s) — {', '.join(tipos.label)}")
                if rutas:
                    for ruta in rutas:
                        self._agregar_insumo(ruta)
            except Exception as ex:
                from src.ui.mensajes import mensaje

                self.app.show_snackbar(mensaje(ex, "cargar los datos"), self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()

        threading.Thread(target=pick, daemon=True).start()

    def _renderizar_lista_insumos(self):
        """Renderiza la lista de archivos cargados."""
        self.insumo_list.controls.clear()
        if not self._insumo_files:
            requiere_archivo = bool(self.insumo_browse_btn.visible)
            self.insumo_list_wrapper.visible = False
            self.insumo_empty_state.visible = requiere_archivo
            self.insumo_empty_state.controls = []
            if requiere_archivo:
                self.insumo_empty_state.controls.extend(
                    [
                        ft.Icon(
                            ft.Icons.UPLOAD_FILE_OUTLINED,
                            size=17,
                            color=G360Theme.warning_color(),
                        ),
                        ft.Text(
                            "Aún no agregaste los archivos de apoyo.",
                            size=12,
                            color=G360Theme.text_muted_color(),
                        ),
                    ]
                )
            return
        # Mantener el listado acotado y desplazable al añadir previews;
        # el contenedor no existe visualmente mientras no haya archivos.
        self.insumo_empty_state.visible = False
        self.insumo_list_wrapper.visible = True
        self.insumo_list_wrapper.height = 220
        for i, f in enumerate(self._insumo_files):
            self.insumo_list.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.DESCRIPTION_OUTLINED,
                                size=16,
                                color=G360Theme.accent_color(),
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        f["lbl"],
                                        size=12,
                                        weight=ft.FontWeight.W_600,
                                        color=G360Theme.text_primary_color(),
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(f["tipo"], size=10, color=G360Theme.text_muted_color()),
                                ],
                                spacing=1,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=16,
                                height=30,
                                width=30,
                                tooltip="Quitar archivo",
                                on_click=lambda _, r=f["ruta"]: self._quitar_insumo(None, r),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                    bgcolor=G360Theme.surface_variant_color(),
                    border_radius=10,
                )
            )
            # Preview de 5 filas
            if f.get("preview") is not None and f["preview"]["headers"]:
                self.insumo_list.controls.append(
                    self._preview_table_from_data(
                        f["preview"]["headers"], f["preview"]["rows"], f.get("tipo", "")
                    )
                )
            # Iniciar carga de preview en thread (fuera del render)
            elif not f.get("preview_loading") and f["preview"] is None:
                key = f["ruta"]
                if key not in self._preview_threads:
                    self._preview_threads.add(key)
                    threading.Thread(
                        target=self._cargar_preview_insumo, args=(f["ruta"], i, key), daemon=True
                    ).start()

    def _preview_table(self, df, nrows: int = 5) -> ft.DataTable:
        cols_show = [
            c
            for c in ["FECHA", "CLIENTE", "CODIGO", "ARTICULO", "CANTIDAD", "SOLES", "TIPO_DOC"]
            if c in df.columns
        ]
        ncols = [
            ft.DataColumn(
                ft.Text(c, size=10, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE_VARIANT)
            )
            for c in cols_show
        ]
        data_rows = []
        for _, r in df.head(min(nrows, len(df))).iterrows():
            cells = []
            for c in cols_show:
                val = r.get(c)
                if c == "FECHA" and val:
                    try:
                        val = fecha_ui(pd.Timestamp(val)) if not pd.isna(val) else ""
                    except Exception:
                        val = str(val)[:10]
                elif c == "SOLES":
                    val = f"S/ {float(val or 0):,.2f}" if val is not None else ""
                elif c == "CANTIDAD":
                    val = f"{float(val or 0):,.2f}" if val is not None else ""
                else:
                    val = str(val) if val is not None else ""
                cells.append(ft.DataCell(ft.Text(val, size=10)))
            data_rows.append(ft.DataRow(cells=cells))
        return ft.DataTable(
            columns=ncols,
            rows=data_rows,
            heading_row_height=28,
            heading_row_color=ft.Colors.with_opacity(0.15, ft.Colors.WHITE),
            horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
            border_radius=12,
        )

    def _pintar_panel_historial(self):
        """Resume el vínculo con el fragmento buscado, sin repetir su preview."""
        panel = getattr(self, "insumo_hist_panel", None)
        if panel is None:
            return
        panel.controls.clear()
        s = self._hist_fragmento
        df = self.df_historial if s is not None else None
        if not s or df is None or df.empty:
            panel.controls.append(
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.HISTORY_OUTLINED, size=17, color=G360Theme.text_muted_color()
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    "Historial aún no seleccionado",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=G360Theme.text_primary_color(),
                                ),
                                ft.Text(
                                    "Busca y aplica un fragmento en Datos del caso.",
                                    size=10,
                                    color=G360Theme.text_muted_color(),
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                    spacing=9,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
            return
        panel.controls.append(
            ft.Row(
                [
                    ft.Icon(
                        ft.Icons.CHECK_CIRCLE_OUTLINE, size=17, color=G360Theme.success_color()
                    ),
                    ft.Text(
                        "Historial local seleccionado",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.text_primary_color(),
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        panel.controls.append(ft.Row(self._fragmento_badges(s), spacing=6, wrap=True))

    def _construir_seccion_datos(self) -> ft.Column:
        """Controles para acotar el historial que alimenta el caso actual.

        (*) multi-seleccion via chips. El resultado se aplica como insumo HISTORIAL.
        Si hay pedidos/OC seleccionados, trae las facturas nacidas de esos filtros;
        si ademas hay facturas, se queda con la interseccion.
        """
        from datetime import timedelta

        # Fechas con default: últimos 30 días SOLO la primera vez; los
        # re-renders (cambio de caso, aplicar fragmento) preservan lo que
        # el usuario eligió (si no, su rango se pierde a mitad de flujo).
        hoy = datetime.now()
        if not hasattr(self, "busq_fd") or not self.busq_fd:
            self.busq_fd = [hoy - timedelta(days=30)]
        if not hasattr(self, "busq_fh") or not self.busq_fh:
            self.busq_fh = [hoy]
        if not hasattr(self, "_detalle_docs"):
            self._detalle_docs: dict[str, dict] = {}

        def _fmt(d):
            return fecha_ui(d) if d else "todas"

        def _rango_str():
            """Rango activo como 'YYYY-MM-DD' (usado por pickers y búsqueda)."""
            return (
                self.busq_fd[0].strftime("%Y-%m-%d") if self.busq_fd[0] else None,
                self.busq_fh[0].strftime("%Y-%m-%d") if self.busq_fh[0] else None,
            )

        def _invalidar_preview_busqueda():
            """Oculta el resultado anterior cuando cambian sus filtros."""
            self._busq_df = None
            btn = getattr(self, "busq_exec_btn", None)
            if btn is not None:
                btn.visible = False
                btn.disabled = True
            preview = getattr(self, "busq_preview_tbl", None)
            if preview is not None:
                preview.visible = False
            rows = getattr(self, "busq_preview_rows", None)
            if rows is not None:
                rows.controls.clear()
            badges = getattr(self, "busq_badges", None)
            if badges is not None:
                badges.visible = False
                badges.controls.clear()

        # Sin expand: la fila de filtros usa wrap=True y un hijo expandido
        # dentro de un Wrap rompe el render (WrapParentData/FlexParentData).
        self.busq_vend_dd = control_factory.dropdown(
            # Mismo label que el de Reporte de Compras: son el mismo control y
            # se leen como el mismo. El "(opcional: filtra clientes)" no
            # entraba en 260px y hacia que los dos se vieran distintos.
            "Vendedor (opcional)",
            icon=ft.Icons.PERSON_OUTLINED,
            width=control_factory.WIDTH_FILTER,
            search=True,
            hint="Todos los vendedores…",
        )
        # No mostrar un Dropdown gris y vacío durante la carga o cuando
        # el rango no contiene vendedores. El buscador de cliente sigue
        # disponible en ambos casos.
        self.busq_vend_dd.visible = False
        self.busq_vend_dd.disabled = True
        self.busq_vend_status = ft.Text(
            "Cargando vendedores…",
            size=10,
            color=G360Theme.text_muted_color(),
        )
        # Clientes se eligen desde un botón que abre el picker modal; la búsqueda
        # textual se hace en la barra superior del propio modal (no aquí).
        self.busq_buscar_cli_btn = control_factory.search_button(
            "Buscar cliente",
            ft.Icons.SEARCH,
        )
        self.busq_cli_chips = ft.Row(
            [], wrap=True, spacing=6, visible=False, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.busq_ped_chips = ft.Row(
            [], wrap=True, spacing=6, visible=False, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.busq_oc_chips = ft.Row(
            [], wrap=True, spacing=6, visible=False, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.busq_fac_chips = ft.Row(
            [], wrap=True, spacing=6, visible=False, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.busq_fd_label = control_factory.date_label(f"Desde: {_fmt(self.busq_fd[0])}")
        self.busq_fh_label = control_factory.date_label(f"Hasta: {_fmt(self.busq_fh[0])}")
        self.busq_status = ft.Text(
            "Selecciona vendedor", size=12, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.busq_badges = ft.Row([], wrap=True, spacing=6, visible=False)
        self.busq_preview_tbl = ft.Container(
            content=ft.Column([], spacing=0),
            border_radius=12,
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            padding=0,
            height=170,
            visible=False,
        )
        self.busq_preview_rows = self.busq_preview_tbl.content

        # ── chips ──
        def _pintar_chips():
            self.busq_cli_chips.controls = [
                ft.Chip(
                    # Display corto (68414); la clave interna es 8 dígitos.
                    label=ft.Text(f"{nom[:28]} ({cliente_visible(cid)})", size=10),
                    on_delete=lambda _, c=cid: _quitar_cliente(c),
                    delete_icon_color=G360Theme.error_color(),
                    bgcolor=G360Theme.with_opacity(0.1, G360Theme.ACCENT_2),
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                )
                for cid, nom in self._sel_clientes
            ]
            self.busq_cli_chips.visible = bool(self._sel_clientes)
            self.busq_ped_chips.controls = [
                ft.Chip(
                    label=ft.Text(f"Pedido · {pid}", size=10),
                    on_delete=lambda _, p=pid: _quitar_pedido(pid),
                    delete_icon_color=G360Theme.error_color(),
                    bgcolor=G360Theme.with_opacity(0.1, G360Theme.ACCENT_3),
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                )
                for _, pid in self._sel_pedidos
            ]
            self.busq_ped_chips.visible = bool(self._sel_pedidos)
            self.busq_oc_chips.controls = [
                ft.Chip(
                    label=ft.Text(f"O/C · {oid}", size=10),
                    on_delete=lambda _, o=oid: _quitar_orden(oid),
                    delete_icon_color=G360Theme.error_color(),
                    bgcolor=G360Theme.with_opacity(0.1, G360Theme.SUCCESS),
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                )
                for _, oid in self._sel_ordenes
            ]
            self.busq_oc_chips.visible = bool(self._sel_ordenes)
            self.busq_fac_chips.controls = [
                ft.Chip(
                    label=ft.Text(fid, size=10),
                    on_delete=lambda _, f=fid: _quitar_factura(f),
                    delete_icon_color=G360Theme.error_color(),
                    bgcolor=G360Theme.with_opacity(0.1, G360Theme.WARNING),
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                )
                for _, fid in self._sel_facturas
            ]
            self.busq_fac_chips.visible = bool(self._sel_facturas)
            _refresh_doc_buttons()

        def _refresh_doc_buttons():
            """Revela solo los filtros documentales disponibles para el cliente."""
            has_cli = bool(self._sel_clientes)
            pin = getattr(self, "btn_pin_cli", None)
            if pin is not None:
                pin.visible = has_cli
            prompt = getattr(self, "busq_doc_prompt", None)
            row = getattr(self, "busq_doc_row", None)
            if not has_cli:
                if row is not None:
                    row.visible = False
                if prompt is not None:
                    prompt.value = "Selecciona un cliente para filtrar por pedido, O/C o factura."
                    prompt.visible = True
                return

            cid = self._sel_clientes[-1][0]
            detalle = self._detalle_docs.get(cid)
            if detalle is None:
                if row is not None:
                    row.visible = False
                if prompt is not None:
                    prompt.value = "Cargando pedidos, O/C y facturas del cliente…"
                    prompt.visible = True
                return
            if detalle.get("error"):
                if row is not None:
                    row.visible = False
                if prompt is not None:
                    prompt.value = "No se pudieron cargar los filtros documentales."
                    prompt.visible = True
                return

            opciones = (
                ("busq_doc_ped_btn", "Pedidos", detalle.get("pedidos", 0)),
                ("busq_doc_oc_btn", "Órdenes de compra", detalle.get("ordenes", 0)),
                ("busq_doc_fac_btn", "Facturas", detalle.get("facturas", 0)),
            )
            disponibles = 0
            for attr, label, cantidad in opciones:
                btn = getattr(self, attr, None)
                if btn is not None:
                    btn.visible = cantidad > 0
                    btn.disabled = False
                    btn.text = f"{label} · {cantidad}" if cantidad > 0 else label
                    disponibles += cantidad > 0
            if row is not None:
                row.visible = bool(disponibles)
            if prompt is not None:
                prompt.value = (
                    "No hay pedidos, O/C ni facturas disponibles en este rango."
                    if not disponibles
                    else ""
                )
                prompt.visible = not bool(disponibles)

        def _quitar_cliente(cid):
            self._sel_clientes = [c for c in self._sel_clientes if c[0] != cid]
            self._sel_facturas = [f for f in self._sel_facturas if f[0] != cid]
            self._sel_pedidos = [p for p in self._sel_pedidos if p[0] != cid]
            self._sel_ordenes = [o for o in self._sel_ordenes if o[0] != cid]
            self._detalle_docs.pop(cid, None)
            _invalidar_preview_busqueda()
            _pintar_chips()
            if self._sel_clientes:
                threading.Thread(
                    target=lambda: _load_detail_for(self._sel_clientes[-1][0]),
                    daemon=True,
                ).start()
            self.app.page.update()

        def _quitar_factura(fid):
            self._sel_facturas = [f for f in self._sel_facturas if f[1] != fid]
            _invalidar_preview_busqueda()
            _pintar_chips()
            self.app.page.update()

        def _quitar_pedido(pid):
            self._sel_pedidos = [p for p in self._sel_pedidos if p[1] != pid]
            _invalidar_preview_busqueda()
            _pintar_chips()
            self.app.page.update()

        def _quitar_orden(oid):
            self._sel_ordenes = [o for o in self._sel_ordenes if o[1] != oid]
            _invalidar_preview_busqueda()
            _pintar_chips()
            self.app.page.update()

        # ── cascada vendedor → clientes ──
        def _on_vendedor(_):
            vid = self.busq_vend_dd.value
            _logger.debug("busq.vendedor.change vid=%r", vid)
            self._search_vend_id = vid
            vopt = next((o for o in self.busq_vend_dd.options if o.key == vid), None)
            nombre_txt = vopt.text if vopt and vopt.text else ""
            nombre_txt = nombre_txt.removeprefix("Favorito · ").split(" (")[0]
            self._search_vend_nom = nombre_txt if nombre_txt else (vid or "")
            self._sel_clientes = []
            self._sel_facturas = []
            self._sel_pedidos = []
            self._sel_ordenes = []
            self._detalle_docs.clear()
            _invalidar_preview_busqueda()
            _pintar_chips()
            self.app.page.update()
            # El picker modal usa _search_vend_id al abrirse, no hace falta
            # poblar un dropdown oculto aquí.

        self.busq_vend_dd.on_change = _on_vendedor

        # Cargar clientes al iniciar (para interacción inmediata)

        # ── Recarga compartida de clientes (usa fechas + vendedor + busqueda ACTUALES) ──
        def _merge_pinned_clientes(cs: list, pinned_ids: set[str]):
            """Anexa los clientes anclados que no pasan los filtros actuales.

            Sin esto, un anclado sin ventas en el rango/vendedor activo
            desaparece de la lista y parece que el anclaje "no persiste".
            """
            have = {c["id"] for c in cs}
            for pid in pinned_ids:
                if pid in have:
                    continue
                try:
                    pc = self._cli.fetch_client_by_id(pid)
                except Exception:
                    pc = None
                if pc:
                    cs.append(pc)

        def _refrescar_clientes_dd(search: str = ""):
            try:
                fd_str, fh_str = _rango_str()
                q = (search or "").strip()
                _logger.info(
                    "busq.clientes.refresh q=%r vend=%r fd=%r fh=%r",
                    q,
                    self._search_vend_id,
                    fd_str,
                    fh_str,
                )
                # Sin busqueda: top por actividad (render ligero). Con busqueda:
                # LIKE en DB, pueden ser muchos -> limite mayor.
                cs = self._cli.fetch_clientes(
                    vendedor_id=self._search_vend_id or None,
                    fecha_desde=fd_str,
                    fecha_hasta=fh_str,
                    limit=400 if q else 150,
                    search=q or None,
                )
                pinned = set(self._pinned_clientes)
                if pinned:
                    _merge_pinned_clientes(cs, pinned)
                cs.sort(key=lambda c: (c["id"] not in pinned, -c.get("docs", 0)))
                _logger.debug("busq.clientes.result n=%d", len(cs))
                if cs:
                    self.busq_status.value = (
                        f"{len(cs)} clientes con ventas en el rango — agrega uno o varios"
                    )
                    self.busq_status.color = self.app.G360_SUCCESS
                else:
                    self.busq_status.value = (
                        "Sin clientes con facturas/boletas en este rango de fechas"
                    )
                    self.busq_status.color = G360Theme.warning_color()
                # Updates DIRIGIDOS: page.update() global re-renderiza toda la
                # card en cada tecla y se siente lento.
                try:
                    if getattr(self.busq_status, "page", None) is not None:
                        self.busq_status.update()
                except Exception:
                    pass
            except Exception as ex:
                _logger.exception("busq.clientes.refresh_failed")
                self.busq_status.value = f"Error al cargar clientes: {ex}"
                self.busq_status.color = self.app.G360_ERROR
                try:
                    if getattr(self.busq_status, "page", None) is not None:
                        self.busq_status.update()
                except Exception:
                    pass

        # Cargar clientes al iniciar (para interacción inmediata)
        threading.Thread(target=_refrescar_clientes_dd, daemon=True).start()

        # ── Picker modal de clientes (paginado + multiselección) ──
        # Busca en el campo principal → abre modal con resultados ya filtrados.
        # Sin campo de búsqueda interno: el modal solo selecciona.
        def _abrir_picker_clientes(e=None):
            """Modal de clientes (widget compartido, multi-selección).

            Adopta el mismo control que Reportes de compras para mantener
            simetría entre secciones.
            """
            from src.ui.widgets.cliente_picker import abrir_picker_clientes

            def _on_confirm(elegidos):
                added = []
                for c in elegidos:
                    cid = c["id"]
                    if any(x[0] == cid for x in self._sel_clientes):
                        continue
                    self._sel_clientes.append((cid, c["nombre"]))
                    ventas_db.push_recent("clientes", cid)
                    added.append(cid)
                if not added:
                    self.app.show_snackbar(
                        "Selecciona al menos un cliente nuevo", self.app.G360_WARNING
                    )
                    return
                self._recent_clientes = ventas_db.load_recent("clientes")
                _invalidar_preview_busqueda()
                _pintar_chips()
                threading.Thread(target=_refrescar_clientes_dd, daemon=True).start()
                last_cid = self._sel_clientes[-1][0]
                threading.Thread(target=lambda: _load_detail_for(last_cid), daemon=True).start()

            abrir_picker_clientes(
                self.app,
                on_confirm=_on_confirm,
                multiple=True,
                initial={cid for cid, _ in self._sel_clientes},
                vendedor_id=self._search_vend_id,
                fecha_desde=(self.busq_fd[0].strftime("%Y-%m-%d") if self.busq_fd[0] else None),
                fecha_hasta=(self.busq_fh[0].strftime("%Y-%m-%d") if self.busq_fh[0] else None),
                pinned=self._pinned_clientes,
                recent=self._recent_clientes,
            )

        # ── Modals reutilizables para OC / Pedido / Factura ──────────────
        def _abrir_picker_items(
            title: str,
            icon: str,
            fetch_fn,
            item_label_fn,
            sel_list_attr: str,
            chip_row_attr: str,
            quitar_fn,
            btn_color=None,
        ):
            """Modal de selección para OC/pedido/factura — sin búsqueda interna.
            Los items ya vienen filtrados por el cliente seleccionado."""
            page = self.app.page
            if page is None:
                return

            results_col = ft.Column([], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
            _checks: dict[str, ft.Checkbox] = {}
            _item_info: dict[str, str] = {}
            _all_items: list = []
            _state = {"q": "", "sel": set()}
            _deb = {"n": 0}
            picker_status = ft.Text("Cargando…", size=10, color=ft.Colors.ON_SURFACE_VARIANT)

            def _refresh_modal():
                # Update dirigido: no re-renderiza el TextField (evita tartamudeo/pérdida de foco).
                try:
                    results_col.update()
                    picker_status.update()
                except Exception:
                    pass

            def _on_check(iid_, val):
                if val:
                    _state["sel"].add(iid_)
                else:
                    _state["sel"].discard(iid_)

            def _render_rows(items):
                results_col.controls.clear()
                _checks.clear()
                for item in items:
                    iid = item["id"]
                    lbl = item_label_fn(item)
                    _item_info[iid] = lbl
                    cb = ft.Checkbox(
                        value=(iid in _state["sel"]),
                        on_change=lambda e, i=iid: _on_check(i, e.control.value),
                    )
                    _checks[iid] = cb
                    results_col.controls.append(
                        ft.Container(
                            content=ft.Row(
                                [
                                    cb,
                                    ft.Text(lbl, size=12, expand=True, color=ft.Colors.ON_SURFACE),
                                ],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=ft.padding.symmetric(vertical=2),
                            border=ft.border.only(
                                bottom=ft.BorderSide(1, G360Theme.border_subtle_color())
                            ),
                        )
                    )

            def _toggle_all(val):
                for iid, cb in _checks.items():
                    cb.value = val
                    if val:
                        _state["sel"].add(iid)
                    else:
                        _state["sel"].discard(iid)
                _refresh_modal()

            def _add_selected(_):
                checked = [iid for iid in _state["sel"] if iid in _item_info]
                _logger.debug("picker_items.add_selected title=%r n=%d", title, len(checked))
                added = []
                for iid in checked:
                    sel = getattr(self, sel_list_attr)
                    if any(x[1] == iid for x in sel):
                        continue
                    sel.append((self._sel_clientes[-1][0] if self._sel_clientes else "", iid))
                    getattr(self, chip_row_attr).controls.append(
                        ft.Chip(
                            label=ft.Text(item_label_fn({"id": iid}), size=10),
                            on_delete=lambda _, i=iid: quitar_fn(i),
                            delete_icon_color=G360Theme.error_color(),
                            bgcolor=G360Theme.with_opacity(0.1, G360Theme.ACCENT_2),
                            padding=ft.padding.symmetric(horizontal=8, vertical=2),
                        )
                    )
                    added.append(iid)
                if not added:
                    self.app.show_snackbar(
                        f"Selecciona al menos un {title.lower()} nuevo", self.app.G360_WARNING
                    )
                    return
                _invalidar_preview_busqueda()
                _pintar_chips()
                page.close(dlg)
                if self.app.page:
                    self.app.page.update()

            # Cargar todos los items del cliente (sin paginación, son pocos)
            def _load_all():
                _logger.debug("picker_items.load_start title=%r", title)
                try:
                    items = fetch_fn(None, 999, 0)
                    _all_items.clear()
                    _all_items.extend(items)
                    _apply_filter("")
                    _logger.info(
                        "picker_items.loaded title=%r rows=%d",
                        title,
                        len(items),
                    )
                except Exception:
                    _logger.exception("picker_items.load_failed title=%r", title)
                    picker_status.value = "Error al cargar"
                finally:
                    _refresh_modal()

            def _apply_filter(qq: str):
                qq = (qq or "").strip().lower()
                _state["q"] = qq
                if qq:
                    shown = [it for it in _all_items if qq in str(it.get("id", "")).lower()]
                else:
                    shown = list(_all_items)
                _render_rows(shown)
                picker_status.value = f"{len(shown)} de {len(_all_items)} {title.lower()}(es)" + (
                    f" · filtrando: '{qq}'" if qq else ""
                )
                _logger.debug("picker_items.filter title=%r q=%r n=%d", title, qq, len(shown))
                _refresh_modal()

            def _on_modal_search(e):
                qq = (e.control.value or "").strip()
                _deb["n"] += 1
                my = _deb["n"]

                def w():
                    if my != _deb["n"]:
                        return
                    _apply_filter(qq)

                threading.Timer(0.15, w).start()

            picker_search = ft.TextField(
                label=f"Buscar {title}…",
                dense=True,
                text_size=13,
                border_radius=12,
                prefix_icon=ft.Icons.SEARCH_OUTLINED,
                expand=True,
                content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
                on_change=_on_modal_search,
                on_submit=lambda e: _apply_filter(e.control.value or ""),
                autofocus=True,
            )

            dlg = ft.AlertDialog(
                title=ft.Row(
                    [
                        ft.Icon(icon, color=self.app.G360_ACCENT),
                        ft.Text(f"Seleccionar {title}", size=14, weight=ft.FontWeight.W_700),
                    ],
                    spacing=8,
                ),
                content=ft.Container(
                    content=ft.Column(
                        [
                            picker_search,
                            results_col,
                            picker_status,
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    width=520,
                    height=400,
                    border=ft.border.all(1, G360Theme.border_subtle_color()),
                    border_radius=12,
                    padding=ft.padding.only(left=8, right=8, top=2, bottom=8),
                ),
                actions=[
                    ft.TextButton("Marcar todo", on_click=lambda _: _toggle_all(True)),
                    ft.TextButton("Desmarcar", on_click=lambda _: _toggle_all(False)),
                    ft.ElevatedButton(
                        "Agregar seleccionados",
                        icon=ft.Icons.ADD,
                        on_click=_add_selected,
                        style=ft.ButtonStyle(bgcolor=btn_color or self.app.G360_ACCENT),
                    ),
                    ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            _logger.debug("picker_items.open title=%r", title)
            try:
                _load_all()
            except Exception:
                _logger.exception("picker_items.prefill_failed title=%r", title)
            page.open(dlg)
            _logger.debug("picker_items.dialog_opened title=%r", title)

        def _abrir_picker_ordenes(_):
            _logger.info("picker_items.button ordenes sel_clientes=%d", len(self._sel_clientes))
            if not self._sel_clientes:
                self.app.show_snackbar("Selecciona un cliente primero", self.app.G360_WARNING)
                return
            cid = self._sel_clientes[-1][0]
            fd_str, fh_str = _rango_str()
            _logger.info(
                "picker_items.fetch ordenes cid=%r fd=%r fh=%r",
                cid,
                fd_str,
                fh_str,
            )
            _abrir_picker_items(
                title="Orden de Compra",
                icon=ft.Icons.DESCRIPTION_OUTLINED,
                fetch_fn=lambda q, sz, off: [
                    {**o, "id": o["id"]}
                    for o in self._cli.fetch_ordenes_cliente(
                        cid, fecha_desde=fd_str, fecha_hasta=fh_str
                    )
                    if not q or q.lower() in o["id"].lower()
                ][off : off + sz],
                item_label_fn=lambda o: (
                    f"{o['id']} · {o.get('fecha', '')} · {o.get('n_facturas', 0)} fac."
                ),
                sel_list_attr="_sel_ordenes",
                chip_row_attr="busq_oc_chips",
                quitar_fn=_quitar_orden,
                btn_color=G360Theme.with_opacity(0.1, G360Theme.SUCCESS),
            )

        def _abrir_picker_pedidos(_):
            _logger.info("picker_items.button pedidos sel_clientes=%d", len(self._sel_clientes))
            if not self._sel_clientes:
                self.app.show_snackbar("Selecciona un cliente primero", self.app.G360_WARNING)
                return
            cid = self._sel_clientes[-1][0]
            fd_str, fh_str = _rango_str()
            _logger.info(
                "picker_items.fetch pedidos cid=%r fd=%r fh=%r",
                cid,
                fd_str,
                fh_str,
            )
            _abrir_picker_items(
                title="Pedido",
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                fetch_fn=lambda q, sz, off: [
                    {**p, "id": p["id"]}
                    for p in self._cli.fetch_pedidos_cliente(
                        cid, fecha_desde=fd_str, fecha_hasta=fh_str
                    )
                    if not q or q.lower() in p["id"].lower()
                ][off : off + sz],
                item_label_fn=lambda p: (
                    f"{p['id']} · {p.get('fecha', '')} · {p.get('n_facturas', 0)} fac."
                ),
                sel_list_attr="_sel_pedidos",
                chip_row_attr="busq_ped_chips",
                quitar_fn=_quitar_pedido,
                btn_color=G360Theme.with_opacity(0.1, G360Theme.ACCENT_3),
            )

        def _abrir_picker_facturas(_):
            _logger.info("picker_items.button facturas sel_clientes=%d", len(self._sel_clientes))
            if not self._sel_clientes:
                self.app.show_snackbar("Selecciona un cliente primero", self.app.G360_WARNING)
                return
            cid = self._sel_clientes[-1][0]
            fd_str, fh_str = _rango_str()
            _logger.info(
                "picker_items.fetch facturas cid=%r fd=%r fh=%r",
                cid,
                fd_str,
                fh_str,
            )
            _abrir_picker_items(
                title="Factura",
                icon=ft.Icons.RECEIPT_OUTLINED,
                fetch_fn=lambda q, sz, off: [
                    {**f, "id": f["id"]}
                    for f in self._cli.fetch_facturas_cliente(
                        cid, fecha_desde=fd_str, fecha_hasta=fh_str
                    )
                    if not q or q.lower() in f["id"].lower()
                ][off : off + sz],
                item_label_fn=lambda f: f"{f['id']} · {f.get('fecha', '')}",
                sel_list_attr="_sel_facturas",
                chip_row_attr="busq_fac_chips",
                quitar_fn=_quitar_factura,
                btn_color=G360Theme.with_opacity(0.1, G360Theme.WARNING),
            )

        def _load_detail_for(cid: str):
            """Muestra estadísticas del cliente seleccionado (respetando fechas)."""
            self._detalle_docs.pop(cid, None)
            _refresh_doc_buttons()
            try:
                fd_str = self.busq_fd[0].strftime("%Y-%m-%d") if self.busq_fd[0] else None
                fh_str = self.busq_fh[0].strftime("%Y-%m-%d") if self.busq_fh[0] else None
                os_ = self._cli.fetch_ordenes_cliente(cid, fecha_desde=fd_str, fecha_hasta=fh_str)
                ps = self._cli.fetch_pedidos_cliente(cid, fecha_desde=fd_str, fecha_hasta=fh_str)
                fs = self._cli.fetch_facturas_cliente(cid, fecha_desde=fd_str, fecha_hasta=fh_str)
                self._detalle_docs[cid] = {
                    "ordenes": len(os_),
                    "pedidos": len(ps),
                    "facturas": len(fs),
                }
                _logger.info(
                    "busq.detail cid=%r fd=%r fh=%r oc=%d ped=%d fac=%d",
                    cid,
                    fd_str,
                    fh_str,
                    len(os_),
                    len(ps),
                    len(fs),
                )
            except Exception:
                _logger.exception("busq.detail.load_failed cid=%r", cid)
                self._detalle_docs[cid] = {"error": True}
            finally:
                _refresh_doc_buttons()
                try:
                    if self.app and self.app.page:
                        self.app.page.update()
                except Exception:
                    pass

        # Wire: botón "Buscar cliente" abre el picker (búsqueda dentro del modal)
        self.busq_buscar_cli_btn.on_click = _abrir_picker_clientes

        def _make_date_picker(storage, label_ctl, side):
            def open(_):
                def on_change(ev):
                    storage[0] = ev.control.value
                    fmt = "%d/%m/%Y"
                    label_ctl.value = (
                        f"{side}: {ev.control.value.strftime(fmt)}"
                        if ev.control.value
                        else f"{side}: todas"
                    )
                    label_ctl.color = (
                        self.app.G360_ACCENT if ev.control.value else ft.Colors.ON_SURFACE_VARIANT
                    )
                    # Los resultados/documentos del rango anterior dejan
                    # de corresponder; evita que sigan pareciendo aplicables.
                    _invalidar_preview_busqueda()
                    self._sel_pedidos = []
                    self._sel_ordenes = []
                    self._sel_facturas = []
                    self._detalle_docs.clear()
                    _pintar_chips()
                    if self._sel_clientes:
                        cid = self._sel_clientes[-1][0]
                        threading.Thread(
                            target=lambda: _load_detail_for(cid),
                            daemon=True,
                        ).start()
                    self.app.page.update()
                    threading.Thread(target=_refrescar_clientes_dd, daemon=True).start()

                picker = ft.DatePicker(
                    first_date=datetime(2021, 1, 1), last_date=datetime.now(), on_change=on_change
                )
                self.app.page.open(picker)

            return open

        def _pintar_resultado_busqueda(df, estado: str | None = None):
            """Pinta status + badges + preview y habilita 'USAR COMO HISTORIAL'.

            Compartido por la búsqueda (worker) y la reconstrucción de la
            card tras un re-render (mismo estado visual en ambos).
            """
            s = self._fragmento_stats(df)
            ped_txt = f"{s['pedidos']} pedidos · " if s.get("pedidos") else ""
            oc_txt = f"{s['ordenes']} O/C · " if s.get("ordenes") else ""
            self.busq_status.value = estado or (
                f"✓ {len(df):,} filas · {s['clientes']} clientes · "
                f"{ped_txt}{oc_txt}{s['facturas']} facturas · {s['skus']} SKUs"
            )
            self.busq_status.color = self.app.G360_SUCCESS
            self.busq_badges.controls = self._fragmento_badges(s)
            self.busq_badges.visible = True
            self.busq_preview_rows.controls.clear()
            self.busq_preview_rows.controls.append(self._preview_table(df, nrows=5))
            self.busq_preview_tbl.visible = True
            self.busq_exec_btn.disabled = False
            self.busq_exec_btn.visible = True

        # ── Buscar: concatena fragmentos por cliente/factura ──
        def _busq_executar(_):
            if not self._sel_clientes:
                self.app.show_snackbar("Agrega al menos un cliente", self.app.G360_ERROR)
                return
            fd, fh = _rango_str()
            clientes = list(self._sel_clientes)
            facturas = list(self._sel_facturas)
            pedidos = list(self._sel_pedidos)
            ordenes = list(self._sel_ordenes)
            _invalidar_preview_busqueda()
            self.busq_status.value = "Buscando…"
            self.busq_status.color = G360Theme.WARNING
            self.app.page.update()

            def worker():
                try:
                    dfs = []
                    # Agrupar filtros por cliente y lanzar una consulta por cliente
                    ocl_por_cli: dict[str, list] = {}
                    ped_por_cli: dict[str, list] = {}
                    fac_por_cli: dict[str, set] = {}
                    for cli, oid in ordenes:
                        ocl_por_cli.setdefault(cli, []).append(oid)
                    for cli, pid in pedidos:
                        ped_por_cli.setdefault(cli, []).append(pid)
                    if facturas:
                        for cli, fid in facturas:
                            fac_por_cli.setdefault(cli, set()).add(fid)

                    clients_with_filters = set(ocl_por_cli) | set(ped_por_cli)
                    if clients_with_filters:
                        for cli in clients_with_filters:
                            d = self._cli.fetch_historial(
                                id_cliente=cli,
                                id_pedidos=ped_por_cli.get(cli) or None,
                                ordenes=ocl_por_cli.get(cli) or None,
                                fecha_desde=fd,
                                fecha_hasta=fh,
                            )
                            if d is not None and not d.empty and cli in fac_por_cli:
                                # El id de factura es TIPO-SERIE-NRO
                                # ('F01-204-67375'); DOC_ID del historial
                                # es 'TIPO+SERIE-NRO' ('F204-67375'):
                                # sin la variante la interseccion no
                                # calza y el filtro queda vacio.
                                refs = set(fac_por_cli[cli])
                                for fid in fac_por_cli[cli]:
                                    parts = fid.split("-", 2)
                                    if len(parts) >= 3:
                                        refs.add(f"{parts[0][:1]}{parts[1]}-{parts[2]}")
                                d = d[d["DOC_ID"].isin(refs)]
                            dfs.append(d)
                    elif facturas:
                        for cli, fid in facturas:
                            parts = fid.split("-", 2)
                            serie = parts[1] if len(parts) >= 3 else None
                            nro = parts[2] if len(parts) >= 3 else None
                            dfs.append(
                                self._cli.fetch_historial(
                                    id_cliente=cli, serie_doc=serie, nro_doc=nro
                                )
                            )
                    else:
                        for cli, _ in clientes:
                            dfs.append(
                                self._cli.fetch_historial(
                                    id_cliente=cli, fecha_desde=fd, fecha_hasta=fh
                                )
                            )
                    df_no_vacios = [d for d in dfs if d is not None and not d.empty]
                    df = (
                        pd.concat(df_no_vacios, ignore_index=True)
                        if df_no_vacios
                        else pd.DataFrame()
                    )
                    if not df.empty and "FOLIO_UNICO" in df.columns:
                        # Un doc puede tocar varios pedidos seleccionados — dedup por linea
                        fu = df["FOLIO_UNICO"].fillna("").astype(str).str.strip()
                        if (fu != "").any():
                            df = pd.concat(
                                [
                                    df[fu == ""],
                                    df[fu != ""].drop_duplicates(subset=["FOLIO_UNICO", "CODIGO"]),
                                ],
                                ignore_index=True,
                            )
                    self._busq_df = df if not df.empty else None
                    if df.empty:
                        self.busq_status.value = "Sin resultados"
                        self.busq_status.color = self.app.G360_ERROR
                        self.busq_exec_btn.disabled = True
                        self.busq_exec_btn.visible = False
                    else:
                        _pintar_resultado_busqueda(df)
                except Exception as ex:
                    self.busq_status.value = f"Error: {ex}"
                    self.busq_status.color = self.app.G360_ERROR
                    self.busq_exec_btn.disabled = True
                    self.busq_exec_btn.visible = False
                finally:
                    self.app.page.update()

            threading.Thread(target=worker, daemon=True).start()

        self._busq_ejecutar = _busq_executar

        # Reconstruir estado si se re-renderiza la card (tras aplicar fragmento)
        if self._sel_clientes or self._sel_facturas or self._sel_pedidos or self._sel_ordenes:
            _pintar_chips()

        # Build buttons (must be after method definitions)
        self.busq_btn = G360Theme.ghost_button(
            "Buscar",
            icon=ft.Icons.SEARCH_OUTLINED,
            on_click=self._busq_ejecutar,
        )
        self.busq_exec_btn = G360Theme.accent_button(
            "USAR COMO HISTORIAL",
            icon=ft.Icons.HISTORY,
            on_click=self._aplicar_fragmento,
            disabled=self._busq_df is None or (self._busq_df is not None and self._busq_df.empty),
            width=None,
        )
        self.busq_exec_btn.visible = False
        if self._busq_df is not None and not self._busq_df.empty:
            _pintar_resultado_busqueda(
                self._busq_df,
                estado=(
                    f"✓ Fragmento listo: {len(self._busq_df):,} filas — "
                    + (
                        "aplicado como historial"
                        if self._hist_fragmento
                        else "usa el botón para aplicarlo"
                    )
                ),
            )

        # Cargar vendedores con actividad real (>100 ventas ≈ vendedores de campo)
        def _merge_pinned_vendedores(vends: list, pinned_ids: set[str]):
            """Anexa los vendedores anclados que no superan el umbral de ventas."""
            have = {v["id"] for v in vends}
            for pid in pinned_ids:
                if pid in have:
                    continue
                try:
                    pv = self._cli.fetch_vendedor_by_id(pid)
                except Exception:
                    pv = None
                if pv:
                    vends.append(pv)

        def _cargar_vendedores_dd():
            try:
                vends = self._cli.fetch_vendedores(min_docs=100)
                self._all_vendedores = vends  # cache para el buscador (son pocos)
                pinned_v = set(self._pinned_vendedores)
                if pinned_v:
                    _merge_pinned_vendedores(vends, pinned_v)
                vends.sort(key=lambda v: v["id"] not in pinned_v)
                self.busq_vend_dd.options = [
                    ft.dropdown.Option(
                        key=v["id"],
                        text=("Favorito · " if v["id"] in pinned_v else "")
                        + f"{v['nombre']} ({v.get('codigo', v['id'])})",
                    )
                    for v in vends
                ]
                self.busq_vend_dd.visible = bool(vends)
                self.busq_vend_dd.disabled = not bool(vends)
                btn_pin_vend.visible = bool(vends)
                self.busq_vend_status.visible = not bool(vends)
                if vends:
                    self.busq_vend_status.value = ""
                    if not self._hist_fragmento:
                        self.busq_status.value = f"{len(vends)} vendedores disponibles; puedes filtrar por uno o buscar cliente"
                else:
                    self.busq_vend_status.value = (
                        "Sin vendedores disponibles. Busca el cliente directamente."
                    )
                    self.busq_vend_status.color = G360Theme.text_muted_color()
                if self.app.page:
                    self.app.page.update()
                _refrescar_icono_pin_vend()
            except Exception:
                _logger.exception("busq.vendedores.load_failed")
                self.busq_vend_dd.options = []
                self.busq_vend_dd.visible = False
                self.busq_vend_dd.disabled = True
                btn_pin_vend.visible = False
                self.busq_vend_status.value = (
                    "No se pudieron cargar vendedores; busca el cliente directamente."
                )
                self.busq_vend_status.visible = True
                try:
                    if getattr(self.busq_vend_status, "page", None) is not None:
                        self.busq_vend_status.update()
                except Exception:
                    pass

        # Build card — fechas arriba, luego filtros cascada
        def _toggle_pin(kind: str, value: str | None):
            """Ancla/quita el valor actual y recarga el dropdown afectado."""
            if not value:
                self.app.show_snackbar(
                    "Selecciona primero un cliente o vendedor", self.app.G360_WARNING
                )
                return
            anclado = ventas_db.toggle_pinned(kind, value)
            if kind == "clientes":
                self._pinned_clientes = ventas_db.load_pinned()["clientes"]
                threading.Thread(target=_refrescar_clientes_dd, daemon=True).start()
            else:
                self._pinned_vendedores = ventas_db.load_pinned()["vendedores"]
                _refrescar_icono_pin_vend()
                threading.Thread(target=_cargar_vendedores_dd, daemon=True).start()
            self.app.show_snackbar(
                ("Anclado: " if anclado else "Quitado de anclados: ") + value,
                self.app.G360_SUCCESS if anclado else ft.Colors.ON_SURFACE_VARIANT,
            )

        btn_pin_vend = ft.IconButton(
            icon=ft.Icons.PUSH_PIN_OUTLINED,
            icon_size=18,
            tooltip="Anclar vendedor (siempre visible primero)",
            visible=False,
            on_click=lambda _: _toggle_pin("vendedores", self.busq_vend_dd.value),
        )
        self.btn_pin_vend = btn_pin_vend

        def _refrescar_icono_pin_vend():
            """Pin relleno si el vendedor seleccionado está anclado."""
            on = self.busq_vend_dd.value in set(self._pinned_vendedores)
            btn_pin_vend.icon = ft.Icons.PUSH_PIN if on else ft.Icons.PUSH_PIN_OUTLINED
            btn_pin_vend.tooltip = (
                "Quitar anclaje del vendedor" if on else "Anclar vendedor (siempre visible primero)"
            )
            try:
                if getattr(btn_pin_vend, "page", None) is not None:
                    btn_pin_vend.update()
            except Exception:
                pass

        _orig_vend_on_change = self.busq_vend_dd.on_change

        def _vend_on_change_wrap(e):
            _orig_vend_on_change(e)
            _refrescar_icono_pin_vend()

        self.busq_vend_dd.on_change = _vend_on_change_wrap
        _refrescar_icono_pin_vend()
        threading.Thread(target=_cargar_vendedores_dd, daemon=True).start()

        def _pin_clientes(_):
            cids = [cid for cid, _ in self._sel_clientes]
            if not cids:
                self.app.show_snackbar(
                    "Selecciona primero un cliente con 'Buscar cliente'", self.app.G360_WARNING
                )
                return
            state = ventas_db.load_pinned()
            pinned = set(state["clientes"])
            for cid in cids:
                pinned.add(cid) if cid not in pinned else pinned.remove(cid)
            ventas_db.save_pinned(list(pinned), state["vendedores"])
            self._pinned_clientes = list(pinned)
            self.app.show_snackbar(
                f"{'Anclados' if any(c in pinned for c in cids) else 'Quitados'}: {', '.join(cids[:3])}",
                self.app.G360_SUCCESS
                if any(c in pinned for c in cids)
                else ft.Colors.ON_SURFACE_VARIANT,
            )
            _pintar_chips()
            threading.Thread(target=_refrescar_clientes_dd, daemon=True).start()

        btn_pin_cli = ft.IconButton(
            icon=ft.Icons.PUSH_PIN_OUTLINED,
            icon_size=18,
            tooltip="Anclar clientes seleccionados (siempre visibles primero)",
            visible=False,
            on_click=_pin_clientes,
        )
        self.btn_pin_cli = btn_pin_cli
        # Los filtros documentales no se muestran hasta que el cliente
        # seleccionado tenga opciones disponibles en el rango.
        self.busq_doc_ped_btn = ft.ElevatedButton(
            "Pedidos",
            icon=ft.Icons.INVENTORY_2_OUTLINED,
            height=32,
            visible=False,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=10),
                bgcolor=G360Theme.with_opacity(0.12, G360Theme.ACCENT_3),
            ),
            disabled=not bool(self._sel_clientes),
            on_click=_abrir_picker_pedidos,
        )
        self.busq_doc_oc_btn = ft.ElevatedButton(
            "Órdenes de compra",
            icon=ft.Icons.DESCRIPTION_OUTLINED,
            height=32,
            visible=False,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=10),
                bgcolor=G360Theme.with_opacity(0.12, G360Theme.SUCCESS),
            ),
            disabled=not bool(self._sel_clientes),
            on_click=_abrir_picker_ordenes,
        )
        self.busq_doc_fac_btn = ft.ElevatedButton(
            "Facturas",
            icon=ft.Icons.RECEIPT_OUTLINED,
            height=32,
            visible=False,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=10),
                bgcolor=G360Theme.with_opacity(0.12, G360Theme.WARNING),
            ),
            disabled=not bool(self._sel_clientes),
            on_click=_abrir_picker_facturas,
        )
        self.busq_doc_row = ft.Row(
            [
                self.busq_doc_ped_btn,
                self.busq_doc_oc_btn,
                self.busq_doc_fac_btn,
            ],
            spacing=8,
            wrap=True,
            visible=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.busq_doc_prompt = ft.Text(
            "Selecciona un cliente para ver sus pedidos, O/C y facturas.",
            size=10,
            color=G360Theme.text_muted_color(),
        )
        _refresh_doc_buttons()
        if self._sel_clientes and self._sel_clientes[-1][0] not in self._detalle_docs:
            threading.Thread(
                target=lambda: _load_detail_for(self._sel_clientes[-1][0]),
                daemon=True,
            ).start()
        return ft.Column(
            [
                # El rango estrecha el universo desde el comienzo.
                ft.Row(
                    [
                        self.busq_fd_label,
                        control_factory.date_button(
                            _make_date_picker(self.busq_fd, self.busq_fd_label, "Desde"), "Desde"
                        ),
                        self.busq_fh_label,
                        control_factory.date_button(
                            _make_date_picker(self.busq_fh, self.busq_fh_label, "Hasta"), "Hasta"
                        ),
                        ft.Text("Últimos 30 días", size=10, color=G360Theme.text_muted_color()),
                    ],
                    spacing=8,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        self.busq_vend_dd,
                        btn_pin_vend,
                        self.busq_buscar_cli_btn,
                        btn_pin_cli,
                    ],
                    spacing=6,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.busq_vend_status,
                self.busq_cli_chips,
                ft.Divider(height=1, color=G360Theme.border_subtle_color()),
                self.busq_doc_prompt,
                self.busq_doc_row,
                self.busq_ped_chips,
                self.busq_oc_chips,
                self.busq_fac_chips,
                ft.Row(
                    [self.busq_btn, self.busq_exec_btn],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.END,
                    wrap=True,
                ),
                self.busq_status,
                self.busq_badges,
                self.busq_preview_tbl,
            ],
            spacing=8,
            tight=True,
        )

    def _modal_login_intranet(self, page, on_success, mensaje_previo: str = ""):
        """Modal de login nativo: usuario + contraseña, spinner al verificar.

        on_success(user, password) se llama si la verificacion es exitosa.
        Si falla, muestra error y permite reintentar sin cerrar el modal.
        """
        import flet as ft
        from src.core.capture_service import CaptureService
        from src.core.intranet_client import IntranetClient

        has_creds = CaptureService.has_credentials()

        user_input = ft.TextField(
            label="Usuario intranet",
            width=280,
            dense=True,
            text_size=13,
            border_radius=12,
            prefix_icon=ft.Icons.PERSON_OUTLINED,
        )
        pass_input = ft.TextField(
            label="Contraseña",
            width=280,
            dense=True,
            text_size=13,
            border_radius=12,
            prefix_icon=ft.Icons.LOCK_OUTLINED,
            password=True,
            can_reveal_password=True,
        )
        if has_creds:
            u_saved, _ = CaptureService.credentials()
            user_input.value = u_saved

        login_status = ft.Text(mensaje_previo, size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        spinner = ft.ProgressRing(width=20, height=20, stroke_width=2, visible=False)

        btn_verificar = ft.ElevatedButton(
            "Verificar",
            height=38,
            width=280,
            style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
        )

        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.VPN_KEY_OUTLINED, color=self.app.G360_ACCENT),
                    ft.Text("Acceder a intranet", size=13, weight=ft.FontWeight.W_700),
                ],
                spacing=6,
            ),
            content=ft.Column(
                [
                    user_input,
                    pass_input,
                    ft.Row(
                        [spinner, login_status],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            actions=[
                btn_verificar,
                ft.TextButton("Cancelar", on_click=lambda _: page.close(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.open(dlg)

        def on_verify(_):
            u = (user_input.value or "").strip()
            p = (pass_input.value or "").strip()
            if not u or not p:
                login_status.value = "✗ Ingresa usuario y contraseña"
                login_status.color = self.app.G360_ERROR
                page.update()
                return

            # Desactivar controles mientras verifica
            user_input.disabled = True
            pass_input.disabled = True
            btn_verificar.disabled = True
            spinner.visible = True
            login_status.value = "Verificando credenciales..."
            login_status.color = self.app.G360_ACCENT
            page.update()

            def worker():
                try:
                    cli = IntranetClient(u, p, timeout=90.0)
                    try:
                        ok, msg = cli.verify_credentials()
                    finally:
                        cli.close()
                    if not ok:
                        login_status.value = f"✗ Acceso denegado — {msg}"
                        login_status.color = self.app.G360_ERROR
                    else:
                        CaptureService.save_credentials(u, p)
                        api_suffix = CaptureService.refresh_api_token_best_effort(u, p)
                        login_status.value = f"✓ {msg}{api_suffix}"
                        login_status.color = self.app.G360_SUCCESS
                        # Cerrar modal y continuar
                        page.close(dlg)
                        on_success(u, p)
                except Exception as ex:
                    login_status.value = f"✗ Error: {ex}"
                    login_status.color = self.app.G360_ERROR
                finally:
                    user_input.disabled = False
                    pass_input.disabled = False
                    btn_verificar.disabled = False
                    spinner.visible = False
                    page.update()

            threading.Thread(target=worker, daemon=True).start()

        btn_verificar.on_click = on_verify

    def _actualizar_hoy(self, e):
        """Un clic: abre el modal unificado de sincronización desde la API Go.

        El modal orquesta login intranet → check de frescura → sync incremental.
        Mantiene fallback a XLS/intranet disponible en Configuración → Fuentes.
        """
        page = self.app.page
        if page is None:
            return

        from src.ui.components.api_sync_modal import ApiSyncModal

        modal = ApiSyncModal(app=self.app, page=page)

        def _on_result(res):
            if res.error:
                self.app.show_snackbar(f"Sync falló: {res.error[:100]}", self.app.G360_ERROR)
            elif res.filas > 0:
                self.app.show_snackbar(
                    f"✓ {res.filas:,} filas actualizadas en {res.segundos:.0f}s",
                    self.app.G360_SUCCESS,
                )
                self._refrescar_card_db()
            else:
                self.app.show_snackbar(
                    "Tu DB ya está al día con la API", ft.Colors.ON_SURFACE_VARIANT
                )
                self._refrescar_card_db()

        modal.open(on_result=_on_result)

    def _iniciar_descarga(self, page):
        """Inicia la descarga desde el ultimo dato hasta hoy (post-login)."""
        import time

        from src.core import ventas_db
        from src.core.capture_service import CaptureService

        self.btn_actualizar_hoy.disabled = True
        page.update()

        status = ft.Text("Iniciando...", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        progress = ft.ProgressBar(value=0, visible=False, width=440)
        log_ctl = ft.Text("", size=10, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=10)
        abort_flag = threading.Event()
        _last_ui2 = [0.0]
        _svc_activo2 = [None]
        btn_stop2 = ft.TextButton(
            "Detener",
            on_click=lambda _: _svc_activo2[0].abort() if _svc_activo2[0] else abort_flag.set(),
            visible=False,
        )

        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.SYNC, color=self.app.G360_ACCENT),
                    ft.Text("Actualizando a hoy", size=14, weight=ft.FontWeight.BOLD),
                ],
                spacing=8,
            ),
            content=ft.Column(
                [status, progress, log_ctl], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO
            ),
            actions=[
                btn_stop2,
                ft.TextButton(
                    "Cerrar", on_click=lambda _: (setattr(dlg, "open", False), page.update())
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.open(dlg)

        def on_prog(stage, message, pct):
            try:
                # Updates dirigidos (page.update() completo desde worker congela)
                if pct is not None:
                    progress.visible = True
                    progress.value = max(0.0, min(1.0, pct))
                    progress.update()
                if stage == "chunk":
                    btn_stop2.visible = True
                    btn_stop2.update()
                if message:
                    log_ctl.value = (
                        log_ctl.value + "\n" + f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
                    )[-3000:]
                    status.value = message.split("\n")[-1][:120]
                    log_ctl.update()
                    status.update()
            except Exception:
                pass

        def worker():
            try:
                ventas_db.init_db()
                status.value = "Iniciando descarga..."
                status.update()
                svc = CaptureService(progress_cb=on_prog, abort_event=abort_flag)
                svc.mode_label = "Actualizar a hoy"
                _svc_activo2[0] = svc
                resumen = svc.update_from_last()
                _svc_activo2[0] = None
                if resumen.get("abortado"):
                    status.value = "Actualización detenida"
                    status.color = ft.Colors.ORANGE
                elif resumen.get("chunks_fallidos") or resumen.get("dias_fallidos"):
                    status.value = f"Parcial: {resumen.get('filas', 0)} filas (ver failed_*.json)"
                    status.color = ft.Colors.ORANGE
                else:
                    status.value = f"✓ Al día: {resumen.get('filas', 0)} filas"
                    status.color = self.app.G360_SUCCESS
                    time.sleep(0.4)
                    # 1) Snackbar ANTES de cerrar: page.close() desde el hilo worker
                    #    lanza RuntimeError (event loop) y si va después se traga
                    #    la snackbar. Primero avisamos, luego cerramos protegido.
                    try:
                        self.app.show_snackbar(
                            f"Historial actualizado (+{resumen.get('filas', 0)} filas)",
                            self.app.G360_SUCCESS,
                        )
                    except Exception:
                        pass
                    time.sleep(0.6)
                    try:
                        page.close(dlg)
                    except Exception:
                        pass
            except Exception as ex:
                status.value = f"Error: {ex}"
                status.color = self.app.G360_ERROR
            finally:
                _svc_activo2[0] = None
                self.btn_actualizar_hoy.disabled = False
                try:
                    self.btn_actualizar_hoy.update()
                except Exception:
                    pass
                self._refrescar_card_db()

        threading.Thread(target=worker, daemon=True).start()

    def _panel_intranet(self, page, mensaje_previo: str = ""):
        """Panel de descarga por intranet (login + primera carga/actualizar/extender/importar).

        Reutilizable dentro del modal de tabs. Retorna (content, actions).
        """
        import time

        import flet as ft
        from src.core import ventas_db
        from src.core.capture_service import CaptureService

        has_db = ventas_db.db_exists()
        has_creds = CaptureService.has_credentials()

        def _estado_db_txt():
            return ventas_db.coverage_summary()

        # ── Texto guía contextual ──
        _sync_hint = ""
        try:
            from src.core.db_network import buscar_db_remota

            _fuente = buscar_db_remota()
            if _fuente:
                _sync_hint = (
                    f"• Fuente detectada: {_fuente['ruta_corta']} "
                    f"({_fuente['size_mb']:.0f} MB, {_fuente['mtime']})\n"
                )
        except Exception:
            pass
        if not has_db:
            guia_texto = (
                "No tienes historial local. Elige una opción:\n"
                "• Sincronizar desde la DB fuente (g360-db-ventas)\n"
                "• Descargar desde la intranet (necesitas credenciales)\n"
                "• Copiar de otra PC en tu red (más rápido si un compañero ya descargó)"
            )
        elif has_creds:
            guia_texto = (
                "Tienes credenciales guardadas. Conecta para ver la fecha de la "
                "última actualización y continuar desde allí hasta hoy."
            )
        else:
            guia_texto = (
                "Ingresa tus credenciales para actualizar el historial, o usa la opción "
                "«Fuentes» para copiar desde otra PC."
            )
        guia_txt = ft.Text(
            (_sync_hint + guia_texto).rstrip(),
            size=12,
            color=G360Theme.text_muted_color(),
        )

        # ── Zona A: conexión (login) — visible al abrir ──
        user_input = ft.TextField(
            label="Usuario intranet",
            width=220,
            dense=True,
            text_size=13,
            border_radius=12,
        )
        if has_creds:
            u_saved, _ = CaptureService.credentials()
            user_input.value = u_saved
        pass_input = ft.TextField(
            label="Contraseña",
            width=220,
            dense=True,
            text_size=13,
            border_radius=12,
            password=True,
            can_reveal_password=True,
        )
        login_status = ft.Text(mensaje_previo, size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        btn_conectar = ft.ElevatedButton(
            "🔌 Conectar y guardar",
            on_click=None,
            height=38,
            width=220,
            style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
            tooltip="Verifica y guarda tu usuario/clave (necesarios para actualizar)",
        )
        btn_sync_db = ft.TextButton(
            "🔄 Sincronizar DB fuente",
            on_click=None,
            tooltip="Copia el historial.db actualizado de la DB fuente (g360-db-ventas)",
        )
        btn_sync_api = ft.TextButton(
            "☁ Actualizar desde API",
            on_click=None,
            tooltip=(
                "Actualiza los últimos 90 días desde la API de ventas "
                "(g360-ventas-api) y los escribe en la DB local. Si la API está "
                "apagada, despierta WSL automáticamente. No necesita la DB fuente "
                "ni descargar el XLS por intranet."
            ),
        )
        dd_anyos = ft.Dropdown(
            label="Ventana",
            dense=True,
            width=128,
            value="10",
            options=[
                ft.dropdown.Option("5", "5 años"),
                ft.dropdown.Option("10", "10 años"),
                ft.dropdown.Option("all", "Todos"),
            ],
        )
        btn_sync_parcial = ft.ElevatedButton(
            "⬇ Cargar años desde fuente",
            on_click=None,
            height=38,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.15, self.app.G360_ACCENT),
                color=self.app.G360_ACCENT,
            ),
            tooltip="Copia de la DB fuente (g360-db-ventas) solo los últimos N años (más rápido que la descarga vía intranet)",
        )
        login_box = ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.VPN_KEY_OUTLINED, size=18, color=self.app.G360_ACCENT),
                        ft.Text("Conexión a intranet", size=12, weight=ft.FontWeight.W_600),
                    ],
                    spacing=8,
                ),
                ft.Row([user_input, pass_input], spacing=8),
                login_status,
                btn_conectar,
                ft.Row([btn_sync_db, btn_sync_api], alignment=ft.MainAxisAlignment.CENTER),
                ft.Row(
                    [dd_anyos, btn_sync_parcial], alignment=ft.MainAxisAlignment.CENTER, spacing=8
                ),
                ft.Text(
                    "Carga inicial: copia solo la ventana elegida de la DB fuente "
                    "(5, 10 o todos los años) sin descargar por intranet.",
                    size=10,
                    color=G360Theme.text_muted_color(),
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            visible=True,
        )

        # ── Zona B: descarga — visible solo tras conectar OK ──
        badge_conectado = ft.Row(
            [
                ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=self.app.G360_SUCCESS),
                ft.Text(
                    "", size=12, weight=ft.FontWeight.BOLD, color=self.app.G360_SUCCESS, expand=True
                ),
            ],
            spacing=8,
        )
        db_status = ft.Text("Calculando cobertura…", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        def _fill_db_status():
            try:
                db_status.value = _estado_db_txt()
                if page:
                    page.update()
            except Exception:
                _logger.exception("db_status fallo")

        threading.Thread(target=_fill_db_status, daemon=True).start()
        cap_status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        # Botón único: continuar la descarga desde el último dato guardado hasta hoy
        btn_update = ft.ElevatedButton(
            "🔄 Actualizar hasta hoy",
            on_click=None,
            height=40,
            width=200,
            style=ft.ButtonStyle(
                bgcolor=self.app.G360_ACCENT,
                padding=ft.padding.symmetric(horizontal=24, vertical=10),
            ),
            disabled=not has_creds,
            tooltip="Descarga desde el último día con datos hasta hoy",
        )
        btn_stop = ft.TextButton("Detener", on_click=None, visible=False)
        progress = ft.ProgressBar(value=0, visible=False, width=480)
        log_ctl = ft.Text("", size=10, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=14)
        descarga_box = ft.Column(
            [
                badge_conectado,  # estado de conexión + credenciales
                guia_txt,  # última actualización visible en db_status
                db_status,  # fecha del último dato y cobertura
                btn_update,  # única acción: desde la última fecha hasta hoy
                cap_status,
                progress,
                log_ctl,
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )

        abort_flag = threading.Event()
        _last_ui = [0.0]
        _svc_activo = [None]
        _ui_lock = threading.Lock()

        def _upd(*controles):
            with _ui_lock:
                for c in controles:
                    try:
                        if getattr(c, "page", None) is None:
                            continue
                        c.update()
                    except Exception:
                        _logger.exception("update de UI fallo (%s)", type(c).__name__)

        def set_busy(busy: bool):
            btn_conectar.disabled = busy
            btn_update.disabled = busy
            btn_stop.visible = busy
            try:
                btn_conectar.update()
                btn_update.update()
                btn_stop.update()
            except Exception:
                pass

        def append_log(msg: str):
            log_ctl.value = (log_ctl.value + "\n" + msg)[-4000:]

        def on_progress(stage: str, message: str, pct):
            try:
                # SOLO updates dirigidos y SERIALIZADOS via _upd(): updates
                # concurrentes desde worker/heartbeat/poll corrompen el render.
                now = time.time()
                objetivos = []
                if pct is not None:
                    progress.visible = True
                    progress.value = max(0.0, min(1.0, pct))
                    objetivos.append(progress)
                if message:
                    append_log(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
                    objetivos.append(log_ctl)
                _upd(*objetivos)
                if stage in ("preparacion", "fin") and now - _last_ui[0] >= 0.5:
                    _last_ui[0] = now
                    _upd(db_status)
            except Exception:
                _logger.exception("on_progress fallo")

        def conectar(_):
            """Login real contra la intranet. Si falla, no se revela la descarga."""
            u = (user_input.value or "").strip()
            p = (pass_input.value or "").strip()
            if not u or not p:
                login_status.value = "✗ Ingresa usuario y contraseña"
                login_status.color = self.app.G360_ERROR
                page.update()
                return
            user_input.disabled = True
            pass_input.disabled = True
            btn_conectar.disabled = True
            login_status.value = "Conectando a la intranet..."
            login_status.color = self.app.G360_ACCENT
            self.app.show_loading("Verificando credenciales...")
            page.update()

            def worker():
                from src.core.intranet_client import IntranetClient

                cli = IntranetClient(u, p, timeout=90.0)
                try:
                    ok, msg = cli.verify_credentials()
                except Exception as ex:
                    ok, msg = False, f"Error inesperado: {ex}"
                finally:
                    cli.close()
                if not ok:
                    login_status.value = f"✗ Acceso denegado — {msg}"
                    login_status.color = self.app.G360_ERROR
                    user_input.disabled = False
                    pass_input.disabled = False
                    btn_conectar.disabled = False
                    self.app.hide_loading()
                    page.update()
                    return
                CaptureService.save_credentials(u, p)
                api_suffix = CaptureService.refresh_api_token_best_effort(u, p)
                badge_conectado.controls[1].value = f"Conectado — {msg}{api_suffix}"
                db_status.value = _estado_db_txt()
                btn_update.disabled = False
                login_box.visible = False
                descarga_box.visible = True
                self.app.hide_loading()
                page.update()

            threading.Thread(target=worker, daemon=True).start()

        def stop(_):
            abort_flag.set()
            svc = _svc_activo[0]
            if svc is not None:
                svc.abort()  # cierra el socket: rompe requests en vuelo al instante
            append_log(
                f"[{datetime.now().strftime('%H:%M:%S')}] ⏹ Detención solicitada — abortando request en curso..."
            )

        def _fmt_duracion(seg: float) -> str:
            seg = int(max(0, seg))
            if seg < 60:
                return f"{seg}s"
            m, s = divmod(seg, 60)
            if m < 60:
                return f"{m}m {s:02d}s"
            h, m = divmod(m, 60)
            return f"{h}h {m:02d}m"

        # Estimacion medida en produccion (bench 07/09/2026): mes completo ~2.4 min
        # (GET 10s + POST 134s); dia suelto ~17.5s. Margen del 25%.
        SEG_POR_MES = 180
        SEG_POR_DIA = 25

        def _estimar(n_meses: int = 0, n_dias: int = 0) -> str:
            seg = n_meses * SEG_POR_MES + n_dias * SEG_POR_DIA
            return _fmt_duracion(max(60, seg * 1.25))

        def confirmar_actualizar(_):
            """Alerta de tiempo estimado + confirmación: desde el último dato hasta hoy."""
            if not ventas_db.db_exists():
                self.app.show_snackbar(
                    "Sin historial local: usa '🔄 Sincronizar DB fuente' o '📂 Importar archivo .db'",
                    self.app.G360_ERROR,
                )
                return
            est = _estimar(0, 7)
            detalle = "Solo los últimos 7 días (overlap de seguridad)"

            def iniciar_confirmado(_):
                page.close(dlg_confirm)
                _ejecutar_captura(est)  # update_from_last: última fecha → hoy

            def cancelar_confirmado(_):
                page.close(dlg_confirm)

            dlg_confirm = ft.AlertDialog(
                modal=True,
                title=ft.Row(
                    [
                        ft.Icon(ft.Icons.SCHEDULE, color=ft.Colors.ORANGE),
                        ft.Text("Tiempo estimado de descarga", size=14, weight=ft.FontWeight.BOLD),
                    ],
                    spacing=8,
                ),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.HOURGLASS_TOP, size=40, color=ft.Colors.ORANGE),
                                ft.Text(est, size=28, weight=ft.FontWeight.BOLD),
                            ],
                            spacing=12,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        ft.Text(detalle, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(
                            "La intranet es lenta (~1 min por mes). Puedes detener en cualquier momento "
                            "con el botón 'Detener'; lo ya descargado queda guardado en SQLite.",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Cancelar", on_click=cancelar_confirmado),
                    ft.ElevatedButton(
                        "✓ Iniciar descarga",
                        on_click=iniciar_confirmado,
                        style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.open(dlg_confirm)

        def _ejecutar_captura(est_txt: str):
            cap_status.value = "Preparando SQLite local..."
            cap_status.color = self.app.G360_ACCENT
            progress.visible = True
            progress.value = 0
            log_ctl.value = ""
            set_busy(True)
            page.update()

            def _ts() -> str:
                return datetime.now().strftime("%H:%M:%S")

            def worker():
                t_ini = None
                ultimo_msg = [0.0]  # heartbeat: detecta procesos trabados
                try:
                    # 1) Preparación del SQLite (etapa visible)
                    ventas_db.init_db()
                    append_log(f"[{_ts()}] 🗄️ SQLite listo: {ventas_db.db_path()}")
                    _upd(log_ctl)

                    # 2) Captura con ETA en vivo (login ya validado al conectar)
                    from src.core.capture_service import CaptureService as _CS

                    t_ini = time.time()
                    user, pwd = _CS.credentials()

                    def on_progress_eta(stage: str, message: str, pct):
                        ultimo_msg[0] = time.time()
                        if pct and t_ini:
                            elapsed = time.time() - t_ini
                            eta = _fmt_duracion(elapsed / pct * (1 - pct))
                            message = f"{message}  ⏱ restante ~{eta} (est. inicial {est_txt})"
                        on_progress(stage, message, pct)

                    svc = CaptureService(progress_cb=on_progress_eta, abort_event=abort_flag)
                    svc.mode_label = "Actualizar hasta hoy"
                    _svc_activo[0] = svc
                    # Heartbeat: si no llega mensaje en 60s, avisar en el log
                    # (no interrumpe: solo diagnostico de procesos trabados)
                    corriendo = [True]

                    def heartbeat():
                        while corriendo[0]:
                            time.sleep(10)
                            if not corriendo[0]:
                                break
                            silencio = time.time() - ultimo_msg[0]
                            if silencio >= 60:
                                etapa = (
                                    "POST export (server generando XLS)"
                                    if silencio < 300
                                    else "sin respuesta del server"
                                )
                                on_progress(
                                    "heartbeat",
                                    f"[{_ts()}] ⏳ {silencio:.0f}s sin eventos — probablemente {etapa}; sigue vivo, espera o Detén",
                                    None,
                                )

                    threading.Thread(target=heartbeat, daemon=True).start()
                    try:
                        resumen = svc.update_from_last()  # última fecha → hoy
                    finally:
                        corriendo[0] = False
                    if resumen.get("abortado"):
                        cap_status.value = "Captura detenida por el usuario"
                        cap_status.color = ft.Colors.ORANGE
                    elif resumen.get("chunks_fallidos") or resumen.get("dias_fallidos"):
                        cap_status.value = (
                            f"Parcial: {resumen.get('filas', 0)} filas · "
                            f"{len(resumen.get('chunks_fallidos', []) + resumen.get('dias_fallidos', []))} chunks fallidos (ver data/failed_*.json)"
                        )
                        cap_status.color = ft.Colors.ORANGE
                    else:
                        dur_real = _fmt_duracion(time.time() - t_ini) if t_ini else "?"
                        cap_status.value = (
                            f"OK: {resumen.get('filas', 0)} filas en {dur_real} "
                            f"(dedup eliminó {resumen.get('dedup_eliminadas', 0)})"
                        )
                        cap_status.color = self.app.G360_SUCCESS
                    append_log(f"[{_ts()}] {cap_status.value}")
                    if resumen.get("nc_nd_huerfanas"):
                        append_log(
                            f"[{_ts()}] ℹ️ {resumen['nc_nd_huerfanas']} NC/ND con factura fuera del rango (esperan descarga de meses previos)"
                        )
                    _upd(cap_status)
                    db_status.value = _estado_db_txt()
                    _upd(db_status)
                    self._refrescar_card_db()
                except Exception as ex:
                    cap_status.value = f"Error: {ex}"
                    cap_status.color = self.app.G360_ERROR
                    append_log(f"[{_ts()}] {cap_status.value}")
                    _upd(cap_status)
                finally:
                    progress.visible = False
                    try:
                        _upd(progress)
                    except Exception:
                        pass
                    set_busy(False)

            threading.Thread(target=worker, daemon=True).start()

        # Nota: importar archivo .db vive en la pestaña Fuentes
        # (con comparativa + confirmación). Aquí solo queda el sync
        # automático desde la DB fuente conocida.

        def sincronizar(_):
            """Copia el historial.db de la DB fuente (g360-db-ventas), con snapshot consistente."""
            login_status.value = "Buscando DB fuente..."
            page.update()

            def run():
                from src.core.db_network import buscar_db_remota, sincronizar_desde_remota

                t0 = time.time()
                try:
                    info = buscar_db_remota()
                    if not info:
                        login_status.value = (
                            "✗ No se encontró la DB fuente (g360-db-ventas). "
                            "Configura la variable G360_DB_ORIGEN con su carpeta."
                        )
                        login_status.color = self.app.G360_ERROR
                        page.update()
                        return
                    set_busy(True)
                    try:
                        res = sincronizar_desde_remota(progress_cb=lambda *_a: None)
                        dur = time.time() - t0
                        login_status.value = (
                            f"✓ Sincronizado con la fuente en {dur:.0f}s: "
                            f"{res.get('filas', 0):,} filas "
                            f"({res.get('fecha_min')} a {res.get('fecha_max')})"
                        )
                        login_status.color = self.app.G360_SUCCESS
                        db_status.value = _estado_db_txt()
                        _upd(db_status)
                        self._refrescar_card_db()
                        btn_update.disabled = False
                        descarga_box.visible = True
                    finally:
                        set_busy(False)
                except Exception as ex:
                    login_status.value = f"✗ Sync con la fuente falló: {ex}"
                    login_status.color = self.app.G360_ERROR
                page.update()

            threading.Thread(target=run, daemon=True).start()

        def sincronizar_api(_):
            """Sync incremental desde la API Go hacia la DB local.

            Despierta WSL/API si hace falta, detecta los días desfasados por
            checksum (filas + soles) y baja solo esos días. Reemplaza folio por
            folio, así que no toca la DB fuente ni pasa por la descarga del XLS.
            """
            from src.core.api_auth import default_api_url

            _correr_sync_api(
                status=login_status,
                btn_sync=btn_sync_api,
                app=self.app,
                page=page,
                api_url=default_api_url(),
                set_busy=set_busy,
                al_actualizar=_datos_nuevos,
                registrar=append_log,
            )

        def _datos_nuevos(_res):
            """Refresca la UI solo si el sync trajo filas nuevas."""
            db_status.value = _estado_db_txt()
            _upd(db_status)
            self._refrescar_card_db()
            btn_update.disabled = False
            descarga_box.visible = True

        def cargar_parcial(_):
            """Copia de la DB fuente solo la ventana elegida (5/10/todos años)."""
            sel = dd_anyos.value
            anyos = None if sel == "all" else int(sel)
            login_status.value = "Buscando DB fuente..."
            login_status.color = ft.Colors.ON_SURFACE_VARIANT
            page.update()

            def run():
                from src.core.db_network import sincronizar_desde_remota_parcial

                t0 = time.time()
                try:
                    set_busy(True)
                    try:
                        res = sincronizar_desde_remota_parcial(anyos=anyos)
                        dur = time.time() - t0
                        ventana = "todos los años" if anyos is None else f"últimos {anyos} años"
                        desde = (
                            f" · desde {res.get('fecha_corte')}"
                            if res.get("fecha_corte")
                            else " · data completa"
                        )
                        login_status.value = (
                            f"✓ Cargados {res.get('filas', 0):,} filas ({ventana}{desde}) "
                            f"de {res.get('origen')} en {dur:.0f}s"
                        )
                        login_status.color = self.app.G360_SUCCESS
                        db_status.value = _estado_db_txt()
                        _upd(db_status)
                        self._refrescar_card_db()
                        btn_update.disabled = False
                        descarga_box.visible = True
                    finally:
                        set_busy(False)
                except Exception as ex:
                    login_status.value = f"✗ Carga desde fuente falló: {ex}"
                    login_status.color = self.app.G360_ERROR
                page.update()

            threading.Thread(target=run, daemon=True).start()

        btn_conectar.on_click = conectar
        btn_sync_db.on_click = sincronizar
        btn_sync_api.on_click = sincronizar_api
        btn_sync_parcial.on_click = cargar_parcial
        pass_input.on_submit = conectar
        btn_update.on_click = confirmar_actualizar
        btn_stop.on_click = stop

        content = ft.Column(
            [
                login_box,
                descarga_box,
            ],
            spacing=8,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )
        return content, [btn_stop]

    def _gestionar_datos(self, e, mensaje_previo: str = ""):
        """Modal standalone de descarga por intranet (wrapper del panel)."""
        import flet as ft

        page = self.app.page
        if page is None:
            self.app.show_snackbar(
                "No hay página disponible para abrir el diálogo", self.app.G360_ERROR
            )
            return
        content, actions = self._panel_intranet(page, mensaje_previo)
        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.CLOUD_DOWNLOAD_OUTLINED, color=self.app.G360_ACCENT),
                    ft.Text(
                        "Acceso al historial de ventas (intranet)",
                        size=14,
                        weight=ft.FontWeight.W_700,
                    ),
                ],
                spacing=8,
            ),
            content=content,
            actions=actions + [ft.TextButton("Cerrar", on_click=lambda _: page.close(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
            width=520,
        )
        page.open(dlg)

    def sugerir_carga_inicial(self, page, info: dict):
        """Modal de primera carga (PC nueva): hay una DB fuente y la local
        está vacía. Pregunta qué ventana copiar — 5, 10 o todos los años."""
        import flet as ft

        dd = ft.Dropdown(
            label="Ventana de años",
            dense=True,
            width=210,
            value="10",
            options=[
                ft.dropdown.Option("5", "5 años"),
                ft.dropdown.Option("10", "10 años"),
                ft.dropdown.Option("all", "Todos"),
            ],
        )
        status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        btn = ft.ElevatedButton(
            "Cargar historial",
            height=38,
            style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT),
        )
        dlg = ft.AlertDialog(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.CLOUD_SYNC_OUTLINED, color=self.app.G360_ACCENT),
                    ft.Text("Bienvenido — primera carga", size=14, weight=ft.FontWeight.W_700),
                ],
                spacing=8,
            ),
            content=ft.Column(
                [
                    ft.Text(
                        "Se encontró una DB fuente con ventas ya capturadas:\n"
                        f"{info.get('ruta_corta', info.get('ruta', ''))} "
                        f"({info.get('size_mb', 0):.0f} MB, {info.get('mtime', '')})\n\n"
                        "Elige cuánto cargar. 10 años cubre todo el período de "
                        "reconocimiento; 5 es más liviano; 'Todos' replica la fuente "
                        "completa.",
                        size=12,
                    ),
                    dd,
                    status,
                ],
                spacing=8,
                tight=True,
            ),
            actions=[
                btn,
                ft.TextButton("Más tarde", on_click=lambda _: page.close(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        estado = [True]

        def _run():
            anyos = None if dd.value in (None, "all") else int(dd.value)
            btn.disabled = True
            status.value = "Cargando desde la fuente (puede tardar un poco)..."
            status.color = self.app.G360_ACCENT
            page.update()
            try:
                from src.core.db_network import sincronizar_desde_remota_parcial

                res = sincronizar_desde_remota_parcial(anyos=anyos)
            except Exception as ex:
                btn.disabled = False
                status.value = f"✗ Carga falló: {ex}"
                status.color = self.app.G360_ERROR
                page.update()
                return
            if not estado[0]:
                return
            page.close(dlg)
            estado[0] = False
            self._refrescar_card_db()
            page.update()
            self.app.show_snackbar(
                f"Historial cargado: {res.get('filas', 0):,} filas desde la fuente",
                self.app.G360_SUCCESS,
            )

        btn.on_click = lambda _: threading.Thread(target=_run, daemon=True).start()
        page.open(dlg)
