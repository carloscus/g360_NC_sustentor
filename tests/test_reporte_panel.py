"""Card de Reportes de compras: ciclo expandir/limpiar y reset general."""

from datetime import datetime
from types import SimpleNamespace

import flet as ft
import pandas as pd

from src.core.fechas import fecha_ui
from src.ui import reporte_compras as rc
from src.ui.reporte_panel import ReportePanel
from src.ui.widgets.cliente_picker import _clave_orden


class _FakeApp:
    G360_ACCENT = "#0D2B4E"
    G360_SUCCESS = "OK"
    G360_WARNING = "WARN"
    G360_ERROR = "ERR"

    def __init__(self):
        self.page = None  # sin UI: los hilos/generación no corren
        self.snacks = []

    def show_snackbar(self, msg, color=None):
        self.snacks.append(str(msg))


def _panel():
    return ReportePanel(_FakeApp(), SimpleNamespace())


def _controles(raiz, profundidad: int = 4) -> list:
    """Todos los controles del arbol (el body ahora usa bloques anidados)."""
    out = []
    if isinstance(raiz, ft.Control):
        out.append(raiz)
    if profundidad <= 0:
        return out
    for attr in ("controls", "content"):
        hijo = getattr(raiz, attr, None)
        if isinstance(hijo, ft.Control):
            out.extend(_controles(hijo, profundidad - 1))
        elif isinstance(hijo, list):
            for c in hijo:
                out.extend(_controles(c, profundidad - 1))
    return out


class TestCard:
    def test_colapsada_por_defecto_y_cacheada(self):
        p = _panel()
        card1 = p.construir_card()
        card2 = p.construir_card()
        assert card1 is card2  # misma instancia en cada rebuild
        assert card1.key == "reportes_card"
        assert p._body.visible is False
        assert p._abierto is False

    def test_expandir_abre(self):
        p = _panel()
        p.construir_card()
        p.expandir()
        assert p._body.visible is True
        assert p._abierto is True

    def test_abrir_sin_generar(self):
        """Acceso directo del header: abre la colapsada sin generar nada."""
        p = _panel()
        p.construir_card()
        assert p._abierto is False
        p.abrir()
        assert p._abierto is True
        assert p._body.visible is True
        assert p.state["datos"] == {}

    def test_expandir_prefill(self):
        p = _panel()
        p.construir_card()
        desde, hasta = datetime(2026, 8, 1), datetime(2026, 9, 24)
        p.expandir(cliente=("00068414", "NESTLE"), vendedor="01188", rango=(desde, hasta))
        assert p.state["clientes"] == [("00068414", "NESTLE")]
        assert p.state["vendedor"] == "01188"
        assert p.state["desde"] == desde and p.state["hasta"] == hasta
        assert fecha_ui(desde) in p.desde_label.value  # dd-mm-yyyy, no dd/mm/yyyy
        assert len(p.cli_chips.controls) == 1

    def test_toggle_colapsa(self):
        p = _panel()
        p.construir_card()
        p.expandir()
        p._toggle()
        assert p._abierto is False
        assert p._body.visible is False

    def test_pildora_expandir_identificable(self):
        """La píldora lleva texto e icono sincronizados (no un chevron solo)."""
        import flet as ft

        p = _panel()
        p.construir_card()
        b = p._chevron
        assert isinstance(b, ft.ElevatedButton)
        assert b.text == "Expandir"
        assert b.icon == ft.Icons.EXPAND_MORE
        assert b.tooltip
        p.expandir()
        assert (b.text, b.icon) == ("Colapsar", ft.Icons.EXPAND_LESS)
        p.colapsar()
        assert (b.text, b.icon) == ("Expandir", ft.Icons.EXPAND_MORE)


