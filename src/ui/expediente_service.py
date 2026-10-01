"""Expediente generation service for ReconocimientoView._generar_expediente.

Centraliza la lógica de generación de expedientes (Excel + DOCX) en una función
pura que recibe datos y retorna la lista de archivos generados.

Naming (fuente única: ``build_expediente_id`` en src/ui/catalog.py)::

    EXP-[CASO]-[CLIENTE]-[SERIE]-[NUMERO]-[YYYYMMDD]

    carpeta/     EXP-DC-50561-204-67721-20260910/
    documento/   EXP-DC-50561-204-67721-20260910_Informe.docx
                 EXP-DC-50561-204-67721-20260910_Calculo.xlsx
                 EXP-DC-50561-204-67721-20260910_Historico.xlsx
                 EXP-DC-50561-204-67721-20260910_CalculoND.xlsx  (si hay ND)

Sin correlativo ni código de modalidad: el documento del ERP (cliente + serie +
número) ya identifica el expediente, así que el mismo caso se puede regenerar
sin crear duplicados.
"""

from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from src.domain import RecognitionResult
from src.render.excel_renderer import ExcelRenderer
from src.render.g360_styles import (
    EXP_NAVY,
    EXP_LABEL_BG,
    EXP_TOTAL_BG,
    EXP_BORDER,
    EXP_WHITE,
    EXP_INK_SOFT,
    EXP_ERR_TX,
    EXP_FAINT,
    exp_fill,
)
from src.render.docx_renderer import DocxInformeRenderer
from src.core.utils import resolve_output_path
from src.ui.catalog import (
    CATALOGO,
    build_expediente_id,
    build_documento_nombre,
    MODALIDAD_INDIVIDUAL,
    MODALIDAD_CONSOLIDADO,
)


def generar_expediente(
    resultado,
    tipo_actual: str,
    modalidad: str,
    historico_config,
    df_historial,
    cliente_value: str,
    vendedor_value: str,
    vendedor_display: str,
    antecedentes: str,
    observaciones: str,
    desktop_path: Path,
    omitir_sin_diferencia: bool = False,
    generar_nota_debito: bool = False,
) -> list:
    """Genera expedientes comerciales (Excel + DOCX).

    Naming: ver el docstring del módulo (``EXP-[CASO]-[CLIENTE]-[SERIE]-[NUMERO]-[YYYYMMDD]``).

    Individual: un expediente por (cliente × factura).
    Consolidado: un expediente por cliente.
    """
    res = resultado.resultado
    df_res = res.dataframe if res is not None else pd.DataFrame()

    # Opcional: preparar cálculo de Nota de Débito a partir de diferencias negativas.
    # Se calcula ANTES del filtrado (las filas negativas no son 0, así que el
    # resultado es idéntico; pero así el clon no pierde las filas ND).
    df_nd = None
    if generar_nota_debito and not df_res.empty and "DIFERENCIA" in df_res.columns:
        nd = df_res[df_res["DIFERENCIA"].fillna(0) < -0.001].copy()
        if not nd.empty:
            cant = nd.get("CANTIDAD", pd.Series(1.0, index=nd.index))
            nd["MONTO_ND"] = (nd["DIFERENCIA"].abs() * cant).round(2)
            df_nd = nd

    # Opcional: excluir filas sin diferencia (estado "coincide": |DIFERENCIA|≈0).
    # Se clona el resultado con dataframes filtrados para que los subresultados
    # (por cliente / factura) hereden el filtrado.
    if omitir_sin_diferencia:
        resultado = _clonar_sin_diferencia(resultado)
        if resultado is None:
            return []
        res = resultado.resultado
        df_res = res.dataframe if res is not None else pd.DataFrame()

    # Identificar clientes del resultado
    clientes = []
    if not df_res.empty and "CLIENTE" in df_res.columns:
        clientes = [str(v).strip() for v in df_res["CLIENTE"].dropna().unique() if str(v).strip()]
    if len(clientes) <= 1 and df_historial is not None and "CLIENTE" in df_historial.columns:
        hist_clientes = [
            str(v).strip() for v in df_historial["CLIENTE"].dropna().unique() if str(v).strip()
        ]
        if len(hist_clientes) > len(clientes):
            clientes = hist_clientes

    if not clientes:
        clientes = [cliente_value] if cliente_value else [""]

    # ── Individual: un expediente por (cliente × factura) ──
    if modalidad == MODALIDAD_INDIVIDUAL:
        return _generar_individual(
            resultado,
            tipo_actual,
            historico_config,
            df_historial,
            clientes,
            vendedor_value,
            vendedor_display,
            antecedentes,
            observaciones,
            desktop_path,
            df_nd=df_nd,
        )

    # ── Consolidado: un expediente por cliente ──
    return _generar_consolidado(
        resultado,
        tipo_actual,
        historico_config,
        df_historial,
        clientes,
        vendedor_value,
        vendedor_display,
        antecedentes,
        observaciones,
        desktop_path,
        df_nd=df_nd,
    )


