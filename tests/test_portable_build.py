"""El paquete portable: minimo, completo y verificable.

`portable/build.py` arma la copia para PC nueva con whitelist (solo entra lo
declarado). Estos tests verifican las tres garantias contra el repo real:

1. Completo: lleva todo lo que la app necesita para arrancar.
2. Minimo: no lleva tests, venv, git, cachés ni datos.
3. Verificable: `verificar` detecta un paquete roto o inflado.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portable"))

from build import ARCHIVOS, DIRS, construir, verificar


@pytest.fixture
def paquete(tmp_path):
    return construir(tmp_path / "paquete")


class TestCompleto:
    def test_lleva_lo_obligatorio(self, paquete):
        for rel in ARCHIVOS + DIRS + ["src/core/cartucho.py", "src/ui/view_panels.py"]:
            assert (paquete / rel).exists(), f"falta: {rel}"

    def test_el_src_esta_completo(self, paquete):
        repo = Path(__file__).resolve().parent.parent
        orig = {p.relative_to(repo / "src") for p in (repo / "src").rglob("*.py")}
        copi = {p.relative_to(paquete / "src") for p in (paquete / "src").rglob("*.py")}
        assert orig == copi, f"difieren: {orig ^ copi}"

    def test_todo_parsea(self, paquete):
        import ast

        for p in (paquete / "src").rglob("*.py"):
            ast.parse(p.read_text(encoding="utf-8"))


class TestMinimo:
    def test_sin_tests_ni_docs(self, paquete):
        assert not (paquete / "tests").exists()
        assert not (paquete / "docs").exists()

    def test_sin_venv_ni_git(self, paquete):
        assert not (paquete / ".venv").exists()
        assert not (paquete / ".git").exists()

    def test_sin_caches_ni_compilados(self, paquete):
        assert not list(paquete.rglob("__pycache__"))
        assert not list(paquete.rglob("*.pyc"))

    def test_sin_db_ni_datos(self, paquete):
        assert not list(paquete.rglob("*.db"))
        assert not list(paquete.rglob("*.db-wal"))

    def test_pesa_poco(self, paquete):
        mb = sum(f.stat().st_size for f in paquete.rglob("*") if f.is_file()) / 1024**2
        assert mb < 50, f"el paquete pesa {mb:.1f} MB: algo se colo"


class TestVerificar:
    def test_paquete_sano_pasa(self, paquete):
        assert verificar(paquete, 50) == []

    def test_detecta_faltante(self, paquete):
        (paquete / "main.py").unlink()
        problemas = verificar(paquete, 50)
        assert any("main.py" in p for p in problemas)

    def test_detecta_basura(self, paquete):
        (paquete / "src" / "__pycache__").mkdir()
        (paquete / "src" / "__pycache__" / "x.pyc").write_bytes(b"0")
        problemas = verificar(paquete, 50)
        assert any("__pycache__" in p for p in problemas)

    def test_detecta_sobrepeso(self, paquete):
        problemas = verificar(paquete, 0.0001)
        assert any("MB" in p for p in problemas)
