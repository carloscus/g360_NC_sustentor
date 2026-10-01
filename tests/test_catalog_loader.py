"""Tests del cargador de catálogo YAML y la extensibilidad por configuración.

Cubre la API de CatalogLoader (src/core/catalog_loader.py) y la fusión del
catálogo de UI (src/ui/catalog.py) desde los bloques ``caso`` del YAML: un
proceso nuevo declarado en catalog/processes.yaml debe sumarse al catálogo
sin tocar el código.
"""

import pytest

from src.core.catalog_loader import CatalogLoader
from src.ui import catalog as ui_catalog


class TestCatalogLoader:
    def test_carga_procesos(self):
        c = CatalogLoader()
        ps = c.listar_procesos()
        # 9 procesos: CDT se fusionó dentro de VRS (diferencia_stock) y
        # DF (devolucion_fisica) suma su propio proceso.
        assert len(ps) == 9
        assert "diferencia_cantidad" not in [p["key"] for p in ps]
        assert c.errores == []

    def test_schema_diferencia_stock_combined(self):
        """VRS declara el schema combined: SKU + cantidad + precio + descuentos."""
        c = CatalogLoader()
        s = c.obtener_schema("diferencia_stock")
        assert s, "el schema de diferencia_stock no puede estar vacio"
        assert s["columnas_requeridas"] == ["CODIGO", "PRECIO_BASE", "CANTIDAD_NC"]
        assert "PRECIO _LISTA" in s["header_map"]  # alias de plantilla
        assert s["header_map"]["CANTIDAD"] == "CANTIDAD_NC"
        assert s["columnas_descuento"]["pattern"] == "^DESC\\d+$"

    def test_alias_cantidad_resuelve_a_vrs(self):
        """`diferencia_cantidad` es legacy type de VRS: el loader lo resuelve."""
        c = CatalogLoader()
        assert c.obtener_schema("diferencia_cantidad") == c.obtener_schema("diferencia_stock")
        assert c.obtener_proceso("diferencia_cantidad")["strategy"] == "CantidadDeterminada"
        # VRS declara ambos legacy types y el caso es canónico.
        caso = c.obtener_proceso("diferencia_stock")["caso"]
        assert caso["codigo"] == "VRS"
        assert caso["legacy_types"] == ["diferencia_stock", "diferencia_cantidad"]
        assert "CDT" not in ui_catalog.CATALOGO

    def test_obtener_diferencia_precio(self):
        c = CatalogLoader()
        p = c.obtener_proceso("diferencia_precio")
        assert p["strategy"] == "PriceDifference"
        assert "PRECIO_BASE" in p["template"]["columnas_requeridas"]

    def test_obtener_schema(self):
        c = CatalogLoader()
        s = c.obtener_schema("bonificacion_promocion")
        assert "PRECIO_UNITARIO" in s["columnas_requeridas"]

    def test_ruta_inexistente_degrada_elegancia(self, tmp_path):
        c = CatalogLoader(ruta=str(tmp_path / "no_existe.yaml"))
        assert c.listar_procesos() == []
        assert c.listar_casos() == []
        assert len(c.errores) >= 1

    def test_ruta_invalida_estricto_levanta(self, tmp_path):
        malo = tmp_path / "malo.yaml"
        malo.write_text("key: [invalido\n", encoding="utf-8")
        with pytest.raises(ValueError):
            CatalogLoader(ruta=str(malo), estricto=True)


class TestCatálogoUI:
    def test_casos_de_yaml_se_fusionan(self):
        casos = ui_catalog._casos_desde_yaml()
        assert casos  # el YAML debe declarar casos
        assert "DC" in casos and "VRS" in casos
        # Los casos YAML alimentan el catálogo final
        for codigo in casos:
            assert codigo in ui_catalog.CATALOGO

    def test_catalogo_final_mantiene_los_8_base(self):
        assert set(ui_catalog.ORDEN_CASOS) == {
            "DC",
            "DO",
            "VRS",
            "FPE",
            "PROM",
            "ANF",
            "CMV",
            "DF",
        }
        # VRS absorbe a CDT: un solo caso, un solo motor.
        assert ui_catalog.CATALOGO["VRS"].strategy == "diferencia_stock"
        assert ui_catalog.caso_de_legacy("diferencia_cantidad") == "VRS"
        assert ui_catalog.caso_de_legacy("diferencia_stock") == "VRS"

    def test_casos_yaml_coinciden_con_base(self):
        """Los bloques caso del YAML deben reproducir los valores de la base."""
        for codigo, caso in ui_catalog.CATALOGO.items():
            base = ui_catalog._BASE_CATALOGO.get(codigo)
            if base is None:
                continue  # códigos nuevos declarados solo en YAML
            assert caso.insumos == base.insumos
            assert caso.legacy_types == base.legacy_types
            assert caso.strategy == base.strategy
            assert caso.modalidades == base.modalidades
            assert caso.resultado == base.resultado
            assert caso.historico_default == base.historico_default

    def test_legacy_a_caso_completo(self):
        assert ui_catalog.caso_de_legacy("diferencia_precio") == "DC"
        assert ui_catalog.caso_de_legacy("rebate_volumen") == "CMV"
        assert ui_catalog.caso_de_legacy("devolucion_fisica") == "DF"


class TestExtensibilidad:
    def test_caso_futuro_se_agrega_al_catalogo(self, monkeypatch):
        """Un caso nuevo (codigo XXX) se suma a CATALOGO sin romper la base."""
        from src.ui.catalog import Caso

        def fake_casos_yaml():
            from src.ui.catalog import MODALIDAD_INDIVIDUAL

            return {
                "XXX": Caso(
                    codigo="XXX",
                    label="Caso futuro declarado en YAML",
                    insumos=("historico", "lista_precios"),
                    resultado="NC",
                    legacy_types=("caso_nuevo",),
                    strategy="caso_nuevo",
                    modalidades=(MODALIDAD_INDIVIDUAL,),
                    historico_default={"facturas": (True, True)},
                ),
            }

        monkeypatch.setattr(ui_catalog, "_casos_desde_yaml", fake_casos_yaml)
        fusion = ui_catalog._construir_catalogo()
        assert "XXX" in fusion
        assert fusion["XXX"].label == "Caso futuro declarado en YAML"
        assert fusion["XXX"].modalidades == ("individual",)
        # La base sigue intacta con los 8 originales
        for base_codigo in ui_catalog._ORDEN_BASE:
            assert base_codigo in fusion

    def test_fusion_fallo_no_rompe_base(self, monkeypatch):
        """Si leer el YAML falla, el catálogo degrada a la lista base."""

        def explotar():
            raise RuntimeError("YAML corrupto")

        monkeypatch.setattr(ui_catalog, "_casos_desde_yaml", explotar)
        fusion = ui_catalog._construir_catalogo()
        assert set(fusion) == set(ui_catalog._BASE_CATALOGO)
