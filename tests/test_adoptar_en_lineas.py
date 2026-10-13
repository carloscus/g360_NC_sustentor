"""Adoptar lineas vive SOLO en la pestana Lineas.

Estaba en Gestion, lejos de donde el usuario ve y edita sus lineas. Se
movio a `_panel_lineas` junto al editor, con refresh de los checks al
confirmar. Si alguien lo duplica en otro lado, este test lo dice.
"""

from pathlib import Path


def _donde_esta() -> list[tuple[int, str]]:
    """Lineas con UI de adoptar y en que metodo estan."""
    src = (Path(__file__).resolve().parent.parent / "src" / "ui" / "view_panels.py").read_text(
        encoding="utf-8"
    )
    lineas = src.splitlines()
    # Metodos nivel-4 (def dentro de la clase) y sus rangos.
    metodos: list[tuple[str, int]] = []
    for i, l in enumerate(lineas):
        if l.startswith("    def "):
            metodos.append((l.strip().split("(")[0][4:], i + 1))
    metodos.append(("__fin__", len(lineas) + 1))

    hits = []
    for i, l in enumerate(lineas, start=1):
        t = l.strip()
        if "doptar" in t.lower() and (
            "Button(" in t or 'title=ft.Text("Adoptar' in t or "def adoptar" in t
        ):
            metodo = next(m for m, inicio in reversed(metodos) if inicio <= i)
            hits.append((i, metodo))
    return hits


class TestAdoptarSoloEnLineas:
    def test_un_solo_punto_de_entrada(self):
        hits = _donde_esta()
        botones = [(i, m) for i, m in hits if "def " not in m or True]
        # La funcion + el boton + el titulo del dialogo: todo en _panel_lineas.
        for i, m in botones:
            assert m == "_panel_lineas", f"L{i} esta en {m}, deberia estar en _panel_lineas"

    def test_no_quedo_rastro_en_gestion(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "ui" / "view_panels.py").read_text(
            encoding="utf-8"
        )
        assert "adoptar_lineas_cartucho" not in src, "la funcion vieja de Gestion sigue existiendo"
