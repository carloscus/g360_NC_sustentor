"""DF (Devolución física): asigna las unidades devueltas contra las facturas.

Reglas que fija este archivo:

- la asignación es LIFO por defecto (factura más reciente primero) y cambia con
  el radio de orden de asignación;
- el valor se reconoce al **precio neto** de la factura: una FAE previa descuenta
  el importe de las unidades devueltas y una devolución previa recalcula el
  precio sobre las unidades que quedan;
- el SKU se muestra como lo trae el ERP (con ceros a la izquierda);
- individual: una fila por factura×SKU (habilita un expediente por factura);
  consolidado: una fila por SKU con precio por moda y cortes en la auditoría;
- sin archivo de devoluciones o sin historial el caso aventa, no revienta.
"""

import pandas as pd
import pytest

from src.core.detector import detectar_notas_en_historial
from src.domain import ExpedienteComercial, PipelineContext
from src.pipeline import Pipeline
from src.strategies.devolucion_fisica import DevolucionFisicaStrategy
from src.ui.catalog import CATALOGO
from src.ui.config_builder import (
    _leer_devoluciones,
    _resumen_notas_por_sku,
    build_config,
    build_datos_exp,
)
from src.ui.reconocimiento_config import ESTRATEGIA_POR_TIPO

# Registro de motores del pipeline (vive en la clase, no en el módulo).
STRATEGIES = Pipeline.STRATEGIES

CLI = "EMPRESA X"
RUC = "20560201011"
CID = "00068414"


def _facturas():
    filas = []

    def f(codigo, serie, nro, fecha, cant, precio):
        filas.append(
            dict(
                CODIGO=codigo,
                ARTICULO="ART " + codigo,
                LINEA="L1",
                CANTIDAD=float(cant),
                SOLES=round(cant * precio, 2),
                FECHA=pd.Timestamp(fecha),
                TIPO_DOC="F",
                SERIE=serie,
                NUMERO=str(nro),
                DOC_ID=f"F{serie}-{nro}",
                CLIENTE=CLI,
                COD_CLIENTE=CID,
                DOC_CLIENTE=RUC,
                TIPO_CLASE="factura",
                TIPO_NOTA="",
                CATEGORIA="",
                FACTURA_REF="",
                DOC_NOTA="",
                CANTIDAD_FAE=0.0,
                NC_ASOCIADAS=[],
            )
        )

    f("03108", "F204", 100, "2026-01-05", 100, 5.50)  # 550.00
    f("03108", "F204", 102, "2026-01-18", 80, 5.50)  # 440.00
    f("03108", "F204", 103, "2026-01-25", 60, 5.50)  # 330.00 (FAE -20 debajo)
    f("72015", "F204", 101, "2026-01-12", 200, 5.00)  # 1000.00
    return pd.DataFrame(filas)


def _notas():
    """FAE de -S/20 sobre F204-103 y devolución de 10u sobre F204-102."""
    return pd.DataFrame(
        [
            dict(
                CODIGO="03108",
                ARTICULO="ART 03108",
                CANTIDAD=0.0,
                SOLES=-20.0,
                FECHA=pd.Timestamp("2026-01-28"),
                TIPO_DOC="NC",
                SERIE="F910",
                NUMERO="1",
                TIPO_NOTA="FAE",
                CATEGORIA="valor",
                FACTURA_REF="F204-103",
                CANTIDAD_FAE=20.0,
                DOC_CLIENTE=RUC,
                CLIENTE=CLI,
                COD_CLIENTE=CID,
            ),
            dict(
                CODIGO="03108",
                ARTICULO="ART 03108",
                CANTIDAD=-10.0,
                SOLES=-55.0,
                FECHA=pd.Timestamp("2026-01-29"),
                TIPO_DOC="NC",
                SERIE="F911",
                NUMERO="1",
                TIPO_NOTA="",
                CATEGORIA="devolucion",
                FACTURA_REF="F204-102",
                CANTIDAD_FAE=0.0,
                DOC_CLIENTE=RUC,
                CLIENTE=CLI,
                COD_CLIENTE=CID,
            ),
        ]
    )


def _historial():
    return pd.concat([_facturas(), _notas()], ignore_index=True)


POLITICA = {
    "facturas": {"mostrar": True, "usar": True},
    "nc": {"mostrar": True, "usar": True},
    "ndb": {"mostrar": True, "usar": False},
}


