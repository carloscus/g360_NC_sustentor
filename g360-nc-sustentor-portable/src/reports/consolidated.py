from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime
from src.core.utils import format_id_name, format_doc_id


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
        for doc_key, df_doc in df_grupo.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"], sort=False):
            fmt_doc = format_doc_id(doc_key[0], doc_key[1], doc_key[2])
            facturas_ordenadas.append(fmt_doc)
            sum_soles = df_doc['SOLES'].sum()
            sum_cant = df_doc['CANTIDAD'].sum()
            p_ref = abs(sum_soles / sum_cant) if sum_cant != 0 else 0
            precios_lista.append(f"{p_ref:.2f}")
        
        if len(set(precios_lista)) <= 1:
            precios_str = precios_lista[0] if precios_lista else ""
        else:
            precios_str = ", ".join(precios_lista)
        
        fecha_min = df_grupo['FECHA_ORIG'].min()
        fecha_max = df_grupo['FECHA_ORIG'].max()
        
        nom_linea = df_grupo['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_grupo.columns else ""
        id_linea = df_grupo['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_grupo.columns else ""
        linea_display = format_id_name(id_linea, nom_linea)
        
        return {
            'PERIODO': periodo,
            'TIPO_PERIODO': tipo_periodo,
            'SKU': format_id_name(sku, df_grupo['NOM_ARTICULO'].iloc[0]),
            'LÍNEA': linea_display,
            'CLIENTES': ", ".join(df_grupo['NOM_CLIENTE'].unique()[:3]),
            'CANTIDAD': cant_total,
            'MONTO': soles_total,
            'FACTURAS': ", ".join(facturas_ordenadas),
            'PRECIOS': precios_str,
            'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
            'FECHA_MAX': str(fecha_max),
        }

    @staticmethod
    def _procesar_item_factura(df_doc, doc_key) -> List[Dict]:
        """Procesa una factura pero retorna una fila por cada SKU."""
        tpo, serie, nro = doc_key
        
# Formatear número de factura
        num_factura = format_doc_id(tpo, serie, nro)
        
        # Obtener fecha de la factura
        fecha_factura = df_doc['FECHA_ORIG'].iloc[0] if 'FECHA_ORIG' in df_doc.columns else None
        fecha_str = fecha_factura.strftime('%d/%m/%Y') if fecha_factura else ""
        
        # Formatear Cliente principal de la factura
        id_cliente = df_doc['ID_CLIENTE'].iloc[0] if 'ID_CLIENTE' in df_doc.columns else ""
        nom_cliente = df_doc['NOM_CLIENTE'].iloc[0] if 'NOM_CLIENTE' in df_doc.columns else ""
        cliente_display = format_id_name(id_cliente, nom_cliente)
        
        # Una fila por cada SKU
        items = []
        for sku, df_sku in df_doc.groupby("ID_ARTICULO"):
            nom_art = df_sku['NOM_ARTICULO'].iloc[0] if 'NOM_ARTICULO' in df_sku.columns else sku
            sku_display = format_id_name(sku, nom_art)
            cant_sku = df_sku['CANTIDAD'].sum()
            soles_sku = df_sku['SOLES'].sum()
            pu = soles_sku / cant_sku if cant_sku > 0 else 0
            
            nom_linea = df_sku['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_sku.columns else ""
            id_linea = df_sku['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_sku.columns else ""
            linea_display = format_id_name(id_linea, nom_linea)
            
            items.append({
                'FACTURA': num_factura,
                'FECHA': fecha_str,
                'CLIENTE': cliente_display,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CANTIDAD': cant_sku,
                'PRECIO': pu,
                'MONTO': soles_sku,
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
        
        return {
            'CLIENTE': cliente,
            'LÍNEA': format_id_name(df_sku['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_sku.columns else '', 
                                  df_sku['NOM_LINEA'].iloc[0]),
            'SKU': format_id_name(sku, df_sku['NOM_ARTICULO'].iloc[0]),
            'CANTIDAD': cant_total,
            'MONTO': soles_total,
            'FACTURAS': ", ".join(facturas_ordenadas),
            'PRECIOS': precios_str,
            'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
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
        for doc_key, df_doc in df_grupo.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"], sort=False):
            fmt_doc = format_doc_id(doc_key[0], doc_key[1], doc_key[2])
            facturas_ordenadas.append(fmt_doc)
            sum_soles = df_doc['SOLES'].sum()
            sum_cant = df_doc['CANTIDAD'].sum()
            p_ref = abs(sum_soles / sum_cant) if sum_cant != 0 else 0
            precios_lista.append(f"{p_ref:.2f}")
        
        if len(set(precios_lista)) <= 1:
            precios_str = precios_lista[0] if precios_lista else ""
        else:
            precios_str = ", ".join(precios_lista)
        
        fecha_min = df_grupo['FECHA_ORIG'].min()
        
        nom_articulo = df_grupo['NOM_ARTICULO'].iloc[0]
        nom_linea = df_grupo['NOM_LINEA'].iloc[0] if 'NOM_LINEA' in df_grupo.columns else linea
        id_l = id_linea or (df_grupo['ID_LINEA'].iloc[0] if 'ID_LINEA' in df_grupo.columns else "")
        
        clientes_str = ", ".join(df_grupo['NOM_CLIENTE'].unique()[:3])
        
        # Formato consistente: ID - NOMBRE
        sku_display = format_id_name(sku, nom_articulo)
        linea_display = format_id_name(id_l, nom_linea)
        
        nom_cliente = df_grupo['NOM_CLIENTE'].iloc[0] if 'NOM_CLIENTE' in df_grupo.columns else cliente
        id_c = id_cliente or (df_grupo['ID_CLIENTE'].iloc[0] if 'ID_CLIENTE' in df_grupo.columns else "")
        cliente_display = format_id_name(id_c, nom_cliente)
        
        if tipo_agrupacion == "CLIENTE":
            return {
                'ID_CLIENTE': id_c,
                'CLIENTE': cliente_display,
                'ID_LINEA': id_l,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
            }
        elif tipo_agrupacion == "LINEA":
            return {
                'ID_LINEA': id_l,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CLIENTES': clientes_str,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
            }
        elif tipo_agrupacion == "SKU":
            return {
                'SKU': sku_display,
                'LÍNEA': linea_display,
                'CLIENTES': clientes_str,
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
            }
        else:  # ARTICULO
            return {
                'PRODUCTO': articulo or nom_articulo,
                'ID_LINEA': id_l,
                'LÍNEA': linea_display,
                'SKU': sku_display,
                'CLIENTES': ", ".join(df_grupo['NOM_CLIENTE'].unique()[:3]),
                'CANTIDAD': cant_total,
                'MONTO': soles_total,
                'FACTURAS': ", ".join(facturas_ordenadas),
                'PRECIOS': precios_str,
                'FECHA_ULT': fecha_max_str,
                'FECHA_MIN': str(fecha_min),
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
        """Obtiene resumen de líneas para dashboard."""
        if historial.empty or "NOM_LINEA" not in historial.columns:
            return []
        
        resumen = historial.groupby("NOM_LINEA").agg({
            "SOLES": "sum",
            "ID_ARTICULO": "nunique"
        }).reset_index()
        
        resumen.columns = ["NOM_LINEA", "SOLES", "SKU_COUNT"]
        total = resumen["SOLES"].sum()
        
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
        Si tipo_comparacion="2MESES": solo 2 meses (actual vs anterior)
        Si tipo_comparacion="TODOS": todos los meses como columnas traspuestas.
        
        Estructura dinámica según agrupación:
        - Por SKU: SKU, LÍNEA, CLIENTE, [MES-CANT, MES-MONTO, MES-PRECIO...], TENDENCIA
        - Por Línea: LÍNEA, SKU, CLIENTE, [MES-CANT, MES-MONTO, MES-PRECIO...], TENDENCIA
        - Por Cliente: CLIENTE, SKU, LÍNEA, [MES-CANT, MES-MONTO, MES-PRECIO...], TENDENCIA
        """
        df = historial.copy()
        
        if clientes_filtro:
            df = df[df['NOM_CLIENTE'].isin(clientes_filtro)]
        if vendedores_filtro:
            df = df[df['ID_VENDEDOR'].isin(vendedores_filtro)]
        if lineas_filtro:
            df = df[df['NOM_LINEA'].isin(lineas_filtro)]
        
        df = df.sort_values(by=['FECHA_ORIG'], ascending=[True])
        
        if 'FECHA_ORIG' not in df.columns:
            return {}
        
        df['PERIODO_MES'] = df['FECHA_ORIG'].dt.to_period('M').astype(str)
        
        periodos_unicos = sorted(df['PERIODO_MES'].unique())
        
        if len(periodos_unicos) < 2 and tipo_comparacion == "2MESES":
            return {}
        
        # Buscar columnas necesarias
        col_nom_articulo = next((c for c in df.columns if 'NOM' in c.upper() and 'ARTICULO' in c.upper()), None)
        col_nom_linea = next((c for c in df.columns if 'NOM' in c.upper() and 'LINEA' in c.upper()), None)
        
        # Determinar columnas base según agrupación
        if agrupacion == "ID_CLIENTE":
            group_cols = ["ID_CLIENTE", "NOM_CLIENTE", "ID_ARTICULO"]
            base_keys = ["CLIENTE", "SKU"]
        elif agrupacion == "NOM_LINEA":
            group_cols = ["ID_LINEA", "NOM_LINEA", "ID_ARTICULO"]
            base_keys = ["LINEA", "SKU"]
        else:  # ID_ARTICULO (SKU)
            group_cols = ["ID_ARTICULO"]
            base_keys = ["SKU"]
        
        # Agregar por grupos y período
        agg = df.groupby(group_cols + ["PERIODO_MES"]).agg({
            col_nom_articulo: 'first' if col_nom_articulo else 'first',
            col_nom_linea: 'first' if col_nom_linea else 'first',
            'SOLES': 'sum',
            'CANTIDAD': 'sum'
        }).reset_index()
        
        # Calcular precio unitario
        agg['PRECIO_UNIT'] = agg.apply(
            lambda x: x['SOLES'] / x['CANTIDAD'] if x['CANTIDAD'] > 0 else 0, axis=1
        )
        
        # Determinar meses a mostrar
        if tipo_comparacion == "2MESES" and len(periodos_unicos) >= 2:
            meses_mostrar = [periodos_unicos[-2], periodos_unicos[-1]]  # Anterior, Actual
        else:
            meses_mostrar = periodos_unicos
        
        # Pivotar: trasponer meses como columnas
        resultado = []
        for grupo_vals, df_grupo in agg.groupby(group_cols):
            if not isinstance(grupo_vals, tuple):
                grupo_vals = (grupo_vals,)
            
            item = {}
            
# Llenar claves base según agrupación
            if agrupacion == "ID_CLIENTE":
                id_cliente, nom_cliente, id_articulo = grupo_vals
                nom_art = df_grupo.iloc[0].get(col_nom_articulo, '') if col_nom_articulo else ''
                item['CLIENTE'] = format_id_name(id_cliente, nom_cliente)
                item['SKU'] = format_id_name(id_articulo, nom_art)
                
                id_linea = df_grupo.iloc[0].get('ID_LINEA', '')
                nom_linea = df_grupo.iloc[0].get(col_nom_linea, 'SIN LÍNEA') if col_nom_linea else 'SIN LÍNEA'
                item['LÍNEA'] = format_id_name(id_linea, nom_linea)
                
            elif agrupacion == "NOM_LINEA":
                id_linea, nom_linea, id_articulo = grupo_vals
                nom_art = df_grupo.iloc[0].get(col_nom_articulo, '') if col_nom_articulo else ''
                # Preservar ID como string para mantener ceros a la izquierda
                item['ID_LINEA'] = str(id_linea)
                item['LÍNEA'] = format_id_name(str(id_linea), nom_linea)
                item['SKU'] = format_id_name(str(id_articulo), nom_art)
                clientes = df_grupo['NOM_CLIENTE'].dropna().unique()[:3] if 'NOM_CLIENTE' in df_grupo.columns else []
                item['CLIENTE'] = ", ".join(clientes) if len(clientes) > 0 else ''
            else:  # SKU
                id_articulo = grupo_vals[0]
                nom_art = df_grupo.iloc[0].get(col_nom_articulo, '') if col_nom_articulo else ''
                item['SKU'] = format_id_name(id_articulo, nom_art)
                
                id_linea = df_grupo.iloc[0].get('ID_LINEA', '')
                nom_linea = df_grupo.iloc[0].get(col_nom_linea, 'SIN LÍNEA') if col_nom_linea else 'SIN LÍNEA'
                item['LÍNEA'] = format_id_name(id_linea, nom_linea)
                
                clientes = df_grupo['NOM_CLIENTE'].dropna().unique()[:3] if 'NOM_CLIENTE' in df_grupo.columns else []
                item['CLIENTE'] = ", ".join(clientes) if len(clientes) > 0 else ''
            
            # Agregar columnas por mes (meses como columnas traspuestas)
            for mes in meses_mostrar:
                df_mes = df_grupo[df_grupo['PERIODO_MES'] == mes]
                if not df_mes.empty:
                    cantidad = df_mes['CANTIDAD'].sum()
                    monto = df_mes['SOLES'].sum()
                    precio = df_mes['PRECIO_UNIT'].iloc[0] if not df_mes.empty else 0
                else:
                    cantidad = 0
                    monto = 0
                    precio = 0
                
                item[f'{mes}-CANT'] = int(cantidad)
                item[f'{mes}-MONTO'] = monto
                item[f'{mes}-PRECIO'] = precio
            
# Calcular tendencia (solo para 2 meses)
            if tipo_comparacion == "2MESES" and len(meses_mostrar) >= 2:
                mes_anterior_key = f'{meses_mostrar[0]}-MONTO'
                mes_actual_key = f'{meses_mostrar[1]}-MONTO'
                monto_anterior = item.get(mes_anterior_key, 0)
                monto_actual = item.get(mes_actual_key, 0)
                dif = monto_actual - monto_anterior
                item['DIF_SOLES'] = dif
                if monto_anterior != 0:
                    item['DIF_PCT'] = round(dif / monto_anterior * 100, 2)
                else:
                    item['DIF_PCT'] = 100.0 if monto_actual > 0 else 0.0
                item['TENDENCIA'] = '🔺' if dif > 0 else ('🔻' if dif < 0 else '➡️')
            
            resultado.append(item)
        
        return {
            'COMPARATIVO': resultado,
            'MESES': meses_mostrar,
            'TIPO': tipo_comparacion,
        }

    @staticmethod
    def generar_evolucion_mensual(
        historial: pd.DataFrame,
        agrupacion: str = "ID_ARTICULO",
        clientes_filtro: Optional[List[str]] = None,
        vendedores_filtro: Optional[List[str]] = None,
        lineas_filtro: Optional[List[str]] = None
    ) -> Dict[str, List[Dict]]:
        """
        Genera datos con meses como columnas traspuestas (Cantidad, Monto, Precio).
        Returns: Dict con datos por vendedor, cada item tiene columnas por mes.
        """
        # Reutilizar generar_comparacion_mes_a_mes con tipo_comparacion="TODOS"
        resultado = ReporteConsolidado.generar_comparacion_mes_a_mes(
            historial=historial,
            agrupacion=agrupacion,
            clientes_filtro=clientes_filtro,
            vendedores_filtro=vendedores_filtro,
            lineas_filtro=lineas_filtro,
            tipo_comparacion="TODOS"
        )
        
        if not resultado or 'COMPARATIVO' not in resultado:
            return {}
        
        # Convertir a formato por vendedor (si es necesario)
        resultado_por_vendedor = {}
        for item in resultado.get('COMPARATIVO', []):
            # Determinar vendedor_id (necesitaríamos agregarlo en el item)
            # Por ahora, usar un vendedor genérico
            vendedor_id = "TODOS"
            if vendedor_id not in resultado_por_vendedor:
                resultado_por_vendedor[vendedor_id] = []
            resultado_por_vendedor[vendedor_id].append(item)
        
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
        
        # Filtrar "SIN ASIGNAR" para no inflar metricas
        if 'NOM_CLIENTE' in df.columns:
            df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
        if 'NOM_VENDEDOR' in df.columns:
            df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
        
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
            
            # Agregar por Cliente y Periodo (una sola operación groupby)
            agg = df_vendedor.groupby(['ID_CLIENTE', 'NOM_CLIENTE', 'PERIODO_MES']).agg({
                'SOLES': 'sum',
                'CANTIDAD': 'sum',
            }).reset_index()
            
            # Calcular precio unitario promedio por cliente/mes (vectorizado)
            agg['PRECIO_UNIT'] = agg.apply(
                lambda x: x['SOLES'] / x['CANTIDAD'] if x['CANTIDAD'] > 0 else 0, axis=1
            )
            
            # Calcular total por cliente (vectorizado)
            total_por_cliente = agg.groupby(['ID_CLIENTE', 'NOM_CLIENTE']).agg({
                'SOLES': 'sum',
                'CANTIDAD': 'sum'
            }).reset_index()
            total_por_cliente.columns = ['ID_CLIENTE', 'NOM_CLIENTE', 'MONTO_TOTAL', 'CANTIDAD_TOTAL']
            total_por_cliente = total_por_cliente.sort_values('MONTO_TOTAL', ascending=False)
            
            # Calcular % individual y acumulado (vectorizado)
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
                cliente_display = format_id_name(id_c, nom_c)
                monto_total = row_cliente['MONTO_TOTAL']
                cantidad_total = row_cliente['CANTIDAD_TOTAL']
                
                item = {
                    'VENDEDOR': format_id_name(vendedor_id, nom_vendedor),
                    'CLIENTE': cliente_display,
                    'CANTIDAD': int(cantidad_total),
                    'MONTO': monto_total,
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
        
        if 'NOM_CLIENTE' in df.columns:
            df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
        if 'NOM_VENDEDOR' in df.columns:
            df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
        
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
            df['DOC_FMT'] = df.apply(
                lambda r: format_doc_id(r.get('TPO_DOC'), r.get('SERIE_DOC'), r.get('NRO_DOC')), axis=1
            )
        elif 'NRO_DOC' in df.columns:
            df['DOC_FMT'] = df['NRO_DOC'].astype(str)
        
        for vendedor_id, df_vendedor in df.groupby('ID_VENDEDOR'):
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
                cliente_display = format_id_name(id_c, nom_c)
                sucursal = row.get('NOM_SUCURSAL', 'SUCURSAL PRINCIPAL') if 'NOM_SUCURSAL' in total_por_cliente.columns else 'SUCURSAL PRINCIPAL'
                
                key = (id_c, nom_c, sucursal) if 'NOM_SUCURSAL' in df.columns else (id_c, nom_c)
                df_grupo = grupos.get(key, pd.DataFrame())
                
                facturas = set()
                for _, f in df_grupo.iterrows():
                    if 'DOC_FMT' in f:
                        facturas.add(str(f['DOC_FMT']))
                facturas_str = ", ".join(sorted(facturas)[:20]) + (" ..." if len(facturas) > 20 else "")
                
                item = {
                    'VENDEDOR': format_id_name(vendedor_id, nom_vendedor),
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