class TestChipsHojas:
    def test_sucursales_opcional_por_defecto(self):
        p = _panel()
        p.construir_card()
        assert p.state["hojas"] == set(ReportePanel.DEFAULT_HOJAS)
        assert len(p._chips) == len(ReportePanel.HOJAS_EXPORT)
        assert all(p._chips[k].selected for k in ReportePanel.DEFAULT_HOJAS)
        assert not p._chips["sucursales"].selected

    def test_desmarcar_quita_hoja(self):
        from types import SimpleNamespace

        p = _panel()
        p.construir_card()
        ev = SimpleNamespace(control=SimpleNamespace(data="bd", selected=False))
        p._on_chip(ev)
        assert "bd" not in p.state["hojas"]
        assert "consolidado" in p.state["hojas"]

    def test_marcar_agrega_hoja(self):
        from types import SimpleNamespace

        p = _panel()
        p.construir_card()
        p.state["hojas"].discard("ajustes")
        ev = SimpleNamespace(control=SimpleNamespace(data="ajustes", selected=True))
        p._on_chip(ev)
        assert "ajustes" in p.state["hojas"]

    def test_chip_desconocido_se_ignora(self):
        from types import SimpleNamespace

        p = _panel()
        p.construir_card()
        antes = set(p.state["hojas"])
        ev = SimpleNamespace(control=SimpleNamespace(data="otra", selected=False))
        p._on_chip(ev)
        assert p.state["hojas"] == antes

    def test_limpiar_restablece_hojas_estandar(self):
        from types import SimpleNamespace

        p = _panel()
        p.construir_card()
        p._on_chip(SimpleNamespace(control=SimpleNamespace(data="resumen", selected=False)))
        p.limpiar()
        assert p.state["hojas"] == set(ReportePanel.DEFAULT_HOJAS)
        assert all(p._chips[k].selected for k in ReportePanel.DEFAULT_HOJAS)
        assert not p._chips["sucursales"].selected


class TestCargaDropdowns:
    def test_primera_apertura_carga_vendedores(self):
        p = _panel()
        p.construir_card()
        llamadas = []
        p._cargar_vendedores = lambda: llamadas.append("vend")
        p._mostrar()
        assert llamadas == ["vend"]
        assert p._cargado is True
        p._mostrar()  # segunda apertura no recarga
        assert llamadas == ["vend"]

    def test_expandir_carga_vendedores_y_precarga_cliente(self):
        p = _panel()
        p.construir_card()
        llamadas = []
        p._cargar_vendedores = lambda: llamadas.append("vend")
        p.expandir(cliente=("00068414", "NESTLE"))
        assert llamadas == ["vend"]
        assert p.state["clientes"] == [("00068414", "NESTLE")]


class TestLimpiar:
    def _sucia(self):
        p = _panel()
        p.construir_card()
        p.expandir(
            cliente=("00068414", "NESTLE"),
            vendedor="01188",
            rango=(datetime(2026, 8, 1), datetime(2026, 9, 24)),
        )
        p.state["datos"] = {"00068414": object()}
        p.status.value = "algo"
        p.resultados.controls.append("x")
        return p

    def test_limpia_sin_colapsar(self):
        p = self._sucia()
        p.limpiar(colapsar=False)
        assert p._abierto is True and p._body.visible is True
        assert p.state["clientes"] == []
        assert p.state["vendedor"] is None
        assert p.state["datos"] == {}
        assert p.state["incluir_nc"] is True
        assert not p.vend_dd.value and p.cli_chips.controls == []
        assert p.nc_sw.value is True
        assert p.status.value == ""
        assert p.resultados.controls == []
        # Rango restaurado a defaults (enero → hoy)
        assert p.state["desde"].month == 1 and p.state["desde"].year == datetime.now().year

    def test_limpia_colapsando(self):
        p = self._sucia()
        p.limpiar(colapsar=True)
        assert p._abierto is False and p._body.visible is False

    def test_no_interrumpe_tarea_en_curso(self):
        p = self._sucia()
        p._busy = True
        p.limpiar(colapsar=True)
        # no limpia ni colapsa mientras genera/exporta
        assert p.state["clientes"] == [("00068414", "NESTLE")]
        assert p._abierto is True
        assert p._busy is True

    def test_limpia_refresca_dropdown_vendedores(self):
        p = self._sucia()
        llamadas = []
        p._cargar_vendedores = lambda: llamadas.append("vend")
        p.limpiar(colapsar=False)
        assert not p.vend_dd.value
        assert llamadas == ["vend"]  # opciones recargadas, selección en None

    def test_modal_abre_sin_preseleccion_tras_limpiar(self, monkeypatch):
        import src.ui.widgets.cliente_picker as cp

        p = self._sucia()
        p.limpiar(colapsar=False)
        capturado = {}
        monkeypatch.setattr(
            cp,
            "abrir_picker_clientes",
            lambda app, **kw: capturado.update(kw),
        )
        p._abrir_picker_clientes()
        assert capturado.get("initial") == set()
        assert capturado.get("multiple") is True


