import pandas as pd
from pathlib import Path
from typing import Optional, List
from src.core.data_dictionary import DataDictionary

__all__ = [
    "clean_id_column",
    "read_erp_file",
    "HEADER_KEYWORDS",
    "HEADER_MAP",
    "calcular_precio_unitario_df",
    "coerce_str",
    "format_id_name",
    "format_doc_id",
    "split_doc_id",
    "build_doc_full",
    "resolve_output_path",
    "cliente_visible",
    "PRECIO_DECIMALES",
    "EXCEL_FMT_UNIT_PRICE",
    "normalizar_porcentaje",
    "normalizar_porcentaje_serie",
]


def cliente_visible(cid) -> str:
    """Forma de presentación de un id de cliente: SIN ceros a la izquierda.

    Convención de la app: INTERNAMENTE el id es canónico de 8 dígitos
    ('00068414', relleno en la captura con normalize_client_id para que
    '68414' y '00068414' no se partan en dos clientes), pero SIEMPRE se
    MUESTRA corto ('68414') — chips, status, nombre de archivo, picker y
    encabezados. Los RUCs de 11 dígitos no cambian (sin ceros iniciales).
    """
    s = str(cid or "").strip()
    return s.lstrip("0") or "0"


def _clean_value(val) -> str:
    """Limpieza centralizada para campos de texto, manejando nulos, NaNs y caracteres no imprimibles."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    return "".join(c for c in s if c.isprintable())


def clean_id_column(s: "pd.Series") -> "pd.Series":
    """Limpia un campo ID preservando ceros a la izquierda y eliminando '.0' final de floats.

    Estrategia:
    1. Elimina BOM UTF-8 y espacios alrededor.
    2. Cast a str (mantiene dtype object -> '011019' y '11019' siguen siendo distintos).
    3. Strip de '.0+' repetido al final (caso '1234.0.0' -> '1234', tipico de exports).
    4. NO aplica lstrip('0') -> mantiene leading zeros como dato semantico.
    5. Convierte 'nan'/'NaN'/'None' a '' (vacio).

    Importante: '0' como string no se trata como vacio - es un ID valido.
    """
    if s is None or len(s) == 0:
        return s
    s = s.astype(str)
    s = s.str.replace("\ufeff", "", regex=False)
    s = s.str.strip()
    s = s.where(~s.str.lower().isin({"nan", "none", "null", "<na>"}), "")
    s = s.str.replace(r"(?:\.0+)+$", "", regex=True)
    return s


# ==================== PORCENTAJES ====================
# Convención canónica de la app: el ARCHIVO guarda la fraccion (0.05 = 5%)
# y la mascara Excel '0.00%' la muestra como porcentaje. Se aceptan las dos
# formas de captura y se devuelve SIEMPRE la fraccion:
#   '5%', '5.00%', '5 %' -> 0.05        (texto con %)
#   0.05                 -> 0.05        (ya es fraccion, se usa tal cual)
#   5.0                  -> 0.05        (puntos de porcentaje, se interpretan;
#                                      sin alerta porque el calculo queda bien)
#   150                  -> 1.5  + AL04 (excede 100% de verdad)
#   -0.2                 -> 0.0  + AL04 (descuento negativo)
#   '0,05' / texto ilegible -> 0.0 + AL10 (no interpretable; no se asume)
#   vacio / NaN / 0      -> 0.0  sin alerta (sin descuento es valido)
# El resultado es IDEMPOTENTE: aplicarlo dos veces sobre una fraccion
# valida no la modifica.


def normalizar_porcentaje(valor, *, sku: str = "") -> tuple[float, Optional[str]]:
    """Convierte un descuento/rebate capturado por el usuario a fraccion.

    Returns:
        (fraccion, codigo_alerta): ``codigo_alerta`` es ``None`` cuando el
        valor ya era una fraccion valida, ``'AL04'`` cuando hubo que
        interpretar/corregir un porcentaje fuera de rango o ``'AL10'``
        cuando el texto no se pudo interpretar.
    """
    if isinstance(valor, str):
        raw = valor.strip()
        if raw.lower() in ("", "nan", "none", "null", "<na>"):
            return 0.0, None
        con_pct = "%" in raw
        try:
            num = float(raw.replace("%", "").strip())
        except ValueError:
            return 0.0, "AL10"
    else:
        try:
            num = float(valor)
        except (TypeError, ValueError):
            return 0.0, None
        if num != num:  # NaN
            return 0.0, None
        con_pct = False
    if con_pct:
        num = num / 100
        return (num, "AL04" if num > 1 else None)
    if num < 0:
        return 0.0, "AL04"
    if num > 1:
        num = num / 100
        # Puntos de porcentaje (5 = 5%) se aplican bien: sin alerta. AL04 solo
        # si tras interpretar sigue excediendo 100% (ej: 150 -> 1.5).
        return (num, "AL04" if num > 1 else None)
    return num, None


def normalizar_porcentaje_serie(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Version vectorizada de :func:`normalizar_porcentaje`.

    Returns:
        (fracciones, codigos) — ``codigos`` es una serie de objetos con
        ``None``, ``'AL04'`` o ``'AL10'`` por fila.
    """
    raw = s.astype(str).str.strip()
    vacio = raw.str.lower().isin(("", "nan", "none", "null", "<na>"))
    con_pct = raw.str.contains("%", na=False) & ~vacio
    num = pd.to_numeric(raw.str.replace("%", "", regex=False).str.strip(), errors="coerce")
    no_parseable = ~vacio & num.isna()
    num = num.fillna(0.0).astype(float)
    num = num.where(~con_pct, num / 100)  # '5%' -> 0.05
    negativo = num < 0
    sin_pct_over1 = (num > 1) & ~con_pct  # 5.0 -> 5% (puntos)
    num = num.where(~sin_pct_over1, num / 100)
    # Los puntos interpretados aplican bien (<= 1 tras /100): sin alerta.
    # AL04 solo para exceso real (num > 1 tras /100) o negativo.
    fuera_de_rango = (num > 1) | negativo
    fracciones = num.clip(lower=0.0)
    codigos = pd.Series(None, index=s.index, dtype=object)
    codigos = codigos.where(~fuera_de_rango, "AL04")
    codigos = codigos.where(~no_parseable, "AL10")
    # .where() convierte los None en NaN (float): devolver None reales.
    return fracciones, codigos.astype(object).where(codigos.notna(), None)


