"""
Endpoint local para alimentar g360-erp-nc-sustentor desde Supabase (g360-ventas-db).

Uso standalone:
    uvicorn src.api.supabase_endpoint:app --reload --port 8001

Uso desde el sustentor (sin servidor):
    from src.core.supabase_client import SupabaseVentasClient
    df = SupabaseVentasClient().fetch_historial(id_cliente="00068414")

Endpoints expuestos (si se corre como API):
    GET  /retornos/validar-sku?id_articulo=02211
    GET  /retornos/saldo?id_cliente=00068414&id_articulo=02211
    GET  /retornos/facturas?id_cliente=..&id_articulo=..&limit=50
    POST /retornos/calcular  {id_cliente, id_articulo, cantidad_solicitada}
"""
from __future__ import annotations

from typing import Optional

try:
    from fastapi import FastAPI, Query, HTTPException
    from pydantic import BaseModel

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    FastAPI = object  # type: ignore
    BaseModel = object  # type: ignore

from src.core.supabase_client import SupabaseVentasClient

app = FastAPI(title="g360-retornos API", version="1.0.0") if HAS_FASTAPI else None  # type: ignore

# ── Modelos ────────────────────────────────────────────────────────────

if HAS_FASTAPI:

    class CalcularRequest(BaseModel):
        id_cliente: str
        id_articulo: str
        cantidad_solicitada: float

    class CalcularResponse(BaseModel):
        cantidad_solicitada: float
        cantidad_asignada: float
        total_soles: float
        alertas: list
        breakdown: list

# ── Helpers ────────────────────────────────────────────────────────────

def _get_client() -> SupabaseVentasClient:
    return SupabaseVentasClient()


# ── Endpoints ──────────────────────────────────────────────────────────

