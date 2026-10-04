"""La UI principal no debe romperse al serializar.

En esta build de Flet, `Container.before_update()` serializa el atributo
`alignment` con `EmbedJsonEncoder`, y ese encoder SOLO convierte enums que
estan dentro de dicts: un enum suelto cae en `default()`, que acaba pidiendo
`obj.__dict__` sobre el `mappingproxy` de la clase y revienta con

    AttributeError: 'mappingproxy' object has no attribute '__dict__'

Como `alignment` es el ultimo atributo que se serializa, el fallo se come
`page.add(self.body)` entero y la app arranca en la UI de respaldo. Pasaba
desde las 13:05 con `alignment=ft.MainAxisAlignment.CENTER` en la card DB.

Por eso `alignment` va como string ("center"/"end"), igual que hace el
`_align()` de main.py.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock


RAIZ = Path(__file__).resolve().parents[1]
UI = [RAIZ / "src" / "ui", RAIZ / "src" / "core" / "g360_theme.py", RAIZ / "g360" / "ui"]
ARCHIVOS = sorted({p for d in UI for p in ([d] if d.is_file() else d.rglob("*.py"))})


def _contenedores_con_alignment_invalido():
    """ft.Container(alignment=...) que no sea un ft.Alignment.

    `Container.alignment` es `Optional[Alignment]` (el dataclass x/y), NO un
    MainAxisAlignment. Se usa ast para mirar solo los argumentos del Container
    y no los de un ft.Row anidado (que si acepta el enum, porque Row es
    dataclass y no pasa por EmbedJsonEncoder).
    """
    hallazgos = []
    for path in ARCHIVOS:
        try:
            arbol = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            if not ast.unparse(nodo.func).endswith("Container"):
                continue
            for kw in nodo.keywords:
                if kw.arg != "alignment":
                    continue
                valor = ast.unparse(kw.value)
                if valor in ("None", "NoneType"):
                    continue
                if "alignment.center" in valor or "ft.Alignment(" in valor:
                    continue
                hallazgos.append(f"{path.relative_to(RAIZ)}:{kw.value.lineno} -> {valor}")
    return hallazgos


def test_container_alignment_es_alignment():
    """`alignment` de ft.Container debe ser ft.Alignment, no un enum ni string."""
    hallazgos = _contenedores_con_alignment_invalido()
    assert not hallazgos, (
        "ft.Container(alignment=...) debe ser ft.Alignment (x/y). Con un Enum "
        "explota el encoder ('mappingproxy'); con un string el cliente Dart "
        "rechaza el tipo:\n  " + "\n  ".join(hallazgos)
    )


def _construir_arbol():
    from main import G360App

    return G360App(MagicMock()).body


def test_arbol_ui_se_serializa_sin_errores():
    """Recorre el arbol real y ejecuta before_update() como hace page.add().

    Es el mismo paso que fallaba: si algun control tiene un atributo que el
    encoder no sabe convertir, revienta aqui con el mismo error.
    """
    arbol = _construir_arbol()
    fallos = []

    def walk(c, path="body"):
        try:
            c.before_update()
        except Exception as exc:  # noqa: BLE001 - queremos verlos todos
            fallos.append(f"{path} [{type(c).__name__}]: {type(exc).__name__}: {exc}")
        for i, ch in enumerate(getattr(c, "controls", None) or []):
            walk(ch, f"{path}/ctl[{i}]:{type(ch).__name__}")
        ct = getattr(c, "content", None)
        if ct is not None and not isinstance(getattr(c, "controls", None), list):
            walk(ct, f"{path}/content:{type(ct).__name__}")

    walk(arbol)
    assert not fallos, "controles que no serializan:\n  " + "\n  ".join(fallos[:10])


def test_card_db_alerta_usa_string():
    """Regresion puntual: la card DB tumbo la UI principal por esto."""
    texto = (RAIZ / "src" / "ui" / "view_panels.py").read_text(encoding="utf-8")
    arbol = ast.parse(texto)
    assert arbol is not None
    assert "content=self.card_db_alerta," in texto
    contexto = texto.split("content=self.card_db_alerta,", 1)[1][:400]
    assert "alignment=ft.alignment.center" in contexto, (
        "card_db_alerta debe usar ft.alignment.center; con MainAxisAlignment "
        "reventaba la UI principal"
    )