# ==================== CONSTANTES FISCALES ====================
IGV_PERCENT = 0.18  # 18% IGV Perú

# ==================== CONSTANTES EXCEL ====================
EXCEL_FMT_NUMBER = "#,##0.00"
EXCEL_FMT_CURRENCY = '"S/" #,##0.00'

# Precios unitarios: 5 decimales en toda la app (acuerdo comercial).
PRECIO_DECIMALES = 5
EXCEL_FMT_UNIT_PRICE = "#,##0.00000"

# ==================== MAPA DE COLUMNAS ERP ====================
# Variantes de nombres de columna que pueden venir de distintos ERP
# Se normalizan al estándar interno (clave derecha)
HEADER_MAP = {
    "AÑO": "ANHO",
    "PRECIO_UNI": "PRECIO_UNITARIO",
    "PRECIO UNID": "PRECIO_UNITARIO",
    "PRECIO UNIDAD": "PRECIO_UNITARIO",
    "PRECIO UNITARIO": "PRECIO_UNITARIO",
    "PRECIO UNIT": "PRECIO_UNITARIO",
    "PRECIO_UNIDR": "PRECIO_UNITARIO",
    "P. UNIT": "PRECIO_UNITARIO",
    "P.U.": "PRECIO_UNITARIO",
    "PRECIOUNITARIO": "PRECIO_UNITARIO",
    "PRECIOS": "PRECIO_BASE",
    "PRECIO_LISTA": "PRECIO_BASE",
    "CODIGO": "CODIGO",
    "CODIGO_SKU": "CODIGO",
    "CODIGO SKU": "CODIGO",
    "COD_ARTICULO": "CODIGO",
    "CODIGO ARTICULO": "CODIGO",
    "COD ARTICULO": "CODIGO",
    "ID_ARTICULO": "CODIGO",
    "SKU": "CODIGO",
    "ARTICULO": "ARTICULO",
    "DESCRIPCION": "ARTICULO",
    "NOMBRE ARTICULO": "ARTICULO",
    "NOMBRE": "ARTICULO",
    "NOM_ARTICULO": "ARTICULO",
    "ID_VENDEDOR": "COD_VENDEDOR",
    "COD_VENDEDOR": "COD_VENDEDOR",
    "VENDEDOR": "VENDEDOR",
    "NOM_VENDEDOR": "VENDEDOR",
    "NOMBRE VENDEDOR": "VENDEDOR",
    "ID_CLIENTE": "COD_CLIENTE",
    "COD_CLIENTE": "COD_CLIENTE",
    "DOC_CLIENTE": "DOC_CLIENTE",
    "RUC": "DOC_CLIENTE",
    "NOM_CLIENTE": "CLIENTE",
    "CLIENTE": "CLIENTE",
    "RAZON SOCIAL": "CLIENTE",
    "RAZON_SOCIAL": "CLIENTE",
    "TPO_DOC": "TIPO_DOC",
    "TIPO_DOC": "TIPO_DOC",
    "TIPO DOC": "TIPO_DOC",
    "TIP DOC": "TIPO_DOC",
    "TD": "TIPO_DOC",
    "SERIE_DOC": "SERIE",
    "SERIE": "SERIE",
    "NUM_SERIE": "SERIE",
    "NRO_DOC": "NUMERO",
    "NUMERO": "NUMERO",
    "NUM_DOC": "NUMERO",
    "NRO": "NUMERO",
    "FECHA_ORIG": "FECHA",
    "FECHA": "FECHA",
    "FECHA_DOC": "FECHA",
    "FECHA_DOCUMENTO": "FECHA",
    "FECHA FACTURA": "FECHA",
    "FECHA_REF": "FECHA_REF",
    "FECHA VENC": "FECHA_VENC",
    "FECHA_VENC": "FECHA_VENC",
    "VENCIMIENTO": "FECHA_VENC",
    "CANTIDAD": "CANTIDAD",
    "CANT": "CANTIDAD",
    "UNIDADES": "CANTIDAD",
    "QTY": "CANTIDAD",
    "SOLES": "SOLES",
    "MONTO": "SOLES",
    "MONTO TOTAL": "SOLES",
    "TOTAL": "SOLES",
    "IMPORTE": "SOLES",
    "DOLARES": "DOLARES",
    "MONTO_USD": "DOLARES",
    "MONTO DOLARES": "DOLARES",
    "ID_LINEA": "COD_LINEA",
    "COD_LINEA": "COD_LINEA",
    "LINEA": "LINEA",
    "NOM_LINEA": "LINEA",
    "ID_GRUPO": "COD_GRUPO",
    "NOM_GRUPO": "GRUPO",
    "ID_TIPO": "COD_TIPO",
    "NOM_TIPO": "TIPO",
    "ID_FAMILIA": "COD_FAMILIA",
    "NOM_FAMILIA": "FAMILIA",
    "ORD_COMPRA": "ORDEN_COMPRA",
    "OC": "ORDEN_COMPRA",
    "ID_GUIA": "GUIA",
    "GUIA": "GUIA",
    "MONEDA": "MONEDA",
    "NOM_CONDICION_PAGO": "CONDICION_PAGO",
    "COND_PAGO": "CONDICION_PAGO",
    "ID_SUCURSAL": "COD_SUCURSAL",
    "COD_SUCURSAL": "COD_SUCURSAL",
    "SUCURSAL": "SUCURSAL",
    "NOM_SUCURSAL": "SUCURSAL",
    "ESTADO_LINEA": "ESTADO_LINEA",
}