def _recalcular_resumen(res_resumen, df, df_x=None, **extra):
    """Recalcula total_nc y skus_afectados a partir de un dataframe filtrado.

    Usa el mismo criterio que pipeline._aplicar_reglas_post para el total, y
    cuenta solo los SKUs cuyo monto reconocido es > 0 (mismo criterio que el
    informe), para que el Informe (DOCX) y el Cálculo (XLSX) coincidan con el
    subconjunto del expediente (cliente o factura), no con el resultado
    global.

    `extra` permite fijar campos adicionales del resumen que el clon deba
    sobrescribir (ej: doc_ref por cliente/factura).
    """
    d = df_x if df_x is not None and not df_x.empty else df
    resumen = dict(res_resumen or {})
    total = 0.0
    nc_key = None
    if not d.empty:
        for col in ("Subtotal NC (S/)", "SUBTOTAL (SIN IGV)", "MONTO_NC", "MONTO NC"):
            if col in d.columns:
                nc_key = col
                total = float(d[col].sum())
                break
    resumen["total_nc"] = total
    if not d.empty and nc_key and "SKU" in d.columns:
        resumen["skus_afectados"] = int(d.loc[d[nc_key] > 0, "SKU"].nunique())
    elif not d.empty and "SKU" in d.columns:
        resumen["skus_afectados"] = int(d["SKU"].nunique())
    else:
        resumen["skus_afectados"] = 0
    resumen.update(extra)
    return resumen


def _clonar_sin_diferencia(resultado):
    """Clona el expediente excluyendo filas sin impacto monetario real.

    Excluye:
    - |DIFERENCIA| ≈ 0 (coincidencia exacta, AL02 positiva o negativa)
    - MONTO_NC == 0 (diferencia negativa: PRECIO_LISTA > PRECIO_HIST,
      no genera NC pero el clip(lower=0) pone el monto en cero)
    - ALERTA contiene "AL11" (redondeo acumulable dentro de tolerancia)
    """
    res = resultado.resultado
    if res is None:
        return None
    df = res.dataframe
    df_x = res.get_excel() if hasattr(res, "get_excel") else pd.DataFrame()

    def _flt(d):
        if d is None or d.empty:
            return d
        keep = pd.Series(True, index=d.index)
        # Excluir diferencia unitaria cercana a cero
        if "DIFERENCIA" in d.columns:
            keep &= d["DIFERENCIA"].fillna(0).abs() > 0.001
        # Excluir Monto NC igual a cero (no hay NC que reconocer)
        if "MONTO_NC" in d.columns:
            keep &= pd.to_numeric(d["MONTO_NC"], errors="coerce").fillna(0) > 0.001
        # Excluir redondeo acumulable (AL11)
        if "ALERTA" in d.columns:
            keep &= ~d["ALERTA"].astype(str).str.contains("AL11", na=False)
        return d[keep].copy()

    df_f, df_x_f = _flt(df), _flt(df_x)
    if (df_f is None or df_f.empty) and (df_x_f is None or df_x_f.empty):
        return None
    sub_res = RecognitionResult(
        dataframe=df_f if df_f is not None else pd.DataFrame(),
        dataframe_excel=df_x_f if df_x_f is not None else df_f,
        resumen=_recalcular_resumen(res.resumen, df_f, df_x_f),
        alertas=res.alertas,
        trazabilidad=res.trazabilidad,
    )
    sub_res.metricas = getattr(res, "metricas", {}) or {}
    return SimpleNamespace(resultado=sub_res, datos=resultado.datos, contexto=resultado.contexto)


