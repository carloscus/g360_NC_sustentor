"""Los dialogs se tienen que poder ABRIR, no solo construir el codigo.

`ft.AlertDialog` (Flet 0.28) no acepta `width`/`height`: pasarlos revienta
con TypeError al construir el dialogo. Asi murio el "Modo avanzado (captura
XLS por intranet)": el clic no abria nada y desde afuera parecia que "no
terminaba". El error solo salia en run_log.txt como Future exception, nunca
en la UI.
"""

import ast
from pathlib import Path

import flet as ft

RAIZ = Path(__file__).resolve().parent.parent


def _alertdialogs_con_medidas():
    """Todos los AlertDialog(...) con width/height en src/ y main.py."""
    mal = []
    for p in list((RAIZ / "src").rglob("*.py")) + [RAIZ / "main.py"]:
        try:
            arbol = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(arbol):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nombre = (
                f.attr
                if isinstance(f, ast.Attribute)
                else (f.id if isinstance(f, ast.Name) else "")
            )
            if nombre != "AlertDialog":
                continue
            kws = [k.arg for k in n.keywords]
            if "width" in kws or "height" in kws:
                mal.append(f"{p.relative_to(RAIZ)}:{n.lineno} -> {kws}")
    return mal


class TestDialogsAbren:
    def test_ningun_alertdialog_lleva_width_o_height(self):
        mal = _alertdialogs_con_medidas()
        assert mal == [], (
            "AlertDialog no acepta width/height y revienta al abrir: "
            "el ancho va en el contenido (Container con width). " + "; ".join(mal)
        )

    def test_el_ancho_va_en_el_contenido(self):
        """El patron correcto construye sin error."""
        d = ft.AlertDialog(
            title=ft.Text("t"),
            content=ft.Container(content=ft.Text("c"), width=520),
            actions=[ft.TextButton("Cerrar")],
        )
        assert d.content.width == 520

    def test_pasar_width_revienta(self):
        """Documenta por que existe este test: asi moria el Modo avanzado."""
        import pytest

        with pytest.raises(TypeError):
            ft.AlertDialog(title=ft.Text("t"), width=520)
