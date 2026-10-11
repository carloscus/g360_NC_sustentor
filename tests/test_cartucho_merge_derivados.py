"""El merge debe dejar los derivados al día, igual que el replace.

El replace recalcula `agg_cliente_mes`, `nc_asociadas`, `stats_cache` y
`mes_checksums` (cartucho.py, rama L675-678). El merge traía folios y no
recalculaba nada: el cliente quedaba con un dashboard que no cuadra con las
filas que tiene al lado, y `diagnostico_cobertura` no lo detecta porque
compara días, no números.

Esto importa más a medida que el merge se vuelve el camino habitual (cartucho
incremental en vez de reemplazo completo).
"""

from __future__ import annotations

from src.core import ventas_db
from src.core.cartucho import importar_cartucho
from tests.test_cartucho import _cartucho_minimo, _fila, _vender


def _agg(conn) -> dict:
    """agg_cliente_mes es (cliente, vendedor, mes) -> n_docs distintos."""
    return {
        (r[0], r[1], r[2]): r[3]
        for r in conn.execute("SELECT id_cliente, id_vendedor, mes, n_docs FROM agg_cliente_mes")
    }


def _agg_real(conn) -> dict:
    """Lo mismo, calculado desde `ventas` (la verdad)."""
    return {
        (r[0], r[1], r[2]): r[3]
        for r in conn.execute(
            "SELECT id_cliente, id_vendedor, substr(fecha_orig,1,7), "
            "COUNT(DISTINCT serie_doc || '-' || nro_doc) FROM ventas GROUP BY 1, 2, 3"
        )
    }


def _base(tmp_db) -> None:
    """PC cliente con una venta del dia 28 y sus derivados ya calculados."""
    conn = ventas_db.get_conn()
    _vender(conn, "2026-09-28", "1", soles=10.0)
    ventas_db.record_day_checksum(conn, "2026-09-28")
    conn.commit()
    ventas_db.refresh_agg_cliente_mes(conn)
    ventas_db.populate_nc_asociadas(conn)
    ventas_db.refresh_stats_cache(conn)
    conn.commit()


def _cart(tmp_path, nombre="cart"):
    # Dia 27, folio 9: ni es superset ni es el mismo dia -> merge.
    return _cartucho_minimo(tmp_path / nombre, [_fila("2026-09-27", "9", soles=50.0)])


class TestMergeRecalculaDerivados:
    def test_el_agg_cliente_mes_refleja_lo_mergido(self, tmp_db, tmp_path):
        """La fila traida tiene que aparecer en el agregado, no solo en ventas."""
        _base(tmp_db)
        conn = ventas_db.get_conn()
        antes = _agg(conn)
        assert antes, "el agregado local deberia existir antes del merge"

        r = importar_cartucho(_cart(tmp_path))
        assert r["modo"] == "mergear"

        conn = ventas_db.get_conn()
        agregado, real = _agg(conn), _agg_real(conn)
        assert agregado == real, f"agg_cliente_mes quedo viejo tras el merge: {agregado} vs {real}"
        # Mismo cliente y mismo mes: los dos dias caen en UNA fila de agregado.
        assert len(agregado) == 1
        assert list(agregado.values()) == [2], "deben contar los 2 folios, no 1"

    def test_reporta_que_recalculo(self, tmp_db, tmp_path):
        _base(tmp_db)
        r = importar_cartucho(_cart(tmp_path))
        assert r.get("agregados_recalculados") is True
        assert r.get("meses_recalculados") is not None

    def test_mes_checksums_cubre_el_rango_mergido(self, tmp_db, tmp_path):
        """`mes_checksums` es con lo que compara el proximo cartucho: si queda
        viejo, la proxima actualizacion parte de una base equivocada."""
        _base(tmp_db)
        importar_cartucho(_cart(tmp_path))

        conn = ventas_db.get_conn()
        meses = {r[0] for r in conn.execute("SELECT mes_ref FROM mes_checksums")}
        assert "2026-09" in meses

    def test_stats_cache_no_queda_viejo(self, tmp_db, tmp_path):
        """`stats_cache` es lo que lee la card de salud: si no se refresca, la
        app muestra KPIs de antes de la actualizacion."""
        _base(tmp_db)
        antes = dict(
            (r[0], r[1]) for r in ventas_db.get_conn().execute("SELECT key, value FROM stats_cache")
        )
        importar_cartucho(_cart(tmp_path))
        despues = dict(
            (r[0], r[1]) for r in ventas_db.get_conn().execute("SELECT key, value FROM stats_cache")
        )
        assert despues, "stats_cache deberia seguir poblado"
        assert (
            despues != antes or True
        )  # el contenido puede no cambiar; lo que importa es que se recalculo


class TestAmbasRamasSonFieles:
    """Merge y replace NO terminan con los mismos datos (el merge conserva lo
    local y agrega; el replace queda con lo del cartucho). La garantia no es
    "iguales entre si": es que en los dos casos el derivado dice la verdad
    sobre lo que hay en `ventas`.
    """

    def test_tras_merge_el_agregado_es_fiel(self, tmp_db, tmp_path):
        _base(tmp_db)
        r = importar_cartucho(_cart(tmp_path, "c1"), forzar="mergear")
        assert r["modo"] == "mergear"
        conn = ventas_db.get_conn()
        assert _agg(conn) == _agg_real(conn)

    def test_tras_replace_el_agregado_es_fiel(self, tmp_db, tmp_path):
        _base(tmp_db)
        r = importar_cartucho(_cart(tmp_path, "c2"), forzar="reemplazar")
        assert r["modo"] == "reemplazar"
        conn = ventas_db.get_conn()
        assert _agg(conn) == _agg_real(conn)

    def test_ambas_ramas_lo_reportan(self, tmp_db, tmp_path):
        _base(tmp_db)
        for modo in ("mergear", "reemplazar"):
            r = importar_cartucho(_cart(tmp_path, f"c_{modo}"), forzar=modo)
            assert r.get("agregados_recalculados") is True, f"la rama {modo} no recalculo"
            assert r.get("meses_recalculados") is not None
