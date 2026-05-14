"""
Funciones de validación del historial como fuente de verdad.
Valida la calidad y consistencia de los datos del historial de compras.
"""
import pandas as pd
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


def validar_campos_criticos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida que los campos críticos no sean nulos o vacíos.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado de validación
    """
    campos_criticos = [
        'ID_ARTICULO', 'NOM_ARTICULO', 'FECHA_ORIG', 
        'CANTIDAD', 'SOLES', 'TPO_DOC', 'SERIE_DOC', 'NRO_DOC'
    ]
    
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    for campo in campos_criticos:
        if campo not in df.columns:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo crítico faltante: {campo}")
            continue
        
        nulos = df[campo].isna().sum()
        vacios = (df[campo].astype(str).str.strip() == '').sum()
        
        if nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo {campo}: {nulos} valores nulos")
        
        if vacios > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo {campo}: {vacios} valores vacíos")
        
        resultado['estadisticas'][campo] = {
            'total': len(df),
            'nulos': int(nulos),
            'vacios': int(vacios),
            'validos': int(len(df) - nulos - vacios)
        }
    
    return resultado


def validar_campos_compuestos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de campos compuestos.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado de validación
    """
    campos_compuestos = [
        ('ID_ARTICULO', 'NOM_ARTICULO', 'SKU'),
        ('ID_LINEA', 'NOM_LINEA', 'LÍNEA'),
        ('ID_CLIENTE', 'NOM_CLIENTE', 'CLIENTE'),
        ('ID_VENDEDOR', 'NOM_VENDEDOR', 'VENDEDOR'),
    ]
    
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'inconsistencias': {}
    }
    
    for id_campo, nom_campo, nombre_campo in campos_compuestos:
        if id_campo not in df.columns or nom_campo not in df.columns:
            continue
        
        # Crear diccionario de ID -> NOMBRE
        diccionario = df.groupby(id_campo)[nom_campo].apply(lambda x: x.unique().tolist()).to_dict()
        
        # Detectar inconsistencias (mismo ID, diferentes nombres)
        inconsistencias = {
            id_val: nombres 
            for id_val, nombres in diccionario.items() 
            if len(nombres) > 1
        }
        
        if inconsistencias:
            resultado['valid'] = False
            resultado['errores'].append(
                f"Campo {nombre_campo}: {len(inconsistencias)} IDs con múltiples nombres"
            )
            resultado['inconsistencias'][nombre_campo] = inconsistencias
    
    return resultado


def validar_fechas(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de fechas.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    if 'FECHA_ORIG' not in df.columns:
        resultado['valid'] = False
        resultado['errores'].append("Campo FECHA_ORIG faltante")
        return resultado
    
    # Convertir a datetime si no lo es
    df_temp = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df_temp['FECHA_ORIG']):
        df_temp['FECHA_ORIG'] = pd.to_datetime(df_temp['FECHA_ORIG'], dayfirst=True, errors='coerce')
    
    # Detectar fechas nulas
    fechas_nulas = df_temp['FECHA_ORIG'].isna().sum()
    if fechas_nulas > 0:
        resultado['valid'] = False
        resultado['errores'].append(f"FECHA_ORIG: {fechas_nulas} fechas nulas o inválidas")
    
    # Validar consistencia con ANHO y MES
    if 'ANHO' in df_temp.columns and 'MES' in df_temp.columns:
        df_valido = df_temp.dropna(subset=['FECHA_ORIG', 'ANHO', 'MES'])
        
        inconsistencias = 0
        for _, row in df_valido.iterrows():
            anho_esperado = row['FECHA_ORIG'].year
            mes_esperado = row['FECHA_ORIG'].month
            
            if str(row['ANHO']) != str(anho_esperado) or str(row['MES']) != str(mes_esperado):
                inconsistencias += 1
        
        if inconsistencias > 0:
            resultado['advertencias'].append(
                f"ANHO/MES: {inconsistencias} registros inconsistentes con FECHA_ORIG"
            )
    
    # Estadísticas de fechas
    if not df_temp['FECHA_ORIG'].isna().all():
        resultado['estadisticas']['FECHA_ORIG'] = {
            'min': df_temp['FECHA_ORIG'].min().strftime('%Y-%m-%d'),
            'max': df_temp['FECHA_ORIG'].max().strftime('%Y-%m-%d'),
            'rango_dias': (df_temp['FECHA_ORIG'].max() - df_temp['FECHA_ORIG'].min()).days
        }
    
    return resultado


