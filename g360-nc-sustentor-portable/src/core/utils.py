import pandas as pd
from src.core.data_dictionary import DataDictionary

def _clean_value(val) -> str:
    """Limpieza centralizada para campos de texto, manejando nulos, NaNs y caracteres no imprimibles."""
    if val is None: return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", ""): return ""
    return "".join(c for c in s if c.isprintable())

# ==================== CONSTANTES FISCALES ====================
IGV_PERCENT = 0.18  # 18% IGV Perú

# ==================== CONSTANTES EXCEL ====================
EXCEL_FMT_NUMBER = '#,##0.00'
EXCEL_FMT_NUMBER_INT = '#,##0'
EXCEL_FMT_PCT = '0.00%'

def format_fecha(fecha_val, fmt='%d/%m/%Y') -> str:
    """
    Centraliza el formato de fechas para reportes Excel.
    Por defecto usa dd/mm/yyyy para display humano.
    Args:
        fecha_val: datetime, pd.Timestamp, o string
        fmt: formato de salida (default dd/mm/yyyy)
    Returns:
        String formateado o cadena vacía si no hay fecha
    """
    if fecha_val is None: return ""
    if hasattr(fecha_val, 'strftime'):
        return fecha_val.strftime(fmt)
    try:
        parsed = pd.to_datetime(fecha_val)
        if pd.notna(parsed):
            return parsed.strftime(fmt)
    except:
        pass
    return str(fecha_val)

def calcular_precio_unitario(monto, cantidad, decimales=4) -> float:
    """
    Calcula el precio unitario de forma segura (para valores simples).
    Args:
        monto: Total en soles (SOLES)
        cantidad: Cantidad de unidades
        decimales: Precisión decimal (default 4)
    Returns:
        Precio unitario, o 0 si cantidad es 0
    """
    try:
        m = float(monto)
        c = float(cantidad)
        if c == 0:
            return 0.0
        return round(m / c, decimales)
    except (TypeError, ValueError):
        return 0.0

def calcular_precio_unitario_df(df: pd.DataFrame, col_soles: str = 'SOLES', col_cantidad: str = 'CANTIDAD', 
                          col_resultado: str = 'PRECIO_UNID') -> pd.DataFrame:
    """
    Calcula precio unitario para todo un DataFrame (vectorizado).
    Calcula: PRECIO_UNITARIO = SOLES / CANTIDAD para cada fila.
    Args:
        df: DataFrame con columnas de monto y cantidad
        col_soles: Nombre columna monto (default 'SOLES')
        col_cantidad: Nombre columna cantidad (default 'CANTIDAD')
        col_resultado: Nombre columna resultado (default 'PRECIO_UNID')
    Returns:
        DataFrame con columna PRECIO_UNID calculada
    """
    if col_soles not in df.columns or col_cantidad not in df.columns:
        return df
    
    df = df.copy()
    soles = pd.to_numeric(df[col_soles], errors='coerce').fillna(0)
    cantidad = pd.to_numeric(df[col_cantidad], errors='coerce').fillna(0)
    
    df[col_resultado] = soles / cantidad
    df.loc[cantidad == 0, col_resultado] = 0
    df[col_resultado] = df[col_resultado].replace([float("inf"), -float("inf")], 0).fillna(0).round(4)
    
    return df

def format_id_name(id_val, name_val, field_name: str = None) -> str:
    """
    Centraliza el formato visual 'ID - NOMBRE' utilizando el diccionario de datos.
    Si falta un valor, retorna el disponible. Si ambos faltan, retorna cadena vacía.
    Preserva la longitud original del ID.
    
    Args:
        id_val: Valor del ID
        name_val: Valor del nombre
        field_name: Nombre del campo (opcional, para validación con diccionario)
        
    Returns:
        String formateado o valor disponible
    """
    if field_name:
        return DataDictionary.format_composite_field(field_name, id_val, name_val)
    
    # Comportamiento original para compatibilidad con código existente
    cid = _clean_value(id_val)
    cnm = _clean_value(name_val)
    
    if cid and cnm:
        return f"{cid} - {cnm}"
    return cnm or cid

def format_doc_id(tpo, serie, nro) -> str:
    """
    Centraliza el formato visual de documentos (Facturas/NC).
    Estándar G360 Flexible: Tipo + Serie + '-' + Numero (sin truncar).
    Ejemplo: F, 012, 0457996 -> F012-0457996
    """
    try:
        t = _clean_value(tpo)[:1].upper()
        s = _clean_value(serie).upper()
        n = _clean_value(nro)
        
        if not s and not n: return t
        
        # Limpiar serie: Si ya empieza con el tipo (ej: F012), no lo duplicamos
        serie_clean = s
        if t and s and not s.startswith(t):
            serie_clean = f"{t}{s}"
        elif not s and t:
            serie_clean = t

        if not n: return serie_clean
        
        # El número se mantiene íntegro para evitar pérdida de datos (ej. 0457996)
        return f"{serie_clean}-{n}"
    except Exception:
        return ""