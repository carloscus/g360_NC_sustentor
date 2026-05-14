"""
Diccionario centralizado de campos y formatos del HISTORIAL REAL.
Define la estructura estándar de TODOS los campos del archivo fuente.

Estructura del historial:
ANHO, MES, DOC_CLIENTE, ID_CLIENTE, NOM_CLIENTE, ID_LOCALIDAD_UBIGEO,
NOM_DEPARTAMENTO, NOM_PROVINCIA, NOM_DISTRITO, ID_LINEA, NOM_LINEA,
ID_GRUPO, NOM_GRUPO, ID_TIPO, NOM_TIPO, ID_FAMILIA, NOM_FAMILIA,
ESTADO_LINEA, ID_ARTICULO, NOM_ARTICULO, ID_VENDEDOR, NOM_VENDEDOR,
CANAL DE DISTRIBUCION, COD_SUCURSAL, NOM_SUCURSAL, TPO_DOC, SERIE_DOC,
NRO_DOC, ORD_COMPRA, ID_GUIA, FECHA_ORIG, REFERENCIA, FECHA_REF, MONEDA,
CANTIDAD, SOLES, DOLARES, NOM_CONDICION_PAGO, ID_PEDIDO, FECHA_VENC,
DIVISION, FEC_CARGO

⚠️ IMPORTANTE: Este diccionario define el ESTÁNDAR. Existen EXCEPCIONES
documentadas en reportes específicos (Pareto y NC) que son INTENCIONALES
y NO DEBEN CAMBIARSE sin justificación clara.
"""
import pandas as pd
from typing import Dict, Any, Optional


