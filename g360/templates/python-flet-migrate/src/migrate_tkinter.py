"""
Migración de tkinter/ctkinter a Flet
Script de ayuda para convertir código existente
"""

MAPPING = {
    'tk.Label': 'ft.Text',
    'tk.Button': 'ft.ElevatedButton',
    'tk.Entry': 'ft.TextField',
    'tk.Checkbutton': 'ft.Checkbox',
    'tk.Radiobutton': 'ft.Radio',
    'tk.Listbox': 'ft.ListView',
    'tk.Frame': 'ft.Container',
    'tk.LabelFrame': 'ft.Container with border',
    'tk.Canvas': 'ft.Canvas',
    'tk.Menu': 'ft.AppBar with actions',
    'ctk.CTkButton': 'ft.ElevatedButton',
    'ctk.CTkLabel': 'ft.Text',
    'ctk.CTkEntry': 'ft.TextField',
    'ctk.CTkFrame': 'ft.Container',
    'ctk.CTkCheckBox': 'ft.Checkbox',
    'ctk.CTkRadioButton': 'ft.Radio',
    'ctk.CTkProgressBar': 'ft.ProgressBar',
    'ctk.CTkSlider': 'ft.Slider',
    'ctk.CTkSwitch': 'ft.Switch',
    'ctk.CTkComboBox': 'ft.Dropdown',
    'ctk.CTkTextbox': 'ft.TextField multiline',
    'ctk.CTkTabview': 'ft.Tabs',
}

LAYOUT_MAPPING = {
    'pack()': 'page.add()',
    'pack(fill=..., expand=...)': 'expand=True, alignment=...',
    'grid(row=..., column=...)': 'use Column with Row for grid-like',
    'place(x=..., y=...)': 'position in Container',
}

THEME_MAPPING = {
    'bg': 'bgcolor',
    'fg': 'color',
    'font': 'size + weight',
    'relief': 'border_style',
    'padx/pady': 'padding',
    'width': 'width',
    'height': 'height',
}

def convert_tkinter_to_flet(code: str) -> str:
    result = code
    for tk_widget, flet_widget in MAPPING.items():
        result = result.replace(tk_widget, flet_widget)
    return result

print("Mapeo de tkinter/ctkinter a Flet cargado")
print("Usa: from migrate_tkinter import convert_tkinter_to_flet")
print("Para convertir código existente a Flet")