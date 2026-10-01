"""Reset rápido: sin re-queries de 30s ni re-render innecesario.

Causa original: `_card_info()` (~10 agregados full-scan sobre 2.8M filas,
~29s en frío) corría en cada reset/tipo-change con TTL de 20s.
"""

from types import SimpleNamespace

import pytest

from src.core import ventas_db


@pytest.fixture(autouse=True)
def _limpiar_cache_card():
    yield
    ventas_db._CARD_INFO_CACHE["ts"] = 0.0
    ventas_db._CARD_INFO_CACHE["data"] = None


class TestCardInfoCache:
    def test_ttl_amplio(self):
        assert ventas_db._CARD_INFO_TTL >= 300

    def test_indices_card_existen_tras_init(self, tmp_db):
        conn = tmp_db.get_conn()
        idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_venta_vendedor" in idx
        assert "idx_venta_tpodoc_folio" in idx

    def test_insert_marca_stale_sin_borrar(self, tmp_db, sample_ventas):
        # Serve-stale: la escritura vence la copia pero la conserva para
        # servirla al instante mientras el background refresca.
        ventas_db._CARD_INFO_CACHE["data"] = {"filas": 1}
        ventas_db._CARD_INFO_CACHE["ts"] = 9999999999.0
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, sample_ventas[:1])
        assert ventas_db._CARD_INFO_CACHE["data"] == {"filas": 1}
        assert ventas_db._CARD_INFO_CACHE["ts"] == 0.0

    def test_dedup_marca_stale_sin_borrar(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, sample_ventas[:1])
        ventas_db._CARD_INFO_CACHE["data"] = {"filas": 1}
        ventas_db._CARD_INFO_CACHE["ts"] = 9999999999.0
        ventas_db.dedup_ventas(conn)
        assert ventas_db._CARD_INFO_CACHE["data"] == {"filas": 1}
        assert ventas_db._CARD_INFO_CACHE["ts"] == 0.0

    def test_serve_stale_no_bloquea(self, tmp_db, sample_ventas):
        import time

        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, sample_ventas[:1])
        ventas_db._CARD_INFO_CACHE["data"] = {"filas": 1, "resumen": "viejo"}
        ventas_db._CARD_INFO_CACHE["ts"] = time.time() - 9999
        t0 = time.perf_counter()
        info = ventas_db.db_card_info()
        assert time.perf_counter() - t0 < 5.0
        assert info["resumen"] == "viejo"
        # Espera al refresco en background y verifica que actualizó
        for _ in range(100):
            if ventas_db._CARD_INFO_CACHE["ts"] > time.time() - 60:
                break
            time.sleep(0.1)
        assert ventas_db._CARD_INFO_CACHE["data"]["filas"] == 1


def _vista_fake(tipo_actual="DC"):
    from src.ui.view_handlers import _ViewHandlers
    from src.ui.view_helpers import _ViewHelpers

    vista = SimpleNamespace(
        tipo_actual=tipo_actual,
        tipo_dropdown=SimpleNamespace(value=tipo_actual, options=[]),
        modalidad_actual="individual",
        modalidad_radio=SimpleNamespace(value="individual"),
        resultado=None,
        resultados_container=SimpleNamespace(visible=True),
        alertas_container=SimpleNamespace(visible=True),
        container=SimpleNamespace(update=lambda: None),
        app=SimpleNamespace(
            page=SimpleNamespace(update=lambda: None),
            show_snackbar=lambda *a, **k: None,
            G360_SUCCESS="ok",
        ),
        historial_path="x",
        lista_path="y",
        requerimientos_paths=["z"],
        df_historial="df",
        sku_filter_path="s",
        llamadas=[],
    )
    vista._legacy_tipo = _ViewHelpers._legacy_tipo.__get__(vista)
    vista._caso_de_tipo = _ViewHelpers._caso_de_tipo.__get__(vista)
    vista._caso_dict = _ViewHelpers._caso_dict
    vista._verificar_puede_ejecutar = lambda: vista.llamadas.append("verificar")
    vista._on_tipo_change = lambda e: vista.llamadas.append("render")
    vista.reset = _ViewHandlers.reset.__get__(vista)
    vista._reset_ui = _ViewHandlers._reset_ui.__get__(vista)
    return vista


class TestResetSinRerender:
    def test_mismo_tipo_no_renderiza(self):
        vista = _vista_fake("DC")
        vista.reset()
        assert "render" not in vista.llamadas
        assert "verificar" in vista.llamadas
        assert vista.tipo_actual == "DC"
        assert vista.resultado is None
        assert vista.resultados_container.visible is False
        assert vista.historial_path is None
        assert vista.df_historial is None

    def test_tipo_distinto_si_renderiza(self):
        vista = _vista_fake("CMV")
        vista.reset()
        assert "render" in vista.llamadas
        assert vista.tipo_dropdown.value == "DC"


class TestStatsCache:
    """El KV de KPIs evita los COUNT(DISTINCT) caros en el arranque frío."""

    def test_refresh_guarda_kpis(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        tmp_db.refresh_stats_cache(conn)
        kv = tmp_db.stats_cache_read()
        assert kv["total_rows"] == len(sample_ventas)
        assert {
            "n_vendedores",
            "n_facturas",
            "n_lineas",
            "n_clientes",
            "n_articulos",
            "total_soles",
        } <= set(kv)

    def test_clear_vacia(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        tmp_db.refresh_stats_cache(conn)
        assert tmp_db.stats_cache_read()
        tmp_db.stats_cache_clear()
        assert tmp_db.stats_cache_read() == {}

    def test_escritura_limpia_el_cache(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.refresh_stats_cache(conn)
        assert tmp_db.stats_cache_read()
        tmp_db.insert_ventas(conn, sample_ventas[:1])
        assert tmp_db.stats_cache_read() == {}

    def test_card_info_prefiere_cache(self, tmp_db, sample_ventas, monkeypatch):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        cache = {
            "total_rows": 999,
            "n_clientes": 7,
            "n_articulos": 5,
            "n_lineas": 3,
            "n_vendedores": 2,
            "n_facturas": 11,
            "total_soles": 1.0,
        }
        monkeypatch.setattr(tmp_db, "stats_cache_read", lambda: cache)
        tmp_db._CARD_INFO_CACHE["data"] = None
        tmp_db._CARD_INFO_CACHE["ts"] = 0.0
        info = tmp_db.db_card_info()
        assert info["filas"] == 999
        assert info["facturas"] == 11
        assert info["vendedores"] == 2
        assert info["skus"] == 5
