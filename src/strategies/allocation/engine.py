import re
import pandas as pd
import logging
from typing import List, Dict, Tuple, Optional
from src.core.models import ProcessedItem
from src.core.utils import split_doc_id, build_doc_full, safe_int, safe_float

logger = logging.getLogger(__name__)


def _convertir_porcentaje(p) -> float:
    """Convierte un descuento capturado a FRACCION (0.05 = 5%).

    Delegado al helper canónico ``normalizar_porcentaje``: acepta
    fracciones, '5%' y puntos de porcentaje (5 = 5%, con alerta AL04 en
    el caller). El corte anterior era ``>= 0.5``, que partía a la mitad
    cualquier descuento real de 50% o más.
    """
    # Handle array-like inputs safely to avoid ambiguous truth value errors
    if hasattr(p, "__len__") and not isinstance(p, (str, bytes)):
        return 0.0
    from src.core.utils import normalizar_porcentaje

    return normalizar_porcentaje(p)[0]


def _convertir_porcentaje_y_alerta(p) -> tuple[float, "str | None"]:
    """Igual que _convertir_porcentaje pero devuelve tambien el codigo AL04/AL10."""
    if hasattr(p, "__len__") and not isinstance(p, (str, bytes)):
        return 0.0, None
    from src.core.utils import normalizar_porcentaje

    return normalizar_porcentaje(p)


