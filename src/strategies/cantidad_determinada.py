"""Motor único de reconocimiento por CANTIDAD DETERMINADA (caso VRS).

Absorbe lo que antes eran dos motores separados: el antiguo
``StockPriceDifference`` (VRS / ``diferencia_stock``) y el antiguo
``CantidadDeterminada`` (CDT / ``diferencia_cantidad``).

Regla de cálculo: manda el archivo de SKU; el historial la sustenta.

Insumo (archivo combinado, una fila por SKU)::

    SKU | CANTIDAD | PRECIO_BASE | desc01..desc08

``CANTIDAD`` es exactamente cuántas unidades se reconocen por SKU y
``PRECIO_BASE`` + la cadena de descuentos dan el precio de lista vigente. Un
segundo archivo con solo ``SKU + CANTIDAD`` pisa las cantidades del
combinado (override opcional).

El reparto contra facturas sigue ``sort_mode`` (``fecha_asc`` = FIFO, consume
lo más viejo; ``fecha_desc`` = LIFO) y nunca asigna más de lo facturado: el
exceso queda como alerta AL09. Precio: lista vigente con cadena de
descuentos vs precio histórico, misma regla que DC.

Ambas modalidades: individual (una fila por línea de factura asignada) y
consolidado (una fila por SKU, monto por suma exacta). Las tres columnas de
cobertura — Cantidad Facturada, Stock Sustentado y % Stock Restante — se
emiten siempre.
"""

import logging
import re

import numpy as np
import pandas as pd

from src.core.utils import PRECIO_DECIMALES
from src.core.nc_reconciliation import (
    alertas_reconciliacion,
    normalizar_cliente,
    normalizar_sku,
)
from src.domain import (
    BusinessAlert,
    ExpedienteComercial,
    RecognitionResult,
    generar_texto_alerta,
)
from src.strategies.price_difference import (
    PriceDifferenceStrategy,
    _build_factura,
)

logger = logging.getLogger(__name__)

# Columnas de salida en modalidad individual (mismas que DC → renderer
# con fallback a COLUMNAS_POR_TIPO["diferencia_precio"]).
_COLS_INDIVIDUAL = [
    "CODIGO",
    "ARTICULO",
    "CANTIDAD",
    "SOLES",
    "PRECIO_HIST",
    "PRECIO_BASE",
    "DESCUENTO_COMPUESTO",
    "PRECIO_NETO",
    "DIFERENCIA",
    "MONTO_NC",
    "FACTURA",
    "ALERTA",
    "AUDITORIA_NC",
    "COD_CLIENTE",
    "DOC_CLIENTE",
]

# Columnas de cobertura: se emiten SIEMPRE (VRS las necesita para exponer
# cuánto de lo determinado quedó sustentado). En individual se repiten por
# fila del mismo SKU; en consolidado hay una fila por SKU.
_COLS_COBERTURA = [
    "Cantidad Facturada",
    "Cantidad Disponible",
    "Stock Sustentado",
    "% Stock Restante",
]

# Nombres aceptados para la cantidad, en orden de prioridad.
_COLS_CANTIDAD = (
    "CANTIDAD_NC",
    "CANTIDAD",
    "CANTIDAD_STOCK",
    "CANTIDAD_A_RECONOCER",
    "STOCK",
    "CANTIDAD_REQ",
)


