"""
Diccionario centralizado de campos y formatos del HISTORIAL REAL.
Define la estructura estándar de TODOS los campos del archivo fuente.

Estructura del historial:
ANHO, MES, DOC_CLIENTE, COD_CLIENTE, CLIENTE, ID_LOCALIDAD_UBIGEO,
NOM_DEPARTAMENTO, NOM_PROVINCIA, NOM_DISTRITO, COD_LINEA, LINEA,
COD_GRUPO, GRUPO, COD_TIPO, TIPO, COD_FAMILIA, FAMILIA,
ESTADO_LINEA, CODIGO, ARTICULO, COD_VENDEDOR, VENDEDOR,
CANAL DE DISTRIBUCION, COD_SUCURSAL, SUCURSAL, TIPO_DOC, SERIE,
NUMERO, ORDEN_COMPRA, GUIA, FECHA, REFERENCIA, FECHA_REF, MONEDA,
CANTIDAD, SOLES, DOLARES, CONDICION_PAGO, ID_PEDIDO, FECHA_VENC,
DIVISION, FEC_CARGO

⚠️ IMPORTANTE: Este diccionario define el ESTÁNDAR de formato "ID - NOMBRE"
para los campos compuestos en todos los reportes.
"""

import pandas as pd
from typing import Dict, Any, Optional


