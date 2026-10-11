"""Cartucho incremental: export acotado a un rango + import que lo mergea.

El import YA sabia mergear (decide por `day_checksums`). Lo que faltaba era
poder producir un cartucho parcial. El riesgo propio del parcial es doble:

1. Si el manifiesto no declara el rango, el cliente no puede saber que el
   cartucho no es un historico completo.
2. Si alguien fuerza "reemplazar" con un parcial, el cliente se queda solo con
   ese rango y pierde lo demas.

Ademas el export completo validaba la FUENTE con `integrity_check` (12-25 min
en 3.7 GB) y despues copiaba: validar el archivo que despues se copia no prueba
nada del artefacto que se lleva el cliente.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.core import ventas_db
from src.core.cartucho import (
    MANIFIESTO,
    exportar_cartucho,
    importar_cartucho,
)

_INSERT = (
    "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
    "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _fila(dia: str, nro: str, soles: float = 10.0) -> tuple:
    return (
        f"A{nro}",
        "01",
        "C1",
        "F01",
        "001",
        nro,
        dia,
        dia[:7],
        1.0,
        soles,
        int(dia[:4]),
        int(dia[5:7]),
    )


@pytest.fixture
def db_con_historia(tmp_db):
    """Tres meses de ventas: enero, febrero y marzo."""
    conn = ventas_db.get_conn()
    filas = []
    for dia, nro in [
        ("2026-01-15", "1"),
        ("2026-02-15", "2"),
        ("2026-03-15", "3"),
    ]:
        filas.append(_fila(dia, nro, soles=float(nro) * 10))
    conn.executemany(_INSERT, filas)
    conn.commit()
    for dia, _ in [("2026-01-15", 0), ("2026-02-15", 0), ("2026-03-15", 0)]:
        ventas_db.record_day_checksum(conn, dia)
    conn.commit()
    return conn


def _manifiesto(zpath: Path) -> dict:
    with zipfile.ZipFile(zpath) as zf:
        return json.loads(zf.read(MANIFIESTO).decode("utf-8"))


def _dentro(zpath: Path, sql: str):
    """Ejecuta una consulta contra el historial.db DENTRO del zip."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zpath) as zf:
        zf.extract("historial.db", tmp)
    c = sqlite3.connect(f"file:{(tmp / 'historial.db').as_posix()}?mode=ro", uri=True)
    try:
        return c.execute(sql).fetchone()
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)


class TestExportIncremental:
    def test_solo_trae_el_rango(self, db_con_historia, tmp_path):
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        man = _manifiesto(Path(r["zip"]))
        assert man["db"]["incremental"] is True
        assert man["db"]["rango_solicitado"]["desde"] == "2026-02-01"
        # Solo febrero y marzo.
        assert _dentro(Path(r["zip"]), "SELECT COUNT(*) FROM ventas")[0] == 2
        assert _dentro(Path(r["zip"]), "SELECT MIN(fecha_orig) FROM ventas")[0] == "2026-02-15"

    def test_el_manifiesto_declara_el_rango_real(self, db_con_historia, tmp_path):
        """`fecha_min`/`fecha_max` son los DEL ARTEFACTO, no los de la fuente."""
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        man = _manifiesto(Path(r["zip"]))
        assert man["db"]["fecha_min"] == "2026-02-15"
        assert man["db"]["fecha_max"] == "2026-03-15"
        assert man["db"]["ventas_filas"] == 2

    def test_los_dias_del_manifiesto_son_los_del_rango(self, db_con_historia, tmp_path):
        """Si `dias` tivesse enero, el import creeria que el cartucho cubre un
        dia que no trae, y la decision superset/merge seria falsa."""
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        dias = set(_manifiesto(Path(r["zip"]))["dias"])
        assert dias == {"2026-02-15", "2026-03-15"}

    def test_el_completo_no_es_incremental(self, db_con_historia, tmp_path):
        r = exportar_cartucho(destino=tmp_path / "full")
        man = _manifiesto(Path(r["zip"]))
        assert man["db"]["incremental"] is False
        assert man["db"]["rango_solicitado"] is None
        assert _dentro(Path(r["zip"]), "SELECT COUNT(*) FROM ventas")[0] == 3

    def test_el_incremental_es_mas_chico(self, db_con_historia, tmp_path):
        full = exportar_cartucho(destino=tmp_path / "full")
        inc = exportar_cartucho(destino=tmp_path / "inc", desde="2026-02-01")
        assert Path(inc["zip"]).stat().st_size < Path(full["zip"]).stat().st_size

    def test_el_artefacto_pasa_el_contrato(self, db_con_historia, tmp_path):
        """El parcial se construye con el esquema del app: tiene que servir."""
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            with zipfile.ZipFile(r["zip"]) as zf:
                zf.extractall(tmp)
            c = sqlite3.connect(tmp / "historial.db")
            try:
                rep = ventas_db.verificar_contrato(c)
            finally:
                c.close()
            assert rep["ok"], f"el parcial no cumple el contrato: {rep}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rango_vacio_no_exporta(self, db_con_historia, tmp_path):
        # Rango futuro: la DB tiene 2026, asi que 2030 no trae nada.
        with pytest.raises(ValueError, match="no tiene filas"):
            exportar_cartucho(destino=tmp_path / "vacio", desde="2030-01-01")

    def test_fecha_mal_formada_no_exporta(self, db_con_historia, tmp_path):
        with pytest.raises(ValueError, match="fecha invalida"):
            exportar_cartucho(destino=tmp_path / "malo", desde="15/01/2026")

    def test_rango_invertido_no_exporta(self, db_con_historia, tmp_path):
        with pytest.raises(ValueError, match="rango invertido"):
            exportar_cartucho(destino=tmp_path / "inv", desde="2026-03-01", hasta="2026-01-01")


