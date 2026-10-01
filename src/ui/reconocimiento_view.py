# -*- coding: utf-8 -*-
import flet as ft
import logging
from pathlib import Path
from typing import List

_logger = logging.getLogger("src.ui.reconocimiento_view")
from src.ui.catalog import (
    MODALIDAD_INDIVIDUAL,
    HistorialConfig,
)


from src.ui.view_helpers import _ViewHelpers
from src.ui.view_panels import _ViewPanels
from src.ui.view_handlers import _ViewHandlers


class ReconocimientoView(_ViewHelpers, _ViewPanels, _ViewHandlers):
    """Vista principal del módulo de Reconocimiento.

    Reúne los mixins _ViewHelpers (utilidades), _ViewPanels (constructores
    de UI) y _ViewHandlers (eventos y ejecución) para mantener el archivo
    escalable. __init__ y build se conservan aquí como núcleo.
    """

    def __init__(self, app):
        self.app = app
        self.tipo_actual = "diferencia_precio"  # legacy key; mapped to catalog code on change
        self.modalidad_actual = MODALIDAD_INDIVIDUAL  # "individual" | "consolidado"
        self._historico_config = HistorialConfig()  # current historic config
        self.historial_path = None
        self.lista_path = None
        self.requerimientos_paths: List[Path] = []
        self.container = None
        self.df_historial = None
        self.resultado = None

    def build(self):
        self._init_controls()
        self.container = ft.Container(
            expand=True,
            padding=ft.padding.only(top=22, bottom=28, left=32, right=32),
            content=ft.Column([], scroll=ft.ScrollMode.AUTO, spacing=15),
        )
        self._renderizar_ui()
        self._refrescar_card_db()
        self._iniciar_poll_captura()
        return self.container
