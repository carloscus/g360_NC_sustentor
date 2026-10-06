"""Tests del gate de contrato de `ventas`.

`verificar_contrato` bloquea cuando el archivo tiene una forma distinta a la
esperada. Ese gate es lo que impide que un DB con el schema equivocado avance en
silencio, asi que no debe aflojarse por conveniencia: lo que se prueba aca es
exactamente que afloja SOLO donde tiene que aflojar.

El caso queDolió: las tablas de auditoría que crean las herramientas de
migración (`_migracion_fec_cargo`, `_migracion_fecha_ref`, ...) hacían fallar el
gate y `init_db()` levantaba ContratoDBError en una base perfectamente valida. La
app no arrancaba. Se toleran por PREFIJO (`_migracion_`), no por nombre fijo.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core import ventas_db


def _db_vacia(tmp_path, monkeypatch) -> sqlite3.Connection:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
    ventas_db.cerrar_conexiones()
    conn = ventas_db.connect(readonly=False)
    conn.executescript(ventas_db.CREATE_TABLE_VENTAS)
    conn.executescript(ventas_db.CREATE_AUDIT_TABLES)
    conn.executescript(ventas_db.CREATE_DIM_TABLES)
    conn.executescript(ventas_db.CREATE_FACT_TABLES)
    for stmt in ventas_db.CREATE_INDEXES:
        conn.execute(stmt)
    conn.execute(f"PRAGMA user_version = {ventas_db.CONTRACTO_VERSION}")
    conn.commit()
    return conn


def test_db_fresco_cumple(tmp_path, monkeypatch):
    conn = _db_vacia(tmp_path, monkeypatch)
    try:
        rep = ventas_db.verificar_contrato(conn)
        assert rep["ok"], rep["detalle"]
    finally:
        conn.close()
        ventas_db.cerrar_conexiones()


def test_tablas_de_auditoria_no_rompen_el_contrato(tmp_path, monkeypatch):
    """Lo que rompia la app: _migracion_* debe tolerarse."""
    conn = _db_vacia(tmp_path, monkeypatch)
    try:
        for t in ("_migracion_fec_cargo", "_migracion_fecha_ref", "_migracion_fecha_venc"):
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, col TEXT)")
        conn.commit()

        rep = ventas_db.verificar_contrato(conn)
        assert rep["ok"], f"una tabla de auditoria no debe romper el contrato: {rep['detalle']}"
        assert rep["tablas_extra_no_toleradas"] == []
        assert "_migracion_fec_cargo" in rep["tablas_auditoria"]
        assert len(rep["tablas_auditoria"]) == 3
    finally:
        conn.close()
        ventas_db.cerrar_conexiones()


def test_auditoria_se_tolera_por_prefijo_no_por_nombre(tmp_path, monkeypatch):
    """Una migracion nueva no debe requerir tocar el codigo."""
    conn = _db_vacia(tmp_path, monkeypatch)
    try:
        conn.execute("CREATE TABLE _migracion_indice_nuevo (id INTEGER PRIMARY KEY)")
        conn.commit()
        rep = ventas_db.verificar_contrato(conn)
        assert rep["ok"], rep["detalle"]
        assert rep["tablas_auditoria"] == ["_migracion_indice_nuevo"]
    finally:
        conn.close()
        ventas_db.cerrar_conexiones()


def test_tabla_extra_desconocida_sigue_rompiendo(tmp_path, monkeypatch):
    """El gate NO se afloja: una tabla que no conocemos es deriva de schema."""
    conn = _db_vacia(tmp_path, monkeypatch)
    try:
        conn.execute("CREATE TABLE tabla_desconocida (id INTEGER PRIMARY KEY)")
        conn.commit()

        rep = ventas_db.verificar_contrato(conn)
        assert not rep["ok"], "una tabla desconocida debe romper el contrato"
        assert rep["tablas_extra_no_toleradas"] == ["tabla_desconocida"]
    finally:
        conn.close()
        ventas_db.cerrar_conexiones()


def test_prefijo_casi_identico_no_cuenta_como_auditoria(tmp_path, monkeypatch):
    """`_migracion` sin guion final, o `_migracionx`, NO es nuestra tabla.

    El prefijo incluye el `_` final justamente para que no se cuele cualquier
    tabla que empiece con las mismas letras.
    """
    assert ventas_db._es_auditoria("_migracion_fec_cargo")
    assert not ventas_db._es_auditoria("_migracion")
    assert not ventas_db._es_auditoria("_migracionx")
    assert not ventas_db._es_auditoria("migracion_fec_cargo")
    assert not ventas_db._es_auditoria("tablas_locales")


def test_init_db_no_levanta_con_tablas_de_auditoria(tmp_path, monkeypatch):
    """Regresion del bug reportado: init_db() fallaba y run.bat cortaba en [4/6]."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
    ventas_db.cerrar_conexiones()

    ventas_db.init_db()
    conn = ventas_db.connect(readonly=False)
    try:
        for t in ("_migracion_fec_cargo", "_migracion_fecha_ref", "_migracion_fecha_venc"):
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, col TEXT)")
        conn.commit()
    finally:
        conn.close()

    # Segundo arranque: es el caso de una DB ya migrada en produccion.
    ventas_db.cerrar_conexiones()
    ventas_db.init_db()

    rep = ventas_db.verificar_contrato()
    assert rep["ok"], rep["detalle"]
    ventas_db.cerrar_conexiones()


@pytest.mark.parametrize("falta", ["ventas"])
def test_tabla_del_contrato_faltante_rompe(tmp_path, monkeypatch, falta):
    conn = _db_vacia(tmp_path, monkeypatch)
    try:
        conn.execute(f"DROP TABLE {falta}")
        conn.commit()
        rep = ventas_db.verificar_contrato(conn)
        assert not rep["ok"]
        assert falta in rep["tablas_faltantes"]
    finally:
        conn.close()
        ventas_db.cerrar_conexiones()