class AllocationEngine:
    """
    Motor de asignacion FIFO de facturas para sustentar reconocimientos.
    """

    def __init__(
        self,
        sort_mode: str = "fecha_desc",
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        forzar_cantidad: bool = True,
    ):
        self.sort_mode = sort_mode
        self.fecha_desde = fecha_desde
        self.fecha_hasta = fecha_hasta
        self.forzar_cantidad = forzar_cantidad

    def assign(
        self,
        requerimientos: pd.DataFrame,
        historial: pd.DataFrame,
    ) -> Tuple[List[ProcessedItem], List[str]]:
        df_hist = historial.copy()

        if self.fecha_desde is not None:
            df_hist = df_hist[df_hist["FECHA"] >= pd.Timestamp(self.fecha_desde)]
        if self.fecha_hasta is not None:
            df_hist = df_hist[df_hist["FECHA"] <= pd.Timestamp(self.fecha_hasta)]

        # Cache por SKU (pre-agrupar para evitar filtrados O(N) repetitivos)
        cache = {str(k): v for k, v in df_hist.groupby("CODIGO")}

        resultados = []
        todos_documentos = set()

        cols = [c.upper() for c in requerimientos.columns]
        if "CODIGO" not in cols:
            raise ValueError("Requerimientos debe tener columna CODIGO")
        rename_map = {}
        for c in requerimientos.columns:
            if c.upper() == "CODIGO":
                rename_map[c] = "CODIGO"
            elif c.upper() in ("CANTIDAD_NC", "CANTIDAD"):
                rename_map[c] = "CANTIDAD_NC"
            elif c.upper() in ("PORCENTAJE_DESC", "DESCUENTO"):
                rename_map[c] = "PORCENTAJE_DESC"
        req = requerimientos.rename(columns=rename_map)

        # Limpiar fila de totales
        if not req.empty:
            last = req.iloc[-1].astype(str).str.contains(r"TOTAL|TOTALES", case=False, na=False)
            if last.any():
                req = req.iloc[:-1].reset_index(drop=True)

        for _, fila in req.iterrows():
            codigo_raw = str(fila.get("CODIGO", "")).strip()
            if codigo_raw == "" or codigo_raw.lower() == "nan":
                resultados.append(
                    ProcessedItem(
                        CODIGO="N/A",
                        ARTICULO="FILA SIN CODIGO",
                        CANTIDAD_SOLICITADA=0,
                        CANTIDAD_REAL_ENCONTRADA=0,
                        PRECIO_UNITARIO=0,
                        MONTO_DESCUENTO_UNITARIO=0,
                        PRECIO_NETO_FINAL=0,
                        SUBTOTAL_DESCUENTO=0,
                        PORCENTAJE_APLICADO=0,
                        DOCUMENTOS=[],
                        STATUS="ERROR: Fila vacia o sin codigo de articulo",
                    )
                )
                continue

            raw_cant = str(fila.get("CANTIDAD_NC", 0)).upper().replace("O", "0").strip()
            try:
                cant_val = int(float(pd.to_numeric(raw_cant, errors="coerce") or 0))
            except (ValueError, TypeError):
                cant_val = 0

            porcentaje_val, cod_pct = _convertir_porcentaje_y_alerta(fila.get("PORCENTAJE_DESC", 0))

            warning_prefix = ""
            if cod_pct == "AL04":
                warning_prefix = (
                    "AL04: Porcentaje fuera de rango; interpretado como puntos de porcentaje. "
                )
            elif cant_val <= 0:
                warning_prefix = "INFO: Cantidad vacia o cero. "
            elif porcentaje_val <= 0:
                warning_prefix = "INFO: Descuento vacio o cero. "
            elif porcentaje_val > 1.0:
                warning_prefix = "INFO: Descuento excede 100%. "

            item = self._procesar_articulo(
                codigo_raw, cant_val, porcentaje_val, cache, self.forzar_cantidad
            )

            if warning_prefix and "ERROR" not in item.STATUS:
                item.STATUS = f"{warning_prefix}{item.STATUS}"

            resultados.append(item)
            for doc in item.DOCUMENTOS:
                todos_documentos.add(doc)

        logger.info(
            f"AllocationEngine: {len(resultados)} items procesados, {len(todos_documentos)} docs"
        )
        return resultados, sorted(list(todos_documentos))

    def _procesar_articulo(
        self,
        codigo: str,
        cantidad_nc: int,
        porcentaje_desc: float,
        cache: dict,
        forzar: bool,
    ) -> ProcessedItem:
        codigo_limpio = re.sub(r"\.0$", "", str(codigo)).strip()
        hist_art = cache.get(codigo_limpio, pd.DataFrame())

        if hist_art.empty:
            return ProcessedItem(
                codigo_limpio,
                "NO ENCONTRADO",
                cantidad_nc,
                0,
                0,
                0,
                0,
                0,
                porcentaje_desc,
                [],
                "ERROR: No en historial",
            )

        reciente = hist_art.iloc[0]
        nombre = reciente["ARTICULO"]

        if cantidad_nc <= 0:
            p_ref = round(float(reciente["PRECIO_UNITARIO"]), 2)
            return ProcessedItem(
                codigo_limpio,
                nombre,
                0,
                0,
                p_ref,
                0,
                p_ref,
                0,
                porcentaje_desc,
                [],
                "INFO: Cantidad vacia",
                NUMERO=str(reciente["NUMERO"]),
                SERIE=str(reciente["SERIE"]),
                COD_LINEA=str(reciente.get("COD_LINEA", "")),
                LINEA=str(reciente.get("LINEA", "")),
            )

        # Sort historical invoices by the configured sort_mode before allocation
        hist_art = self._ordenar_historial(hist_art)

        asig = self._ejecutar_asignacion_fifo(hist_art, cantidad_nc)
        return self._finalizar_item(
            codigo_limpio, nombre, cantidad_nc, porcentaje_desc, asig, reciente, forzar
        )

    def _ordenar_historial(self, hist_art: pd.DataFrame) -> pd.DataFrame:
        sort_mode = getattr(self, "sort_mode", "fecha_desc")
        if sort_mode == "fecha_asc":
            return hist_art.sort_values("FECHA", ascending=True)
        elif sort_mode == "fecha_desc":
            return hist_art.sort_values("FECHA", ascending=False)
        elif sort_mode == "cantidad_asc":
            return hist_art.sort_values("CANTIDAD", ascending=True)
        elif sort_mode == "cantidad_desc":
            return hist_art.sort_values("CANTIDAD", ascending=False)
        return hist_art

    def _ejecutar_asignacion_fifo(self, hist_art: pd.DataFrame, cantidad_nc: int) -> Dict:
        res = {
            "docs": [],
            "doc_cantidad": {},
            "precios": set(),
            "asignado": 0,
            "restante": cantidad_nc,
            "valor_soporte_total": 0.0,
            "doc_montos": {},
        }
        for _, fila in hist_art.iterrows():
            if res["restante"] <= 0:
                break
            cant_fila = safe_int(fila["CANTIDAD"])
            tomar = min(cant_fila, res["restante"])
            tipo, serie, nro = split_doc_id(fila["TIPO_DOC"], fila["SERIE"], fila["NUMERO"])
            doc_full = build_doc_full(tipo, serie, nro)
            if doc_full not in res["docs"]:
                res["docs"].append(doc_full)
                res["doc_cantidad"][doc_full] = 0
                res["doc_montos"][doc_full] = 0

            precio_fila = safe_float(fila["PRECIO_UNITARIO"])
            monto = tomar * precio_fila
            res["doc_cantidad"][doc_full] += tomar
            res["doc_montos"][doc_full] += monto
            res["valor_soporte_total"] += monto
            res["precios"].add(round(precio_fila, 2))
            res["asignado"] += tomar
            res["restante"] -= tomar
        return res

    def _finalizar_item(self, cod, nom, cant_nc, porc, asig, reciente, forzar) -> ProcessedItem:
        precio_ref = round(safe_float(reciente["PRECIO_UNITARIO"]), 5)
        status = "OK"

        if asig["restante"] > 0:
            if asig["asignado"] == 0:
                status = (
                    f"ADVERTENCIA SE USARON {cant_nc} UNIDADES: Sin sustento disponible"
                    if forzar
                    else "ADVERTENCIA SE USARON 0 UNIDADES: Sin sustento disponible"
                )
            else:
                if forzar:
                    status = f"ADVERTENCIA SE USARON {cant_nc} UNIDADES: Sustentadas {int(asig['asignado'])}, pendientes {int(asig['restante'])}"
                else:
                    status = f"ADVERTENCIA SE USARON {int(asig['asignado'])} UNIDADES: Encontradas {int(asig['asignado'])}, pendientes {int(asig['restante'])}"
        elif len(asig["precios"]) > 1:
            p_min = min(asig["precios"])
            p_max = max(asig["precios"])
            status = f"INFO: Precios variables (Rango: {p_min:.5f}-{p_max:.5f}). Se uso el mas reciente: {precio_ref:.5f}"

        m_desc_u = round(precio_ref * porc, 5)
        cant_f = int(cant_nc if forzar else asig["asignado"])

        doc_ref = asig["docs"][0] if asig["docs"] else ""

        return ProcessedItem(
            CODIGO=cod,
            ARTICULO=nom,
            CANTIDAD_SOLICITADA=cant_nc,
            CANTIDAD_REAL_ENCONTRADA=cant_f,
            PRECIO_UNITARIO=precio_ref,
            MONTO_DESCUENTO_UNITARIO=m_desc_u,
            PRECIO_NETO_FINAL=round(precio_ref - m_desc_u, 5),
            SUBTOTAL_DESCUENTO=round(m_desc_u * cant_f, 2),
            PORCENTAJE_APLICADO=porc,
            DOCUMENTOS=asig["docs"],
            STATUS=status,
            NUMERO=str(reciente["NUMERO"]),
            SERIE=str(reciente["SERIE"]),
            COD_LINEA=str(reciente.get("COD_LINEA", "")),
            LINEA=str(reciente.get("LINEA", "")),
            FACTURA_REF=doc_ref,
            DOCUMENTOS_CANTIDAD=asig.get("doc_cantidad", {}),
            DOCUMENTOS_MONTOS=asig.get("doc_montos", {}),
            VALOR_SOPORTE_TOTAL=asig.get("valor_soporte_total", 0),
        )
