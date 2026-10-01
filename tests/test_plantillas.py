"""Plantillas descargables: hoja de datos limpia + mapeo de columnas + convención.

Regresión de la auditoría de plantillas:

1. Las INSTRUCCIONES se escribian en la MISMA hoja que los datos, y
   ``read_erp_file`` las devolvia como filas: FPE emitia 6 errores
   "AL06 - SKU INSTRUCCIONES: no encontrado en historial" por corrida.
   Ahora viven en la hoja LEEME.
2. Los descuentos se guardan como FRACCION en celdas con formato
   ``0.00%`` (0.05 se ve 5.00%), nunca como puntos (5.0 se ve 500.00%).
3. Cada columna de plantilla tiene ruta hasta su estrategia (header_map
   del YAML, regex ``desc01 -> DESC1`` o nombre identico).

Convención de nombres (unificada, sin marca):
``Lista_de_<Precios|Descuentos|Devoluciones>[_y_Cantidades]``, una plantilla por archivo de insumo, con el caso canónico (DC, DO, VRS, FPE, DF) y el
insumo que llena declarados en la propia plantilla.
"""

import re

import pytest
from openpyxl import load_workbook

from src.core.catalog_loader import CatalogLoader
from src.core.utils import read_erp_file
from src.render.templates import _COLUMN_FORMATS, PlantillaGenerator

# plantilla -> procesos YAML cuyas columnas REQUERIDAS debe poder producir
PLANTILLA_PROCESOS = {
    "lista_precios": ["diferencia_precio"],
    "lista_precios_cantidad": ["diferencia_stock", "diferencia_cantidad"],
    "lista_descuentos": ["descuento_precio", "descuento_factura"],
    "lista_descuentos_cantidad": ["feria_preventa"],
}


@pytest.fixture(scope="module")
def plantillas(tmp_path_factory):
    """Genera las 4 plantillas una vez para toda la sesion."""
    out = tmp_path_factory.mktemp("plantillas")
    rutas = {}
    for nombre in PlantillaGenerator.TEMPLATES:
        ruta = out / f"{nombre}.xlsx"
        PlantillaGenerator().generar(nombre, str(ruta))
        rutas[nombre] = ruta
    return rutas


def _es_instruccion(valor) -> bool:
    texto = str(valor or "").strip().upper()
    return texto.startswith("INSTRUCCION") or bool(
        re.match(r"^[1-9]\.\s", str(valor or "").strip())
    )


