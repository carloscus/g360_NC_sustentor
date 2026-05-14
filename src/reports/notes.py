from typing import List, Dict, Optional, Tuple
import pandas as pd
from datetime import datetime
from src.core.utils import format_id_name, format_doc_id, IGV_PERCENT


class ReporteNotasCredito:
    """Lógica del reporte de Notas de Crédito - separada del UI."""

    @staticmethod
    def procesar_lote(
        df: pd.DataFrame,
        cliente: str,
        motivo: str,
        factura_ref: str = ""
    ) -> Tuple[List[Dict], List[str], datetime, datetime]:
        """
        Procesa un lote de notas de crédito.
        Retorna: (items_procesados, docs_unicos, fecha_inicio, fecha_fin)
        """
        items = []
        docs_unicos = []
        
        items_agrupados = df.groupby("ID_ARTICULO")
        
        for sku, grupo in items_agrupados:
            cantidad = grupo["CANTIDAD"].sum()
            soles = grupo["SOLES"].sum()
            precio_prom = soles / cantidad if cantidad > 0 else 0
            
            # Obtener datos de línea si existen
            nom_linea = grupo["NOM_LINEA"].iloc[0] if "NOM_LINEA" in grupo.columns else ""
            id_linea = grupo["ID_LINEA"].iloc[0] if "ID_LINEA" in grupo.columns else ""
            
            nom_art = grupo["NOM_ARTICULO"].iloc[0] if "NOM_ARTICULO" in grupo.columns else ""

            items.append({
                "SKU": format_id_name(sku, nom_art),
                "NOM_ARTICULO": nom_art,
                "LINEA": format_id_name(id_linea, nom_linea),
                "CANTIDAD": abs(cantidad),
                "SOLES": abs(soles),
                "PRECIO_UNID": abs(precio_prom),
            })
            
            for _, fila in grupo.iterrows():
                doc = format_doc_id(fila.get('TPO_DOC'), fila.get('SERIE_DOC'), fila.get('NRO_DOC'))
                if doc not in docs_unicos:
                    docs_unicos.append(doc)
        
        fecha_min = df["FECHA_ORIG"].min()
        fecha_max = df["FECHA_ORIG"].max()
        
        return items, docs_unicos, fecha_min, fecha_max

    @staticmethod
    def calcular_totales(items: List[Dict], incluir_igv: bool = True) -> Dict:
        """Calcula subtotal, igv y total."""
        subtotal = sum(item["SOLES"] for item in items)
        igv = subtotal * IGV_PERCENT if incluir_igv else 0
        total = subtotal + igv
        
        return {
            "subtotal": subtotal,
            "igv": igv,
            "total": total,
            "items_count": len(items)
        }