class DataDictionary:
    """
    Diccionario centralizado de campos y formatos del HISTORIAL REAL.
    Define la estructura estándar de todos los campos usados en reportes
    basado en el archivo fuente de historial de facturación.

    Modo de uso:
        # Instancia única (cache global):
        dd = DataDictionary(df_historial)
        dd.get_precio_sku('001')

        # Uso estático (sin historial, solo formato de nombres):
        DataDictionary.format_composite_field('CLIENTE', '001', 'NOMBRE')
    """

    # Instancia global compartida (se setea al instanciar con DataFrame)
    _instance: Optional["DataDictionary"] = None

    # ═══════════════════════════════════════════════════════════════
    # CAMPOS DEL HISTORIAL REAL (ordenados según el archivo fuente)
    # ═══════════════════════════════════════════════════════════════
    HISTORIAL_FIELDS = {
        # --- Periodo / Tiempo ---
        "ANHO": {
            "type": "int",
            "description": "Año de la transacción",
            "alias": "AÑO",
            "in_report": True,
        },
        "MES": {
            "type": "str",
            "description": "Mes de la transacción (ej: 05-MAYO)",
            "alias": None,
            "in_report": True,
        },
        "FECHA": {
            "type": "datetime",
            "description": "Fecha original del documento",
            "alias": "FECHA",
            "in_report": True,
        },
        "FECHA_REF": {
            "type": "datetime",
            "description": "Fecha de referencia",
            "alias": None,
            "in_report": False,
        },
        "FECHA_VENC": {
            "type": "datetime",
            "description": "Fecha de vencimiento",
            "alias": "FECHA_VENCIMIENTO",
            "in_report": False,
        },
        "FEC_CARGO": {
            "type": "datetime",
            "description": "Fecha de cargo",
            "alias": None,
            "in_report": False,
        },
        # --- Cliente ---
        "DOC_CLIENTE": {
            "type": "str",
            "description": "Número de documento del cliente (RUC/DNI)",
            "alias": "RUC_CLIENTE",
            "in_report": True,
        },
        "COD_CLIENTE": {
            "type": "str",
            "description": "ID interno del cliente",
            "alias": "COD_CLIENTE",
            "in_report": True,
        },
        "CLIENTE": {
            "type": "str",
            "description": "Nombre o razón social del cliente",
            "alias": "CLIENTE",
            "in_report": True,
        },
        # --- Ubicación / Geografía ---
        "ID_LOCALIDAD_UBIGEO": {
            "type": "str",
            "description": "Código de ubicación geográfica",
            "alias": "UBIGEO",
            "in_report": False,
        },
        "NOM_DEPARTAMENTO": {
            "type": "str",
            "description": "Nombre del departamento",
            "alias": "DEPARTAMENTO",
            "in_report": True,
        },
        "NOM_PROVINCIA": {
            "type": "str",
            "description": "Nombre de la provincia",
            "alias": "PROVINCIA",
            "in_report": True,
        },
        "NOM_DISTRITO": {
            "type": "str",
            "description": "Nombre del distrito",
            "alias": "DISTRITO",
            "in_report": True,
        },
        # --- Línea de Negocio ---
        "COD_LINEA": {
            "type": "str",
            "description": "ID de la línea de negocio",
            "alias": "COD_LINEA",
            "in_report": True,
        },
        "LINEA": {
            "type": "str",
            "description": "Nombre de la línea de negocio",
            "alias": "LINEA",
            "in_report": True,
        },
        "ESTADO_LINEA": {
            "type": "str",
            "description": "Estado de la línea (LINEA NUEVA / LINEA TRADICIONAL)",
            "alias": None,
            "in_report": True,
        },
        # --- Jerarquía de Producto ---
        "COD_GRUPO": {
            "type": "str",
            "description": "ID del grupo de producto",
            "alias": "COD_GRUPO",
            "in_report": True,
        },
        "GRUPO": {
            "type": "str",
            "description": "Nombre del grupo de producto",
            "alias": "GRUPO",
            "in_report": True,
        },
        "COD_TIPO": {
            "type": "str",
            "description": "ID del tipo de producto",
            "alias": "COD_TIPO",
            "in_report": True,
        },
        "TIPO": {
            "type": "str",
            "description": "Nombre del tipo de producto",
            "alias": "TIPO",
            "in_report": True,
        },
        "COD_FAMILIA": {
            "type": "str",
            "description": "ID de la familia de producto",
            "alias": "COD_FAMILIA",
            "in_report": True,
        },
        "FAMILIA": {
            "type": "str",
            "description": "Nombre de la familia de producto",
            "alias": "FAMILIA",
            "in_report": True,
        },
        # --- Artículo / SKU ---
        "CODIGO": {
            "type": "str",
            "description": "ID del artículo (SKU)",
            "alias": "SKU, COD_ARTICULO",
            "in_report": True,
        },
        "ARTICULO": {
            "type": "str",
            "description": "Nombre del artículo",
            "alias": "ARTICULO, DESCRIPCION",
            "in_report": True,
        },
        # --- Vendedor ---
        "COD_VENDEDOR": {
            "type": "str",
            "description": "ID del vendedor",
            "alias": "COD_VENDEDOR",
            "in_report": True,
        },
        "VENDEDOR": {
            "type": "str",
            "description": "Nombre del vendedor",
            "alias": "VENDEDOR",
            "in_report": True,
        },
        # --- Canal / Sucursal ---
        "CANAL DE DISTRIBUCION": {
            "type": "str",
            "description": "Canal de distribución",
            "alias": "CANAL, CANAL_DIST",
            "in_report": True,
        },
        "COD_SUCURSAL": {
            "type": "str",
            "description": "Código de la sucursal",
            "alias": "ID_SUCURSAL",
            "in_report": True,
        },
        "SUCURSAL": {
            "type": "str",
            "description": "Nombre de la sucursal",
            "alias": "SUCURSAL",
            "in_report": True,
        },
        # --- Documento ---
        "TIPO_DOC": {
            "type": "str",
            "description": "Tipo de documento (F=Factura, B=Boleta, NC=Nota Crédito)",
            "alias": "TIPO_DOC, TIPODOC",
            "in_report": True,
        },
        "SERIE": {
            "type": "str",
            "description": "Serie del documento",
            "alias": "SERIE",
            "in_report": True,
        },
        "NUMERO": {
            "type": "str",
            "description": "Número del documento",
            "alias": "NUM_DOC, NUMERO",
            "in_report": True,
        },
        "REFERENCIA": {
            "type": "str",
            "description": "Referencia del documento",
            "alias": None,
            "in_report": False,
        },
        # --- Logística ---
        "ORDEN_COMPRA": {
            "type": "str",
            "description": "Orden de compra del cliente",
            "alias": "OC, ORDEN_COMPRA",
            "in_report": True,
        },
        "GUIA": {
            "type": "str",
            "description": "ID de la guía de remisión",
            "alias": "GUIA, ID_GUIA_REMISION",
            "in_report": True,
        },
        # --- Financiero / Moneda ---
        "MONEDA": {
            "type": "str",
            "description": "Moneda de la transacción (SOL/DOLAR)",
            "alias": None,
            "in_report": True,
        },
        "CANTIDAD": {
            "type": "float",
            "description": "Cantidad de unidades",
            "alias": "QTY, UNIDADES",
            "in_report": True,
        },
        "SOLES": {
            "type": "float",
            "description": "Monto en soles",
            "alias": "MONTO_SOLES, TOTAL_SOLES",
            "in_report": True,
        },
        "DOLARES": {
            "type": "float",
            "description": "Monto en dólares",
            "alias": "MONTO_DOLARES, TOTAL_DOLARES",
            "in_report": True,
        },
        "CONDICION_PAGO": {
            "type": "str",
            "description": "Condición de pago (ej: FACTURA 60 DIAS)",
            "alias": "CONDICION_PAGO, PLAZO",
            "in_report": True,
        },
        "ID_PEDIDO": {
            "type": "str",
            "description": "ID del pedido asociado",
            "alias": "PEDIDO, ORDER_ID",
            "in_report": True,
        },
        "DIVISION": {
            "type": "str",
            "description": "División comercial",
            "alias": None,
            "in_report": True,
        },
        "PRECIO_UNITARIO": {
            "type": "float",
            "description": "Precio unitario calculado (SOLES / CANTIDAD)",
            "alias": "PRECIO_UNI, PRECIO UNITARIO",
            "in_report": True,
        },
    }

    # Campos compuestos (formato "ID - NOMBRE")
    COMPOSITE_FIELDS = {
        "SKU": {
            "id_field": "CODIGO",
            "name_field": "ARTICULO",
            "display_name": "SKU",
            "format": "ID - NOMBRE",
            "required": True,
        },
        "LÍNEA": {
            "id_field": "COD_LINEA",
            "name_field": "LINEA",
            "display_name": "Línea",
            "format": "ID - NOMBRE",
            "required": True,
        },
        "CLIENTE": {
            "id_field": "COD_CLIENTE",
            "name_field": "CLIENTE",
            "display_name": "Cliente",
            "format": "ID - NOMBRE",
            "required": True,
        },
        "VENDEDOR": {
            "id_field": "COD_VENDEDOR",
            "name_field": "VENDEDOR",
            "display_name": "Vendedor",
            "format": "ID - NOMBRE",
            "required": False,
        },
        "SUCURSAL": {
            "id_field": "COD_SUCURSAL",
            "name_field": "SUCURSAL",
            "display_name": "Sucursal",
            "format": "ID - NOMBRE",
            "required": False,
        },
    }

    # Campos de documento
    DOCUMENT_FIELDS = {
        "FACTURA": {
            "format": "TIPO + SERIE - NUMERO",
            "fields": ["TIPO_DOC", "SERIE", "NUMERO"],
            "example": "F012-0457996",
            "required": True,
        },
        "PEDIDO": {
            "format": "ID_PEDIDO",
            "fields": ["ID_PEDIDO"],
            "example": "KG935",
            "required": False,
        },
        "GUIA": {
            "format": "GUIA",
            "fields": ["GUIA"],
            "example": "73848",
            "required": False,
        },
        "ORDEN_COMPRA": {
            "format": "ORDEN_COMPRA",
            "fields": ["ORDEN_COMPRA"],
            "example": "12345",
            "required": False,
        },
    }

    # Campos de lista (plural) - mapeo a campos del historial
    LIST_FIELDS = {
        "CLIENTES": {
            "singular": "CLIENTE",
            "id_field": "COD_CLIENTE",
            "name_field": "CLIENTE",
            "description": "Lista de clientes",
        },
        "FACTURAS": {
            "singular": "FACTURA",
            "fields": ["TIPO_DOC", "SERIE", "NUMERO"],
            "description": "Lista de facturas",
        },
        "LINEAS": {
            "singular": "LÍNEA",
            "id_field": "COD_LINEA",
            "name_field": "LINEA",
            "description": "Lista de líneas de negocio",
        },
        "VENDEDORES": {
            "singular": "VENDEDOR",
            "id_field": "COD_VENDEDOR",
            "name_field": "VENDEDOR",
            "description": "Lista de vendedores",
        },
        "ARTICULOS": {
            "singular": "SKU",
            "id_field": "CODIGO",
            "name_field": "ARTICULO",
            "description": "Lista de artículos/SKUs",
        },
    }

    # Categorías de campos para agrupación
    FIELD_CATEGORIES = {
        "TIEMPO": ["ANHO", "MES", "FECHA", "FECHA_REF", "FECHA_VENC", "FEC_CARGO"],
        "CLIENTE": ["DOC_CLIENTE", "COD_CLIENTE", "CLIENTE"],
        "UBICACION": ["ID_LOCALIDAD_UBIGEO", "NOM_DEPARTAMENTO", "NOM_PROVINCIA", "NOM_DISTRITO"],
        "PRODUCTO": [
            "CODIGO",
            "ARTICULO",
            "COD_LINEA",
            "LINEA",
            "ESTADO_LINEA",
            "COD_GRUPO",
            "GRUPO",
            "COD_TIPO",
            "TIPO",
            "COD_FAMILIA",
            "FAMILIA",
        ],
        "COMERCIAL": [
            "COD_VENDEDOR",
            "VENDEDOR",
            "CANAL DE DISTRIBUCION",
            "COD_SUCURSAL",
            "SUCURSAL",
            "DIVISION",
        ],
        "DOCUMENTO": [
            "TIPO_DOC",
            "SERIE",
            "NUMERO",
            "REFERENCIA",
            "ORDEN_COMPRA",
            "GUIA",
            "ID_PEDIDO",
        ],
        "FINANCIERO": [
            "MONEDA",
            "CANTIDAD",
            "SOLES",
            "DOLARES",
            "CONDICION_PAGO",
            "PRECIO_UNITARIO",
        ],
    }

    # Valores a filtrar
    FILTER_VALUES = {
        "SIN ASIGNAR": True,
        "": True,
        "nan": True,
        "None": True,
    }

    # ═══════════════════════════════════════════════════════════════
    # CACHE DINÁMICO (construido al instanciar con un DataFrame)
    # ═══════════════════════════════════════════════════════════════

    def __init__(self, df: Optional[pd.DataFrame] = None):
        """
        Inicializa el diccionario de datos. Si se proporciona un DataFrame
        del historial, construye los caches de datos precomputados.
        La última instancia creada se registra como global para que los
        métodos estáticos puedan acceder al cache.

        Args:
            df: DataFrame con el historial procesado (opcional)
        """
        self._df = df
        self._cache: Dict[str, Any] = {}
        if df is not None and not df.empty:
            self._build_caches()
            DataDictionary._instance = self

    def _build_caches(self):
        """Construye todos los caches de datos precomputados desde el DataFrame."""
        df = self._df

        # 1. Cache de campos reales detectados en el archivo
        self._cache["columnas_reales"] = list(df.columns)
        self._cache["total_filas"] = len(df)
        self._cache["rango_fechas"] = (
            df["FECHA"].min() if "FECHA" in df.columns else None,
            df["FECHA"].max() if "FECHA" in df.columns else None,
        )

        # 2. Cache de precios por SKU: Referencia (Último) vs Promedio Ponderado
        if all(c in df.columns for c in ["CODIGO", "PRECIO_UNITARIO", "SOLES", "CANTIDAD"]):
            # Precio de Referencia: El primero encontrado (asumiendo orden descendente es el más reciente)
            self._cache["precio_ref_sku"] = (
                df.groupby("CODIGO")["PRECIO_UNITARIO"].first().to_dict()
            )

            # Precio Promedio Ponderado: Sum(Soles) / Sum(Cantidad)
            # Evitamos división por cero con un pequeño filtro
            agrupado = df.groupby("CODIGO").agg({"SOLES": "sum", "CANTIDAD": "sum"})
            # Solo calculamos donde la cantidad es > 0
            mask = agrupado["CANTIDAD"] > 0
            agrupado.loc[mask, "PRECIO_PROM"] = agrupado["SOLES"] / agrupado["CANTIDAD"]
            self._cache["precio_avg_sku"] = agrupado["PRECIO_PROM"].fillna(0).to_dict()

            # 2.1 Precio Promedio Ponderado por [CLIENTE + SKU]
            # Útil para CRM: ¿A cuánto le vendo REALMENTE a este cliente este producto?
            if "COD_CLIENTE" in df.columns:
                agrupado_cli = df.groupby(["COD_CLIENTE", "CODIGO"]).agg(
                    {"SOLES": "sum", "CANTIDAD": "sum"}
                )
                mask_cli = agrupado_cli["CANTIDAD"] > 0
                agrupado_cli.loc[mask_cli, "PRECIO_PROM"] = (
                    agrupado_cli["SOLES"] / agrupado_cli["CANTIDAD"]
                )
                # Guardamos como dict de tuplas {(id_cliente, id_sku): precio}
                self._cache["precio_avg_cliente_sku"] = (
                    agrupado_cli["PRECIO_PROM"].fillna(0).to_dict()
                )

        # 3. Cache de totales por documento (factura)
        if all(c in df.columns for c in ["TIPO_DOC", "SERIE", "NUMERO", "SOLES"]):
            from src.core.utils import format_doc_id

            df_docs = df.copy()
            df_docs["DOC_ID"] = df_docs.apply(
                lambda x: format_doc_id(x["TIPO_DOC"], x["SERIE"], x["NUMERO"]), axis=1
            )
            self._cache["total_documento"] = df_docs.groupby("DOC_ID")["SOLES"].sum().to_dict()
            self._cache["docs_por_sku"] = (
                df_docs.groupby("CODIGO")["DOC_ID"].apply(set).to_dict()
                if "CODIGO" in df_docs.columns
                else {}
            )

        # 4. Cache de totales por cliente
        if all(c in df.columns for c in ["COD_CLIENTE", "SOLES"]):
            self._cache["total_cliente"] = df.groupby("COD_CLIENTE")["SOLES"].sum().to_dict()
            if "CLIENTE" in df.columns:
                self._cache["nombre_cliente"] = (
                    df.groupby("COD_CLIENTE")["CLIENTE"].first().to_dict()
                )

        # 5. Cache de totales por vendedor
        if all(c in df.columns for c in ["COD_VENDEDOR", "SOLES"]):
            self._cache["total_vendedor"] = df.groupby("COD_VENDEDOR")["SOLES"].sum().to_dict()
            if "VENDEDOR" in df.columns:
                self._cache["nombre_vendedor"] = (
                    df.groupby("COD_VENDEDOR")["VENDEDOR"].first().to_dict()
                )

        # 6. Cache de totales por línea de negocio
        if all(c in df.columns for c in ["COD_LINEA", "SOLES"]):
            self._cache["total_linea"] = df.groupby("COD_LINEA")["SOLES"].sum().to_dict()
            if "LINEA" in df.columns:
                self._cache["nombre_linea"] = df.groupby("COD_LINEA")["LINEA"].first().to_dict()

        # 7. Cache de totales por período (mes)
        if "SOLES" in df.columns:
            periodo_col = None
            for col in ["PERIODO_TEND", "PERIODO_MES", "MES"]:
                if col in df.columns:
                    periodo_col = col
                    break
            if periodo_col:
                self._cache["total_periodo"] = df.groupby(periodo_col)["SOLES"].sum().to_dict()

        # 8. Cache de dominios válidos (para validación y filtros)
        for col, cache_key in [
            ("COD_CLIENTE", "dominio_clientes"),
            ("COD_VENDEDOR", "dominio_vendedores"),
            ("CODIGO", "dominio_skus"),
            ("COD_LINEA", "dominio_lineas"),
            ("CANAL DE DISTRIBUCION", "dominio_canales"),
            ("COD_SUCURSAL", "dominio_sucursales"),
            ("MONEDA", "dominio_monedas"),
        ]:
            if col in df.columns:
                self._cache[cache_key] = set(df[col].dropna().unique())

        # 9. Cache de cantidad de SKUs por línea
        if all(c in df.columns for c in ["COD_LINEA", "CODIGO"]):
            self._cache["skus_por_linea"] = df.groupby("COD_LINEA")["CODIGO"].nunique().to_dict()

    # ── Métodos de acceso a cache ──

    def get_precio_referencia(self, sku: str) -> float:
        """Retorna el último precio unitario (para sustento de NC)."""
        cache = self._cache.get("precio_ref_sku", {})
        return cache.get(str(sku), 0.0)

    def get_precio_promedio(self, sku: str) -> float:
        """Retorna el precio promedio ponderado (para análisis CRM/Pareto)."""
        cache = self._cache.get("precio_avg_sku", {})
        return cache.get(str(sku), 0.0)

    def get_precio_promedio_cliente(self, cliente_id: str, sku_id: str) -> float:
        """Retorna el precio promedio ponderado que paga un cliente específico por un SKU."""
        cache = self._cache.get("precio_avg_cliente_sku", {})
        # Buscamos por la clave compuesta (tupla)
        return cache.get((str(cliente_id), str(sku_id)), 0.0)

    def get_total_documento(self, doc_id: str) -> float:
        """Retorna el monto total en soles para una factura específica."""
        cache = self._cache.get("total_documento", {})
        return cache.get(str(doc_id), 0.0)

    def get_monto_factura(self, tpo, serie, nro) -> float:
        """Busca el monto de factura usando los componentes crudos."""
        from src.core.utils import format_doc_id

        return self.get_total_documento(format_doc_id(tpo, serie, nro))

    def get_total_cliente(self, cliente_id: str) -> float:
        """Retorna el total en soles cacheado para un cliente."""
        cache = self._cache.get("total_cliente", {})
        return cache.get(str(cliente_id), 0.0)

    def get_nombre_cliente(self, cliente_id: str) -> str:
        """Retorna el nombre cacheado para un ID de cliente."""
        cache = self._cache.get("nombre_cliente", {})
        return cache.get(str(cliente_id), "")

    def get_total_vendedor(self, vendedor_id: str) -> float:
        """Retorna el total en soles cacheado para un vendedor."""
        cache = self._cache.get("total_vendedor", {})
        return cache.get(str(vendedor_id), 0.0)

    def get_nombre_vendedor(self, vendedor_id: str) -> str:
        """Retorna el nombre cacheado para un ID de vendedor."""
        cache = self._cache.get("nombre_vendedor", {})
        return cache.get(str(vendedor_id), "")

    def get_total_linea(self, linea_id: str) -> float:
        """Retorna el total en soles cacheado para una línea."""
        cache = self._cache.get("total_linea", {})
        return cache.get(str(linea_id), 0.0)

    def get_dominio(self, campo: str) -> set:
        """Retorna el conjunto de valores únicos para un campo."""
        key = f"dominio_{campo.lower()}"
        return self._cache.get(key, set())

    def get_cache(self, key: str, default=None):
        """Acceso genérico al cache interno."""
        return self._cache.get(key, default)

    @property
    def campos_disponibles(self) -> list:
        """Retorna la lista de columnas reales del historial."""
        return self._cache.get("columnas_reales", [])

    @property
    def total_filas(self) -> int:
        """Retorna el total de filas del historial."""
        return self._cache.get("total_filas", 0)

    @property
    def rango_fechas(self):
        """Retorna tupla (fecha_min, fecha_max) del historial."""
        return self._cache.get("rango_fechas", (None, None))

    # ── Acceso global al cache (para uso sin referencia directa) ──

    @classmethod
    def get_instance(cls) -> Optional["DataDictionary"]:
        """Retorna la instancia global (la última creada con DataFrame)."""
        return cls._instance

    @classmethod
    def get_precio(cls, sku: str) -> float:
        """Acceso estático al precio cacheado por SKU."""
        inst = cls._instance
        return inst.get_precio_sku(sku) if inst else 0.0

    @classmethod
    def get_cliente_nombre(cls, cliente_id: str) -> str:
        inst = cls._instance
        return inst.get_nombre_cliente(cliente_id) if inst else ""

    @classmethod
    def get_vendedor_nombre(cls, vendedor_id: str) -> str:
        inst = cls._instance
        return inst.get_nombre_vendedor(vendedor_id) if inst else ""

    @classmethod
    def get_linea_nombre(cls, linea_id: str) -> str:
        inst = cls._instance
        return inst.get_nombre_linea(linea_id) if inst else ""

    @classmethod
    def get_dominio_valores(cls, campo: str) -> set:
        """Retorna valores únicos de un campo desde el cache global."""
        inst = cls._instance
        if inst:
            key = f"dominio_{campo.lower().replace(' ', '_')}"
            return inst._cache.get(key, set())
        return set()

    # ═══════════════════════════════════════════════════════════════
    # MÉTODOS ESTÁTICOS (compatibles con uso sin instancia)
    # ═══════════════════════════════════════════════════════════════

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
            return str(name_val or id_val or "")

        field_def = DataDictionary.COMPOSITE_FIELDS[field_name]

        if field_def["format"] == "ID - NOMBRE":
            cid = str(id_val).strip() if id_val else ""
            cnm = str(name_val).strip() if name_val else ""

            if cid and cnm:
                return f"{cid} - {cnm}"
            return cnm or cid

        return str(name_val or id_val or "")

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
            return DataDictionary.COMPOSITE_FIELDS[field_name]["display_name"]

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
            return DataDictionary.COMPOSITE_FIELDS[field_name]["required"]

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
            return (field_def["id_field"], field_def["name_field"])

        return (None, None)

    @staticmethod
    def get_id_name_pairs(
        df: "pd.DataFrame",
        *,
        id_field: str,
        name_field: str,
        sort_by_name: bool = True,
        return_tuples: bool = True,
    ) -> list:
        """Extrae pares (id, nombre) únicos de un DataFrame del ERP.

        Es la pieza compartida por constructores de dropdowns Flet, scanners de
        ERP, y cualquier consumidor que necesite un mapa "ID -> NOMBRE" deduplicado.

        Reglas:
            - Si solo existe ``name_field`` en el df: cada par es (None, name).
            - Si solo existe ``id_field``: cada par es (id, id).
            - Si no existe ninguno: devuelve [].
            - Filtra espacios/NaN de id_field y name_field.
            - Orden por nombre (case-insensitive) si ``sort_by_name=True``.

        Args:
            df: DataFrame del historial o lista normalizada.
            id_field: columna del id (e.g. ``COD_CLIENTE``, ``CODIGO``).
            name_field: columna del nombre (e.g. ``CLIENTE``, ``ARTICULO``).
            sort_by_name: si True ordena por name_field case-insensitive.
            return_tuples: si True devuelve ``[(id, name), ...]``; si False
                devuelve ``[{"id": id, "name": name}, ...]``.

        Returns:
            Lista de tuplas (id, name) o dicts según ``return_tuples``.
        """
        if df is None or df.empty:
            return []

        has_id = id_field in df.columns
        has_name = name_field in df.columns

        if not has_id and not has_name:
            return []

        if has_id and has_name:
            sub = df[[id_field, name_field]].copy()
            sub[id_field] = sub[id_field].astype(str).str.strip()
            sub[name_field] = sub[name_field].astype(str).str.strip()
            # Filtro robusto: '' o 'nan' o 'None' en cualquiera -> excluir
            invalid_id = sub[id_field].str.lower().isin({"", "nan", "none", "null", "<na>"})
            invalid_nm = sub[name_field].str.lower().isin({"", "nan", "none", "null", "<na>"})
            mask = ~(invalid_id | invalid_nm)
            sub = sub[mask].drop_duplicates()
            if sub.empty:
                return []
            if sort_by_name:
                sub = sub.sort_values(
                    by=name_field,
                    key=lambda s: s.str.lower(),
                    kind="mergesort",
                )
            if return_tuples:
                return list(zip(sub[id_field].tolist(), sub[name_field].tolist()))
            return [{"id": i, "name": n} for i, n in zip(sub[id_field], sub[name_field])]

        # Solo name_field
        if has_name:
            sub = df[[name_field]].drop_duplicates()
            sub[name_field] = sub[name_field].astype(str).str.strip()
            sub = sub[sub[name_field].ne("") & sub[name_field].ne("nan")]
            if sub.empty:
                return []
            if sort_by_name:
                sub = sub.sort_values(
                    by=name_field,
                    key=lambda s: s.str.lower(),
                    kind="mergesort",
                )
            if return_tuples:
                return [(None, n) for n in sub[name_field].tolist()]
            return [{"id": None, "name": n} for n in sub[name_field].tolist()]

        # Solo id_field
        sub = df[[id_field]].drop_duplicates()
        sub[id_field] = sub[id_field].astype(str).str.strip()
        sub = sub[sub[id_field].ne("") & sub[id_field].ne("nan")]
        if sub.empty:
            return []
        if sort_by_name:
            sub["_k"] = sub[id_field]
            sub = sub.sort_values(by="_k", key=lambda s: s.str.lower())
        if return_tuples:
            return [(i, i) for i in sub[id_field].tolist()]
        return [{"id": i, "name": i} for i in sub[id_field].tolist()]

    @classmethod
    def unique_clients(cls, df: "pd.DataFrame", **kwargs) -> list:
        """Atajo: pares unicos de (COD_CLIENTE, CLIENTE)."""
        return cls.get_id_name_pairs(df, id_field="COD_CLIENTE", name_field="CLIENTE", **kwargs)

    @classmethod
    def unique_vendors(cls, df: "pd.DataFrame", **kwargs) -> list:
        """Atajo: pares unicos de (COD_VENDEDOR, VENDEDOR)."""
        return cls.get_id_name_pairs(df, id_field="COD_VENDEDOR", name_field="VENDEDOR", **kwargs)

    @classmethod
    def unique_skus(cls, df: "pd.DataFrame", **kwargs) -> list:
        """Atajo: pares unicos de (CODIGO, ARTICULO)."""
        return cls.get_id_name_pairs(df, id_field="CODIGO", name_field="ARTICULO", **kwargs)

    @classmethod
    def unique_branches(cls, df: "pd.DataFrame", **kwargs) -> list:
        """Atajo: pares unicos de (COD_SUCURSAL, SUCURSAL)."""
        return cls.get_id_name_pairs(df, id_field="COD_SUCURSAL", name_field="SUCURSAL", **kwargs)

    @classmethod
    def validate_composite_field(
        cls, field_name: str, id_val: str, name_val: str
    ) -> Dict[str, Any]:
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
            "valid": True,
            "errors": [],
            "warnings": [],
        }

        if field_name not in DataDictionary.COMPOSITE_FIELDS:
            result["valid"] = False
            result["errors"].append(f"Campo '{field_name}' no definido en el diccionario")
            return result

        field_def = DataDictionary.COMPOSITE_FIELDS[field_name]

        # Validar campos obligatorios
        if field_def["required"]:
            if not id_val or str(id_val).strip() == "":
                result["valid"] = False
                result["errors"].append(f"ID obligatorio para campo '{field_name}'")

            if not name_val or str(name_val).strip() == "":
                result["warnings"].append(f"Nombre vacío para campo '{field_name}'")

        return result
