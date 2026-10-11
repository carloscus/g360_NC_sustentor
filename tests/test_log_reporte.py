"""Trazabilidad del path de reporte en app.log.

El export de compras y la generacion de expedientes escriben archivos que se
entregan al usuario final (Excel / Informe / Historico). Antes ninguno de los
tres modulos tenia un solo `logger`: un SQL roto, una hoja omitida o un render
que fallaba a mitad solo se veian como un snackbar efimero, y el app.log no
permitia distinguir "no se genero nada" de "se genero y no lo encontre".

Estos tests fijan el contrato de logging: se registra el inicio, el fin con el
detalle de lo escrito, el fallo con traceback, y una hoja descartada por un fetch
caido avisa en vez de desaparecer en silencio.
"""

import logging
import os
import time
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.domain import ExpedienteComercial, PipelineContext
from src.pipeline import Pipeline
from src.ui import expediente_service as es
from src.ui import reporte_compras as rc
from src.ui import reporte_panel as rp
from src.ui.catalog import HistorialConfig
from src.ui.config_builder import build_config, build_datos_exp
from src.ui.reporte_panel import ReportePanel

LOG_REPORTE = "src.ui.reporte_compras"
LOG_PANEL = "src.ui.reporte_panel"
LOG_EXP = "src.ui.expediente_service"


def _nivel(caplog, nivel, logger):
    caplog.set_level(nivel, logger=logger)
    return caplog


def _avisos(caplog, logger, nivel=logging.WARNING):
    return [r.getMessage() for r in caplog.records if r.name == logger and r.levelno >= nivel]


# ── _paquete_export: la hoja que se cae tiene que avisar ─────────────────


class _Cli:
    """Cliente stub: cada fetch devuelve el df dado o revienta con `boom`."""

    def __init__(self, df=None, boom=()):
        self._df = pd.DataFrame() if df is None else df
        self._boom = set(boom)

    def _get(self, nombre):
        def f(cid, **kw):
            if nombre in self._boom:
                raise RuntimeError(f"SQL roto en {nombre}")
            # Estos dos devuelven dict en el cliente real, no DataFrame.
            if nombre in ("fetch_client_by_id", "fetch_vendedor_by_id"):
                return {"nombre": "CLIENTE DEMO", "doc": "206039928"}
            return self._df

        f.__name__ = nombre
        return f

    def __getattr__(self, name):
        if not name.startswith("fetch_"):
            raise AttributeError(name)
        return self._get(name)