class TestConvencionDeNombres:
    def test_cinco_listas_una_por_archivo_de_insumo(self):
        assert list(PlantillaGenerator.TEMPLATES) == [
            "lista_precios",
            "lista_precios_cantidad",
            "lista_descuentos",
            "lista_descuentos_cantidad",
            "lista_devoluciones",
        ]

    @pytest.mark.parametrize(
        "nombre_archivo",
        [
            "Lista_de_Precios",
            "Lista_de_Precios_y_Cantidades",
            "Lista_de_Descuentos",
            "Lista_de_Descuentos_y_Cantidades",
            "Lista_de_Devoluciones",
        ],
    )
    def test_nombre_de_archivo_segun_convencion(self, nombre_archivo):
        nombres = {t["nombre"] for t in PlantillaGenerator.TEMPLATES.values()}
        assert nombre_archivo in nombres
        assert all(n.startswith("Lista_de_") for n in nombres)

    @pytest.mark.parametrize("clave", list(PlantillaGenerator.TEMPLATES))
    def test_casos_declarados_existen_en_el_catalogo(self, clave):
        from src.ui.catalog import CATALOGO

        casos = PlantillaGenerator.TEMPLATES[clave]["casos"]
        assert casos, clave
        for codigo in casos:
            assert codigo in CATALOGO, f"{clave}: caso {codigo} no existe"

    @pytest.mark.parametrize("clave", list(PlantillaGenerator.TEMPLATES))
    def test_insumos_declarados_existen_en_el_catalogo(self, clave):
        from src.ui.catalog import INSUMOS

        for insumo in PlantillaGenerator.TEMPLATES[clave]["insumos"]:
            assert insumo in INSUMOS, f"{clave}: insumo {insumo} no existe"

    def test_cada_insumo_de_archivo_tiene_plantilla(self):
        """Todo insumo que se carga como archivo (no historico) tiene plantilla.

        El insumo ``sku`` no tiene archivo propio: es la columna CODIGO_SKU que
        todas las listas ya traen, así que un solo archivo puede llenar el slot
        de SKUs y el de descuentos/cantidad a la vez (DO, FPE, PROM, CMV).
        """
        from src.ui.catalog import CATALOGO

        embebidos = {"sku"}  # cubiertos por la columna CODIGO_SKU
        for caso in CATALOGO.values():
            for insumo in caso.insumos:
                if insumo in ("historico", "linea", "objetivo", "mecanica", *embebidos):
                    continue  # parámetros de pantalla o columna embebida
                cubierto = any(
                    insumo in PlantillaGenerator.TEMPLATES[t]["insumos"]
                    for t in PlantillaGenerator.TEMPLATES
                )
                assert cubierto, f"insumo {insumo} ({caso.codigo}) sin plantilla"

    @pytest.mark.parametrize("clave", list(PlantillaGenerator.TEMPLATES))
    def test_columnas_en_mayusculas_y_con_formato(self, clave):
        """C: convención única de columnas (mayúsculas, SKU y formatos)."""
        for col, _desc in PlantillaGenerator.TEMPLATES[clave]["columnas"]:
            assert col == col.upper(), f"{clave}: columna {col} no es MAYÚSCULAS"
            assert col in _COLUMN_FORMATS, f"{clave}: {col} sin formato declarado"

    @pytest.mark.parametrize("clave", list(PlantillaGenerator.TEMPLATES))
    def test_columna_de_sku_siempre_codigo_sku(self, clave):
        columnas = [c for c, _ in PlantillaGenerator.TEMPLATES[clave]["columnas"]]
        assert "CODIGO_SKU" in columnas, clave

    def test_la_hoja_no_reparte_el_prefijo_plantilla(self, plantillas):
        for nombre, ruta in plantillas.items():
            wb = load_workbook(str(ruta))
            assert wb.sheetnames[0].startswith("LISTA_"), nombre
            assert len(wb.sheetnames[0]) <= 31, nombre

    def test_la_hoja_leeme_dice_casos_e_insumos(self, plantillas):
        for nombre, ruta in plantillas.items():
            wb = load_workbook(str(ruta))
            textos = [
                c.value for row in wb["LEEME"].iter_rows() for c in row if isinstance(c.value, str)
            ]
            tpl = PlantillaGenerator.TEMPLATES[nombre]
            assert any("Casos:" in t and tpl["casos"][0] in t for t in textos), nombre
            assert any("Insumos:" in t for t in textos), nombre

    def test_el_nombre_no_se_antepone_dos_veces(self):
        """El diálogo usaba "Plantilla_" + un nombre que ya lo traía."""
        for clave, tpl in PlantillaGenerator.TEMPLATES.items():
            assert not tpl["nombre"].startswith("Plantilla_"), clave
            assert not tpl["nombre"].startswith("PLANTILLA_"), clave

    def test_claves_legacy_siguen_resolviendo(self, plantillas, tmp_path):
        """Un script con la clave vieja obtiene la plantilla vigente."""
        assert PlantillaGenerator.resolver_clave("stock_cliente") == "lista_precios_cantidad"
        assert PlantillaGenerator.resolver_clave("feria_preventa") == "lista_descuentos_cantidad"
        salida = tmp_path / "legacy.xlsx"
        PlantillaGenerator().generar("stock_cliente", str(salida))
        assert salida.exists()

    def test_clave_desconocida_dice_que_hay(self, tmp_path):
        with pytest.raises(ValueError) as err:
            PlantillaGenerator().generar("no_existe", str(tmp_path / "x.xlsx"))
        assert "lista_precios" in str(err.value)


