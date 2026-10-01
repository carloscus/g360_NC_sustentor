"""Config builder for RecogniciónView._ejecutar.

Centraliza la lógica de construir el dict `config` y el DataFrame `datos_exp`
a partir de los valores de los controles UI. Extraído de _ejecutar para
reducir la complejidad de RecognocimientoView.
"""

from datetime import datetime
from typing import Any
import pandas as pd
from src.core.utils import read_erp_file


def build_config(tipo_actual: str, ui: dict[str, Any]) -> dict:
    """Construye el dict `config` para PipelineContext a partir de valores UI.

    Args:
        tipo_actual: Clave del tipo de operación (ej: 'diferencia_precio').
        ui: Diccionario con valores de controles UI. Keys esperadas:
            - modalidad, factura_id, fecha_desde/hasta, fecha_desde_pd/hasta_pd
            - mecanica, mecanica_personalizada, meta_monto, rebate_pct
            - requerimientos_paths, sort_mode, forzar_cantidad
            - fp_desde, fp_hasta, cliente_fp
            - stock_cliente_path (override opcional de cantidades en VRS)
            - factura_ci, factura_df
            - sku_filter_path, cliente_pd, chk_incluir_nc
            - cliente_pb, lineas_selected, categorias_nc

    Returns:
        Dict config listo para PipelineContext.config.
    """
    cfg = _get_tipo_config(tipo_actual)
    config = {}

    # Documento visible y documento usado son dos opciones distintas. Capture
    # el snapshot real de checks de UI; usar solo los defaults del Caso hacia
    # que el usuario pudiera cambiar los controles sin efecto.
    raw_hist = ui.get("historico_config") or {}
    doc_hist = {}
    for doc, default in {
        "facturas": (True, True),
        "nc": (True, False),
        "ndb": (True, False),
    }.items():
        value = raw_hist.get(doc, default)
        if isinstance(value, dict):
            show, use = value.get("mostrar", default[0]), value.get("usar", default[1])
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            show, use = value[0], value[1]
        else:
            show, use = default
        doc_hist[doc] = {"mostrar": bool(show), "usar": bool(use)}
    config["documentos_historial"] = doc_hist
    config["incluir_nc"] = doc_hist["nc"]["usar"]
    config["incluir_ndb"] = doc_hist["ndb"]["usar"]

    if cfg.get("tiene_modalidad", False):
        config["modalidad"] = ui.get("modalidad")
        if config["modalidad"] == "por_factura":
            config["factura_id"] = ui.get("factura_id")

    if cfg.get("tiene_periodo", False):
        config["fecha_desde"] = _parse_date(ui.get("fecha_desde"))
        config["fecha_hasta"] = _parse_date(ui.get("fecha_hasta"))

    if cfg.get("tiene_mecanica", False):
        mecanica = ui.get("mecanica", "12+1")
        if mecanica == "personalizado":
            config["mecanica"] = ui.get("mecanica_personalizada") or "12+1"
        else:
            config["mecanica"] = mecanica or "12+1"

    if cfg.get("tiene_meta", False):
        config["meta_monto"] = _safe_float(ui.get("meta_monto"))
        config["porcentaje_rebate"] = _safe_float(ui.get("rebate_pct"))

    if tipo_actual in (
        "diferencia_precio",
        "descuento_precio",
        "diferencia_cantidad",
        "diferencia_stock",
    ):
        # El rango de fechas ya vino filtrado por el fragmento de busqueda.
        if tipo_actual == "diferencia_precio":
            config["sort_mode"] = ui.get("sort_mode_dc") or "fecha_desc"
            # Individual: una fila por línea de factura. Consolidado: una fila
            # por SKU (cantidad Σ, precio moda, monto por suma exacta).
            config["modalidad"] = ui.get("modalidad") or "individual"
        if tipo_actual in ("diferencia_cantidad", "diferencia_stock"):
            # FIFO (fecha_asc) por defecto: consume lo más viejo primero;
            # el mismo radio DC gobierna FIFO/LIFO de esta cadena.
            # VRS usa `diferencia_stock`; `diferencia_cantidad` queda como
            # alias oculto (ver src/ui/catalog.py TIPOS_ALIAS).
            config["sort_mode"] = ui.get("sort_mode_dc") or "fecha_asc"
            config["modalidad"] = ui.get("modalidad") or "individual"
        if tipo_actual == "descuento_precio":
            # DO hereda la regla de modalidad de DC/VRS: en consolidado las
            # NC/NDB solo detallan (informativas) y no ajustan precios.
            config["modalidad"] = ui.get("modalidad") or "individual"
        # Compatibilidad para los call-sites anteriores; la fuente canónica es
        # documentos_historial (NC/NDB independientes).
        config["incluir_nc"] = doc_hist["nc"]["usar"]

    if tipo_actual == "feria_preventa":
        config["ruta_requerimientos"] = [str(p) for p in (ui.get("requerimientos_paths") or [])]
        config["sort_mode"] = ui.get("sort_mode")
        config["forzar_cantidad"] = ui.get("forzar_cantidad")
        # FPE comparte la convención de DC/DO/VRS: individual reparte por
        # factura×SKU, consolidado agrupa por SKU. En consolidado las NC/NDB
        # solo detallan (ver src/core/nc_reconciliation.py).
        config["modalidad"] = ui.get("modalidad") or "individual"
        if ui.get("fp_desde"):
            config["fecha_desde"] = ui["fp_desde"]
        if ui.get("fp_hasta"):
            config["fecha_hasta"] = ui["fp_hasta"]
        cliente_fp = ui.get("cliente_fp")
        if cliente_fp:
            config["cliente"] = cliente_fp

    if tipo_actual == "anular_factura":
        config["factura_id"] = ui.get("factura_ci")

    if tipo_actual == "devolucion_fisica":
        # DF no usa lista de precios: el valor sale de la factura a la que se
        # asigna cada unidad devuelta (precio neto, con NC previas descontadas).
        config["modalidad"] = ui.get("modalidad") or "individual"
        # LIFO por defecto (la factura más reciente es la que se devuelve
        # primero). Usa el mismo radio que DC/VRS: "orden de asignación".
        config["sort_mode"] = ui.get("sort_mode_dc") or "fecha_desc"
        config["devoluciones"] = _leer_devoluciones(
            ui.get("devoluciones_path") or ui.get("stock_cliente_path")
        )

    if tipo_actual == "descuento_precio":
        config["factura_id"] = ui.get("factura_df")
        config["descuento_pct"] = _safe_float(ui.get("descuento_pct"))
        desc_path = ui.get("desc_file_path") or ui.get("sku_filter_path")
        if desc_path:
            sku_map = _read_sku_filter(desc_path)
            if sku_map is not None:
                config["sku_filter"] = sku_map

    if tipo_actual in (
        "diferencia_precio",
        "descuento_precio",
        "diferencia_cantidad",
        "diferencia_stock",
    ):
        config["incluir_nc"] = ui.get("incluir_nc", False)

    if tipo_actual == "bonificacion_promocion":
        if ui.get("sku_filter_path"):
            skus = _read_sku_list(ui["sku_filter_path"])
            if skus is not None:
                config["skus"] = skus

    if tipo_actual == "rebate_volumen":
        lineas = ui.get("lineas_selected") or []
        if lineas:
            config["lineas"] = lineas
        cats = ui.get("categorias_nc") or []
        config["categorias_nc"] = cats

    return config


