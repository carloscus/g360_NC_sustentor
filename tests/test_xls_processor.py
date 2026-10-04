"""Tests del parser del reporte Estadistica11 (port de parser.rs)."""

import pytest

from src.core.xls_processor import (
    ParseOutput,
    clean_id,
    derivar_campos,
    normalize_client_id,
    normalize_line_id,
    normalize_seller_id,
    parse_f64,
    parse_f64_ctx,
    parse_date,
    parse_report_rows,
    resolve_nc_nd_cross,
)

HEADER = [
    "ANHO",
    "MES",
    "ID_CLIENTE",
    "DOC_CLIENTE",
    "NOM_CLIENTE",
    "NOM_DEPARTAMENTO",
    "NOM_PROVINCIA",
    "NOM_DISTRITO",
    "ID_LINEA",
    "NOM_LINEA",
    "ID_GRUPO",
    "NOM_GRUPO",
    "ID_TIPO",
    "NOM_TIPO",
    "ID_FAMILIA",
    "NOM_FAMILIA",
    "ID_ARTICULO",
    "NOM_ARTICULO",
    "ID_VENDEDOR",
    "NOM_VENDEDOR",
    "COD_SUCURSAL",
    "NOM_SUCURSAL",
    "TPO_DOC",
    "SERIE_DOC",
    "NRO_DOC",
    "REFERENCIA",
    "FECHA_ORIG",
    "FECHA_REF",
    "MONEDA",
    "CANTIDAD FAE",
    "CANTIDAD",
    "SOLES",
    "DOLARES",
    "ID_PEDIDO",
    "FECHA_VENC",
]


def fila(**over):
    row = [
        "2024",
        "01",
        "68414",
        "20100047218",
        "CLIENTE DEMO SAC",
        "LIMA",
        "LIMA",
        "SAN ISIDRO",
        "01",
        "GASEOSAS",
        "01",
        "G",
        "01",
        "T",
        "01",
        "F",
        "02211",
        "GASEOSA 3L",
        "177",
        "VENDEDOR UNO",
        "01",
        "LIMA",
        "F012",
        "012",
        "457996",
        "",
        "15/01/2024",
        "",
        "Soles",
        "",
        "100",
        "250,00",
        "0",
        "P1",
        "",
    ]
    mapping = {name: i for i, name in enumerate(HEADER)}
    for k, v in over.items():
        row[mapping[k]] = v
    return row


class TestNumericos:
    def test_cantidad_punto_decimal(self):
        assert parse_f64("25.056", True) == 25.056
        assert parse_f64("1,000", True) == 1000.0

    def test_montos_latino(self):
        assert parse_f64("1.000,50", False) == 1000.50
        assert parse_f64("250,00", False) == 250.0
        assert parse_f64("1.000", False) == 1000.0  # 3 ceros => miles
        assert parse_f64("25.056", False) == 25.056  # 3 decimales no cero => decimal
        assert parse_f64("1234.5", False) == 1234.5

    def test_ctx(self):
        assert parse_f64_ctx("100", "CANTIDAD") == 100.0
        assert parse_f64_ctx("250,00", "SOLES") == 250.0

    def test_vacios(self):
        assert parse_f64("", True) == 0.0
        assert parse_f64("abc", False) == 0.0


