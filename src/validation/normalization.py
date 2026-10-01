import unicodedata
import pandas as pd
import re
import logging
from typing import Optional, List
from src.core.utils import (
    HEADER_MAP,
    HEADER_KEYWORDS,
    clean_id_column,
    split_doc_id,
    build_doc_full,
    calcular_precio_unitario_df,
    PRECIO_DECIMALES,
    normalizar_porcentaje_serie,
)

logger = logging.getLogger(__name__)

# Precisión unificada del pipeline: precios unitarios y diferencias se
# trabajan a 5 decimales en toda la cadena (lista → neto → diferencia).
# El redondeo a 2 decimales ocurre solo en el monto final (MONTO_NC).


def _limpiar_col(col) -> str:
    if hasattr(col, "__len__") and not isinstance(col, (str, bytes)):
        return ""
    try:
        if pd.isna(col):
            return ""
    except (ValueError, TypeError):
        return ""
    if pd.isna(col):
        return ""
    s = str(col).replace("\ufeff", "").strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    return "".join(c for c in s if c.isprintable())


# Columnas examinadas para detectar la fila de cabecera del ERP.
# Mantener alineado con HEADER_KEYWORDS de utils.py (cubre mas variantes para robustez).
IDS_COLUMNAS_ERP = (
    "CODIGO",
    "NUMERO",
    "COD_CLIENTE",
    "DOC_CLIENTE",
    "TIPO_DOC",
    "SERIE",
    "COD_LINEA",
    "COD_GRUPO",
    "COD_TIPO",
    "COD_FAMILIA",
    "COD_VENDEDOR",
    "COD_SUCURSAL",
)
COLS_NUM_INGESTA = (
    "CANTIDAD",
    "SOLES",
    "DOLARES",
    "PRECIO_UNITARIO",
    "PRECIO_BASE",
    "PRECIO_CORRECTO",
    "PRECIO_NETO",
)
COLS_FECHA = ("FECHA", "FECHA_REF", "FECHA_VENC")
COLS_CRITICAS_ERP = ("ANHO", "CODIGO", "CANTIDAD", "FECHA", "SOLES", "NUMERO")