class TestNombreArchivo:
    def test_solo_fecha_creacion(self):
        n = rc.nombre_archivo_compras(
            "DISTRIBUIDORA LOS ANGELES & HNOS. S.A.C.", "56101", "2026-09-26"
        )
        assert n == ("Compras - DISTRIBUIDORA LOS ANGELES & HNOS. S.A.C. (56101) - 2026-09-26.xlsx")

    def test_fecha_por_defecto_hoy(self):
        from datetime import date

        n = rc.nombre_archivo_compras("M", "56101")
        assert n == f"Compras - M (56101) - {date.today().isoformat()}.xlsx"

    def test_sanitiza_invalidos(self):
        n = rc.nombre_archivo_compras('CLIENTE "X" <y> |1|', "56101", "2026-09-26")
        assert '"' not in n and "<" not in n and ">" not in n and "|" not in n

    def test_sin_nombre(self):
        from datetime import date

        assert rc.nombre_archivo_compras("", "56101") == (
            f"Compras - (56101) - {date.today().isoformat()}.xlsx"
        )

    def test_recorta_nombre_largo(self):
        n = rc.nombre_archivo_compras("A" * 80, "1", "2026-09-26")
        nombre_parte = n.split(" - ")[1].rsplit(" (", 1)[0]
        assert len(nombre_parte) <= 48


class TestOrdenClientes:
    def test_clave_normaliza(self):
        assert _clave_orden("Ábaco") == _clave_orden("abaco") == "ABACO"

    def test_orden_en_picker(self):
        """El picker compartido lista A→Z (sin tildes de por medio)."""
        cs = [{"id": "2", "nombre": "zeta"}, {"id": "1", "nombre": "Ábaco"}]
        assert [c["id"] for c in sorted(cs, key=lambda c: _clave_orden(c["nombre"]))] == ["1", "2"]


class TestClientesMultiseleccion:
    def test_confirmar_agrega_clientes_y_chips(self):
        p = _panel()
        p.construir_card()
        p._generar = lambda *a, **k: None  # sin page/DB
        p._on_clientes_confirmados(
            [
                {"id": "00056101", "nombre": "MULTICOPIAS"},
                {"id": "00068414", "nombre": "NESTLE"},
            ]
        )
        assert p.state["clientes"] == [("00056101", "MULTICOPIAS"), ("00068414", "NESTLE")]
        assert len(p.cli_chips.controls) == 2

    def test_confirmar_no_duplica(self):
        p = _panel()
        p.construir_card()
        p._generar = lambda *a, **k: None
        p._on_clientes_confirmados([{"id": "1", "nombre": "A"}])
        p._on_clientes_confirmados([{"id": "1", "nombre": "A"}, {"id": "2", "nombre": "B"}])
        assert p.state["clientes"] == [("1", "A"), ("2", "B")]

    def test_quitar_cliente_limpia_chip_y_datos(self):
        p = _panel()
        p.construir_card()
        p.state["clientes"] = [("1", "A"), ("2", "B")]
        p.state["datos"] = {"1": object(), "2": object()}
        p._pintar_cli_chips()
        p._quitar_cliente("1")
        assert p.state["clientes"] == [("2", "B")]
        assert set(p.state["datos"]) == {"2"}
        assert len(p.cli_chips.controls) == 1

    def test_generar_sin_clientes_avisa(self):
        p = _panel()
        p.construir_card()
        p._generar()  # page None → retorna temprano, sin error
        assert p.state["clientes"] == []