class TestNormalizacion:
    def test_client_id(self):
        assert normalize_client_id("68414") == "00068414"
        assert normalize_client_id("20100047218") == "20100047218"
        assert normalize_client_id("") == ""
        assert normalize_client_id("nan") == ""

    def test_linea_vendedor(self):
        # Canónico pelado (espejo de g360-ventas-db): se quita el '01'.
        assert normalize_line_id("01") == "01"
        assert normalize_line_id("MA") == "MA"
        assert normalize_line_id("0101") == "01"
        assert normalize_line_id("01AD") == "AD"
        assert normalize_seller_id("177") == "177"
        assert normalize_seller_id("A02") == "A02"
        assert normalize_seller_id("01177") == "177"
        assert normalize_seller_id("01A02") == "A02"

    def test_prefijo_empresa_es_transparente(self):
        """'0102' = empresa(01) + codigo(02): corta y larga convergen, y el
        allowlist matchea el CODIGO (sin el 01) en ambas formas."""
        from src.core.ventas_db import is_allowed_line
        from src.core.xls_processor import linea_corta, vendedor_corto

        # lineas: corta y larga son el mismo codigo
        for corta, larga in [
            ("02", "0102"),
            ("01", "0101"),
            ("AD", "01AD"),
            ("MA", "01MA"),
            ("CG", "01CG"),
        ]:
            assert linea_corta(larga) == corta
            assert linea_corta(corta) == corta
            assert normalize_line_id(larga) == corta  # pela el prefijo
            assert normalize_line_id(corta) == corta
            assert is_allowed_line(larga) == is_allowed_line(corta) is True
        # vendedores
        for corta, larga in [
            ("177", "01177"),
            ("052", "01052"),
            ("M17", "01M17"),
            ("I02", "01I02"),
        ]:
            assert vendedor_corto(larga) == corta
            assert vendedor_corto(corta) == corta
            assert normalize_seller_id(larga) == corta
            assert normalize_seller_id(corta) == corta
        # codigo fuera del allowlist se filtra en ambas formas
        assert not is_allowed_line("0120") and not is_allowed_line(linea_corta("0120"))

    def test_clean_id_trailing_zero(self):
        assert clean_id("457996.0") == "457996"
        assert clean_id("100.0") == "100"
        assert clean_id("25.05") == "25.05"  # no termina .0 completo

    def test_parse_date(self):
        assert parse_date("15/01/2024") == "2024-01-15"
        assert parse_date("15-01-2024") == "2024-01-15"
        assert parse_date("") == ""
        assert parse_date("xx/yy/zz") == ""


class TestDerivarCampos:
    def test_factura(self):
        v = {
            "tpo_doc": "F012",
            "serie_doc": "012",
            "nro_doc": "457996",
            "referencia": "",
            "cantidad": 10.0,
        }
        derivar_campos(v)
        assert v["tipo_operacion"] == "venta"
        assert v["folio_unico"] == "F012/012/457996"

    def test_ncr_devolucion_y_ajuste(self):
        v = {
            "tpo_doc": "NCR",
            "serie_doc": "N012",
            "nro_doc": "1",
            "referencia": "F01/204-50867",
            "cantidad": -5.0,
        }
        derivar_campos(v)
        assert v["tipo_operacion"] == "devolucion"
        assert v["factura_ref_serie"] == "204"
        assert v["factura_ref_nro"] == "50867"
        v2 = {
            "tpo_doc": "NCR",
            "serie_doc": "N012",
            "nro_doc": "2",
            "referencia": "F01/204-50867",
            "cantidad": 0.0,
        }
        derivar_campos(v2)
        assert v2["tipo_operacion"] == "ajuste_valor"

    def test_ndb_incremento(self):
        v = {
            "tpo_doc": "NDB",
            "serie_doc": "N012",
            "nro_doc": "3",
            "referencia": "F01/204-50867",
            "cantidad": 0.0,
        }
        derivar_campos(v)
        assert v["tipo_operacion"] == "nota_debito"


