"""Contraste fuente/fondo (WCAG) en tema claro y oscuro.

Regla: texto normal ≥4.5:1, texto grande (≥18px o 14px bold) e iconos ≥3:1.
Los valores se leen del tema real (G360Theme) en cada modo, asi el test
falla si alguien baja un tono o hardcodea un color sin variante.
"""

import pytest

from src.core.g360_theme import G360Theme

CARD_DARK = "#1a1f2e"
VARIANT_DARK = "#232838"
CARD_LIGHT = "#ffffff"  # ft.Colors.SURFACE claro (~#FEF7FF, dif. despreciable)
VARIANT_LIGHT = "#e4ebf3"
GREEN_800 = "#2e7d32"  # Material Green 800 (ft.Colors.GREEN_800)


def _hx(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _blend(fg_hex, opacity, bg_hex):
    f, b = _hx(fg_hex), _hx(bg_hex)
    return tuple(round(f[i] * opacity + b[i] * (1 - opacity)) for i in range(3))


def _lum(rgb):
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (c(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg_hex, bg_hex):
    a, b = _lum(_hx(fg_hex)), _lum(_hx(bg_hex))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@pytest.fixture(params=[True, False], ids=["dark", "light"])
def modo(request):
    G360Theme.set_theme_mode(request.param)
    yield request.param
    G360Theme.set_theme_mode(True)  # default de la app


def _card(modo):
    return CARD_DARK if modo else CARD_LIGHT


def _variant(modo):
    return VARIANT_DARK if modo else VARIANT_LIGHT


class TestTextoNormal:
    """Texto pequeño (labels, botones, chips, badges, leyendas): ≥4.5."""

    def test_boton_primario_blanco(self, modo):
        assert _ratio("ffffff", G360Theme.button_color()) >= 4.5

    def test_boton_exportar_blanco(self, modo):
        assert _ratio("ffffff", GREEN_800) >= 4.5

    def test_texto_acento_en_card(self, modo):
        assert _ratio(G360Theme.accent_text_color(), _card(modo)) >= 4.5

    def test_texto_acento2_en_card(self, modo):
        assert _ratio(G360Theme.accent_2_color(), _card(modo)) >= 4.5

    def test_texto_acento3_en_variante(self, modo):
        assert _ratio(G360Theme.accent_3_color(), _variant(modo)) >= 4.5

    def test_semanticos_en_card(self, modo):
        card = _card(modo)
        assert _ratio(G360Theme.ok_color(), card) >= 4.5
        assert _ratio(G360Theme.warning_color(), card) >= 4.5
        assert _ratio(G360Theme.error_color(), card) >= 4.5

    def test_textos_base(self, modo):
        import flet as ft

        card = _card(modo)
        assert _ratio(G360Theme.text_primary_color(), card) >= 4.5
        # Muted dark = blanco 60% sobre card (resoluble); en light es el
        # token Material ON_SURFACE_VARIANT (par validado por el framework).
        assert _ratio("#%02x%02x%02x" % _blend("ffffff", 0.6, CARD_DARK), CARD_DARK) >= 4.5
        G360Theme.set_theme_mode(False)
        try:
            assert G360Theme.text_muted_color() == ft.Colors.ON_SURFACE_VARIANT
        finally:
            G360Theme.set_theme_mode(modo)

    def test_badge_acento(self, modo):
        assert _ratio("ffffff", G360Theme.primary_color()) >= 4.5

    def test_overlay_loading(self, modo):
        scrim = _blend("000000", 0.55, _card(modo))
        bg = "#%02x%02x%02x" % scrim
        assert _ratio("ffffff", bg) >= 4.5


class TestTextoGrandeIconos:
    """Valores KPI (18px bold), iconos y estados grandes: ≥3.0."""

    def test_acento_base_en_variante(self, modo):
        assert _ratio(G360Theme.accent_color(), _variant(modo)) >= 3.0

    def test_acento2_en_variante(self, modo):
        assert _ratio(G360Theme.accent_2_color(), _variant(modo)) >= 3.0


class TestTokens:
    """Los métodos resuelven la variante correcta por modo."""

    def test_button_color_por_modo(self):
        G360Theme.set_theme_mode(True)
        try:
            assert G360Theme.button_color() == G360Theme.PRIMARY_HOVER
            G360Theme.set_theme_mode(False)
            assert G360Theme.button_color() == G360Theme.PRIMARY
        finally:
            G360Theme.set_theme_mode(True)

    def test_map_sin_duplicados_y_cubre_nuevos(self):
        m = G360Theme._color_map()
        assert m[G360Theme.ACCENT_2] == G360Theme.accent_2_color()
        assert m[G360Theme.ACCENT_3] == G360Theme.accent_3_color()
        assert m[G360Theme.SUCCESS] == G360Theme.ok_color()