def build_datos_exp(
    tipo_actual: str,
    df_historial: pd.DataFrame,
    config: dict,
    ui: dict[str, Any],
) -> pd.DataFrame:
    """Construye el DataFrame datos_exp para ExpedienteComercial.

    Args:
        tipo_actual: Clave del tipo de operación.
        df_historial: DataFrame del historial cargado.
        config: Dict config ya construido (se modifica en-place para nc_por_linea).
        ui: Diccionario con valores de controles UI.

    Returns:
        DataFrame filtrado para datos_exp.
    """
    datos_exp = df_historial.copy()

    if tipo_actual in (
        "diferencia_precio",
        "descuento_precio",
        "diferencia_cantidad",
        "diferencia_stock",
    ):
        # VRS entra por acá: mismo filtro de cliente/vendedor y mismas alertas
        # de devoluciones que DC (no se descarta ninguna línea en silencio).
        # DO y VRS comparten la misma reconciliación NC/NDB (FAE→precio,
        # devolución→cantidad) que DC.
        config["_reconciliar_nc_factura_sku"] = True
        datos_exp = _filter_pd(datos_exp, config, ui)

    elif tipo_actual == "feria_preventa":
        cliente_fp = config.get("cliente")
        if cliente_fp and "CLIENTE" in datos_exp.columns:
            datos_exp = datos_exp[
                datos_exp["CLIENTE"].astype(str).str.strip() == cliente_fp.strip()
            ]
        # Misma convención de reconciliación que DC/DO/VRS: las NC/NDB no se
        # asignan como stock al AllocationEngine, se reconcilian por
        # factura+SKU y las aplica el motor solo en individual.
        config["_reconciliar_nc_factura_sku"] = True
        datos_exp, config = _reconciliar_notas_factura(datos_exp, config, ui)

    elif tipo_actual == "bonificacion_promocion":
        datos_exp = _filter_bonificacion(datos_exp, config, ui)

    elif tipo_actual == "devolucion_fisica":
        # El cálculo se hace sobre las facturas; las NC/NDB previas no se
        # calculan, solo se resumen para descontar el precio neto de las
        # unidades ya devueltas (ver DevolucionFisicaStrategy).
        from src.core.detector import detectar_notas_en_historial

        ui_full = ui.get("df_historial_full")
        base = ui_full if ui_full is not None else datos_exp
        config["notas_por_factura"] = _resumen_notas_por_sku(detectar_notas_en_historial(base))
        if "TIPO_CLASE" in datos_exp.columns:
            datos_exp = datos_exp[
                datos_exp["TIPO_CLASE"].astype(str).str.lower() == "factura"
            ].copy()
        cliente_df = ui.get("cliente_pb")
        if cliente_df and "CLIENTE" in datos_exp.columns:
            datos_exp = datos_exp[
                datos_exp["CLIENTE"].astype(str).str.strip() == str(cliente_df).strip()
            ]

    elif tipo_actual == "rebate_volumen":
        datos_exp = _build_datos_rebate(datos_exp, config, ui)

    return datos_exp


