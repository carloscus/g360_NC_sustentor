import pandas as pd
import re
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
import logging
from openpyxl.utils import get_column_letter
from typing import List, Tuple
from datetime import datetime
from src.core.processor import ProcessedItem, NCProcessor

logger = logging.getLogger(__name__)

class G360Styles:
    """
    Centraliza la identidad visual de los reportes G360.
    Define colores, fuentes y bordes compartidos entre plantillas y reportes finales.
    """
    def __init__(self):
        self.side = Side(style='thin', color="000000")
        self.border = Border(left=self.side, right=self.side, top=self.side, bottom=self.side)
        self.header_fill = PatternFill(start_color="0B1220", end_color="0B1220", fill_type="solid")
        self.header_font = Font(color="FFFFFF", bold=True)
        self.critical_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        self.total_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
        self.alert_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
        self.warning_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        self.info_fill = PatternFill(start_color="C9DAF8", end_color="C9DAF8", fill_type="solid")
        self.zebra_fill = PatternFill(start_color="F9F9F9", end_color="F9F9F9", fill_type="solid")
        self.alert_font = Font(color="FFFFFF", bold=True)
        self.warning_font = Font(color="9C5700", bold=True)
        self.info_font = Font(color="003366", bold=True)
        self.center_align = Alignment(horizontal='center', vertical='center')
        self.left_align = Alignment(horizontal='left', vertical='center')
        self.wrap_alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)