def _generar_individual(
    resultado,
    tipo_actual,
    historico_config,
    df_historial,
    clientes,
    vendedor_value,
    vendedor_display,
    antecedentes,
    observaciones,
    desktop_path,
    df_nd=None,
) -> list:
    """Individual: un Expediente por factura dentro de cada cliente."""
    dirs = []
    for cli in sorted(clientes):
        sub_cli = _subresultado_por_cliente(resultado, cli)
        if sub_cli is None:
            continue
        df_f = sub_cli.resultado.dataframe
        # El dataframe puede ser una vista previa por SKU (ej: FPE) sin
        # FACTURA; el detalle por factura vive en dataframe_excel.
        df_x = sub_cli.resultado.get_excel() if hasattr(sub_cli.resultado, "get_excel") else None
        split_src = df_f if "FACTURA" in df_f.columns else df_x
        if split_src is None or split_src.empty or "FACTURA" not in split_src.columns:
            exp_dir = _generar_uno(
                resultado=sub_cli,
                tipo_actual=tipo_actual,
                modalidad=MODALIDAD_CONSOLIDADO,
                historico_config=historico_config,
                df_historial=df_historial,
                cliente_value=cli,
                vendedor_value=vendedor_value,
                vendedor_display=vendedor_display,
                antecedentes=antecedentes,
                observaciones=observaciones,
                desktop_path=desktop_path,
                df_nd=df_nd,
            )
            dirs.append(exp_dir)
            continue
        # Agrupar por factura
        for fac in sorted(split_src["FACTURA"].dropna().unique()):
            fac_str = str(fac).strip()
            sub_fac = _subresultado_por_factura(sub_cli, fac_str)
            if sub_fac is None:
                continue
            exp_dir = _generar_uno(
                resultado=sub_fac,
                tipo_actual=tipo_actual,
                modalidad=MODALIDAD_INDIVIDUAL,
                historico_config=historico_config,
                df_historial=df_historial,
                cliente_value=cli,
                vendedor_value=vendedor_value,
                vendedor_display=vendedor_display,
                antecedentes=antecedentes,
                observaciones=observaciones,
                desktop_path=desktop_path,
                df_nd=df_nd,
            )
            dirs.append(exp_dir)
    return dirs


def _generar_consolidado(
    resultado,
    tipo_actual,
    historico_config,
    df_historial,
    clientes,
    vendedor_value,
    vendedor_display,
    antecedentes,
    observaciones,
    desktop_path,
    df_nd=None,
) -> list:
    """Consolidado: un Expediente por cliente (todas las facturas juntas)."""
    dirs = []
    for cli in sorted(clientes):
        sub_cli = _subresultado_por_cliente(resultado, cli)
        if sub_cli is None:
            continue
        exp_dir = _generar_uno(
            resultado=sub_cli,
            tipo_actual=tipo_actual,
            modalidad=MODALIDAD_CONSOLIDADO,
            historico_config=historico_config,
            df_historial=df_historial,
            cliente_value=cli,
            vendedor_value=vendedor_value,
            vendedor_display=vendedor_display,
            antecedentes=antecedentes,
            observaciones=observaciones,
            desktop_path=desktop_path,
            df_nd=df_nd,
        )
        dirs.append(exp_dir)
    return dirs


def _subresultado_por_cliente(resultado, cliente: str):
    """Clona el expediente filtrando dataframe/dataframe_excel por cliente."""
    res = resultado.resultado
    if res is None:
        return None
    df = res.dataframe
    df_x = res.get_excel() if hasattr(res, "get_excel") else pd.DataFrame()

    def _flt(d):
        if d is None or d.empty or "CLIENTE" not in d.columns:
            return d
        return d[d["CLIENTE"].astype(str).str.strip() == cliente].copy()

    df_f, df_x_f = _flt(df), _flt(df_x)
    if df_f.empty and (df_x_f is None or df_x_f.empty):
        return None
    sub_res = RecognitionResult(
        dataframe=df_f,
        dataframe_excel=df_x_f if df_x_f is not None else df_f,
        resumen=_recalcular_resumen(
            res.resumen,
            df_f,
            df_x_f,
            doc_ref=_doc_ref_de_df(df_x_f if df_x_f is not None and not df_x_f.empty else df_f),
        ),
        alertas=res.alertas,
        trazabilidad=res.trazabilidad,
    )
    sub_res.metricas = getattr(res, "metricas", {}) or {}
    # Clona solo lo necesario del expediente original
    sub_exp = SimpleNamespace(
        resultado=sub_res, datos=_flt(resultado.datos), contexto=resultado.contexto
    )
    return sub_exp