def _get_tipo_config(tipo_actual: str) -> dict:
    from src.ui.reconocimiento_config import TIPO_CONFIG

    return TIPO_CONFIG.get(tipo_actual, {})


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value), "%d/%m/%Y")
    except (ValueError, TypeError):
        return None


def _safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def _read_sku_filter(path: str) -> dict | None:
    try:
        df_sku = read_erp_file(path)
        sku_col = "CODIGO_SKU" if "CODIGO_SKU" in df_sku.columns else df_sku.columns[0]
        pct_col = "DESCUENTO_PORCENTAJE" if "DESCUENTO_PORCENTAJE" in df_sku.columns else None
        sku_map = {}
        for _, sr in df_sku.iterrows():
            sku = str(sr[sku_col]).strip()
            if pct_col:
                try:
                    sku_map[sku] = float(str(sr[pct_col]).replace("%", "").strip())
                except (ValueError, TypeError):
                    sku_map[sku] = 0
            else:
                sku_map[sku] = 0
        return sku_map
    except Exception:
        return None


def _read_sku_list(path: str) -> list | None:
    try:
        df_sku = read_erp_file(path)
        sku_col = "CODIGO_SKU" if "CODIGO_SKU" in df_sku.columns else df_sku.columns[0]
        return df_sku[sku_col].astype(str).str.strip().tolist()
    except Exception:
        return None


def _apply_common_filters(df: pd.DataFrame, ui: dict, cliente_key: str) -> pd.DataFrame:
    """Filtra por cliente y vendedor (común a diferencia_precio y rebate)."""
    cliente = ui.get(cliente_key)
    if cliente and "CLIENTE" in df.columns:
        df = df[df["CLIENTE"].astype(str).str.strip() == cliente.strip()]
    vendedor_id = ui.get("vendedor_id")
    if vendedor_id and "COD_VENDEDOR" in df.columns:
        df = df[df["COD_VENDEDOR"].astype(str).str.strip() == vendedor_id.strip()]
    return df