class TestPaqueteExportAvisaHojaCaida:
    def test_fetch_caido_avisa_con_nombre_y_cid(self, caplog):
        _nivel(caplog, logging.WARNING, LOG_REPORTE)
        b = rc._paquete_export(
            _Cli(boom=("fetch_notas_sku_cliente",)),
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"ajustes"},
        )
        assert b["df_dev"] is None  # la hoja se omite...
        avisos = [
            r for r in caplog.records if r.name == LOG_REPORTE and r.levelno >= logging.WARNING
        ]
        assert len(avisos) == 1
        msg = avisos[0].getMessage()
        assert "fetch_notas_sku_cliente" in msg  # ...pero se dice cual fue
        assert "00056101" in msg
        assert avisos[0].exc_info is not None  # con traceback para diagnosticar

    def test_sin_datos_no_avisa(self, caplog):
        """Un fetch que devuelve 0 filas es data, no falla: no debe ensuciar."""
        _nivel(caplog, logging.WARNING, LOG_REPORTE)
        rc._paquete_export(
            _Cli(df=pd.DataFrame()),
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"ajustes"},
        )
        assert _avisos(caplog, LOG_REPORTE) == []

    def test_sucursales_caida_avisa(self, caplog):
        _nivel(caplog, logging.WARNING, LOG_REPORTE)
        b = rc._paquete_export(
            _Cli(boom=("fetch_analisis_sucursales_cliente",)),
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"sucursales"},
        )
        assert b["df_suc_pareto"] is None
        avisos = _avisos(caplog, LOG_REPORTE)
        assert len(avisos) == 1
        assert "Sucursales" in avisos[0]

    def test_varias_hojas_caidas_avisan_una_por_una(self, caplog):
        _nivel(caplog, logging.WARNING, LOG_REPORTE)
        rc._paquete_export(
            _Cli(boom=("fetch_notas_sku_cliente", "fetch_facturas_sku_detalle_cliente")),
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"ajustes", "facturas"},
        )
        avisos = _avisos(caplog, LOG_REPORTE)
        assert len(avisos) == 2
        assert any("fetch_notas_sku_cliente" in m for m in avisos)
        assert any("fetch_facturas_sku_detalle_cliente" in m for m in avisos)

    def test_resumen_distingue_con_datos_de_sin_datos(self, caplog):
        """Una linea con los conteos: 'vacia' y 'caida' ya no se confunden."""
        _nivel(caplog, logging.INFO, LOG_REPORTE)
        df = pd.DataFrame({"X": [1, 2, 3]})
        rc._paquete_export(
            _Cli(df=df), "00056101", "2026-09-01", "2026-10-31", True, hojas={"ajustes"}
        )
        con_datos = next(
            m for m in _avisos(caplog, LOG_REPORTE, logging.INFO) if "hojas con datos" in m
        )
        assert "dev=3" in con_datos and "huerf=3" in con_datos

        caplog.clear()
        rc._paquete_export(_Cli(), "00056101", "2026-09-01", "2026-10-31", True, hojas={"ajustes"})
        sin_datos = next(
            m for m in _avisos(caplog, LOG_REPORTE, logging.INFO) if "hojas con datos" in m
        )
        assert "ninguna" in sin_datos


# ── ReportePanel._exportar / ._generar: inicio, OK por cliente, fin ─────


class _App:
    G360_ACCENT = "#0D2B4E"
    G360_SUCCESS = "OK"
    G360_WARNING = "WARN"
    G360_ERROR = "ERR"

    def __init__(self, page, desktop):
        self.page = page
        self._desktop = desktop
        self.snacks = []
        self.loading = []

    def _get_desktop_path(self):
        return self._desktop

    def show_loading(self, msg):
        self.loading.append(msg)

    def hide_loading(self):
        self.loading.append("hide")

    def show_snackbar(self, msg, color=None):
        self.snacks.append(str(msg))


def _df_preview():
    """DataFrame con lo que _render_resultados/_construir_tabla necesitan."""
    return pd.DataFrame(
        {
            "COD_LINEA": ["0101"],
            "LINEA": ["GASEOSAS"],
            "MES_REF": ["2026-09"],
            "SOLES": [10.0],
            "CANTIDAD": [4.0],
        }
    )


