from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime
from src.core.utils import format_id_name, format_doc_id, format_fecha
from src.core.data_dictionary import DataDictionary


class ReporteConsolidado:
    """Lógica del reporte consolidado - separada del UI."""

    @staticmethod
    def generar_consolidado(
        historial: pd.DataFrame,
        agrupacion: str = "ID_ARTICULO",
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None,
        periodo: str = "MES"
    ) -> Dict[str, List[Dict]]:
        """
        Genera datos consolidados por vendedor.
        Orden: Ascendente (más antiguo → más reciente)
        
        Tipos de agrupacion:
        - "ID_ARTICULO" o "ID_ARTICULO": Por SKU (una fila por SKU)
        - "NOM_LINEA": Por línea (una fila por SKU dentro de línea)
        - "ID_CLIENTE": Por cliente (una fila por SKU dentro de cliente)
        - "PERIODO_MES": Por mes (una fila por SKU dentro de mes)
        - "FACTURA": Por documento completo (una fila por cada SKU de cada factura)
        
        periodo: "MES" (solo para agrupaciones de período)
        
        Retorna: Dict[vendedor_id] = List[Dict] con campos según tipo de agrupacion
        """
        df = historial.copy(); 
        
        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
# Ordenar por fecha ascendente (más antiguo → más reciente)
        df = df.sort_values(by=['FECHA_ORIG', 'ID_ARTICULO'], ascending=[True, True])
        
        df = ReporteConsolidado._agregar_columna_periodo(df, periodo)
        
        resultado_por_vendedor = {}
        
        for vendedor_id, df_vendedor in df.groupby("ID_VENDEDOR"):
            items_por_agrupacion = []
            
            if agrupacion == "ID_CLIENTE":
                # Por CLIENTE: Unir todo lo vendido al cliente por Línea y SKU
                for (id_c, nom_c, id_l, nom_l, sku), df_grupo in df_vendedor.groupby(
                    ["ID_CLIENTE", "NOM_CLIENTE", "ID_LINEA", "NOM_LINEA", "ID_ARTICULO"]
                ):
                    item = ReporteConsolidado._procesar_item_agrupado(
                        df_grupo, nom_c, nom_l, sku, "CLIENTE", id_cliente=id_c, id_linea=id_l
                    )
                    items_por_agrupacion.append(item)
            elif agrupacion == "NOM_LINEA":
                # Por LÍNEA: Unir por Línea, SKU y Cliente
                for (id_l, nom_l, sku, nom_c), df_grupo in df_vendedor.groupby(
                    ["ID_LINEA", "NOM_LINEA", "ID_ARTICULO", "NOM_CLIENTE"]
                ):
                    item = ReporteConsolidado._procesar_item_agrupado(
                        df_grupo, nom_c, nom_l, sku, "LINEA", id_linea=id_l
                    )
                    items_por_agrupacion.append(item)
            elif agrupacion == "PERIODO_MES":
                # Por PERIODO: Unir por Mes, SKU, Línea y Cliente
                for (periodo_val, sku, id_l, nom_l, nom_c), df_grupo in df_vendedor.groupby(
                    ["PERIODO", "ID_ARTICULO", "ID_LINEA", "NOM_LINEA", "NOM_CLIENTE"]
                ):
                    item = ReporteConsolidado._procesar_item_periodo(
                        df_grupo, periodo_val, sku, "MES"
                    )
                    items_por_agrupacion.append(item)
            elif agrupacion == "FACTURA":
                for doc_key, df_doc in df_vendedor.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"]):
                    items_factura = ReporteConsolidado._procesar_item_factura(df_doc, doc_key)
                    items_por_agrupacion.extend(items_factura)
            else:
                # Por SKU: Unir por SKU, Línea y Cliente
                for (sku, id_l, nom_l, nom_c), df_grupo in df_vendedor.groupby(
                    ["ID_ARTICULO", "ID_LINEA", "NOM_LINEA", "NOM_CLIENTE"]
                ):
                    item = ReporteConsolidado._procesar_item_agrupado(
                        df_grupo, nom_c, nom_l, sku, "SKU"
                    )
                    items_por_agrupacion.append(item)
            
            # Estandarizar campo FECHA y ordenar por fecha
            if items_por_agrupacion:
                for item in items_por_agrupacion:
                    if 'FECHA_MIN' in item:
                        item['FECHA'] = item['FECHA_MIN']
                    elif 'PERIODO' in item:
                        item['FECHA'] = item['PERIODO']
                    elif 'FECHA' in item:
                        item['FECHA'] = item['FECHA']
                    else:
                        item['FECHA'] = ''
            
            # Ordenar segun tipo de agrupacion
            if agrupacion == "ID_CLIENTE":
                items_por_agrupacion.sort(key=lambda x: (x.get('ID_CLIENTE', '')))
            elif agrupacion == "NOM_LINEA":
                items_por_agrupacion.sort(key=lambda x: (x.get('ID_LINEA', '')))
            elif agrupacion == "ID_ARTICULO":
                items_por_agrupacion.sort(key=lambda x: (x.get('SKU', '').split(' - ')[0] if x.get('SKU') else ''))
            elif agrupacion == "PERIODO_MES":
                items_por_agrupacion.sort(key=lambda x: x.get('FECHA') or '')
            elif agrupacion == "FACTURA":
                items_por_agrupacion.sort(key=lambda x: x.get('FECHA') or '')
            else:
                items_por_agrupacion.sort(key=lambda x: x.get('SKU', '').split(' - ')[0] if x.get('SKU') else '')
            
            resultado_por_vendedor[vendedor_id] = items_por_agrupacion
        
        return resultado_por_vendedor

    @staticmethod
    def _agregar_columna_periodo(df: pd.DataFrame, periodo: str = "MES") -> pd.DataFrame:
        """Agrega columna PERIODO calculada a partir de FECHA_ORIG."""
        if 'FECHA_ORIG' not in df.columns:
            df['PERIODO'] = 'Sin Fecha'
            return df
        
        df = df.copy()
        
        if periodo == "TRIMESTRE":
            df['PERIODO'] = df['FECHA_ORIG'].dt.to_period('Q').astype(str)
        else:
            df['PERIODO'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        
        return df

    @staticmethod
    def _procesar_item_periodo(df_grupo, periodo: str, sku: str, tipo_periodo: str) -> Dict:
        """Procesa una fila para agrupación por período."""
        cant_total = df_grupo['CANTIDAD'].sum()
        soles_total = df_grupo['SOLES'].sum()
        
        facturas_ordenadas = []
        precios_lista = []
        pedidos_lista = []
        for doc_key, df_doc in df_grupo.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"], sort=False):
            fmt_doc = format_doc_id(doc_key[0], doc_key[1], doc_key[2])
            facturas_ordenadas.append(fmt_doc)
            p_ref = df_doc['PRECIO_UNID'].iloc[0] if 'PRECIO_UNID' in df_doc.columns else 0
            precios_lista.append(f"{float(p_ref):.2f}")
            if 'ID_PEDIDO' in df_doc.columns:
                pedidos_lista.append(str(df_doc['ID_PEDIDO'].iloc[0]))
        
        if len(set(precios_lista)) <= 1:
            precios_str = precios_lista[0] if precios_lista else ""
        else:
            precios_str = ", ".join(precios_lista)
        
        pedidos_str = ", ".join(pedidos_lista) if pedidos_lista else ""
        
        fecha_min = df_grupo['FECHA_ORIG'].min()
        fecha_max = df_grupo['FECHA_ORIG'].max()
        fecha_max_str = format_fecha(fecha_max)
        
        nom_linea = df_grupo['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_grupo.columns else ""
        id_linea = df_grupo['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_grupo.columns else ""
        linea_display = format_id_name(id_linea, nom_linea, field_name='LÍNEA')
        
        return {
            'PERIODO': periodo,
            'TIPO_PERIODO': tipo_periodo,
            'SKU': format_id_name(sku, df_grupo['NOM_ARTICULO'].iloc[0], field_name='SKU'),
            'LÍNEA': linea_display,
            'CLIENTE': format_id_name(df_grupo['ID_CLIENTE'].iloc[0], df_grupo['NOM_CLIENTE'].iloc[0], field_name='CLIENTE'),
            'CANTIDAD': cant_total,
            'MONTO': soles_total,
            'FACTURAS': ", ".join(facturas_ordenadas),
            'PRECIOS': precios_str,
            'PEDIDOS': pedidos_str,
            'FECHA_ULT': fecha_max_str,
            'FECHA_MIN': format_fecha(fecha_min),
            'FECHA_MAX': fecha_max_str,
        }

    @staticmethod
    def _procesar_item_factura(df_doc, doc_key) -> List[Dict]:
        """Procesa una factura pero retorna una fila por cada SKU."""
        tpo, serie, nro = doc_key
        
# Formatear número de factura
        num_factura = format_doc_id(tpo, serie, nro)
        
        # Obtener fecha de la factura
        fecha_factura = df_doc['FECHA_ORIG'].iloc[0] if 'FECHA_ORIG' in df_doc.columns else None
        fecha_str = format_fecha(fecha_factura) if fecha_factura else ""
        
        # Formatear Cliente principal de la factura
        id_cliente = df_doc['ID_CLIENTE'].iloc[0] if 'ID_CLIENTE' in df_doc.columns else ""
        nom_cliente = df_doc['NOM_CLIENTE'].iloc[0] if 'NOM_CLIENTE' in df_doc.columns else ""
        cliente_display = format_id_name(id_cliente, nom_cliente, field_name='CLIENTE')
        
        # Obtener ID_PEDIDO
        id_pedido = str(df_doc['ID_PEDIDO'].iloc[0]) if 'ID_PEDIDO' in df_doc.columns else ""
        
        # Una fila por cada SKU
        items = []
        for sku, df_sku in df_doc.groupby("ID_ARTICULO"):
            nom_art = df_sku['NOM_ARTICULO'].iloc[0] if 'NOM_ARTICULO' in df_sku.columns else sku
            sku_display = format_id_name(sku, nom_art, field_name='SKU')
            cant_sku = df_sku['CANTIDAD'].sum()
            soles_sku = df_sku['SOLES'].sum()
            pu = soles_sku / cant_sku if cant_sku > 0 else 0
            
            nom_linea = df_sku['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_sku.columns else ""
            id_linea = df_sku['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_sku.columns else ""
            linea_display = format_id_name(id_linea, nom_linea, field_name='LÍNEA')
            
            items.append({
                'FACTURA': num_factura,
                'FECHA': fecha_str,
                'CLIENTE': cliente_display,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CANTIDAD': cant_sku,
                'PRECIO': pu,
                'MONTO': soles_sku,
                'PEDIDO': id_pedido,
            })
        
        return items

    @staticmethod
    def _formatear_factura(fila) -> str:
        """Usa la utilidad centralizada para formatear facturas."""
        return format_doc_id(fila.get('TPO_DOC'), fila.get('SERIE_DOC'), fila.get('NRO_DOC'))

    @staticmethod
    def _procesar_item_compacto(df_sku, cliente: str, sku: str) -> Dict:
        """Procesa una fila en modo compacto (cliente + SKU)"""
        cant_total = df_sku['CANTIDAD'].sum()
        soles_total = df_sku['SOLES'].sum()
        
        facturas_ordenadas = []
        for _, fila in df_sku.iterrows():
            doc = ReporteConsolidado._formatear_factura(fila)
            if doc not in facturas_ordenadas:
                facturas_ordenadas.append(doc)
        
        precios_unicos = df_sku['PRECIO_UNID'].unique()
        precios_str = ", ".join([f"{p:.2f}" for p in precios_unicos])
        
        fecha_min = df_sku['FECHA_ORIG'].min()
        fecha_max = df_sku['FECHA_ORIG'].max()
        fecha_max_str = format_fecha(fecha_max)
        
        return {
            'CLIENTE': cliente,
            'LÍNEA': format_id_name(df_sku['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_sku.columns else '', 
                                  df_sku['NOM_LINEA'].iloc[0], field_name='LÍNEA'),
            'SKU': format_id_name(sku, df_sku['NOM_ARTICULO'].iloc[0], field_name='SKU'),
            'CANTIDAD': cant_total,
            'MONTO': soles_total,
            'FACTURAS': ", ".join(facturas_ordenadas),
            'PRECIOS': precios_str,
            'FECHA_ULT': fecha_max_str,
            'FECHA_MIN': format_fecha(fecha_min),
        }

    @staticmethod
    def _procesar_item_agrupado(df_grupo, cliente: str, linea: str, sku: str, 
                                tipo_agrupacion: str, articulo: str = "",
                                id_cliente: str = "", id_linea: str = "") -> Dict:
        """
        Procesa una fila según el tipo de agrupación.
        
        Todos los IDs se presentan como "ID - NOMBRE" para consistencia visual.
        
        tipos:
        - CLIENTE: cliente + línea + SKU
        - LINEA: línea + SKU  
        - SKU: SKU + artículo
        - ARTICULO: artículo
        """
        cant_total = df_grupo['CANTIDAD'].sum()
        soles_total = df_grupo['SOLES'].sum()
        
        facturas_ordenadas = []
        precios_lista = []
        pedidos_lista = []
        for doc_key, df_doc in df_grupo.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"], sort=False):
            fmt_doc = format_doc_id(doc_key[0], doc_key[1], doc_key[2])
            facturas_ordenadas.append(fmt_doc)
            p_ref = df_doc['PRECIO_UNID'].iloc[0] if 'PRECIO_UNID' in df_doc.columns else 0
            precios_lista.append(f"{float(p_ref):.2f}")
            if 'ID_PEDIDO' in df_doc.columns:
                pedidos_lista.append(str(df_doc['ID_PEDIDO'].iloc[0]))
        
        if len(set(precios_lista)) <= 1:
            precios_str = precios_lista[0] if precios_lista else ""
        else:
            precios_str = ", ".join(precios_lista)
        
        pedidos_str = ", ".join(pedidos_lista) if pedidos_lista else ""
        
        fecha_min = df_grupo['FECHA_ORIG'].min()
        fecha_max = df_grupo['FECHA_ORIG'].max()
        fecha_max_str = format_fecha(fecha_max)
        
        nom_articulo = df_grupo['NOM_ARTICULO'].iloc[0]
        nom_linea = df_grupo['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_grupo.columns else linea
        id_l = id_linea or (df_grupo['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_grupo.columns else "")
        
        sku_display = format_id_name(sku, nom_articulo, field_name='SKU')
        linea_display = format_id_name(id_l, nom_linea, field_name='LÍNEA')
        
        nom_cliente = df_grupo['NOM_CLIENTE'].iloc[0] if 'NOM_CLIENTE' in df_grupo.columns else cliente
        id_c = id_cliente or (df_grupo['ID_CLIENTE'].iloc[0] if 'ID_CLIENTE' in df_grupo.columns else "")
        cliente_display = format_id_name(id_c, nom_cliente, field_name='CLIENTE')
        
        fecha_min_fmt = format_fecha(fecha_min)
        
        if tipo_agrupacion == "CLIENTE":
            return {
                'CLIENTE': cliente_display,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'PEDIDOS': pedidos_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': fecha_min_fmt,
            }
        elif tipo_agrupacion == "LINEA":
            return {
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CLIENTE': cliente_display,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'PEDIDOS': pedidos_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': fecha_min_fmt,
            }
        elif tipo_agrupacion == "SKU":
            return {
                'SKU': sku_display,
                'LÍNEA': linea_display,
                'CLIENTE': cliente_display,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'PEDIDOS': pedidos_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': fecha_min_fmt,
            }

    @staticmethod
    def obtener_vendedores(historial: pd.DataFrame) -> List[Dict]:
        """Obtiene vendedores únicos con ID y nombre."""
        if historial.empty or "ID_VENDEDOR" not in historial.columns:
            return []
        
        vendedores = historial.groupby("ID_VENDEDOR").agg({
            "NOM_VENDEDOR": "first",
            "SOLES": "sum"
        }).reset_index()
        
        return vendedores.sort_values("SOLES", ascending=False).to_dict("records")

    @staticmethod
    def obtener_clientes_por_vendedor(historial: pd.DataFrame, vendedor_ids: List[str]) -> List[str]:
        """Filtra clientes por vendedor(es)."""
        if historial.empty or "ID_VENDEDOR" not in historial.columns:
            return []
        
        df_filtrado = historial[historial['ID_VENDEDOR'].isin(vendedor_ids)]
        return df_filtrado['NOM_CLIENTE'].dropna().unique().tolist()

    @staticmethod
    def obtener_resumen_lineas(historial: pd.DataFrame, limit: int = None) -> List[Dict]:
        """Obtiene resumen de líneas para dashboard con formato ID - NOMBRE."""
        if historial.empty or "NOM_LINEA" not in historial.columns:
            return []
        
        # Agrupar por ID y Nombre para consistencia
        group_cols = ["ID_LINEA", "NOM_LINEA"] if "ID_LINEA" in historial.columns else ["NOM_LINEA"]
        
        resumen = historial.groupby(group_cols).agg({
            "SOLES": "sum",
            "ID_ARTICULO": "nunique"
        }).reset_index()
        
        resumen.columns = group_cols + ["SOLES", "SKU_COUNT"]
        total = resumen["SOLES"].sum()
        
        # Crear nombre formateado
        if "ID_LINEA" in resumen.columns:
            resumen["NOM_LINEA_FMT"] = resumen.apply(
                lambda x: format_id_name(x["ID_LINEA"], x["NOM_LINEA"], field_name='LÍNEA'), axis=1
            )
        else:
            resumen["NOM_LINEA_FMT"] = resumen["NOM_LINEA"]
        
        if total > 0:
            resumen["PORCENTAJE"] = resumen["SOLES"] / total
            max_soles = resumen["SOLES"].max()
            resumen["ESCALA_VISUAL"] = resumen["SOLES"] / max_soles
            resumen["ES_NEGATIVO"] = resumen["SOLES"] < 0
        
        resultado = resumen.sort_values("SOLES", ascending=False)
        if limit:
            resultado = resultado.head(limit)
        
        return resultado.to_dict("records")

    @staticmethod
    def generar_comparacion_mes_a_mes(
        historial: pd.DataFrame,
        agrupacion: str = "ID_ARTICULO",
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None,
        tipo_comparacion: str = "2MESES"
    ) -> Dict[str, List[Dict]]:
        """
        Genera comparación MES A MES con meses como columnas.
        Una FILA por cada combinación única (según agrupación).
        Columnas dinámicas: CANT, MONTO, FACTURAS por cada mes.
        Al final: FECHA ULT, DIF_SOLES, DIF_PCT, TENDENCIA.
        
        Estructura según agrupación:
        - Por SKU: SKU, LÍNEA, CLIENTE, [MES-CANT, MES-MONTO, MES-FACTURAS...], FECHA ULT, DIF..., TENDENCIA
        - Por Línea: LÍNEA, SKU, CLIENTE, [MES-CANT, MES-MONTO, MES-FACTURAS...], FECHA ULT, DIF..., TENDENCIA
        - Por Cliente: CLIENTE, SKU, LÍNEA, [MES-CANT, MES-MONTO, MES-FACTURAS...], FECHA ULT, DIF..., TENDENCIA
        """
        df = historial.copy()
        
        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
        if 'FECHA_ORIG' not in df.columns or df.empty:
            return {}
        
        df = df.sort_values(by=['FECHA_ORIG'], ascending=[True])
        
        df['PERIODO_MES'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        periodos_unicos = sorted(df['PERIODO_MES'].unique())
        
        if len(periodos_unicos) < 2 and tipo_comparacion == "2MESES":
            return {}
        
        col_nom_articulo = next((c for c in df.columns if 'NOM' in c.upper() and 'ARTICULO' in c.upper()), None)
        col_nom_linea = next((c for c in df.columns if 'NOM' in c.upper() and 'LINEA' in c.upper()), None)
        
        if agrupacion == "ID_CLIENTE":
            group_cols = ["ID_CLIENTE", "NOM_CLIENTE", "ID_ARTICULO", "ID_LINEA", "NOM_LINEA"]
        elif agrupacion == "NOM_LINEA":
            group_cols = ["ID_LINEA", "NOM_LINEA", "ID_ARTICULO", "ID_CLIENTE", "NOM_CLIENTE"]
        else:
            group_cols = ["ID_ARTICULO", "ID_LINEA", "NOM_LINEA", "ID_CLIENTE", "NOM_CLIENTE"]
        
        agg_dict = {
            'SOLES': 'sum',
            'CANTIDAD': 'sum'
        }
        if col_nom_articulo and col_nom_articulo not in group_cols:
            agg_dict[col_nom_articulo] = 'first'
        
        agg = df.groupby(group_cols + ['PERIODO_MES']).agg(agg_dict).reset_index()
        
        soles_agg = pd.to_numeric(agg['SOLES'], errors='coerce').fillna(0)
        cant_agg = pd.to_numeric(agg['CANTIDAD'], errors='coerce').fillna(0)
        agg['PRECIO_UNIT'] = (soles_agg / cant_agg).replace([float('inf'), -float('inf')], 0).fillna(0).round(4)
        
        if tipo_comparacion == "2MESES" and len(periodos_unicos) >= 2:
            meses_mostrar = [periodos_unicos[-2], periodos_unicos[-1]]
        else:
            meses_mostrar = periodos_unicos
        
        resultado_por_vendedor = {}
        
        for vendedor_id, df_vendedor in df.groupby("ID_VENDEDOR"):
            nom_vendedor = df_vendedor['NOM_VENDEDOR'].iloc[0] if 'NOM_VENDEDOR' in df_vendedor.columns else ''
            
            vendedor_keys = df_vendedor[group_cols].drop_duplicates()
            agg_vendedor = agg.merge(vendedor_keys, on=group_cols, how='inner')
            
            if agrupacion == "ID_CLIENTE":
                grupo_principal = ["ID_CLIENTE", "NOM_CLIENTE", "ID_ARTICULO"]
            elif agrupacion == "NOM_LINEA":
                grupo_principal = ["ID_LINEA", "NOM_LINEA", "ID_ARTICULO"]
            else:
                grupo_principal = ["ID_ARTICULO", "ID_LINEA", "NOM_LINEA"]
            
            # Optimización: Pre-agrupar df_vendedor para evitar filtrado manual repetitivo O(N^2)
            df_grupos_vendedor = dict(list(df_vendedor.groupby(grupo_principal)))
            
            agg_base = agg_vendedor.groupby(grupo_principal).agg({
                'PERIODO_MES': lambda x: list(x),
                'SOLES': 'sum',
                'CANTIDAD': 'sum'
            }).reset_index()
            
            cols_to_merge = []
            extra_cols = []
            if col_nom_articulo and col_nom_articulo in df_vendedor.columns and col_nom_articulo not in grupo_principal:
                cols_to_merge.append(col_nom_articulo)
            
            if 'NOM_LINEA' in df_vendedor.columns and 'NOM_LINEA' not in grupo_principal:
                cols_to_merge.append('NOM_LINEA')
            if 'ID_LINEA' in df_vendedor.columns and 'ID_LINEA' not in grupo_principal:
                extra_cols.append('ID_LINEA')
            
            if agrupacion == "ID_CLIENTE":
                if 'NOM_CLIENTE' in df_vendedor.columns and 'NOM_CLIENTE' not in grupo_principal:
                    cols_to_merge.append('NOM_CLIENTE')
            else:
                if 'NOM_CLIENTE' in df_vendedor.columns:
                    cols_to_merge.append('NOM_CLIENTE')
                if 'ID_CLIENTE' in df_vendedor.columns and 'ID_CLIENTE' not in grupo_principal:
                    extra_cols.append('ID_CLIENTE')
            
            for col in cols_to_merge + extra_cols:
                temp = df_vendedor.groupby(grupo_principal[0])[col].first().reset_index()
                agg_base = agg_base.merge(temp, on=grupo_principal[0], how='left')
            
            resultado = []
            
            for _, row in agg_base.iterrows():
                grupo_keys = [row[c] for c in grupo_principal]
                
                item = {}
                
                if agrupacion == "ID_CLIENTE":
                    id_c, nom_c, id_art = grupo_keys[:3]
                    nom_art = row.get(col_nom_articulo, '') if col_nom_articulo else id_art
                    id_l = row.get('ID_LINEA', '')
                    nom_l = row.get('NOM_LINEA', '') if 'NOM_LINEA' in row.index else ''
                    item['CLIENTE'] = format_id_name(id_c, nom_c, field_name='CLIENTE')
                    item['SKU'] = format_id_name(id_art, nom_art, field_name='SKU')
                    item['ID_CLIENTE'] = str(id_c) if id_c else ''
                    item['ID_LINEA'] = str(id_l) if id_l else ''
                    item['LÍNEA'] = format_id_name(id_l, nom_l, field_name='LÍNEA')
                elif agrupacion == "NOM_LINEA":
                    id_l, nom_l, id_art = grupo_keys[:3]
                    nom_art = row.get(col_nom_articulo, '') if col_nom_articulo else id_art
                    id_c = row.get('ID_CLIENTE', '')
                    nom_c = row.get('NOM_CLIENTE', '') if 'NOM_CLIENTE' in row.index else ''
                    item['LÍNEA'] = format_id_name(id_l, nom_l, field_name='LÍNEA')
                    item['SKU'] = format_id_name(id_art, nom_art, field_name='SKU')
                    item['CLIENTE'] = format_id_name(id_c, nom_c, field_name='CLIENTE')
                    item['ID_LINEA'] = str(id_l) if id_l else ''
                else:
                    id_art = grupo_keys[0]
                    id_l = row.get('ID_LINEA', '')
                    nom_l = row.get('NOM_LINEA', '') if 'NOM_LINEA' in row.index else ''
                    nom_art = row.get(col_nom_articulo, '') if col_nom_articulo else id_art
                    id_c = row.get('ID_CLIENTE', '')
                    nom_c = row.get('NOM_CLIENTE', '') if 'NOM_CLIENTE' in row.index else ''
                    item['SKU'] = format_id_name(id_art, nom_art, field_name='SKU')
                    item['LÍNEA'] = format_id_name(id_l, nom_l, field_name='LÍNEA')
                    item['CLIENTE'] = format_id_name(id_c, nom_c, field_name='CLIENTE')
                    item['ID_LINEA'] = str(id_l) if id_l else ''
                
                # Recuperar datos del grupo pre-calculado (Optimización de búsqueda O(1))
                df_grupo_base = df_grupos_vendedor.get(tuple(grupo_keys), pd.DataFrame())

                for mes in meses_mostrar:
                    df_mes = df_grupo_base[df_grupo_base['PERIODO_MES'] == mes]
                    if not df_mes.empty:
                        item[f'{mes}-CANT'] = int(df_mes['CANTIDAD'].sum())
                        item[f'{mes}-MONTO'] = round(df_mes['SOLES'].sum(), 2)
                        item[f'{mes}-FACTURAS'] = df_mes.groupby(['TPO_DOC', 'SERIE_DOC', 'NRO_DOC']).ngroups
                    else:
                        item[f'{mes}-CANT'] = 0
                        item[f'{mes}-MONTO'] = 0.0
                        item[f'{mes}-FACTURAS'] = 0
                
                if not df_grupo_base.empty:
                    fecha_max = df_grupo_base['FECHA_ORIG'].max()
                    item['FECHA_ULT'] = format_fecha(fecha_max)
                else:
                    item['FECHA_ULT'] = ''
                
                if tipo_comparacion == "2MESES" and len(meses_mostrar) >= 2:
                    monto_anterior = item.get(f'{meses_mostrar[0]}-MONTO', 0)
                    monto_actual = item.get(f'{meses_mostrar[1]}-MONTO', 0)
                    dif = monto_actual - monto_anterior
                    item['DIF_SOLES'] = round(dif, 2)
                    item['DIF_PCT'] = round(dif / monto_anterior * 100, 2) if monto_anterior != 0 else 0.0
                    item['TENDENCIA'] = '🔺' if dif > 0 else ('🔻' if dif < 0 else '➡️')
                
                resultado.append(item)
            
            resultado_por_vendedor[vendedor_id] = {
                'MESES': meses_mostrar,
                'TIPO': tipo_comparacion,
                'DATA': resultado
            }
        
        return resultado_por_vendedor

    @staticmethod
    def generar_evolucion_mensual(
        historial: pd.DataFrame,
        agrupacion: str = "ID_ARTICULO",
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None
    ) -> Dict[str, List[Dict]]:
        """
        Genera datos con meses como columnas traspuestas (Cantidad, Monto, Facturas).
        Returns: Dict[vendedor_id] = {'MESES': [...], 'DATA': [...]}
        """
        resultado = ReporteConsolidado.generar_comparacion_mes_a_mes(
            historial=historial,
            agrupacion=agrupacion,
            clientes_filtro=clientes_filtro,
            vendedores_filtro=vendedores_filtro,
            lineas_filtro=lineas_filtro,
            tipo_comparacion="TODOS"
        )
        
        resultado_por_vendedor = {}
        for vid, data in resultado.items():
            if vid not in resultado_por_vendedor:
                resultado_por_vendedor[vid] = []
            items_data = data.get('DATA', [])
            meses = data.get('MESES', [])
            
            for item in items_data:
                if len(meses) >= 2:
                    m_act = meses[-1]
                    m_ant = meses[-2]
                    monto_act = item.get(f'{m_act}-MONTO', 0)
                    monto_ant = item.get(f'{m_ant}-MONTO', 0)
                    dif = monto_act - monto_ant
                    item['DIF_SOLES'] = round(dif, 2)
                    item['DIF_PCT'] = round(dif / monto_ant * 100, 2) if monto_ant != 0 else 0.0
                    item['TENDENCIA'] = '🔺' if dif > 0 else ('🔻' if dif < 0 else '➡️')
            
            resultado_por_vendedor[vid] = items_data
        
        return resultado_por_vendedor

    @staticmethod
    def generar_pareto_por_vendedor(
        historial: pd.DataFrame,
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None
    ) -> Dict[str, List[Dict]]:
        '''
        Genera reporte Pareto por Cliente (una hoja por vendedor).
        - Clientes ordenados por monto (mayor a menor)
        - Metricas: % Individual, % Acumulado, Categoria (Vital/Trivial)
        - Tendencia por meses: columnas traspuestas con Cantidad, Monto, Precio
        '''
        df = historial.copy()
        
        # Filtrar valores no deseados usando el diccionario centralizado
        df = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
        df = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')

        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
        if 'FECHA_ORIG' not in df.columns or df.empty:
            return {}
        
        df = df.sort_values(by=['FECHA_ORIG'], ascending=[True])
        
        # Agregar columna de periodo (vectorizado)
        df['PERIODO_MES'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        meses_unicos = sorted(df['PERIODO_MES'].unique())
        
        resultado_por_vendedor = {}
        
        for vendedor_id, df_vendedor in df.groupby('ID_VENDEDOR'):
            # Obtener nombre del vendedor
            nom_vendedor = df_vendedor['NOM_VENDEDOR'].iloc[0] if 'NOM_VENDEDOR' in df_vendedor.columns else ''
            
            # Crear llave única de documento para contar (Optimizado para alto rendimiento)
            df_vendedor['DOC_KEY'] = [f"{str(t)}|{str(s)}|{str(n)}" for t, s, n in zip(df_vendedor['TPO_DOC'], df_vendedor['SERIE_DOC'], df_vendedor['NRO_DOC'])]
            
            # Agregar por Cliente (contando documentos únicos)
            agg = df_vendedor.groupby(['ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_MES']).agg({
                'SOLES': 'sum',
                'CANTIDAD': 'sum',
                'DOC_KEY': 'nunique',
            }).reset_index()
            agg = agg.rename(columns={'DOC_KEY': 'NRO_DOCS'})
            
            # Calcular precio unitario promedio por cliente/mes (vectorizado)
            soles_agg = pd.to_numeric(agg['SOLES'], errors='coerce').fillna(0)
            cant_agg = pd.to_numeric(agg['CANTIDAD'], errors='coerce').fillna(0)
            agg['PRECIO_UNIT'] = soles_agg / cant_agg
            agg.loc[cant_agg == 0, 'PRECIO_UNIT'] = 0
            agg['PRECIO_UNIT'] = agg['PRECIO_UNIT'].round(4)
            
            # Total de documentos únicos por cliente
            nro_docs_por_cliente = agg.groupby(['ID_CLIENTE', 'NOM_CLIENTE'])['NRO_DOCS'].sum().reset_index()
            nro_docs_por_cliente.columns = ['ID_CLIENTE', 'NOM_CLIENTE', 'NRO_DOCS_TOTAL']
            nro_docs_dict = dict(zip(
                zip(nro_docs_por_cliente['ID_CLIENTE'], nro_docs_por_cliente['NOM_CLIENTE']),
                nro_docs_por_cliente['NRO_DOCS_TOTAL']
            ))
            
            # Calcular total por cliente (vectorizado)
            # CORRECCIÓN: Agrupar por cliente primero para evitar duplicados
            total_por_cliente = agg.groupby(['ID_CLIENTE', 'NOM_CLIENTE']).agg({
                'SOLES': 'sum',
                'CANTIDAD': 'sum'
            }).reset_index()
            total_por_cliente.columns = ['ID_CLIENTE', 'NOM_CLIENTE', 'MONTO_TOTAL', 'CANTIDAD_TOTAL']
            total_por_cliente = total_por_cliente.sort_values('MONTO_TOTAL', ascending=False)
            
            # Calcular % individual y acumulado (vectorizado)
            # CORRECCIÓN: Dependencia de Vendedor = SUM(SOLES) del cliente / SUM(SOLES) TOTAL del vendedor
            total_vendedor = total_por_cliente['MONTO_TOTAL'].sum()
            if total_vendedor > 0:
                total_por_cliente['PCT_INDIVIDUAL'] = total_por_cliente['MONTO_TOTAL'] / total_vendedor * 100
            else:
                total_por_cliente['PCT_INDIVIDUAL'] = 0
            
            total_por_cliente['PCT_ACUMULADO'] = total_por_cliente['PCT_INDIVIDUAL'].cumsum()
            total_por_cliente['CATEGORIA'] = total_por_cliente['PCT_ACUMULADO'].apply(
                lambda x: 'VITAL (≤80%)' if x <= 80 else 'VITAL (100%)' if x == 100 else 'TRIVIAL (>80%)'
            )
            
            # Construir lista de items (usar groupby para evitar filtrado repetido)
            items = []
            # Crear un diccionario de grupos por cliente para acceso rapido
            grupos_por_cliente = dict(list(agg.groupby(['ID_CLIENTE', 'NOM_CLIENTE'])))
            
            for _, row_cliente in total_por_cliente.iterrows():
                id_c, nom_c = row_cliente['ID_CLIENTE'], row_cliente['NOM_CLIENTE']
                cliente_display = format_id_name(id_c, nom_c, field_name='CLIENTE')
                monto_total = row_cliente['MONTO_TOTAL']
                cantidad_total = row_cliente['CANTIDAD_TOTAL']
                
                item = {
                    'VENDEDOR': format_id_name(vendedor_id, nom_vendedor, field_name='VENDEDOR'),
                    'CLIENTE': cliente_display,
                    'CANTIDAD': int(cantidad_total),
                    'MONTO': monto_total,
                    'NRO_DOCS': int(nro_docs_dict.get((id_c, nom_c), 0)),
                    'PCT_INDIVIDUAL': row_cliente['PCT_INDIVIDUAL'],
                    'PCT_ACUMULADO': row_cliente['PCT_ACUMULADO'],
                    'CATEGORIA': row_cliente['CATEGORIA'],
                }
                
                # Agregar columnas por mes usando el grupo pre-computado
                df_cliente = grupos_por_cliente.get((id_c, nom_c), pd.DataFrame())
                
                for mes in meses_unicos:
                    if not df_cliente.empty:
                        df_mes = df_cliente[df_cliente['PERIODO_MES'] == mes]
                        if not df_mes.empty:
                            cantidad = df_mes['CANTIDAD'].sum()
                            monto = df_mes['SOLES'].sum()
                            precio = df_mes['PRECIO_UNIT'].iloc[0] if not df_mes.empty else 0
                        else:
                            cantidad = 0
                            monto = 0
                            precio = 0
                    else:
                        cantidad = 0
                        monto = 0
                        precio = 0
                    
                    item[f'{mes}-CANT'] = int(cantidad)
                    item[f'{mes}-MONTO'] = monto
                    item[f'{mes}-PRECIO'] = precio
                
# Calcular tendencia (mes actual vs anterior)
                if len(meses_unicos) >= 2:
                    mes_actual = meses_unicos[-1]
                    mes_anterior = meses_unicos[-2]
                    
                    monto_actual = item.get(f'{mes_actual}-MONTO', 0)
                    monto_anterior = item.get(f'{mes_anterior}-MONTO', 0)
                    
                    dif = monto_actual - monto_anterior
                    item['DIF_SOLES'] = dif
                    item['DIF_PCT'] = (dif / monto_anterior * 100) if monto_anterior > 0 else 0
                    item['TENDENCIA'] = '🔺' if dif > 0 else ('🔻' if dif < 0 else '➡️')
                else:
                    item['DIF_SOLES'] = 0
                    item['DIF_PCT'] = 0
                    item['TENDENCIA'] = '➡️'
                
                items.append(item)
            
            resultado_por_vendedor[vendedor_id] = items
        
        return resultado_por_vendedor

    @staticmethod
    def generar_pareto_sucursales(
        historial: pd.DataFrame,
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None
    ) -> Dict[str, List[Dict]]:
        """
        Genera reporte Pareto por Cliente + Sucursal con detalle de facturas.
        Returns: Dict con datos por vendedor.
        """
        df = historial.copy()
        
        # Filtrar valores no deseados usando el diccionario centralizado
        df = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
        df = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')

        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
        if 'FECHA_ORIG' not in df.columns or df.empty:
            return {}
        
        df = df.sort_values(by=['FECHA_ORIG'], ascending=[True])
        df['PERIODO_MES'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        meses_unicos = sorted(df['PERIODO_MES'].unique())
        
        resultado_por_vendedor = {}
        
        agrupar_por = ['ID_CLIENTE', 'NOM_CLIENTE']
        if 'NOM_SUCURSAL' in df.columns:
            agrupar_por.append('NOM_SUCURSAL')
        
        if 'NRO_DOC' in df.columns and 'TPO_DOC' in df.columns and 'SERIE_DOC' in df.columns:
            # Optimización: Usar list comprehension en lugar de apply(axis=1) para mejorar velocidad en datasets grandes
            df['DOC_FMT'] = [format_doc_id(t, s, n) for t, s, n in zip(df['TPO_DOC'], df['SERIE_DOC'], df['NRO_DOC'])]
        elif 'NRO_DOC' in df.columns:
            df['DOC_FMT'] = df['NRO_DOC'].astype(str)
        
        for vendedor_id, df_vendedor_orig in df.groupby('ID_VENDEDOR'):
            df_vendedor = df_vendedor_orig.copy()
            nom_vendedor = df_vendedor['NOM_VENDEDOR'].iloc[0] if 'NOM_VENDEDOR' in df_vendedor.columns else ''
            
            agg_cols = agrupar_por + ['PERIODO_MES']
            agg_dict = {'SOLES': 'sum', 'CANTIDAD': 'sum'}
            if 'DOC_FMT' in df_vendedor.columns:
                agg_dict['DOC_FMT'] = lambda x: list(x.unique())
            
            agg = df_vendedor.groupby(agg_cols).agg(agg_dict).reset_index()
            
            total_por_cliente = agg.groupby(agrupar_por).agg({
                'SOLES': 'sum',
                'CANTIDAD': 'sum'
            }).reset_index()
            total_por_cliente.columns = agrupar_por + ['MONTO_TOTAL', 'CANTIDAD_TOTAL']
            total_por_cliente = total_por_cliente.sort_values('MONTO_TOTAL', ascending=False)
            
            total_vendedor = total_por_cliente['MONTO_TOTAL'].sum()
            if total_vendedor > 0:
                total_por_cliente['PCT_INDIVIDUAL'] = total_por_cliente['MONTO_TOTAL'] / total_vendedor * 100
            else:
                total_por_cliente['PCT_INDIVIDUAL'] = 0
            
            total_por_cliente['PCT_ACUMULADO'] = total_por_cliente['PCT_INDIVIDUAL'].cumsum()
            total_por_cliente['CATEGORIA'] = total_por_cliente['PCT_ACUMULADO'].apply(
                lambda x: 'VITAL (≤80%)' if x <= 80 else 'VITAL (100%)' if x == 100 else 'TRIVIAL (>80%)'
            )
            
            grupos = dict(list(agg.groupby(agrupar_por)))
            items = []
            
            for _, row in total_por_cliente.iterrows():
                id_c, nom_c = row['ID_CLIENTE'], row['NOM_CLIENTE']
                cliente_display = format_id_name(id_c, nom_c, field_name='CLIENTE')
                sucursal = row.get('NOM_SUCURSAL', 'SUCURSAL PRINCIPAL') if 'NOM_SUCURSAL' in total_por_cliente.columns else 'SUCURSAL PRINCIPAL'
                
                key = (id_c, nom_c, sucursal) if 'NOM_SUCURSAL' in df.columns else (id_c, nom_c)
                df_grupo = grupos.get(key, pd.DataFrame())
                
                # Corrección: Extraer facturas de la lista agrupada correctamente
                facturas = set()
                for doc_list in df_grupo.get('DOC_FMT', []):
                    facturas.update(doc_list)
                facturas_str = ", ".join(sorted(facturas)[:20]) + (" ..." if len(facturas) > 20 else "")
                
                item = {
                    'VENDEDOR': format_id_name(vendedor_id, nom_vendedor, field_name='VENDEDOR'),
                    'CLIENTE': cliente_display,
                    'SUCURSAL': sucursal,
                    'FACTURAS': facturas_str,
                    'CANTIDAD': int(row['CANTIDAD_TOTAL']),
                    'MONTO': row['MONTO_TOTAL'],
                    'PCT_INDIVIDUAL': row['PCT_INDIVIDUAL'],
                    'PCT_ACUMULADO': row['PCT_ACUMULADO'],
                    'CATEGORIA': row['CATEGORIA'],
                }
                
                for mes in meses_unicos:
                    df_mes = df_grupo[df_grupo['PERIODO_MES'] == mes] if not df_grupo.empty else pd.DataFrame()
                    cantidad = df_mes['CANTIDAD'].sum() if not df_mes.empty else 0
                    monto = df_mes['SOLES'].sum() if not df_mes.empty else 0
                    item[f'{mes}-CANT'] = int(cantidad)
                    item[f'{mes}-MONTO'] = monto
                
                items.append(item)
            
            items.sort(key=lambda x: (x['CLIENTE'], -x['MONTO']))
            resultado_por_vendedor[vendedor_id] = items
        
        return resultado_por_vendedor

    @staticmethod
    def generar_pareto_completo(
        historial: pd.DataFrame,
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None
    ) -> Dict:
        """
        Genera reporte Pareto completo para Excel (formato simple).
        Estructura: 1 fila por cliente, columnas hacia la derecha por línea.
        
        Returns: {
            'RESUMEN': {'KPIS': {...}},
            'CLIENTES': [{'CLIENTE': ..., 'MONTO_TOTAL': ..., 'PCT_GLOBAL': ..., 'CATEGORIA': ..., 'L{ID}_CANT': ..., 'L{ID}_MONTO': ..., 'L{ID}_PCT': ...}],
            'LINEAS': [{'ID_LINEA': ...}]
        }
        """
        df = historial.copy()
        
        # Filtrar valores no deseados usando el diccionario centralizado
        df = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
        df = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')

        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
        if 'FECHA_ORIG' not in df.columns or df.empty:
            return {'RESUMEN': {'KPIS': {}}, 'CLIENTES': [], 'LINEAS': []}
        
        df = df.sort_values(by=['FECHA_ORIG'], ascending=[True])
        
        # Identificar periodos para tendencia (Mes Actual vs Anterior)
        df['PERIODO_TEND'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        periodos = sorted(df['PERIODO_TEND'].unique())
        mes_act = periodos[-1] if periodos else None
        mes_ant = periodos[-2] if len(periodos) >= 2 else None

        # Obtener todas las líneas únicas
        lineas_unicas = df[['ID_LINEA', 'NOM_LINEA']].drop_duplicates().sort_values('ID_LINEA')
        lineas_list = [{'ID_LINEA': str(row['ID_LINEA']), 'NOM_LINEA': str(row['NOM_LINEA'])} for _, row in lineas_unicas.iterrows()]
        
        # Agrupar por CLIENTE + LÍNEA para obtener datos por línea
        # FASE 1: Filtrar montos negativos para cálculo de Pareto
        df_positivos = df[df['SOLES'] >= 0].copy()
        
        # CORRECCIÓN: Agrupar por CLIENTE + LÍNEA primero para evitar duplicados
        # Esto evita el error de sumar porcentajes de múltiples artículos por factura
        agg_cliente_linea = df_positivos.groupby(['ID_CLIENTE', 'NOM_CLIENTE', 'ID_LINEA', 'NOM_LINEA']).agg({
            'SOLES': 'sum',
            'CANTIDAD': 'sum'
        }).reset_index()
        
        # Agrupar por cliente para obtener totales
        # CORRECCIÓN: Calcular Market Share sobre el total del cliente, no sumando porcentajes parciales
        agg_cliente = agg_cliente_linea.groupby(['ID_CLIENTE', 'NOM_CLIENTE']).agg({
            'SOLES': 'sum',
            'CANTIDAD': 'sum'
        }).reset_index()
        agg_cliente.columns = ['ID_CLIENTE', 'NOM_CLIENTE', 'MONTO_TOTAL', 'CANTIDAD_TOTAL']
        agg_cliente = agg_cliente.sort_values('MONTO_TOTAL', ascending=False)
        
        # Calcular porcentajes globales
        # CORRECCIÓN: Market Share = SUM(SOLES) del cliente / SUM(SOLES) TOTAL del dataset
        total_global = agg_cliente['MONTO_TOTAL'].sum()
        if total_global > 0:
            agg_cliente['PCT_GLOBAL'] = agg_cliente['MONTO_TOTAL'] / total_global * 100
        else:
            agg_cliente['PCT_GLOBAL'] = 0
        
        # FASE 1: Validar que la suma de porcentajes sea 100%
        # CORRECCIÓN: Esto evita errores de agregación como porcentajes que superan el 100%
        suma_pct = agg_cliente['PCT_GLOBAL'].sum()
        if abs(suma_pct - 100) > 0.1:  # Tolerancia de 0.1%
            print(f"WARNING: Suma de porcentajes = {suma_pct:.2f}%, debería ser 100%")
        
        agg_cliente['PCT_ACUMULADO'] = agg_cliente['PCT_GLOBAL'].cumsum()
        agg_cliente['CATEGORIA'] = agg_cliente['PCT_ACUMULADO'].apply(
            lambda x: 'VITAL (≤80%)' if x <= 80 else 'VITAL (100%)' if x == 100 else 'TRIVIAL (>80%)'
        )
        
        # Diccionario para tendencia: (ID_CLIENTE, NOM_CLIENTE) -> {PERIODO: SOLES}
        df_tend = df.groupby(['ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_TEND'])['SOLES'].sum().unstack(fill_value=0)

        # Crear diccionario de datos por cliente para acceso rápido
        clientes_dict = {}
        for _, row in agg_cliente.iterrows():
            id_c = str(row['ID_CLIENTE'])
            nom_c = row['NOM_CLIENTE']
            cliente_display = format_id_name(id_c, nom_c, field_name='CLIENTE')
            
            # Calcular tendencia
            # CORRECCIÓN: Manejar datos incompletos y división por cero
            m_act = df_tend.loc[(row['ID_CLIENTE'], nom_c), mes_act] if mes_act and (row['ID_CLIENTE'], nom_c) in df_tend.index else 0
            m_ant = df_tend.loc[(row['ID_CLIENTE'], nom_c), mes_ant] if mes_ant and (row['ID_CLIENTE'], nom_c) in df_tend.index else 0
            
            # Si el mes actual es el último y está incompleto, usar lógica de variación
            if mes_act and mes_ant:
                # Detectar si el mes actual es el mes actual del sistema
                from datetime import datetime
                mes_actual_sistema = datetime.now().strftime('%Y-%m')
                mes_actual_periodo = mes_act if mes_act else ''
                
                # Si el mes actual es el mes del sistema y el monto es muy bajo, podría estar incompleto
                if mes_actual_periodo == mes_actual_sistema and m_act < m_ant * 0.5:
                    tendencia = '⏳'  # En proceso
                else:
                    dif = m_act - m_ant
                    tendencia = '🔺' if dif > 1 else ('🔻' if dif < -1 else '➡️')
            else:
                tendencia = '➡️'  # No hay suficientes datos
            
            cliente_data = {
                'ID_CLIENTE': id_c,
                'NOM_CLIENTE': nom_c,
                'ID_VENDEDOR': str(df[df['ID_CLIENTE'] == row['ID_CLIENTE']]['ID_VENDEDOR'].iloc[0]),
                'NOM_VENDEDOR': str(df[df['ID_CLIENTE'] == row['ID_CLIENTE']]['NOM_VENDEDOR'].iloc[0]),
                'CLIENTE': cliente_display,
                'MONTO_TOTAL': round(float(row['MONTO_TOTAL']), 2),
                'PCT_GLOBAL': round(float(row['PCT_GLOBAL']), 2),
                'CATEGORIA': row['CATEGORIA'],
                'TENDENCIA': tendencia,
            }
            
            # Agregar columnas por línea
            for linea in lineas_list:
                lid = linea['ID_LINEA']
                cliente_data[f'L{lid}_CANT'] = 0
                cliente_data[f'L{lid}_MONTO'] = 0.0
                cliente_data[f'L{lid}_PCT'] = 0.0
            
            clientes_dict[(id_c, nom_c)] = cliente_data
        
        # Llenar datos por línea
        for _, row in agg_cliente_linea.iterrows():
            id_c = str(row['ID_CLIENTE'])
            nom_c = row['NOM_CLIENTE']
            lid = str(row['ID_LINEA'])
            
            if (id_c, nom_c) in clientes_dict:
                clientes_dict[(id_c, nom_c)][f'L{lid}_CANT'] = int(row['CANTIDAD'])
                clientes_dict[(id_c, nom_c)][f'L{lid}_MONTO'] = round(float(row['SOLES']), 2)
                
                # Calcular porcentaje por línea
                monto_cliente = clientes_dict[(id_c, nom_c)]['MONTO_TOTAL']
                if monto_cliente > 0:
                    clientes_dict[(id_c, nom_c)][f'L{lid}_PCT'] = round(float(row['SOLES']) / monto_cliente * 100, 2)
        
        # Convertir a lista ordenada por monto
        clientes_list = [clientes_dict[(row['ID_CLIENTE'], row['NOM_CLIENTE'])] for _, row in agg_cliente.iterrows()]
        
        # Calcular KPIs
        clientes_triviales = agg_cliente[~agg_cliente['CATEGORIA'].str.contains('VITAL')]
        venta_trivial = clientes_triviales['MONTO_TOTAL'].sum() if not clientes_triviales.empty else 0
        num_triviales = clientes_triviales.shape[0]
        
        # FASE 2: Índice de Dispersión (Clientes Triviales / Venta Trivial)
        indice_dispersion = (num_triviales / venta_trivial) if venta_trivial > 0 else 0
        
        # SUGERENCIA 1: HHI (Índice de Concentración Herfindahl-Hirschman)
        # HHI = Σ(market_share_i²)
        # Interpretación: <0.15 = Baja, 0.15-0.25 = Moderada, >0.25 = Alta concentración
        hhi = 0
        if total_global > 0:
            hhi = ((agg_cliente['MONTO_TOTAL'] / total_global) ** 2).sum()
        
        kpis = {
            'TOTAL_CLIENTES': agg_cliente['ID_CLIENTE'].nunique(),
            'MONTO_TOTAL': round(total_global, 2),
            'TOTAL_LINEAS': lineas_unicas['ID_LINEA'].nunique(),
            'TOTAL_FACTURAS': df['NRO_DOC'].nunique() if 'NRO_DOC' in df.columns else 0,
            'CLIENTES_VITALES': agg_cliente[agg_cliente['CATEGORIA'].str.contains('VITAL')].shape[0],
            # Nuevo KPI: Market Share de Clientes Vitales
            'PCT_VITAL_MARKET_SHARE': round((agg_cliente[agg_cliente['CATEGORIA'].str.contains('VITAL')]['MONTO_TOTAL'].sum() / total_global * 100), 2) if total_global > 0 else 0,
            # FASE 2: Índice de Dispersión
            'INDICE_DISPERSION': round(indice_dispersion, 4),
            'CLIENTES_TRIVIALES': num_triviales,
            'VENTA_TRIVIAL': round(venta_trivial, 2),
            # SUGERENCIA 1: HHI (Índice de Concentración)
            'HHI': round(hhi, 4),
        }
        
        # Organizar datos por vendedor para las pestañas individuales
        por_vendedor = {}
        for cli in clientes_list:
            v_id = cli.get('ID_VENDEDOR', 'S/V')
            v_nom = cli.get('NOM_VENDEDOR', 'SIN ASIGNAR')
            key = format_id_name(v_id, v_nom)
            if key not in por_vendedor: por_vendedor[key] = []
            por_vendedor[key].append(cli)

        return {
            'RESUMEN': {'KPIS': kpis},
            'CLIENTES': clientes_list,
            'LINEAS': lineas_list,
            'POR_VENDEDOR': por_vendedor,
            'PERIODOS': periodos
        }