class TestPropertiesXlsx:
    def test_autor_y_descripcion(self, tmp_path):
        import pandas as pd
        from openpyxl import load_workbook

        df = pd.DataFrame([{"DOC": "F204-1", "FECHA": "2026-09-05", "SOLES": 10.0, "CANTIDAD": 5}])
        out = tmp_path / "C.xlsx"
        rc._escribir_xlsx(out, "56101", "M", df_hechos=df, hojas={"bd"})
        wb = load_workbook(str(out))
        assert wb.properties.creator == "ccusi"
        assert wb.properties.description == "Generado por G360"


class TestSubtitulos:
    @staticmethod
    def _textos(ctrl):
        """Recorre el árbol de controles y devuelve los textos planos."""
        out = []
        val = getattr(ctrl, "value", None)
        if isinstance(val, str):
            out.append(val)
        for hijo in getattr(ctrl, "controls", None) or []:
            out += TestSubtitulos._textos(hijo)
        content = getattr(ctrl, "content", None)
        if content is not None:
            out += TestSubtitulos._textos(content)
        return out

    def test_card_titulo_claro_y_subtitulo(self):
        p = _panel()
        card = p.construir_card()
        textos = self._textos(card)
        assert "Reporte de compras" in textos
        assert not any(t[:1].isdigit() and "REPORTES" in t for t in textos)
        assert any(t.startswith("Consulta directa a la base local") for t in textos)

    def test_bajada_en_header_no_en_body(self):
        """La bajada vive en el header (como el resto de cards), no en el body colapsable."""
        import flet as ft

        p = _panel()
        card = p.construir_card()
        header = card.content.controls[0]
        assert isinstance(header, ft.Column)
        assert any(getattr(t, "value", "").startswith("Consulta directa") for t in header.controls)
        # El body arranca con el bloque de filtros (Column de filters_block),
        # no con una fila suelta.
        assert isinstance(p._body.content.controls[0], ft.Column)