def _panel(tmp_path, monkeypatch, df, boom=()):
    """Panel con una pagina (para que los hilos corran) y salida en tmp_path."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    panel = ReportePanel(_App(page=object(), desktop=out), SimpleNamespace())
    panel.construir_card()
    panel.state["clientes"] = [("00056101", "CLIENTE DEMO")]
    panel.state["desde"] = datetime(2026, 9, 1)
    panel.state["hasta"] = datetime(2026, 10, 31)
    panel.state["hojas"] = {"consolidado"}
    panel.state["incluir_nc"] = True
    panel.state["datos"] = {"00056101": df}

    monkeypatch.setattr(
        "src.core.ventas_db_client.VentasDbClient",
        lambda *a, **k: _Cli(df=df, boom=boom),
        raising=True,
    )
    monkeypatch.setattr(os, "startfile", lambda *_: None)
    return panel


def _escribir_fake(*a, **kw):
    from openpyxl import Workbook

    path = kw["path"] if "path" in kw else a[0]
    wb = Workbook()
    wb.active.title = "Consolidado"
    wb.save(str(path))


def _esperar(panel, timeout=10):
    """_exportar/_generar corren en hilo: espera a que suelte el busy."""
    fin = time.time() + timeout
    while time.time() < fin and panel._busy:
        time.sleep(0.02)
    assert not panel._busy, "el hilo del panel no termino"


class TestPanelExportLog:
    def test_export_ok_deja_rastro_completo(self, caplog, tmp_path, monkeypatch):
        _nivel(caplog, logging.INFO, LOG_PANEL)
        panel = _panel(tmp_path, monkeypatch, _df_preview())
        # El panel importa el writer por nombre: hay que parchearlo en su modulo.
        monkeypatch.setattr(rp, "_escribir_xlsx", _escribir_fake)
        panel._exportar()
        _esperar(panel)

        msgs = _avisos(caplog, LOG_PANEL, logging.INFO)
        assert any("reporte.export inicio" in m for m in msgs)
        ok = next(m for m in msgs if "reporte.export OK" in m)
        assert "cid=56101" in ok  # el cliente, para auditar
        assert ".xlsx" in ok  # el archivo, para encontrarlo
        assert any("reporte.export fin" in m and "1 Excel" in m for m in msgs)

    def test_export_fallo_deja_traceback(self, caplog, tmp_path, monkeypatch):
        _nivel(caplog, logging.INFO, LOG_PANEL)
        panel = _panel(tmp_path, monkeypatch, _df_preview())

        def _boom(*a, **kw):
            raise RuntimeError("no se pudo escribir")

        monkeypatch.setattr(rp, "_escribir_xlsx", _boom)
        panel._exportar()
        _esperar(panel)

        errs = [r for r in caplog.records if r.name == LOG_PANEL and r.levelno >= logging.ERROR]
        assert len(errs) == 1
        assert "reporte.export fallo" in errs[0].getMessage()
        assert errs[0].exc_info is not None

    def test_preview_fallo_deja_traceback(self, caplog, tmp_path, monkeypatch):
        _nivel(caplog, logging.INFO, LOG_PANEL)
        panel = _panel(tmp_path, monkeypatch, _df_preview(), boom=("fetch_compras_cliente",))
        panel._generar()
        _esperar(panel)

        errs = [r for r in caplog.records if r.name == LOG_PANEL and r.levelno >= logging.ERROR]
        assert len(errs) == 1
        assert "reporte.preview fallo" in errs[0].getMessage()
        assert "00056101" in errs[0].getMessage()

    def test_preview_ok_registra_periodo(self, caplog, tmp_path, monkeypatch):
        _nivel(caplog, logging.INFO, LOG_PANEL)
        panel = _panel(tmp_path, monkeypatch, _df_preview())
        panel._generar()
        _esperar(panel)

        msgs = _avisos(caplog, LOG_PANEL, logging.INFO)
        assert any("reporte.preview" in m and "2026-09-01" in m for m in msgs)


# ── generar_expediente ──────────────────────────────────────────────────


def _exp_real():
    """Expediente con el pipeline real (DC, una factura con NC aplicada)."""
    hist = pd.DataFrame(
        [
            {
                "CODIGO": "00123",
                "ARTICULO": "Producto X",
                "CANTIDAD": 100.0,
                "SOLES": 1000.0,
                "FECHA": pd.Timestamp("2026-01-15"),
                "TIPO_DOC": "F01",
                "SERIE": "001",
                "NUMERO": "10",
                "DOC_ID": "F001-10",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "CLIENTE": "CLIENTE X",
                "COD_CLIENTE": "00056101",
                "DOC_CLIENTE": "206039928",
            },
            {
                "CODIGO": "00123",
                "ARTICULO": "Producto X",
                "CANTIDAD": 0.0,
                "CANTIDAD_FAE": 100.0,
                "SOLES": -100.0,
                "FECHA": pd.Timestamp("2026-01-20"),
                "TIPO_DOC": "NCR",
                "SERIE": "N900",
                "NUMERO": "1",
                "DOC_ID": "NCRN900-1",
                "TIPO_CLASE": "descuento",
                "FACTURA_REF": "F001-10",
                "REFERENCIA": "F01/001-10",
                "CLIENTE": "CLIENTE X",
                "COD_CLIENTE": "00056101",
                "DOC_CLIENTE": "206039928",
            },
        ]
    )
    policy = HistorialConfig(facturas=(True, True), nc=(True, True), ndb=(True, False))
    ui = {
        "modalidad": "individual",
        "sort_mode_dc": "fecha_asc",
        "historico_config": policy.as_dict(),
        "cliente_pb": "CLIENTE X",
        "df_historial_full": hist,
    }
    config = build_config("diferencia_precio", ui)
    data = build_datos_exp("diferencia_precio", hist, config, ui)
    exp = ExpedienteComercial(
        nombre="Diferencia de precio",
        familia="precio",
        estrategia="PriceDifference",
        datos=data,
        contexto=PipelineContext(config=config),
    )
    exp.condiciones = [pd.DataFrame({"SKU": ["00123"], "PRECIO_BASE": [8.0]})]
    exp = Pipeline().ejecutar(exp)
    assert exp.resultado is not None
    return exp, hist, policy


def _gen(exp, hist, policy, tmp_path, **over):
    kw = dict(
        resultado=exp,
        tipo_actual="DC",
        modalidad="individual",
        historico_config=policy,
        df_historial=hist,
        cliente_value="CLIENTE X",
        vendedor_value="01177",
        vendedor_display="VENDEDOR UNO",
        antecedentes="prueba de logging",
        observaciones="",
        desktop_path=tmp_path,
    )
    kw.update(over)
    return es.generar_expediente(**kw)


class TestExpedienteLog:
    def test_inicio_y_fin_con_cantidad(self, caplog, tmp_path):
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        dirs = _gen(exp, hist, policy, tmp_path)
        assert len(dirs) == 1

        msgs = _avisos(caplog, LOG_EXP, logging.INFO)
        inicio = next(m for m in msgs if "expediente inicio" in m)
        assert "caso=DC" in inicio and "individual" in inicio
        fin = next(m for m in msgs if "expediente fin" in m)
        assert "1 expediente(s)" in fin
        assert dirs[0].name in fin  # donde fue a parar

    def test_ok_registra_archivos_escritos(self, caplog, tmp_path):
        """El log lista los archivos reales: sirve para saber que se entrego."""
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        dirs = _gen(exp, hist, policy, tmp_path)

        ok = next(m for m in _avisos(caplog, LOG_EXP, logging.INFO) if "expediente OK" in m)
        for p in dirs[0].iterdir():
            if p.is_file():
                assert p.name in ok  # cada archivo escrito queda nombrado
        assert "Informe.docx" in ok
        assert "Calculo.xlsx" in ok

    def test_fallo_a_mitad_avisa_cuantos_quedaron(self, caplog, tmp_path, monkeypatch):
        """Los expedientes ya escritos quedan en disco: hay que decirlo."""
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()

        # Dos facturas en el mismo cliente: el loop individual llama dos veces.
        df = exp.resultado.dataframe
        df2 = pd.concat([df, df], ignore_index=True)
        df2["FACTURA"] = ["F001-10", "F001-11"]
        exp.resultado.dataframe = df2

        real_uno = es._generar_uno
        n = {"i": 0}

        def _uno(*a, **kw):
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("se cayo el historico")
            return real_uno(*a, **kw)

        monkeypatch.setattr(es, "_generar_uno", _uno)

        with pytest.raises(RuntimeError, match="se cayo el historico"):
            _gen(exp, hist, policy, tmp_path)

        errs = [r for r in caplog.records if r.name == LOG_EXP and r.levelno >= logging.ERROR]
        assert len(errs) == 1
        msg = errs[0].getMessage()
        assert "expediente fallo" in msg and "individual" in msg
        assert "tras 1 expediente(s)" in msg  # el que si quedo en disco
        assert errs[0].exc_info is not None

    def test_consolidado_fallo_tambien_avisa(self, caplog, tmp_path, monkeypatch):
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()

        def _boom(*a, **kw):
            raise RuntimeError("docx corrupto")

        monkeypatch.setattr(es, "_generar_informe", _boom)
        with pytest.raises(RuntimeError, match="docx corrupto"):
            _gen(exp, hist, policy, tmp_path, modalidad="consolidado")

        errs = [r for r in caplog.records if r.name == LOG_EXP and r.levelno >= logging.ERROR]
        assert len(errs) == 1
        assert "expediente fallo" in errs[0].getMessage()
        assert "consolidado" in errs[0].getMessage()

    def test_sin_diferencia_registra_que_no_hubo_que_generar(self, caplog, tmp_path):
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        # Todo queda fuera del filtro de _clonar_sin_cero: sin diferencias
        # unitarias y sin monto de NC reconocible.
        for d in (exp.resultado.dataframe, exp.resultado.get_excel()):
            if d is None or d.empty:
                continue
            for col in ("DIFERENCIA", "MONTO_NC"):
                if col in d.columns:
                    d[col] = 0.0
        dirs = _gen(exp, hist, policy, tmp_path, ocultar_cero=True)
        assert dirs == []
        msgs = _avisos(caplog, LOG_EXP, logging.INFO)
        assert any("sin filas con diferencia" in m for m in msgs)

    def test_solo_ndb_sin_nc_genera_solo_calculond(self, caplog, tmp_path):
        """Todo negativo + ND + check Solo NDB: expediente solo-NDB (unicamente CalculoND).

        El modo solo-NDB es MANUAL: sin el check no se fuerza, aunque el
        monto NC sea 0. Antes habia un automatico que lo activaba solo.
        """
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        df = exp.resultado.dataframe
        df["DIFERENCIA"] = -0.5
        if "MONTO_NC" in df.columns:
            df["MONTO_NC"] = 0.0
        exp.resultado.resumen["total_nc"] = 0.0
        dirs = _gen(exp, hist, policy, tmp_path, generar_nota_debito=True, solo_ndb=True)
        assert len(dirs) == 1
        nombres = sorted(p.name for p in dirs[0].iterdir() if p.is_file())
        assert len(nombres) == 1 and "CalculoND" in nombres[0]
        msgs = _avisos(caplog, LOG_EXP, logging.INFO)
        assert any("solo-NDB" in m for m in msgs)

    def test_sin_check_no_hay_solo_ndb_aunque_no_haya_nc(self, caplog, tmp_path):
        """Sin el check, el expediente sale normal aunque el monto NC sea 0.

        Sin este test, el automatico viejo volvia: todo negativo generaba
        solo CalculoND sin que nadie lo pidiera.
        """
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        df = exp.resultado.dataframe
        df["DIFERENCIA"] = -0.5
        if "MONTO_NC" in df.columns:
            df["MONTO_NC"] = 0.0
        exp.resultado.resumen["total_nc"] = 0.0
        dirs = _gen(exp, hist, policy, tmp_path, generar_nota_debito=True)
        assert len(dirs) == 1
        nombres = sorted(p.name for p in dirs[0].iterdir() if p.is_file())
        assert any("CalculoND" in n for n in nombres)
        assert any("Calculo" in n and "CalculoND" not in n for n in nombres), (
            "sin el check tiene que salir tambien el calculo normal, no solo el ND"
        )

    def test_solo_ndb_explicito_con_nc_genera_solo_calculond(self, caplog, tmp_path):
        """Con NC y ND: el check Solo NDB genera unicamente el CalculoND."""
        _nivel(caplog, logging.INFO, LOG_EXP)
        exp, hist, policy = _exp_real()
        df = exp.resultado.dataframe
        base = df.iloc[[0]].copy()
        df["DIFERENCIA"] = -0.5
        if "MONTO_NC" in df.columns:
            df["MONTO_NC"] = 0.0
        extra = base.copy()
        extra["DIFERENCIA"] = 0.5
        if "MONTO_NC" in extra.columns:
            extra["MONTO_NC"] = 50.0
        exp.resultado.dataframe = pd.concat([df, extra], ignore_index=True)
        exp.resultado.resumen["total_nc"] = 50.0
        dirs = _gen(exp, hist, policy, tmp_path, generar_nota_debito=True, solo_ndb=True)
        assert len(dirs) == 1
        nombres = sorted(p.name for p in dirs[0].iterdir() if p.is_file())
        assert len(nombres) == 1 and "CalculoND" in nombres[0]
