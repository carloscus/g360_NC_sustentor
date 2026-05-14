"""
Diccionario centralizado de campos y formatos para evitar inconsistencias.
Define la estructura estándar de todos los campos usados en reportes.

⚠️ IMPORTANTE: Este diccionario define el ESTÁNDAR de formato de campos compuestos.
Sin embargo, existen EXCEPCIONES documentadas en reportes específicos (Pareto y NC).
Estas excepciones son INTENCIONALES y NO DEBEN CAMBIARSE sin justificación clara.

Ver README.md para más detalles sobre estas excepciones.
"""
import pandas as pd
from typing import Dict, Any, Optional


class DataDictionary:
    """
    Diccionario centralizado de campos y formatos para evitar inconsistencias.
    Define la estructura estándar de todos los campos usados en reportes.
    """
    
    # Campos compuestos (formato "ID - NOMBRE")
    COMPOSITE_FIELDS = {
        'SKU': {
            'id_field': 'ID_ARTICULO',
            'name_field': 'NOM_ARTICULO',
            'display_name': 'SKU',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'LÍNEA': {
            'id_field': 'ID_LINEA',
            'name_field': 'NOM_LINEA',
            'display_name': 'Línea',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'CLIENTE': {
            'id_field': 'ID_CLIENTE',
            'name_field': 'NOM_CLIENTE',
            'display_name': 'Cliente',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'VENDEDOR': {
            'id_field': 'ID_VENDEDOR',
            'name_field': 'NOM_VENDEDOR',
            'display_name': 'Vendedor',
            'format': 'ID - NOMBRE',
            'required': False,
        },
        'SUCURSAL': {
            'id_field': 'COD_SUCURSAL',
            'name_field': 'NOM_SUCURSAL',
            'display_name': 'Sucursal',
            'format': 'ID - NOMBRE',
            'required': False,
        },
    }
    
    # Campos de documento
    DOCUMENT_FIELDS = {
        'FACTURA': {
            'format': 'TIPO + SERIE - NUMERO',
            'example': 'F012-0457996',
            'required': True,
        },
        'PEDIDO': {
            'format': 'ID_PEDIDO',
            'example': '12345',
            'required': False,
        },
    }
    
    # Campos de lista (plural)
    LIST_FIELDS = {
        'CLIENTES': {
            'singular': 'CLIENTE',
            'description': 'Lista de clientes',
        },
        'FACTURAS': {
            'singular': 'FACTURA',
            'description': 'Lista de facturas',
        },
    }
    
    # Valores a filtrar
    FILTER_VALUES = {
        'SIN ASIGNAR': True,
        '': True,
        'nan': True,
        'None': True,
    }
    
    # ⚠️ EXCEPCIONES DOCUMENTADAS
    # Estas excepciones son INTENCIONALES y NO DEBEN CAMBIARSE sin justificación clara
    # Ver README.md para más detalles
    EXCEPCIONES = {
        'PARETO_LINEAS_ENCABEZADOS': {
            'descripcion': 'En reporte Pareto, los encabezados de columnas de líneas usan solo el ID (ej: 0101, 0156)',
            'justificacion': 'Ahorro de espacio con muchas líneas. Los nombres de líneas pueden ser muy largos.',
            'ubicacion': 'src/excel/generator.py:683',
            'ejemplo': '0101, 0156, 0201',
            'no_cambiar': True,
        },
        'NC_SKU_ADICIONAL': {
            'descripcion': 'En reporte NC, hay una columna adicional de SKU con solo el ID (ID puro)',
            'justificacion': 'Facilita filtrado manual en Excel. Permite ordenamiento alfabético y validación con sistemas externos.',
            'ubicacion': 'src/excel/generator.py:165-173',
            'ejemplo': 'Columna "SKU (ID Puro)": "12345"',
            'no_cambiar': True,
        },
    }
    
    @staticmethod
    def format_composite_field(field_name: str, id_val: str, name_val: str) -> str:
        """
        Formatea un campo compuesto según el diccionario.
        
        Args:
            field_name: Nombre del campo (ej: 'SKU', 'LÍNEA', 'CLIENTE')
            id_val: Valor del ID
            name_val: Valor del nombre
            
        Returns:
            String formateado o valor disponible
        """
        if field_name not in DataDictionary.COMPOSITE_FIELDS:
            return str(name_val or id_val or '')
        
        field_def = DataDictionary.COMPOSITE_FIELDS[field_name]
        
        if field_def['format'] == 'ID - NOMBRE':
            cid = str(id_val).strip() if id_val else ''
            cnm = str(name_val).strip() if name_val else ''
            
            if cid and cnm:
                return f"{cid} - {cnm}"
            return cnm or cid
        
        return str(name_val or id_val or '')
    
    @staticmethod
    def should_filter_value(value: str) -> bool:
        """
        Determina si un valor debe ser filtrado.
        
        Args:
            value: Valor a evaluar
            
        Returns:
            True si debe ser filtrado, False en caso contrario
        """
        if value is None:
            return True
        
        val_str = str(value).strip().upper()
        return val_str in DataDictionary.FILTER_VALUES
    
    @staticmethod
    def filter_dataframe(df: pd.DataFrame, field_name: str) -> pd.DataFrame:
        """
        Filtra un DataFrame eliminando valores no deseados.
        
        Args:
            df: DataFrame a filtrar
            field_name: Nombre del campo a filtrar
            
        Returns:
            DataFrame filtrado
        """
        if field_name not in df.columns:
            return df
        
        return df[~df[field_name].apply(DataDictionary.should_filter_value)]
    
    @staticmethod
    def get_field_display_name(field_name: str) -> str:
        """
        Obtiene el nombre para mostrar de un campo.
        
        Args:
            field_name: Nombre del campo
            
        Returns:
            Nombre para mostrar
        """
        if field_name in DataDictionary.COMPOSITE_FIELDS:
            return DataDictionary.COMPOSITE_FIELDS[field_name]['display_name']
        
        return field_name
    
    @staticmethod
    def is_field_required(field_name: str) -> bool:
        """
        Determina si un campo es obligatorio.
        
        Args:
            field_name: Nombre del campo
            
        Returns:
            True si es obligatorio, False en caso contrario
        """
        if field_name in DataDictionary.COMPOSITE_FIELDS:
            return DataDictionary.COMPOSITE_FIELDS[field_name]['required']
        
        return False
    
    @staticmethod
    def get_field_id_name(field_name: str) -> tuple:
        """
        Obtiene los nombres de campos ID y NOMBRE para un campo compuesto.
        
        Args:
            field_name: Nombre del campo compuesto
            
        Returns:
            Tupla (id_field, name_field) o (None, None) si no existe
        """
        if field_name in DataDictionary.COMPOSITE_FIELDS:
            field_def = DataDictionary.COMPOSITE_FIELDS[field_name]
            return (field_def['id_field'], field_def['name_field'])
        
        return (None, None)
    
    @staticmethod
    def validate_composite_field(field_name: str, id_val: str, name_val: str) -> Dict[str, Any]:
        """
        Valida un campo compuesto según el diccionario.
        
        Args:
            field_name: Nombre del campo
            id_val: Valor del ID
            name_val: Valor del nombre
            
        Returns:
            Diccionario con resultado de validación
        """
        result = {
            'valid': True,
            'errors': [],
            'warnings': [],
        }
        
        if field_name not in DataDictionary.COMPOSITE_FIELDS:
            result['valid'] = False
            result['errors'].append(f"Campo '{field_name}' no definido en el diccionario")
            return result
        
        field_def = DataDictionary.COMPOSITE_FIELDS[field_name]
        
        # Validar campos obligatorios
        if field_def['required']:
            if not id_val or str(id_val).strip() == '':
                result['valid'] = False
                result['errors'].append(f"ID obligatorio para campo '{field_name}'")
            
            if not name_val or str(name_val).strip() == '':
                result['warnings'].append(f"Nombre vacío para campo '{field_name}'")
        
        return result
    
    @staticmethod
    def tiene_excepcion(field_name: str, contexto: str = None) -> Dict[str, Any]:
        """
        Verifica si un campo tiene una excepción documentada en un contexto específico.
        
        Args:
            field_name: Nombre del campo a verificar
            contexto: Contexto donde se usa el campo (ej: 'encabezados', 'columna_adicional')
            
        Returns:
            Diccionario con información sobre la excepción
        """
        resultado = {
            'tiene_excepcion': False,
            'excepcion': None,
            'justificacion': '',
            'ubicacion': '',
            'ejemplo': '',
            'no_cambiar': True,
        }
        
        # Verificar excepciones por contexto
        if contexto == 'encabezados' and field_name == 'LÍNEA':
            excepcion = DataDictionary.EXCEPCIONES.get('PARETO_LINEAS_ENCABEZADOS')
            if excepcion:
                resultado['tiene_excepcion'] = True
                resultado['excepcion'] = excepcion
                resultado['justificacion'] = excepcion['justificacion']
                resultado['ubicacion'] = excepcion['ubicacion']
                resultado['ejemplo'] = excepcion['ejemplo']
                resultado['no_cambiar'] = excepcion['no_cambiar']
        
        elif contexto == 'columna_adicional' and field_name == 'SKU':
            excepcion = DataDictionary.EXCEPCIONES.get('NC_SKU_ADICIONAL')
            if excepcion:
                resultado['tiene_excepcion'] = True
                resultado['excepcion'] = excepcion
                resultado['justificacion'] = excepcion['justificacion']
                resultado['ubicacion'] = excepcion['ubicacion']
                resultado['ejemplo'] = excepcion['ejemplo']
                resultado['no_cambiar'] = excepcion['no_cambiar']
        
        return resultado