def _a_sku_canonico(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la columna de código a ``SKU``.

    Acepta el archivo crudo (``SKU``) y el ya normalizado por el pipeline
    (``CODIGO``), que es lo que deja el ``header_map`` del schema VRS.
    """
    if "SKU" in df.columns:
        return df
    if "CODIGO" in df.columns:
        return df.rename(columns={"CODIGO": "SKU"})
    return df


class CantidadDeterminadaStrategy:
    """Precio de lista vs histórico sobre la cantidad determinada por SKU.

    A diferencia de ``diferencia_precio`` (manda el historial), acá manda el
    archivo de SKU: se consume el historial en el orden de ``sort_mode``
    hasta agotar la cantidad pedida por SKU.
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df_hist = expediente.datos
        config = expediente.contexto.config or {}
        alertas: list = []
        pd_helper = PriceDifferenceStrategy()

        def _error(msg: str) -> RecognitionResult:
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=[
                    BusinessAlert(
                        tipo="error", severidad="alta", mensaje=msg, motor="CantidadDeterminada"
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        if df_hist is None or df_hist.empty:
            return _error("Historial vacío")

        # VRS comparte la política factura×SKU de DC. En consolidado el
        # reconciliador es informativo; en individual sólo ajusta matches
        # exactos habilitados por los checks del historial.
        df_hist = pd_helper._aplicar_reconciliacion_notas(df_hist, config)

        # ── condiciones: [0] combinado (precio + cantidad) ───────────────
        #                 [1] override opcional (solo cantidades)
        combined = expediente.condiciones[0] if len(expediente.condiciones) > 0 else None
        override = expediente.condiciones[1] if len(expediente.condiciones) > 1 else None
        if combined is None or not isinstance(combined, pd.DataFrame) or combined.empty:
            return _error("Falta la lista de precios (condiciones[0])")
        # El pipeline normaliza según el schema YAML: SKU→CODIGO y
        # CANTIDAD→CANTIDAD_NC. Aceptamos ambos nombres y trabajamos con "SKU".
        combined = _a_sku_canonico(combined)
        if "SKU" not in combined.columns or "PRECIO_BASE" not in combined.columns:
            return _error("Lista sin SKU o PRECIO_BASE")

        override_sku = None
        if override is not None and isinstance(override, pd.DataFrame) and not override.empty:
            override = _a_sku_canonico(override)
            col_ov = next((c for c in _COLS_CANTIDAD if c in override.columns), None)
            if col_ov is None or "SKU" not in override.columns:
                return _error("El archivo de override de cantidades necesita SKU + CANTIDAD")
            override_sku = override[["SKU", col_ov]].copy()

        # ── lista con cadena de descuentos (mismo motor que DC) ──────
        cols_desc = sorted(
            [c for c in combined.columns if re.match(r"^DESC\d+$", c, re.IGNORECASE)],
            key=lambda c: int(re.search(r"\d+", c).group()),
        )
        precio_col = "PRECIO_BASE"
        lista_norm = combined.copy()
        # Si el archivo llega crudo (sin pasar por el pipeline) los números
        # vienen como texto y la cadena de descuentos revienta con TypeError.
        lista_norm["PRECIO_BASE"] = pd.to_numeric(
            lista_norm["PRECIO_BASE"], errors="coerce"
        ).fillna(0)
        for c in cols_desc:
            lista_norm[c] = pd.to_numeric(lista_norm[c], errors="coerce")
        if cols_desc:
            from src.validation.normalization import NormalizationEngine

            norm = NormalizationEngine({"columnas_descuento": {"pattern": "^DESC\\d+$"}})
            lista_norm = norm.aplicar_cadena_descuentos(combined)
            alertas.extend(norm.alertas)  # AL04/AL10 de la cadena
            if "PRECIO_CALCULADO" in lista_norm.columns:
                precio_col = "PRECIO_CALCULADO"
        # Defensa de cruce: la plantilla puede traer ' A001 ' con espacios.
        lista_norm["SKU"] = lista_norm["SKU"].astype(str).str.strip()
        hist = df_hist
        if (hist["CODIGO"].astype(str).str.strip() != hist["CODIGO"].astype(str)).any():
            hist = hist.copy()
            hist["CODIGO"] = hist["CODIGO"].astype(str).str.strip()

        # ── cantidad exacta por SKU (una fila por SKU; >0) ───────────
        # El combinado aporta la cantidad de cada SKU; el override pisa solo
        # los SKUs que trae (si no trae ninguno, manda el combinado).
        # Si el combinado no trae cantidad (flujo legacy lista + cantidades),
        # las cantidades salen únicamente del override.
        req = pd.DataFrame(columns=["SKU", "CANTIDAD"])
        col_cant = next((c for c in _COLS_CANTIDAD if c in lista_norm.columns), None)
        if col_cant is not None:
            req = lista_norm[["SKU", col_cant]].rename(columns={col_cant: "CANTIDAD"})
        if override_sku is not None:
            req_ov = override_sku.rename(columns={override_sku.columns[-1]: "CANTIDAD"})
            req = pd.concat([req, req_ov], ignore_index=True).drop_duplicates(
                subset="SKU", keep="last"
            )
        req["SKU"] = req["SKU"].astype(str).str.strip()
        req["CANTIDAD"] = pd.to_numeric(req["CANTIDAD"], errors="coerce").fillna(0)
        req = req[req["CANTIDAD"] > 0].groupby("SKU", as_index=False)["CANTIDAD"].sum()
        req = req[req["SKU"] != ""]
        if req.empty:
            return _error("Archivo de cantidad sin montos > 0")

        rec_map = config.get("reconciliacion_nc") or {}
        req_skus = {normalizar_sku(s) for s in req["SKU"]}
        pares_hist = set()
        for _, row in df_hist.iterrows():
            sku_key = normalizar_sku(row.get("CODIGO"))
            if sku_key not in req_skus:
                continue
            invoice = str(row.get("DOC_ID", "") or "").strip()
            client = normalizar_cliente(row.get("COD_CLIENTE", ""))
            pares_hist.add(
                (client, invoice, sku_key)
                if any(len(k) == 3 for k in rec_map)
                else (invoice, sku_key)
            )
        alertas.extend(
            alertas_reconciliacion(rec_map, pares=pares_hist, motor="CantidadDeterminada")
        )

        # ── historial de esos SKUs con unidades (>0) y precio de lista ─
        cols_merge = list(dict.fromkeys(["SKU", "PRECIO_BASE", precio_col] + cols_desc))
        merged_todo = hist.merge(
            lista_norm[cols_merge].rename(columns={"SKU": "CODIGO"}), on="CODIGO", how="inner"
        )
        facturado_por_sku = (
            merged_todo.groupby("CODIGO", sort=False)["CANTIDAD_FACTURADA"]
            .sum()
            .astype(float)
            .to_dict()
        )
        disponible_por_sku = (
            merged_todo.groupby("CODIGO", sort=False)["CANTIDAD"].sum().astype(float).to_dict()
        )
        merged = merged_todo
        merged = merged[pd.to_numeric(merged["CANTIDAD"], errors="coerce").fillna(0) > 0].copy()
        if merged.empty:
            for _, pedido in req.iterrows():
                sku = str(pedido["SKU"])
                total_disponible = float(disponible_por_sku.get(sku, 0.0))
                solicitado = float(pedido["CANTIDAD"])
                if total_disponible < solicitado:
                    msg = generar_texto_alerta(
                        "AL09", sku=sku, asignado=total_disponible, solicitado=solicitado
                    )
                    alertas.append(
                        BusinessAlert(
                            codigo="AL09",
                            tipo="warning",
                            severidad="media",
                            sku=sku,
                            mensaje=msg,
                            motor="CantidadDeterminada",
                        )
                    )
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=alertas
                + [
                    BusinessAlert(
                        tipo="warning",
                        severidad="media",
                        mensaje="Sin unidades disponibles para sustentar las cantidades solicitadas",
                        motor="CantidadDeterminada",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        # ── consumo en el orden de sort_mode (FIFO/LIFO) ──────────────
        asc = str(config.get("sort_mode", "fecha_asc")) == "fecha_asc"
        merged["_f"] = pd.to_datetime(merged.get("FECHA"), errors="coerce")
        merged = merged.sort_values("_f", ascending=asc, na_position="last", kind="mergesort")

        # Total facturado por SKU (antes del reparto) para la cobertura.
        filas = []
        excesos: dict = {}  # sku -> (asignado, solicitado)
        for sku, g in merged.groupby("CODIGO", sort=False):
            fila = req[req["SKU"] == str(sku)]
            if fila.empty:
                continue
            restante = float(fila["CANTIDAD"].iloc[0])
            pedido = restante
            for _, row in g.iterrows():
                if restante <= 0:
                    break
                toma = min(restante, float(row["CANTIDAD"]))
                if toma <= 0:
                    continue
                f = row.copy()
                f["_asignado"] = toma
                filas.append(f)
                restante -= toma
            if restante > 0:
                excesos[str(sku)] = (pedido - restante, pedido)

        if not filas:
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=[
                    BusinessAlert(
                        tipo="warning",
                        severidad="media",
                        mensaje="Sin facturas para las cantidades determinadas",
                        motor="CantidadDeterminada",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        alloc = pd.DataFrame(filas).reset_index(drop=True)
        # Precio unitario de la línea (antes de pisar CANTIDAD con lo
        # asignado): PRECIO_HIST = SOLES/CANTIDAD de la factura.
        qty_original = pd.to_numeric(
            alloc.get("CANTIDAD_FACTURADA", alloc["CANTIDAD"]), errors="coerce"
        ).fillna(0)
        soles_ajustados = pd.to_numeric(
            alloc.get("SOLES_CALCULO", alloc["SOLES"]), errors="coerce"
        ).fillna(0)
        alloc["PRECIO_HIST"] = np.where(qty_original > 0, soles_ajustados / qty_original, 0).round(
            PRECIO_DECIMALES
        )
        alloc["PRECIO_NETO"] = alloc[precio_col]
        alloc["DESCUENTO_COMPUESTO"] = np.where(
            alloc["PRECIO_BASE"] > 0, (1 - alloc["PRECIO_NETO"] / alloc["PRECIO_BASE"]).round(4), 0
        )
        alloc["DIFERENCIA"] = (alloc["PRECIO_HIST"] - alloc["PRECIO_NETO"]).round(PRECIO_DECIMALES)
        # Cantidad EXACTA repartida (nunca más que lo facturado) — es la
        # cantidad visible del resultado en ambas modalidades.
        alloc["CANT_ASIG"] = alloc["_asignado"]
        alloc["MONTO_NC"] = (alloc["DIFERENCIA"].clip(lower=0) * alloc["CANT_ASIG"]).round(2)
        alloc["CANTIDAD"] = alloc["CANT_ASIG"]
        alloc["FACTURA"] = alloc.apply(_build_factura, axis=1)

        # ── cobertura (siempre presente) ───────────────────────────────
        _agregar_cobertura(alloc, req, facturado_por_sku, disponible_por_sku)

        # ── alertas de precio (mismas reglas que DC) ──────────────────
        def _clase(r):
            dif = float(r.get("DIFERENCIA", 0))
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANT_ASIG", 0))
            pb = float(r.get("PRECIO_BASE", 0))
            if dif > 0 and not pd_helper._dentro_de_tol_redondeo(dif, cant, pb):
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            if dif > 0 and pd_helper._dentro_de_tol_redondeo(dif, cant, pb):
                return generar_texto_alerta(
                    "AL11",
                    sku=sku,
                    diferencia_unitaria=dif,
                    diferencia_total=dif * cant,
                    cantidad=cant,
                )
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        alloc["ALERTA"] = alloc.apply(_clase, axis=1)
        alloc["ALERTA"] = [
            " | ".join(x for x in (str(base or "").strip(), str(note or "").strip()) if x)
            for base, note in zip(
                alloc["ALERTA"], alloc.get("ALERTA_NOTAS", pd.Series("", index=alloc.index))
            )
        ]
        alloc["AUDITORIA_NC"] = alloc.get("AUDITORIA_NOTAS", pd.Series("", index=alloc.index))

        # ── exceso: cantidad determinada > lo facturado (AL09) ────────
        for sku, (asig, pedido) in excesos.items():
            txt = generar_texto_alerta("AL09", sku=sku, asignado=asig, solicitado=pedido)
            alertas.append(
                BusinessAlert(
                    codigo="AL09",
                    tipo="warning",
                    severidad="media",
                    sku=sku,
                    mensaje=txt,
                    motor="CantidadDeterminada",
                )
            )

        modalidad = str(config.get("modalidad", "individual"))
        if modalidad == "consolidado":
            # SOLES prorreado a lo asignado: _consolidar_por_sku suma los
            # SOLES COMPLETOS de cada linea, pero aca solo se tomo una parte
            # de la unidad. Sin esto el "TOTAL FACTURA" del Cálculo sale
            # inflado (120u de lineas de 350 y 175 -> 525 en vez de 420 =
            # 120 x 3.50). En DC no hace falta porque ahi se usan todas
            # las unidades de la linea.
            alloc["SOLES"] = (alloc["PRECIO_HIST"] * alloc["CANT_ASIG"]).round(2)
            df_result = pd_helper._consolidar_por_sku(
                alloc, sort_mode=config.get("sort_mode", "fecha_asc"), alertas=alertas
            )
            # La cobertura se recalcula sobre la fila consolidada (una por
            # SKU): _consolidar_por_sku no arrastra las columnas extras.
            _propagar_cobertura_consolidada(df_result, facturado_por_sku, disponible_por_sku, req)

            # Fila consolidada avisa el exceso (misma alerta de los docs).
            # _consolidar_por_sku renombra CODIGO → SKU en su salida.
            def _con_exceso(r):
                sku_fila = str(r.get("CODIGO", "") or r.get("SKU", ""))
                txt = ""
                for sku, (asig, pedido) in excesos.items():
                    if sku_fila == sku:
                        txt = " · " + generar_texto_alerta(
                            "AL09", sku=sku, asignado=asig, solicitado=pedido
                        )
                return str(r.get("ALERTA", "")) + txt

            if excesos and "ALERTA" in df_result.columns:
                df_result["ALERTA"] = df_result.apply(_con_exceso, axis=1)
        else:
            # AL09 visible en la fila individual, igual que en consolidado:
            # si no, el exceso solo aparece en el panel de alertas.
            if excesos and "ALERTA" in alloc.columns:

                def _fila_exceso(r):
                    sku_fila = str(r.get("CODIGO", ""))
                    for sku, (asig, pedido) in excesos.items():
                        if sku_fila == sku:
                            return (
                                str(r.get("ALERTA", ""))
                                + " · "
                                + generar_texto_alerta(
                                    "AL09", sku=sku, asignado=asig, solicitado=pedido
                                )
                            )
                    return str(r.get("ALERTA", ""))

                alloc["ALERTA"] = alloc.apply(_fila_exceso, axis=1)
            # Convención de columnas: el render espera SKU (DC renombra
            # CODIGO→SKU al salir, igual que ferias/stock).
            cols = _COLS_INDIVIDUAL + _COLS_COBERTURA
            df_result = (
                alloc[[c for c in cols if c in alloc.columns]]
                .rename(columns={"CODIGO": "SKU"})
                .reset_index(drop=True)
            )

        sku_col = "CODIGO" if "CODIGO" in df_result.columns else "SKU"
        resumen = {
            "total_nc": float(df_result.get("MONTO_NC", pd.Series(dtype=float)).sum() or 0),
            "skus_afectados": int(df_result[sku_col].nunique()) if not df_result.empty else 0,
        }
        docs_unicos = sorted(
            {str(d) for d in df_result.get("FACTURAS", pd.Series(dtype=str)).dropna()}
            | {str(d) for d in df_result.get("FACTURA", pd.Series(dtype=str)).dropna()}
        )
        docs_unicos = [d for d in docs_unicos if d and d.lower() not in ("nan", "none")]
        # doc_ref para el naming del expediente, misma convención que DC:
        # la factura que más valor aporta (el split de expediente cae a
        # consolidado con ID "EXP-VRS-<cli>--<fecha>" si falta).
        if modalidad == "consolidado" and "FACTURA" in alloc.columns:
            try:
                por_doc = alloc.groupby("FACTURA")["SOLES"].sum()
                if not por_doc.empty:
                    resumen["doc_ref"] = str(por_doc.idxmax())
            except Exception:
                pass
        if docs_unicos and modalidad == "consolidado":
            resumen["documentos_unicos"] = docs_unicos
            resumen["titulo_documentos"] = "FACTURAS COMPROMETIDAS"
        logger.info(
            "CantidadDeterminada: %d filas, %d SKUs, excesos %d",
            len(df_result),
            resumen["skus_afectados"],
            len(excesos),
        )
        return RecognitionResult(dataframe=df_result, alertas=alertas, resumen=resumen)


def _agregar_cobertura(
    alloc: pd.DataFrame,
    req: pd.DataFrame,
    facturado_por_sku: dict,
    disponible_por_sku: dict | None = None,
) -> None:
    """Escribe las columnas de cobertura (in-place).

    Se calculan por SKU (no por fila) para que un SKU repartido en varias
    facturas muestre el mismo total en todas sus filas, igual que la vista
    consolidada.
    """
    pedido_por_sku = dict(
        zip(
            req["SKU"].astype(str),
            pd.to_numeric(req["CANTIDAD"], errors="coerce").fillna(0),
        )
    )
    sustentado_por_sku = alloc.groupby("CODIGO", sort=False)["CANT_ASIG"].sum()
    skus = alloc["CODIGO"].astype(str)
    alloc["Cantidad Facturada"] = skus.map(lambda s: float(facturado_por_sku.get(s, 0.0)))
    disponible_por_sku = disponible_por_sku or facturado_por_sku
    alloc["Cantidad Disponible"] = skus.map(lambda s: float(disponible_por_sku.get(s, 0.0)))
    alloc["Stock Sustentado"] = skus.map(lambda s: float(sustentado_por_sku.get(s, 0.0)))
    alloc["% Stock Restante"] = [
        round(float(pedido_por_sku.get(s, 0.0)) / f * 100, 1) if f > 0 else 0.0
        for s, f in zip(skus, alloc["Cantidad Disponible"])
    ]


def _propagar_cobertura_consolidada(
    df_result: pd.DataFrame, facturado_por_sku: dict, disponible_por_sku: dict, req: pd.DataFrame
) -> None:
    """Reconstruye la cobertura en la salida consolidada (una fila por SKU)."""
    if df_result.empty:
        return
    col_sku = "SKU" if "SKU" in df_result.columns else "CODIGO"
    if col_sku not in df_result.columns:
        return
    pedido_por_sku = dict(
        zip(
            req["SKU"].astype(str),
            pd.to_numeric(req["CANTIDAD"], errors="coerce").fillna(0),
        )
    )
    skus = df_result[col_sku].astype(str)
    df_result["Cantidad Facturada"] = skus.map(lambda s: float(facturado_por_sku.get(s, 0.0)))
    df_result["Cantidad Disponible"] = skus.map(lambda s: float(disponible_por_sku.get(s, 0.0)))
    df_result["Stock Sustentado"] = pd.to_numeric(
        df_result.get("CANTIDAD", pd.Series(0.0, index=df_result.index)), errors="coerce"
    ).fillna(0)
    df_result["% Stock Restante"] = [
        round(float(pedido_por_sku.get(s, 0.0)) / f * 100, 1) if f > 0 else 0.0
        for s, f in zip(skus, df_result["Cantidad Disponible"])
    ]
