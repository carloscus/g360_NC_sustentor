import sys
import re

with open('src/excel/generator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Eliminar if es_vital: cX.fill = self.styles.total_fill
# Also the `es_vital = 'VITAL' in ...` can stay or go, it doesn't hurt.
content = re.sub(r'[ \t]*if es_vital:.*\.fill = self\.styles\.total_fill\n?', '', content)

# 2. Reemplazar anchos manuales por auto_adjust
old_widths = """        # Ajustar anchos de columnas
        ws.column_dimensions['A'].width = 45
        ws.column_dimensions['B'].width = 45
        ws.column_dimensions['C'].width = 18
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 10
        ws.column_dimensions['F'].width = 18"""

new_widths = """        # Autoajuste de columnas
        self._auto_adjust_columns(ws)"""

content = content.replace(old_widths, new_widths)

# 3. Fix chart size to H1:T30
old_chart = """                chart.shape = 4
                chart.height = 7.5  # Aproximadamente cubre hasta la fila 10
                chart.width = 15    # Ancho a la derecha
                
                # Posicionar desde H1
                ws.add_chart(chart, "H1")"""

new_chart = """                chart.shape = 4
                chart.height = 15.6  # Cubre hasta la fila 30 (aprox 15.6 cm)
                chart.width = 24     # Cubre hasta la columna T (aprox 24 cm)
                
                # Posicionar en H1
                ws.add_chart(chart, "H1")"""

content = content.replace(old_chart, new_chart)

with open('src/excel/generator.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done cleaning styles and sizing chart.")