class NormalizationEngine:
    """Normalización robusta de historial de compras.
    Soporta múltiples formatos ERP, detecta cabeceras dinámicamente,
    estandariza nombres de columna, IDs de documento y SKU.

    Esta clase es el motor canónico de ingesta + normalización del proyecto.
    AllocationEngine / FeriaPreventaStrategy consumen sus métodos.
    """

    def __init__(self, schema: Optional[dict] = None):
        self.schema = schema or {}
        # Alertas detectadas durante la normalizacion (por ejemplo: un
        # descuento capturado como puntos de porcentaje). El Pipeline las
        # transfiere al expediente y las strategies a su resultado.
        self.alertas: list = []

    def _registrar_alertas_descuento(
        self, df: pd.DataFrame, col: str, codigos: Optional[pd.Series]
    ) -> None:
        """Agrega AL04/AL10 por columna de descuento (una alerta por codigo)."""
        if codigos is None or not codigos.notna().any():
            return
        from src.domain import BusinessAlert

        tiene_sku = "SKU" in df.columns or "CODIGO" in df.columns
        sku_col = "SKU" if "SKU" in df.columns else ("CODIGO" if "CODIGO" in df.columns else None)
        specs = {
            "AL04": (
                "warning",
                "media",
                "descuento capturado fuera de rango; se interpreto como "
                "puntos de porcentaje (ej: 5 = 5%)",
            ),
            "AL10": (
                "info",
                "baja",
                "descuento no interpretable; se tomo 0. Revisar el "
                "formato de la columna (ej: '0,05' con coma decimal)",
            ),
        }
        for codigo, (tipo, severidad, detalle) in specs.items():
            mask = codigos == codigo
            if not mask.any():
                continue
            afectados = []
            if tiene_sku and sku_col is not None:
                afectados = [str(v).strip() for v in df.loc[mask, sku_col]]
            muestra = ", ".join(sorted(set(afectados))[:5])
            if len(set(afectados)) > 5:
                muestra += f" (+{len(set(afectados)) - 5} mas)"
            quien = f" en SKU {muestra}" if muestra else ""
            self.alertas.append(
                BusinessAlert(
                    codigo=codigo,
                    tipo=tipo,
                    severidad=severidad,
                    mensaje=(f"{codigo} - Columna {col}{quien}: {detalle}"),
                    motor="Normalization",
                )
            )

    def preparar_historial_erp(
        self,
        df: pd.DataFrame,
        validar_columnas: bool = True,
        omitidas_sink: Optional[List[dict]] = None,
        reescribir_precio_unitario_cero: bool = True,
    ) -> pd.DataFrame:
        """Prepara un DataFrame del historial ERP (unifica ingestas NCProcessor + estrategias).

        Pasos aplicados en orden:
        1. Deteccion dinamica de la fila de cabecera (idempotente).
        2. Purga de fila TOTAL/TOTALES al final si existe.
        3. Mapeo de variantes de nombres de columna al estandar interno (HEADER_MAP).
        4. Strip de columnas string (object dtype).
        5. Limpieza de IDs como texto preservando ceros a la izquierda.
        6. Conversion de columnas numericas con reemplazo O->0.
        7. Normalizacion de fechas (serial Excel + dayfirst).
        8. Calculo de PRECIO_UNITARIO si falta (o si suma 0 y la opcion esta activa).
        9. Validacion de columnas criticas (raise ValueError si validar_columnas=True).

        Args:
            df: DataFrame crudo del ERP (puede tener filas vacias iniciales, BOM en
                cabeceras, totales al final, etc.).
            validar_columnas: si True, lanza ValueError cuando faltan columnas criticas.
                Default True para detectar errores temprano.
            registrar_omitidas: lista opcional donde se registran filas con fechas
                ilegibles (consumida por NCProcessor para auditoria).
            reescribir_precio_unitario_cero: si True y PRECIO_UNITARIO suma 0 pero
                hay SOLES/CANTIDAD, lo recalcula. Util cuando el ERP trae precio en 0
                por export incorrecto.

        Returns:
            DataFrame normalizado y validado, listo para analisis/estrategia/NC.

        Raises:
            ValueError: si validar_columnas=True y faltan cols criticas (ANHO, CODIGO,
                CANTIDAD, FECHA, SOLES, NUMERO).
        """
        if df.empty:
            return df

        df = df.copy()

        # 1. Deteccion + limpieza de cabecera
        df = self._identify_and_clean_headers(df)

        # 2. Mapeo de headers al estandar interno (HEADER_MAP)
        df = self._apply_header_map(df)

        # 3. Strip universal de strings
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].fillna("").astype(str).str.strip()

        # 4. Limpieza de IDs como texto (sin coerción numerica -> preserva ceros iniciales)
        for col in IDS_COLUMNAS_ERP:
            if col in df.columns:
                df[col] = clean_id_column(df[col])

        # 5. Limpieza numerica con reemplazo O->0
        for col in COLS_NUM_INGESTA:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.upper().str.replace("O", "0").str.strip(),
                    errors="coerce",
                ).fillna(0)

        # 6. Normalizacion de fechas (serial Excel + dayfirst)
        df = self._normalize_dates(df, omitidas_sink=omitidas_sink)

        # 7. PRECIO_UNITARIO: calcular si falta o es 0
        if "PRECIO_UNITARIO" not in df.columns or (
            reescribir_precio_unitario_cero
            and "SOLES" in df.columns
            and "CANTIDAD" in df.columns
            and (
                pd.api.types.is_numeric_dtype(df["PRECIO_UNITARIO"])
                and df["PRECIO_UNITARIO"].sum() == 0
            )
        ):
            df = calcular_precio_unitario_df(df)

        # 8. Validacion de columnas criticas
        if validar_columnas:
            self._validar_columnas_criticas(df)

        return df

    def _validar_columnas_criticas(self, df: pd.DataFrame) -> None:
        """Lanza ValueError si faltan columnas criticas para historial ERP."""
        faltantes = [c for c in COLS_CRITICAS_ERP if c not in df.columns]
        if faltantes:
            raise ValueError(
                f"No se pudieron encontrar las columnas criticas: {', '.join(faltantes)}"
            )

    def normalizar_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()

        # 1. Detectar fila de cabecera dinámicamente
        df = self._identify_and_clean_headers(df)

        # 2. Mapear variantes de nombres de columna al estándar interno
        df = self._apply_header_map(df)

        # 3. Limpiar valores string (antes de conversión numérica)
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].fillna("").astype(str).str.strip()

        # 4. Limpiar tipos de datos (IDs como string, numéricos, O/0)
        df = self._clean_data_types(df)

        # 5. Normalizar fechas (incluyendo seriales Excel)
        df = self._normalize_dates(df)

        # 6. Calcular PRECIO_UNITARIO si no existe
        if (
            "PRECIO_UNITARIO" not in df.columns
            and "SOLES" in df.columns
            and "CANTIDAD" in df.columns
        ):
            df["PRECIO_UNITARIO"] = df.apply(
                lambda r: r["SOLES"] / r["CANTIDAD"] if r["CANTIDAD"] > 0 else 0,
                axis=1,
            )

        # 7. Normalizar tipo de documento (primer carácter)
        if "TIPO_DOC" in df.columns:
            df["TIPO_DOC"] = df["TIPO_DOC"].astype(str).str.strip().str.upper().str[0]

        # 8. Generar DOC_ID en formato estándar F201-1515
        self._normalizar_doc_id(df)

        # 9. Asegurar que columnas numéricas sean float64 (previene mixed types en iterrows)
        _COLS_NUMERICAS_FINALES = [
            "CANTIDAD",
            "SOLES",
            "PRECIO_UNITARIO",
            "DOLARES",
            "PRECIO_BASE",
            "PRECIO_CORRECTO",
            "PRECIO_NETO",
        ]
        for col in _COLS_NUMERICAS_FINALES:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("float64")

        return df

    def _identify_and_clean_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Busca dinámicamente la fila de encabezados basada en palabras clave.
        Si la primera fila ya parece cabecera, la usa directamente."""
        # Si la primera fila contiene palabras clave de cabecera, ya está bien
        first_row = {_limpiar_col(val) for val in df.iloc[0].values}
        if len(HEADER_KEYWORDS.intersection(first_row)) >= 2:
            df.columns = [_limpiar_col(c) for c in df.iloc[0]]
            df = df.iloc[1:].reset_index(drop=True)
            # Tras consumir fila 0 como cabecera, purga TOTAL al final si existe
            if not df.empty:
                last = df.iloc[-1].astype(str).str.contains(r"TOTAL|TOTALES", case=False, na=False)
                if last.any():
                    df = df.iloc[:-1].reset_index(drop=True)
            return df

        # Buscar en todas las filas
        header_idx = -1
        for i, row in df.iterrows():
            row_vals = {_limpiar_col(val) for val in row.values}
            if len(HEADER_KEYWORDS.intersection(row_vals)) >= 2:
                header_idx = i
                break

        if header_idx != -1:
            df.columns = [_limpiar_col(c) for c in df.iloc[header_idx]]
            df = df.iloc[header_idx + 1 :].reset_index(drop=True)
        else:
            df.columns = [_limpiar_col(c) for c in df.columns]

        # Eliminar fila de totales al final
        if not df.empty:
            last = df.iloc[-1].astype(str).str.contains(r"TOTAL|TOTALES", case=False, na=False)
            if last.any():
                df = df.iloc[:-1].reset_index(drop=True)

        return df

    def _apply_header_map(self, df: pd.DataFrame) -> pd.DataFrame:
        """Renombra columnas según HEADER_MAP. Conserva nombres ya estándar."""
        cols_actuales = list(df.columns)
        nuevos = {}
        for c in cols_actuales:
            c_norm = _limpiar_col(c)
            if c_norm in HEADER_MAP:
                nuevos[c] = HEADER_MAP[c_norm]
        if nuevos:
            df = df.rename(columns=nuevos)
        # Segunda pasada: asegurar uppercase en columnas ya estándar
        df.columns = [_limpiar_col(c) for c in df.columns]
        return df

    def _clean_data_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Limpia tipos de datos: IDs a string, numéricos a float."""
        cols_id = [
            "CODIGO",
            "TIPO_DOC",
            "SERIE",
            "NUMERO",
            "COD_CLIENTE",
            "DOC_CLIENTE",
            "COD_VENDEDOR",
            "COD_SUCURSAL",
            "GUIA",
            "ORDEN_COMPRA",
        ]
        for col in cols_id:
            if col in df.columns:
                df[col] = clean_id_column(df[col])

        for col in [
            "CANTIDAD",
            "SOLES",
            "DOLARES",
            "PRECIO_UNITARIO",
            "PRECIO_BASE",
            "PRECIO_CORRECTO",
            "PRECIO_NETO",
        ]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.upper().str.replace("O", "0").str.strip(),
                    errors="coerce",
                ).fillna(0)
        return df

    def _normalize_dates(
        self,
        df: pd.DataFrame,
        omitidas_sink: Optional[List[dict]] = None,
    ) -> pd.DataFrame:
        """Convierte fechas manejando seriales de Excel y formatos mixtos.

        Si omitidas_sink es una lista, se anade a ella un dict por cada fila
        cuya fecha no se pudo parsear (con CODIGO, NUMERO, CANTIDAD si existen).
        """
        for col in COLS_FECHA:
            if col not in df.columns:
                continue
            df[col] = df[col].astype(str).str.strip().replace(["", "nan", "NaN", "None"], pd.NA)

            # Manejar seriales Excel
            def _convert_excel(v):
                if hasattr(v, "__len__") and not isinstance(v, (str, bytes)):
                    return pd.NA
                try:
                    if pd.isna(v):
                        return pd.NA
                except (ValueError, TypeError):
                    return pd.NA
                try:
                    fv = float(v)
                    if 1 <= fv <= 100000:
                        return pd.to_datetime(int(fv), unit="D", origin="1899-12-30")
                except (ValueError, TypeError):
                    pass
                return v

            df[col] = df[col].apply(_convert_excel)
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce", format="mixed")

            # Reportar omitidas: filas donde la fecha quedo NaT tras parseo
            # (solo si el caller explicitamente registro la columna FECHA y dio sink).
            if omitidas_sink is not None and col == "FECHA":
                mascara_invalidos = df[col].isna()
                if mascara_invalidos.any():
                    cols_reporte = [c for c in ("NUMERO", "CODIGO", "CANTIDAD") if c in df.columns]
                    if cols_reporte:
                        omitidas_sink.extend(
                            df.loc[mascara_invalidos, cols_reporte].to_dict("records")
                        )
                    else:
                        omitidas_sink.extend({"_fila": idx} for idx in df.index[mascara_invalidos])

        return df

    def _normalizar_doc_id(self, df: pd.DataFrame) -> None:
        """Genera columna DOC_ID en formato F201-1515 a partir de TIPO_DOC, SERIE, NUMERO."""
        tiene_tpo = "TIPO_DOC" in df.columns
        tiene_serie = "SERIE" in df.columns
        tiene_nro = "NUMERO" in df.columns

        if not (tiene_tpo or tiene_serie or tiene_nro):
            return

        # Vectorizado: aplica split_doc_id a las 3 columnas y construye DOC_ID
        tipo_in = df["TIPO_DOC"] if tiene_tpo else "F"
        serie_in = df["SERIE"] if tiene_serie else ""
        nro_in = df["NUMERO"] if tiene_nro else ""
        tipo_s, serie_s, nro_s = split_doc_id(tipo_in, serie_in, nro_in)

        # Construye DOC_ID usando vectorizacion (build_doc_full escalar en loop)
        # Para preservar dtype, lo hacemos elemento por elemento sobre los valores ya limpios
        # (split_doc_id ya aplico las reglas de limpieza; build_doc_full solo formatea)
        docs = []
        for t, s, n in zip(
            tipo_s.tolist() if hasattr(tipo_s, "tolist") else tipo_s,
            serie_s.tolist() if hasattr(serie_s, "tolist") else serie_s,
            nro_s.tolist() if hasattr(nro_s, "tolist") else nro_s,
        ):
            docs.append(build_doc_full(t, s, n))
        df["DOC_ID"] = docs

    def normalizar_condicion(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        df.columns = [_limpiar_col(c) for c in df.columns]

        # Normalizar DESC_01 → DESC1 y DESC01 → DESC1 para descuentos
        for col in list(df.columns):
            m = re.match(r"^DESC_?0*(\d+)$", col, re.IGNORECASE)
            if m:
                df = df.rename(columns={col: f"DESC{int(m.group(1))}"})

        # Mapear headers de plantilla a nombres internos
        header_map = self.schema.get("header_map", {})
        for col in list(df.columns):
            if col in header_map:
                df = df.rename(columns={col: header_map[col]})

        df.columns = [_limpiar_col(c) for c in df.columns]
        columnas_requeridas = self.schema.get("columnas_requeridas", [])
        columnas_descuento_cfg = self.schema.get("columnas_descuento", None)
        if columnas_descuento_cfg is None:
            cols_descuento = []
            convert_percent = False
        else:
            convert_percent = columnas_descuento_cfg.get("convert_percent", True)
            pattern = columnas_descuento_cfg.get("pattern", "^DESC\\d+$")
            cols_descuento = [c for c in df.columns if re.match(pattern, c, re.IGNORECASE)]
            cols_descuento.sort()
        for c in cols_descuento:
            if not convert_percent:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
                continue
            # Convención canónica: el archivo guarda la fraccion (0.05 = 5%).
            # Se aceptan ademas los puntos de porcentaje (5 = 5%, sin alerta
            # porque aplican bien; AL04 solo si exceden 100%) y los textos
            # con '%'. Todo lo no interpretable queda en 0 con AL10.
            fracciones, codigos = normalizar_porcentaje_serie(df[c])
            df[c] = fracciones
            self._registrar_alertas_descuento(df, c, codigos)
        for col in columnas_requeridas:
            if col in df.columns and col not in cols_descuento and col not in ("SKU", "CODIGO"):
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(r"[S/ ]", "", regex=True).str.strip(),
                    errors="coerce",
                ).fillna(0)
        for col in ["SKU", "CODIGO"]:
            if col in df.columns:
                df[col] = clean_id_column(df[col])
        return df

    def validar_schema(self, df: pd.DataFrame) -> list:
        errores = []
        req = self.schema.get("columnas_requeridas", [])
        for col in req:
            if col not in df.columns:
                errores.append(
                    {"columna": col, "mensaje": f"Columna requerida '{col}' no encontrada"}
                )
        tipos = self.schema.get("tipos", {})
        for col, tipo_cfg in tipos.items():
            if col not in df.columns:
                continue
            tipo = tipo_cfg if isinstance(tipo_cfg, str) else tipo_cfg.get("type", "string")
            if tipo == "float" and col in df.columns:
                try:
                    pd.to_numeric(df[col], errors="raise")
                except (ValueError, TypeError):
                    errores.append(
                        {"columna": col, "mensaje": f"Columna '{col}' debe ser numérica"}
                    )
        return errores

    def aplicar_cadena_descuentos(
        self, df: pd.DataFrame, precio_col: str = "PRECIO_BASE"
    ) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        pattern = self.schema.get("columnas_descuento", {}).get("pattern", "^DESC\\d+$")
        cols_desc = sorted([c for c in df.columns if re.match(pattern, c, re.IGNORECASE)])
        if not cols_desc:
            return df
        precio_inicial = df[precio_col]
        precio_calculado = precio_inicial.copy()
        for c in cols_desc:
            # Coercion defensiva: el pipeline ya normalizo las condiciones,
            # pero esta funcion se llama tambien directamente desde las
            # strategies con datos crudos ('5%', 5.0, '0,05', ...).
            fracciones, codigos = normalizar_porcentaje_serie(df[c])
            df[c] = fracciones
            self._registrar_alertas_descuento(df, c, codigos)
            precio_calculado *= 1 - fracciones
        df["PRECIO_CALCULADO"] = precio_calculado.round(PRECIO_DECIMALES)
        # Barrera: un precio neto negativo es imposible. Si aparece, el
        # descuento o la base estan mal capturados (nunca se propaga).
        negativos = (df["PRECIO_CALCULADO"] < 0) & (precio_inicial > 0)
        if negativos.any():
            from src.domain import BusinessAlert

            skus = []
            if "SKU" in df.columns:
                skus = [str(v).strip() for v in df.loc[negativos, "SKU"]]
            muestra = ", ".join(sorted(set(skus))[:5]) or "varios SKU"
            self.alertas.append(
                BusinessAlert(
                    codigo="AL04",
                    tipo="warning",
                    severidad="media",
                    mensaje=(
                        f"AL04 - Precio neto calculado negativo en {muestra}: "
                        "descuento supera el precio base. Se ignora la cadena "
                        "de descuentos para esos SKU."
                    ),
                    motor="Normalization",
                )
            )
            df.loc[negativos, "PRECIO_CALCULADO"] = precio_inicial[negativos].round(
                PRECIO_DECIMALES
            )
        if "PRECIO_NETO" in df.columns and not df.columns.duplicated().any():
            suma_neto = df["PRECIO_NETO"].sum()
            if isinstance(suma_neto, (int, float)) and suma_neto > 0:
                tolerancia = self.schema.get("validation", {}).get("tolerancia_precio_neto", 0.01)
                df["_DIF_NETO"] = (df["PRECIO_CALCULADO"] - df["PRECIO_NETO"]).abs()
                df["_NETO_OK"] = df["_DIF_NETO"] <= tolerancia
        return df