# Palabras clave para detectar la fila de cabecera dinámicamente
HEADER_KEYWORDS = {
    "ANHO",
    "AÑO",
    "DOC_CLIENTE",
    "CODIGO",
    "SKU",
    "NUMERO",
    "FECHA",
    "CANTIDAD",
    "PRECIOS",
}
EXCEL_FMT_PCT = "0.00%"


def read_erp_file(
    path, file_ext: Optional[str] = None, *, nrows: Optional[int] = None
) -> pd.DataFrame:
    """Lectura única de archivos del ERP preservando ceros a la izquierda.

    Centraliza las 6+ llamadas dispersas que hacen ``pd.read_excel/read_csv``
    en main.py / vistas / estrategias. Mantiene dtype=str para IDs y normaliza
    extension/encoding de manera consistente.

    Args:
        path: ruta al archivo (.xls, .xlsx, .xlsm o .csv).
        file_ext: extension explicita (e.g. '.csv'); si es None se deduce de path.suffix.
        nrows: si se pasa, limita numero de filas (util para reconocimiento rapido).

    Returns:
        DataFrame con todas las columnas cargadas como object/str (cero coercion numerica).

    Raises:
        FileNotFoundError: si path no existe.
        ValueError: si la extension no es soportada.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")

    if file_ext is None:
        file_ext = p.suffix.lower()

    dtype_kwargs = {"dtype": str}

    if file_ext == ".csv":
        df = pd.read_csv(
            p,
            encoding="utf-8-sig",
            sep=None,
            engine="python",
            nrows=nrows,
            **dtype_kwargs,
        )
    elif file_ext == ".xls":
        df = pd.read_excel(p, engine="xlrd", nrows=nrows, **dtype_kwargs)
    elif file_ext in (".xlsx", ".xlsm"):
        df = pd.read_excel(p, engine="openpyxl", nrows=nrows, **dtype_kwargs)
    else:
        raise ValueError(f"Extension no soportada: {file_ext}")

    return df


def calcular_precio_unitario_df(
    df: pd.DataFrame,
    col_soles: str = "SOLES",
    col_cantidad: str = "CANTIDAD",
    col_resultado: str = "PRECIO_UNITARIO",
) -> pd.DataFrame:
    """
    Calcula precio unitario para todo un DataFrame (vectorizado).
    Calcula: PRECIO_UNITARIO = SOLES / CANTIDAD para cada fila.
    Args:
        df: DataFrame con columnas de monto y cantidad
        col_soles: Nombre columna monto (default 'SOLES')
        col_cantidad: Nombre columna cantidad (default 'CANTIDAD')
        col_resultado: Nombre columna resultado (default 'PRECIO_UNITARIO')
    Returns:
        DataFrame con columna PRECIO_UNITARIO calculada
    """
    if col_soles not in df.columns or col_cantidad not in df.columns:
        return df

    df = df.copy()
    soles = pd.to_numeric(df[col_soles], errors="coerce").fillna(0)
    cantidad = pd.to_numeric(df[col_cantidad], errors="coerce").fillna(0)

    df[col_resultado] = soles / cantidad
    df.loc[cantidad == 0, col_resultado] = 0
    df[col_resultado] = (
        df[col_resultado]
        .replace([float("inf"), -float("inf")], 0)
        .fillna(0)
        .round(PRECIO_DECIMALES)
    )

    return df


def coerce_str(valor, default="No especificado") -> str:
    """Retorna el valor limpio o un default si es None/vacío/NaN. Reutilizable en cualquier renderer."""
    if valor is None:
        return default
    s = str(valor).strip()
    if s.lower() in ("nan", "none", "", "null"):
        return default
    return s


def safe_int(valor, default=0) -> int:
    """Convierte un valor a int de forma segura. Útil en iterrows() donde pandas puede devolver strings."""
    if valor is None:
        return default
    try:
        return int(float(str(valor).strip().replace(",", "")))
    except (TypeError, ValueError):
        return default


def safe_float(valor, default=0.0) -> float:
    """Convierte un valor a float de forma segura. Útil en iterrows() donde pandas puede devolver strings."""
    if valor is None:
        return default
    try:
        return float(str(valor).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def sanitize_label(label: str) -> str:
    """Limpia un label para usarlo en nombres de archivo (reemplaza espacios y slashes)."""
    return label.replace(" ", "_").replace("/", "-")


def build_expediente_dir(desktop_path, doc_ref, cliente, nombre_corto=""):
    """Construye el directorio del expediente comercial."""
    from datetime import datetime

    ts = datetime.now().strftime("%d%m%Y_%H%M%S")
    cliente_clean = sanitize_label(cliente or "CLIENTE")
    factura_clean = sanitize_label(doc_ref or "DOC")
    prefix = f"NC_{nombre_corto}" if nombre_corto else "EXP"
    folder = f"{prefix}_{cliente_clean}_{factura_clean}_{ts}"
    exp_dir = desktop_path / folder
    exp_dir.mkdir(parents=True, exist_ok=True)
    return exp_dir


def build_excel_filename(doc_ref, cliente, nombre_corto=""):
    """Construye el nombre del archivo Excel del expediente."""
    from datetime import datetime

    fecha_str = datetime.now().strftime("%d%m%Y")
    factura_clean = sanitize_label(doc_ref or "SIN_DOCP")
    cliente_clean = sanitize_label(cliente or "CLIENTE")
    if nombre_corto:
        return f"NC_{sanitize_label(nombre_corto)}_{factura_clean}_{cliente_clean}_{fecha_str}.xlsx"
    return f"{factura_clean}_{cliente_clean}_{fecha_str}.xlsx"


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

        if not s and not n:
            return t

        # Limpiar serie: Si ya empieza con el tipo (ej: F012), no lo duplicamos
        serie_clean = s
        if t and s and not s.startswith(t):
            serie_clean = f"{t}{s}"
        elif not s and t:
            serie_clean = t

        if not n:
            return serie_clean

        # El número se mantiene íntegro para evitar pérdida de datos (ej. 0457996)
        return f"{serie_clean}-{n}"
    except Exception:
        return ""


# ==================== SPLIT DOC_ID ====================
def split_doc_id(tpo: object, serie: object, nro: object) -> tuple:
    """
    Descompone un documento en (tipo, serie_limpia, nro_limpio) sin prefijo duplicado.

    Reglas:
    1. tipo = primera letra de tpo en mayuscula, default 'F' si vacio.
    2. serie_limpia = serie sin prefijo de tipo duplicado (e.g. 'F001' -> '001' si tipo='F')
                       y sin guiones iniciales.
    3. nro_limpio = parte posterior al primer '-' del nro, sin prefijo de tipo repetido
                    y sin guiones iniciales.

    Acepta tipos simples (str, None, etc.) o pd.Series. Para Series retorna tupla de Series.
    Para escalares retorna tupla de str.

    Casos cubiertos:
        'F', 'F001', '0457996'           -> ('F', '001', '0457996')   doc: F001-0457996
        'F', 'FF001', '0457996'          -> ('F', '001', '0457996')   doc: F001-0457996
        'F', '001', 'F-0457996'          -> ('F', '001', '0457996')   doc: F001-0457996
        'F', '', '0457996'               -> ('F', '',   '0457996')    doc: F-0457996
        'F', '', ''                      -> ('F', '',   '')           doc: F
        None, '', '' (o None)            -> ('F', '',   '')           default tipo='F'
        'nc' (lowercase), '001', '100'   -> ('N', '001', '100')       normaliza a mayuscula
    """
    import pandas as _pd

    if isinstance(tpo, _pd.Series) or isinstance(serie, _pd.Series) or isinstance(nro, _pd.Series):
        return _split_doc_id_series(tpo, serie, nro)
    return _split_doc_id_scalar(tpo, serie, nro)


def _strip_tipo_prefix(value: str, tipo: str) -> str:
    """Elimina prefijo de tipo repetido (while loop, e.g. 'FF' -> '')."""
    if not value:
        return ""
    while value.upper().startswith(tipo.upper()):
        value = value[1:]
    return value.lstrip("-").strip()


def _clean_part(value: object) -> str:
    """Convierte a str y limpia vacios tipo nan/None. Sin alterar ceros a la izquierda."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    return s