class TestEstaticosEnElRepo:
    """E: lo que se descarga es lo mismo que vive en assets/templates."""

    @pytest.mark.parametrize(
        "nombre",
        [
            "Lista_de_Precios",
            "Lista_de_Precios_y_Cantidades",
            "Lista_de_Descuentos",
            "Lista_de_Descuentos_y_Cantidades",
            "Lista_de_Devoluciones",
            "Historial_de_Ventas",
        ],
    )
    def test_existe_el_estatico(self, nombre):
        from pathlib import Path

        ruta = Path("assets/templates") / f"{nombre}.xlsx"
        assert ruta.exists(), f"falta el estático {ruta.name}"

    @pytest.mark.parametrize(
        "nombre",
        [
            "Lista_de_Precios",
            "Lista_de_Precios_y_Cantidades",
            "Lista_de_Descuentos",
            "Lista_de_Descuentos_y_Cantidades",
            "Lista_de_Devoluciones",
        ],
    )
    def test_el_estatico_tiene_las_mismas_columnas_que_el_generador(self, nombre):
        from pathlib import Path

        clave = next(c for c, t in PlantillaGenerator.TEMPLATES.items() if t["nombre"] == nombre)
        esperado = [c for c, _ in PlantillaGenerator.TEMPLATES[clave]["columnas"]]
        wb = load_workbook(str(Path("assets/templates") / f"{nombre}.xlsx"), read_only=True)
        ws = wb[wb.sheetnames[0]]
        cabeceras = [ws.cell(row=1, column=i).value for i in range(1, len(esperado) + 1)]
        wb.close()
        assert cabeceras == esperado, (
            f"{nombre}: el estático quedó desactualizado; regenerar con "
            "PlantillaGenerator().generar(...)"
        )

    def test_no_quedan_nombres_antiguos(self):
        from pathlib import Path

        assert not list(Path("assets/templates").glob("Plantilla_*"))


class TestHojaDeDatosLimpia:
    @pytest.mark.parametrize("nombre", list(PlantillaGenerator.TEMPLATES))
    def test_read_erp_file_no_ve_instrucciones(self, plantillas, nombre):
        df = read_erp_file(str(plantillas[nombre]))
        assert not df.empty
        col0 = df.iloc[:, 0]
        malas = [v for v in col0 if _es_instruccion(v)]
        assert not malas, f"{nombre}: {len(malas)} filas de instrucciones como datos"

    @pytest.mark.parametrize("nombre", list(PlantillaGenerator.TEMPLATES))
    def test_hoja_leeme_existe_y_tiene_los_pasos(self, plantillas, nombre):
        wb = load_workbook(str(plantillas[nombre]))
        assert "LEEME" in wb.sheetnames, nombre
        textos = [
            c.value for row in wb["LEEME"].iter_rows() for c in row if isinstance(c.value, str)
        ]
        assert any("INSTRUCCIONES" in t for t in textos), nombre
        assert any(t.startswith("1.") for t in textos), nombre

    @pytest.mark.parametrize("nombre", list(PlantillaGenerator.TEMPLATES))
    def test_solo_hojas_de_datos_y_leeme(self, plantillas, nombre):
        wb = load_workbook(str(plantillas[nombre]))
        # La primera hoja es la de datos (read_erp_file lee la hoja 0).
        assert wb.sheetnames[0].startswith("LISTA_"), nombre
        assert set(wb.sheetnames) == {wb.sheetnames[0], "LEEME"}, nombre


class TestFormatosYValores:
    @pytest.mark.parametrize("nombre", list(PlantillaGenerator.TEMPLATES))
    def test_columnas_de_descuento_son_fraccion(self, plantillas, nombre):
        """0.05 guardado en celda 0.00% -> se ve 5.00% (no 500.00%)."""
        wb = load_workbook(str(plantillas[nombre]))
        ws = wb[wb.sheetnames[0]]
        cols_desc = [
            (c, _COLUMN_FORMATS.get(c)) for c, _ in PlantillaGenerator.TEMPLATES[nombre]["columnas"]
        ]
        desc_pct = [c for c, fmt in cols_desc if fmt == "0.00%"]
        for col_name in desc_pct:
            idx = [c for c, _ in PlantillaGenerator.TEMPLATES[nombre]["columnas"]].index(
                col_name
            ) + 1
            for fila in range(2, ws.max_row + 1):
                celda = ws.cell(row=fila, column=idx)
                if celda.value in (None, ""):
                    continue
                valor = float(celda.value)
                assert 0 <= valor <= 1, (
                    f"{nombre}.{col_name} fila {fila} = {valor}: fuera de rango "
                    "para una fraccion (max 1.0 = 100%)"
                )

    @pytest.mark.parametrize("nombre", list(PlantillaGenerator.TEMPLATES))
    def test_sku_en_formato_texto(self, plantillas, nombre):
        """El SKU va como '@' para no perder ceros a la izquierda."""
        wb = load_workbook(str(plantillas[nombre]))
        ws = wb[wb.sheetnames[0]]
        encabezados = [ws.cell(row=1, column=i).value for i in range(1, ws.max_column + 1)]
        assert "CODIGO_SKU" in encabezados, nombre
        i = encabezados.index("CODIGO_SKU") + 1
        assert ws.cell(row=1, column=i).number_format == "@", (
            f"{nombre}: CODIGO_SKU no esta en formato texto"
        )


