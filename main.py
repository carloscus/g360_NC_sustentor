import sys
import flet as ft
import pandas as pd
import os
import re
import shutil
import threading
from collections import Counter
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Default level for console output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
from datetime import datetime
from typing import Optional
from pathlib import Path

from src.core.processor import NCProcessor
from src.excel.generator import ExcelGenerator

# Importaciones Oficiales G360 Ecosystem
if getattr(sys, 'frozen', False):
    # Si es un ejecutable, el directorio base es donde está el EXE
    BASE_DIR = Path(sys.executable).parent
else:
    # MODO DESARROLLO: Directorio base es la carpeta actual del proyecto
    BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# from g360.palettes import G360_PALETTE
try:
    from g360.ui.signature import G360Signature
except ImportError:
    # Fallback si el componente compartido no se encuentra (para desarrollo local sin el ecosistema completo)
    G360Signature = None
    print(f"⚠️ Aviso: No se encontró el componente G360Signature en {BASE_DIR}")

# Paleta de colores para el Pie Chart (G360 Themed)
PIE_CHART_COLORS = [
    ft.colors.CYAN_400,
    ft.colors.PURPLE_400,
    ft.colors.ORANGE_400,
    ft.colors.LIGHT_GREEN_400,
    ft.colors.PINK_400,
    ft.colors.BLUE_400,
    ft.colors.AMBER_400,
    ft.colors.TEAL_400,
    ft.colors.DEEP_PURPLE_400,
    ft.colors.RED_400,
    ft.colors.INDIGO_400,
    ft.colors.LIME_400,
    ft.colors.DEEP_ORANGE_400,
    ft.colors.LIGHT_BLUE_400,
    ft.colors.YELLOW_600,
]