def _split_doc_id_scalar(tpo, serie, nro) -> tuple:
    """Implementacion escalar de split_doc_id."""
    t = _clean_part(tpo)
    if not t:
        t = "F"
    t = t[:1].upper()

    s_raw = _clean_part(serie)
    n_raw = _clean_part(nro)

    s_clean = _strip_tipo_prefix(s_raw, t)
    if "-" in n_raw:
        n_clean = n_raw.split("-", 1)[1]
    else:
        n_clean = n_raw
    n_clean = _strip_tipo_prefix(n_clean, t)

    return (t, s_clean, n_clean)


def _split_doc_id_series(tpo, serie, nro) -> tuple:
    """Implementacion vectorizada (pd.Series) de split_doc_id."""
    import pandas as _pd

    target_len = max(
        len(tpo) if isinstance(tpo, _pd.Series) else 1,
        len(serie) if isinstance(serie, _pd.Series) else 1,
        len(nro) if isinstance(nro, _pd.Series) else 1,
    )

    p_ser = _broadcast_to_series(tpo, target_len)
    s_ser = _broadcast_to_series(serie, target_len)
    n_ser = _broadcast_to_series(nro, target_len)

    p_str = p_ser.astype(str).str.strip()
    p_str = p_str.where(~p_str.str.lower().isin({"nan", "none", ""}), "F")
    tipo = p_str.str[:1].str.upper()

    s_str = s_ser.astype(str).str.strip()
    s_str = s_str.where(~s_str.str.lower().isin({"nan", "none"}), "")
    s_clean = _strip_series_while_starts(s_str, tipo)

    n_str = n_ser.astype(str).str.strip()
    n_str = n_str.where(~n_str.str.lower().isin({"nan", "none"}), "")
    has_dash = n_str.str.contains("-", na=False)
    n_after = n_str.where(~has_dash, n_str.str.split("-", n=1).str[1])
    n_clean = _strip_series_while_starts(n_after, tipo)

    return (tipo, s_clean, n_clean)


