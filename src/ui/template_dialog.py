import os
import threading
import flet as ft
from src.core.g360_theme import G360Theme
from src.render.templates import PlantillaGenerator
from src.core.utils import resolve_output_path


def _etiqueta_casos(casos: list) -> str:
    """'DC · Diferencia de costo' con los nombres canónicos del catálogo.

    Si la plantilla también sirve a otros casos (columnas compatibles) se
    listan aparte: 'FPE · Feria / preventa / evento (compatible: PROM, CMV)'.
    """
    from src.ui.catalog import CATALOGO

    if not casos:
        return ""

    def _texto(codigo: str) -> str:
        caso = CATALOGO.get(codigo)
        return f"{codigo} · {caso.label}" if caso else codigo

    principal = _texto(casos[0])
    otros = [c for c in casos[1:]]
    if not otros:
        return principal
    return f"{principal} (compatible: {', '.join(otros)})"


def mostrar_dialogo_plantillas(page, app):
    """Muestra el diálogo de selección y descarga de plantillas.

    Cada opción dice el nombre del archivo (que es la convención), qué CASO lo
    usa y qué INSUMO llena, con el mismo vocabulario del selector de casos
    (DC, DO, VRS, FPE...) y no con los tipos legacy del proceso.
    """
    claves = list(PlantillaGenerator.TEMPLATES)
    checkboxes = []
    filas = []
    for clave in claves:
        tpl = PlantillaGenerator.TEMPLATES[clave]
        cb = ft.Checkbox(
            label=f"{tpl['nombre']}.xlsx",
            value=False,
            label_style=ft.TextStyle(size=13, weight=ft.FontWeight.W_600),
        )
        detalle = ft.Text(
            f"{_etiqueta_casos(tpl.get('casos', []))} — {tpl.get('descripcion', '')}",
            size=10,
            color=G360Theme.text_muted_color(),
        )
        checkboxes.append(cb)
        filas.append(ft.Column([cb, detalle], spacing=0, tight=True))

    content = ft.Column(filas, spacing=10)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.DOWNLOAD_OUTLINED, size=20, color=app.G360_ACCENT),
                ft.Text("Descargar Plantillas", weight=ft.FontWeight.BOLD),
            ],
            spacing=8,
        ),
        content=ft.Container(content, padding=ft.padding.only(top=10)),
        actions=[
            ft.TextButton("Cancelar", on_click=lambda _: _cerrar_dialogo(page, dialog)),
            ft.ElevatedButton(
                "DESCARGAR SELECCIONADAS",
                on_click=lambda _: _descargar_plantillas(page, app, dialog, checkboxes, claves),
                style=ft.ButtonStyle(bgcolor=G360Theme.button_color(), color="white"),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(dialog)
    dialog.open = True
    page.update()


def _cerrar_dialogo(page, dialog):
    dialog.open = False
    page.update()


def _descargar_plantillas(page, app, dialog, checkboxes, claves):
    seleccionados = [claves[i] for i, cb in enumerate(checkboxes) if cb.value]
    if not seleccionados:
        app.show_snackbar("Selecciona al menos una plantilla", app.G360_WARNING)
        return
    dialog.open = False
    page.update()

    app.show_loading("Generando plantillas...")

    def task():
        try:
            out_dir = app._get_desktop_path()
            out_dir.mkdir(exist_ok=True)
            generador = PlantillaGenerator()
            descargados = []
            for clave in seleccionados:
                # El nombre ya viene completo ("G360_Lista_de_Precios"); anteponer
                # otro "Plantilla_" lo duplicaba.
                nombre = PlantillaGenerator.TEMPLATES[clave]["nombre"]
                out_path = resolve_output_path(out_dir / f"{nombre}.xlsx")
                generador.generar(clave, str(out_path))
                descargados.append(out_path.name)
            app.show_snackbar(
                f"\u2705 {len(descargados)} plantilla(s): {', '.join(descargados)}",
                app.G360_SUCCESS,
            )
            if os.name == "nt":
                os.startfile(str(out_dir))
        except Exception as ex:
            from src.ui.mensajes import mensaje

            app.show_snackbar(mensaje(ex, "usar la plantilla"), app.G360_ERROR)
        finally:
            app.hide_loading()

    threading.Thread(target=task, daemon=True).start()
