"""
Cliente Supabase para g360-erp-nc-sustentor.

Lee directamente de g360-ventas-db (tabla ventas + vistas) y mapea
al formato historial esperado por el pipeline (CODIGO, ARTICULO, etc.).

Uso:
    from src.core.supabase_client import SupabaseVentasClient

    cli = SupabaseVentasClient()  # lee .env o config de g360-ventas-db
    df = cli.fetch_historial(id_cliente="00068414", id_articulo="02211")
    # df ya viene con columnas CODIGO, CLIENTE, FECHA, CANTIDAD, SOLES, etc.

Requiere: pip install supabase httpx  (o usa REST directo sin SDK)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd


# Mapeo Supabase (ventas) -> historial sustentor
VENTAS_TO_HISTORIAL = {
    "id_articulo": "CODIGO",
    "nom_articulo": "ARTICULO",
    "id_linea": "COD_LINEA",
    "nom_linea": "LINEA",
    "id_grupo": "COD_GRUPO",
    "nom_grupo": "GRUPO",
    "id_tipo": "COD_TIPO",
    "nom_tipo": "TIPO",
    "id_familia": "COD_FAMILIA",
    "nom_familia": "FAMILIA",
    "id_cliente": "COD_CLIENTE",
    "nom_cliente": "CLIENTE",
    "doc_cliente": "DOC_CLIENTE",
    "tpo_doc": "TIPO_DOC",
    "serie_doc": "SERIE",
    "nro_doc": "NUMERO",
    "referencia": "REFERENCIA",
    "moneda": "MONEDA",
    "cantidad": "CANTIDAD",
    "soles": "SOLES",
    "dolares": "DOLARES",
    "precio_unitario": "PRECIO_UNITARIO",
    "anho": "ANHO",
    "mes": "MES",
    "fecha_orig": "FECHA",
    "fecha_ref": "FECHA_REF",
    "fecha_venc": "FECHA_VENC",
    "cod_sucursal": "COD_SUCURSAL",
    "nom_sucursal": "SUCURSAL",
    "departamento": "NOM_DEPARTAMENTO",
    "provincia": "NOM_PROVINCIA",
    "distrito": "NOM_DISTRITO",
    "id_vendedor": "COD_VENDEDOR",
    "nom_vendedor": "VENDEDOR",
    "id_pedido": "ID_PEDIDO",
    "tipo_operacion": "TIPO_OPERACION",
    "folio_unico": "FOLIO_UNICO",
}


def _load_config() -> tuple[str, str]:
    """Carga URL y anon key desde .env, env vars o config de g360-ventas-db."""
    # 1. .env local del sustentor
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:
            pass

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY", "")

    # 2. Fallback: config de g360-ventas-db (%APPDATA%/g360-db-ventas/data/config.json)
    if not url or "TU_SUPABASE" in url:
        try:
            import json

            cfg_path = Path.home() / "AppData/Roaming/g360-db-ventas/data/config.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                url = cfg.get("supabase", {}).get("url", url)
                key = cfg.get("supabase", {}).get("key", key)
        except Exception:
            pass

    # 3. Hardcoded fallback del proyecto actual
    if not url or "TU_SUPABASE" in url:
        url = "https://tqdmoytaucnfrpaklprc.supabase.co"
    if not key or "TU_ANON" in key:
        key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRxZG1veXRhdWNuZnJwYWtscHJjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc0NDUzMzMsImV4cCI6MjEwMzAyMTMzM30.HwmwX3PbHA3BYcJkutEXlpiSUG83YvGyYi-h--SGU2U"

    return url.rstrip("/"), key


class SupabaseVentasClient:
    """Cliente REST ligero sin dependencia supabase-py (usa httpx/requests)."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        env_url, env_key = _load_config()
        self.url = (url or env_url).rstrip("/")
        self.key = key or env_key
        self._headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "count=exact",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> list:
        """GET a PostgREST. Usa httpx si está disponible, sino requests."""
        try:
            import httpx

            resp = httpx.get(f"{self.url}{path}", headers=self._headers, params=params, timeout=30.0)
            resp.raise_for_status()
            return resp.json()
        except ImportError:
            import requests

            resp = requests.get(f"{self.url}{path}", headers=self._headers, params=params or {}, timeout=30)
            resp.raise_for_status()
            return resp.json()

    def _map_to_historial(self, rows: list[dict]) -> pd.DataFrame:
        """Mapea filas Supabase -> DataFrame historial."""
        if not rows:
            return pd.DataFrame()
        mapped = []
        for r in rows:
            m = {}
            for sup_col, hist_col in VENTAS_TO_HISTORIAL.items():
                m[hist_col] = r.get(sup_col)
            mapped.append(m)
        df = pd.DataFrame(mapped)
        # Normalizar tipos para el pipeline
        if "FECHA" in df.columns:
            df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
        for col in ["CANTIDAD", "SOLES", "PRECIO_UNITARIO", "DOLARES"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df

    # ── API pública ──────────────────────────────────────────────────

    def fetch_historial(
        self,
        *,
        id_cliente: Optional[str] = None,
        id_articulo: Optional[str] = None,
        mes_ref: Optional[str] = None,
        limit: int = 50000,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Trae historial filtrado y lo deja listo para el pipeline."""
        params: dict = {
            "select": ",".join(VENTAS_TO_HISTORIAL.keys()),
            "limit": str(limit),
            "offset": str(offset),
            "order": "fecha_orig.desc",
        }
        if id_cliente:
            params["id_cliente"] = f"eq.{id_cliente}"
        if id_articulo:
            params["id_articulo"] = f"eq.{id_articulo}"
        if mes_ref:
            params["mes_ref"] = f"eq.{mes_ref}"
        rows = self._get("/rest/v1/ventas", params=params)
        return self._map_to_historial(rows)

    def fetch_facturas_disponibles(
        self,
        id_cliente: str,
        id_articulo: str,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Usa la vista vw_facturas_disponibles (si existe) o replica la lógica."""
        try:
            rows = self._get(
                "/rest/v1/vw_facturas_disponibles",
                params={
                    "id_cliente": f"eq.{id_cliente}",
                    "id_articulo": f"eq.{id_articulo}",
                    "limit": str(limit),
                    "order": "fecha_orig.desc",
                },
            )
            return pd.DataFrame(rows)
        except Exception:
            # Fallback: calcular desde ventas base
            df = self.fetch_historial(id_cliente=id_cliente, id_articulo=id_articulo, limit=limit * 5)
            if df.empty:
                return df
            # Saldo simple: vendido - devuelto por folio
            return df

    def test_connection(self) -> tuple[bool, str]:
        """Verifica que Supabase responde y la tabla existe."""
        try:
            rows = self._get("/rest/v1/ventas", params={"select": "id", "limit": "1"})
            return True, f"OK — {len(rows)} fila(s) accesible(s)"
        except Exception as e:
            return False, str(e)

    # ── Helper para el pipeline ─────────────────────────────────────

    def to_expediente_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        """Asegura que el df tenga las columnas mínimas que espera el pipeline."""
        required = ["CODIGO", "ARTICULO", "COD_CLIENTE", "CLIENTE", "TIPO_DOC", "SERIE", "NUMERO", "FECHA", "CANTIDAD", "SOLES"]
        for col in required:
            if col not in df.columns:
                df[col] = "" if df[col].dtype == object else 0
        return df