def _subresultado_por_factura(resultado, factura: str):
    """Clona el expediente filtrando por FACTURA dentro de un cliente."""
    res = resultado.resultado
    if res is None:
        return None
    df = res.dataframe
    df_x = res.get_excel() if hasattr(res, "get_excel") else pd.DataFrame()

    def _flt(d):
        if d is None or d.empty or "FACTURA" not in d.columns:
            return d
        return d[d["FACTURA"].astype(str).str.strip() == factura].copy()

    df_f, df_x_f = _flt(df), _flt(df_x)
    if df_f.empty and (df_x_f is None or df_x_f.empty):
        return None
    # Vista previa sin FACTURA (ej: FPE): se recorta a los SKUs que trae
    # la factura para que el Informe no muestre los demás documentos.
    if (
        df_f is not None
        and not df_f.empty
        and "FACTURA" not in df_f.columns
        and df_x_f is not None
        and not df_x_f.empty
    ):
        sku_col = next(
            (c for c in ("SKU", "CODIGO") if c in df_f.columns and c in df_x_f.columns), ""
        )
        if sku_col:
            skus = set(df_x_f[sku_col].astype(str).str.strip())
            df_f = df_f[df_f[sku_col].astype(str).str.strip().isin(skus)].copy()
    sub_res = RecognitionResult(
        dataframe=df_f,
        dataframe_excel=df_x_f if df_x_f is not None else df_f,
        resumen=_recalcular_resumen(res.resumen, df_f, df_x_f, doc_ref=factura),
        alertas=res.alertas,
        trazabilidad=res.trazabilidad,
    )
    sub_res.metricas = getattr(res, "metricas", {}) or {}
    sub_exp = SimpleNamespace(
        resultado=sub_res, datos=_flt(resultado.datos), contexto=resultado.contexto
    )
    return sub_exp


def _extract_serie_nro(doc_ref: str) -> tuple:
    """Extrae (serie, nro) de un documento tipo 'F01-12345' o '1-374377'."""
    if not doc_ref:
        return ("", "")
    doc_ref = str(doc_ref).strip()
    # Formato esperado: XNN-NNNNN o NN-NNNNN (primer caracter = tipo doc)
    if "-" in doc_ref:
        parte, nro = doc_ref.rsplit("-", 1)
        # parte = 'F01' or '1' -> serie = todo menos el primer caracter (tipo)
        serie = parte[1:] if len(parte) > 1 else parte
        try:
            int(nro)
            return (serie, nro)
        except ValueError:
            pass
    return ("", "")


def _extract_cliente_id(res, df_historial=None, cliente_value="") -> str:
    """Extrae el COD_CLIENTE del resultado para el ID del expediente.

    Busca en dataframe y dataframe_excel. Si el resultado no trae columnas de
    cliente, lo resuelve desde el historial o desde el valor del filtro de la UI.
    """
    candidates = []
    df = res.dataframe
    df_x = res.get_excel() if hasattr(res, "get_excel") else pd.DataFrame()
    for d in (df, df_x):
        if d is None or d.empty:
            continue
        for col in ("COD_CLIENTE", "id_cliente", "DOC_CLIENTE", "RUC"):
            if col in d.columns:
                vals = d[col].dropna().unique()
                if len(vals) > 0:
                    candidates.append(str(vals[0]).strip())
    for v in candidates:
        if v and v.lower() not in ("nan", "none", "0", ""):
            return v
    # Fallback 1: buscar en el historial por COD_CLIENTE o por nombre/RUC
    if df_historial is not None and not df_historial.empty:
        if "COD_CLIENTE" in df_historial.columns:
            cli_filt = str(cliente_value or "").strip()
            if cli_filt:
                m = df_historial[df_historial["COD_CLIENTE"].astype(str).str.strip() == cli_filt]
                if not m.empty:
                    return str(m["COD_CLIENTE"].iloc[0]).strip()
        if "CLIENTE" in df_historial.columns:
            for ref in (candidates, [cliente_value]):
                for v in ref:
                    if not v or v.lower() in ("nan", "none", ""):
                        continue
                    m = df_historial[
                        df_historial["CLIENTE"]
                        .astype(str)
                        .str.contains(str(v), case=False, na=False)
                    ]
                    if not m.empty and "COD_CLIENTE" in df_historial.columns:
                        return str(m["COD_CLIENTE"].iloc[0]).strip()
        # Historial de un único cliente: devolver ese COD_CLIENTE.
        if "COD_CLIENTE" in df_historial.columns:
            unicos = [
                v
                for v in df_historial["COD_CLIENTE"].astype(str).str.strip().unique()
                if v and v.lower() not in ("nan", "none", "")
            ]
            if len(unicos) == 1:
                return unicos[0]
    # Fallback 2: el propio valor del filtro de la UI
    if str(cliente_value or "").strip() and str(cliente_value).strip().lower() not in (
        "nan",
        "none",
        "",
    ):
        return str(cliente_value).strip()
    return ""


def _doc_ref_de_df(df):
    if df is None or df.empty or "FACTURA" not in df.columns:
        return ""
    try:
        g = df.groupby("FACTURA")["SOLES"].sum() if "SOLES" in df.columns else None
        if g is not None and not g.empty:
            return str(g.idxmax())
    except Exception:
        pass
    return str(df["FACTURA"].iloc[0])


