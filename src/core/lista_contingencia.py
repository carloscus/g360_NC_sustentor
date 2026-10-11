# -*- coding: utf-8 -*-
"""Lector de CONTINGENCIA para listas de precios sin plantilla.

La plantilla oficial (Lista_de_Precios.xlsx, con CODIGO_SKU/PRECIO_BASE) es el
camino normal y NO se toca. Esto existe solo para archivos de proveedor que no
la cumplen: sin encabezados, con filas de categoria entre los datos, con filas
vacias, y repartidos en varias hojas con distinto layout.

Regla verificada fila por fila sobre el caso real (VINIFAN 2027, 819 filas):
una fila es dato si el PRECIO es numerico y el CODIGO no esta vacio. Da los
634 SKU exactos, sin falsos positivos ni negativos. Acepta SKU alfanumericos
(no exige digitos) y se salta vacias, categorias y headers, porque en todas
esas el precio no es numero.

El despachador `leer_lista_precios` prueba primero el camino normal y solo si
el resultado no parece una lista (columnas basura tipo "FORROS VINIFAN" +
"Unnamed", o sin filas utiles) cae a la contingencia. Devuelve (df, origen)
para que la UI pueda decir cual se uso.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.core.utils import read_erp_file

log = logging.getLogger(__name__)

_COLS_SKU = {"CODIGO_SKU", "CODIGO", "CÓDIGO", "SKU"}
_COLS_PRECIO = {"PRECIO_BASE", "PRECIO", "PRECIO_CORRECTO_SIN_IGV", "PRECIO DE LISTA"}


def _normaliza_nombre(c) -> str:
    return str(c or "").strip().upper()


def _parece_lista(df: pd.DataFrame | None) -> bool:
    """¿El DataFrame leido por el camino normal sirve como lista de precios?"""
    if df is None or df.empty:
        return False
    cols = {_normaliza_nombre(c) for c in df.columns}
    tiene_sku = bool(cols & _COLS_SKU)
    tiene_precio = bool(cols & _COLS_PRECIO) or any("PRECIO" in c for c in cols)
    if not (tiene_sku and tiene_precio):
        return False
    col_sku = next((c for c in df.columns if _normaliza_nombre(c) in _COLS_SKU), None)
    col_pre = next(
        (
            c
            for c in df.columns
            if _normaliza_nombre(c) in _COLS_PRECIO or "PRECIO" in _normaliza_nombre(c)
        ),
        None,
    )
    if col_sku is None or col_pre is None:
        return False
    sku_ok = df[col_sku].astype(str).str.strip().replace({"nan": "", "None": "", "none": ""}) != ""
    pre_ok = pd.to_numeric(df[col_pre], errors="coerce").notna()
    return bool((sku_ok & pre_ok).any())


def _fila_header(fila: pd.Series) -> int:
    """Indice de la celda con CODIGO y de la celda con PRECIO, o (-1, -1)."""
    vals = [_normaliza_nombre(v) for v in fila.tolist()]
    i_sku = next((i for i, v in enumerate(vals) if v in _COLS_SKU), -1)
    i_pre = next((i for i, v in enumerate(vals) if "PRECIO" in v), -1)
    return i_sku, i_pre


def _leer_hoja(path: Path, hoja: str) -> pd.DataFrame:
    """Una hoja -> filas canonicas (CODIGO_SKU, PRECIO_BASE, DESC1..n)."""
    crudo = pd.read_excel(path, sheet_name=hoja, header=None, engine="openpyxl")
    if crudo.empty:
        return pd.DataFrame()

    # 1. Buscar fila de encabezados (BLISTERIA la trae en la fila 5).
    idx_header = -1
    for i, (_, fila) in enumerate(crudo.iterrows()):
        i_sku, i_pre = _fila_header(fila)
        if i_sku >= 0 and i_pre >= 0:
            idx_header = i
            break

    if idx_header >= 0:
        headers = [str(v or "").strip() for v in crudo.iloc[idx_header].tolist()]
        datos = crudo.iloc[idx_header + 1 :].reset_index(drop=True)
        datos.columns = [h if h else f"__{j}" for j, h in enumerate(headers)]
        col_sku = next((c for c in datos.columns if _normaliza_nombre(c) in _COLS_SKU), None)
        col_pre = next((c for c in datos.columns if "PRECIO" in _normaliza_nombre(c)), None)
        if col_sku is None or col_pre is None:
            return pd.DataFrame()
        cols_desc = [
            c
            for c in datos.columns
            if "DESCUENTO" in _normaliza_nombre(c) or "DESC." in _normaliza_nombre(c)
        ]
        out = pd.DataFrame({"CODIGO_SKU": datos[col_sku], "PRECIO_BASE": datos[col_pre]})
        for k, c in enumerate(cols_desc, 1):
            out[f"DESC{k}"] = datos[c]
    else:
        # 2. Sin encabezados (VINIFAN): por posicion.
        # col0 = SKU, col1 = precio, col2+ = cascada de descuentos.
        datos = crudo.reset_index(drop=True)
        if datos.shape[1] < 2:
            return pd.DataFrame()
        out = pd.DataFrame({"CODIGO_SKU": datos.iloc[:, 0], "PRECIO_BASE": datos.iloc[:, 1]})
        k = 1
        for j in range(2, datos.shape[1]):
            serie = pd.to_numeric(datos.iloc[:, j], errors="coerce")
            if serie.notna().any():
                out[f"DESC{k}"] = datos.iloc[:, j]
                k += 1

    # 3. La regla: precio numerico y codigo no vacio. Saca vacias, categorias
    # y headers que hayan pasado, sin importar la hoja.
    precio = pd.to_numeric(out["PRECIO_BASE"], errors="coerce")
    sku = out["CODIGO_SKU"].astype(str).str.strip()
    sku = sku.replace({"nan": "", "None": "", "none": ""})
    utiles = precio.notna() & (sku != "")
    return out.loc[utiles].reset_index(drop=True)


def leer_contingencia(path: str | Path) -> pd.DataFrame:
    """Lee TODAS las hojas y las concatena en un solo DataFrame canonico."""
    p = Path(path)
    xl = pd.ExcelFile(p, engine="openpyxl")
    partes = []
    for hoja in xl.sheet_names:
        try:
            df = _leer_hoja(p, hoja)
        except Exception:
            log.warning("contingencia: hoja %s ilegible, se salta", hoja)
            continue
        if not df.empty:
            df["__HOJA"] = hoja
            partes.append(df)
    if not partes:
        return pd.DataFrame()
    out = pd.concat(partes, ignore_index=True)
    return out.drop(columns=["__HOJA"], errors="ignore")


def leer_lista_precios(path: str | Path) -> tuple[pd.DataFrame, str]:
    """Camino normal primero, contingencia solo si hace falta.

    Returns (df, origen) donde origen es "plantilla" o "contingencia", para
    que la UI pueda decir cual se uso. Si ninguno sirve, devuelve el de la
    plantilla para que el error de abajo sea el de siempre.
    """
    df = read_erp_file(path)
    if _parece_lista(df):
        return df, "plantilla"
    log.warning("la lista no parece plantilla; probando lectura de contingencia: %s", path)
    df2 = leer_contingencia(path)
    if df2 is not None and not df2.empty:
        log.warning("contingencia: %s queda con %d SKU utiles", path, len(df2))
        return df2, "contingencia"
    return df, "plantilla"