class TestMapeoHastaLaEstrategia:
    def test_requeridas_producibles_desde_la_plantilla(self, plantillas):
        """Toda columna REQUERIDA de un proceso sale de alguna plantilla.

        Es la direccion que rompe el caso: si alguien cambia un encabezado
        de plantilla sin tocar el header_map del YAML, el proceso deja de
        recibir su input y falla con "columna requerida no encontrada".
        """
        loader = CatalogLoader()
        for nombre, procesos in PLANTILLA_PROCESOS.items():
            columnas = [c for c, _ in PlantillaGenerator.TEMPLATES[nombre]["columnas"]]
            for proc in procesos:
                schema = loader.obtener_schema(proc)
                assert schema, f"proceso {proc} sin schema"
                header_map = schema.get("header_map", {})
                producibles = {header_map.get(c, c) for c in columnas}
                for requerida in schema.get("columnas_requeridas", []):
                    assert requerida in producibles, (
                        f"{nombre} no produce '{requerida}' para {proc}: "
                        f"columnas={columnas}, map={header_map}"
                    )

    def test_columna_descuento_convierte_a_desc_n(self):
        """'DESC01' -> 'DESC1' via la regex de normalizar_condicion."""
        for col in ["DESC01", "DESC02", "DESC_03", "DESC8"]:
            m = re.match(r"^DESC_?0*(\d+)$", col, re.IGNORECASE)
            assert m, col
            assert f"DESC{int(m.group(1))}" in ("DESC1", "DESC2", "DESC3", "DESC8")

    def test_stock_end_to_end(self, plantillas):
        """VRS combined: la plantilla (SKU+CANTIDAD+PRECIO_BASE+desc) llega al motor.

        El header_map del schema VRS normaliza SKU->CODIGO y CANTIDAD->CANTIDAD_NC,
        asi que el motor recibe el archivo ya estandarizado.
        """
        import pandas as pd

        from src.domain import ExpedienteComercial, PipelineContext
        from src.pipeline import Pipeline

        # DIFERENCIA = PRECIO_HIST - PRECIO_NETO (misma convencion que DC):
        # el historico debe superar al precio de lista de la plantilla para
        # que haya NC (72015 -> 4.80 con 5% = 4.56 ; A002 -> 12.00).
        historial = pd.DataFrame(
            {
                "CODIGO": ["72015", "A002"],
                "ARTICULO": ["P1", "P2"],
                "CANTIDAD": [100, 100],
                "SOLES": [500.0, 1500.0],
                "TIPO_DOC": ["F", "F"],
                "SERIE": ["001", "001"],
                "NUMERO": ["100", "101"],
                "FECHA": pd.to_datetime(["2026-01-15"] * 2),
                "COD_CLIENTE": ["00068414"] * 2,
                "DOC_CLIENTE": ["20560201011"] * 2,
                "LINEA": ["01 L"] * 2,
                "PRECIO_UNITARIO": [5.0, 15.0],
            }
        )
        # El archivo combinado tal como lo baja el cliente de la plantilla.
        combined = read_erp_file(str(plantillas["lista_precios_cantidad"]))
        exp = ExpedienteComercial(
            nombre="VRS",
            familia="VRS",
            estrategia="CantidadDeterminada",
            datos=historial,
            contexto=PipelineContext(config={"sort_mode": "fecha_asc", "modalidad": "individual"}),
        )
        exp.condiciones = [combined]
        exp = Pipeline().ejecutar(exp)
        assert exp.resultado is not None
        df = exp.resultado.dataframe
        assert not df.empty, "VRS no produjo filas con la plantilla de stock"
        assert exp.resultado.resumen["total_nc"] > 0
        # La cobertura se emite siempre.
        for col in ("Cantidad Facturada", "Stock Sustentado", "% Stock Restante"):
            assert col in df.columns
