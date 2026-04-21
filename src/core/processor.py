import pandas as pd
import re
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProcessedItem:
    """
    Representa el resultado final del procesamiento de un ítem de Nota de Crédito.
    Contiene tanto la data técnica como el estado de auditoría para el reporte Excel.
    """
    ID_ARTICULO: str
    NOM_ARTICULO: str
    CANTIDAD_SOLICITADA: int
    CANTIDAD_REAL_ENCONTRADA: int
    PRECIO_UNITARIO: float
    MONTO_DESCUENTO_UNITARIO: float
    PRECIO_NETO_FINAL: float
    SUBTOTAL_DESCUENTO: float
    PORCENTAJE_APLICADO: float
    DOCUMENTOS: List[str]
    STATUS: str
    NRO_DOC: str = ""
    SERIE_DOC: str = ""
    FACTURA_REF: str = "" # Documento principal de referencia
    DOCUMENTOS_CANTIDAD: Dict[str, float] = field(default_factory=dict)  # Mapeo de doc => cantidad tomada


class NCProcessor:
    """
    Motor de procesamiento de Notas de Crédito con lógica FIFO Inversa
    Orden: Facturas más recientes primero, asignación hacia atrás
    """

    def __init__(self, historial_compras: pd.DataFrame):
        """
        Inicializa el procesador preparando la base de datos de historial.
        Utiliza un diccionario de caché para optimizar búsquedas repetitivas de SKUs.
        """
        self.filas_omitidas_detalle: List[Dict] = []
        self.historial = self._preparar_historial(historial_compras)
        self._cache_articulos: Dict[str, pd.DataFrame] = {}

    def _limpiar_col_universal(self, col_name) -> str:
        """
        Limpieza profunda de nombres de columnas. Elimina BOM de archivos UTF-8,
        espacios en blanco y caracteres no imprimibles.
        """
        if pd.isna(col_name): return ""
        s = str(col_name).replace('\ufeff', '').strip().upper()
        return "".join(char for char in s if char.isprintable())

    # Columnas oficiales del reporte Historial
    COLUMNAS_HISTORIAL = (
        "ANHO", "MES", "DOC_CLIENTE", "ID_CLIENTE", "NOM_CLIENTE", 
        "ID_LOCALIDAD_UBIGEO", "NOM_DEPARTAMENTO", "NOM_PROVINCIA", "NOM_DISTRITO",
        "ID_LINEA", "NOM_LINEA", "ID_GRUPO", "NOM_GRUPO", "ID_TIPO", "NOM_TIPO",
        "ID_FAMILIA", "NOM_FAMILIA", "ESTADO_LINEA",
        "ID_ARTICULO", "NOM_ARTICULO", # Columnas críticas para el procesamiento
        "ID_VENDEDOR", "NOM_VENDEDOR", "CANAL DE DISTRIBUCION",
        "COD_SUCURSAL", "NOM_SUCURSAL",
        "TPO_DOC", "SERIE_DOC", "NRO_DOC", "ORD_COMPRA", "ID_GUIA",
        "FECHA_ORIG", "REFERENCIA", "FECHA_REF", "MONEDA",
        "CANTIDAD", "SOLES", "DOLARES", "NOM_CONDICION_PAGO", "ID_PEDIDO",
        "FECHA_VENC", "DIVISION", "PRECIO_UNID"
    )

    def _preparar_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepara y ordena el historial de compras con detección robusta de cabeceras.
        Normaliza nombres de columnas, asegura tipos de datos y ordena para lógica FIFO.
        """
        logger.info("Iniciando preparación de historial de compras...")

        # Evitar re-procesamiento si las columnas críticas ya están normalizadas (Idempotencia)
        cols_criticas_set = {"ANHO", "ID_ARTICULO", "CANTIDAD", "FECHA_ORIG", "SOLES", "NRO_DOC"}
        es_primera_vez = not cols_criticas_set.issubset(df.columns)
        
        if not es_primera_vez:
            logger.info("El historial ya presenta columnas normalizadas. Ejecutando limpieza de tipos de datos de todas formas.")
        else:
            # Si no está normalizado, es la primera carga: realizamos limpieza completa y ordenamiento inicial
            # 1. Identificar y limpiar cabeceras
            df = self._identify_and_clean_headers(df)
            logger.debug(f"Cabeceras identificadas. Columnas actuales: {df.columns.tolist()}")

            # 2. Normalizar nombres de columnas y validación
            df = self._normalize_column_names(df)

        # 3. Copia profunda y limpieza de tipos de datos
        df = self._clean_data_types(df.copy())

        # 4. Procesar fechas y ordenar siempre (es fundamental para la lógica FIFO y evitar errores de tipos)
        df = self._parse_dates_and_sort(df)
        
        logger.info(f"Historial preparado con éxito: {len(df)} registros válidos.")
        
        return df

    def _identify_and_clean_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Busca dinámicamente la fila de encabezados basada en palabras clave.
        Esto permite procesar archivos que tengan filas vacías o logos al inicio.
        """
        header_row_index = -1
        keywords_cabecera = {"ANHO", "AÑO", "DOC_CLIENTE", "ID_ARTICULO", "NRO_DOC"}
        for i, row in df.iterrows():
            row_cleansed = {self._limpiar_col_universal(val) for val in row.values if pd.notna(val)}
            if len(keywords_cabecera.intersection(row_cleansed)) >= 2:
                header_row_index = i
                break
        if header_row_index != -1:
            df.columns = [self._limpiar_col_universal(c) for c in df.iloc[header_row_index]]
            df = df.iloc[header_row_index + 1:].reset_index(drop=True)
            logger.info(f"Headers detectados en fila {header_row_index + 1}")
        else:
            df.columns = [self._limpiar_col_universal(c) for c in df.columns]
            logger.warning("No se detectaron palabras clave de cabecera; se usará la primera fila.")

        if not df.empty:
            if df.iloc[-1].astype(str).str.contains(r'TOTAL|TOTALES', case=False, na=False).any():
                df = df.iloc[:-1].reset_index(drop=True)
        return df

    def _normalize_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Unifica los nombres de columnas de diferentes versiones de reportes ERP
        a un estándar interno (ej: 'PRECIO UNITARIO' -> 'PRECIO_UNID').
        """
        mapeo_norm = {
            "AÑO": "ANHO", "PRECIO_UNI": "PRECIO_UNID", "PRECIO UNID": "PRECIO_UNID",
            "PRECIO UNITARIO": "PRECIO_UNID", "PRECIO_UNIDR": "PRECIO_UNID"
        }
        df.columns = [mapeo_norm.get(c, c) for c in df.columns]
        cols_criticas = ["ANHO", "ID_ARTICULO", "CANTIDAD", "FECHA_ORIG", "SOLES", "NRO_DOC"]
        faltantes = [c for c in cols_criticas if c not in df.columns]
        if faltantes:
            logger.error(f"Columnas críticas faltantes: {faltantes}")
            raise ValueError(f"No se pudieron encontrar las columnas: {', '.join(faltantes)}")
        return df

    def _clean_data_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Asegura la integridad de los datos: convierte IDs a string (evita notación científica),
        limpia errores comunes de digitación ('O' por '0') y recalcula precios reales.
        """
        cols_a_string = ["ID_ARTICULO", "NRO_DOC", "ID_CLIENTE", "DOC_CLIENTE", "TPO_DOC", "SERIE_DOC"]
        for col in cols_a_string:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip().replace('nan', '')

        # Limpieza numérica optimizada
        for col in ["CANTIDAD", "SOLES"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.upper().str.replace('O', '0').str.strip(), 
                    errors='coerce'
                ).fillna(0)

        # Protección contra re-cálculo en multi-reportes
        if "PRECIO_UNID" not in df.columns or df["PRECIO_UNID"].sum() == 0:
            df["PRECIO_UNID"] = (df["SOLES"] / df["CANTIDAD"]).replace([float("inf"), -float("inf")], 0).fillna(0).round(4)
            
        return df

    def _parse_dates_and_sort(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convierte fechas manejando seriales de Excel y formatos mixtos.
        Registra las filas omitidas para auditoría.
        SOLUCION: Error 'not supported between instances of Str and float'
        """
        # ✅ PRIMERO: Limpieza universal, eliminamos cadenas vacias y nan antes de cualquier procesamiento
        df['FECHA_ORIG'] = df['FECHA_ORIG'].astype(str).str.strip().replace(['', 'nan', 'NaN', 'None'], pd.NA)

        # 1. Manejo de fechas seriales de Excel (números que representan días desde 1900)
        def convert_excel_date(x):
            if pd.isna(x):
                return pd.NA
            try:
                # Intentamos convertir a float para capturar "45292.0" o 45292
                f_val = float(x)
                # Rango de seguridad: fechas entre 1900 (1) y 2100 (73050 aprox)
                if 1 <= f_val <= 100000:
                    return pd.to_datetime(int(f_val), unit='D', origin='1899-12-30')
            except (ValueError, TypeError):
                pass
            return x

        df['FECHA_ORIG'] = df['FECHA_ORIG'].apply(convert_excel_date)

        if not pd.api.types.is_datetime64_any_dtype(df['FECHA_ORIG']):
            df['FECHA_ORIG'] = pd.to_datetime(df['FECHA_ORIG'], dayfirst=True, errors='coerce', format='mixed')

        # 2. Identificar qué filas se van a omitir antes de borrarlas
        mascara_invalidos = df['FECHA_ORIG'].isna()
        if mascara_invalidos.any():
            detalles = df[mascara_invalidos][['NRO_DOC', 'ID_ARTICULO', 'CANTIDAD']].to_dict('records')
            self.filas_omitidas_detalle.extend(detalles)
            logger.warning(f"Se omitirán {len(detalles)} filas por fechas ilegibles.")
            for d in detalles[:5]: # Loguear solo las primeras 5 como muestra
                logger.debug(f"Fila inválida: Doc {d.get('NRO_DOC')} - SKU {d.get('ID_ARTICULO')}")

        # 3. Limpiar y ordenar
        df = df.dropna(subset=['FECHA_ORIG']).reset_index(drop=True)
        df = df.sort_values(by=['ID_ARTICULO', 'FECHA_ORIG'], ascending=[True, False]).reset_index(drop=True)
        return df

    def _get_articulo_historial(self, codigo: str) -> pd.DataFrame:
        """
        Obtiene el sub-dataframe de un SKU. Utiliza caché para evitar
        operaciones de filtrado costosas en reportes grandes.
        """
        if codigo not in self._cache_articulos:
            self._cache_articulos[codigo] = self.historial[self.historial['ID_ARTICULO'] == codigo].copy()
        return self._cache_articulos[codigo]

    def procesar_articulo(
        self,
        codigo: str,
        cantidad_nc: int,
        porcentaje_desc: float,
        forzar_cantidad_solicitada: bool = True
    ) -> ProcessedItem:
        """
        Orquesta el procesamiento individual. Si el artículo existe, ejecuta la 
        asignación de documentos; si no, genera un estado de error. Maneja casos
        donde la cantidad solicitada es cero para retornar información informativa.
        """
        codigo_limpio = re.sub(r'\.0$', '', str(codigo)).strip()
        articulo_historial = self._get_articulo_historial(codigo_limpio)
        
        if articulo_historial.empty:
            return self._crear_item_error(codigo_limpio, cantidad_nc, porcentaje_desc)

        # Referencias base (el más reciente)
        reciente = articulo_historial.iloc[0]
        nombre_articulo = reciente['NOM_ARTICULO']
        
        # Si la cantidad es 0 o negativa, no ejecutamos FIFO pero devolvemos info base
        if cantidad_nc <= 0:
            return self._construir_item_vacio(codigo_limpio, nombre_articulo, porcentaje_desc, reciente)

        # Lógica FIFO
        asignacion = self._ejecutar_asignacion_fifo(articulo_historial, cantidad_nc)
        
        return self._finalizar_item(
            codigo_limpio, nombre_articulo, cantidad_nc, porcentaje_desc, 
            asignacion, reciente, forzar_cantidad_solicitada
        )

    def _ejecutar_asignacion_fifo(self, articulo_historial: pd.DataFrame, cantidad_nc: int) -> Dict:
        """
        Lógica core de asignación: recorre el historial (ya ordenado por fecha DESC)
        y va descontando de las facturas la cantidad necesaria para sustentar la NC.
        Detecta si un artículo proviene de múltiples precios/facturas.
        Retorna también el mapeo de cantidad por documento para auditoría posterior.
        """
        res = {"docs": [], "doc_cantidad": {}, "precios": set(), "asignado": 0, "restante": cantidad_nc, "valor_soporte_total": 0.0, "doc_montos": {}}
        for _, fila in articulo_historial.iterrows():
            if res["restante"] <= 0:
                break
            tomar = min(fila['CANTIDAD'], res["restante"])
            tipo = str(fila['TPO_DOC']).strip()
            tipo = tipo[0] if tipo else 'F'

            # Extraer serie: limpiar prefijos y guiones
            serie = str(fila['SERIE_DOC']).strip()
            # Eliminar el tipo de documento si está al inicio
            while serie.upper().startswith(tipo.upper()):
                serie = serie[1:]
            # Eliminar guiones
            serie = serie.lstrip('-').strip()

            # Extraer número: eliminar prefijos y tipo
            nro = str(fila['NRO_DOC']).strip()
            # Si nro contiene guión, tomar la parte numérica después del guión
            if '-' in nro:
                nro = nro.split('-', 1)[1]
            # Eliminar cualquier prefijo de tipo que haya quedado
            while nro.upper().startswith(tipo.upper()):
                nro = nro[1:]
            nro = nro.lstrip('-').strip()

            doc_full = f"{tipo}{serie}-{nro}"
            if doc_full not in res["docs"]:
                res["docs"].append(doc_full)
                res["doc_cantidad"][doc_full] = 0
                res["doc_montos"][doc_full] = 0
            
            monto_proporcional = tomar * float(fila['PRECIO_UNID'])
            res["doc_cantidad"][doc_full] += tomar
            res["doc_montos"][doc_full] += monto_proporcional
            res["valor_soporte_total"] += monto_proporcional
            res["precios"].add(round(float(fila['PRECIO_UNID']), 2))
            res["asignado"] += tomar
            res["restante"] -= tomar
        return res

    def _finalizar_item(self, cod, nom, cant_nc, porc, asig, reciente, forzar) -> ProcessedItem:
        """
        Calcula los montos financieros finales (Descuento Unitario, Neto y Subtotal)
        y determina el mensaje de estado basado en el éxito de la asignación FIFO.
        """
        precio_ref = round(float(reciente['PRECIO_UNID']), 4)
        status = "OK"
        if asig["restante"] > 0:
            status = f"PENDIENTE SUSTENTO: Sustentado: {int(asig['asignado'])} | Faltan: {int(asig['restante'])}."
        elif len(asig["precios"]) > 1:
            p_min = min(asig["precios"])
            p_max = max(asig["precios"])
            status = f"INFO: Precios variables (Rango: {p_min:.2f}-{p_max:.2f}). Se usó el más reciente: {precio_ref:.4f}"

        m_desc_u = round(precio_ref * porc, 4)
        cant_f = cant_nc if forzar else asig["asignado"]

        doc_ref = asig["docs"][0] if asig["docs"] else ""

        return ProcessedItem(
            ID_ARTICULO=cod, NOM_ARTICULO=nom, CANTIDAD_SOLICITADA=cant_nc,
            CANTIDAD_REAL_ENCONTRADA=cant_f, PRECIO_UNITARIO=precio_ref,
            MONTO_DESCUENTO_UNITARIO=m_desc_u, PRECIO_NETO_FINAL=round(precio_ref - m_desc_u, 4),
            SUBTOTAL_DESCUENTO=round(m_desc_u * cant_f, 2), PORCENTAJE_APLICADO=porc,
            DOCUMENTOS=asig["docs"], STATUS=status, NRO_DOC=str(reciente['NRO_DOC']), SERIE_DOC=str(reciente['SERIE_DOC']),
            FACTURA_REF=doc_ref,
            DOCUMENTOS_CANTIDAD=asig.get("doc_cantidad", {})
        )

    def _crear_item_error(self, cod, cant, porc) -> ProcessedItem:
        return ProcessedItem(
            cod, "NO ENCONTRADO", cant, 0, 0, 0, 0, 0, porc, [], "ERROR: No en historial"
        )

    def _construir_item_vacio(self, cod, nom, porc, rec) -> ProcessedItem:
        p_ref = round(float(rec['PRECIO_UNID']), 2)
        return ProcessedItem(
            cod, nom, 0, 0, p_ref, 0, p_ref, 0, porc, [], "INFO: Cantidad vacía", str(rec['NRO_DOC']), str(rec['SERIE_DOC'])
        )

    def _convertir_porcentaje(self, p) -> float:
        """
        Convierte un valor de porcentaje (ej. '3%', '1.25', '0.03') a un float decimal.
        """
        if pd.isna(p) or str(p).strip() == "": return 0.0
        s = str(p).replace('%', '').strip()
        try:
            val = float(s)
            # Si el usuario pone 1.25 o 3 (>= 0.5), es el entero del porcentaje
            if val >= 0.5:
                return val / 100
            # Si pone 0.0125 o 0.03 (< 0.5), ya es el decimal
            return val
        except (ValueError, TypeError):
            logger.warning(f"No se pudo convertir el porcentaje: '{p}'. Se usará 0.0")
            return 0.0

    def procesar_lote(
        self,
        requerimientos: pd.DataFrame,
        forzar_cantidad_solicitada: bool = True
    ) -> Tuple[List[ProcessedItem], List[str]]:
        """
        Procesa un lote completo de requerimientos de Notas de Crédito.
        
        Args:
            requerimientos: DataFrame con los ítems a procesar (CODIGO, CANTIDAD_NC, PORCENTAJE_DESC).
            forzar_cantidad_solicitada: Si es True, la cantidad en el reporte será la solicitada,
                                        incluso si no hay suficiente stock en el historial.
                                        Si es False, la cantidad será la máxima encontrada.
        
        Returns:
            Una tupla que contiene:
            - Lista de objetos ProcessedItem con los resultados del procesamiento de cada ítem.
            - Lista ordenada de strings con los números de documento únicos utilizados.
        """
        logger.info(f"Procesando lote de {len(requerimientos)} requerimientos.")

        # Limpiar columnas de requerimientos
        requerimientos.columns = [self._limpiar_col_universal(c) for c in requerimientos.columns]
        
        # Eliminar fila de totales en requerimientos si existe por error al final
        if not requerimientos.empty:
            if requerimientos.iloc[-1].astype(str).str.contains(r'TOTAL|TOTALES', case=False, na=False).any():
                requerimientos = requerimientos.iloc[:-1].reset_index(drop=True)

        # Validar columnas de requerimientos (en uppercase)
        cols_req = ['CODIGO', 'CANTIDAD_NC', 'PORCENTAJE_DESC']
        faltantes = [c for c in cols_req if c not in requerimientos.columns]
        if faltantes:
            raise ValueError(f"Archivo de Requerimientos incompleto. Faltan columnas: {', '.join(faltantes)}")

        resultados = []
        todos_documentos = set()

        for _, fila in requerimientos.iterrows():
            # Validar si la fila tiene datos mínimos
            codigo_raw = str(fila.get('CODIGO', '')).strip()
            if codigo_raw == "" or codigo_raw.lower() == "nan":
                resultados.append(ProcessedItem(
                    ID_ARTICULO="N/A",
                    NOM_ARTICULO="FILA SIN CÓDIGO",
                    CANTIDAD_SOLICITADA=0,
                    CANTIDAD_REAL_ENCONTRADA=0,
                    PRECIO_UNITARIO=0,
                    MONTO_DESCUENTO_UNITARIO=0,
                    PRECIO_NETO_FINAL=0,
                    SUBTOTAL_DESCUENTO=0,
                    PORCENTAJE_APLICADO=0,
                    DOCUMENTOS=[],
                    STATUS="ERROR: Fila vacía o sin código de artículo"
                ))
                continue

            # Limpieza segura de CANTIDAD_NC en requerimientos
            raw_cant = str(fila['CANTIDAD_NC']).upper().replace('O', '0').strip()
            try:
                cant_val = int(float(pd.to_numeric(raw_cant, errors='coerce') or 0))
            except (ValueError, TypeError):
                cant_val = 0
                logger.warning(f"Cantidad inválida para SKU {codigo_raw}: '{raw_cant}'")

            porcentaje_val = self._convertir_porcentaje(fila['PORCENTAJE_DESC'])

            # Alerta informativa por valores en cero, pero permitimos el procesamiento
            warning_prefix = ""
            if cant_val <= 0:
                warning_prefix = "INFO: Cantidad vacía o cero. "
            elif porcentaje_val <= 0:
                warning_prefix = "INFO: Descuento vacío o cero. "
            elif porcentaje_val > 1.0:
                warning_prefix = "INFO: Descuento excede 100%. "
            elif porcentaje_val > 1.0:
                warning_prefix = "INFO: Descuento excede 100%. "
            
            item = self.procesar_articulo(
                codigo=codigo_raw,
                cantidad_nc=cant_val,
                porcentaje_desc=porcentaje_val,
                forzar_cantidad_solicitada=forzar_cantidad_solicitada
            )

            # Si el artículo se encontró pero tiene valores en 0, inyectamos la alerta azul
            if warning_prefix and "ERROR" not in item.STATUS:
                item.STATUS = f"{warning_prefix}{item.STATUS}"

            resultados.append(item)
            
            # Agregar documentos al set global
            for doc in item.DOCUMENTOS:
                todos_documentos.add(doc)

        logger.info(f"Lote finalizado. Items procesados: {len(resultados)}. Documentos hallados: {len(todos_documentos)}")
        return resultados, sorted(list(todos_documentos))

    def obtener_rango_fechas(self) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        """
        Obtiene el rango de fechas (más antigua y más reciente) del historial procesado.
        
        Returns:
            Una tupla con la fecha más antigua y la fecha más reciente (pd.Timestamp), o (None, None) si el historial está vacío.
        """
        if self.historial.empty:
            return None, None
        
        fecha_reciente = self.historial['FECHA_ORIG'].max()
        fecha_antigua = self.historial['FECHA_ORIG'].min()
        
        return fecha_antigua, fecha_reciente

    def obtener_resumen_lineas(self) -> List[Dict]:
        """
        Genera un resumen estadístico de los montos totales por línea de producto (NOM_LINEA).
        Retorna el Top 5 de líneas con sus montos y porcentajes sobre el total.
        
        Returns:
            Una lista de diccionarios, cada uno representando una línea con 'NOM_LINEA', 'SOLES' y 'PORCENTAJE'.
        """
        if self.historial.empty or "NOM_LINEA" not in self.historial.columns:
            return []
        
        # Agrupar por línea y sumar montos
        resumen = self.historial.groupby("NOM_LINEA")["SOLES"].sum().reset_index()
        total_general = resumen["SOLES"].sum()
        
        if total_general == 0: return []
        
        # ✅ Escala Raiz Cuadrada (mejor balance)
        # Aumenta valores pequeños pero no exagera demasiado
        max_val = resumen["SOLES"].abs().max()
        
        # Escala Raiz Cubica, la mas balanceada y natural
        resumen["ESCALA_VISUAL"] = (resumen["SOLES"].abs() ** 0.55) / (max_val ** 0.55)
        
        # ✅ Deteccion de valores negativos
        resumen["ES_NEGATIVO"] = resumen["SOLES"] < 0
        
        # Mantener el porcentaje real para mostrar
        resumen["PORCENTAJE"] = (resumen["SOLES"] / total_general).abs()
        
        return resumen.sort_values("SOLES", ascending=False).head(16).to_dict("records")
