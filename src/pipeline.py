import logging
import pandas as pd
from src.core.catalog_loader import CatalogLoader
from src.domain import ExpedienteComercial, BusinessAlert
from src.strategies.price_difference import PriceDifferenceStrategy
from src.strategies.cantidad_determinada import CantidadDeterminadaStrategy
from src.strategies.promotion_bonus import PromotionBonusStrategy
from src.strategies.volume_rebate import VolumeRebateStrategy
from src.strategies.cancel_invoice import CancelInvoiceStrategy
from src.strategies.feria_preventa import FeriaPreventaStrategy
from src.strategies.descuento_factura import DescuentoFacturaStrategy
from src.strategies.devolucion_fisica import DevolucionFisicaStrategy
from src.validation.engine import ValidationEngine, BusinessValidator

logger = logging.getLogger(__name__)


# Alias de compatibilidad: el cargador canónico vive en src.core.catalog_loader.
CatalogoCargador = CatalogLoader


class Pipeline:
    """Pipeline invariable: Carga → Normalización → Validación → Cálculo → Reglas → Render."""

    STRATEGIES = {
        "PriceDifference": PriceDifferenceStrategy,
        "CantidadDeterminada": CantidadDeterminadaStrategy,
        "PromotionBonus": PromotionBonusStrategy,
        "VolumeRebate": VolumeRebateStrategy,
        "CancelInvoice": CancelInvoiceStrategy,
        "FeriaPreventa": FeriaPreventaStrategy,
        "DescuentoFactura": DescuentoFacturaStrategy,
        "DevolucionFisica": DevolucionFisicaStrategy,
    }

    def __init__(self):
        self.catalogo = CatalogoCargador()
        self.validation_engine = ValidationEngine()
        self.business_validator = BusinessValidator()

    def ejecutar(self, expediente: ExpedienteComercial) -> ExpedienteComercial:
        ctx = expediente.contexto
        ctx.log_evento(f"Inicio pipeline: {expediente.estrategia}")

        # Paso 2: Normalización
        try:
            from src.validation.normalization import NormalizationEngine

            schema = self.catalogo.obtener_schema(
                self._resolver_key_por_estrategia(expediente.estrategia, expediente.variante)
            )
            norm = NormalizationEngine(schema)
            expediente.datos = norm.normalizar_historial(expediente.datos)
            for i, cond in enumerate(expediente.condiciones):
                if isinstance(cond, pd.DataFrame):
                    expediente.condiciones[i] = norm.normalizar_condicion(cond)
            ctx.log_evento("Normalización completada")
        except Exception as e:
            expediente.agregar_alerta(
                BusinessAlert(
                    tipo="error",
                    severidad="alta",
                    mensaje=f"Error en normalización: {e}",
                    motor=expediente.estrategia,
                )
            )
            ctx.log_evento(f"Error normalización: {e}")
            return expediente

        # Paso 3: Validación técnica
        errores_tecnicos = self.validation_engine.validar(expediente.datos)
        for err in errores_tecnicos:
            alerta_tipo = err.get("tipo", "error")
            expediente.agregar_alerta(
                BusinessAlert(
                    tipo=alerta_tipo,
                    severidad="alta",
                    sku=err.get("sku", ""),
                    mensaje=err["mensaje"],
                    motor=expediente.estrategia,
                )
            )
        ctx.log_evento(f"Validación técnica: {len(errores_tecnicos)} errores")

        if expediente.tiene_alertas_bloqueantes():
            ctx.log_evento("Pipeline detenido por alertas bloqueantes")
            return expediente

        # Paso 4: Validación comercial
        advertencias = self.business_validator.validar(expediente)
        for adv in advertencias:
            expediente.agregar_alerta(adv)
        ctx.log_evento(f"Validación comercial: {len(advertencias)} advertencias")

        # Paso 5: Cálculo
        estrategia_cls = self.STRATEGIES.get(expediente.estrategia)
        if not estrategia_cls:
            expediente.agregar_alerta(
                BusinessAlert(
                    tipo="error",
                    severidad="alta",
                    mensaje=f"Estrategia no encontrada: {expediente.estrategia}",
                    motor=expediente.estrategia,
                )
            )
            return expediente

        try:
            estrategia = estrategia_cls()
            expediente.resultado = estrategia.process(expediente)
            ctx.log_evento(f"Cálculo completado: {expediente.resultado.resumen}")
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            logger.error("Error en cálculo:\n%s", tb)
            expediente.agregar_alerta(
                BusinessAlert(
                    tipo="error",
                    severidad="alta",
                    mensaje=f"Error en cálculo: {e}",
                    motor=expediente.estrategia,
                )
            )
            ctx.log_evento(f"Error cálculo: {e}")

        # Paso 6: Reglas (post-procesamiento)
        self._aplicar_reglas_post(expediente)

        ctx.log_evento("Pipeline completado")
        return expediente

    def _resolver_key_por_estrategia(self, estrategia: str, variante: str = "") -> str:
        if estrategia == "PriceDifference" and variante == "discount_period":
            return "descuento_precio"
        mapa = {
            "PriceDifference": "diferencia_precio",
            "CantidadDeterminada": "diferencia_stock",
            "PromotionBonus": "bonificacion_promocion",
            "VolumeRebate": "rebate_volumen",
            "CancelInvoice": "anular_factura",
            "FeriaPreventa": "feria_preventa",
            "DescuentoFactura": "descuento_factura",
        }
        return mapa.get(estrategia, "")

    def _aplicar_reglas_post(self, expediente: ExpedienteComercial):
        if not expediente.resultado or expediente.resultado.get_excel().empty:
            return
        df = expediente.resultado.get_excel()
        # Buscar la columna de Total NC con el esquema nuevo o retro-compat legacy
        total = 0.0
        if "Subtotal NC (S/)" in df.columns:
            total = df["Subtotal NC (S/)"].sum()
        elif "SUBTOTAL (SIN IGV)" in df.columns:
            total = df["SUBTOTAL (SIN IGV)"].sum()
        elif "MONTO_NC" in df.columns:
            total = df["MONTO_NC"].sum()
        elif "MONTO NC" in df.columns:
            total = df["MONTO NC"].sum()
        expediente.resultado.resumen["total_nc"] = total
        expediente.resultado.resumen["skus_afectados"] = (
            df["SKU"].nunique() if "SKU" in df.columns else 0
        )