class TestFiltroSku:
    """Filtro SKU de la vista previa (vendedor + cliente + rango + SKU)."""

    def test_parse_skus(self):
        from src.ui.reporte_compras import _parse_skus

        assert _parse_skus("78339") == ["78339"]
        assert _parse_skus("78339, 0078339 123;78339") == ["78339", "0078339", "123"]
        assert _parse_skus("") == []
        assert _parse_skus(None) == []

    def test_filtrar_es_estricto_011019_vs_11019(self):
        """011019 y 11019 son productos distintos: coincidencia exacta."""
        from src.ui.reporte_compras import _filtrar_por_sku

        df = pd.DataFrame(
            {"COD_SKU": ["011019", "11019", "08034", "78339"], "SOLES": [1.0, 2.0, 3.0, 4.0]}
        )
        assert _filtrar_por_sku(df, ["11019"])["COD_SKU"].tolist() == ["11019"]
        assert _filtrar_por_sku(df, ["011019"])["COD_SKU"].tolist() == ["011019"]
        assert _filtrar_por_sku(df, ["08034"])["COD_SKU"].tolist() == ["08034"]
        assert _filtrar_por_sku(df, ["8034"]).empty
        assert _filtrar_por_sku(df, []) is df

    def test_sugerir_variante_ceros(self):
        from src.ui.reporte_compras import _sugerir_variante_ceros

        existentes = ["011019", "11019", "08034", "78339"]
        assert _sugerir_variante_ceros(["11019"], existentes) == ["011019"]
        assert _sugerir_variante_ceros(["011019"], existentes) == ["11019"]
        # El exacto no se sugiere a si mismo; sin variantes, vacio.
        assert _sugerir_variante_ceros(["78339"], existentes) == []
        assert _sugerir_variante_ceros(["999"], existentes) == []

    def test_tabla_por_sku_muestra_codigo_completo(self):
        from src.ui.reporte_compras import _construir_tabla

        df = pd.DataFrame(
            [
                {
                    "MES_REF": "2026-01",
                    "COD_SKU": "78339",
                    "SKU": "ITEM X",
                    "SOLES": 100.0,
                    "CANTIDAD": 10.0,
                },
                {
                    "MES_REF": "2026-01",
                    "COD_SKU": "014850",
                    "SKU": "ITEM Y",
                    "SOLES": 50.0,
                    "CANTIDAD": 5.0,
                },
            ]
        )
        tabla = _construir_tabla(df, col_cod="COD_SKU", col_nom="SKU", codigo_completo=True)
        headers = [c.label.controls[0].value for c in tabla.columns[1:-1]]
        assert headers == ["78339", "014850"]
        # Modo clásico intacto: sufijo de línea.
        df_l = pd.DataFrame(
            [
                {
                    "MES_REF": "2026-01",
                    "COD_LINEA": "0101",
                    "LINEA": "GASEOSAS",
                    "SOLES": 100.0,
                    "CANTIDAD": 10.0,
                },
            ]
        )
        tabla_l = _construir_tabla(df_l)
        assert tabla_l.columns[1].label.controls[0].value == "01"

    def test_panel_con_campo_sku_y_estado(self):
        p = _panel()
        p.construir_card()
        assert p.state["skus"] == []
        # El campo SKU vive en el bloque de filtros complementarios.
        assert p.sku_field in _controles(p._body.content.controls[0])

    def test_celda_muestra_precio_unitario(self):
        from src.ui.reporte_compras import _construir_tabla, _pu_txt

        assert _pu_txt(100.0, 10.0) == "P.U. 10.00000"
        assert _pu_txt(100.0, 0.0) == "P.U. —"
        df = pd.DataFrame(
            [
                {
                    "MES_REF": "2026-01",
                    "COD_SKU": "78339",
                    "SKU": "ITEM X",
                    "SOLES": 100.0,
                    "CANTIDAD": 10.0,
                },
            ]
        )
        tabla = _construir_tabla(df, col_cod="COD_SKU", col_nom="SKU", codigo_completo=True)
        celda = tabla.rows[0].cells[1].content.controls
        assert [t.value for t in celda][2] == "P.U. 10.00000"
        # Fila TOTAL tambien lo trae.
        total = tabla.rows[-1].cells[1].content.controls
        assert [t.value for t in total][2] == "P.U. 10.00000"

    def test_helper_section_header_sub(self):
        from src.core.g360_theme import G360Theme

        col = G360Theme.section_header_sub(1, "T", "sub")
        assert len(col.controls) == 2
        assert col.controls[1].value == "sub"
        solo = G360Theme.section_header_sub(1, "T")
        assert len(solo.controls) == 1


class TestControlFactory:
    """Compras y Búsqueda comparten el mismo estilo de filtros (simetría)."""

    def test_dropdown_estilo_unico(self):
        from src.ui.widgets import control_factory as cf

        dd = cf.dropdown("X", icon="i", expand=True, search=True)
        assert dd.border_radius == cf.RADIUS
        assert dd.dense is True
        assert dd.text_size == cf.TEXT_SIZE
        assert dd.expand is True
        assert dd.enable_search is True

    def test_dropdown_editable_y_width(self):
        from src.ui.widgets import control_factory as cf

        dd = cf.dropdown("X", width=230, editable=True, on_change=lambda e: None)
        assert dd.width == 230
        assert dd.editable is True
        assert dd.on_change is not None

    def test_date_label_activo_vs_todas(self):
        from src.core.g360_theme import G360Theme
        from src.ui.widgets import control_factory as cf

        on = cf.date_label("Desde: 01-01-2026", active=True)
        off = cf.date_label("Hasta: todas", active=False)
        assert on.color == G360Theme.accent_text_color()
        assert off.color == G360Theme.text_muted_color()

    def test_search_button_y_date_button(self):
        from src.ui.widgets import control_factory as cf

        b = cf.search_button("Buscar cliente", "icon")
        assert b.height == cf.HEIGHT
        d = cf.date_button(lambda e: None, "Desde")
        assert d.height == cf.HEIGHT

    def test_panel_usa_la_fabrica(self):
        """Los filtros de la card de compras salen con el estilo compartido."""
        from src.ui.widgets import control_factory as cf

        p = _panel()
        p.construir_card()
        assert p.vend_dd.border_radius == cf.RADIUS
        assert p.btn_buscar_cli.height == cf.HEIGHT
        from src.core.g360_theme import G360Theme

        assert p.desde_label.size == G360Theme.TYPE_BODY