def _generar_uno(
    resultado,
    tipo_actual: str,
    modalidad: str,
    historico_config,
    df_historial,
    cliente_value: str,
    vendedor_value: str,
    vendedor_display: str,
    antecedentes: str,
    observaciones: str,
    desktop_path: Path,
    df_nd=None,
) -> Path:
    """Genera UN expediente (Excel + DOCX) y retorna el directorio.

    Naming: ver el docstring del módulo.
    """
    res = resultado.resultado
    cliente = _resolve_cliente(cliente_value, res, df_historial)
    doc_ref = _resolve_doc_ref(res, resultado)

    # Extraer SERIE y NUMERO del documento de referencia para el ID
    serie, nro = _extract_serie_nro(doc_ref)
    cliente_id = _extract_cliente_id(res, df_historial=df_historial, cliente_value=cliente_value)
    exp_id = build_expediente_id(tipo_actual, cliente_id, serie, nro)

    # Crear directorio del expediente
    exp_dir = desktop_path / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)

    # Nombre corto del caso para compatibilidad con renderers
    caso = CATALOGO.get(tipo_actual)
    nombre_corto = caso.label if caso else tipo_actual

    # Archivos a generar
    excel_name = build_documento_nombre(exp_id, "Calculo", "xlsx")
    docx_name = build_documento_nombre(exp_id, "Informe", "docx")
    historico_name = build_documento_nombre(exp_id, "Historico", "xlsx")

    ruc = _extract_ruc(resultado.datos)

    periodo_txt = _texto_periodo(res.dataframe) or _texto_periodo(df_historial)
    fecha_doc = _fecha_documento(df_historial, cliente, doc_ref)

    datos_base = {
        "cliente": cliente,
        "representante": vendedor_display,
        "tipo_operacion": nombre_corto,
        "descripcion": antecedentes,
        "observaciones": observaciones,
        "modalidad": modalidad,
        "periodo": periodo_txt,
        "fecha_documento": fecha_doc,
        "historico_config": historico_config.as_dict() if historico_config else {},
    }

    # Generar Informe (DOCX)
    docx_path = resolve_output_path(exp_dir / docx_name)
    _generar_informe(
        resultado,
        tipo_actual,
        excel_name,
        datos_base,
        docx_path,
        nombre_historico=(
            historico_name
            if historico_config
            and any(historico_config.incluir(d) for d in ("facturas", "nc", "ndb"))
            else ""
        ),
    )

    # Generar Cálculo (Excel)
    excel_path = resolve_output_path(exp_dir / excel_name)
    _generar_calculo(
        resultado,
        tipo_actual,
        cliente,
        vendedor_display,
        doc_ref,
        ruc,
        excel_path,
        df_historial,
        periodo=periodo_txt,
        antecedentes=antecedentes,
        observaciones=observaciones,
        modalidad=modalidad,
    )

    # Generar Cálculo de Nota de Débito (si hay filas de diferencias negativas)
    if df_nd is not None and not df_nd.empty:
        nd_sub = _filtrar_nd_scope(df_nd, cliente, doc_ref, modalidad)
        if nd_sub is not None and not nd_sub.empty:
            nd_path = resolve_output_path(
                exp_dir / build_documento_nombre(exp_id, "CalculoND", "xlsx")
            )
            _generar_calculo_nd(
                nd_sub, tipo_actual, cliente, vendedor_display, doc_ref, ruc, nd_path
            )

    # Generar Histórico opcional
    if historico_config and any(historico_config.incluir(d) for d in ("facturas", "nc", "ndb")):
        historico_path = resolve_output_path(exp_dir / historico_name)
        _generar_historico(
            resultado,
            tipo_actual,
            historico_path,
            df_historial=df_historial,
            cliente=cliente,
            vendedor=vendedor_display,
            exp_id=exp_id,
            historico_config=historico_config,
        )

    return exp_dir


def _generar_informe(resultado, tipo, excel_name, datos_base, docx_path, nombre_historico=""):
    """Genera el informe en PDF/DOCX."""
    DocxInformeRenderer().generar(
        resultado.resultado,
        expediente=resultado,
        ruta_salida=str(docx_path),
        datos_adicionales={
            **datos_base,
            "nombre_historico": nombre_historico,
            "tipo_calculo": tipo,
            "numero_referencia": _resolve_doc_ref(resultado.resultado, resultado),
        },
        nombre_archivo_excel=excel_name,
    )


