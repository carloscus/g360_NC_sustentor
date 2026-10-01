"""Convención de nombres: expedientes, sus documentos y las plantillas.

Una sola fuente de verdad: ``src/ui/catalog.py`` (build_expediente_id /
build_documento_nombre) y ``src/render/templates.py`` (PlantillaGenerator).

Reglas fijadas aquí (si cambian, este archivo es el que hay que actualizar):

- Expediente: ``EXP-[CASO]-[CLIENTE]-[SERIE]-[NUMERO]-[YYYYMMDD]`` — sin
  correlativo y sin código de modalidad; el documento del ERP identifica el
  expediente y regenerarlo no crea duplicados.
- Documentos del expediente: sufijo estable Informe | Calculo | Historico |
  CalculoND sobre el id del expediente.
- Sin marca en los nombres de archivo: nada de prefijos de producto.
- Plantillas: ``Lista_de_<Precios|Descuentos>[_y_Cantidades]`` (ver
  tests/test_plantillas.py para el detalle de las 4 listas).
"""

import pytest

from src.ui.catalog import (
    CATALOGO,
    build_documento_nombre,
    build_expediente_id,
)


class TestExpedienteId:
    def test_esquema_caso_cliente_serie_numero_fecha(self):
        exp_id = build_expediente_id("DC", "00050561", "F204", "67721", fecha="20260910")
        assert exp_id == "EXP-DC-50561-F204-67721-20260910"

    def test_todos_los_casos_generan_id(self):
        for codigo in CATALOGO:
            exp_id = build_expediente_id(codigo, "00050561", "F204", "67721", fecha="20260910")
            assert exp_id.startswith(f"EXP-{codigo}-"), codigo

    def test_cliente_con_ceros_se_normaliza(self):
        """'00050561' y '50561' dan el mismo expediente (mismo cliente)."""
        a = build_expediente_id("DC", "00050561", "F204", "1", fecha="20260101")
        b = build_expediente_id("DC", "50561", "F204", "1", fecha="20260101")
        assert a == b == "EXP-DC-50561-F204-1-20260101"

    def test_misma_factura_misma_fecha_es_el_mismo_expediente(self):
        """Regenerar un caso no debe crear una carpeta nueva."""
        args = ("DO", "50561", "F204", "67721", "20260910")
        assert build_expediente_id(*args) == build_expediente_id(*args)

    def test_fecha_vacia_usa_hoy(self):
        exp_id = build_expediente_id("DC", "50561", "F204", "67721")
        assert len(exp_id.rsplit("-", 1)[-1]) == 8  # YYYYMMDD
        assert exp_id.rsplit("-", 1)[-1].isdigit()

    def test_no_lleva_marca_ni_espacios(self):
        exp_id = build_expediente_id("VRS", "50561", "F204", "67721", fecha="20260910")
        assert " " not in exp_id
        for marca in ("g360", "G360", "plantilla", "PLANTILLA"):
            assert marca not in exp_id, marca


class TestDocumentosDelExpediente:
    @pytest.mark.parametrize(
        "tipo_doc,extension",
        [
            ("Informe", "docx"),
            ("Calculo", "xlsx"),
            ("Historico", "xlsx"),
            ("CalculoND", "xlsx"),
        ],
    )
    def test_sufijo_estable(self, tipo_doc, extension):
        exp_id = "EXP-DC-50561-F204-67721-20260910"
        assert build_documento_nombre(exp_id, tipo_doc, extension) == (
            f"{exp_id}_{tipo_doc}.{extension}"
        )

    def test_no_hay_sufijos_del_esquema_viejo(self):
        """El esquema anterior colaba la modalidad (FAC/CON) y un correlativo."""
        exp_id = build_expediente_id("DC", "50561", "F204", "67721", fecha="20260910")
        partes = exp_id.split("-")
        assert "FAC" not in partes and "CON" not in partes
        assert len(partes) == 6  # EXP-CASO-CLI-SERIE-NRO-FECHA
        assert "2026-001" not in exp_id  # sin correlativo

    def test_el_nombre_del_archivo_es_el_de_la_carpeta(self):
        """El documento cuelga de una carpeta con el mismo id."""
        exp_id = build_expediente_id("FPE", "50561", "F201", "100", fecha="20260101")
        nombre = build_documento_nombre(exp_id, "Informe", "docx")
        assert nombre.startswith(f"{exp_id}_")
