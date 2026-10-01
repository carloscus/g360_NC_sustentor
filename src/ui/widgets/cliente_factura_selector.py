import flet as ft
from typing import Optional, Callable
from src.core.utils import build_dropdown_options


class ClienteFacturaSelector:
    """Componente reutilizable para selección de cliente y factura.

    Unifica la lógica duplicada en 5+ clusters de dropdowns de cliente/factura
    en reconocimiento_view.py. Maneja filtrado por vendedor, carga de clientes
    y facturas, y construcción de opciones de dropdown.

    Args:
        suffix: Sufijo único para los IDs de los controles (ej: 'sf', 'df', 'ci').
        on_cliente_change: Callback cuando cambia el cliente (opcional).
        on_factura_change: Callback cuando cambia la factura (opcional).
        show_factura: Si True, muestra el dropdown de factura (default: False).
        use_build_options: Si True, usa build_dropdown_options para clientes
            (default: True). Si False, usa lógica manual con format_id_name.
    """

    def __init__(
        self,
        suffix: str,
        on_cliente_change: Optional[Callable] = None,
        on_factura_change: Optional[Callable] = None,
        show_factura: bool = False,
        use_build_options: bool = True,
    ):
        self.suffix = suffix
        self._on_cliente_change = on_cliente_change
        self._on_factura_change = on_factura_change
        self._show_factura = show_factura
        self._use_build_options = use_build_options

        self.cliente_dropdown = ft.Dropdown(
            label="Cliente",
            dense=True,
            text_size=12,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=4),
            width=260,
            on_change=self._handle_cliente_change,
        )

        self.factura_dropdown = ft.Dropdown(
            label="Factura",
            dense=True,
            text_size=12,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=4),
            width=260,
            visible=show_factura,
            on_change=self._handle_factura_change,
        )

    def _handle_cliente_change(self, e):
        cliente = e.control.value
        if cliente and self._show_factura:
            pass  # subclass will override or caller handles via callback
        elif not cliente and self._show_factura:
            self.factura_dropdown.options = []
            self.factura_dropdown.value = None
        if self._on_cliente_change:
            self._on_cliente_change(e)

    def _handle_factura_change(self, e):
        if self._on_factura_change:
            self._on_factura_change(e)

    def cargar_clientes(self, df_historial, vendedor_id=None):
        """Carga opciones de cliente desde el historial, filtrado por vendedor."""
        if df_historial is None:
            return
        df = df_historial
        if "CLIENTE" not in df.columns:
            self.cliente_dropdown.options = []
            return

        if vendedor_id and "COD_VENDEDOR" in df.columns:
            df = df[df["COD_VENDEDOR"].astype(str).str.strip() == vendedor_id.strip()]

        if self._use_build_options:
            self.cliente_dropdown.options = build_dropdown_options(
                df,
                id_field="COD_CLIENTE",
                name_field="CLIENTE",
                label_field="CLIENTE",
                max_options=100,
            )
        else:
            tiene_id = "COD_CLIENTE" in df.columns
            if tiene_id:
                from src.core.utils import format_id_name

                mask_valida = df["CLIENTE"].astype(str).str.strip().ne("") & df["CLIENTE"].notna()
                clientes = df.loc[mask_valida, ["COD_CLIENTE", "CLIENTE"]].drop_duplicates()
                opts = []
                for _, r in clientes.iterrows():
                    cid = str(r["COD_CLIENTE"]).strip()
                    cnom = str(r["CLIENTE"]).strip()
                    display = format_id_name(cid, cnom, "CLIENTE") if cid else cnom
                    opts.append(ft.dropdown.Option(key=cnom, text=display))
            else:
                clientes = df["CLIENTE"].dropna().unique()
                opts = [ft.dropdown.Option(key=c, text=c) for c in sorted(clientes)[:100]]
            self.cliente_dropdown.options = opts

    def cargar_facturas(self, df_historial):
        """Carga opciones de factura para el cliente seleccionado."""
        if df_historial is None or not self.cliente_dropdown.value:
            return
        cliente = self.cliente_dropdown.value
        df = df_historial
        mask = df["CLIENTE"].astype(str).str.strip() == cliente.strip()
        facturas = df[mask & df["TIPO_DOC"].astype(str).str.upper().str.startswith("F")].copy()
        if facturas.empty:
            self.factura_dropdown.options = []
            return
        facturas["DOC_ID"] = facturas.apply(
            lambda r: (
                f"{str(r['TIPO_DOC']).strip()[0]}{str(r['SERIE']).strip()}-{str(r['NUMERO']).strip().replace('.0', '')}"
            ),
            axis=1,
        )
        # Descendente por fecha: la factura mas reciente primero
        unicas = (
            facturas.sort_values("FECHA", ascending=False).groupby("DOC_ID").first().reset_index()
        )
        opts = [
            ft.dropdown.Option(
                key=r.DOC_ID,
                text=f"{r.DOC_ID} | {str(getattr(r, 'FECHA', ''))[:10]} | S/ {getattr(r, 'SOLES', 0):,.2f}",
            )
            for r in unicas.itertuples()
        ][:100]
        self.factura_dropdown.options = opts

    def limpiar(self):
        """Limpia ambos dropdowns."""
        self.cliente_dropdown.value = None
        self.factura_dropdown.options = []
        self.factura_dropdown.value = None

    def limpiar_facturas(self):
        """Limpia solo el dropdown de facturas."""
        self.factura_dropdown.options = []
        self.factura_dropdown.value = None

    @property
    def cliente_value(self) -> Optional[str]:
        return self.cliente_dropdown.value

    @property
    def factura_value(self) -> Optional[str]:
        return self.factura_dropdown.value