class TestGeometriaHorizontal:
    """Presupuesto horizontal con la ventana mínima (960 px)."""

    def test_presupuesto_minimo(self):
        # 950 (main.py: min_width) − 64 (vista 32+32) − 32 (card 16+16) − 16 (cuerpo 8+8)
        assert rc.ANCHO_UTIL_MIN == 838

    def test_tabla_hasta_8_lineas_cabe(self):
        for n in (1, 4, 8):
            assert rc.ancho_estimado_tabla(n) <= rc.ANCHO_UTIL_MIN, n

    def test_tabla_muchas_lineas_scrollea(self):
        # Con 15 líneas excede el ancho: la fila tiene scroll horizontal
        assert rc.ancho_estimado_tabla(15) > rc.ANCHO_UTIL_MIN

    def test_fila_vendedor_mide_como_el_de_busqueda(self):
        """Mismo ancho que el vendedor de Búsqueda: WIDTH_FILTER, no elástico.

        Este test antes afirmaba `expand is True` con el comentario "igual que el
        de Búsqueda: ocupa el ancho disponible", pero la premisa era falsa: el
        de Búsqueda nunca fue elástico, tenía width=260. Los dos eran el mismo
        control con medidas distintas.

        Y "elástico" tampoco era lo que queremos: con expand cada dropdown se
        estira a lo que sobre en SU card, y como las cards tienen anchos
        distintos seguian viéndose distintos. Un ancho fijo si garantiza la
        igualdad, que es lo que se pidió.
        """
        from src.ui.widgets import control_factory as cf

        p = _panel()
        p.construir_card()
        assert p.vend_dd.width == cf.WIDTH_FILTER
        assert p.vend_dd.expand is False
        assert p.vend_dd.border_radius == cf.RADIUS
        assert p.vend_dd.height is None  # ft.Dropdown no lleva height

    def test_chips_hojas_igual_cliente(self):
        """Chips de hojas con el mismo estilo que los chips de cliente."""
        from src.core.g360_theme import G360Theme

        p = _panel()
        p.construir_card()
        chip = p._chips["consolidado"]
        assert chip.padding == ft.padding.symmetric(horizontal=8, vertical=2)
        label = chip.label
        assert label.size == G360Theme.TYPE_SUBTITLE

    def test_cuerpo_padding_simetrico(self):
        p = _panel()
        p.construir_card()
        pad = p._body.padding
        assert pad.left == pad.right == 8

    def test_body_ordenado_filtros_rango_hojas_acciones_salida(self):
        """El cuerpo va: bloque de filtros, divisor, acciones, status, salida.

        Los filtros usan el esqueleto de 3 bloques (filters_block):
        principales (vendedor + clientes + rango), complementarios (SKU) y
        salida (NC + hojas a exportar).
        """
        import flet as ft

        p = _panel()
        p.construir_card()
        cols = p._body.content.controls
        # 0 bloque de filtros, 1 divisor, 2 acciones, 3 status, 4 resultados
        bloque = cols[0]
        assert isinstance(bloque, ft.Column)

        # Cada bloque de filters_block es un par (header, contenido); el
        # contenido se envuelve en Column solo si el bloque trae mas de un
        # control.
        assert len(bloque.controls) == 6  # 3 pares header + contenido

        def _bloque_de(ctrl):
            """Header y contenido del bloque que contiene `ctrl`."""
            for i, header in enumerate(bloque.controls[:-1]):
                contenido = bloque.controls[i + 1]
                if ctrl in _controles(contenido):
                    return header, contenido
            raise AssertionError(f"{ctrl} no esta en ningun bloque")

        # Orden: uso cotidiano, criterio especifico, empaque.
        h_principales, c_principales = _bloque_de(p.vend_dd)
        h_complementarios, c_complementarios = _bloque_de(p.sku_field)
        h_salida, c_salida = _bloque_de(p.nc_sw)
        assert bloque.controls.index(h_principales) < bloque.controls.index(h_complementarios)
        assert bloque.controls.index(h_complementarios) < bloque.controls.index(h_salida)

        # El SKU ya no compite en la fila del vendedor: el vendedor y sus
        # clientes van en una Row de principales; el SKU, en otro bloque.
        controles_principales = _controles(c_principales)
        assert p.vend_dd in controles_principales
        assert p.btn_buscar_cli in controles_principales
        assert p.cli_chips in c_principales.controls
        assert p.sku_field not in controles_principales
        assert p.sku_field in _controles(c_complementarios)
        assert p.nc_sw in _controles(c_salida)

        assert isinstance(cols[1], ft.Divider)
        assert isinstance(cols[2], ft.Row)
        assert p.btn_generar in cols[2].controls
        assert p.btn_exportar in cols[2].controls
        assert cols[2].alignment == ft.MainAxisAlignment.END

    def test_jerarquia_de_los_dos_pasos(self):
        """ "Ver datos" consulta (secundario) y "Generar Excel" escribe (primario).

        Antes los dos eran accent relleno con rótulos indistinguibles; el peso
        visual es lo que dice cuál termina el flujo.
        """
        import flet as ft

        p = _panel()
        p.construir_card()

        assert p.btn_generar.text == "Ver datos"
        assert isinstance(p.btn_generar, ft.OutlinedButton), "el paso 1 es secundario"
        assert p.btn_exportar.text == "Generar Excel"
        assert isinstance(p.btn_exportar, ft.ElevatedButton), "el paso 2 es primario"
        # El primario lleva el relleno de acento; el secundario, solo borde.
        assert p.btn_exportar.style.bgcolor is not None
        assert p.btn_generar.style.side is not None

    def test_fila_filtros_sin_wrap_ni_anidadas(self):
        """Regresión del bloque gris: la fila con el dropdown elástico no
        usa wrap ni contiene filas anidadas (rompe el layout en Flutter)."""
        import flet as ft

        p = _panel()
        p.construir_card()
        # El bloque de filtros es una Column con un header + las filas.
        bloque = p._body.content.controls[0]
        filas = [c for c in bloque.controls if isinstance(c, ft.Row)]
        assert filas, "el bloque de filtros debe traer sus filas"
        for fila in filas:
            # La fila del vendedor es la unica sin wrap (dropdown elastico).
            if p.vend_dd in fila.controls:
                assert fila.wrap is not True
                assert not any(isinstance(c, ft.Row) for c in fila.controls)

    def test_fila_rango_cabe(self):
        # icono 14 + labels ~110×2 + botones 32×2 + switch ~180 + spacings
        assert 14 + 110 + 32 + 110 + 32 + 180 + 5 * 8 <= rc.ANCHO_UTIL_MIN

    def test_fila_acciones_alineada_derecha(self):
        import flet as ft

        p = _panel()
        p.construir_card()
        fila = next(
            c
            for c in p._body.content.controls
            if isinstance(c, ft.Row) and p.btn_exportar in getattr(c, "controls", [])
        )
        assert fila.alignment == ft.MainAxisAlignment.END


