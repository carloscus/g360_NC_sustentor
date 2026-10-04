"""Tests de la Card DB tras la depuración UI (bug autodestructor + KPIs)."""

from __future__ import annotations

from types import SimpleNamespace


def _construye(con_db):
    import flet as ft

    from src.ui import view_panels
    from src.ui.view_panels import _ViewPanels

    v = _ViewPanels.__new__(_ViewPanels)
    v.app = SimpleNamespace()

    state = {"exists": con_db}
    mock_info = {
        "exists": con_db,
        "filas": 2814851 if con_db else 0,
        "fecha_min": "2010-01-04",
        "fecha_max": "2026-10-02",
        "meses": 202,
        "clientes": 25900,
        "facturas": 496000,
        "skus": 22300,
        "lineas_prod": 53,
        "nc_asociadas": 71000,
        "dias_desde_ultimo": 0,
        "incompletos": [],
        "huecos": [],
    }
    original = view_panels.ventas_db.db_card_info
    view_panels.ventas_db.db_card_info = lambda: mock_info
    try:
        v._construir_card_db()
    finally:
        view_panels.ventas_db.db_card_info = original
    return v, ft


class TestCardDepurada:
    def test_sin_db_no_tumba_el_refresco(self):
        """Bug histórico: sin DB el método se rebindaba a noop y mataba a la card.

        El rendereo sin DB no debe desactivar _refrescar_card_db: tras cargar la
        DB, la card tiene que pintar KPIs sin reiniciar la app.
        """
        v, ft = _construye(con_db=False)
        assert callable(v._refrescar_card_db)
        # Crucial: el método sigue siendo el del objeto, no un lambda tombado.
        assert getattr(v._refrescar_card_db, "__self__", None) is v
        # Repetir con DB y confirmar que sí pinta KPIs.
        v2, _ = _construye(con_db=True)
        assert getattr(v2._refrescar_card_db, "__self__", None) is v2

    def test_con_db_chips_minimos_y_snapshot(self):
        """Con DB la card muestra solo lo accionable, no los 7 chips viejos."""
        v, ft = _construye(con_db=True)
        filas = v.card_db_kpis.controls
        assert len(filas) == 1, "una sola fila de chips"
        chips_containers = filas[0].controls
        textos = []
        for c in chips_containers:
            col = c.content
            textos += [t.value for t in col.controls if hasattr(t, "value")]
        # Cobertura + Filas + NC asoc. + Snapshot (y no Facturas/Meses/Clientes/...)
        assert "Cobertura" in textos, textos
        assert "Filas" in textos, textos
        assert "NC asoc." in textos, textos
        assert "Snapshot" in textos, textos
        assert "Facturas" not in textos, textos
        assert "Clientes" not in textos, textos

    def test_sin_db_sin_nc_chip(self):
        """Sin DB no hay chips; la card pide config, no datos fantasmas."""
        v, ft = _construye(con_db=False)
        filas = v.card_db_kpis.controls
        assert len(filas) == 1
        # El único control es el mensaje "Sin base de datos local".
        primer = filas[0]
        textos = [t.value for t in primer.controls if hasattr(t, "value")]
        assert any("Sin base de datos local" in str(t) for t in textos), textos

    def test_nc_chip_solo_si_hay_nc(self):
        """El chip NC solo aparece si hay NC asociadas."""
        import flet as ft

        from src.ui import view_panels
        from src.ui.view_panels import _ViewPanels

        v = _ViewPanels.__new__(_ViewPanels)
        v.app = SimpleNamespace()
        mock_info = {
            "exists": True,
            "filas": 1000,
            "fecha_min": "2026-01-01",
            "fecha_max": "2026-10-02",
            "meses": 10,
            "clientes": 5,
            "facturas": 100,
            "skus": 3,
            "lineas_prod": 2,
            "nc_asociadas": 0,  # sin NC
            "dias_desde_ultimo": 0,
            "incompletos": [],
            "huecos": [],
        }
        original = view_panels.ventas_db.db_card_info
        view_panels.ventas_db.db_card_info = lambda: mock_info
        try:
            v._construir_card_db()
        finally:
            view_panels.ventas_db.db_card_info = original
        chips = v.card_db_kpis.controls[0].controls
        textos = []
        for c in chips:
            textos += [t.value for t in c.content.controls if hasattr(t, "value")]
        assert "NC asoc." not in textos
        assert "Snapshot" in textos


class TestChipSnapshot:
    def test_chip_sin_chequeo_muestra_muted(self):
        """Con health-check aún sin correr, el chip es gris y no rompe."""
        from src.core.api_robustness import state
        from src.ui import view_panels
        from src.ui.view_panels import _ViewPanels

        state._ULTIMO = None
        v = _ViewPanels.__new__(_ViewPanels)
        txt, color = v._chip_snapshot_txt()
        assert txt == "—"

    def test_chip_offline_es_rojito(self):
        """API offline → chip rojo 'offline'."""
        from src.core.api_robustness import state
        from src.ui.view_panels import _ViewPanels

        state.publicar_health(
            {
                "api_online": False,
                "desfase_horas": None,
                "url": "x",
                "error": "caido",
                "checked_at": 0.0,
            }
        )
        v = _ViewPanels.__new__(_ViewPanels)
        txt, _ = v._chip_snapshot_txt()
        assert txt == "offline"

    def test_chip_fresco_es_ok(self):
        """Snapshot ≤ 2h → chip OK verdoso."""
        from src.core.api_robustness import state
        from src.ui.view_panels import _ViewPanels

        state.publicar_health(
            {
                "api_online": True,
                "desfase_horas": 0.5,
                "url": "x",
                "error": None,
                "checked_at": 0.0,
            }
        )
        v = _ViewPanels.__new__(_ViewPanels)
        txt, _ = v._chip_snapshot_txt()
        assert txt == "ok"

    def test_chip_viejo_avisa(self):
        """Snapshot > 24h → chip rojo con horas."""
        from src.core.api_robustness import state
        from src.ui.view_panels import _ViewPanels

        state.publicar_health(
            {
                "api_online": True,
                "desfase_horas": 72,
                "url": "x",
                "error": None,
                "checked_at": 0.0,
            }
        )
        v = _ViewPanels.__new__(_ViewPanels)
        txt, _ = v._chip_snapshot_txt()
        assert txt == "viejo 72h"