def _generar_calculo(
    resultado,
    tipo,
    cliente,
    vendedor,
    doc_ref,
    ruc,
    excel_path,
    df_historial=None,
    periodo="",
    antecedentes="",
    observaciones="",
    modalidad="individual",
):
    """Genera la hoja de cálculo con el resultado del cálculo."""
    caso = CATALOGO.get(tipo)
    tipo_renderer = caso.legacy_types[0] if caso and caso.legacy_types else tipo
    ExcelRenderer().generar(
        resultado.resultado,
        str(excel_path),
        tipo=tipo_renderer,
        cliente=cliente,
        vendedor=vendedor,
        motivo=(CATALOGO[tipo].label if tipo in CATALOGO else tipo),
        doc_ref=doc_ref,
        ruc=ruc,
        df_historial=df_historial,
        periodo=periodo,
        antecedentes=antecedentes,
        observaciones=observaciones,
        modalidad=modalidad,
    )


def _filtrar_nd_scope(df_nd, cliente, doc_ref, modalidad):
    """Filtra el dataframe ND al alcance del expediente actual (cliente y opcionalmente factura)."""
    d = df_nd
    if d is None or d.empty:
        return None
    if "CLIENTE" in d.columns:
        cli_col = d["CLIENTE"].astype(str).str.strip()
        d = d[cli_col == cliente]
    if modalidad == MODALIDAD_INDIVIDUAL and doc_ref and "FACTURA" in d.columns:
        doc_col = d["FACTURA"].astype(str).str.strip()
        d = d[doc_col == str(doc_ref).strip()]
    return d.copy() if not d.empty else None