def _correr(modalidad="individual", devoluciones=None, hist=None, sort="fecha_desc"):
    hist = _historial() if hist is None else hist
    ui = {
        "modalidad": modalidad,
        "historico_config": POLITICA,
        "df_historial_full": hist,
        "cliente_pb": CLI,
        "sort_mode_dc": sort,
    }
    config = build_config("devolucion_fisica", ui)
    config["devoluciones"] = (
        devoluciones
        if devoluciones is not None
        else {
            "03108": {
                "cantidad": 50.0,
                "fecha": "",
                "articulo": "ART 03108",
                "sku_original": "03108",
            },
        }
    )
    datos = build_datos_exp("devolucion_fisica", hist, config, ui)
    est, var = ESTRATEGIA_POR_TIPO["devolucion_fisica"]
    exp = ExpedienteComercial(
        nombre="DF",
        familia="DF",
        estrategia=est,
        variante=var,
        datos=datos,
        contexto=PipelineContext(config=config, antecedentes="", observaciones=""),
    )
    return Pipeline().ejecutar(exp)


class TestRegistroDeEstrategia:
    def test_df_esta_registrada_en_el_pipeline(self):
        """El caso estaba declarado pero sin motor: fallaba al ejecutar."""
        assert "DevolucionFisica" in STRATEGIES

    @pytest.mark.parametrize("codigo", sorted(CATALOGO))
    def test_todo_caso_del_catalogo_tiene_motor(self, codigo):
        caso = CATALOGO[codigo]
        estrategia, _ = ESTRATEGIA_POR_TIPO.get(caso.legacy_types[0], ("", ""))
        assert estrategia, f"{codigo}: sin ESTRATEGIA_POR_TIPO"
        assert estrategia in STRATEGIES, f"{codigo}: '{estrategia}' no está en Pipeline.STRATEGIES"


class TestAsignacionLifo:
    def test_toma_la_factura_mas_reciente(self):
        df = _correr().resultado.get_excel()
        fila = df[df["SKU"] == "03108"].iloc[0]
        assert fila["FACTURA"] == "F204-103"
        assert fila["CANTIDAD"] == 50.0

    def test_fifo_toma_la_mas_antigua(self):
        df = _correr(sort="fecha_asc").resultado.get_excel()
        fila = df[df["SKU"] == "03108"].iloc[0]
        assert fila["FACTURA"] == "F204-100"

    def test_reparte_entre_varias_facturas(self):
        df = _correr(
            devoluciones={"03108": {"cantidad": 150.0, "sku_original": "03108"}}
        ).resultado.get_excel()
        # 60 (F204-103) + 80 (F204-102) + 10 (F204-100)
        assert df["CANTIDAD"].sum() == 150.0
        assert set(df["FACTURA"]) == {"F204-103", "F204-102", "F204-100"}

    def test_muestra_el_sku_del_erp_con_ceros(self):
        df = _correr().resultado.get_excel()
        assert "03108" in set(df["SKU"])
        assert "3108" not in set(df["SKU"])


class TestPrecioNeto:
    def test_fae_previa_descuenta_el_precio_de_las_unidades(self):
        df = _correr().resultado.get_excel()
        fila = df[df["SKU"] == "03108"].iloc[0]
        # (330 - 20) / 60 = 5.16667
        assert fila["PRECIO_HIST"] == pytest.approx(5.16667, abs=0.0001)
        assert fila["MONTO_NC"] == pytest.approx(50 * 5.16667, abs=0.01)

    def test_avisa_que_la_factura_ya_tenia_nota(self):
        res = _correr().resultado
        assert any(a.codigo == "AL12" for a in res.alertas)
        fila = res.get_excel()
        assert "NC previa" in str(fila[fila["SKU"] == "03108"]["AUDITORIA_NC"].iloc[0])

    def test_sin_nota_previas_el_precio_es_el_de_la_factura(self):
        res = _correr(hist=_facturas()).resultado
        fila = res.get_excel()
        fila = fila[fila["SKU"] == "03108"].iloc[0]
        assert fila["PRECIO_HIST"] == 5.50
        assert not [a for a in res.alertas if a.codigo == "AL12"]

    def test_resumen_de_notas_separa_fae_y_devolucion(self):
        resumen = _resumen_notas_por_sku(detectar_notas_en_historial(_notas()))
        fae = resumen["F204-103"]["3108"]
        dev = resumen["F204-102"]["3108"]
        assert (fae["fae_qty"], fae["fae_soles"]) == (20.0, 20.0)
        assert dev["dev_qty"] == 10.0
        assert dev["fae_qty"] == 0.0


