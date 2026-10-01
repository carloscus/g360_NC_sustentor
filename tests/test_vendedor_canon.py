"""Canónico de vendedor y auditoría post-ingesta.

El id de vendedor vive pelado en 3 caracteres (sufijo del ERP: '178', 'M17',
'052'). Una ingesta puede dejarlo con el prefijo de empresa sin quitar
('01178') o recortarle un carácter de más ('178' -> '78'). Ambas cosas cuelgan
al vendedor del reporte sin avisar, así que se fija el contrato con tests y se
audita la DB después de cada sincronización.
"""

import sqlite3

import pytest

from src.core import ventas_db
from src.core.xls_processor import (
    normalize_seller_id,
    parse_report_rows,
    vendedor_corto,
)

# (id como lo emite el ERP, id canónico de 3 chars)
CANONICOS = [
    ("01177", "177"),
    ("01052", "052"),
    ("01M17", "M17"),
    ("01I02", "I02"),
    ("01178", "178"),
    ("01A02", "A02"),
    ("01208", "208"),
    ("01112", "112"),
]

HEADER = [
    "ANHO",
    "MES",
    "ID_CLIENTE",
    "DOC_CLIENTE",
    "NOM_CLIENTE",
    "ID_LINEA",
    "NOM_LINEA",
    "ID_ARTICULO",
    "NOM_ARTICULO",
    "ID_VENDEDOR",
    "NOM_VENDEDOR",
    "TPO_DOC",
    "SERIE_DOC",
    "NRO_DOC",
    "FECHA_ORIG",
    "CANTIDAD",
    "SOLES",
]


def fila(**over):
    row = [
        "2026",
        "9",
        "00004884",
        "20439429721",
        "DISTRIBUCIONES CONTINENTAL",
        "01",
        "GASEOSAS",
        "000123",
        "GASEOSA 3L",
        "01178",
        "MILCA SARAY REYES",
        "F01",
        "001",
        "1",
        "15/09/2026",
        "10",
        "250,00",
    ]
    mapping = {name: i for i, name in enumerate(HEADER)}
    for k, v in over.items():
        row[mapping[k]] = v
    return row


class TestNormalizacion:
    @pytest.mark.parametrize("larga,corta", CANONICOS)
    def test_quita_prefijo_y_no_un_char_mas(self, larga, corta):
        assert normalize_seller_id(larga) == corta
        assert vendedor_corto(larga) == corta

    @pytest.mark.parametrize("codigo", [c for _, c in CANONICOS])
    def test_idempotente(self, codigo):
        """El canónico es punto fijo: normalizar dos veces no recorta más."""
        assert normalize_seller_id(normalize_seller_id(codigo)) == codigo
        assert vendedor_corto(vendedor_corto(codigo)) == codigo

    @pytest.mark.parametrize("codigo", [c for _, c in CANONICOS])
    def test_no_toca_lo_ya_pelado(self, codigo):
        assert normalize_seller_id(codigo) == codigo
        assert vendedor_corto(codigo) == codigo

    @pytest.mark.parametrize("larga,corta", CANONICOS)
    def test_todo_sale_de_3_chars(self, larga, corta):
        """Sobre-recorte (el bug real): '01178' -> '78', nunca."""
        assert len(normalize_seller_id(larga)) == 3
        assert len(vendedor_corto(larga)) == 3

    def test_solo_quita_el_prefijo_a_codigos_de_4_mas(self):
        """Un código de 3 chars es canónico aunque empiece con '01'."""
        for cod in ("011", "010", "012", "017"):
            assert normalize_seller_id(cod) == cod
            assert vendedor_corto(cod) == cod

    @pytest.mark.parametrize("basura", ["", None, "nan", "None"])
    def test_vacios(self, basura):
        assert normalize_seller_id(basura) == ""
        assert vendedor_corto(basura) == ""


class TestParseReport:
    def test_intranet_entra_pelado(self):
        """Cada fila conserva su vendedor; el 5to carácter nunca se pierde."""
        filas = [HEADER] + [fila(ID_VENDEDOR=larga) for larga, _ in CANONICOS]
        out = parse_report_rows(filas, "2026-09", "ventas_2026-09-26.xls")
        assert [v["id_vendedor"] for v in out.ventas] == [c for _, c in CANONICOS]

    def test_fila_con_prefijo_01_peligroso(self):
        """'01178' es el caso que se rompió en producción."""
        out = parse_report_rows([HEADER, fila(ID_VENDEDOR="01178")], "2026-09", "x.xls")
        assert out.ventas[0]["id_vendedor"] == "178"

    def test_no_duplica_por_repetir_prefijo(self):
        """'01178' y '178' son el mismo vendedor, no dos carteras."""
        out = parse_report_rows(
            [HEADER, fila(ID_VENDEDOR="01178"), fila(ID_VENDEDOR="178", NRO_DOC="2")],
            "2026-09",
            "x.xls",
        )
        assert {v["id_vendedor"] for v in out.ventas} == {"178"}


