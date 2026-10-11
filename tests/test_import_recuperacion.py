"""Reemplazar la DB: que un fallo a mitad no deje el historial inservible.

La copia es IN-PLACE sobre el archivo vivo (en Windows no se puede borrar ni
renombrar con otro hilo abierto en WAL). Eso significa que un fallo a mitad
deja paginas viejas mezcladas con nuevas. Las dos redes son:

1. `_chequear_espacio`: el fallo mas probable (disco lleno) se detecta antes
   de tocar el archivo.
2. `_recuperar`: si aun asi falla, se restaura desde el backup previo y el
   error lo dice explicitamente.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from src.core import ventas_db
from src.core import ventas_db_backup as mod
from src.core.ventas_db_backup import backup_db

_INSERT = (
    "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
    "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _fila(i: int) -> tuple:
    dia = 1 + (i % 28)
    return (
        f"A{i}",
        f"L{i}",
        "C001",
        "F01",
        "001",
        str(i),
        f"2026-01-{dia:02d}",
        "2026-01",
        1.0,
        float(i * 100),
        2026,
        1,
    )


@pytest.fixture
def db_con_datos(tmp_db):
    p = ventas_db.db_path()
    c = sqlite3.connect(p)
    c.executemany(_INSERT, [_fila(i) for i in range(1, 4)])
    c.commit()
    c.close()
    return p


def _filas_de(p: Path) -> int:
    c = sqlite3.connect(p)
    try:
        return c.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    finally:
        c.close()


def _origen(origen: Path) -> Path:
    shutil.copy2(ventas_db.db_path(), origen)
    c = sqlite3.connect(origen)
    c.execute("DELETE FROM ventas")
    c.execute(_INSERT, _fila(1))
    c.commit()
    c.close()
    return origen


def _disco_falso(monkeypatch, libre: int):
    """Parchea disk_usage para simular X bytes libres."""

    class _Uso:
        def __init__(self, free):
            self.free = free
            self.total = 100 * 1024**3
            self.used = self.total - free

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Uso(libre))


class TestChequeoDeEspacio:
    def test_aborta_si_no_hay_espacio(self, db_con_datos, monkeypatch, tmp_path):
        destino = ventas_db.db_path()
        origen = _origen(tmp_path / "grande.db")
        _disco_falso(monkeypatch, 10)  # 10 bytes

        with pytest.raises(RuntimeError, match="Espacio insuficiente"):
            mod._chequear_espacio(origen, destino)

    def test_el_margen_cubre_el_wal(self, db_con_datos, monkeypatch, tmp_path):
        """Que 'justo' no cuente como alcanza: el WAL tambien necesita."""
        destino = ventas_db.db_path()
        origen = _origen(tmp_path / "grande2.db")
        crecimiento = max(0, origen.stat().st_size - destino.stat().st_size)
        _disco_falso(monkeypatch, crecimiento + 1024)

        with pytest.raises(RuntimeError, match="Espacio insuficiente"):
            mod._chequear_espacio(origen, destino)

    def test_pasa_si_hay_espacio(self, db_con_datos, monkeypatch, tmp_path):
        destino = ventas_db.db_path()
        origen = _origen(tmp_path / "grande3.db")
        _disco_falso(monkeypatch, 50 * 1024**3)

        mod._chequear_espacio(origen, destino)  # no debe levantar

    def test_sin_medicion_no_bloquea(self, db_con_datos, monkeypatch, tmp_path):
        """No se puede medir el disco: el backup previo ya es la red."""
        destino = ventas_db.db_path()
        origen = _origen(tmp_path / "grande4.db")

        def _boom(_p):
            raise OSError("no se pudo consultar")

        monkeypatch.setattr(shutil, "disk_usage", _boom)
        mod._chequear_espacio(origen, destino)


class TestRecuperacion:
    def test_restaura_la_db_y_lo_dice(self, db_con_datos):
        destino = ventas_db.db_path()
        bkp = backup_db(force=True, motivo="red")
        filas_antes = _filas_de(destino)

        # Simula una copia a medias: se pisa el encabezado del archivo vivo.
        with open(destino, "r+b") as fh:
            fh.seek(0)
            fh.write(b"SQLITE\x00" + b"\xde\xad\xbe\xef" * 64)

        with pytest.raises(RuntimeError) as ex:
            mod._recuperar(bkp, destino, OSError("disco lleno"), "La copia fallo")

        assert "restauro" in str(ex.value).lower()
        assert _filas_de(destino) == filas_antes, "la DB debe quedar como estaba"

    def test_sin_backup_no_promete_restaurar(self, db_con_datos):
        with pytest.raises(RuntimeError) as ex:
            mod._recuperar(None, ventas_db.db_path(), OSError("x"), "La copia fallo")
        msg = str(ex.value).lower()
        assert "no habia backup" in msg
        assert "restauro" not in msg, "no se puede decir que restauro si no habia con que"

    def test_el_error_incluye_la_causa_original(self, db_con_datos):
        bkp = backup_db(force=True, motivo="causa")
        with pytest.raises(RuntimeError) as ex:
            mod._recuperar(bkp, ventas_db.db_path(), OSError("disco lleno"), "La copia fallo")
        assert "disco lleno" in str(ex.value)


class TestImportVerificaAntesDeSeguir:
    def test_si_la_copia_falla_se_restaura_y_no_sigue(self, db_con_datos, monkeypatch, tmp_path):
        """El cableado: copia que lanza -> recuperar -> error, sin seguir.

        No se puede "corromper antes" para probarlo (la copia reemplaza el
        archivo entero y lo deja bien). Lo que importa es el cableado: cuando
        la copia falla a mitad, `import_db` restaura y aborta en vez de seguir
        a reconstruir indices sobre una base a medias.
        """
        destino = ventas_db.db_path()
        origen = _origen(tmp_path / "orig.db")
        filas_antes = _filas_de(destino)

        real = mod.backup_db
        monkeypatch.setattr(
            ventas_db, "backup_db", lambda *a, **kw: real(force=True, motivo="import")
        )

        # Proxy que hace fallar SOLO la primera backup() (la del reemplazo);
        # la segunda es la restauracion y tiene que poder pasar. Solo se
        # envuelven las conexiones abiertas con uri=True (el origen): la
        # backup API exige un sqlite3.Connection real como destino, asi que
        # el destino tiene que pasar intacto.
        estado = {"fallos": 1}
        connect_real = mod.sqlite3.connect

        class _Proxy:
            def __init__(self, conn):
                self._c = conn

            def __getattr__(self, n):
                return getattr(self._c, n)

            def backup(self, *a, **kw):
                if estado["fallos"] > 0:
                    estado["fallos"] -= 1
                    raise sqlite3.OperationalError("database or disk is full")
                return self._c.backup(*a, **kw)

            def close(self):
                return self._c.close()

        def _connect(*a, **kw):
            conn = connect_real(*a, **kw)
            return _Proxy(conn) if kw.get("uri") else conn

        monkeypatch.setattr(mod.sqlite3, "connect", _connect)

        with pytest.raises(RuntimeError) as ex:
            mod.import_db(origen)

        assert estado["fallos"] == 0, "la copia del reemplazo tiene que fallar"
        assert "restauro" in str(ex.value).lower()
        assert _filas_de(destino) == filas_antes, "la DB debe quedar como estaba"

    def test_un_import_limpio_deja_la_db_sana(self, db_con_datos, monkeypatch, tmp_path):
        """Y el camino feliz sigue funcionando (no rompimos nada)."""
        origen = _origen(tmp_path / "ok.db")

        real = mod.backup_db
        monkeypatch.setattr(
            ventas_db, "backup_db", lambda *a, **kw: real(force=True, motivo="import")
        )

        stats = mod.import_db(origen)
        assert stats["filas"] == 1, "el origen reemplaza al local"
        c = sqlite3.connect(ventas_db.db_path())
        try:
            assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            c.close()