class TestModalidades:
    def test_individual_una_fila_por_factura(self):
        df = _correr(
            "individual", devoluciones={"03108": {"cantidad": 150.0, "sku_original": "03108"}}
        ).resultado.get_excel()
        assert len(df) == 3
        assert "FACTURA" in df.columns and "FACTURAS" not in df.columns

    def test_consolidado_una_fila_por_sku_con_facturas(self):
        res = _correr(
            "consolidado", devoluciones={"03108": {"cantidad": 150.0, "sku_original": "03108"}}
        ).resultado
        df = res.get_excel()
        assert len(df) == 1
        assert "FACTURAS" in df.columns
        assert set(df["FACTURAS"].iloc[0].split(", ")) == {"F204-100", "F204-102", "F204-103"}
        assert res.resumen["documentos_unicos"] == ["F204-100", "F204-102", "F204-103"]

    def test_consolidado_precio_por_moda_y_monto_exacto(self):
        hist = _facturas()
        hist.loc[hist["DOC_ID"] == "FF204-102", "SOLES"] = 320.0  # 4.00/u
        res = _correr(
            "consolidado",
            hist=hist,
            devoluciones={"03108": {"cantidad": 140.0, "sku_original": "03108"}},
        ).resultado
        fila = res.get_excel().iloc[0]
        assert fila["PRECIO_HIST"] == 5.50  # 5.50 aparece en 2 cortes
        assert fila["MONTO_NC"] == pytest.approx(60 * 5.50 + 80 * 4.00, abs=0.01)
        assert "moda S/ 5.50000" in fila["AUDITORIA_NC"]
        assert "Cortes:" in fila["AUDITORIA_NC"]


class TestAlertasYEntradasInvalidas:
    def test_sku_sin_historial_aventa_al06(self):
        res = _correr(
            devoluciones={"NOEXISTE": {"cantidad": 5.0, "sku_original": "NOEXISTE"}}
        ).resultado
        assert any(a.codigo == "AL06" for a in res.alertas)
        assert res.resumen["total_nc"] == 0

    def test_cantidad_insuficiente_aventa_al09(self):
        res = _correr(
            devoluciones={"72015": {"cantidad": 250.0, "sku_original": "72015"}}
        ).resultado
        al09 = [a for a in res.alertas if a.codigo == "AL09"]
        assert al09 and "200" in al09[0].mensaje
        assert res.resumen["unidades_sin_sustento"] == 50.0

    def test_precios_variables_aventa_al03(self):
        hist = _facturas()
        hist.loc[hist["DOC_ID"] == "FF204-102", "SOLES"] = 320.0
        res = _correr(
            hist=hist, devoluciones={"03108": {"cantidad": 140.0, "sku_original": "03108"}}
        ).resultado
        assert any(a.codigo == "AL03" for a in res.alertas)

    def test_cantidad_cero_aventa_al10(self):
        res = _correr(devoluciones={"03108": {"cantidad": 0.0, "sku_original": "03108"}}).resultado
        assert any(a.codigo == "AL10" for a in res.alertas)

    def test_sin_archivo_de_devoluciones_aventa(self):
        config = build_config("devolucion_fisica", {"modalidad": "individual"})
        config["devoluciones"] = {}
        datos = build_datos_exp("devolucion_fisica", _historial(), config, {})
        exp = ExpedienteComercial(
            nombre="DF",
            familia="DF",
            estrategia="DevolucionFisica",
            datos=datos,
            contexto=PipelineContext(config=config),
        )
        res = DevolucionFisicaStrategy().process(exp)
        assert res.dataframe.empty
        assert any(a.tipo == "error" for a in res.alertas)

    def test_historial_vacio_aventa(self):
        res = DevolucionFisicaStrategy().process(
            ExpedienteComercial(
                nombre="DF",
                familia="DF",
                estrategia="DevolucionFisica",
                datos=pd.DataFrame(),
                contexto=PipelineContext(config={"devoluciones": {"S1": {"cantidad": 1}}}),
            )
        )
        assert any(a.tipo == "error" for a in res.alertas)


class TestArchivoDeDevoluciones:
    def _archivo(
        self, tmp_path, filas, columnas=("CODIGO_SKU", "CANTIDAD_DEVUELTA", "FECHA_DEVOLUCION")
    ):
        ruta = tmp_path / "devoluciones.xlsx"
        pd.DataFrame(filas, columns=list(columnas)).to_excel(ruta, index=False)
        return ruta

    def test_lee_la_plantilla(self, tmp_path):
        ruta = self._archivo(tmp_path, [["03108", 20, "2026-02-01"], ["72015", 5, None]])
        dev = _leer_devoluciones(str(ruta))
        assert set(dev) == {"03108", "72015"}
        assert dev["03108"]["cantidad"] == 20.0
        assert dev["03108"]["fecha"] == "2026-02-01"
        assert dev["03108"]["sku_original"] == "03108"

    def test_suma_varias_filas_del_mismo_sku(self, tmp_path):
        ruta = self._archivo(
            tmp_path, [["03108", 20], ["03108", 5]], columnas=("CODIGO_SKU", "CANTIDAD_DEVUELTA")
        )
        dev = _leer_devoluciones(str(ruta))
        assert dev["03108"]["cantidad"] == 25.0

    def test_acepta_nombres_internos(self, tmp_path):
        ruta = self._archivo(tmp_path, [["03108", 7]], columnas=("CODIGO", "CANTIDAD"))
        assert _leer_devoluciones(str(ruta))["03108"]["cantidad"] == 7.0

    def test_sin_archivo_devuelve_vacio(self):
        assert _leer_devoluciones(None) == {}
        assert _leer_devoluciones("no-existe.xlsx") == {}