class TestImportDeIncremental:
    def test_una_pc_con_historia_mergea(self, db_con_historia, tmp_path):
        """El caso real: el cliente ya tiene enero, el cartucho trae feb+mar."""
        conn = db_con_historia
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")

        res = importar_cartucho(r["zip"])
        assert res["modo"] == "mergear", "no debe reemplazar: el cliente tiene enero"

        conn = ventas_db.get_conn()
        dias = sorted(
            r[0] for r in conn.execute("SELECT DISTINCT substr(fecha_orig,1,10) FROM ventas")
        )
        assert dias == ["2026-01-15", "2026-02-15", "2026-03-15"], "no debe perder enero"

    def test_forzar_reemplazar_no_pierde_el_historico(self, db_con_historia, tmp_path):
        """El guard: un parcial NUNCA reemplaza, ni si lo piden a mano."""
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")

        res = importar_cartucho(r["zip"], forzar="reemplazar")
        assert res["modo"] == "mergear"

        conn = ventas_db.get_conn()
        dias = sorted(
            r[0] for r in conn.execute("SELECT DISTINCT substr(fecha_orig,1,10) FROM ventas")
        )
        assert "2026-01-15" in dias, "forzar=reemplazar no puede borrar el historial local"

    def test_una_pc_solo_con_enero_recibe_feb_mar(self, db_con_historia, tmp_path):
        """Simula dos maquinas: la fuente exporta, la cliente solo tiene enero.

        Aqui export e import comparten la misma DB (un solo tmp_db), asi que el
        orden importa: primero se exporta el incremental con la historia
        completa, y despues se borra feb/mar para dejar a la DB en estado de
        "cliente que solo tiene enero".
        """
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")

        conn = ventas_db.get_conn()
        conn.execute("DELETE FROM ventas WHERE fecha_orig >= '2026-02-01'")
        conn.execute("DELETE FROM day_checksums WHERE dia >= '2026-02-01'")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 1, (
            "la cliente arranca con solo enero"
        )

        res = importar_cartucho(r["zip"])
        assert res["modo"] == "mergear"

        dias = sorted(
            x[0] for x in conn.execute("SELECT DISTINCT substr(fecha_orig,1,10) FROM ventas")
        )
        assert dias == ["2026-01-15", "2026-02-15", "2026-03-15"]

    def test_el_derivado_queda_fiel(self, db_con_historia, tmp_path):
        """Mergear tambien recalcula: sin esto el dashboard no cuadra."""
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        res = importar_cartucho(r["zip"])
        assert res["modo"] == "mergear"

        conn = ventas_db.get_conn()
        agg = {
            (r[0], r[1], r[2]): r[3]
            for r in conn.execute(
                "SELECT id_cliente, id_vendedor, mes, n_docs FROM agg_cliente_mes"
            )
        }
        real = {
            (r[0], r[1], r[2]): r[3]
            for r in conn.execute(
                "SELECT id_cliente, id_vendedor, substr(fecha_orig,1,7), "
                "COUNT(DISTINCT serie_doc || '-' || nro_doc) FROM ventas GROUP BY 1,2,3"
            )
        }
        assert agg == real
        assert res.get("agregados_recalculados") is True

    def test_el_allowlist_no_lo_toca(self, db_con_historia, tmp_path):
        """El cliente con otras lineas las conserva: el cartucho es info."""
        antes = list(ventas_db.allowed_lines())
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-02-01")
        importar_cartucho(r["zip"])
        assert list(ventas_db.allowed_lines()) == antes