class ExcelGenerator:
    """
    Encargado de la transformación de ProcessedItems a archivos Excel (.xlsx).
    Implementa lógica de formato dinámico, fórmulas de Excel y auto-ajuste de columnas.
    """
    def __init__(self):
        self.wb = Workbook()
        self.ws = self.wb.active
        assert self.ws is not None
        self.ws.title = "Sustento NC"
        self.styles = G360Styles()

        self.fmt_num = '#,##0.00'
        self.fmt_num_4 = '#,##0.0000'
        self.fmt_pct = '0.00%'

    def _limpiar(self, texto):
        """Asegura que el texto sea grabable en Excel (elimina caracteres no imprimibles)."""
        if texto is None: return ""
        return "".join(c for c in str(texto) if c.isprintable()).strip()

    def _escribir_encabezado_y_totales(self, cliente: str, motivo: str, fila_fin_datos: int):
        """
        Construye la sección superior del reporte. 
        Utiliza referencias de celdas ($fila_fin_datos) para crear fórmulas de SUMA
        que abarquen exactamente el rango de datos procesados.
        """
        
        # Fila 1: Fecha
        self.ws.cell(row=1, column=1, value="FECHA:").font = Font(bold=True)
        self.ws.cell(row=1, column=2, value=datetime.now().strftime("%d/%m/%Y"))

        # Fila 2: Nombre del Cliente (Grande y Negrita)
        c_cliente = self.ws.cell(row=2, column=1, value=self._limpiar(cliente).upper())
        c_cliente.font = Font(bold=True, size=14)
        self.ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=5)

        # Fila 3: Motivo
        self.ws.cell(row=3, column=1, value="MOTIVO:").font = Font(bold=True)
        self.ws.cell(row=3, column=2, value=self._limpiar(motivo))

        # Nota aclaratoria sobre IGV (Fila 4)
        c_nota = self.ws.cell(row=4, column=1, value="* Los cálculos de descuento y subtotales por ítem no incluyen IGV. El impuesto se calcula al finalizar el reporte.")
        c_nota.font = Font(italic=True, size=9, color="666666")
        self.ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=7)

        # Cuadro de Totales Superiores (Filas 1-3, Columnas H-I)
        # Los datos empiezan en la fila 7, por lo que la suma es de I7 a I...
        f_sub = f"=SUM(I7:I{max(8, fila_fin_datos)})"
        f_igv = f"=ROUND(I1*0.18, 2)"  # I1 es el Subtotal
        f_tot = f"=ROUND(I1+I2, 2)"    # I1 + I2 es el Total con IGV

        labels = [("Subtotal (Sin IGV):", f_sub), ("IGV (18.00%):", f_igv), ("TOTAL NC FINAL:", f_tot)]
        
        for i, (lab, form) in enumerate(labels, 1):
            # Etiqueta (Columna H)
            c_l = self.ws.cell(row=i, column=8, value=lab)
            c_l.font = Font(bold=True)
            c_l.fill = self.styles.total_fill
            c_l.border = self.styles.border
            
            # Valor (Columna I)
            c_v = self.ws.cell(row=i, column=9, value=form)
            c_v.number_format = self.fmt_num
            c_v.border = self.styles.border
            c_v.fill = self.styles.total_fill
            if "TOTAL" in lab:
                c_v.font = Font(bold=True, size=12)

    def _escribir_cabeceras(self, fila: int):
        """Define los nombres de las columnas de la tabla de datos y aplica estilo G360."""
        cols = [
            "ID_ARTICULO", "NOM_ARTICULO", "CANT. SUSTENTAR", "P.U. (SIN IGV)",
            "TOT. FACT. (NETO)", "DESC. (%)", "DESC. UNIT. (NETO)", "PRECIO NETO", 
            "SUBTOTAL (SIN IGV)", "FACTURAS", "ALERTA"
        ]
        for i, texto in enumerate(cols, 1):
            celda = self.ws.cell(row=fila, column=i, value=texto)
            celda.fill = self.styles.header_fill
            celda.font = self.styles.header_font
            celda.alignment = self.styles.center_align
            celda.border = self.styles.border

    def _escribir_fila(self, fila: int, item: ProcessedItem):
        """
        Escribe una fila de datos. Inserta fórmulas vivas (ROUND, SUM) en lugar de valores estáticos
        para permitir que el usuario realice ajustes manuales en el Excel si es necesario.
        Aplica lógica de colores (Semáforo de alertas) según el estado del ítem.
        """
        # Zebra Striping
        bg_fill = self.styles.zebra_fill if fila % 2 == 0 else None

        c_id = self.ws.cell(row=fila, column=1, value=self._limpiar(item.ID_ARTICULO))
        c_id.border = self.styles.border
        c_nom = self.ws.cell(row=fila, column=2, value=self._limpiar(item.NOM_ARTICULO))
        c_nom.border = self.styles.border
        
        c_cant = self.ws.cell(row=fila, column=3, value=item.CANTIDAD_SOLICITADA)
        c_cant.border = self.styles.border
        c_cant.alignment = self.styles.center_align

        c_pu = self.ws.cell(row=fila, column=4, value=float(item.PRECIO_UNITARIO))
        c_pu.border = self.styles.border
        c_pu.number_format = self.fmt_num

        c_tf = self.ws.cell(row=fila, column=5, value=f"=ROUND(C{fila}*D{fila}, 2)")
        c_tf.border = self.styles.border
        c_tf.number_format = self.fmt_num

        c_perc = self.ws.cell(row=fila, column=6, value=float(item.PORCENTAJE_APLICADO))
        c_perc.border = self.styles.border
        c_perc.number_format = self.fmt_pct
        c_perc.alignment = self.styles.center_align

        # Monto del descuento unitario (P.U * %)
        c_du = self.ws.cell(row=fila, column=7, value=f"=ROUND(D{fila}*F{fila}, 4)")
        c_du.border = self.styles.border
        c_du.fill = self.styles.critical_fill
        c_du.number_format = self.fmt_num_4

        # Precio Neto (P.U - Descuento Unitario)
        c_neto = self.ws.cell(row=fila, column=8, value=f"=D{fila}-G{fila}")
        c_neto.border = self.styles.border
        c_neto.number_format = self.fmt_num_4

        # Subtotal NC (Cantidad * Descuento Unitario)
        c_sub = self.ws.cell(row=fila, column=9, value=f"=ROUND(C{fila}*G{fila}, 2)")
        c_sub.border = self.styles.border
        c_sub.number_format = self.fmt_num

        c_docs = self.ws.cell(row=fila, column=10, value=self._limpiar("; ".join(item.DOCUMENTOS)))
        c_docs.border = self.styles.border
        c_docs.alignment = self.styles.wrap_alignment
        
        status = self._limpiar(item.STATUS)        
        # Aplicar Zebra Striping a toda la fila si corresponde
        if bg_fill:
            for col_idx in range(1, 12): # Columnas A hasta K (incluyendo Alerta)
                if col_idx != 7: # No sobreescribir el color crítico de la columna G
                    self.ws.cell(row=fila, column=col_idx).fill = bg_fill
                if col_idx == 7:
                    self.ws.cell(row=fila, column=col_idx).fill = self.styles.critical_fill

        c_alert = self.ws.cell(row=fila, column=11, value=status)
        c_alert.border = self.styles.border
        c_alert.alignment = self.styles.wrap_alignment
        
        # Lógica de colores por tipo de alerta
        if any(x in status.upper() for x in ["ERROR", "ALERTA", "PENDIENTE", "FALTAN"]):
            c_alert.fill = self.styles.alert_fill
            c_alert.font = self.styles.alert_font
        elif "VARIABLE" in status.upper():
            c_alert.fill = self.styles.warning_fill
            c_alert.font = self.styles.warning_font
        elif "INFO" in status.upper():
            c_alert.fill = self.styles.info_fill
            c_alert.font = self.styles.info_font

    def generar_reporte(self, ruta_salida, cliente, motivo, items_procesados, documentos_unicos, rango_fechas, sheet_name=None):
        """
        Genera un reporte de Notas de Crédito en formato Excel.
        
        Args:
            ruta_salida (str): Ruta completa donde se guardará el archivo Excel.
            cliente (str): Nombre del cliente para el encabezado del reporte.
            motivo (str): Motivo de la Nota de Crédito.
            items_procesados (List[ProcessedItem]): Lista de ítems ya procesados por NCProcessor.
            documentos_unicos (List[str]): Lista de documentos únicos utilizados en el sustento.
            rango_fechas (Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]): Rango de fechas del historial.
            sheet_name (Optional[str]): Nombre opcional para la hoja de Excel.
        """
        # 1. Calcular fila final real
        fila_inicio_datos = 7
        fila_fin_datos = fila_inicio_datos + len(items_procesados) - 1

        # Asignar nombre a la hoja si se proporciona (limpiando caracteres prohibidos en Excel)
        if sheet_name:
            clean_name = re.sub(r'[\\/*?:\[\]]', "", str(sheet_name))[:31]
            if clean_name:
                self.ws.title = clean_name

        # 2. Escribir Encabezado y Totales Superiores
        self._escribir_encabezado_y_totales(cliente, motivo, fila_fin_datos)

        self.ws.freeze_panes = "C7" # Congelar ID y Nombre, y filas de encabezado

        # 3. Cabeceras de Tabla (Fila 6)
        fila_cab = 6
        self._escribir_cabeceras(fila_cab)
        
        # 4. Datos (Fila 7 en adelante)
        f_act = 7
        for it in items_procesados:
            self._escribir_fila(f_act, it)
            f_act += 1
        
        # 5. Footer
        f_foot = f_act + 1
        self.ws.merge_cells(start_row=f_foot, start_column=1, end_row=f_foot, end_column=11)
        txt_docs = f"Documentos únicos procesados: {', '.join([self._limpiar(d) for d in documentos_unicos])}"
        c_f = self.ws.cell(row=f_foot, column=1, value=txt_docs)
        c_f.font = Font(italic=True, color="555555")

        # 6. Auto-ajuste de anchos optimizado (muestreo de las primeras 100 filas)
        for col in self.ws.columns:
            max_length = 0
            column = col[0].column_letter
            # Solo verificamos las cabeceras y las primeras 100 filas para rendimiento
            for i, cell in enumerate(col):
                if i > 100: break 
                try:
                    if cell.value:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                except: pass
            adjusted_width = (max_length + 3)
            self.ws.column_dimensions[column].width = min(adjusted_width, 50) # Cap at 50
            
        self.wb.save(ruta_salida)

    def generar_plantilla_vacia(self, ruta_salida):
        """
        Genera la plantilla oficial de Requerimientos lista para usar,
        con formato, ejemplos, validaciones e instrucciones.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "REQUERIMIENTOS"

        note_fill = PatternFill(start_color="FFFBE6", end_color="FFFBE6", fill_type="solid")

        # Cabeceras oficiales
        columnas = [
            ("CODIGO", "Código del Artículo / SKU"),
            ("NOM_ARTICULO", "Nombre del Artículo (opcional)"),
            ("CANTIDAD_NC", "Cantidad de unidades a procesar"),
            ("PORCENTAJE_DESC", "Descuento a aplicar (%)")
        ]

        # Escribir cabeceras
        for col, (nombre, descripcion) in enumerate(columnas, 1):
            celda = ws.cell(row=1, column=col, value=nombre)
            celda.fill = self.styles.header_fill
            celda.font = self.styles.header_font
            celda.alignment = self.styles.center_align
            celda.comment = f"\n{descripcion}\n"
            celda.border = self.styles.border

        # Ejemplos de uso
        ejemplos = [
            ["123456", "PRODUCTO EJEMPLO 1", 5, "10%"],
            ["789012", "PRODUCTO EJEMPLO 2", 12, 3.5],
            ["345678", "", 2, 0.05],
        ]

        for fila, datos in enumerate(ejemplos, 2):
            for col, valor in enumerate(datos, 1):
                celda = ws.cell(row=fila, column=col, value=valor)
                celda.fill = self.styles.zebra_fill
                celda.border = self.styles.border

        # Filas de instrucciones
        fila_nota = 6
        ws.merge_cells(start_row=fila_nota, start_column=1, end_row=fila_nota, end_column=4)
        celda_nota = ws.cell(row=fila_nota, column=1, value="📋 INSTRUCCIONES:")
        celda_nota.font = Font(bold=True, size=11)
        celda_nota.fill = note_fill

        instrucciones = [
            "1. Eliminar las filas de ejemplo (2,3,4) antes de cargar tus datos",
            "2. Solo las columnas CODIGO, CANTIDAD_NC y PORCENTAJE_DESC son obligatorias",
            "3. El descuento se acepta en formato: 10%, 10, 0.1, 10.5",
            "4. No dejar filas vacías entre registros",
            "5. No modificar el nombre ni orden de las columnas",
            "6. Guardar el archivo antes de importar al sistema"
        ]

        for i, texto in enumerate(instrucciones, 7):
            ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=4)
            celda = ws.cell(row=i, column=1, value=texto)
            celda.font = Font(size=10, color="444444")

        # Ajustar anchos de columna
        ws.column_dimensions['A'].width = 18
        ws.column_dimensions['B'].width = 50
        ws.column_dimensions['C'].width = 22
        ws.column_dimensions['D'].width = 25

        # Congelar primera fila
        ws.freeze_panes = "A2"

        # Agregar filtros automáticos
        ws.auto_filter.ref = "A1:D1"

        wb.save(ruta_salida)

    def generar_plantilla_historial(self, ruta_salida):
        """
        Genera la plantilla oficial del Historial de Compras / Reporte Base,
        con todas las columnas oficiales que acepta el procesador.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "HISTORIAL_COMPRAS"

        # Todas las columnas oficiales del procesador
        columnas = NCProcessor.COLUMNAS_HISTORIAL

        for col, nombre in enumerate(columnas, 1):
            celda = ws.cell(row=1, column=col, value=nombre)
            celda.fill = self.styles.header_fill
            celda.font = self.styles.header_font
            celda.alignment = self.styles.center_align
            celda.border = self.styles.border

        # Ajustar anchos
        for i in range(1, len(columnas)+1):
            ws.column_dimensions[get_column_letter(i)].width = 22

        # Congelar primera fila
        ws.freeze_panes = "A2"

        # Filtros automáticos
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columnas))}1"

        wb.save(ruta_salida)

    def generar_plantillas_completas(self, directorio_salida):
        """
        Genera AMBAS plantillas oficiales necesarias para el proceso completo.
        """
        ruta_req = Path(directorio_salida) / "Plantilla_Requerimientos_NC.xlsx"
        ruta_hist = Path(directorio_salida) / "Plantilla_Historial_Compras.xlsx"
        
        self.generar_plantilla_vacia(str(ruta_req))
        self.generar_plantilla_historial(str(ruta_hist))
        
        return ruta_req, ruta_hist