class _PageStub:
    def __init__(self):
        self.opened = None
        self.closed = False

    def open(self, c):
        self.opened = c

    def close(self, c=None):
        self.closed = True

    def update(self):
        pass


def _hijos(ctrl):
    """Controles anidados de un control (incluye content/title/actions)."""
    out = list(getattr(ctrl, "controls", None) or [])
    for attr in ("content", "title"):
        c = getattr(ctrl, attr, None)
        if c is not None:
            out.append(c)
    out += list(getattr(ctrl, "actions", None) or [])
    return out


def _textos_de(ctrl):
    out = []
    for attr in ("value", "label", "text", "hint_text"):
        val = getattr(ctrl, attr, None)
        if isinstance(val, str):
            out.append(val)
    for h in _hijos(ctrl):
        out += _textos_de(h)
    return out


def _buscar(ctrl, pred):
    if pred(ctrl):
        return ctrl
    for h in _hijos(ctrl):
        r = _buscar(h, pred)
        if r is not None:
            return r
    return None


class TestClientePickerSmoke:
    """El modal compartido construye y confirma (caza kwargs inválidos)."""

    def test_abre_carga_y_confirma(self, monkeypatch):
        import time as _t
        import src.ui.widgets.cliente_picker as cp

        class _CliStub:
            def fetch_clientes(self, **kw):
                return [{"id": "00056101", "nombre": "MULTICOPIAS", "docs": 3}]

            def fetch_client_by_id(self, cid):
                return None

        monkeypatch.setattr(cp, "VentasDbClient", lambda *a, **k: _CliStub())
        app = SimpleNamespace(
            page=_PageStub(), show_snackbar=lambda *a, **k: None, G360_SUCCESS="s", G360_WARNING="w"
        )
        recibido = {}
        cp.abrir_picker_clientes(app, on_confirm=lambda s: recibido.update(sel=s), multiple=True)
        dlg = app.page.opened
        assert dlg is not None
        # El campo de búsqueda se construyó (kwargs válidos de TextField)
        assert any("Buscar por nombre" in t for t in _textos_de(dlg))
        _t.sleep(0.35)  # deja correr la carga en hilo
        chk = _buscar(dlg, lambda c: c.__class__.__name__ == "Checkbox")
        assert chk is not None, "no se renderizaron filas de clientes"
        chk.value = True
        chk.on_change(SimpleNamespace(control=chk))
        btn = _buscar(dlg, lambda c: getattr(c, "text", "") == "Agregar seleccionados")
        assert btn is not None
        btn.on_click(SimpleNamespace())
        assert recibido.get("sel") == [{"id": "00056101", "nombre": "MULTICOPIAS"}]

    def test_confirmar_sin_seleccion_avisa(self, monkeypatch):
        import src.ui.widgets.cliente_picker as cp

        class _CliStub:
            def fetch_clientes(self, **kw):
                return []

            def fetch_client_by_id(self, cid):
                return None

        monkeypatch.setattr(cp, "VentasDbClient", lambda *a, **k: _CliStub())
        avisos = []
        app = SimpleNamespace(
            page=_PageStub(),
            show_snackbar=lambda m, *a, **k: avisos.append(m),
            G360_SUCCESS="s",
            G360_WARNING="w",
        )
        cp.abrir_picker_clientes(app, on_confirm=lambda s: None, multiple=True)
        dlg = app.page.opened
        btn = _buscar(dlg, lambda c: getattr(c, "text", "") == "Agregar seleccionados")
        btn.on_click(SimpleNamespace())
        assert any("al menos un cliente" in m for m in avisos)

    def test_sin_clientes_muestra_estado_vacio_en_lista(self, monkeypatch):
        import time as _t
        import src.ui.widgets.cliente_picker as cp

        class _CliStub:
            def fetch_clientes(self, **kw):
                return []

            def fetch_client_by_id(self, cid):
                return None

        monkeypatch.setattr(cp, "VentasDbClient", lambda *a, **k: _CliStub())
        app = SimpleNamespace(
            page=_PageStub(), show_snackbar=lambda *a, **k: None, G360_SUCCESS="s", G360_WARNING="w"
        )
        cp.abrir_picker_clientes(app, on_confirm=lambda _s: None, multiple=True)
        _t.sleep(0.35)
        textos = _textos_de(app.page.opened)
        assert any("No hay clientes con ventas" in t for t in textos)


class TestResetGeneral:
    def test_reset_general_colapsa(self):
        from src.ui.view_handlers import _ViewHandlers

        class _StubPanel:
            def __init__(self):
                self.calls = []

            def limpiar(self, colapsar=False):
                self.calls.append(colapsar)

        vista = SimpleNamespace(reporte_panel=_StubPanel())
        _ViewHandlers._reset_ui.__get__(vista)()
        assert vista.reporte_panel.calls == [True]

    def test_reset_sin_panel_no_rompe(self):
        from src.ui.view_handlers import _ViewHandlers

        vista = SimpleNamespace()
        _ViewHandlers._reset_ui.__get__(vista)()  # hasattr-guarded