def _db(tmp_path, name="historial.db"):
    conn = sqlite3.connect(str(tmp_path / name))
    conn.execute(ventas_db.CREATE_TABLE_VENTAS)
    conn.execute("CREATE TABLE dim_vendedor (id_vendedor TEXT PRIMARY KEY, nom_vendedor TEXT)")
    return conn


def _vender(conn, vendedor, n=1, mes=9, nombre="MILCA SARAY REYES"):
    for i in range(n):
        conn.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, "
            "serie_doc, nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes, "
            "id_vendedor, nom_vendedor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "000123",
                "01",
                "00004884",
                "F01",
                "001",
                str(1000 + i),
                "2026-09-15",
                "2026-09",
                10.0,
                250.0,
                2026,
                mes,
                vendedor,
                nombre,
            ),
        )


class TestAuditoria:
    def test_db_sana_no_reporta_nada(self, tmp_path):
        conn = _db(tmp_path)
        for _, corta in CANONICOS:
            conn.execute("INSERT INTO dim_vendedor VALUES (?,?)", (corta, "V"))
            _vender(conn, corta)
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is True
        assert r["malformados"] == []
        assert r["n_malformados"] == 0
        conn.close()

    def test_detecta_prefijo_01(self, tmp_path):
        conn = _db(tmp_path)
        conn.execute("INSERT INTO dim_vendedor VALUES ('178','MILCA')")
        _vender(conn, "01178", n=4)
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is False
        assert r["n_malformados"] == 1
        assert r["filas_malformadas"] == 4
        assert r["malformados"][0]["id"] == "01178"
        assert r["malformados"][0]["sugerencia"] == "178"
        conn.close()

    def test_detecta_sobre_recorte(self, tmp_path):
        """El bug del 26/09: el prefijo '01' se quitó dos veces ('178'->'78')."""
        conn = _db(tmp_path)
        conn.execute("INSERT INTO dim_vendedor VALUES ('178','MILCA')")
        _vender(conn, "78", n=7)
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is False
        assert r["malformados"][0]["id"] == "78"
        assert r["malformados"][0]["sugerencia"] == "178"
        assert r["malformados"][0]["n"] == 7
        assert r["malformados"][0]["desde"] == "2026-09"
        conn.close()

    def test_detecta_todos_los_tipos(self, tmp_path):
        conn = _db(tmp_path)
        for cod in ("178", "M17", "052", "170"):
            conn.execute("INSERT INTO dim_vendedor VALUES (?,?)", (cod, "V"))
        _vender(conn, "01178", n=2)  # prefijo de empresa sin quitar
        _vender(conn, "70", n=3)  # sobre-recorte de 170
        _vender(conn, "17", n=1)  # sobre-recorte de M17
        _vender(conn, "52", n=5)  # sobre-recorte de 052
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["n_malformados"] == 4
        assert r["filas_malformadas"] == 11
        assert {m["id"] for m in r["malformados"]} == {"01178", "70", "17", "52"}
        assert {m["sugerencia"] for m in r["malformados"]} == {"178", "170", "M17", "052"}
        conn.close()

    def test_vendedor_nuevo_no_es_error(self, tmp_path):
        """Un id de 3 chars fuera del maestro es vendedor nuevo, no corrupción."""
        conn = _db(tmp_path)
        conn.execute("INSERT INTO dim_vendedor VALUES ('178','MILCA')")
        _vender(conn, "178", n=2)
        _vender(conn, "218", n=1, nombre="CHRIS ROJAS")
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is True
        assert r["malformados"] == []
        assert r["n_sin_maestro"] == 1
        assert r["sin_maestro"][0]["id"] == "218"
        assert r["sin_maestro"][0]["nombre"] == "CHRIS ROJAS"
        conn.close()

    def test_vacio_no_reporta(self, tmp_path):
        conn = _db(tmp_path)
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is True
        assert r["malformados"] == [] and r["sin_maestro"] == []
        conn.close()

    def test_no_sugiere_si_es_ambiguo(self, tmp_path):
        """Sin candidato único en el maestro, no inventa: sugiere None."""
        conn = _db(tmp_path)
        for cod in ("170", "270", "370"):
            conn.execute("INSERT INTO dim_vendedor VALUES (?,?)", (cod, "X"))
        _vender(conn, "70")
        conn.commit()
        r = ventas_db.auditar_vendedores(conn)
        assert r["malformados"][0]["sugerencia"] is None
        conn.close()

    def test_sobre_la_db_local(self):
        """Guard contra la DB real: hoy no debe haber vendedores malformados."""
        if not ventas_db.db_exists():
            pytest.skip("no hay DB local")
        r = ventas_db.auditar_vendedores()
        if r["malformados"]:
            detalle = ", ".join(
                f"{m['id']}→{m['sugerencia'] or '?'} ({m['n']})" for m in r["malformados"][:8]
            )
            pytest.fail(f"vendedores malformados en la DB local: {detalle}")