class DataDictionary:
    """
    Diccionario centralizado de campos y formatos del HISTORIAL REAL.
    Define la estructura estándar de todos los campos usados en reportes
    basado en el archivo fuente de historial de facturación.
    """
    
    # ═══════════════════════════════════════════════════════════════
    # CAMPOS DEL HISTORIAL REAL (ordenados según el archivo fuente)
    # ═══════════════════════════════════════════════════════════════
    HISTORIAL_FIELDS = {
        # --- Periodo / Tiempo ---
        'ANHO': {
            'type': 'int',
            'description': 'Año de la transacción',
            'alias': 'AÑO',
            'in_report': True,
        },
        'MES': {
            'type': 'str',
            'description': 'Mes de la transacción (ej: 05-MAYO)',
            'alias': None,
            'in_report': True,
        },
        'FECHA_ORIG': {
            'type': 'datetime',
            'description': 'Fecha original del documento',
            'alias': 'FECHA',
            'in_report': True,
        },
        'FECHA_REF': {
            'type': 'datetime',
            'description': 'Fecha de referencia',
            'alias': None,
            'in_report': False,
        },
        'FECHA_VENC': {
            'type': 'datetime',
            'description': 'Fecha de vencimiento',
            'alias': 'FECHA_VENCIMIENTO',
            'in_report': False,
        },
        'FEC_CARGO': {
            'type': 'datetime',
            'description': 'Fecha de cargo',
            'alias': None,
            'in_report': False,
        },
        
        # --- Cliente ---
        'DOC_CLIENTE': {
            'type': 'str',
            'description': 'Número de documento del cliente (RUC/DNI)',
            'alias': 'RUC_CLIENTE',
            'in_report': True,
        },
        'ID_CLIENTE': {
            'type': 'str',
            'description': 'ID interno del cliente',
            'alias': 'COD_CLIENTE',
            'in_report': True,
        },
        'NOM_CLIENTE': {
            'type': 'str',
            'description': 'Nombre o razón social del cliente',
            'alias': 'CLIENTE',
            'in_report': True,
        },
        
        # --- Ubicación / Geografía ---
        'ID_LOCALIDAD_UBIGEO': {
            'type': 'str',
            'description': 'Código de ubicación geográfica',
            'alias': 'UBIGEO',
            'in_report': False,
        },
        'NOM_DEPARTAMENTO': {
            'type': 'str',
            'description': 'Nombre del departamento',
            'alias': 'DEPARTAMENTO',
            'in_report': True,
        },
        'NOM_PROVINCIA': {
            'type': 'str',
            'description': 'Nombre de la provincia',
            'alias': 'PROVINCIA',
            'in_report': True,
        },
        'NOM_DISTRITO': {
            'type': 'str',
            'description': 'Nombre del distrito',
            'alias': 'DISTRITO',
            'in_report': True,
        },
        
        # --- Línea de Negocio ---
        'ID_LINEA': {
            'type': 'str',
            'description': 'ID de la línea de negocio',
            'alias': 'COD_LINEA',
            'in_report': True,
        },
        'NOM_LINEA': {
            'type': 'str',
            'description': 'Nombre de la línea de negocio',
            'alias': 'LINEA',
            'in_report': True,
        },
        'ESTADO_LINEA': {
            'type': 'str',
            'description': 'Estado de la línea (LINEA NUEVA / LINEA TRADICIONAL)',
            'alias': None,
            'in_report': True,
        },
        
        # --- Jerarquía de Producto ---
        'ID_GRUPO': {
            'type': 'str',
            'description': 'ID del grupo de producto',
            'alias': 'COD_GRUPO',
            'in_report': True,
        },
        'NOM_GRUPO': {
            'type': 'str',
            'description': 'Nombre del grupo de producto',
            'alias': 'GRUPO',
            'in_report': True,
        },
        'ID_TIPO': {
            'type': 'str',
            'description': 'ID del tipo de producto',
            'alias': 'COD_TIPO',
            'in_report': True,
        },
        'NOM_TIPO': {
            'type': 'str',
            'description': 'Nombre del tipo de producto',
            'alias': 'TIPO',
            'in_report': True,
        },
        'ID_FAMILIA': {
            'type': 'str',
            'description': 'ID de la familia de producto',
            'alias': 'COD_FAMILIA',
            'in_report': True,
        },
        'NOM_FAMILIA': {
            'type': 'str',
            'description': 'Nombre de la familia de producto',
            'alias': 'FAMILIA',
            'in_report': True,
        },
        
        # --- Artículo / SKU ---
        'ID_ARTICULO': {
            'type': 'str',
            'description': 'ID del artículo (SKU)',
            'alias': 'SKU, COD_ARTICULO',
            'in_report': True,
        },
        'NOM_ARTICULO': {
            'type': 'str',
            'description': 'Nombre del artículo',
            'alias': 'ARTICULO, DESCRIPCION',
            'in_report': True,
        },
        
        # --- Vendedor ---
        'ID_VENDEDOR': {
            'type': 'str',
            'description': 'ID del vendedor',
            'alias': 'COD_VENDEDOR',
            'in_report': True,
        },
        'NOM_VENDEDOR': {
            'type': 'str',
            'description': 'Nombre del vendedor',
            'alias': 'VENDEDOR',
            'in_report': True,
        },
        
        # --- Canal / Sucursal ---
        'CANAL DE DISTRIBUCION': {
            'type': 'str',
            'description': 'Canal de distribución',
            'alias': 'CANAL, CANAL_DIST',
            'in_report': True,
        },
        'COD_SUCURSAL': {
            'type': 'str',
            'description': 'Código de la sucursal',
            'alias': 'ID_SUCURSAL',
            'in_report': True,
        },
        'NOM_SUCURSAL': {
            'type': 'str',
            'description': 'Nombre de la sucursal',
            'alias': 'SUCURSAL',
            'in_report': True,
        },
        
        # --- Documento ---
        'TPO_DOC': {
            'type': 'str',
            'description': 'Tipo de documento (F=Factura, B=Boleta, NC=Nota Crédito)',
            'alias': 'TIPO_DOC, TIPODOC',
            'in_report': True,
        },
        'SERIE_DOC': {
            'type': 'str',
            'description': 'Serie del documento',
            'alias': 'SERIE',
            'in_report': True,
        },
        'NRO_DOC': {
            'type': 'str',
            'description': 'Número del documento',
            'alias': 'NUM_DOC, NUMERO',
            'in_report': True,
        },
        'REFERENCIA': {
            'type': 'str',
            'description': 'Referencia del documento',
            'alias': None,
            'in_report': False,
        },
        
        # --- Logística ---
        'ORD_COMPRA': {
            'type': 'str',
            'description': 'Orden de compra del cliente',
            'alias': 'OC, ORDEN_COMPRA',
            'in_report': True,
        },
        'ID_GUIA': {
            'type': 'str',
            'description': 'ID de la guía de remisión',
            'alias': 'GUIA, ID_GUIA_REMISION',
            'in_report': True,
        },
        
        # --- Financiero / Moneda ---
        'MONEDA': {
            'type': 'str',
            'description': 'Moneda de la transacción (SOL/DOLAR)',
            'alias': None,
            'in_report': True,
        },
        'CANTIDAD': {
            'type': 'float',
            'description': 'Cantidad de unidades',
            'alias': 'QTY, UNIDADES',
            'in_report': True,
        },
        'SOLES': {
            'type': 'float',
            'description': 'Monto en soles',
            'alias': 'MONTO_SOLES, TOTAL_SOLES',
            'in_report': True,
        },
        'DOLARES': {
            'type': 'float',
            'description': 'Monto en dólares',
            'alias': 'MONTO_DOLARES, TOTAL_DOLARES',
            'in_report': True,
        },
        'NOM_CONDICION_PAGO': {
            'type': 'str',
            'description': 'Condición de pago (ej: FACTURA 60 DIAS)',
            'alias': 'CONDICION_PAGO, PLAZO',
            'in_report': True,
        },
        'ID_PEDIDO': {
            'type': 'str',
            'description': 'ID del pedido asociado',
            'alias': 'PEDIDO, ORDER_ID',
            'in_report': True,
        },
        'DIVISION': {
            'type': 'str',
            'description': 'División comercial',
            'alias': None,
            'in_report': True,
        },
    }
    
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
            'fields': ['TPO_DOC', 'SERIE_DOC', 'NRO_DOC'],
            'example': 'F012-0457996',
            'required': True,
        },
        'PEDIDO': {
            'format': 'ID_PEDIDO',
            'fields': ['ID_PEDIDO'],
            'example': 'KG935',
            'required': False,
        },
        'GUIA': {
            'format': 'ID_GUIA',
            'fields': ['ID_GUIA'],
            'example': '73848',
            'required': False,
        },
        'ORD_COMPRA': {
            'format': 'ORD_COMPRA',
            'fields': ['ORD_COMPRA'],
            'example': '12345',
            'required': False,
        },
    }
    
    # Campos de lista (plural) - mapeo a campos del historial
    LIST_FIELDS = {
        'CLIENTES': {
            'singular': 'CLIENTE',
            'id_field': 'ID_CLIENTE',
            'name_field': 'NOM_CLIENTE',
            'description': 'Lista de clientes',
        },
        'FACTURAS': {
            'singular': 'FACTURA',
            'fields': ['TPO_DOC', 'SERIE_DOC', 'NRO_DOC'],
            'description': 'Lista de facturas',
        },
        'LINEAS': {
            'singular': 'LÍNEA',
            'id_field': 'ID_LINEA',
            'name_field': 'NOM_LINEA',
            'description': 'Lista de líneas de negocio',
        },
        'VENDEDORES': {
            'singular': 'VENDEDOR',
            'id_field': 'ID_VENDEDOR',
            'name_field': 'NOM_VENDEDOR',
            'description': 'Lista de vendedores',
        },
        'ARTICULOS': {
            'singular': 'SKU',
            'id_field': 'ID_ARTICULO',
            'name_field': 'NOM_ARTICULO',
            'description': 'Lista de artículos/SKUs',
        },
    }
    
    # Categorías de campos para agrupación
    FIELD_CATEGORIES = {
        'TIEMPO': ['ANHO', 'MES', 'FECHA_ORIG', 'FECHA_REF', 'FECHA_VENC', 'FEC_CARGO'],
        'CLIENTE': ['DOC_CLIENTE', 'ID_CLIENTE', 'NOM_CLIENTE'],
        'UBICACION': ['ID_LOCALIDAD_UBIGEO', 'NOM_DEPARTAMENTO', 'NOM_PROVINCIA', 'NOM_DISTRITO'],
        'PRODUCTO': ['ID_ARTICULO', 'NOM_ARTICULO', 'ID_LINEA', 'NOM_LINEA', 'ESTADO_LINEA',
                     'ID_GRUPO', 'NOM_GRUPO', 'ID_TIPO', 'NOM_TIPO', 'ID_FAMILIA', 'NOM_FAMILIA'],
        'COMERCIAL': ['ID_VENDEDOR', 'NOM_VENDEDOR', 'CANAL DE DISTRIBUCION',
                      'COD_SUCURSAL', 'NOM_SUCURSAL', 'DIVISION'],
        'DOCUMENTO': ['TPO_DOC', 'SERIE_DOC', 'NRO_DOC', 'REFERENCIA',
                      'ORD_COMPRA', 'ID_GUIA'],
        'FINANCIERO': ['MONEDA', 'CANTIDAD', 'SOLES', 'DOLARES',
                       'NOM_CONDICION_PAGO', 'ID_PEDIDO'],
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
    def tiene_excepcion(field_name: str, contexto: str = "") -> Dict[str, Any]:
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
