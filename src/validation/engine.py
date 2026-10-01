import pandas as pd
from src.domain import BusinessAlert, ExpedienteComercial


class ValidationEngine:
    """Validación técnica: columnas, tipos, fechas, SKU, documentos."""

    COLUMNAS_CRITICAS = [
        "CODIGO",
        "ARTICULO",
        "FECHA",
        "CANTIDAD",
        "SOLES",
        "TIPO_DOC",
        "SERIE",
        "NUMERO",
    ]

    def validar(self, df: pd.DataFrame) -> list:
        resultados = []
        if df.empty:
            resultados.append({"sku": "", "mensaje": "DataFrame vacío", "tipo": "error"})
            return resultados

        # Validar columnas críticas (bloqueante)
        for col in self.COLUMNAS_CRITICAS:
            if col not in df.columns:
                columnas_reales = [c for c in df.columns if c.strip()]
                mensaje = f"Columna crítica faltante: {col}"
                if columnas_reales:
                    mensaje += f" | Columnas detectadas: {', '.join(columnas_reales[:15])}"
                resultados.append({"sku": "", "mensaje": mensaje, "tipo": "error"})

        # Validar valores nulos en columnas críticas (bloqueante)
        for col in ["CODIGO", "CANTIDAD"]:
            if col in df.columns:
                nulos = df[col].isna().sum()
                if nulos > 0:
                    resultados.append(
                        {
                            "sku": "",
                            "mensaje": f"{int(nulos)} filas con '{col}' nulo",
                            "tipo": "error",
                        }
                    )

        # Validar fechas (bloqueante)
        if "FECHA" in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(df["FECHA"]):
                try:
                    pd.to_datetime(df["FECHA"], errors="raise")
                except Exception:
                    resultados.append(
                        {"sku": "", "mensaje": "FECHA no es una fecha válida", "tipo": "error"}
                    )

        # Validar montos negativos (no bloqueante)
        if "SOLES" in df.columns:
            negativos = (df["SOLES"] < 0).sum()
            if negativos > 0:
                resultados.append(
                    {
                        "sku": "",
                        "mensaje": f"{int(negativos)} filas con SOLES negativos (se ajustarán)",
                        "tipo": "warning",
                    }
                )

        # Validar documentos vacíos (no bloqueante)
        for col in ["TIPO_DOC", "SERIE", "NUMERO"]:
            if col in df.columns:
                vacios = (df[col].astype(str).str.strip() == "").sum()
                if vacios > 0:
                    resultados.append(
                        {
                            "sku": "",
                            "mensaje": f"{int(vacios)} filas con '{col}' vacío (se omitirán)",
                            "tipo": "warning",
                        }
                    )

        return resultados


class BusinessValidator:
    """Validación comercial: reglas de negocio sobre el expediente."""

    def validar(self, expediente: ExpedienteComercial) -> list:
        alertas = []
        resultado = expediente.resultado
        if not resultado or resultado.dataframe.empty:
            return alertas

        df = resultado.dataframe

        # SKU sin precio en lista
        col_precio = (
            "PRECIO_NETO"
            if "PRECIO_NETO" in df.columns
            else ("PRECIO_LISTA" if "PRECIO_LISTA" in df.columns else None)
        )
        if col_precio:
            sin_precio = df[df[col_precio].isna() | (df[col_precio] == 0)]
            for _, row in sin_precio.iterrows():
                alertas.append(
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        sku=str(row.get("SKU", "")),
                        mensaje="SKU sin precio en lista de precios",
                        motor=expediente.estrategia,
                    )
                )

        # Diferencia positiva (ya era menor)
        if "DIFERENCIA" in df.columns:
            dif_pos = df[df["DIFERENCIA"] > 0]
            for _, row in dif_pos.iterrows():
                alertas.append(
                    BusinessAlert(
                        tipo="info",
                        severidad="baja",
                        sku=str(row.get("SKU", "")),
                        mensaje=f"Precio ya es menor (S/ {row['DIFERENCIA']:.5f}), no genera NC",
                        impacto=float(row.get("MONTO_NC", 0)),
                        motor=expediente.estrategia,
                    )
                )

        # Cadena descuentos no coincide
        if "_NETO_OK" in df.columns:
            no_ok = df[df["_NETO_OK"] == False]
            for _, row in no_ok.iterrows():
                alertas.append(
                    BusinessAlert(
                        tipo="warning",
                        severidad="media",
                        sku=str(row.get("SKU", "")),
                        mensaje=f"Descuentos no coinciden: calculado S/ {row.get('PRECIO_CALCULADO', 0):.5f} vs declarado S/ {row.get('PRECIO_NETO', 0):.5f}. Se usó calculado.",
                        impacto=float(abs(row.get("_DIF_NETO", 0))),
                        motor=expediente.estrategia,
                    )
                )

        return alertas