class G360App:
    """
    Controlador principal de la interfaz G360 NC-Sustentor.
    Maneja el estado de la aplicación, eventos de usuario y orquestación de procesos.
    """

    def __init__(self, page: ft.Page):
        self.page = page

        # 1. Configuración de Colores y Estilos (Constantes de la App)
        self.G360_BLUE = "#00d084"
        self.G360_SUCCESS = "#22c55e"
        self.G360_ACCENT = "#00d084"
        self.G360_SURFACE = "#151e2e"
        self.G360_BG_DARK = "#0b1220"

        # Rutas de Plantillas Oficiales
        self.TEMPLATES_DIR = BASE_DIR / "assets" / "templates"
        self.PLANTILLA_REQUERIMIENTOS = self.TEMPLATES_DIR / "G360_Plantilla_REQUERIMIENTOS.xlsx"
        self.PLANTILLA_HISTORIAL = self.TEMPLATES_DIR / "G360_Plantilla_HISTORIAL.xlsx"
        
        # Verificar y generar plantillas si no existen al iniciar
        self._verificar_plantillas()

        # 2. Configuración inicial de la página (ventana)
        # 2. Configuración inicial de la página (ventana)
        self._setup_page()

        # 3. Estado inicial de la aplicación
        self.historial_path = None
        self.requerimientos_paths = []
        self.df_historial_preview = None

        self.dialog_event = threading.Event()
        self.user_choice = None

        # 4. Inicialización de componentes de UI y construcción del layout
        self._init_components()
        self._build_ui()

    def _setup_page(self):
        logger.info("Configurando página Flet.")
        """Configura los parámetros visuales globales de la ventana."""
        self.page.title = "G360 NC Sustentor"
        self.page.window_width = 1100
        self.page.window_height = 900
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = "#0b1220"
        self.page.padding = 0
        self.page.window_resizable = True  # Permite redimensionar la ventana
        self.page.window_icon = "images/favicon.ico"

    def _init_components(self):
        logger.info("Inicializando componentes de UI.")
        self.txt_cliente = ft.TextField(
            label="Nombre del Cliente",
            hint_text="Identificación oficial...",
            prefix_icon=ft.icons.BUSINESS_CENTER_OUTLINED,
            border_radius=15,
            bgcolor=ft.colors.with_opacity(0.05, ft.colors.WHITE),
            expand=True,
            on_change=lambda _: self.verificar_boton_generar(),
        )
        self.txt_motivo = ft.TextField(
            label="Motivo del Sustento",
            hint_text="Razón técnica...",
            prefix_icon=ft.icons.ASSIGNMENT_OUTLINED,
            border_radius=15,
            bgcolor=ft.colors.with_opacity(0.05, ft.colors.WHITE),
            expand=True,
            on_change=lambda _: self.verificar_boton_generar(),
        )
        self.sw_forzar_cant = ft.Switch(
            label="Sustentar cantidad total (aunque falte stock)",
            value=True,
            active_color=self.G360_ACCENT,
            label_style=ft.TextStyle(size=12),
        )
        self.rg_tipo_orden = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(value="fecha", label="Más Recientes"),
                    ft.Radio(value="cantidad", label="Mayor Volumen"),
                ],
                spacing=30,
            ),
            value="fecha",
        )
        self.card_historial = self.file_status_card(
            "1. HISTORIAL (BASE)", ft.icons.HISTORY_EDU
        )
        self.card_requerimientos = self.file_status_card(
            "2. REQUERIMIENTOS", ft.icons.FACT_CHECK_OUTLINED
        )
        self.status = ft.Text("", weight=ft.FontWeight.W_600, size=13)
        self.req_files_container = ft.Row(
            wrap=True, spacing=5
        )  # Nueva lista visual de archivos
        self.progress = ft.ProgressBar(width=450, color=self.G360_ACCENT, visible=False)

        # Dashboard Components - Contenedores específicos para badges
        self.date_badges_container = ft.Row(spacing=8)  # Para las fechas

        # Contenedores para la disposición "mariposa"
        self.left_lines_column = ft.Column(
            spacing=10, expand=True, scroll=ft.ScrollMode.ADAPTIVE
        )
        self.right_lines_column = ft.Column(
            spacing=10, expand=True, scroll=ft.ScrollMode.ADAPTIVE
        )
        self.central_axis = ft.VerticalDivider(width=1, color="white24", thickness=1)

        self.lines_list_container = ft.Row(  # Este Row contendrá las dos columnas
            controls=[
                self.left_lines_column,
                self.central_axis,
                self.right_lines_column,
            ],
            spacing=20,  # Espacio entre la columna izquierda y derecha
            vertical_alignment=ft.CrossAxisAlignment.START,  # Alinea los elementos al inicio (arriba)
            expand=True,
        )

        self.dashboard_card = ft.Container(
            visible=False,
            height=580,
            padding=20,
            bgcolor=self.G360_SURFACE,
            border_radius=18,
             animate_opacity=300,
            animate_scale=ft.Animation(400, ft.AnimationCurve.DECELERATE),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.icons.ANALYTICS_OUTLINED, color=self.G360_BLUE),
                            ft.Text(
                                "RESUMEN DE HISTORIAL POR LÍNEAS",
                                weight=ft.FontWeight.BOLD,
                                color=self.G360_BLUE,
                            ),
                        ]
                    ),
                    self.date_badges_container,  # Badges de fechas aquí
                    self.lines_list_container,  # Ahora es el ft.Row que contiene las dos columnas
                ],
                spacing=15,
                expand=True,
            ),  # El Column principal del dashboard_card también debe expandirse
        )

        self.btn_generar = ft.ElevatedButton(
            "PROCESAR SUSTENTO NC",
            icon=ft.icons.ROCKET_LAUNCH_ROUNDED,
            height=65,
            width=450,
            disabled=True,
            on_click=lambda _: self.generar_flow(),
        )

        # Vista Previa Historial en Layout Principal
        self.preview_data_table = ft.DataTable(
            columns=[],
            rows=[],
            column_spacing=20,
            heading_row_height=35,
            heading_row_color=ft.colors.with_opacity(0.15, self.G360_ACCENT),
            border_radius=10,
            horizontal_lines=ft.border.BorderSide(
                0.5, ft.colors.with_opacity(0.1, ft.colors.WHITE)
            ),
            vertical_lines=ft.border.BorderSide(
                0.3, ft.colors.with_opacity(0.05, ft.colors.WHITE)
            ),
        )
        self.preview_container = ft.Container(
            visible=False,
            animate_opacity=300,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.icons.TABLE_CHART_OUTLINED,
                                color=self.G360_BLUE,
                                size=18,
                            ),
                            ft.Text(
                                "VISTA PREVIA HISTORIAL",
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=self.G360_BLUE,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Container(
                        content=ft.Row(
                            [self.preview_data_table], scroll=ft.ScrollMode.ALWAYS
                        ),
                        bgcolor=self.G360_SURFACE,
                        padding=12,
                        border_radius=12,
                        height=280,
                        shadow=ft.BoxShadow(
                            spread_radius=0,
                            blur_radius=25,
                            color=ft.colors.with_opacity(0.08, self.G360_ACCENT),
                            blur_style=ft.ShadowBlurStyle.OUTER,
                        ),
                    ),
                ],
                spacing=10,
            ),
        )

        # Pickers
        self.fp_h = ft.FilePicker(on_result=self.seleccionar_historial)
        self.fp_r = ft.FilePicker(on_result=self.seleccionar_requerimientos)
        self.page.overlay.extend([self.fp_h, self.fp_r])

    def file_status_card(self, title, icon):
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(icon, size=30, color=ft.colors.GREY_400),
                    ft.Text(title, size=12, weight="bold"),
                    ft.Text(
                        "Pendiente", size=11, italic=True, color=ft.colors.GREY_500
                    ),
                ],
                horizontal_alignment="center",
                spacing=5,
            ),
            padding=15,
            bgcolor=self.G360_SURFACE,
            border_radius=15,
            expand=True,
            border=ft.border.all(1, "#333333"),
        )

    def verificar_boton_generar(self):
        is_ready = all(
            [
                self.historial_path,
                self.requerimientos_paths,
                self.txt_cliente.value,
                self.txt_motivo.value,
            ]
        )
        self.btn_generar.disabled = not is_ready
        self.btn_generar.update()

    def seleccionar_historial(self, e: ft.FilePickerResultEvent):
        if not e.files:
            return

        if self.progress.visible: return # Evitar cargas simultáneas

        # UI Feedback inmediato
        self.progress.visible = True
        self.btn_generar.disabled = True
        self.status.value = "⏳ Analizando base de datos (Historial)..."
        self.status.color = self.G360_BLUE
        self.page.update()

        def load_task():
            try:
                path = e.files[0].path
                df_full = pd.read_excel(path, dtype=str)
                proc = NCProcessor(df_full)

                if proc.historial.empty:
                    self.historial_path = None
                    self._actualizar_card_ui(self.card_historial, False, "Sin datos")
                    self.status.value = "⚠️ No se hallaron datos válidos en el archivo."
                else:
                    self.historial_path = path
                    self.df_historial_preview = proc.historial.head(10)
                    
                    # Auto-llenado de cliente
                    col_nom = next((c for c in proc.historial.columns if "NOM_CLIENTE" in str(c).upper()), None)
                    if col_nom and not self.txt_cliente.value:
                        valid_names = proc.historial[col_nom].dropna()
                        if not valid_names.empty:
                            self.txt_cliente.value = str(valid_names.iloc[0]).strip()

                    self._update_dashboard(proc)
                    self._show_preview()
                    self._actualizar_card_ui(self.card_historial, True, e.files[0].name)
                    self.status.value = f"✅ Historial vinculado: {e.files[0].name}"
                    self.status.color = self.G360_SUCCESS
            except PermissionError:
                self.historial_path = None
                self._actualizar_card_ui(self.card_historial, False, "Acceso Denegado")
                self.status.value = "❌ El historial está abierto o bloqueado por otro programa."
                self.status.color = "red"
            except Exception as ex:
                self.historial_path = None
                self._actualizar_card_ui(self.card_historial, False, "Error")
                self.status.value = f"❌ Error: {str(ex)}"
                self.status.color = "red"
            finally:
                self.progress.visible = False
                self.verificar_boton_generar()
                self.page.update()

        threading.Thread(target=load_task, daemon=True).start()

    def _update_dashboard(self, proc):
        logger.info("Actualizando dashboard con datos del historial.")
        # Badges de fechas
        f_ant, f_rec = proc.obtener_rango_fechas()
        
        date_badges = [
            self._crear_badge(f"📅 {f_ant.strftime('%d/%m/%Y')}" if f_ant and not pd.isna(f_ant) else "---"),
            self._crear_badge(
                f"📅 {f_rec.strftime('%d/%m/%Y')}" if f_rec and not pd.isna(f_rec) else "---", is_new=True
            ),
        ]
        self.dashboard_card.opacity = 0
        self.dashboard_card.scale = 0.95
        self.date_badges_container.controls = date_badges  # Asignar a su contenedor

        # Resumen por líneas (Lista de Progreso Linear)
        lineas_resumen = proc.obtener_resumen_lineas()
        self.left_lines_column.controls.clear()
        self.right_lines_column.controls.clear()

        # Función auxiliar para crear el control de cada línea con soporte para "Espejo"
        def create_line_item_control(line_data, original_index, is_left=False):
            color = PIE_CHART_COLORS[
                original_index % len(PIE_CHART_COLORS)
            ]  # Ciclar colores

            # Configuración de alineación según el lado
            alignment = (
                ft.MainAxisAlignment.END if is_left else ft.MainAxisAlignment.START
            )
            cross_alignment = (
                ft.CrossAxisAlignment.END if is_left else ft.CrossAxisAlignment.START
            )
            # Rotación de 180 grados (pi) para que la barra crezca de derecha a izquierda
            bar_rotation = ft.Rotate(3.14159) if is_left else None

            # Sub-fila de datos numéricos (Monto y %)
            data_row = ft.Row(
                [
                    ft.Text(
                        f"{line_data['PORCENTAJE']:.2%}",
                        size=12,
                        color=color,
                        weight="bold",
                    ),
                    ft.Text(
                        f"S/ {line_data['SOLES']:,.0f}",
                        size=13,
                        font_family="monospace",
                        weight="bold",
                    ),
                ],
                spacing=35,
                rtl=is_left,
            )

            # Construir la fila principal invirtiendo el orden para el efecto espejo real
            name_text = ft.Text(
                line_data["NOM_LINEA"], size=13, weight=ft.FontWeight.W_500
            )
            row_controls = [data_row, name_text] if is_left else [name_text, data_row]

            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(row_controls, alignment=alignment, spacing=15),
            ft.ProgressBar(
                value=line_data["ESCALA_VISUAL"],
                color=ft.colors.RED_400 if line_data["ES_NEGATIVO"] else color,
                bgcolor=ft.colors.with_opacity(0.1, ft.colors.RED_400 if line_data["ES_NEGATIVO"] else color),
                height=12,
                border_radius=6,
                rotate=bar_rotation,
            ),
                    ],
                    spacing=5,
                    horizontal_alignment=cross_alignment,
                ),
                padding=ft.padding.only(bottom=15),  # Más aire entre cada fila
            )

        # Distribuir los elementos en pares para mantener el balance visual (1 y 2, 3 y 4, etc.)
        for i in range(0, len(lineas_resumen), 2):
            # El más alto del par actual va a la izquierda
            self.left_lines_column.controls.append(
                create_line_item_control(lineas_resumen[i], i, is_left=True)
            )

            # El segundo más alto del par actual va a la derecha
            if i + 1 < len(lineas_resumen):
                self.right_lines_column.controls.append(
                    create_line_item_control(
                        lineas_resumen[i + 1], i + 1, is_left=False
                    )
                )

        self.dashboard_card.visible = not proc.historial.empty
        self.dashboard_card.opacity = 1
        self.dashboard_card.scale = 1
        self.page.update()

    def _crear_badge(self, text, is_new=False):
        bg_color = self.G360_ACCENT if is_new else "#2A3547"
        return ft.Container(
            content=ft.Text(text, size=10, weight="bold", color="white"),
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            bgcolor=bg_color,
            border_radius=8,
        )

    def seleccionar_requerimientos(self, e: ft.FilePickerResultEvent):
        logger.info("Evento de selección de requerimientos disparado.")
        if e.files:
            nuevos_archivos = 0
            for f in e.files:
                path = f.path
                if path is None:
                    logger.warning(f"El archivo '{f.name}' no tiene ruta local. Omitiendo (Modo Web no soportado para rutas directas).")
                    continue
                
                if path not in self.requerimientos_paths:
                    self.requerimientos_paths.append(path)
                    nuevos_archivos += 1
                    logger.info(f"Requerimiento añadido: {f.name}")
                    # Añadir indicador visual del archivo cargado
                    self.req_files_container.controls.append(
                        ft.Container(
                            content=ft.Text(f.name, size=10, color="white"),
                            bgcolor=ft.colors.with_opacity(0.1, self.G360_ACCENT),
                            padding=ft.padding.symmetric(horizontal=10, vertical=5),
                            border_radius=5,
                            border=ft.border.all(1, self.G360_ACCENT),
                        )
                    )
            
            if nuevos_archivos > 0:
                logger.info(f"Se vincularon {nuevos_archivos} nuevos archivos de requerimientos.")
            if self.requerimientos_paths:
                self._actualizar_card_ui(
                    self.card_requerimientos,
                    True,
                    f"{len(self.requerimientos_paths)} archivos",
                )
                self.status.value = "✅ Requerimientos vinculados correctamente."
                self.status.color = self.G360_SUCCESS
                
            self.verificar_boton_generar()
            self.page.update()

    def _actualizar_card_ui(self, card, success, text=""):
        if success:
            card.border = ft.border.all(1, self.G360_ACCENT)
            card.content.controls[2].value = text
            card.content.controls[2].color = self.G360_ACCENT
        else:
            card.border = ft.border.all(1, "#333333")
            logger.debug(f"Actualizando tarjeta '{card.content.controls[1].value}' a estado: {text if text else 'Pendiente'}")
            card.content.controls[2].value = text if text else "Pendiente"
            card.content.controls[2].color = ft.colors.GREY_500

    def _show_preview(self):
        self.preview_data_table.columns.clear()
        self.preview_data_table.rows.clear()

        if (
            self.df_historial_preview is not None
            and not self.df_historial_preview.empty
        ):
            # Seleccionar columnas clave para no saturar la vista
            cols = self.df_historial_preview.columns.tolist()[:6]
            for col in cols:
                self.preview_data_table.columns.append(
                    ft.DataColumn(ft.Text(col.upper(), size=10, weight="bold"))
                )
            for _, fila in self.df_historial_preview.head(7).iterrows():
                celdas = [
                    ft.DataCell(
                        ft.Text(str(fila[c])[:22], size=9, color=ft.colors.GREY_300)
                    )
                    for c in cols
                ]
                self.preview_data_table.rows.append(ft.DataRow(cells=celdas))
            self.preview_container.visible = True
            self.preview_container.opacity = 1
        else:
            self.preview_container.visible = False

        self.page.update()

    def reset_app(self, e):
        logger.info("Reseteando la aplicación a su estado inicial.")
        # Limpiar estado interno y memoria
        self.historial_path = None
        self.requerimientos_paths = []
        self.date_badges_container.controls.clear()
        self.left_lines_column.controls.clear()  # Limpiar también las columnas individuales
        self.right_lines_column.controls.clear()
        self.req_files_container.controls.clear()
        self.df_historial_preview = None
        self.status.value = "✨ Sistema reseteado."
        self.status.color = self.G360_BLUE

        # Limpiar campos de texto
        self.txt_cliente.value = ""
        self.txt_motivo.value = ""

        # Resetear interfaz
        self._actualizar_card_ui(self.card_historial, False)
        self._actualizar_card_ui(self.card_requerimientos, False)
        self.dashboard_card.visible = False
        self.preview_container.visible = False
        self.verificar_boton_generar()
        self.page.update()
        logger.info("Estado de la aplicación y UI reseteados completamente.")

    def _verificar_plantillas(self):
        """Verifica que las plantillas oficiales existan en assets/templates"""
        try:
            self.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            if self.PLANTILLA_REQUERIMIENTOS.exists() and self.PLANTILLA_HISTORIAL.exists():
                logger.info("✅ Plantillas disponibles en assets/templates")
            else:
                logger.warning(f"⚠️ Plantillas no encontradas en {self.TEMPLATES_DIR}")
        except Exception as ex:
            logger.warning(f"No se pudieron verificar las plantillas: {str(ex)}")

    def crear_plantillas(self, e):
        def task():
            logger.info("Copiando plantillas oficiales desde assets/templates al Escritorio...")
            try:
                copied_count = 0
                
                for template_path in [self.PLANTILLA_REQUERIMIENTOS, self.PLANTILLA_HISTORIAL]:
                    if template_path.exists():
                        # Verificación de duplicados con opción de "Guardar como" (Crear Copia)
                        dest_path = self._get_unique_out_path(template_path.name)
                        if dest_path:
                            shutil.copy2(template_path, dest_path)
                            copied_count += 1
                
                if copied_count > 0:
                    desktop = self._get_desktop_path()
                    if os.name == 'nt' and not self.page.web:
                        os.startfile(str(desktop))
                    
                    self.page.snack_bar = ft.SnackBar(ft.Text(f"✅ Se exportaron {copied_count} plantillas al Escritorio"))
                    self.page.snack_bar.open = True
                    self.status.value = f"✅ {copied_count} plantillas exportadas"
                    self.status.color = self.G360_SUCCESS
                else:
                    self.status.value = "❌ Plantillas no encontradas en assets/templates"
                    self.status.color = "red"
                
                self.page.update()
            except Exception as ex:
                self.status.value = f"❌ Error plantillas: {str(ex)}"
                self.status.color = "red"
                self.page.update()
        
        threading.Thread(target=task, daemon=True).start()

    def preguntar_sobrescribir(self, filename):
        self.dialog_event.clear()

        logger.info(f"Preguntando al usuario sobre archivo duplicado: '{filename}'")
        def handle_click(choice):
            self.user_choice = choice
            self.page.dialog.open = False
            self.page.update()
            self.dialog_event.set()

        self.page.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Archivo duplicado"),
            content=ft.Text(f"El archivo '{filename}' ya existe en el escritorio. ¿Qué desea hacer?"),
            actions=[
                ft.TextButton("Sobrescribir", on_click=lambda _: handle_click("overwrite")),
                ft.TextButton("Crear Copia", on_click=lambda _: handle_click("copy")),
                ft.TextButton("Saltar", on_click=lambda _: handle_click("skip")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog.open = True
        self.page.update()
        self.dialog_event.wait()
        logger.info(f"Usuario eligió: '{self.user_choice}' para '{filename}'")
        return self.user_choice

    def _sort_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aplica el ordenamiento seleccionado por el usuario al historial."""
        if self.rg_tipo_orden.value == "fecha" and "FECHA_ORIG" in df.columns:
            df["FECHA_DT"] = pd.to_datetime(df["FECHA_ORIG"], dayfirst=True, errors="coerce", format="mixed")
            logger.debug("Historial ordenado por fecha (más recientes primero).")
            return df.sort_values(by="FECHA_DT", ascending=False).drop(columns=["FECHA_DT"])
        elif self.rg_tipo_orden.value == "cantidad" and "CANTIDAD" in df.columns:
            df["CANT_NUM"] = pd.to_numeric(df["CANTIDAD"], errors="coerce").fillna(0)
            logger.debug("Historial ordenado por cantidad (mayor volumen primero).")
            return df.sort_values(by="CANT_NUM", ascending=False).drop(columns=["CANT_NUM"])
        return df

    def _get_desktop_path(self) -> Path:
        """Obtiene la ruta real del Escritorio, comprobando si usa OneDrive u otras rutas."""
        onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
        if onedrive_desktop.exists():
            return onedrive_desktop
        return Path.home() / "Desktop"

    def _get_unique_out_path(self, base_name: str) -> Optional[Path]:
        """Gestiona la existencia del archivo y retorna la ruta final o None si se salta."""
        out_path = self._get_desktop_path() / base_name
        if out_path.exists():
            choice = self.preguntar_sobrescribir(out_path.name)
            if choice == "skip":
                logger.info(f"Generación de '{out_path.name}' omitida por elección del usuario.")
                return None
            if choice == "copy":
                base_out_path = out_path
                counter = 1
                while out_path.exists():
                    out_path = base_out_path.parent / f"{base_out_path.stem} ({counter}){base_out_path.suffix}"
                    counter += 1
                logger.info(f"Se generará una copia de '{base_name}' como '{out_path.name}'.")
        return out_path

    def _update_inventory_balances(self, df_h: pd.DataFrame, items: list) -> pd.DataFrame:
        """Descuenta las cantidades procesadas del historial actual por cada documento utilizado."""
        for item in items:
            # Si el item tiene el mapeo de cantidad por documento, usarlo
            if item.DOCUMENTOS_CANTIDAD:
                for doc_str, cant_tomada in item.DOCUMENTOS_CANTIDAD.items():
                    # Parsear el documento para obtener serie y nro
                    # Formato: F204-51999 => tipo=F, serie=204, nro=51999
                    if '-' in doc_str:
                        tipo_serie, nro_target = doc_str.rsplit('-', 1)
                        tipo_target = tipo_serie[0] if tipo_serie else 'F'
                        serie_target = tipo_serie[1:] if len(tipo_serie) > 1 else ''
                    else:
                        continue
                    
                    # Normalizar y buscar el documento en el historial
                    mask_match = False
                    indices_art = df_h.index[df_h["ID_ARTICULO"] == item.ID_ARTICULO]
                    for idx in indices_art:
                        row = df_h.loc[idx]
                        # Normalizar SERIE_DOC
                        serie_h = str(row['SERIE_DOC']).strip()
                        while serie_h.upper().startswith(tipo_target.upper()):
                            serie_h = serie_h[1:]
                        serie_h = serie_h.lstrip('-').strip()
                        
                        # Normalizar NRO_DOC
                        nro_h = str(row['NRO_DOC']).strip()
                        if '-' in nro_h:
                            nro_h = nro_h.split('-', 1)[1]
                        while nro_h.upper().startswith(tipo_target.upper()):
                            nro_h = nro_h[1:]
                        nro_h = nro_h.lstrip('-').strip()
                        
                        # Comparar
                        if serie_h == serie_target and nro_h == nro_target:
                            precio_u = float(df_h.at[idx, "PRECIO_UNID"])
                            nuevo_v = max(0.0, float(df_h.at[idx, "CANTIDAD"]) - float(cant_tomada or 0))
                            logger.debug(f"Descontando {cant_tomada} de '{item.ID_ARTICULO}' Doc({doc_str}). Nuevo saldo: {nuevo_v}")
                            df_h.at[idx, "CANTIDAD"] = nuevo_v
                            # Sincronización de SOLES para evitar saltos de precio en Reporte 2
                            df_h.at[idx, "SOLES"] = round(nuevo_v * precio_u, 4)
                            mask_match = True
                            break
                    
                    if not mask_match:
                        logger.warning(f"No se encontró documento {doc_str} para {item.ID_ARTICULO} en el historial.")
            else:
                # Fallback: usar el documento más reciente si no existe mapeo
                mask = (
                    (df_h["ID_ARTICULO"] == item.ID_ARTICULO)
                )
                # Filtrar por NRO y SERIE normalizados
                nro_item = str(item.NRO_DOC).strip()
                serie_item = str(item.SERIE_DOC).strip()
                for idx in df_h[mask].index:
                    if df_h.at[idx, "ID_ARTICULO"] == item.ID_ARTICULO:
                        match_found = False
                        try:
                            if nro_item in str(df_h.at[idx, "NRO_DOC"]) and serie_item in str(df_h.at[idx, "SERIE_DOC"]):
                                match_found = True
                        except:
                            pass
                        
                        if match_found:
                            precio_u = float(df_h.at[idx, "PRECIO_UNID"])
                            nuevo_v = max(0.0, float(df_h.at[idx, "CANTIDAD"]) - float(item.CANTIDAD_REAL_ENCONTRADA or 0))
                            logger.debug(f"Descontando {item.CANTIDAD_REAL_ENCONTRADA} de '{item.ID_ARTICULO}' (Doc: {item.NRO_DOC}). Nuevo saldo: {nuevo_v}")
                            df_h.at[idx, "CANTIDAD"] = nuevo_v
                            # Sincronizar SOLES para que el P.U. no salte en el Reporte 2
                            df_h.at[idx, "SOLES"] = round(nuevo_v * precio_u, 4)
                            break
        
        return df_h[pd.to_numeric(df_h["CANTIDAD"], errors="coerce").fillna(0) > 0].reset_index(drop=True)

    def generar_flow(self):
        logger.info("Iniciando flujo de generación de reportes.")
        self.progress.visible = True
        self.btn_generar.disabled = True
        self.page.update()
        try:
            cli_clean = re.sub(r'[\\/*?:"<>|]', "", self.txt_cliente.value).strip().replace(" ", "_")
            base_fname = f"NC_{cli_clean}_{datetime.now().strftime('%d%m%Y')}.xlsx"
            logger.debug(f"Nombre base del archivo de salida: {base_fname}")
            
            # Carga y ordenamiento inicial
            df_h_original = pd.read_excel(self.historial_path, dtype=str)
            logger.info(f"Historial base cargado: {len(df_h_original)} filas.")
            proc_hist_initial = NCProcessor(df_h_original) # Para limpiar el historial una vez
            df_h = self._sort_historial(proc_hist_initial.historial.copy()) # Usar el historial limpio y luego ordenar
            logger.info(f"Historial normalizado y ordenado por {self.rg_tipo_orden.value}: {len(df_h)} registros válidos.")

            for idx, req_path in enumerate(self.requerimientos_paths):
                logger.info(f"Procesando archivo de requerimientos {idx + 1}/{len(self.requerimientos_paths)}: {Path(req_path).name}")
                self.status.value = f"⏳ Procesando {idx + 1}/{len(self.requerimientos_paths)}..."
                try:
                    self.page.update()
                    df_r = pd.read_excel(req_path, dtype=str)
                    logger.debug(f"Requerimiento leído: {len(df_r)} filas.")
                    proc = NCProcessor(df_h)
                    # df_h ya viene limpio del paso inicial, proc lo reutiliza de forma eficiente.
                    
                    items, docs = proc.procesar_lote(df_r, forzar_cantidad_solicitada=self.sw_forzar_cant.value)

                    logger.debug(f"Lote procesado. {len(items)} ítems y {len(docs)} documentos únicos.")
                    # LÓGICA INTELIGENTE DE SELECCIÓN DE FACTURA DE REFERENCIA
                    doc_stats = {} # {doc: {'frecuencia': 0, 'monto_nc': 0.0}}
                    for it in items:
                        # Estimamos cuánto del subtotal de la NC pertenece a cada factura basándonos en la proporción de cantidad
                        for doc, cant_en_doc in it.DOCUMENTOS_CANTIDAD.items():
                            if doc not in doc_stats: doc_stats[doc] = {'frecuencia': 0, 'monto_nc': 0.0}
                            doc_stats[doc]['frecuencia'] += 1
                            # Proporción: (Cant tomada del doc / Cant total encontrada) * Subtotal NC
                            prop = cant_en_doc / it.CANTIDAD_REAL_ENCONTRADA if it.CANTIDAD_REAL_ENCONTRADA > 0 else 0
                            doc_stats[doc]['monto_nc'] += it.SUBTOTAL_DESCUENTO * prop

                    # Ordenar facturas por frecuencia (desc) y luego por monto (desc)
                    ranking_docs = sorted(
                        doc_stats.keys(), 
                        key=lambda d: (doc_stats[d]['frecuencia'], doc_stats[d]['monto_nc']), 
                        reverse=True
                    )
                    
                    # Si la más frecuente tiene un monto muy bajo (ej. < 5% del total NC), 
                    # evaluamos si la segunda tiene mucha más relevancia económica.
                    final_ref_doc = ranking_docs[0] if ranking_docs else "Sustento"
                    if len(ranking_docs) > 1:
                        monto_total_nc = sum(d['monto_nc'] for d in doc_stats.values())
                        if doc_stats[ranking_docs[0]]['monto_nc'] < (monto_total_nc * 0.05) and doc_stats[ranking_docs[1]]['monto_nc'] > doc_stats[ranking_docs[0]]['monto_nc']:
                            final_ref_doc = ranking_docs[1]
                    
                    # Verificación de duplicados con opción de "Guardar como"
                    out_path = self._get_unique_out_path(f"PARTE_{idx + 1}_{base_fname}")
                    logger.debug(f"Ruta de salida para el reporte: {out_path}")
                    if not out_path: continue

                    logger.info(f"Generando reporte Excel para Lote {idx + 1}")
                    ExcelGenerator().generar_reporte(
                        str(out_path), self.txt_cliente.value, f"{self.txt_motivo.value} (P{idx + 1})",
                        items, docs, proc.obtener_rango_fechas(),
                        sheet_name=final_ref_doc,
                        factura_referencia=final_ref_doc
                    )
                    df_h = self._update_inventory_balances(df_h, items)
                    
                    if not self.page.web and os.name == 'nt':
                        os.startfile(str(out_path))
                except PermissionError:
                    logger.error(f"Acceso denegado a archivo en Lote {idx + 1}.")
                    self.status.value = f"❌ Error: Archivo abierto o bloqueado en Lote {idx + 1}."
                    self.status.color = "red"
                    self.page.update()
                    continue
                except Exception as ex:
                    logger.error(f"No se pudo completar la escritura del reporte: {ex}")
                    self.status.value = f"❌ Error: ¿Archivo abierto en Excel? ({ex})"
                    self.status.color = "red"
                    self.page.update()
                    continue

            self.status.value = "🎯 Proceso Completado"
            self.status.color = self.G360_ACCENT
            logger.info("Flujo de generación de reportes completado exitosamente.")
        except Exception as ex:
            logger.exception("Error crítico durante el flujo de generación de reportes:")
            self.status.value = f"❌ Error: {str(ex)}"
            self.status.color = "red"
        finally:
            self.progress.visible = False
            self.btn_generar.disabled = False
            self.page.update()

    def _build_ui(self):
        sidebar = ft.Container(
            width=160,
            padding=ft.padding.only(top=40, bottom=40, left=10, right=10),
            bgcolor=self.G360_SURFACE,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Image(src="/images/logo-g360.svg", width=50, height=50),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=30, color="transparent"),
                    ft.Text(
                        "ACCIONES",
                        size=12,
                        weight=ft.FontWeight.BOLD,
                        color=ft.colors.GREY_500,
                    ),
                    ft.Divider(height=10, color="transparent"),
                    ft.Container(
                        content=ft.TextButton(
                            "Plantillas",
                            icon=ft.icons.DOWNLOAD_FOR_OFFLINE,
                            on_click=self.crear_plantillas,
                        ),
                        padding=ft.padding.symmetric(vertical=10),
                    ),
                    ft.Container(
                        content=ft.TextButton(
                            "Limpiar", icon=ft.icons.REFRESH, on_click=self.reset_app
                        ),
                        padding=ft.padding.symmetric(vertical=10),
                    ),
                    ft.Container(expand=True),
                    G360Signature(mode="powered", version="2.0")
                    if G360Signature
                    else ft.Text("powered G360", color=self.G360_BLUE, size=10),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        body = ft.Container(
            expand=True,
            padding=ft.padding.only(top=18, bottom=80, left=40, right=40),
            content=ft.Column(
                [
                    ft.Text(
                        "XLSX CALCULOS NC", size=30, weight=ft.FontWeight.W_900
                    ),
                    ft.Row([self.txt_cliente, self.txt_motivo], spacing=15),
                    ft.Row([self.sw_forzar_cant], alignment="end"),
                    self.rg_tipo_orden,
                    ft.Row(
                        [
                            ft.GestureDetector(
                                content=self.card_historial,
                                on_tap=lambda _: self.fp_h.pick_files(
                                    allowed_extensions=["xlsx", "xls"]
                                ),
                            ),
                            ft.GestureDetector(
                                content=self.card_requerimientos,
                                on_tap=lambda _: self.fp_r.pick_files(
                                    allowed_extensions=["xlsx", "xls"]
                                ),
                            ),
                        ],
                        spacing=15,
                    ),
                    self.dashboard_card,
                    self.preview_container,
                    ft.Column(
                        [
                            self.status,
                            self.progress,
                            self.btn_generar,
                            ft.Text(
                                "(i) Todos los cálculos de descuento y subtotales se realizan sin IGV.",
                                size=11,
                                italic=True,
                                color="grey",
                            ),
                        ],
                        horizontal_alignment="center",
                        spacing=15,
                    ),
                ],
                scroll="auto",
                spacing=25,
            ),
        )
        self.page.add(ft.Row([sidebar, ft.VerticalDivider(width=1), body], expand=True))


def main(page: ft.Page):
    G360App(page)


if __name__ == "__main__":
    # Configuración de puerto fijo para evitar WinError 10013 (Access Denied)
    # El host 127.0.0.1 asegura que el socket sea puramente local.
    ft.app(
        target=main, 
        view=ft.AppView.FLET_APP, 
        assets_dir="assets", 
        host="127.0.0.1",
        port=8888  # Puerto fuera del rango reservado de Windows
    )