def _generar_calculo_nd(nd_sub, tipo, cliente, vendedor, doc_ref, ruc, nd_path):
    """Genera la hoja de cálculo de Nota de Débito (solo el cálculo)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Calculo ND"
    ws.sheet_view.showGridLines = False
    # Márgenes laterales simétricos 0.8" (~2.0 cm, igual que el resto).
    ws.page_margins.left = 0.8
    ws.page_margins.right = 0.8

    header_fill = exp_fill(EXP_NAVY)
    label_fill = exp_fill(EXP_LABEL_BG)
    total_fill = exp_fill(EXP_TOTAL_BG)
    thin = Border(
        left=Side(style="thin", color=EXP_BORDER),
        right=Side(style="thin", color=EXP_BORDER),
        top=Side(style="thin", color=EXP_BORDER),
        bottom=Side(style="thin", color=EXP_BORDER),
    )

    caso = CATALOGO[tipo].label if tipo in CATALOGO else tipo

    ws.cell(row=1, column=1, value="NOTA DE DÉBITO — CÁLCULO")
    ws.cell(row=1, column=1).font = Font(bold=True, size=13, color=EXP_WHITE)
    ws.cell(row=1, column=1).fill = header_fill
    ws.merge_cells("A1:H1")
    ws.row_dimensions[1].height = 26

    info = [
        ("Caso", caso),
        ("Cliente", cliente or "—"),
        ("RUC", ruc or "—"),
        ("Vendedor", vendedor or "—"),
        ("Factura de referencia", doc_ref or "—"),
    ]
    r = 3
    for lbl, val in info:
        c = ws.cell(row=r, column=1, value=lbl)
        c.font = Font(bold=True, size=9, color=EXP_INK_SOFT)
        c.fill = label_fill
        c.border = thin
        c.alignment = Alignment(horizontal="right")
        v = ws.cell(row=r, column=2, value=val)
        v.font = Font(size=9)
        v.border = thin
        r += 1

    r += 1
    headers = [
        "N°",
        "FACTURA",
        "SKU",
        "ARTICULO",
        "CANTIDAD",
        "PRECIO FACTURADO",
        "PRECIO NETO",
        "MONTO ND",
    ]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=r, column=ci, value=h)
        c.font = Font(bold=True, size=9, color=EXP_WHITE)
        c.fill = header_fill
        c.border = thin
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    widths = [15, 14, 12, 32, 10, 14, 14, 13]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    r += 1
    gran_total = 0.0

    def _val(row, *keys, default=0.0):
        for k in keys:
            if k in nd_sub.columns:
                v = row.get(k)
                if v is not None and str(v) not in ("", "nan", "None"):
                    try:
                        return float(v)
                    except ValueError:
                        pass
        return default

    def _txt(row, *keys, default=""):
        for k in keys:
            if k in nd_sub.columns:
                v = row.get(k)
                if v is not None and str(v) not in ("", "nan", "None"):
                    return str(v)
        return default

    for idx, (_, row) in enumerate(nd_sub.iterrows(), 1):
        monto_nd = _val(row, "MONTO_ND")
        gran_total += monto_nd
        values = [
            idx,
            _txt(row, "FACTURA", "FACTURAS"),
            _txt(row, "SKU", "CODIGO"),
            _txt(row, "ARTICULO", "DESCRIPCION")[:40],
            _val(row, "CANTIDAD"),
            _val(row, "PRECIO_HIST", "PRECIO_UNITARIO", "PRECIO"),
            _val(row, "PRECIO_NETO", "PRECIO_BASE"),
            monto_nd,
        ]
        for ci, val in enumerate(values, 1):
            c = ws.cell(row=r, column=ci, value=val)
            c.border = thin
            c.font = Font(size=9)
            if ci == 1:
                c.alignment = Alignment(horizontal="center")
            if ci in (5, 6, 7):
                c.number_format = "#,##0.00"
            if ci == 8:
                c.number_format = "#,##0.00"
                c.fill = total_fill
                c.font = Font(bold=True, size=9, color=EXP_ERR_TX)
        r += 1

    c = ws.cell(row=r, column=1, value="TOTAL ND (S/)")
    c.font = Font(bold=True, size=10, color=EXP_ERR_TX)
    c.fill = label_fill
    c.border = thin
    c.alignment = Alignment(horizontal="right")
    ws.merge_cells(f"A{r}:G{r}")
    t = ws.cell(row=r, column=8, value=round(gran_total, 2))
    t.font = Font(bold=True, size=10, color=EXP_ERR_TX)
    t.fill = total_fill
    t.border = thin
    t.number_format = "#,##0.00"
    r += 2
    nota = ws.cell(
        row=r,
        column=1,
        value="La diferencia negativa (precio facturado < precio neto) genera una Nota de Débito por el importe señalado.",
    )
    nota.font = Font(italic=True, size=8, color=EXP_FAINT)
    ws.merge_cells(f"A{r}:H{r}")

    try:
        wb.save(str(nd_path))
    except PermissionError:
        raise PermissionError(
            f"No se pudo guardar el archivo de Nota de Débito. ¿Está abierto?\n{nd_path}"
        )
    return nd_path


def _generar_historico(
    resultado,
    tipo,
    historico_path,
    df_historial=None,
    cliente="",
    vendedor="",
    exp_id="",
    historico_config=None,
):
    """Genera el reporte histórico (segmento del expediente) en estilo clásico.

    Usa `generar_historico_clasico`: reporte sobrio tipo ERP Gupta (economato
    y negro), filtrado al cliente del expediente y guardado dentro de la
    carpeta del mismo.
    """
    from src.render.audit_renderer import generar_historico_clasico

    hist = df_historial if df_historial is not None else pd.DataFrame()

    # Filtrar al cliente del expediente (cada hecho de la UI ya recorta por
    # rango de fechas al construir el fragmento; aquí solo acotamos cliente).
    if not hist.empty and "CLIENTE" in hist.columns:
        cli = str(cliente).strip()
        if cli:
            hist = hist[hist["CLIENTE"].astype(str).str.strip() == cli].copy()
        else:
            # Sin cliente definido, conserva el primer cliente del segmento
            vals = hist["CLIENTE"].dropna().unique()
            if len(vals) > 0:
                hist = hist[hist["CLIENTE"].astype(str).str.strip() == str(vals[0]).strip()].copy()

    hist = _filtrar_documentos_historico(hist, historico_config)

    if hist.empty:
        return historico_path

    ruc = _extract_ruc(resultado.datos)
    fecha_desde, fecha_hasta = _periodo_de_df(hist)
    generar_historico_clasico(
        df_historial=hist,
        cliente_nombre=cliente
        or (str(hist["CLIENTE"].iloc[0]) if "CLIENTE" in hist.columns else ""),
        cliente_ruc=ruc,
        tipo_operacion=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        vendedor=vendedor,
        expediente_id=exp_id,
        ruta_salida=historico_path,
        reclamados=_reclamados_de_df(
            resultado.resultado.dataframe if resultado.resultado is not None else None
        ),
    )
    return historico_path


def _filtrar_documentos_historico(hist: pd.DataFrame, historico_config) -> pd.DataFrame:
    """Apply the independent Mostrar settings to the exported history sheet."""
    if hist is None or hist.empty or historico_config is None:
        return hist
    if "TIPO_CLASE" in hist.columns:
        clases = hist["TIPO_CLASE"].astype(str).str.lower()
    else:
        clases = pd.Series("sin_dato", index=hist.index)
    if "TIPO_DOC" in hist.columns:
        tpos = hist["TIPO_DOC"].astype(str).str.upper()
        es_ndb = tpos.str.startswith("ND")
    else:
        es_ndb = clases == "cargo"
    es_factura = clases == "factura"
    es_nota = ~es_factura
    visible = (
        (es_factura & historico_config.incluir("facturas"))
        | (es_nota & es_ndb & historico_config.incluir("ndb"))
        | (es_nota & ~es_ndb & historico_config.incluir("nc"))
    )
    return hist[visible].copy()


def _reclamados_de_df(df):
    """Pares (factura, sku) del Cálculo para resaltar en el Histórico."""
    pares = set()
    if df is None or df.empty:
        return pares
    for _, row in df.iterrows():
        sku = str(row.get("SKU", "")).strip()
        if not sku or sku.lower() == "nan":
            continue
        docs = set()
        for col in ("FACTURAS", "FACTURA"):
            if col not in df.columns:
                continue
            for p in str(row.get(col, "")).replace(";", ",").split(","):
                p = p.strip().split(" (")[0].strip()
                if p and p.lower() not in ("nan", "none"):
                    docs.add(p)
        for d in docs:
            pares.add((d, sku))
    return pares


def _resolve_cliente(cliente_value, res, df_historial):
    """Resuelve el nombre del cliente desde múltiples fuentes."""
    cliente = cliente_value or ""
    if not cliente or cliente == "CLIENTE":
        df = res.dataframe
        if not df.empty and "CLIENTE" in df.columns:
            vals = df["CLIENTE"].dropna().unique()
            if len(vals) > 0:
                cliente = str(vals[0])
    if not cliente or cliente == "CLIENTE":
        if df_historial is not None and "CLIENTE" in df_historial.columns:
            vals = df_historial["CLIENTE"].dropna().unique()
            if len(vals) > 0:
                cliente = str(vals[0])
    return cliente or "CLIENTE"


def _resolve_doc_ref(res, expediente):
    """Resuelve la referencia del documento desde el resumen o config."""
    resumen = res.resumen
    return (
        resumen.get("doc_ref")
        or resumen.get("documento_referencia")
        or (expediente.contexto.config or {}).get("factura_id")
        or ""
    )


def _fecha_documento(df_historial, cliente, doc_ref) -> str:
    """Fecha de emisión (FECHA, dd/mm/aaaa) de la factura de referencia.

    Busca doc_ref en el historial filtrado por cliente (columnas DOC_ID o
    FACTURA, con y sin letra inicial de serie). Vacío si no se encuentra.
    """
    from datetime import datetime as _dt

    if df_historial is None or df_historial.empty or not doc_ref:
        return ""
    hist = df_historial
    if "CLIENTE" in hist.columns and cliente:
        m = hist[hist["CLIENTE"].astype(str).str.strip() == str(cliente).strip()]
        if not m.empty:
            hist = m
    refs = [str(doc_ref).strip()]
    pelada = refs[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    if pelada and pelada != refs[0]:
        refs.append(pelada)
    for col in ("DOC_ID", "FACTURA", "FACTURAS"):
        if col not in hist.columns or "FECHA" not in hist.columns:
            continue
        vals = hist[hist[col].astype(str).str.strip().isin(refs)]
        if vals.empty:
            continue
        v = vals["FECHA"].dropna().iloc[0]
        if isinstance(v, _dt):
            return v.strftime("%d/%m/%Y")
        s = str(v).strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
            try:
                return _dt.strptime(s, fmt).strftime("%d/%m/%Y")
            except ValueError:
                continue
        return s[:10]
    return ""


def _texto_periodo(df) -> str:
    """Formatea el rango de fechas del DataFrame como 'dd/mm/aaaa al dd/mm/aaaa'."""
    desde, hasta = _periodo_de_df(df)
    if desde and hasta:
        return f"{desde} al {hasta}" if desde != hasta else desde
    return ""


def _periodo_de_df(df):
    """Extrae (fecha_desde, fecha_hasta) legibles del DataFrame del histórico."""
    if df is None or df.empty or "FECHA" not in df.columns:
        return None, None
    from datetime import datetime

    fechas = []
    for v in df["FECHA"].dropna().unique():
        if isinstance(v, datetime):
            fechas.append(v)
        else:
            s = str(v).strip()
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
                try:
                    fechas.append(datetime.strptime(s, fmt))
                    break
                except ValueError:
                    continue
    if not fechas:
        return None, None
    return min(fechas).strftime("%d/%m/%Y"), max(fechas).strftime("%d/%m/%Y")


def _extract_ruc(df_datos):
    """Extrae el RUC del cliente desde el DataFrame de datos."""
    if df_datos is None or df_datos.empty or "DOC_CLIENTE" not in df_datos.columns:
        return ""
    for val in df_datos["DOC_CLIENTE"].dropna().unique():
        s = str(val).strip()
        if s and s.lower() not in ("nan", "none", ""):
            return s
    return ""
