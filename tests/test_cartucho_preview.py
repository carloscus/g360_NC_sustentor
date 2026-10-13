"""Previsualizar antes de extraer: confirmar ANTES de lo pesado.

Elegir un ZIP disparaba extraccion (39s) + validacion (~2.5 min) + backup
(~3 min) en silencio, y recien al final se sabia si fue reemplazo o merge.
`previsualizar_cartucho` lee SOLO el manifiesto (instantaneo, sin extraer)
y decide el modo, para pedir confirmacion antes.
"""

import sqlite3
import zipfile
from pathlib import Path

from src.core import ventas_db
from src.core.cartucho import MANIFIESTO, exportar_cartucho, previsualizar_cartucho

_INSERT = (
    "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
    "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _fila(dia: str, nro: str) -> tuple:
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
        10.0,
        int(dia[:4]),
        int(dia[5:7]),
    )


def _db_con(dias: list[str]):
    p = ventas_db.db_path()
    c = sqlite3.connect(p)
    c.execute(ventas_db.CREATE_TABLE_VENTAS)
    c.executescript(ventas_db.CREATE_AUDIT_TABLES)
    c.executescript(ventas_db.CREATE_DIM_TABLES)
    c.executescript(ventas_db.CREATE_FACT_TABLES)
    c.execute("PRAGMA user_version = 3")
    for i, d in enumerate(dias, 1):
        c.execute(_INSERT, _fila(d, str(i)))
    c.commit()
    c.close()
    conn = ventas_db.get_conn()
    for d in dias:
        ventas_db.record_day_checksum(conn, d)
    conn.commit()
    return p


class TestPrevisualizar:
    def test_sin_extraer(self, tmp_db, tmp_path, monkeypatch):
        """No debe crear carpetas temp ni tocar el ZIP: solo lee el manifiesto."""
        import tempfile

        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c")
        zpath = Path(r["zip"])

        antes = set(Path(tempfile.gettempdir()).glob("cartucho-*"))
        prev = previsualizar_cartucho(zpath)
        despues = set(Path(tempfile.gettempdir()).glob("cartucho-*"))

        assert despues == antes, "previsualizar no debe extraer nada"
        assert prev["ventas_filas"] == 1
        assert prev["zip_mb"] is not None and prev["zip_mb"] >= 0

    def test_incremental_siempre_mergea(self, tmp_db, tmp_path):
        """Un parcial nunca reemplaza, aunque cubra todo lo local."""
        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-09-01")
        prev = previsualizar_cartucho(r["zip"])
        assert prev["modo"] == "mergear"
        assert prev["incremental"] is True

    def test_completo_con_mas_datos_reemplaza(self, tmp_db, tmp_path):
        """Si el cartucho cubre todo lo local, el modo es reemplazar."""
        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c")
        prev = previsualizar_cartucho(r["zip"])
        assert prev["incremental"] is False
        assert prev["modo"] == "reemplazar"
        assert prev["fecha_min"] == "2026-09-28"

    def test_carpeta_tambien_funciona(self, tmp_db, tmp_path):
        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c", comprimir=False)

        # exportar con comprimir=False deja la carpeta
        carpeta = Path(r["carpeta"])
        assert (carpeta / MANIFIESTO).exists()
        prev = previsualizar_cartucho(carpeta)
        assert prev["ventas_filas"] == 1
        assert prev["zip_mb"] is None

    def test_archivo_invalido_levanta(self, tmp_path):
        malo = tmp_path / "nada.zip"
        with zipfile.ZipFile(malo, "w") as zf:
            zf.writestr("otro.txt", "hola")
        import pytest

        with pytest.raises(ValueError, match="manifiesto"):
            previsualizar_cartucho(malo)

    def test_ruta_inexistente_levanta(self, tmp_path):
        import pytest

        with pytest.raises(ValueError):
            previsualizar_cartucho(tmp_path / "noexiste.zip")


class TestModoVsImportReal:
    """El modo anticipado tiene que coincidir con el que decide el import."""

    def test_coinciden(self, tmp_db, tmp_path):
        from src.core.cartucho import importar_cartucho

        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c", desde="2026-09-01")
        prev = previsualizar_cartucho(r["zip"])
        res = importar_cartucho(r["zip"])
        assert res["modo"] == prev["modo"] == "mergear"


class TestFrescura:
    """El receptor decide reemplazo vs suma mirando qué tan fresca es la copia."""

    def test_exportar_devuelve_generado_en_y_por(self, tmp_db, tmp_path):
        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c")
        assert r.get("generado_en"), "el resultado debe traer el timestamp"
        assert r.get("generado_por"), "el resultado debe traer el origen"

    def test_preview_expone_frescura(self, tmp_db, tmp_path):
        _db_con(["2026-09-28"])
        r = exportar_cartucho(destino=tmp_path / "c")
        prev = previsualizar_cartucho(r["zip"])
        assert prev.get("generado_en") == r["generado_en"]
        assert prev.get("generado_por") == r["generado_por"]
        assert prev.get("frescura") == "recién generado"

    def test_frescura_relativa(self):
        from datetime import datetime, timedelta

        from src.core.cartucho import frescura_cartucho

        ahora = datetime(2026, 10, 13, 12, 0, 0)
        assert frescura_cartucho(ahora.isoformat(), ahora=ahora) == "recién generado"
        assert (
            frescura_cartucho((ahora - timedelta(minutes=5)).isoformat(), ahora=ahora)
            == "hace 5 min"
        )
        assert (
            frescura_cartucho((ahora - timedelta(hours=3)).isoformat(), ahora=ahora) == "hace 3 h"
        )
        assert (
            frescura_cartucho((ahora - timedelta(days=2)).isoformat(), ahora=ahora) == "hace 2 días"
        )
        assert frescura_cartucho(None) == ""
        assert frescura_cartucho("no-es-fecha") == ""