class TestParseReport:
    def _rows(self, *filas):
        return [HEADER] + list(filas)

    def test_parse_basico(self):
        out = parse_report_rows(self._rows(fila()), "2024-01", file_source="t.xls")
        assert isinstance(out, ParseOutput)
        assert len(out.ventas) == 1
        v = out.ventas[0]
        assert v["id_cliente"] == "00068414"
        assert v["id_vendedor"] == "177"
        assert v["id_linea"] == "01"
        assert v["fecha_orig"] == "2024-01-15"
        assert v["cantidad"] == 100.0
        assert v["soles"] == 250.0
        assert v["precio_unitario"] == 2.5
        assert v["tipo_operacion"] == "venta"
        assert v["mes_ref"] == "2024-01"

    def test_precio_con_fae_si_cantidad_cero(self):
        out = parse_report_rows(
            self._rows(
                fila(
                    TPO_DOC="NCR",
                    SERIE_DOC="N012",
                    NRO_DOC="9",
                    REFERENCIA="F01/012-457996",
                    CANTIDAD="0",
                    **{"CANTIDAD FAE": "100"},
                    SOLES="-50",
                )
            ),
            "2024-01",
        )
        v = out.ventas[0]
        assert v["cantidad_fae"] == 100.0
        assert v["precio_unitario"] == pytest.approx(-0.5)

    def test_linea_no_permitida_nc_con_factura_en_archivo(self):
        # NC con linea no permitida pero factura referenciada presente con linea ok
        out = parse_report_rows(
            self._rows(
                fila(),
                fila(
                    TPO_DOC="NCR",
                    SERIE_DOC="N012",
                    NRO_DOC="900001",
                    REFERENCIA="F01/012-457996",
                    ID_LINEA="ZZ",
                    CANTIDAD="-10",
                    SOLES="-25",
                    FECHA_ORIG="10/02/2024",
                ),
            ),
            "2024-01",
        )
        assert len(out.ventas) == 2
        assert out.ventas[1]["tipo_operacion"] == "devolucion"

    def test_linea_no_permitida_nc_cross_month(self):
        # F6: espejo completo, sin filtro. La NCR va a ventas aunque su línea
        # no esté en la allowlist y su factura no esté en el archivo.
        out = parse_report_rows(
            self._rows(
                fila(),
                fila(
                    TPO_DOC="NCR",
                    SERIE_DOC="N012",
                    NRO_DOC="900002",
                    REFERENCIA="F01/999-111111",
                    ID_LINEA="ZZ",
                    CANTIDAD="-10",
                    SOLES="-25",
                    FECHA_ORIG="10/02/2024",
                ),
            ),
            "2024-01",
        )
        assert len(out.ventas) == 2
        assert out.nc_nd_pendientes == []

    def test_fila_sin_cliente_o_sku_se_ignora(self):
        out = parse_report_rows(self._rows(fila(ID_CLIENTE="")), "2024-01")
        assert out.ventas == []

    def test_header_detectado_con_ruido_previo(self):
        ruido = [["Reporte de Estadistica", "11"], [""], []]
        out = parse_report_rows(ruido + [HEADER] + [fila()], "2024-01")
        assert len(out.ventas) == 1


class TestCrossMonth:
    def test_resolve_contra_db(self, populated_db):
        from src.core import ventas_db

        conn = ventas_db.get_conn()
        pendiente = [
            dict(
                id_articulo="02211",
                tpo_doc="NCR",
                serie_doc="N012",
                nro_doc="999999",
                factura_ref_serie="012",
                factura_ref_nro="457996",
            )
        ]
        resueltas, sin = resolve_nc_nd_cross(conn, pendiente)
        assert len(resueltas) == 1
        assert sin == []

    def test_resolve_sin_factura_en_db(self, populated_db):
        from src.core import ventas_db

        conn = ventas_db.get_conn()
        pendiente = [
            dict(
                id_articulo="02211",
                tpo_doc="NCR",
                serie_doc="N012",
                nro_doc="999998",
                factura_ref_serie="999",
                factura_ref_nro="000001",
            )
        ]
        resueltas, sin = resolve_nc_nd_cross(conn, pendiente)
        assert resueltas == []
        assert len(sin) == 1


