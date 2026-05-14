import sys
import re

with open('src/excel/generator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update G360Styles
old_styles = """        self.header_fill = PatternFill(start_color="0B1220", end_color="0B1220", fill_type="solid")
        self.header_font = Font(color="FFFFFF", bold=True)
        self.critical_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        self.total_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
        self.alert_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
        self.warning_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        self.info_fill = PatternFill(start_color="C9DAF8", end_color="C9DAF8", fill_type="solid")
        self.zebra_fill = PatternFill(start_color="F9F9F9", end_color="F9F9F9", fill_type="solid")
        self.alert_font = Font(color="FFFFFF", bold=True)"""

new_styles = """        self.header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid") # Modern Corporate Blue
        self.header_font = Font(color="FFFFFF", bold=True)
        self.critical_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # Light Orange
        self.total_fill = PatternFill(start_color="E9EEF4", end_color="E9EEF4", fill_type="solid") # Light grayish blue
        self.alert_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") # Light Red
        self.warning_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid") # Soft Yellow
        self.info_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid") # Soft blue
        self.zebra_fill = PatternFill(start_color="F9F9F9", end_color="F9F9F9", fill_type="solid")
        self.alert_font = Font(color="9C0006", bold=True) # Dark Red"""

content = content.replace(old_styles, new_styles)

# 2. Update Pareto Sheet name
old_pareto_name = """                sheet_name = f"V_{id_vendedor[:28]}"[:31]
                sheet_name = re.sub(r'[\\\\/*?:\\[\\]]', "", sheet_name)
                if sheet_name in self.wb.sheetnames:
                    sheet_name = f"{sheet_name}_ copy"[:31]"""

new_pareto_name = """                nombre_corto = (vendedores_nombres.get(id_vendedor, '') or id_vendedor).split(' - ')[-1].strip().split(' ')[0]
                sheet_name = f"Ventas_{nombre_corto[:20]}"[:31]
                sheet_name = re.sub(r'[\\\\/*?:\\[\\]]', "", sheet_name)
                if sheet_name in self.wb.sheetnames:
                    sheet_name = f"{sheet_name}_c"[:31]"""

content = content.replace(old_pareto_name, new_pareto_name)


# 3. Update Anexo Evolucion (Facturas y Nombres de hoja)
old_evol = """    def _escribir_anexo_evolucion(self, datos_pareto, historial, rango_fechas):
        \"\"\"Hojas para medir los meses presentes en el historial (Análisis de Evolución por Vendedor).\"\"\"
        periodos = datos_pareto.get('PERIODOS', [])
        
        # Agrupar historial por Cliente y Mes
        if 'PERIODO_TEND' not in historial.columns:
            historial['PERIODO_TEND'] = historial['FECHA_ORIG'].dt.to_period('M').astype(str)
            
        if 'ID_VENDEDOR' in historial.columns and 'NOM_VENDEDOR' in historial.columns:
            df_mensual = historial.groupby(['ID_VENDEDOR', 'NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_TEND'])['SOLES'].sum().unstack(fill_value=0)
            usar_id_vendedor = True
        else:
            df_mensual = historial.groupby(['NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_TEND'])['SOLES'].sum().unstack(fill_value=0)
            usar_id_vendedor = False
            
        # Obtener los vendedores únicos
        vendedores_keys = df_mensual.index.droplevel(['ID_CLIENTE', 'NOM_CLIENTE']).unique()
            
        # Eliminar la hoja "EVOLUCION_MENSUAL" si existiera previamente
        if "EVOLUCION_MENSUAL" in self.wb.sheetnames:
            del self.wb["EVOLUCION_MENSUAL"]
            
        for v_key in vendedores_keys:
            if usar_id_vendedor:
                id_v, nom_v = v_key
                vendedor_display = format_id_name(id_v, nom_v)
                df_vendedor = df_mensual.loc[(id_v, nom_v)]
            else:
                nom_v = v_key
                vendedor_display = str(nom_v)
                df_vendedor = df_mensual.loc[nom_v]
                
            # Crear nombre de hoja seguro
            sheet_name = f"EVOL_{vendedor_display[:26]}"[:31]
            import re
            sheet_name = re.sub(r'[\\\\/*?:\\[\\]]', "", sheet_name)
            
            # Evitar duplicados
            if sheet_name in self.wb.sheetnames:
                sheet_name = f"{sheet_name}_c"[:31]
                
            ws = self.wb.create_sheet(sheet_name)
            ws.cell(row=1, column=1, value=f"EVOLUCIÓN MENSUAL: {vendedor_display}").font = self.styles.title_font
            
            cabeceras = ['CLIENTE'] + [p.upper() for p in periodos] + ['PROM. MENSUAL']
            for i, text in enumerate(cabeceras, 1):
                c = ws.cell(row=4, column=i, value=text)
                c.fill = self.styles.header_fill; c.font = self.styles.header_font; c.border = self.styles.border
                
            fila = 5
            for row_key, row_vals in df_vendedor.iterrows():
                id_c, nom_c = row_key
                ws.cell(row=fila, column=1, value=format_id_name(id_c, nom_c)).border = self.styles.border
                
                c_vals = 2
                sum_soles = 0
                previous_month_value = 0
                
                for p in periodos:
                    val = row_vals.get(p, 0)
                    sum_soles += val
                    cell = ws.cell(row=fila, column=c_vals, value=val)
                    cell.number_format = '#,##0.00'; cell.border = self.styles.border
                    
                    if previous_month_value > 0 and (val - previous_month_value) / previous_month_value < -0.20:
                        cell.fill = self.styles.warning_fill
                        cell.font = self.styles.alert_font
                        
                    previous_month_value = val
                    c_vals += 1
                
                prom = ws.cell(row=fila, column=c_vals, value=sum_soles / len(periodos) if periodos else 0)
                prom.number_format = '#,##0.00'; prom.border = self.styles.border; prom.fill = self.styles.total_fill
                fila += 1
                
            self._auto_adjust_columns(ws)
            ws.freeze_panes = "B5\"\"\"

new_evol = """    def _escribir_anexo_evolucion(self, datos_pareto, historial, rango_fechas):
        \"\"\"Hojas para medir los meses presentes en el historial (Análisis de Evolución por Vendedor).\"\"\"
        periodos = datos_pareto.get('PERIODOS', [])
        
        # Agrupar historial por Cliente y Mes
        if 'PERIODO_TEND' not in historial.columns:
            historial['PERIODO_TEND'] = historial['FECHA_ORIG'].dt.to_period('M').astype(str)
            
        if 'ID_VENDEDOR' in historial.columns and 'NOM_VENDEDOR' in historial.columns:
            df_mensual = historial.groupby(['ID_VENDEDOR', 'NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_TEND'])['SOLES'].sum().unstack(fill_value=0)
            df_facturas = historial.groupby(['ID_VENDEDOR', 'NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE'])['FACTURA'].nunique()
            usar_id_vendedor = True
        else:
            df_mensual = historial.groupby(['NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_TEND'])['SOLES'].sum().unstack(fill_value=0)
            df_facturas = historial.groupby(['NOM_VENDEDOR', 'ID_CLIENTE', 'NOM_CLIENTE'])['FACTURA'].nunique()
            usar_id_vendedor = False
            
        # Obtener los vendedores únicos
        vendedores_keys = df_mensual.index.droplevel(['ID_CLIENTE', 'NOM_CLIENTE']).unique()
            
        # Eliminar la hoja "EVOLUCION_MENSUAL" si existiera previamente
        if "EVOLUCION_MENSUAL" in self.wb.sheetnames:
            del self.wb["EVOLUCION_MENSUAL"]
            
        for v_key in vendedores_keys:
            if usar_id_vendedor:
                id_v, nom_v = v_key
                vendedor_display = format_id_name(id_v, nom_v)
                df_vendedor = df_mensual.loc[(id_v, nom_v)]
            else:
                nom_v = v_key
                vendedor_display = str(nom_v)
                df_vendedor = df_mensual.loc[nom_v]
                
            # Crear nombre de hoja seguro más limpio
            nombre_corto = nom_v.split(' ')[0] if nom_v else str(vendedor_display).split(' ')[0]
            sheet_name = f"Evol_{nombre_corto[:24]}"[:31]
            import re
            sheet_name = re.sub(r'[\\\\/*?:\\[\\]]', "", sheet_name)
            
            # Evitar duplicados
            if sheet_name in self.wb.sheetnames:
                sheet_name = f"{sheet_name}_c"[:31]
                
            ws = self.wb.create_sheet(sheet_name)
            ws.cell(row=1, column=1, value=f"EVOLUCIÓN MENSUAL: {vendedor_display}").font = self.styles.title_font
            
            cabeceras = ['CLIENTE'] + [p.upper() for p in periodos] + ['PROM. MENSUAL', 'N° DOCS']
            for i, text in enumerate(cabeceras, 1):
                c = ws.cell(row=4, column=i, value=text)
                c.fill = self.styles.header_fill; c.font = self.styles.header_font; c.border = self.styles.border
                
            fila = 5
            for row_key, row_vals in df_vendedor.iterrows():
                id_c, nom_c = row_key
                ws.cell(row=fila, column=1, value=format_id_name(id_c, nom_c)).border = self.styles.border
                
                c_vals = 2
                sum_soles = 0
                previous_month_value = 0
                
                for p in periodos:
                    val = row_vals.get(p, 0)
                    sum_soles += val
                    cell = ws.cell(row=fila, column=c_vals, value=val)
                    cell.number_format = '#,##0.00'; cell.border = self.styles.border
                    
                    if previous_month_value > 0 and (val - previous_month_value) / previous_month_value < -0.20:
                        cell.fill = self.styles.alert_fill
                        cell.font = self.styles.alert_font
                        
                    previous_month_value = val
                    c_vals += 1
                
                # Promedio
                prom = ws.cell(row=fila, column=c_vals, value=sum_soles / len(periodos) if periodos else 0)
                prom.number_format = '#,##0.00'; prom.border = self.styles.border; prom.fill = self.styles.total_fill
                c_vals += 1
                
                # N° Facturas (Documentos)
                if usar_id_vendedor:
                    cant_facturas = df_facturas.loc[(id_v, nom_v, id_c, nom_c)] if (id_v, nom_v, id_c, nom_c) in df_facturas.index else 0
                else:
                    cant_facturas = df_facturas.loc[(nom_v, id_c, nom_c)] if (nom_v, id_c, nom_c) in df_facturas.index else 0
                
                cell_facturas = ws.cell(row=fila, column=c_vals, value=cant_facturas)
                cell_facturas.number_format = '#,##0'; cell_facturas.border = self.styles.border
                cell_facturas.alignment = self.styles.center_align
                
                fila += 1
                
            self._auto_adjust_columns(ws)
            ws.freeze_panes = "B5\"\"\"

content = content.replace(old_evol, new_evol)

with open('src/excel/generator.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Refactor complete.")