def _filter_pd(datos_exp: pd.DataFrame, config: dict, ui: dict) -> pd.DataFrame:
    datos_exp = _apply_common_filters(datos_exp, ui, "cliente_pb")

    historial_full = ui.get("df_historial_full")

    # DC calcula con facturas; las notas se reconcilian por factura+SKU aparte.
    # No quitar silenciosamente devoluciones completas: si el check "Usar" está
    # activo, la estrategia reduce la cantidad; si no, conserva la factura y
    # deja la observación para auditoría.
    if "TIPO_CLASE" in datos_exp.columns:
        datos_exp, config = _reconciliar_notas_factura(datos_exp, config, ui)

    nc_por_linea = {}
    if "LINEA" in datos_exp.columns and "CODIGO" in datos_exp.columns:
        from src.core.detector import detectar_notas_en_historial

        notas_nc = (
            detectar_notas_en_historial(historial_full)
            if historial_full is not None
            else pd.DataFrame()
        )
        if notas_nc is not None and not notas_nc.empty:
            codigo_linea = datos_exp[["CODIGO", "LINEA"]].drop_duplicates()
            codigo_to_linea = dict(
                zip(
                    codigo_linea["CODIGO"].astype(str).str.strip(),
                    codigo_linea["LINEA"],
                )
            )
            for _, nr in notas_nc.iterrows():
                cod = str(nr.get("CODIGO", "")).strip()
                if cod in codigo_to_linea:
                    ln = codigo_to_linea[cod]
                    nc_por_linea[ln] = nc_por_linea.get(ln, 0) + float(nr.get("SOLES", 0))
    config["nc_por_linea"] = nc_por_linea
    return datos_exp


def _reconciliar_notas_factura(
    datos_exp: pd.DataFrame, config: dict, ui: dict
) -> tuple[pd.DataFrame, dict]:
    """Deja solo facturas y prepara la reconciliación NC/NDB por factura+SKU.

    Convención compartida por DC/DO/VRS/FPE: el cálculo se hace sobre las
    facturas del fragmento; las NC/NDB no se descartan en silencio, se
    reconcilian aparte (`config["reconciliacion_nc"]`) y las aplica el motor
    SOLO en modalidad individual con el check "Usar" activo y coincidencia
    exacta. En consolidado quedan como informativas.

    Devuelve (datos_exp, config) para poder encadenar la llamada.
    """
    datos_exp = datos_exp[datos_exp["TIPO_CLASE"] == "factura"].copy()
    if config.get("_reconciliar_nc_factura_sku"):
        from src.core.nc_reconciliation import reconciliar_notas

        historial_full = ui.get("df_historial_full")
        config["reconciliacion_nc"] = reconciliar_notas(
            historial_full if historial_full is not None else datos_exp,
            documentos=config.get("documentos_historial"),
            modalidad=config.get("modalidad", "individual"),
        )
    return datos_exp, config


def _resumen_notas_por_sku(df_notas: pd.DataFrame) -> dict:
    """Notas previas por (factura, SKU) para el precio neto de DF.

    ``{factura: {sku: {fae_qty, fae_soles, dev_qty, docs}}}``:

    - ``fae_*``: notas que ajustan el VALOR de la factura (FAE). Se descuentan
      del precio de las unidades que se devuelven.
    - ``dev_qty``: unidades ya devueltas por notas de devolución; el precio
      unitario pasa a calcularse sobre las que quedan en la factura.

    Se separa FAE de devolucion porque una toca la cantidad y la otra los soles: si
    si se mezclaran, el mismo soles se descontaria dos veces.
    """
    from src.core.nc_reconciliation import normalizar_sku

    out: dict = {}
    if df_notas is None or df_notas.empty:
        return out
    for _, r in df_notas.iterrows():
        fac = str(r.get("FACTURA_REF", "") or "").strip()
        sku = normalizar_sku(r.get("CODIGO"))
        if not fac or not sku:
            continue
        item = out.setdefault(fac, {}).setdefault(
            sku, {"fae_qty": 0.0, "fae_soles": 0.0, "dev_qty": 0.0, "docs": set()}
        )
        fae = abs(float(r.get("CANTIDAD_FAE", 0) or 0)) > 0
        qty = abs(float(r.get("CANTIDAD_FAE", 0) if fae else r.get("CANTIDAD", 0)) or 0)
        soles = abs(float(r.get("SOLES", 0) or 0))
        if fae:
            item["fae_qty"] += qty
            item["fae_soles"] += soles
        else:
            item["dev_qty"] += qty
        doc = str(r.get("DOC_NOTA", "") or "").strip()
        if doc:
            item["docs"].add(doc)
    return out


