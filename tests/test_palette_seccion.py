"""Paleta propia de la sección de Reporte de Compras.

El reporte es una zona independiente: con el azul global se leía como otra
pieza del flujo principal. Se differentiates con el violeta (ACCENT_2), con un
velo del mismo tono sobre la card y borde de 1px teñido.

Esto fija dos cosas que ya se rompieron una vez:
- que la bajada siga cumpliendo 4.5:1 (a 0.72 de opacidad daba 3.78:1);
- que la sección siga siendo distinguible del acento global.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core.g360_theme import G360Theme as T

RAIZ = Path(__file__).resolve().parents[1]
HEX = re.compile(r"^#([0-9a-fA-F]{6})(?:,([\d.]+))?$")
MAT = re.compile(r"^(white|black)(\d+)$")
# Flet devuelve algunos colores como enum de Material ('surface'). Se resuelve
# con el valor aproximado de Material 3 para poder medir el contraste.
MATERIAL = {
    "surface": (248, 249, 250),
    "surface_variant": (242, 242, 242),
    "surface_container": (240, 241, 244),
    "outline_variant": (202, 202, 202),
    "on_surface": (28, 27, 26),
    "on_surface_variant": (82, 80, 78),
}


def _parse(color):
    if isinstance(color, tuple):
        return color, 1.0
    if color is None:
        return None
    # Enum de Material: usar su .value
    valor = getattr(color, "value", color)
    if not isinstance(valor, str):
        return None
    m = HEX.match(valor.strip())
    if m:
        h = m.group(1)
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)), (
            float(m.group(2)) if m.group(2) else 1.0
        )
    m = MAT.match(valor.strip())
    if m:
        base = (255, 255, 255) if m.group(1) == "white" else (0, 0, 0)
        return base, int(m.group(2)) / 100
    if valor.strip().lower() in MATERIAL:
        return MATERIAL[valor.strip().lower()], 1.0
    return None


def _sobre(color, fondo):
    got = _parse(color)
    if got is None:
        return fondo
    rgb, a = got
    if a >= 1.0:
        return rgb
    return tuple(round(rgb[i] * a + fondo[i] * (1 - a)) for i in range(3))


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture(params=[True, False], ids=["dark", "light"])
def tema(request):
    T.set_theme_mode(request.param)
    yield request.param
    T.set_theme_mode(True)


def test_la_seccion_no_usa_el_acento_global(tema):
    """Si comparte acento con el resto, la zona no se diferencia."""
    assert T.section_accent_color() != T.accent_color()


def test_bajada_cumple_contraste(tema):
    """4.5:1 sobre la card base y sobre la card teñida de la sección."""
    card = _parse(T.surface_color())[0]
    teñida = _sobre(T.section_surface_color(), card)
    bajada = _sobre(T.subtitle_color(), card)
    for nombre, fondo in (("card", card), ("card de sección", teñida)):
        c = _ratio(bajada, fondo)
        assert c >= 4.5, f"bajada sobre {nombre}: {c:.2f}:1 (min 4.5)"


def test_texto_principal_cumple_sobre_la_card_teñida(tema):
    card = _parse(T.surface_color())[0]
    fondo = _sobre(T.section_surface_color(), card)
    assert _ratio(_sobre(T.text_primary_color(), fondo), fondo) >= 4.5


def test_acento_de_seccion_alcanza_para_iconos(tema):
    """El acento se usa en iconos y botones: 3:1 alcanza (no es texto)."""
    card = _parse(T.surface_color())[0]
    fondo = _sobre(T.section_surface_color(), card)
    assert _ratio(_sobre(T.section_accent_color(), fondo), fondo) >= 3.0


def test_reporte_usa_la_paleta_de_seccion():
    """El panel del reporte no puede volver al azul global."""
    texto = (RAIZ / "src" / "ui" / "reporte_panel.py").read_text(encoding="utf-8")
    assert "accent = G360Theme.section_accent_color()" in texto
    assert "accent = self.app.G360_ACCENT" not in texto
    assert "G360Theme.section_surface_color()" in texto
    assert "G360Theme.section_border_color()" in texto


def test_el_velo_es_sutil():
    """Un velo, no un bloque de color: la sección sigue siendo legible."""
    card = _parse(T.surface_color())[0]
    fondo = _sobre(T.section_surface_color(), card)
    card = _parse(T.surface_color())[0]
    delta = max(abs(fondo[i] - card[i]) for i in range(3))
    assert delta <= 40, f"el velo movió el fondo {delta} niveles: ya no es sutil"
