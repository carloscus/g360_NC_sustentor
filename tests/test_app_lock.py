"""El lock de instancia unica: que el arranque bloqueado sea accionable.

El mecanismo en si es del sistema operativo (Windows suelta el lock al morir
el proceso), asi que lo que se prueba aqui es:

1. que la proteccion NO se desactive sola (regresion real de esta sesion), y
2. que el arranque bloqueado diga QUE proceso hay que cerrar, en vez de
   obligar a ir al Administrador de tareas a buscar un python.exe a ciegas.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

_RAIZ = str(Path(__file__).resolve().parent.parent)


@pytest.fixture
def lock(tmp_path, monkeypatch):
    """Redirige LOCK_FILE/PID_FILE a temporales y restaura el estado global."""
    f = tmp_path / ".app_instance.lock"
    monkeypatch.setattr(main, "LOCK_FILE", f)
    monkeypatch.setattr(main, "PID_FILE", tmp_path / ".app_instance.pid")
    monkeypatch.delitem(main.__dict__, "_instance_fh", raising=False)
    yield f
    fh = main.__dict__.pop("_instance_fh", None)
    if fh is not None:
        fh.close()


def _arranque_en_otro_proceso(lock) -> str:
    """Ejecuta `_acquire_instance_lock` en un proceso NUEVO contra el mismo lock."""
    src = textwrap.dedent(
        f"""
        import pathlib, sys
        sys.path.insert(0, {_RAIZ!r})
        import main
        main.LOCK_FILE = pathlib.Path({str(lock)!r})
        main.PID_FILE = pathlib.Path({str(lock)!r}).with_suffix(".pid")
        print("OK" if main._acquire_instance_lock() else "BLOQUEADO")
        """
    )
    r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True)
    return r.stdout + r.stderr


class TestLockDeInstancia:
    def test_el_primer_arranque_toma_el_lock(self, lock):
        assert main._acquire_instance_lock() is True

    def test_el_segundo_proceso_se_rechaza(self, lock):
        """El bloqueo es POR PROCESO, no por handle.

        Re-tomar el lock desde el mismo proceso funciona (Windows no lo ve
        como conflicto), asi que el rechazo solo se reproduce con un proceso
        de verdad: es exactamente el caso del doble clic en el acceso directo.
        """
        assert main._acquire_instance_lock() is True
        assert "BLOQUEADO" in _arranque_en_otro_proceso(lock)

    def test_la_proteccion_sigue_activa_tras_anotar_el_pid(self, lock):
        """REGRESION: escribir en el archivo bloqueado LIBERA el lock.

        Windows invalida los byte-range locks cuando el archivo se modifica, y
        ahi la proteccion de instancia unica se desactiva sola: el segundo
        arranque pasa y no dice nada. Por eso el PID va en PID_FILE y
        LOCK_FILE, una vez bloqueado, no se vuelve a tocar.
        """
        assert main._acquire_instance_lock() is True
        assert main.PID_FILE.exists(), "el PID debe anotarse para el diagnostico"
        assert "BLOQUEADO" in _arranque_en_otro_proceso(lock)

    def test_el_lock_esta_vigente_y_el_archivo_vacio(self, lock):
        """Con el lock tomado el archivo es ilegible y no lleva datos.

        El PermissionError al leer es la prueba de que el byte-range lock
        sigue vivo; y el archivo vacio al soltar, la de que no se escribio
        nada (escribirlo lo liberaria).
        """
        assert main._acquire_instance_lock() is True
        with pytest.raises(PermissionError):
            lock.read_bytes()

        main.__dict__["_instance_fh"].close()
        main.__dict__.pop("_instance_fh")
        assert lock.read_bytes() == b""

    def test_guarda_el_pid_propio(self, lock):
        main._acquire_instance_lock()
        assert main.PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_libera_al_cerrar_el_manejador(self, lock):
        main._acquire_instance_lock()
        main.__dict__["_instance_fh"].close()
        main.__dict__.pop("_instance_fh")
        # Sin proceso vivo el lock se puede tomar de nuevo.
        assert main._acquire_instance_lock() is True

    def test_pid_ilegible_no_rompe(self, lock):
        main.PID_FILE.write_text("no-es-un-pid", encoding="utf-8")
        assert main._leer_pid_bloqueante() is None

    def test_pid_inexistente_no_rompe(self, lock):
        main.PID_FILE.unlink(missing_ok=True)
        assert main._leer_pid_bloqueante() is None

    def test_pid_propio_se_detecta_vivo(self):
        assert main._pid_vivo(os.getpid()) is True

    def test_pid_imposible_no_esta_vivo(self):
        assert main._pid_vivo(None) is False
        assert main._pid_vivo(0) is False


class TestLogAislado:
    """Los tests no deben escribir en el app.log de produccion.

    `main` engancha el RotatingFileHandler a los loggers "src"/"g360" en tiempo
    de IMPORT, y ese handler vive hasta que muere el proceso. Sin aislamiento
    el log real se llenaba de ruido de pytest: +330 bytes por archivo de test y
    3.372 lineas acumuladas, lo que vuelve inutil el diagnostico de la app.
    """

    def test_el_log_de_la_sesion_no_es_el_de_produccion(self):
        import main

        prod = Path(os.environ["APPDATA"]) / "g360-erp-nc-sustentor" / "app.log"
        assert main._handler.baseFilename != str(prod)

    def test_el_log_cae_en_un_directorio_temporal(self):
        import tempfile

        import main

        assert main._handler.baseFilename.startswith(str(Path(tempfile.gettempdir())))

    def test_el_override_respeta_la_variable_de_entorno(self, tmp_path, monkeypatch):
        """El override se puede forzar: con G360_LOG_DIR manda el, no %APPDATA%."""
        import importlib

        destino = tmp_path / "log-forzado"
        monkeypatch.setenv("G360_LOG_DIR", str(destino))
        recargado = importlib.reload(sys.modules["main"])
        try:
            assert recargado._log_dir == destino
            assert recargado._handler.baseFilename == str(destino / "app.log")
        finally:
            monkeypatch.delenv("G360_LOG_DIR", raising=False)
            importlib.reload(sys.modules["main"])

    def test_override_vacio_no_revienta_la_app(self, monkeypatch):
        """G360_LOG_DIR="" cae a %APPDATA%, no a Path(None).

        `Path(os.environ.get(...) or ...)` construye Path(None) ANTES de que
        el `or` pueda decidir, y el import de main revienta: la app no
        arrancaba. Con la variable vacia debe caer al comportamiento normal.
        """
        import importlib

        monkeypatch.setenv("G360_LOG_DIR", "")
        recargado = importlib.reload(sys.modules["main"])
        try:
            assert recargado._log_dir == (Path(os.environ["APPDATA"]) / "g360-erp-nc-sustentor")
        finally:
            monkeypatch.delenv("G360_LOG_DIR", raising=False)
            importlib.reload(sys.modules["main"])


class TestMensajeDeDuplicado:
    def test_nombra_el_pid_a_cerrar(self, lock, monkeypatch):
        """Con el lock tomado, el mensaje dice el PID: se puede actuar."""
        monkeypatch.setattr(main, "_leer_pid_bloqueante", lambda: 3432)
        monkeypatch.setattr(main, "_pid_vivo", lambda pid: True)
        msg = main._mensaje_instancia_duplicada()
        assert "3432" in msg
        assert "Administrador de tareas" in msg

    def test_no_inventa_piloto_si_no_hay_pid(self, lock, monkeypatch):
        monkeypatch.setattr(main, "_leer_pid_bloqueante", lambda: None)
        msg = main._mensaje_instancia_duplicada()
        assert "Administrador de tareas" not in msg
        assert "ya existe una instancia" in msg.lower()

    def test_pid_muerto_no_pide_matar_al_equivocado(self, lock, monkeypatch):
        """Windows suelta el lock al morir: un PID muerto significa otra cosa."""
        monkeypatch.setattr(main, "_leer_pid_bloqueante", lambda: 999999)
        monkeypatch.setattr(main, "_pid_vivo", lambda pid: False)
        msg = main._mensaje_instancia_duplicada()
        assert "999999" in msg
        assert "terminálo" not in msg and "terminalo" not in msg.lower()