def _leer_devoluciones(path) -> dict:
    """Lee el archivo de devoluciones → {sku: {cantidad, fecha, articulo}}.

    Acepta los nombres de columna de la plantilla (`CODIGO_SKU`,
    `CANTIDAD_DEVUELTA`, `FECHA_DEVOLUCION`) y los equivalentes internos
    (`CODIGO`, `CANTIDAD`, `FECHA`) que llegan si el archivo ya pasó por el
    normalizador. Si el mismo SKU aparece en varias filas, las cantidades se
    suman (una devolución puede venir en varios lotes).
    """
    if not path:
        return {}
    from src.core.utils import read_erp_file

    try:
        df = read_erp_file(path)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    col_sku = next((c for c in ("CODIGO_SKU", "CODIGO", "SKU") if c in df.columns), "")
    col_cant = next(
        (c for c in ("CANTIDAD_DEVUELTA", "CANTIDAD", "CANTIDAD_NC") if c in df.columns), ""
    )
    if not col_sku or not col_cant:
        return {}
    col_fecha = next((c for c in ("FECHA_DEVOLUCION", "FECHA") if c in df.columns), "")
    col_art = "ARTICULO" if "ARTICULO" in df.columns else ""
    salida: dict = {}
    for _, r in df.iterrows():
        sku = str(r[col_sku]).strip()
        if not sku or sku.lower() == "nan":
            continue
        try:
            cant = float(str(r[col_cant]).replace(",", "").strip() or 0)
        except (TypeError, ValueError):
            continue
        # `sku_original` conserva el código tal como vino del ERP (con ceros a
        # la izquierda): el informe debe mostrar ese, no la versión normalizada.
        item = salida.setdefault(
            sku, {"cantidad": 0.0, "fecha": "", "articulo": "", "sku_original": sku}
        )
        item["cantidad"] += cant
        if col_art and not item["articulo"]:
            item["articulo"] = str(r[col_art] or "").strip()
        if col_fecha and not item["fecha"]:
            item["fecha"] = str(r[col_fecha] or "").strip()
    return {k: v for k, v in salida.items() if v["cantidad"] != 0}


def _filter_bonificacion(datos_exp: pd.DataFrame, config: dict, ui: dict) -> pd.DataFrame:
    cliente_pb = ui.get("cliente_pb")
    if cliente_pb and "CLIENTE" in datos_exp.columns:
        datos_exp = datos_exp[datos_exp["CLIENTE"].astype(str).str.strip() == cliente_pb.strip()]
    return datos_exp


def _build_datos_rebate(datos_exp: pd.DataFrame, config: dict, ui: dict) -> pd.DataFrame:
    datos_exp = _apply_common_filters(datos_exp, ui, "cliente_pb")

    categorias_selected = config.get("categorias_nc") or []
    if "TIPO_CLASE" in datos_exp.columns:
        datos_exp = datos_exp[
            (datos_exp["TIPO_CLASE"] == "factura")
            | (datos_exp["TIPO_CLASE"].isin(categorias_selected))
        ]
    else:
        from src.core.detector import _es_documento_nota, clasificar_nota

        notas_exp = datos_exp[datos_exp["TIPO_DOC"].apply(_es_documento_nota)].copy()
        if not notas_exp.empty:
            clasif = notas_exp.apply(
                lambda r: clasificar_nota(
                    str(r.get("TIPO_DOC", "")),
                    float(r.get("CANTIDAD", 0)),
                    float(r.get("SOLES", 0)),
                )["categoria"],
                axis=1,
            )
            mask_incluir = ~datos_exp["TIPO_DOC"].apply(_es_documento_nota) | (
                datos_exp["TIPO_DOC"].apply(_es_documento_nota)
                & clasif.reindex(datos_exp.index).isin(categorias_selected)
            )
            datos_exp = datos_exp[mask_incluir]

    nc_por_linea = {}
    if "LINEA" in datos_exp.columns and "CODIGO" in datos_exp.columns and categorias_selected:
        if "TIPO_CLASE" in datos_exp.columns:
            notas_incluidas = datos_exp[datos_exp["TIPO_CLASE"].isin(categorias_selected)]
        else:
            notas_incluidas = pd.DataFrame()
        if not notas_incluidas.empty:
            codigo_linea = datos_exp[["CODIGO", "LINEA"]].drop_duplicates()
            codigo_to_linea = dict(
                zip(
                    codigo_linea["CODIGO"].astype(str).str.strip(),
                    codigo_linea["LINEA"],
                )
            )
            for _, nr in notas_incluidas.iterrows():
                cod = str(nr.get("CODIGO", "")).strip()
                if cod in codigo_to_linea:
                    ln = codigo_to_linea[cod]
                    nc_por_linea[ln] = nc_por_linea.get(ln, 0) + float(nr.get("SOLES", 0))
    config["nc_por_linea"] = nc_por_linea
    return datos_exp