def validar_montos_cantidades(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de montos y cantidades.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    # Validar CANTIDAD
    if 'CANTIDAD' in df.columns:
        cant_nulos = df['CANTIDAD'].isna().sum()
        cant_cero = (df['CANTIDAD'] == 0).sum()
        cant_negativo = (df['CANTIDAD'] < 0).sum()
        
        if cant_nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"CANTIDAD: {cant_nulos} valores nulos")
        
        if cant_cero > 0:
            resultado['advertencias'].append(f"CANTIDAD: {cant_cero} valores cero")
        
        if cant_negativo > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"CANTIDAD: {cant_negativo} valores negativos")
        
        resultado['estadisticas']['CANTIDAD'] = {
            'min': float(df['CANTIDAD'].min()),
            'max': float(df['CANTIDAD'].max()),
            'promedio': float(df['CANTIDAD'].mean())
        }
    
    # Validar SOLES
    if 'SOLES' in df.columns:
        soles_nulos = df['SOLES'].isna().sum()
        soles_negativo = (df['SOLES'] < 0).sum()
        
        if soles_nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"SOLES: {soles_nulos} valores nulos")
        
        if soles_negativo > 0:
            resultado['advertencias'].append(f"SOLES: {soles_negativo} valores negativos")
        
        resultado['estadisticas']['SOLES'] = {
            'min': float(df['SOLES'].min()),
            'max': float(df['SOLES'].max()),
            'total': float(df['SOLES'].sum())
        }
    
    # Validar consistencia PRECIO_UNID
    if 'PRECIO_UNID' in df.columns and 'CANTIDAD' in df.columns and 'SOLES' in df.columns:
        df_valido = df.dropna(subset=['PRECIO_UNID', 'CANTIDAD', 'SOLES'])
        df_valido = df_valido[df_valido['CANTIDAD'] > 0]
        
        # Calcular precio esperado
        precio_esperado = df_valido['SOLES'] / df_valido['CANTIDAD']
        
        # Comparar con PRECIO_UNID
        diferencia = abs(df_valido['PRECIO_UNID'] - precio_esperado)
        inconsistencias = (diferencia > 0.01).sum()
        
        if inconsistencias > 0:
            resultado['advertencias'].append(
                f"PRECIO_UNID: {inconsistencias} registros inconsistentes con SOLES/CANTIDAD"
            )
    
    return resultado


def validar_documentos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de documentos.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    # Validar campos de documento
    campos_doc = ['TPO_DOC', 'SERIE_DOC', 'NRO_DOC']
    for campo in campos_doc:
        if campo not in df.columns:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo faltante: {campo}")
            continue
        
        nulos = df[campo].isna().sum()
        vacios = (df[campo].astype(str).str.strip() == '').sum()
        
        if nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"{campo}: {nulos} valores nulos")
        
        if vacios > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"{campo}: {vacios} valores vacíos")
    
    # Detectar documentos duplicados
    if all(campo in df.columns for campo in campos_doc):
        df_temp = df.copy()
        df_temp['DOC_KEY'] = df_temp['TPO_DOC'].astype(str) + '|' + df_temp['SERIE_DOC'].astype(str) + '|' + df_temp['NRO_DOC'].astype(str)
        duplicados = df_temp['DOC_KEY'].duplicated().sum()
        
        if duplicados > 0:
            resultado['advertencias'].append(f"Documentos: {duplicados} registros duplicados")
        
        resultado['estadisticas']['DOCUMENTOS'] = {
            'total': df_temp['DOC_KEY'].nunique(),
            'duplicados': int(duplicados)
        }
    
    return resultado


def validar_historial_completo(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida completamente el historial como fuente de verdad.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado completo de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {},
        'validaciones': {}
    }
    
    logger.info("Iniciando validación completa del historial...")
    
    # Ejecutar todas las validaciones
    validaciones = [
        ('campos_criticos', validar_campos_criticos(df)),
        ('campos_compuestos', validar_campos_compuestos(df)),
        ('fechas', validar_fechas(df)),
        ('montos_cantidades', validar_montos_cantidades(df)),
        ('documentos', validar_documentos(df)),
    ]
    
    for nombre, validacion in validaciones:
        resultado['validaciones'][nombre] = validacion
        
        if not validacion['valid']:
            resultado['valid'] = False
            resultado['errores'].extend(validacion['errores'])
        
        resultado['advertencias'].extend(validacion['advertencias'])
        resultado['estadisticas'].update(validacion.get('estadisticas', {}))
    
    # Estadísticas generales
    resultado['estadisticas']['GENERAL'] = {
        'total_registros': len(df),
        'total_columnas': len(df.columns),
        'columnas': df.columns.tolist()
    }
    
    # Log de resultados
    if resultado['valid']:
        logger.info(f"Validación exitosa: {len(df)} registros válidos")
    else:
        logger.error(f"Validación falló: {len(resultado['errores'])} errores encontrados")
        for error in resultado['errores'][:5]:
            logger.error(f"  - {error}")
    
    if resultado['advertencias']:
        logger.warning(f"Advertencias: {len(resultado['advertencias'])} encontradas")
        for advertencia in resultado['advertencias'][:5]:
            logger.warning(f"  - {advertencia}")
    
    return resultado


