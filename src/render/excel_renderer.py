import re
import pandas as pd
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Side, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage
from src.domain import RecognitionResult, CATALOGO_ALERTAS
from src.core.utils import EXCEL_FMT_NUMBER, EXCEL_FMT_CURRENCY, EXCEL_FMT_PCT
from src.render.g360_styles import (
    G360Styles,
    EXP_NAVY,
    EXP_BORDER,
    EXP_LABEL_BG,
    EXP_TOTAL_BG,
    EXP_WHITE,
    EXP_INK,
    EXP_INK_SOFT,
    EXP_GRAY,
    EXP_MUTED,
    EXP_FAINT,
    EXP_ERR_BG,
    EXP_ERR_TX,
    EXP_WARN_BG,
    EXP_WARN_TX,
    EXP_INFO_BG,
    EXP_INFO_TX,
    EXP_OK_BG,
    EXP_OK_TX,
    EXP_META_OK_BG,
    EXP_META_FAIL_BG,
    EXP_META_OK_TX,
    EXP_META_FAIL_TX,
    exp_fill,
)
from src.render.excel_render_calculo import _CalculoWriter
from src.ui.reconocimiento_config import NAMING
from src.ui.catalog import CATALOGO

CIPSA_RUC = "20100654025"


def _naming(tipo: str, campo: str, default: str = "") -> str:
    return NAMING.get(tipo, {}).get(campo, default)


def _build_qr_data(
    ruc_emisor: str,
    serie: str,
    numero: str,
    monto_total: float,
    fecha: str,
    ruc_adq: str,
    tipo_doc: str = "01",
) -> str:
    """Construye la cadena QR SUNAT (pipe-delimited, hash vacío como placeholder)."""
    mont_igv = round(monto_total * 0.18 / 1.18, 2)
    return "|".join(
        [
            ruc_emisor,
            tipo_doc,
            serie,
            numero,
            f"{mont_igv:.2f}",
            f"{monto_total:.2f}",
            fecha,
            "6",
            ruc_adq,
            "",
        ]
    )


def _generate_qr_image(qr_data: str, size_px: int = 120) -> Optional[BytesIO]:
    """Genera imagen PNG del QR desde datos SUNAT."""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=4, border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((size_px, size_px))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        return None


LABEL_TIPO = {k: v["titulo"] for k, v in NAMING.items()}


def _label_tipo(tipo: str) -> str:
    """Nombre amigable del tipo para títulos: prefiere el label del catálogo
    ("Descuento comercial") sobre el código crudo ("DO")."""
    caso = CATALOGO.get(tipo)
    if caso:
        return caso.label
    return LABEL_TIPO.get(tipo, tipo)


# Códigos de catálogo (UI) → clave NAMING para la justificación de sustento.
# El renderer recibe tipo=tipo_actual ("DO", ...) mientras NAMING vive por
# clave de estrategia; sin este puente el fallback nunca dispara.
TIPO_A_NAMING = {
    "DO": "descuento_precio",  # DO corre modos descuento (archivo SKU o % global)
}


def _texto_lineas(df, skus_afectados: int = 0, con_nombres: bool = False) -> str:
    """Texto de 'Líneas de producto' desde la columna LINEA (nombres válidos).

    Excel (con_nombres=False): 'N líneas · X SKU' con N = líneas distintas.
    DOCX (con_nombres=True): '01 Pelotas, 02 Forros · X SKU' (todas, sin truncar).
    Sin columna LINEA: solo 'X SKU'.
    """
    lineas: list = []
    if df is not None and not df.empty and "LINEA" in df.columns:
        lineas = sorted(
            {
                str(v).strip()
                for v in df["LINEA"].dropna().unique()
                if str(v).strip() and str(v).strip().lower() != "nan"
            }
        )
    sku_txt = f"{skus_afectados:,} SKU"
    if not lineas:
        return sku_txt
    if con_nombres:
        return f"{', '.join(lineas)} · {sku_txt}"
    return f"{len(lineas):,} líneas · {sku_txt}"


# Tipos cuya modalidad 'consolidado' agrupa por SKU y por eso la columna
# de documentos pasa de FACTURA (uno) a FACTURAS (lista). Compartido con
# excel_render_calculo para que ambos escriban los mismos encabezados.
TIPOS_CONSOLIDADOS_POR_SKU = {
    "diferencia_precio",
    "diferencia_cantidad",
    "diferencia_stock",
    "descuento_precio",
    "devolucion_fisica",
}

