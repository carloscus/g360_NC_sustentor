"""Backups de la DB: que el de una operacion destructiva exista SIEMPRE.

`backup_db()` sin `force` devuelve None si ya hay backup del dia, y ese None
lo descartaba `import_db` antes de reemplazar la DB: la UI anunciaba "backup
automatico" y en realidad no habia copia del estado que se iba a pisar. Con la
captura diaria ya hecha, el respaldo previo nunca se tomaba.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.core import ventas_db
from src.core.ventas_db_backup import backup_db, backup_dir

# Mismo juego de columnas que usan los demas tests del repo: `tmp_db` crea
# `ventas` con el esquema real, con CHECKs y NOT NULL que no se pueden
# inventar (mes_ref, fecha_orig con GLOB de fecha valida).
_INSERT = (
    "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
    "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _fila(i: int) -> tuple:
    dia = 1 + (i % 28)  # el CHECK de fecha_orig exige un dia real
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


class _RelojFijo(datetime):
    """datetime congelado: fuerza dos backups al mismo segundo."""

    _fijado: datetime | None = None

    def __new__(cls, *a, **kw):
        if cls._fijado is not None and not a:
            return cls._fijado
        return datetime.__new__(cls, *a, **kw)

    @classmethod
    def now(cls, tz=None):
        return cls._fijado or datetime.now(tz)


@pytest.fixture
def db_con_datos(tmp_db):
    """DB con el esquema real de `ventas` (la crea tmp_db) y unas filas."""
    p = ventas_db.db_path()
    c = sqlite3.connect(p)
    c.executemany(_INSERT, [_fila(i) for i in range(1, 4)])
    c.commit()
    c.close()
    return p


def _agregar_fila(p: Path, n: int) -> None:
    c = sqlite3.connect(p)
    c.execute(_INSERT, _fila(n))
    c.commit()
    c.close()


def _backups() -> list[Path]:
    d = backup_dir()
    return sorted(d.glob("historial_*.db")) if d.exists() else []


def _filas_de(p: Path) -> int:
    c = sqlite3.connect(p)
    try:
        return c.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    finally:
        c.close()


def _origen_minimo(origen: Path) -> Path:
    """DB origen valida: se clona el esquema real y queda con una sola fila."""
    shutil.copy2(ventas_db.db_path(), origen)
    c = sqlite3.connect(origen)
    c.execute("DELETE FROM ventas")
    c.execute(_INSERT, _fila(1))
    c.commit()
    c.close()
    return origen


class TestBackupForzado:
    def test_el_forzado_no_se_salta_si_ya_hay_daily(self, db_con_datos):
        """El caso del bug: daily hecho, la DB crece, y aun asi hay copia."""
        diario = backup_db()
        assert diario is not None, "el primer backup del dia debe hacerse"

        _agregar_fila(db_con_datos, 99)

        forzado = backup_db(force=True, motivo="import")
        assert forzado is not None, "con force=True nunca se saltea"
        assert forzado != diario
        assert _filas_de(forzado) == _filas_de(db_con_datos), "la copia es del estado ACTUAL"

    def test_el_forzado_lleva_el_motivo_en_el_nombre(self, db_con_datos):
        b = backup_db(force=True, motivo="import")
        assert b is not None
        assert "pre-import" in b.name

    def test_el_diario_si_respeta_el_deduplicado(self, db_con_datos):
        assert backup_db() is not None
        assert backup_db() is None, "el daily no se repite el mismo dia"

    def test_sin_db_no_hay_backup(self, tmp_db):
        ventas_db.db_path().unlink(missing_ok=True)
        assert backup_db(force=True) is None


class TestRotacion:
    def test_conserva_solo_max_keep(self, db_con_datos):
        """La DB pesa varios GB: las copias completas se acumulan solas."""
        for i in range(5):
            assert backup_db(force=True, motivo=f"t{i}") is not None
        assert len(_backups()) == 2, f"debian quedar 2, quedaron {len(_backups())}"

    def test_la_rotacion_conserva_el_mas_reciente(self, db_con_datos):
        """La rotacion corre en CADA backup, no solo al final.

        Con max_keep=2, al crear el tercero el primero ya fue expulsado: no se
        pueden guardar referencias a archivos que la rotacion borro. Los
        mtime se fijan a mano porque la resolucion del reloj no alcanza para
        distinguir backups seguidos en el mismo tick.
        """
        ahora = time.time()

        viejo = backup_db(force=True, motivo="viejo")
        assert viejo is not None
        os.utime(viejo, (ahora - 300, ahora - 300))

        nuevo = backup_db(force=True, motivo="nuevo")
        assert nuevo is not None
        os.utime(nuevo, (ahora - 200, ahora - 200))

        # El tercero expulsa al mas viejo (por fecha), no a otro.
        tercero = backup_db(force=True, motivo="tercero")
        assert tercero is not None

        restantes = {p.name for p in _backups()}
        assert len(restantes) == 2, f"debian quedar 2: {restantes}"
        assert viejo.name not in restantes, "el mas viejo por fecha debe caer"
        assert nuevo.name in restantes

    def test_ordena_por_fecha_y_no_por_nombre(self, db_con_datos):
        """El prefijo "pre-" ordena despues de "2026..." alfabeticamente.

        Con max_keep chico, ordenar por nombre haria que un forzado viejo se
        tomara por nuevo y la rotacion expulsara al diario correcto.
        """
        diario = backup_db()
        forzado = backup_db(force=True, motivo="import")
        assert diario is not None and forzado is not None

        ahora = time.time()
        os.utime(diario, (ahora - 3600, ahora - 3600))
        os.utime(forzado, (ahora, ahora))

        backup_db(force=True, motivo="extra")
        restantes = {p.name for p in _backups()}
        assert len(restantes) == 2
        assert forzado.name in restantes, "el mas nuevo por fecha debe sobrevivir"
        assert diario.name not in restantes

    def test_no_borra_los_tmp_a_medio_copiar(self, db_con_datos):
        backup_db(force=True, motivo="uno")
        tmp = backup_dir() / ".tmp_en_curso.db"
        tmp.write_bytes(b"basura")
        backup_db(force=True, motivo="dos")
        assert tmp.exists(), "un .tmp en curso no es un backup viejo: no se toca"
        tmp.unlink()

    def test_dos_forzados_en_el_mismo_segundo_no_se_pisan(self, db_con_datos, monkeypatch):
        """El timestamp es de 1 segundo: dos forzados seguidos colisionaban.

        El segundo copy2 sobreescribia al primero y la rotacion contaba una
        sola copia: creia tener red de seguridad donde no la tenia.
        """
        from src.core import ventas_db_backup as mod

        _RelojFijo._fijado = datetime(2026, 10, 10, 18, 57, 17)
        monkeypatch.setattr(mod, "datetime", _RelojFijo)
        try:
            a = backup_db(force=True, motivo="x")
            b = backup_db(force=True, motivo="x")
            c = backup_db(force=True, motivo="x")
        finally:
            _RelojFijo._fijado = None
        assert a and b and c
        assert len({a.name, b.name, c.name}) == 3, "los tres deben coexistir"


class TestLlamadoresDestructivos:
    """`import_db` llama a `ventas_db.backup_db` (modulo re-exportado), no al
    de `ventas_db_backup`: el parche va sobre `ventas_db`."""

    def test_import_db_aborta_sin_backup(self, db_con_datos, monkeypatch):
        """Si el respaldo falla, NO se reemplaza la DB."""
        from src.core import ventas_db_backup as mod

        filas_antes = _filas_de(db_con_datos)
        origen = _origen_minimo(db_con_datos.parent / "origen.db")
        monkeypatch.setattr(ventas_db, "backup_db", lambda **kw: None)

        with pytest.raises(RuntimeError, match="respaldar"):
            mod.import_db(origen)

        assert _filas_de(db_con_datos) == filas_antes, "la DB local no debe tocarse"

    def test_import_db_pide_backup_forzado(self, db_con_datos, monkeypatch):
        """Que el daily no pueda saltarse el respaldo previo."""
        from src.core import ventas_db_backup as mod

        visto: dict = {}

        def _spy(**kw):
            visto.update(kw)
            return None  # forzamos el camino de abortar

        monkeypatch.setattr(ventas_db, "backup_db", _spy)
        origen = _origen_minimo(db_con_datos.parent / "origen2.db")

        with pytest.raises(RuntimeError):
            mod.import_db(origen)

        assert visto.get("force") is True, "import_db debe pedir backup forzado"
        assert visto.get("motivo") == "import"