def _broadcast_to_series(value, target_len: int):
    """Convierte un valor a pd.Series del largo target.

    - Si ya es Series: reindexa o broadcastea.
    - Si es escalar: replica.
    """
    import pandas as _pd

    if isinstance(value, _pd.Series):
        if len(value) == target_len:
            return value.reset_index(drop=True)
        if len(value) == 1:
            return _pd.Series([value.iloc[0]] * target_len)
        raise ValueError(f"Series de largo {len(value)} no compatible con target {target_len}")
    return _pd.Series([value] * target_len)


def _strip_series_while_starts(s: "pd.Series", tipo: "pd.Series") -> "pd.Series":
    """Elimina prefijos de tipo con while loop vectorizado por chunks."""

    result = s.copy()
    unique_tipos = tipo.unique()
    for t in unique_tipos:
        # Determine if t should be skipped (empty or null) without triggering ambiguous truth value errors
        skip = False
        if t is None:
            skip = True
        elif isinstance(t, (str, bytes)):
            if not t:
                skip = True
        else:
            # For array-like or iterable objects (e.g., numpy arrays, lists, Series), check size without using bool()
            try:
                if len(t) == 0:
                    skip = True
            except TypeError:
                # Not sized, try truthiness safely
                try:
                    if not t:
                        skip = True
                except Exception:
                    # If truthiness fails, skip to be safe
                    skip = True
        if skip:
            continue

        mask_group = tipo == t
        grupo = result.where(mask_group, "")
        for _ in range(50):  # limite defensivo
            m = grupo.str.upper().str.startswith(t.upper())
            if not m.any():
                break
            grupo = grupo.where(~m, grupo.str[1:])
        result = result.where(~mask_group, grupo)
    return result.str.lstrip("-").str.strip()


