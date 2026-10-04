"""La invariante "en storage siempre ISO" tiene que estar impuesta por codigo.

No por convencion ni por el formato que mande el ERP. Antes:

- `xls_processor.parse_date` solo acceptaba dd/mm/yyyy y dd-mm-yyyy. Una fila con
  fecha ISO devolvia '' y el llamador hacia `continue`: la fila entera se
  perdia en silencio, sin error ni log.
- `ventas_db.insert_ventas` / `replace_folios` eran pass-through: lo que
  escribia el ERP era lo que quedaba en la base. De ahi que `ventas.fec_cargo`
  este en dd/mm/yyyy mientras las otras tres columnas de fecha estan en ISO.
- `fecha_iso()` no tenia ninguna llamada en produccion.

El SQL de este proyecto compara fechas como texto (ORDER BY, BETWEEN, MIN/MAX,
substr), y eso solo es cronologico con ISO. Con dd/mm/yyyy un filtro por rango
devuelve cero filas sin dar error.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.fechas import fecha_iso
from src.core.ventas_db import COLS_FECHA, _normaliza_fechas_venta
from src.core.xls_processor import parse_date


# ── El parser que perdería filas ────────────────────────────────────────────


@pytest.mark.parametrize(
    "entrada",
    [
        "15/01/2024",  # dd/mm/yyyy (lo que manda hoy la intranet)
        "15-01-2024",
        "2024-01-15",  # ISO
        "2024/01/15",
        "2024-01-15 10:30:00",
        "2024-01-15T10:30:00Z",
        "03/10/2026 08:00",
    ],
)
def test_parse_date_acepta_cualquier_formato(entrada):
    """Antes solo aceptaba dd/mm y dd-mm: la fila con ISO se perdia."""
    assert parse_date(entrada) == "2024-01-15" or parse_date(entrada) == "2026-10-03"


@pytest.mark.parametrize("malo", ["", "   ", "basura", "31/02/2024", "2024-13-45"])
def test_parse_date_rechaza_lo_ilegible_sin_raising(malo):
    assert parse_date(malo) == ""


def test_parse_date_no_revienta_con_no_string():
    assert parse_date(None) == ""


def test_parse_date_delega_en_el_modigo_comun():
    """Un solo parser en el proyecto: si no, divergen."""
    from src.core import fechas

    assert parse_date("2024-01-15") == fechas.fecha_iso("2024-01-15")


# ── Normalización al escribir ────────────────────────────────────────────────


def test_las_cuatro_columnas_de_fecha_estan_cubiertas():
    assert set(COLS_FECHA) == {"fecha_orig", "fecha_ref", "fecha_venc", "fec_cargo"}


def test_normaliza_todos_los_formatos_a_iso():
    v = {"fecha_orig": "15/01/2024", "fecha_ref": "15-01-2024", "fec_cargo": "01/09/2015"}
    salida = _normaliza_fechas_venta(v)
    assert salida["fecha_orig"] == "2024-01-15"
    assert salida["fecha_ref"] == "2024-01-15"
    assert salida["fec_cargo"] == "2015-09-01"


def test_normalizar_es_idempotente():
    v = {"fecha_orig": "2024-01-15", "fec_cargo": "2015-09-01"}
    assert _normaliza_fechas_venta(v) == v


def test_normalizar_no_destroza_una_fecha_ilegible():
    """Es preferible un dato raro a perder la fila."""
    v = {"fecha_orig": "basura"}
    assert _normaliza_fechas_venta(v)["fecha_orig"] == "basura"


@pytest.mark.parametrize("vacio", ["", None])
def test_normalizar_no_toca_vacios(vacio):
    v = {"fecha_orig": vacio}
    assert _normaliza_fechas_venta(v)["fecha_orig"] == vacio


def test_normalizar_no_copia_si_no_hay_cambios():
    """Sin cambios, devuelve el mismo dict: no paga copia en 2.8M de filas."""
    v = {"fecha_orig": "2024-01-15", "soles": 10}
    assert _normaliza_fechas_venta(v) is v


def test_normalizar_no_muta_el_entrada():
    v = {"fecha_orig": "15/01/2024"}
    _normaliza_fechas_venta(v)
    assert v["fecha_orig"] == "15/01/2024", "el dict original no debe mutarse"


# ── La invariante, sobre una DB real ────────────────────────────────────────


def test_lo_que_se_guarda_es_iso(tmp_path):
    """Extremo a extremo: insertar de verdad con formatos mezclados y leer la DB."""
    from src.core import ventas_db

    conn = sqlite3.connect(tmp_path / "t.db")
    ventas_db.init_db(conn)

    cols = [c.strip() for c in ventas_db.INSERT_COLS.split(",")]
    base = {c: None for c in cols}
    # NOT NULL sin default: hay que darles valor para que el INSERT pase.
    base.update(
        id_articulo="A1",
        id_linea="L1",
        id_cliente="C1",
        tpo_doc="F001",
        cantidad=1.0,
        soles=10.0,
        anho=2024,
        mes=1,
    )
    filas = [
        dict(base, fecha_orig="15/01/2024", fec_cargo="01/09/2015", mes_ref="2024-01"),
        dict(base, fecha_orig="2024-03-20", fec_cargo="2020-02-02", mes_ref="2024-03"),
        dict(base, fecha_orig="15-06-2024", fec_cargo="", mes_ref="2024-06"),
    ]
    for f in filas:
        n = _normaliza_fechas_venta(f)
        mr = str(n.get("mes_ref", ""))
        if len(mr) > 7:
            n = dict(n, mes_ref=mr[:7])
        conn.execute(
            f"INSERT INTO ventas ({ventas_db.INSERT_COLS}) VALUES ({','.join('?' * len(cols))})",
            [n.get(c) for c in cols],
        )
    conn.commit()

    guardadas = conn.execute(
        "SELECT fecha_orig, fec_cargo FROM ventas ORDER BY fecha_orig"
    ).fetchall()
    conn.close()

    assert len(guardadas) == 3, guardadas
    for fecha_orig, fec_cargo in guardadas:
        assert len(fecha_orig) == 10 and fecha_orig[4] == "-" and fecha_orig[7] == "-", fecha_orig
        if fec_cargo:
            assert len(fec_cargo) == 10 and fec_cargo[4] == "-" and fec_cargo[7] == "-", fec_cargo
    # Y el rango por texto tiene que ser el rango cronologico.
    orden = [r[0] for r in guardadas]
    assert orden == sorted(orden), orden


def test_el_orden_lexicografico_es_cronologico():
    """La razon de que importe: el SQL ordena por texto."""
    dias = ["15/01/2024", "03/02/2024", "28/12/2023", "01/03/2025"]
    como_texto = sorted(dias)
    como_fecha = sorted(fecha_iso(d) for d in dias)
    # Con dd/mm/yyyy el orden por texto NO coincide con el cronologico.
    assert como_texto != como_fecha, "el test dejaria de probar nada"
    # Con ISO, si.
    assert como_fecha == sorted(como_fecha)