class TestLineaReconocimiento:
    """Regla de allowlist de lineas: aplica al parsear archivos y cambia al
    instante (config -> is_allowed_line -> cache). No requiere re-descargar
    ni reiniciar; solo re-procesar el archivo si quieres lineas ya saltadas."""

    def _inv(self, linea, serie="012", nro="300001"):
        return fila(ID_LINEA=linea, TPO_DOC="F01", SERIE_DOC=serie, NRO_DOC=nro)

    def _nc(self, linea, serie, nro, ref):
        return fila(
            ID_LINEA=linea,
            TPO_DOC="NCR",
            SERIE_DOC=serie,
            NRO_DOC=nro,
            REFERENCIA=ref,
            FECHA_ORIG="15/02/2024",
            ANHO="2024",
            MES="02",
        )

    @staticmethod
    def _rows(*filas):
        return [HEADER] + list(filas)

    def test_toda_linea_se_guarda_sin_filtro(self, tmp_db):
        # F6: el parse no filtra por allowlist (espejo completo). La línea 45
        # se guarda aunque no esté aprobada; la allowlist es solo de pantalla.
        inv = self._inv("45")
        out = parse_report_rows(self._rows(inv), "2024-01")
        assert len(out.ventas) == 1
        assert out.ventas[0]["id_linea"] == "45"

    def test_quitar_linea_no_afecta_guardado(self, tmp_db):
        import src.core.ventas_db as ventas_db

        inv = self._inv("01")
        sin01 = [x for x in ventas_db.DEFAULT_ALLOWED_LINES if x != "01"]
        ventas_db.save_app_config({"allowed_lines": sin01})
        out = parse_report_rows(self._rows(inv), "2024-01")
        assert len(out.ventas) == 1  # se guarda igual; el filtro es de pantalla

    def test_cache_se_invalida_con_guardar(self, tmp_db):
        import src.core.ventas_db as ventas_db

        ventas_db.reset_allowed_lines_cache()
        assert "AA" not in ventas_db.allowed_lines()
        ventas_db.save_app_config({"allowed_lines": ["AA"]})
        assert ventas_db.allowed_lines() == ["AA"]
        ventas_db.save_app_config({})  # sin clave => vuelve a defaults
        assert ventas_db.allowed_lines() == ventas_db.DEFAULT_ALLOWED_LINES

    def test_ncnd_linea_no_permitida_con_factura_permitida(self, tmp_db):
        inv = self._inv("01", nro="300010")
        nc = self._nc("ZX", "N012", "900010", "F01/012-300010")
        out = parse_report_rows(self._rows(inv, nc), "2024-02")
        assert len(out.ventas) == 2  # NC conservada: ref valida de factura permitida
        assert out.nc_nd_pendientes == []

    def test_ncnd_siempre_se_guarda(self, tmp_db):
        # F6: factura y NC van a ventas aunque sus líneas no estén permitidas.
        inv = self._inv("ZZ", nro="300020")
        nc = self._nc("ZX", "N012", "900020", "F01/012-300020")
        out = parse_report_rows(self._rows(inv, nc), "2024-02")
        assert len(out.ventas) == 2
        assert out.nc_nd_pendientes == []

    def test_ncnd_sin_factura_en_archivo_igual_se_guarda(self, tmp_db):
        # F6: la NCR es un documento real aunque su factura no esté acá.
        nc = self._nc("ZX", "N012", "900030", "F01/012-999999")
        out = parse_report_rows(self._rows(nc), "2024-02")
        assert len(out.ventas) == 1
        assert out.nc_nd_pendientes == []


class TestParseCamposCompletos:
    """Los 7 campos que el parse ignoraba (fase 2): ahora aterrizan en la fila."""

    HEADER44 = HEADER + [
        "ID_LOCALIDAD_UBIGEO",
        "ESTADO_LINEA",
        "CANAL DE DISTRIBUCION",
        "ID_GUIA",
        "NOM_CONDICION_PAGO",
        "DIVISION",
        "FEC_CARGO",
        "ORD_COMPRA",
    ]

    def _fila44(self, **over):
        row = fila() + [
            "150118",
            "LINEA TRADICIONAL",
            "MAYORISTA",
            "G-1",
            "CONTADO",
            "CONSUMO MASIVO",
            "01/02/2024",
            " 001561 ",
        ]
        mapping = {name: i for i, name in enumerate(self.HEADER44)}
        for k, v in over.items():
            row[mapping[k]] = v
        return row

    def test_siete_campos_aterran(self):
        out = parse_report_rows([self.HEADER44, self._fila44()], "2024-01")
        assert len(out.ventas) == 1
        v = out.ventas[0]
        assert v["id_ubigeo"] == "150118"
        assert v["estado_linea"] == "LINEA TRADICIONAL"
        assert v["canal_distribucion"] == "MAYORISTA"
        assert v["id_guia"] == "G-1"
        assert v["nom_condicion_pago"] == "CONTADO"
        assert v["division"] == "CONSUMO MASIVO"
        # `fec_cargo` llega en dd/mm/yyyy del ERP. Lo que se fija aca es que`n        # el write path lo normalice: el que decide el formato final es`n        # ventas_db._normaliza_fechas_venta, no el processor.`n        assert v["fec_cargo"] == "01/02/2024"  # ver test_normalizacion_ventas_db

    def test_orden_compra_sale_normalizada(self):
        out = parse_report_rows([self.HEADER44, self._fila44()], "2024-01")
        assert out.ventas[0]["ord_compra"] == "1561"

    def test_fila_de_tipos_se_descarta(self):
        # El grid expone una fila de tipos bajo el header; no es un dato.
        tipos = self._fila44(
            ID_ARTICULO="texto",
            NOM_ARTICULO="texto",
            ID_CLIENTE="texto",
            NOM_CLIENTE="texto",
            ID_VENDEDOR="91",
            NOM_VENDEDOR="texto",
            CANTIDAD="numero",
            SOLES="numero decimal",
        )
        out = parse_report_rows([self.HEADER44, tipos], "2024-01")
        assert out.ventas == []