def build_doc_full(tipo: str, serie_clean: str, nro_clean: str) -> str:
    """
    Construye el DOC_ID canónico a partir de los componentes limpios.
    Reglas:
        - Si hay serie: 'TIPO+SERIE-NRO'  (e.g. 'F001-0457996')
        - Sin serie, con nro: 'TIPO-NRO'   (e.g. 'F-0457996')
        - Solo tipo: 'TIPO'                (e.g. 'F')
    """
    t = (tipo or "F")[:1].upper() if tipo else "F"
    s = serie_clean or ""
    n = nro_clean or ""
    if s and n:
        return f"{t}{s}-{n}"
    if s:
        return f"{t}{s}"
    if n:
        return f"{t}-{n}"
    return t


# ==================== DROPDOWN OPTIONS (Flet UI) ====================
def build_dropdown_options(
    df: "pd.DataFrame",
    *,
    id_field: str,
    name_field: str,
    label_field: Optional[str] = None,
    key_field: Optional[str] = None,
    value_field: Optional[str] = None,
    max_options: int = 500,
) -> list:
    """Fabrica ``ft.dropdown.Option`` desde un DataFrame del ERP.

    Centraliza el patron duplicado en 2 vistas de reconocimiento_view.py:
    iterar ``df.loc[df['CLIENTE'].notna()].drop_duplicates(...)`` y construir
    ``ft.dropdown.Option(key=cnom, text=format_id_name(...))`` a mano.

    Args:
        df: DataFrame del historial (o subset).
        id_field: columna ID (e.g. ``COD_CLIENTE``).
        name_field: columna nombre (e.g. ``CLIENTE``).
        label_field: nombre del campo compuesto para ``format_id_name`` (opcional).
            Si None se infiere: 'SKU' si id_field es 'CODIGO', 'CLIENTE' si
            id_field es 'COD_CLIENTE', etc.
        key_field: nombre del df-key para ``dropdown.Option(key=...)``. Si None
            se usa name_field. Si id esta presente, se prefiere ``{id} - {name}``.
        value_field: nombre del campo value a guardar (default: id o name).
        max_options: cap defensivo para evitar dropdowns enormes.

    Returns:
        Lista de ``ft.dropdown.Option`` o ``[]`` si Flet no esta disponible /
        dataframe vacio / columnas no presentes.
    """
    try:
        import flet as ft
    except ImportError:
        return []

    if df is None or df.empty:
        return []

    if label_field is None:
        label_field = _infer_label_field(id_field)

    pairs = DataDictionary.get_id_name_pairs(
        df, id_field=id_field, name_field=name_field, return_tuples=True
    )

    options: List["ft.dropdown.Option"] = []
    for i, n in pairs[:max_options]:
        if i is None or i == "":
            text = n
            opt_key = str(n)
        elif n is None or n == "":
            text = i
            opt_key = str(i)
        else:
            text = format_id_name(i, n, label_field)
            opt_key = str(key_field or n)

        opt = ft.dropdown.Option(
            key=opt_key,
            text=text,
        )
        options.append(opt)

    return options


def _infer_label_field(id_field: str) -> str:
    """Inferencia de label para format_id_name segun id_field."""
    mapping = {
        "CODIGO": "SKU",
        "COD_ARTICULO": "SKU",
        "COD_CLIENTE": "CLIENTE",
        "COD_VENDEDOR": "VENDEDOR",
        "COD_LINEA": None,  # 'LINEA'
        "COD_SUCURSAL": "SUCURSAL",
    }
    return mapping.get(id_field, id_field) or id_field


def resolve_output_path(path: Path) -> Path:
    """
    Si el archivo ya existe, agrega sufijo (2), (3)... antes de la extensión.
    Útil para evitar colisiones en descargas sin sobreescribir archivos previos.

    Args:
        path: Ruta original del archivo.

    Returns:
        Ruta con sufijo numerado si el archivo ya existe, o la original si no.
    """
    path = Path(path)
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 2
    while True:
        new_path = parent / f"{stem} ({counter}){suffix}"
        if not new_path.exists():
            return new_path
        counter += 1