if HAS_FASTAPI:

    @app.get("/retornos/validar-sku")
    def validar_sku(id_articulo: str = Query(..., description="SKU a validar")):
        cli = _get_client()
        try:
            df = cli.fetch_historial(id_articulo=id_articulo, limit=1)
            if df.empty:
                return {"existe": False}
            row = df.iloc[0]
            return {
                "existe": True,
                "nom_articulo": str(row.get("ARTICULO", "")),
                "id_linea": str(row.get("COD_LINEA", "")),
                "vendido_total": float(df["CANTIDAD"].sum()) if "CANTIDAD" in df.columns else 0,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/retornos/saldo")
    def saldo(id_cliente: str = Query(...), id_articulo: str = Query(...)):
        cli = _get_client()
        try:
            df = cli.fetch_historial(id_cliente=id_cliente, id_articulo=id_articulo, limit=50000)
            if df.empty:
                return {
                    "id_cliente": id_cliente,
                    "id_articulo": id_articulo,
                    "total_vendido": 0,
                    "total_devuelto": 0,
                    "saldo_disponible": 0,
                }
            # Separar por tipo_operacion si existe, sino asumir todo vendido
            if "TIPO_OPERACION" in df.columns:
                vendido = df[df["TIPO_OPERACION"] == "venta"]["CANTIDAD"].sum()
                devuelto = df[df["TIPO_OPERACION"] == "devolucion"]["CANTIDAD"].abs().sum()
            else:
                vendido = df["CANTIDAD"].sum()
                devuelto = 0
            return {
                "id_cliente": id_cliente,
                "id_articulo": id_articulo,
                "total_vendido": float(vendido),
                "total_devuelto": float(devuelto),
                "saldo_disponible": float(vendido - devuelto),
                "facturas_count": int(len(df)),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/retornos/facturas")
    def facturas(
        id_cliente: str = Query(...),
        id_articulo: str = Query(...),
        limit: int = Query(50, ge=1, le=500),
    ):
        cli = _get_client()
        try:
            df = cli.fetch_facturas_disponibles(id_cliente, id_articulo, limit=limit)
            return df.to_dict(orient="records") if not df.empty else []
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/retornos/calcular", response_model=CalcularResponse)
    def calcular(req: CalcularRequest):
        cli = _get_client()
        try:
            # Usar la vista Supabase directamente si está disponible
            # Fallback: lógica LIFO simple en Python
            df = cli.fetch_historial(id_cliente=req.id_cliente, id_articulo=req.id_articulo, limit=50000)
            if df.empty:
                raise HTTPException(status_code=400, detail="Sin historial para cliente+SKU")

            # Agrupar por folio y calcular saldo (simplificado: sin NC totales aún)
            # Para la versión completa, consultar vw_facturas_disponibles
            try:
                facturas = cli.fetch_facturas_disponibles(req.id_cliente, req.id_articulo, limit=200)
                if not facturas.empty and "saldo_disponible" in facturas.columns:
                    # Usar la vista con saldo ya calculado
                    facturas = facturas.sort_values("fecha_orig", ascending=False)
                    remaining = req.cantidad_solicitada
                    breakdown = []
                    total = 0.0
                    for _, row in facturas.iterrows():
                        if remaining <= 0:
                            break
                        saldo = float(row.get("saldo_disponible", 0))
                        if saldo <= 0:
                            continue
                        tomar = min(remaining, saldo)
                        precio = float(row.get("precio_para_devolucion", row.get("precio_unitario", 0)))
                        subtotal = tomar * precio
                        total += subtotal
                        breakdown.append(
                            {
                                "folio_unico": str(row.get("folio_unico", "")),
                                "fecha_orig": str(row.get("fecha_orig", "")),
                                "cantidad_asignada": tomar,
                                "saldo_original": float(row.get("cantidad_vendida", 0)),
                                "saldo_despues": saldo - tomar,
                                "precio_unitario": float(row.get("precio_unitario", 0)),
                                "precio_para_devolucion": precio,
                                "subtotal": round(subtotal, 2),
                                "estado_periodo": str(row.get("estado_periodo", "DENTRO_PERIOD")),
                            }
                        )
                        remaining -= tomar

                    if remaining > 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Saldo insuficiente. Solicitado {req.cantidad_solicitada}, disponible {req.cantidad_solicitada - remaining}",
                        )

                    return {
                        "cantidad_solicitada": req.cantidad_solicitada,
                        "cantidad_asignada": req.cantidad_solicitada,
                        "total_soles": round(total, 2),
                        "alertas": [],
                        "breakdown": breakdown,
                    }
            except HTTPException:
                raise
            except Exception:
                pass

            # Fallback sin vista: cálculo simple por fecha
            df_sorted = df.sort_values("FECHA", ascending=False) if "FECHA" in df.columns else df
            remaining = req.cantidad_solicitada
            breakdown = []
            total = 0.0
            for _, row in df_sorted.iterrows():
                if remaining <= 0:
                    break
                cant = float(row.get("CANTIDAD", 0))
                if cant <= 0:
                    continue
                tomar = min(remaining, cant)
                precio = float(row.get("PRECIO_UNITARIO", 0))
                subtotal = tomar * precio
                total += subtotal
                breakdown.append(
                    {
                        "folio_unico": str(row.get("FOLIO_UNICO", "")),
                        "fecha_orig": str(row.get("FECHA", "")),
                        "cantidad_asignada": tomar,
                        "precio_para_devolucion": precio,
                        "subtotal": round(subtotal, 2),
                    }
                )
                remaining -= tomar

            if remaining > 0:
                raise HTTPException(status_code=400, detail=f"Saldo insuficiente. Faltan {remaining}u")

            return {
                "cantidad_solicitada": req.cantidad_solicitada,
                "cantidad_asignada": req.cantidad_solicitada,
                "total_soles": round(total, 2),
                "alertas": [],
                "breakdown": breakdown,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    def health():
        cli = _get_client()
        ok, msg = cli.test_connection()
        return {"ok": ok, "message": msg, "url": cli.url}