COLUMNAS_POR_TIPO = {
    "diferencia_precio": {
        "table1": {
            "title": "COMO SE ATENDIÓ",
            "columns": [
                {"header": "N°", "index": True, "width": 5},
                {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
                {"header": "SKU", "keys": ["SKU"], "format": "@", "center": True, "width": 12},
                {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
                {
                    "header": "CANTIDAD",
                    "keys": ["CANTIDAD_FACTURADA", "CANTIDAD"],
                    "format": "#,##0",
                    "center": True,
                    "width": 10,
                },
                {
                    "header": "PRECIO UNID.",
                    "keys": ["PRECIO_HIST_FACTURA", "PRECIO_HIST"],
                    "format": "#,##0.00000",
                    "width": 14,
                },
                {"header": "TOTAL FACTURA", "formula": True, "format": "#,##0.00", "width": 14},
            ],
        },
        "table2": {
            "title": "LISTA DE PRECIOS",
            "columns": [
                {"header": "N°", "index": True, "width": 5},
                {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
                {"header": "SKU", "keys": ["SKU"], "format": "@", "center": True, "width": 12},
                {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
                {
                    "header": "CANTIDAD",
                    "keys": ["CANTIDAD_CALCULO", "CANTIDAD"],
                    "format": "#,##0",
                    "center": True,
                    "width": 10,
                },
                {
                    "header": "PRECIO HIST. EFECTIVO",
                    "keys": ["PRECIO_HIST"],
                    "format": "#,##0.00000",
                    "width": 17,
                },
                {
                    "header": "PRECIO LISTA",
                    "keys": ["PRECIO_BASE", "PRECIO_HIST"],
                    "format": "#,##0.00000",
                    "width": 13,
                },
                {"header": "PRECIO NETO", "formula": True, "format": "#,##0.00000", "width": 14},
                {"header": "DIF. UNITARIA", "formula": True, "format": "#,##0.00000", "width": 14},
                {
                    "header": "MONTO",
                    "formula": True,
                    "format": "#,##0.00",
                    "highlight": True,
                    "width": 13,
                },
                {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 65},
                {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
            ],
        },
    },
    "descuento_precio": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
        {"header": "SKU", "keys": ["SKU"], "format": "@", "center": True, "width": 12},
        {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
        {"header": "LINEA", "keys": ["LINEA"], "format": None, "center": True, "width": 12},
        {
            "header": "CANTIDAD",
            "keys": ["CANTIDAD"],
            "format": "#,##0",
            "center": True,
            "width": 10,
        },
        {
            "header": "PRECIO ATENDIDO",
            "keys": ["PRECIO_HIST"],
            "format": "#,##0.00000",
            "width": 14,
        },
        {
            "header": "% DESC.",
            "keys": ["%_DESCUENTO", "DESC1"],
            "format": "0.00%",
            "center": True,
            "width": 10,
        },
        {
            "header": "PRECIO NETO",
            "formula": True,
            "formula_expr": "ROUND({PRECIO ATENDIDO}*(1-{% DESC.}),5)",
            "format": "#,##0.00000",
            "width": 14,
        },
        {
            "header": "DIF. UNITARIA",
            "formula": True,
            "formula_expr": "MAX(0,ROUND({PRECIO ATENDIDO}-{PRECIO NETO},5))",
            "keys": ["DIFERENCIA"],
            "format": "#,##0.00000",
            "width": 14,
        },
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "ROUND({DIF. UNITARIA}*{CANTIDAD},2)",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 13,
        },
        {"header": "GLOSA", "keys": ["ALERTA"], "format": None, "width": 65},
        {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
    ],
    "descuento_factura": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
        {"header": "SKU", "keys": ["SKU"], "format": None, "center": True, "width": 12},
        {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
        {
            "header": "CANTIDAD",
            "keys": ["CANTIDAD"],
            "format": "#,##0",
            "center": True,
            "width": 10,
        },
        {
            "header": "PRECIO UNIT.",
            "keys": ["PRECIO_UNITARIO"],
            "format": "#,##0.00000",
            "width": 14,
        },
        {
            "header": "% DESC.",
            "keys": ["%_DESCUENTO", "DESC1"],
            "format": "0.00%",
            "center": True,
            "width": 10,
        },
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "ROUND({PRECIO UNIT.}*{CANTIDAD}*{% DESC.},2)",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 13,
        },
        {"header": "GLOSA", "keys": ["ALERTA"], "format": None, "width": 65},
        {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
    ],
    "bonificacion_promocion": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["FACTURAS"], "format": None, "width": 18},
        {"header": "SKU", "keys": ["SKU"], "format": None, "center": True, "width": 12},
        {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
        {
            "header": "CANTIDAD",
            "keys": ["CANTIDAD"],
            "format": "#,##0",
            "center": True,
            "width": 12,
        },
        {"header": "REGLA", "keys": ["CICLOS"], "format": None, "center": True, "width": 10},
        {
            "header": "BONIFICACION",
            "keys": ["BONIFICACION"],
            "format": "#,##0",
            "center": True,
            "width": 12,
        },
        {
            "header": "PRECIO UNIT.",
            "keys": ["PRECIO_UNITARIO"],
            "format": "#,##0.00000",
            "width": 12,
        },
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "ROUND({BONIFICACION}*{PRECIO UNIT.},2)",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 13,
        },
        {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 70},
        {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
    ],
    "rebate_volumen": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "LÍNEA", "keys": ["LINEA"], "format": None, "width": 40},
        {"header": "SKUS", "keys": ["SKUS"], "format": None, "width": 30},
        {
            "header": "CANT. TOTAL",
            "keys": ["CANTIDAD"],
            "format": "#,##0",
            "center": True,
            "width": 12,
        },
        {
            "header": "MONTO BASE",
            "keys": ["MONTO_BASE", "SOLES"],
            "format": "#,##0.00",
            "width": 15,
        },
        {
            "header": "% DEL TOTAL",
            "formula": True,
            "formula_expr": "IF({SUM:MONTO BASE}=0,0,ROUND({MONTO BASE}/{SUM:MONTO BASE}*100,2))",
            "keys": ["%_DEL_TOTAL"],
            "format": "0.00",
            "center": True,
            "width": 11,
        },
        {"header": "% REBATE", "keys": ["%_REBATE"], "format": "0.00", "center": True, "width": 10},
        {
            "header": "MONTO REBATE",
            "formula": True,
            "formula_expr": "ROUND({MONTO BASE}*IF({% REBATE}>1,{% REBATE}/100,{% REBATE}),2)",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 15,
        },
        {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 40},
        {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
    ],
    "anular_factura": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 16},
        {"header": "SKU", "keys": ["SKU"], "format": None, "center": True, "width": 12},
        {"header": "ARTÍCULO", "keys": ["ARTICULO"], "format": None, "width": 30},
        {
            "header": "CANTIDAD",
            "keys": ["CANTIDAD"],
            "format": "#,##0",
            "center": True,
            "width": 10,
        },
        {"header": "P. UNITARIO", "keys": ["PRECIO_HIST"], "format": "#,##0.00000", "width": 13},
        {"header": "TOTAL FACTURA", "keys": ["MONTO_FACTURA"], "format": "#,##0.00", "width": 13},
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "{TOTAL FACTURA}",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 13,
        },
        {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 50},
    ],
    "devolucion_fisica": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["FACTURA", "FACTURAS"], "format": None, "width": 18},
        {"header": "SKU", "keys": ["SKU"], "format": "@", "center": True, "width": 12},
        {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 30},
        {
            "header": "CANTIDAD DEVUELTA",
            "keys": ["CANTIDAD", "CANTIDAD_DEVUELTA"],
            "format": "#,##0",
            "center": True,
            "width": 14,
        },
        {
            "header": "P. UNITARIO NETO",
            "keys": ["PRECIO_HIST"],
            "format": "#,##0.00000",
            "width": 15,
        },
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "ROUND({P. UNITARIO NETO}*{CANTIDAD DEVUELTA},2)",
            "keys": ["MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 13,
        },
        {"header": "ALERTA", "keys": ["ALERTA", "Alerta"], "format": None, "width": 50},
        {"header": "AUDITORÍA", "keys": ["AUDITORIA_NC"], "format": None, "width": 60},
    ],
    "feria_preventa": [
        {"header": "N°", "index": True, "width": 5},
        {"header": "FACTURA", "keys": ["Facturas (qty)", "FACTURAS"], "format": None, "width": 22},
        {"header": "SKU", "keys": ["SKU"], "format": None, "center": True, "width": 12},
        {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 28},
        {"header": "LINEA", "keys": ["LINEA"], "format": None, "width": 16},
        {
            "header": "CANT. SOL.",
            "keys": ["Cant. Solicitada"],
            "format": "#,##0",
            "center": True,
            "width": 10,
        },
        {
            "header": "CANT. SUST.",
            "keys": ["Cant. Sustentada", "CANT. SUSTENTAR", "CANT. SUSTENTAR"],
            "format": "#,##0",
            "center": True,
            "width": 10,
        },
        {
            "header": "% CUMPL.",
            "formula": True,
            "formula_expr": "IF({CANT. SOL.}=0,0,ROUND({CANT. SUST.}/{CANT. SOL.}*100,1))",
            "keys": ["% Cumplimiento"],
            "format": '0.0"%"',
            "center": True,
            "width": 9,
        },
        {
            "header": "P.U. HIST.",
            "keys": ["P.U. Hist.", "P.U. (SIN IGV)", "PRECIO_UNITARIO"],
            "format": "#,##0.00000",
            "width": 12,
        },
        {
            "header": "% DESC.",
            "keys": ["Desc. (%) Aplicado", "DESC. (%)", "PORCENTAJE_APLICADO"],
            "format": '0.00"%"',
            "center": True,
            "width": 9,
        },
        {
            "header": "DESC. UNIT.",
            "formula": True,
            "formula_expr": "ROUND({P.U. HIST.}*{% DESC.}/100,5)",
            "keys": ["Desc. Unit. (S/)", "DESC. UNIT. (NETO)", "MONTO_DESCUENTO_UNITARIO"],
            "format": "#,##0.00000",
            "width": 12,
        },
        {
            "header": "P.U. RESULT.",
            "formula": True,
            "formula_expr": "ROUND({P.U. HIST.}-{DESC. UNIT.},5)",
            "keys": ["P.U. Result.", "PRECIO NETO", "PRECIO_NETO_FINAL"],
            "format": "#,##0.00000",
            "width": 12,
        },
        {
            "header": "TOT. SUSTENTO",
            "keys": ["Tot. Sustento (S/)", "TOT. FACT. (NETO)", "VALOR_SOPORTE_TOTAL"],
            "format": "#,##0.00",
            "width": 14,
        },
        {
            "header": "CANT. FACTURADA",
            "keys": ["Cant. Facturada"],
            "format": "#,##0",
            "center": True,
            "width": 13,
        },
        {
            "header": "CANT. DISPONIBLE",
            "keys": ["Cant. Disponible"],
            "format": "#,##0",
            "center": True,
            "width": 13,
        },
        {
            "header": "% STOCK REST.",
            "keys": ["% Stock Restante"],
            "format": '0.0"%"',
            "center": True,
            "width": 12,
        },
        {
            "header": "MONTO",
            "formula": True,
            "formula_expr": "ROUND({DESC. UNIT.}*{CANT. SUST.},2)",
            "keys": ["Subtotal NC (S/)", "SUBTOTAL (SIN IGV)", "MONTO_NC"],
            "format": "#,##0.00",
            "highlight": True,
            "width": 14,
        },
        {"header": "GLOSA", "keys": ["Glosa"], "format": None, "width": 25},
        {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 50},
        {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
    ],
    "diferencia_stock": {
        "table1": {
            "title": "COMO SE ATENDIÓ",
            "columns": [
                {"header": "N°", "index": True, "width": 5},
                {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
                {
                    "header": "SKU",
                    "keys": ["SKU"],
                    "format": None,
                    "center": True,
                    "width": 12,
                    "erp_input": True,
                },
                {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
                {
                    "header": "CANTIDAD FACTURADA",
                    "keys": ["Cantidad Facturada"],
                    "format": "#,##0",
                    "center": True,
                    "width": 16,
                },
                {
                    "header": "CANTIDAD DISPONIBLE",
                    "keys": ["Cantidad Disponible"],
                    "format": "#,##0",
                    "center": True,
                    "width": 17,
                },
                {
                    "header": "CANTIDAD SOLICITADA",
                    "keys": ["CANTIDAD"],
                    "format": "#,##0",
                    "center": True,
                    "width": 16,
                    "erp_input": True,
                },
                {
                    "header": "STOCK SUSTENTADO",
                    "keys": ["Stock Sustentado"],
                    "format": "#,##0",
                    "center": True,
                    "width": 15,
                },
                {
                    "header": "% STOCK RESTANTE",
                    "keys": ["% Stock Restante"],
                    "format": '0.0"%"',
                    "center": True,
                    "width": 13,
                },
                {
                    "header": "PRECIO UNID.",
                    "keys": ["PRECIO_HIST", "Precio Hist."],
                    "format": "#,##0.00000",
                    "width": 14,
                },
            ],
        },
        "table2": {
            "title": "LISTA DE PRECIOS ACTUAL - DIFERENCIA A RECONOCER",
            "columns": [
                {"header": "N°", "index": True, "width": 5},
                {"header": "FACTURA", "keys": ["FACTURA"], "format": None, "width": 18},
                {
                    "header": "SKU",
                    "keys": ["SKU"],
                    "format": None,
                    "center": True,
                    "width": 12,
                    "erp_input": True,
                },
                {"header": "ARTICULO", "keys": ["ARTICULO"], "format": None, "width": 35},
                {
                    "header": "PRECIO LISTA",
                    "keys": ["Precio Lista"],
                    "format": "#,##0.00000",
                    "width": 13,
                },
                {"header": "PRECIO NETO", "formula": True, "format": "#,##0.00000", "width": 14},
                {
                    "header": "DIF. UNITARIA",
                    "formula": True,
                    "format": "#,##0.00000",
                    "width": 14,
                    "erp_input": True,
                },
                {
                    "header": "MONTO (Sin IGV)",
                    "formula": True,
                    "format": "#,##0.00",
                    "highlight": True,
                    "width": 16,
                },
                {"header": "ALERTA", "keys": ["ALERTA"], "format": None, "width": 65},
                {"header": "AUDITOR\u00cdA", "keys": ["AUDITORIA_NC"], "format": None, "width": 55},
            ],
        },
    },
}

TOTAL_NC_KEYS = {
    "diferencia_precio": "MONTO_NC",
    "descuento_precio": "MONTO_NC",
    "descuento_factura": "MONTO_NC",
    "bonificacion_promocion": "MONTO_NC",
    "rebate_volumen": "MONTO_NC",
    "anular_factura": "MONTO_NC",
    "feria_preventa": "Subtotal NC (S/)",
    "diferencia_stock": "MONTO_NC",
}

HEADER_NAVY = EXP_NAVY
LABEL_FONT = Font(bold=True, size=10, color=EXP_INK_SOFT)
VALUE_FONT = Font(size=10, color=EXP_INK_SOFT)
LABEL_FILL = exp_fill(EXP_LABEL_BG)
THIN_BORDER = Border(
    left=Side(style="thin", color=EXP_BORDER),
    right=Side(style="thin", color=EXP_BORDER),
    top=Side(style="thin", color=EXP_BORDER),
    bottom=Side(style="thin", color=EXP_BORDER),
)
S_PEN = '"S/ "#,##0.00'  # moneda con sufijo S/ visible y valor numerico


def columnas_consolidadas_dc(col_defs):
    """Variante de columnas para consolidado: FACTURA → FACTURAS.

    En consolidado cada fila agrupa un SKU de varias facturas; la columna
    muestra la lista de documentos en vez del documento único. Retorna una
    copia (no muta el catálogo global). Funciona con la tabla de dos bloques
    (DC/VRS) y con la lista plana de columnas (DO y familia de precio).
    """
    if isinstance(col_defs, list):
        nuevas = []
        for cd in col_defs:
            if cd.get("header") == "FACTURA":
                nuevas.append({"header": "FACTURAS", "keys": ["FACTURAS"], "width": 30})
            else:
                nuevas.append(cd)
        return nuevas
    if not isinstance(col_defs, dict):
        return col_defs
    nuevo = dict(col_defs)
    for tabla in ("table1", "table2"):
        bloque = nuevo.get(tabla)
        if not bloque:
            continue
        nuevas = []
        for cd in bloque["columns"]:
            if cd.get("header") == "FACTURA":
                nuevas.append({"header": "FACTURAS", "keys": ["FACTURAS"], "width": 30})
            else:
                nuevas.append(cd)
        nuevo[tabla] = {**bloque, "columns": nuevas}
    return nuevo


class ExcelRenderer(_CalculoWriter):
    """Genera Excel unificado de 1 hoja (header + calculo) para todos los tipos de NC."""

    def __init__(self):
        self.wb = Workbook()
        self.wb.properties.creator = "ccusi"
        self.wb.properties.description = "Generado por G360"
        self.ws = self.wb.active
        self.ws.title = "NC"
        self.fmt_num = EXCEL_FMT_NUMBER
        self.fmt_currency = EXCEL_FMT_CURRENCY
        # Referencias de celdas para vincular header ↔ tabla
        self._hdr_sub_row = None  # fila del subtotal en header
        self._hdr_igv_row = None  # fila del IGV en header
        self._hdr_tot_row = None  # fila del total en header
        self._hdr_sub_col = None  # col del subtotal en header (tv_col=G=7)
        self._hdr_igv_col = None  # col del IGV en header (tv_col=G=7)
        self._hdr_tot_col = None  # col del total en header (tv_col=G=7)
        self.fmt_pct = EXCEL_FMT_PCT

    def generar(
        self,
        resultado: RecognitionResult,
        ruta_salida: str,
        tipo: str = "",
        cliente: str = "",
        cliente_id: str = "",
        vendedor: str = "",
        motivo: str = "",
        doc_ref: str = "",
        ruc: str = "",
        archivo: str = "",
        df_historial: Optional[pd.DataFrame] = None,
        periodo: str = "",
        antecedentes: str = "",
        observaciones: str = "",
        modalidad: str = "individual",
    ) -> Path:
        # Prioriza dataframe_excel (reporte detallado 13 cols) si esta disponible.
        # Si no, cae a dataframe (vista previa) para retro-compatibilidad.
        df = resultado.get_excel()
        if df.empty:
            return Path(ruta_salida)

        # Normalizar nombres de columnas de descuento a formato estandar DESC1, DESC2...
        # Se registra la procedencia: si un DESC visible viene de un compuesto
        # redondeado (DESCUENTO_COMPUESTO), la hoja congela PRECIO NETO para no
        # descuadrar contra el docx; con DESC exactos va fórmula viva.
        _desc_alias = {
            "DESCUENTO_COMPUESTO": "DESC1",
            "%_DESCUENTO": "DESC1",
            "DESC_REQ": "DESC1",
        }
        _desc_origen = {}
        for old, new in _desc_alias.items():
            if old in df.columns and new not in df.columns:
                df = df.rename(columns={old: new})
                _desc_origen[new] = old

        out_path = Path(ruta_salida)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not doc_ref or doc_ref == "NC":
            doc_ref = self._extraer_doc_ref(df)
        if (not doc_ref or doc_ref == "NC") and tipo == "rebate_volumen" and resultado.metricas:
            doc_ref = resultado.metricas.get("factura_ref", "")

        if doc_ref and doc_ref != "NC":
            self.ws.title = f"NC_{doc_ref}"[:31]

        nc_key = TOTAL_NC_KEYS.get(tipo, "MONTO_NC")
        total_nc = (
            float(df[nc_key].apply(lambda x: round(float(x), 2)).sum())
            if nc_key in df.columns
            else 0
        )
        skus_afectados = resultado.resumen.get("skus_afectados", len(df))
        cantidad_reconocida = float(df["CANTIDAD"].sum()) if "CANTIDAD" in df.columns else 0

        if not cliente or cliente == "CLIENTE":
            cliente = self._extraer_cliente(df) or cliente
        if not cliente or cliente == "CLIENTE" and df_historial is not None:
            cliente = self._extraer_cliente(df_historial) or cliente

        if not ruc:
            ruc = self._extraer_ruc(df)

        # Compute max_col for header merging (two‑table layout needs DESC injection count)
        col_defs = COLUMNAS_POR_TIPO.get(tipo, COLUMNAS_POR_TIPO["diferencia_precio"])
        if tipo in TIPOS_CONSOLIDADOS_POR_SKU and modalidad == "consolidado":
            col_defs = columnas_consolidadas_dc(col_defs)
        if isinstance(col_defs, dict):
            desc_cols_count = (
                len(
                    [
                        c
                        for c in df.columns
                        if re.match(r"^DESC\d+$", c) and not (df[c].fillna(0) == 0).all()
                    ]
                )
                if not df.empty
                else 0
            )
            max_col = max(
                len(col_defs["table1"]["columns"]),
                len(col_defs["table2"]["columns"]) + desc_cols_count,
            )
        else:
            max_col = len(col_defs)

        r = self._escribir_header(
            tipo=tipo,
            cliente=cliente,
            vendedor=vendedor,
            doc_ref=doc_ref,
            skus_afectados=skus_afectados,
            total_nc=total_nc,
            cantidad_reconocida=cantidad_reconocida,
            ruc=ruc,
            archivo=archivo,
            nc_detalle=(resultado.metricas or {}).get("nc_detalle", ""),
            df=df,
            max_col=max_col,
            periodo=periodo,
            antecedentes=antecedentes,
            observaciones=observaciones,
        )

        if tipo == "rebate_volumen" and resultado.metricas:
            m = resultado.metricas
            total_venta = m.get("total_venta", 0)
            meta = m.get("meta", 0)
            pct = m.get("porcentaje_rebate", 0)
            cumplida = total_venta >= meta if meta > 0 else True
            meta_fill = PatternFill(
                start_color=EXP_META_OK_BG if cumplida else EXP_META_FAIL_BG,
                end_color=EXP_META_OK_BG if cumplida else EXP_META_FAIL_BG,
                fill_type="solid",
            )
            ws = self.ws

            ws.cell(row=r, column=1, value="META (S/)").font = LABEL_FONT
            ws.cell(row=r, column=1).fill = LABEL_FILL
            ws.cell(row=r, column=1).border = THIN_BORDER
            c = ws.cell(row=r, column=2, value=f"S/ {meta:,.2f}")
            c.font = Font(bold=True, size=11, color=HEADER_NAVY)
            c.border = THIN_BORDER
            c.fill = meta_fill
            ws.merge_cells(f"B{r}:D{r}")
            ws.cell(row=r, column=5, value="TOTAL VENTA (S/)").font = LABEL_FONT
            ws.cell(row=r, column=5).fill = LABEL_FILL
            ws.cell(row=r, column=5).border = THIN_BORDER
            c2 = ws.cell(row=r, column=6, value=f"S/ {total_venta:,.2f}")
            c2.font = Font(
                bold=True, size=11, color=EXP_META_OK_TX if cumplida else EXP_META_FAIL_TX
            )
            c2.border = THIN_BORDER
            c2.fill = meta_fill
            r += 1

            ws.cell(row=r, column=1, value="% REBATE").font = LABEL_FONT
            ws.cell(row=r, column=1).fill = LABEL_FILL
            ws.cell(row=r, column=1).border = THIN_BORDER
            ws.cell(row=r, column=2, value=f"{pct:.1f}%").font = VALUE_FONT
            ws.cell(row=r, column=2).border = THIN_BORDER
            ws.merge_cells(f"B{r}:D{r}")
            estado = "META CUMPLIDA - Aplica rebate" if cumplida else "META NO ALCANZADA - Revisar"
            ws.cell(row=r, column=5, value=estado).font = Font(
                bold=True, size=11, color=EXP_META_OK_TX if cumplida else EXP_META_FAIL_TX
            )
            ws.cell(row=r, column=5).fill = meta_fill
            ws.cell(row=r, column=5).border = THIN_BORDER
            ws.cell(row=r, column=6, value="").border = THIN_BORDER
            ws.merge_cells(f"E{r}:F{r}")
            r += 1

            if cumplida:
                ws.cell(row=r, column=1, value="CÁLCULO REBATE").font = LABEL_FONT
                ws.cell(row=r, column=1).fill = LABEL_FILL
                ws.cell(row=r, column=1).border = THIN_BORDER
                formula_text = (
                    f"S/ {total_venta:,.2f} × {pct:.1f}% = S/ {total_venta * pct / 100:,.2f}"
                )
                ws.cell(row=r, column=2, value=formula_text).font = Font(
                    bold=True, size=10, color=HEADER_NAVY
                )
                ws.cell(row=r, column=2).border = THIN_BORDER
                ws.merge_cells(f"B{r}:F{r}")
                r += 1

        r = self._escribir_calculo(
            df=df,
            tipo=tipo,
            alertas=resultado.alertas,
            start_row=r,
            desc_origen=_desc_origen,
            modalidad=modalidad,
        )

        # Unique documents list (reusable across strategies)
        docs_unicos = resultado.resumen.get("documentos_unicos", [])
        titulo_docs = resultado.resumen.get("titulo_documentos", "DOCUMENTOS CONSULTADOS")
        if docs_unicos:
            r = self._escribir_documentos_consultados(docs_unicos, titulo_docs, start_row=r + 1)

        self.ws.sheet_view.showGridLines = False
        # Márgenes laterales simétricos 0.8" (~2.0 cm, igual que el DOCX).
        self.ws.page_margins.left = 0.8
        self.ws.page_margins.right = 0.8
        # Columna A a 15.00 (~110 px): etiquetas del encabezado y N°.
        self.ws.column_dimensions["A"].width = 15
        # Hoja Resumen de facturas comprometidas
        resumen_comp = resultado.resumen.get("resumen_comprometidas")
        if resumen_comp is not None and not resumen_comp.empty:
            self._escribir_resumen_comprometidas(resumen_comp)
        try:
            self.wb.save(str(out_path))
        except PermissionError:
            raise PermissionError(
                f"No se pudo guardar el archivo. ¿Está abierto en otro programa?\n"
                f"Cierre el archivo e intente nuevamente.\nRuta: {out_path}"
            )
        self._escribir_hoja_formatos(tipo, df)
        return out_path

    def _extraer_doc_ref(self, df):
        for col in ("FACTURA", "FACTURAS"):
            if col in df.columns:
                val = str(df[col].iloc[0])
                if val and val != "nan":
                    return val
        return ""

    def _extraer_cliente(self, df):
        for col in ("CLIENTE", "NOM_CLIENTE"):
            if col in df.columns:
                val = str(df[col].iloc[0])
                if val and val != "nan":
                    return val
        return ""

    @staticmethod
    def _extraer_ruc(df) -> str:
        if df.empty or "DOC_CLIENTE" not in df.columns:
            return ""
        for val in df["DOC_CLIENTE"].dropna().unique():
            s = str(val).strip()
            if s and s.lower() not in ("nan", "none", ""):
                return s
        return ""

    def _escribir_resumen_comprometidas(self, df_resumen):
        """Escribe hoja 'Resumen' con facturas comprometidas por NC/ND."""
        ws_res = self.wb.create_sheet("Resumen")
        gs = G360Styles()
        headers = [
            "FACTURA",
            "CLIENTE",
            "TOTAL FACTURA",
            "CANT. VENDIDA",
            "SKUS",
            "NC EXIST.",
            "NC MONTO",
            "DOCS NC",
            "% NC",
            "ESTADO",
        ]
        for col, h in enumerate(headers, 1):
            c = ws_res.cell(row=1, column=col, value=h)
            c.font = gs.header_font
            c.fill = gs.header_fill
            c.alignment = Alignment(horizontal="center")
            c.border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin"),
            )
        widths = [18, 25, 14, 12, 6, 8, 12, 25, 8, 14]
        for i, w in enumerate(widths):
            ws_res.column_dimensions[chr(65 + i)].width = w
        for row_idx, (_, row) in enumerate(df_resumen.iterrows(), 2):
            for col, key in enumerate(
                [
                    "FACTURA",
                    "CLIENTE",
                    "TOTAL_FACTURA",
                    "CANTIDAD_VENDIDA",
                    "SKUS",
                    "NC_CANTIDAD",
                    "NC_MONTO",
                    "NC_DOCS",
                    "%_NC",
                    "ESTADO",
                ],
                1,
            ):
                val = row.get(key, "")
                if isinstance(val, float):
                    val = round(val, 2) if key != "%_NC" else val
                c = ws_res.cell(row=row_idx, column=col, value=val)
                c.border = Border(
                    left=Side(style="thin", color="BFBFBF"),
                    right=Side(style="thin", color="BFBFBF"),
                    top=Side(style="thin", color="BFBFBF"),
                    bottom=Side(style="thin", color="BFBFBF"),
                )
                if key == "ESTADO":
                    if val == "comprometida":
                        c.fill = PatternFill(
                            start_color=EXP_ERR_BG, end_color=EXP_ERR_BG, fill_type="solid"
                        )
                    else:
                        c.fill = PatternFill(
                            start_color=EXP_OK_BG, end_color=EXP_OK_BG, fill_type="solid"
                        )
                if key == "%_NC":
                    c.number_format = "0.0%"
                if key in ("TOTAL_FACTURA", "NC_MONTO"):
                    c.number_format = "#,##0.00"
        ws_res.sheet_view.showGridLines = False

    def _insert_qr_anf(
        self, df: pd.DataFrame, doc_ref: str, monto_total: float, ruc_cliente: str
    ) -> Optional[BytesIO]:
        """Genera imagen QR SUNAT paraANF, referenciando la factura original."""
        # Parsear serie y numero de doc_ref (formato "F204-64777")
        serie, numero = "", ""
        if "-" in doc_ref:
            serie_part, numero = doc_ref.rsplit("-", 1)
            serie = serie_part[1:] if len(serie_part) > 1 else serie_part  # "204" from "F204"
        # Extraer fecha de la factura desde el dataframe
        fecha = ""
        for col in ("FECHA", "fecha"):
            if col in df.columns and not df[col].isna().all():
                val = df[col].iloc[0]
                if hasattr(val, "date"):
                    fecha = val.date().isoformat()
                else:
                    fecha = str(val)[:10]
                break
        if not fecha:
            fecha = datetime.now().strftime("%Y-%m-%d")
        qr_data = _build_qr_data(
            CIPSA_RUC, serie, numero, monto_total, fecha, ruc_cliente or CIPSA_RUC
        )
        return _generate_qr_image(qr_data, size_px=110)

    def _escribir_header(
        self,
        tipo,
        cliente,
        vendedor,
        doc_ref,
        skus_afectados,
        total_nc,
        cantidad_reconocida,
        ruc="",
        archivo="",
        nc_detalle="",
        max_col=0,
        df=None,
        periodo="",
        antecedentes="",
        observaciones="",
    ):
        """Header corporativo: barra título, DATOS DE LA OPERACIÓN (izq.) y
        TOTALES (der.) en formulas SUM vivas; antecedentes debajo."""
        ws = self.ws
        label_tipo = _label_tipo(tipo).replace("NC por ", "")
        subtitulo = _naming(tipo, "subtitulo", "")
        if max_col == 0:
            cd = COLUMNAS_POR_TIPO.get(tipo, COLUMNAS_POR_TIPO["diferencia_precio"])
            if isinstance(cd, list):
                max_col = len(cd)
            else:
                max_col = max(
                    len(cd["table1"]["columns"]),
                    len(cd["table2"]["columns"]) + 8,
                )
        last_letter = get_column_letter(max_col)

        # ── Fila 1: Título principal (barra navy) ───────────────────────
        ws.cell(row=1, column=1, value=f"SUSTENTO COMERCIAL — {label_tipo}").font = Font(
            bold=True, size=14, color=EXP_WHITE
        )
        ws.cell(row=1, column=1).fill = PatternFill(
            start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
        )
        ws.merge_cells(f"A1:{last_letter}1")
        ws.row_dimensions[1].height = 28

        # ── Fila 2: Subtitulo + fecha ─────────────────────────────────
        r2 = 2
        ws.cell(row=r2, column=1, value=subtitulo or "").font = Font(
            size=10, color=EXP_MUTED, italic=True
        )
        ws.cell(row=r2, column=1).fill = PatternFill(
            start_color=EXP_WHITE, end_color=EXP_WHITE, fill_type="solid"
        )
        ws.cell(row=r2, column=1).border = THIN_BORDER
        # Fecha en la columna mas a la derecha disponible
        fecha_col = max(2, max_col)
        ws.cell(
            row=r2, column=fecha_col, value=f"Fecha: {datetime.now().strftime('%d/%m/%Y')}"
        ).font = Font(size=9, color=EXP_FAINT)
        ws.cell(row=r2, column=fecha_col).alignment = Alignment(horizontal="right")

        # ── Bloque DATOS DE LA OPERACIÓN (izq.) + TOTALES (der.) ───────
        r = 3
        tl_col = min(6, max_col - 1)  # columna donde empiezan los totales
        tv_col = tl_col + 1

        def _panel_title(row, col, text, end_col):
            c = ws.cell(row=row, column=col, value=text)
            c.font = Font(bold=True, size=10, color=EXP_WHITE)
            c.fill = PatternFill(start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid")
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = THIN_BORDER
            if end_col > col:
                ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=end_col)
            return c

        def _field(row, col, val_col, lbl, val, bold=False):
            c_lbl = ws.cell(row=row, column=col, value=lbl)
            c_lbl.font = LABEL_FONT
            c_lbl.alignment = Alignment(horizontal="right", vertical="center")
            c_lbl.fill = LABEL_FILL
            c_lbl.border = THIN_BORDER
            c_val = ws.cell(row=row, column=val_col, value=val)
            c_val.font = Font(bold=bold, size=10, color=EXP_INK)
            c_val.border = THIN_BORDER

        _panel_title(r, 1, "DATOS DE LA OPERACIÓN", 4)
        _panel_title(r, tl_col, "TOTALES", tv_col)

        _field(4, 1, 2, "Cliente", cliente or "—")
        _field(5, 1, 2, "RUC", ruc or "—")
        _field(6, 1, 2, "Vendedor", vendedor or "—")
        _field(7, 1, 2, "Periodo", periodo or "—")
        _field(8, 1, 2, "Líneas de producto", _texto_lineas(df, skus_afectados))
        _field(9, 1, 2, "Doc. de referencia", doc_ref or "—", bold=True)

        def _total_row(row, lbl, style_bold=False, fill=EXP_LABEL_BG):
            c_lbl = ws.cell(row=row, column=tl_col, value=lbl)
            c_lbl.font = Font(bold=True, size=10, color=EXP_INK_SOFT)
            c_lbl.alignment = Alignment(horizontal="right", vertical="center")
            c_lbl.fill = PatternFill(start_color=fill, end_color=fill, fill_type="solid")
            c_lbl.border = THIN_BORDER
            c_val = ws.cell(row=row, column=tv_col, value=0)
            c_val.number_format = S_PEN
            c_val.font = Font(bold=style_bold, size=11, color=EXP_ERR_TX if style_bold else EXP_INK)
            c_val.border = THIN_BORDER

        _total_row(4, "Subtotal (Sin IGV):")
        _total_row(5, "IGV (18%):")
        _total_row(6, "TOTAL (con IGV):", style_bold=True, fill=EXP_TOTAL_BG)

        # Referencias para _fijar_totales_formulas() y el bloque de totales
        # del pie (los totales SON formulas SUM sobre la columna MONTO).
        self._hdr_sub_row, self._hdr_igv_row, self._hdr_tot_row = 4, 5, 6
        self._hdr_sub_col = self._hdr_igv_col = self._hdr_tot_col = tv_col

        r = 10  # fila en blanco antes del bloque de sustento

        # ── Sustento (solo narrativa; sin bloque de observaciones: estas
        # quedan en el DOCX §4). Prioridad: antecedentes del usuario >
        # justificación por tipo del catálogo > texto genérico. El parámetro
        # `observaciones` se recibe por compatibilidad pero no se imprime aquí.
        just_text = (
            (antecedentes or "").strip()
            or _naming(TIPO_A_NAMING.get(tipo, tipo), "justificacion", "").strip()
            or (
                "Se sustenta el reconocimiento comercial mediante la emisión "
                "de la Nota de Crédito por el importe calculado."
            )
        )
        ws.cell(row=r, column=1, value=just_text).font = Font(size=10, italic=True, color=EXP_GRAY)
        ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(f"A{r}:{last_letter}{r}")
        # Altura según contenido: ~1 línea cada N caracteres del ancho fusión.
        ancho_chars = 14 + 28 + max(0, max_col - 2) * 8.43
        n_lineas = (len(just_text) + int(ancho_chars) - 1) // max(60, int(ancho_chars))
        ws.row_dimensions[r].height = min(150, max(30, n_lineas * 15))
        r += 2

        # ── QR Code SUNAT (ANF: referencia a factura original) ─────────
        if tipo == "anular_factura":
            r_qr = r + 1
            qr_buf = self._insert_qr_anf(df, doc_ref, total_nc, ruc or "")
            if qr_buf:
                qr_img = XlImage(qr_buf)
                qr_img.width = 110
                qr_img.height = 110
                ws.add_image(qr_img, f"{get_column_letter(max_col)}{r_qr}")
                ws.cell(row=r_qr, column=max_col, value="QR SUNAT\n(Factura ref.)").font = Font(
                    size=7, color=EXP_FAINT, italic=True
                )
                ws.cell(row=r_qr, column=max_col).alignment = Alignment(horizontal="center")
            r = r_qr + 2

        # Column widths
        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 28

        return r

    def _fijar_totales_formulas(self, col_letter: str, first_row: int, last_row: int) -> None:
        """Inyecta formulas SUM/Round en las celdas TOTALES del header.

        Se llama DESPUES de escribir la(s) tabla(s), cuando se conoce el rango
        real de la columna MONTO. Si el usuario elimina filas de la tabla,
        el rango de la formula se contrae y los totales se recalculan solos
        (SUM/ROUND) sin tocar el archivo.
        """
        if self._hdr_sub_row is None:
            return
        ws = self.ws
        sub = f"{get_column_letter(self._hdr_sub_col)}{self._hdr_sub_row}"
        igv = f"{get_column_letter(self._hdr_igv_col)}{self._hdr_igv_row}"
        tot = f"{get_column_letter(self._hdr_tot_col)}{self._hdr_tot_row}"
        lo, hi = sorted((first_row, last_row))
        rng = f"{col_letter}{lo}:{col_letter}{hi}"
        ws[sub] = f"=SUM({rng})"
        ws[igv] = f"=ROUND(${sub}*0.18,2)"
        ws[tot] = f"=ROUND(${sub}+${igv},2)"
        for ref in (sub, igv, tot):
            ws[ref].number_format = S_PEN

    def _obtener_valor(self, row, *keys, default=0.0):
        for k in keys:
            v = row.get(k)
            if v is not None and str(v) not in ("", "nan", "None"):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
        return default

    def _estado(self, alerta_text):
        texto = str(alerta_text or "").strip()
        for codigo, info in CATALOGO_ALERTAS.items():
            if texto.startswith(codigo):
                if info["tipo"] == "error":
                    return "ERROR"
                if info["tipo"] == "warning":
                    return "ALERTA"
                if info["tipo"] == "info":
                    return "INFO"
        return "OK"

    def _color_estado(self, cell, estado):
        if estado == "ERROR":
            cell.fill = exp_fill(EXP_ERR_BG)
            cell.font = Font(bold=True, color=EXP_ERR_TX, size=10)
        elif estado == "ALERTA":
            cell.fill = exp_fill(EXP_WARN_BG)
            cell.font = Font(bold=True, color=EXP_WARN_TX, size=10)
        elif estado == "INFO":
            cell.fill = exp_fill(EXP_INFO_BG)
            cell.font = Font(bold=True, color=EXP_INFO_TX, size=10)
        elif estado == "OK":
            cell.fill = exp_fill(EXP_OK_BG)
            cell.font = Font(bold=True, color=EXP_OK_TX, size=10)

    def _escribir_documentos_consultados(
        self, documentos: list, titulo: str = "FACTURAS CONSULTADAS", start_row: int = 0
    ) -> int:
        """Lista los documentos únicos debajo del cálculo. Reutilizable entre estrategias.

        Args:
            documentos: Lista de IDs de documento (ej. ["F204-22928", "F204-22929"])
            titulo: Título de la sección (ej. "FACTURAS CONSULTADAS", "DOCUMENTOS REVISADOS")
            start_row: Fila desde la cual empezar a escribir

        Returns:
            Siguiente fila disponible
        """
        if not documentos:
            return start_row

        ws = self.ws
        r = start_row

        # Format: "Documentos únicos procesados: F204-22928, F204-22929, ..."
        txt_docs = f"{titulo}: {', '.join(documentos)}"
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        c = ws.cell(row=r, column=1, value=txt_docs)
        c.font = Font(italic=True, size=9, color=EXP_GRAY)

        return r + 1

    def _escribir_hoja_formatos(self, tipo: str, df_result: pd.DataFrame) -> None:
        """Añade hoja 'Formatos Esperados' con esquema según el tipo de caso.

        Cada caso tiene insumos distintos (lista con precios, % descuento,
        requerimientos feria, stock). Esta hoja explica qué columnas debe
        traer cada archivo que carga el usuario en INSUMOS.
        """
        ws = self.wb.create_sheet("Formatos Esperados")
        nav_y = HEADER_NAVY
        thin = Border(
            left=Side(style="thin", color=EXP_BORDER),
            right=Side(style="thin", color=EXP_BORDER),
            top=Side(style="thin", color=EXP_BORDER),
            bottom=Side(style="thin", color=EXP_BORDER),
        )
        header_fill = PatternFill(start_color=nav_y, end_color=nav_y, fill_type="solid")
        header_font = Font(bold=True, size=10, color=EXP_WHITE)
        hint_font = Font(size=9, color=EXP_FAINT, italic=True)
        note_font = Font(size=10, color=EXP_INK_SOFT)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)

        def _section(row, title):
            ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=12, color=nav_y)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)

        def _col(row, idx, name, desc):
            c = ws.cell(row=row, column=idx, value=name)
            c.font = header_font
            c.fill = header_fill
            c.alignment = center
            c.border = thin
            d = ws.cell(row=row, column=idx + 1, value=desc)
            d.font = hint_font
            d.border = thin
            d.alignment = left_align

        def _ex(row, vals):
            for i, v in enumerate(vals):
                c = ws.cell(row=row, column=i + 1, value=v)
                c.font = Font(size=9, color=EXP_GRAY)
                c.border = thin
                c.alignment = center if i == 0 else left_align

        # ── Columnas siempre requeridas del HISTORIAL ───────────────────
        r = 1
        _section(r, "HISTORIAL — Columnas mínimas (siempre necesarias)")
        r += 2
        hist_cols = [
            ("COD_CLIENTE", "ID único del cliente (ej: 00012345)"),
            ("CLIENTE", "Razón social del cliente"),
            ("TIPO_DOC", "F01, F31, B01, NCR, NDB…"),
            ("SERIE", "Serie documento (ej: 01, 03, 204)"),
            ("NUMERO", "Número documento (ej: 123456)"),
            ("FECHA", "Fecha emisión DD/MM/AAAA"),
            ("CODIGO", "SKU / código del artículo"),
            ("ARTICULO", "Descripción del artículo"),
            ("CANTIDAD", "Cantidad vendida (positivo)"),
            ("SOLES", "Monto total soles de la línea"),
            ("PRECIO_UNITARIO", "Precio unitario (se recalcula si falta)"),
        ]
        for nm, ds in hist_cols:
            _col(r, 1, nm, ds)
            r += 1

        # ── Esquema específico por tipo de caso ────────────────────────
        caso_especifico = {
            "diferencia_precio": {
                "titulo": "LISTA DE PRECIOS — Diferencia de Costo (SKU + precio + descuentos)",
                "columnas": [
                    ("SKU", "Código del artículo (debe coincidir con CODIGO del historial)"),
                    ("PRECIO_BASE", "Precio base sin IGV (ej: 5.14)"),
                    ("DESC1", "Descuento 1 decimal (0.25 = 25%)"),
                    ("DESC2", "Descuento 2 decimal (opcional)"),
                    ("DESC3", "Descuento 3 decimal (opcional)"),
                    ("DESC4", "Descuento 4 decimal (opcional)"),
                ],
                "ejemplo": ["03108", "5.14", "0.25", "0.04", "", ""],
                "notas": [
                    "• PRECIO_BASE es obligatorio; sin él la lista no se procesa.",
                    "• Las cantidades siempre se toman de las facturas (una eventual columna CANTIDAD se ignora).",
                    "• Modalidad Individual: una fila por línea de factura. Consolidado: una fila por SKU (monto por suma exacta).",
                    "• Descuentos en decimal: 0.25 = 25%, NO 25.",
                    "• Acepta alias: PRECIO_LISTA, PRECIOS → PRECIO_BASE.",
                ],
            },
            "descuento_precio": {
                "titulo": "LISTA DE DESCUENTOS — Descuento Comercial (sin lista de precios)",
                "columnas": [
                    ("SKU", "Código del artículo del historial al cual se aplica el descuento"),
                    ("DESCUENTO_PORCENTAJE", "Porcentaje de descuento (0.05 = 5%; acepta 5 = 5%)"),
                ],
                "ejemplo": ["03108", "5.0"],
                "notas": [
                    "• Solo se necesita SKU + porcentaje; no hay PRECIO_BASE ni cadena de descuentos.",
                    "• El % se aplica directamente al precio histórico: NC = PRECIO_HIST × %DESC × CANTIDAD.",
                    "• Un mismo % puede aplicarse a múltiples SKUs de diferentes cliente/factura.",
                    "• También se acepta un solo valor global (sin SKU) que aplica a todo el fragmento.",
                    "• Alias aceptados: %, DESCUENTO, PCT_DESC.",
                ],
            },
            "feria_preventa": {
                "titulo": "REQUERIMIENTOS — Feria / Preventa (sustento por cantidad)",
                "columnas": [
                    ("CODIGO_SKU", "SKU que se requiere sustentar"),
                    ("CANTIDAD_A_SUSTENTAR", "Total unidades a cubrir con facturas del historial"),
                    (
                        "DESCUENTO_PORCENTAJE",
                        "Descuento aplicable sobre la diferencia (opcional, 0 si no aplica)",
                    ),
                ],
                "ejemplo": ["03108", "100", "0.05"],
                "notas": [
                    "• Se carga un archivo por cada evento/preventa.",
                    "• Las cantidades se asignan LIFO/FIFO a facturas del historial.",
                    "• Si el requerimiento no trae descuento, se usa precio neto tal cual.",
                ],
            },
            "diferencia_stock": {
                "titulo": "RECONOCIMIENTO POR STOCK (VRS) — Cantidad y precio por SKU",
                "columnas": [
                    ("CODIGO", "SKU del artículo a reconocer"),
                    ("CANTIDAD", "Unidades determinadas a reconocer por SKU"),
                    ("PRECIO_BASE", "Precio de lista vigente antes de descuentos"),
                    ("DESC01", "Descuento 1 (fracción, ej: 0.05 = 5%) — opcional"),
                    ("DESC02", "Descuento 2 (fracción) — opcional"),
                ],
                "ejemplo": ["72015", "12954", "4.80", "0.05", "0"],
                "notas": [
                    "• Un archivo combinado: una fila por SKU con cantidad y precio.",
                    "• Opcionalmente un segundo archivo SKU + CANTIDAD pisa las cantidades.",
                    "• Se compara el precio histórico de compra vs precio de lista actual.",
                    "• La asignación FIFO/LIFO se hace sobre las compras históricas del SKU.",
                ],
            },
            "bonificacion_promocion": {
                "titulo": "PROMOCIÓN / MECÁNICA — Bonificación 12+1, 48+1, etc.",
                "columnas": [
                    ("SKU", "Artículo promocionado"),
                    ("PRECIO_UNITARIO", "Precio de venta sin IGV"),
                    ("REGLA_PROMOCIONAL", "Mecánica: 12+1, 48+1, 6+1, etc."),
                    ("UNIDADES_X_PRESENTACION", "Unidades por pack (default 1)"),
                ],
                "ejemplo": ["A001", "3.50", "12+1", "1"],
                "notas": [
                    "• Se calcula cuántas unidades bonificadas corresponden por la regla.",
                    "• La bonificación se sustenta con facturas del período de la promo.",
                    "• El precio unitario se usa para calcular el monto NC de las unidades bonificadas.",
                ],
            },
            "rebate_volumen": {
                "titulo": "META / REBATE — Concurso de Venta por Línea",
                "columnas": [
                    ("LINEA", "Línea de producto (ej: BEBIDAS, SNACKS)"),
                    ("META_MONTO_SOLES", "Meta alcanzable en soles para el período"),
                    ("PORCENTAJE_REBATE", "Porcentaje de rebate (0.05 = 5%; acepta 5.0 = 5%)"),
                ],
                "ejemplo": ["BEBIDAS", "50000", "5.0"],
                "notas": [
                    "• No se requiere archivo externo si la meta ya está en el sistema.",
                    "• El cálculo se hace por línea de producto seleccionada.",
                    "• El rebate se aplica sobre el volumen acumulado vs meta.",
                ],
            },
            "anular_factura": {
                "titulo": "ANULACIÓN DE FACTURA — NC 100% del valor",
                "columnas": [],
                "ejemplo": [],
                "notas": [
                    "• No requiere insumo adicional — la factura se selecciona en DATOS DEL CASO.",
                    "• El expediente generará NC por el 100% del valor de la factura referenciada.",
                    "• La modalidad Individual genera un expediente por factura; Consolidado agrupa.",
                ],
            },
            "devolucion_fisica": {
                "titulo": "DEVOLUCIÓN FÍSICA — Asignación LIFO de devoluciones",
                "columnas": [
                    ("SKU", "Artículo devuelto"),
                    ("CANTIDAD_DEVUELTA", "Cantidad física devuelta"),
                    (
                        "FECHA_DEVOLUCION",
                        "Fecha de devolución (opcional, se infiere del historial)",
                    ),
                ],
                "ejemplo": ["03108", "50", ""],
                "notas": [
                    "• Las cantidades devueltas se asignan LIFO a facturas del mismo SKU.",
                    "• El precio neto de la NC es el precio histórico de la factura asignada.",
                    "• Si ya existe una NC total previa para esas unidades, se descuenta.",
                ],
            },
        }

        esquema = caso_especifico.get(tipo, caso_especifico["diferencia_precio"])
        _section(r, esquema["titulo"])
        r += 2
        if esquema["columnas"]:
            _col(r, 1, "COLUMNA", "DESCRIPCION")
            r += 1
            for nm, ds in esquema["columnas"]:
                _col(r, 1, nm, ds)
                r += 1
            if esquema["ejemplo"]:
                r += 1
                _section(r, "EJEMPLO")
                r += 1
                _ex(r, esquema["ejemplo"])
                r += 2
        _section(r, "NOTAS")
        r += 1
        for nota in esquema["notas"]:
            ws.cell(row=r, column=1, value=nota).font = note_font
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
            r += 1

        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 58
        ws.freeze_panes = "A2"