class DiccionarioDatosMaestros:
    """
    Diccionario centralizado de datos maestros para validar consistencia.
    """
    
    def __init__(self):
        self.clientes = {}  # ID_CLIENTE -> NOM_CLIENTE
        self.articulos = {}  # ID_ARTICULO -> NOM_ARTICULO
        self.lineas = {}  # ID_LINEA -> NOM_LINEA
        self.vendedores = {}  # ID_VENDEDOR -> NOM_VENDEDOR
        self.sucursales = {}  # COD_SUCURSAL -> NOM_SUCURSAL
    
    def cargar_desde_historial(self, df: pd.DataFrame):
        """Carga los diccionarios desde el historial."""
        logger.info("Cargando diccionario de datos maestros...")
        
        if 'ID_CLIENTE' in df.columns and 'NOM_CLIENTE' in df.columns:
            self.clientes = df.groupby('ID_CLIENTE')['NOM_CLIENTE'].first().to_dict()
            logger.info(f"  Clientes: {len(self.clientes)} únicos")
        
        if 'ID_ARTICULO' in df.columns and 'NOM_ARTICULO' in df.columns:
            self.articulos = df.groupby('ID_ARTICULO')['NOM_ARTICULO'].first().to_dict()
            logger.info(f"  Artículos: {len(self.articulos)} únicos")
        
        if 'ID_LINEA' in df.columns and 'NOM_LINEA' in df.columns:
            self.lineas = df.groupby('ID_LINEA')['NOM_LINEA'].first().to_dict()
            logger.info(f"  Líneas: {len(self.lineas)} únicas")
        
        if 'ID_VENDEDOR' in df.columns and 'NOM_VENDEDOR' in df.columns:
            self.vendedores = df.groupby('ID_VENDEDOR')['NOM_VENDEDOR'].first().to_dict()
            logger.info(f"  Vendedores: {len(self.vendedores)} únicos")
        
        if 'COD_SUCURSAL' in df.columns and 'NOM_SUCURSAL' in df.columns:
            self.sucursales = df.groupby('COD_SUCURSAL')['NOM_SUCURSAL'].first().to_dict()
            logger.info(f"  Sucursales: {len(self.sucursales)} únicas")
    
    def validar_consistencia(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Valida la consistencia del historial contra los diccionarios."""
        resultado = {
            'valid': True,
            'errores': [],
            'advertencias': [],
            'inconsistencias': {}
        }
        
        logger.info("Validando consistencia de datos maestros...")
        
        # Validar clientes
        if 'ID_CLIENTE' in df.columns and 'NOM_CLIENTE' in df.columns:
            for _, row in df.iterrows():
                id_cliente = row['ID_CLIENTE']
                nom_cliente = row['NOM_CLIENTE']
                
                if id_cliente in self.clientes:
                    if self.clientes[id_cliente] != nom_cliente:
                        if 'CLIENTE' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['CLIENTE'] = []
                        resultado['inconsistencias']['CLIENTE'].append({
                            'id': id_cliente,
                            'nombre_esperado': self.clientes[id_cliente],
                            'nombre_encontrado': nom_cliente
                        })
        
        # Validar artículos
        if 'ID_ARTICULO' in df.columns and 'NOM_ARTICULO' in df.columns:
            for _, row in df.iterrows():
                id_articulo = row['ID_ARTICULO']
                nom_articulo = row['NOM_ARTICULO']
                
                if id_articulo in self.articulos:
                    if self.articulos[id_articulo] != nom_articulo:
                        if 'ARTICULO' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['ARTICULO'] = []
                        resultado['inconsistencias']['ARTICULO'].append({
                            'id': id_articulo,
                            'nombre_esperado': self.articulos[id_articulo],
                            'nombre_encontrado': nom_articulo
                        })
        
        # Validar líneas
        if 'ID_LINEA' in df.columns and 'NOM_LINEA' in df.columns:
            for _, row in df.iterrows():
                id_linea = row['ID_LINEA']
                nom_linea = row['NOM_LINEA']
                
                if id_linea in self.lineas:
                    if self.lineas[id_linea] != nom_linea:
                        if 'LINEA' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['LINEA'] = []
                        resultado['inconsistencias']['LINEA'].append({
                            'id': id_linea,
                            'nombre_esperado': self.lineas[id_linea],
                            'nombre_encontrado': nom_linea
                        })
        
        # Validar vendedores
        if 'ID_VENDEDOR' in df.columns and 'NOM_VENDEDOR' in df.columns:
            for _, row in df.iterrows():
                id_vendedor = row['ID_VENDEDOR']
                nom_vendedor = row['NOM_VENDEDOR']
                
                if id_vendedor in self.vendedores:
                    if self.vendedores[id_vendedor] != nom_vendedor:
                        if 'VENDEDOR' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['VENDEDOR'] = []
                        resultado['inconsistencias']['VENDEDOR'].append({
                            'id': id_vendedor,
                            'nombre_esperado': self.vendedores[id_vendedor],
                            'nombre_encontrado': nom_vendedor
                        })
        
        # Calcular estadísticas
        for campo, inconsistencias in resultado['inconsistencias'].items():
            if len(inconsistencias) > 0:
                resultado['valid'] = False
                resultado['errores'].append(
                    f"{campo}: {len(inconsistencias)} inconsistencias encontradas"
                )
                logger.error(f"  {campo}: {len(inconsistencias)} inconsistencias")
        
        if resultado['valid']:
            logger.info("Validación de consistencia exitosa")
        else:
            logger.warning(f"Validación de consistencia encontró problemas")
        
        return resultado
