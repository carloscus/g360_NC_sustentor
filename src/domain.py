from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd

CATALOGO_ALERTAS = {
    "AL01": {
        "desc": "Diferencia positiva - cliente sobrepagó",
        "tipo": "error",
        "severidad": "media",
    },
    "AL02": {"desc": "Diferencia negativa o precio coincide", "tipo": "info", "severidad": "baja"},
    "AL03": {
        "desc": "SKU sin precio en lista / Precios variables",
        "tipo": "info",
        "severidad": "baja",
    },
    "AL04": {"desc": "Descuento inválido (>100%)", "tipo": "warning", "severidad": "media"},
    "AL05": {"desc": "Meta de compra no alcanzada", "tipo": "warning", "severidad": "baja"},
    "AL06": {"desc": "SKU no encontrado en historial", "tipo": "error", "severidad": "alta"},
    "AL08": {"desc": "SKU con descuentos múltiples en lotes", "tipo": "info", "severidad": "baja"},
    "AL09": {"desc": "Stock insuficiente para sustentar", "tipo": "warning", "severidad": "media"},
    "AL10": {"desc": "Cantidad o descuento en cero", "tipo": "info", "severidad": "baja"},
    "AL11": {
        "desc": "Diferencia dentro de tolerancia de redondeo",
        "tipo": "info",
        "severidad": "baja",
    },
    "AL12": {
        "desc": "Factura ya cuenta con NC/NDB previa por el SKU",
        "tipo": "warning",
        "severidad": "media",
    },
    "AL13": {
        "desc": "La cantidad acumulada de NC/NDB supera la cantidad facturada",
        "tipo": "warning",
        "severidad": "media",
    },
}


def generar_texto_alerta(codigo: str = "OK", **kwargs) -> str:
    """Genera texto detallado de alerta con código + descripción contextual.

    Kwargs disponibles según el tipo de alerta:
      - sku, diferencia, precio_facturado, precio_neto, cantidad
      - documentos: dict[doc_id, {"cantidad": int, "monto": float}]
      - total, meta, porcentaje, asignado, solicitado, detalle
    """
    if codigo == "OK":
        return "OK"

    if codigo == "AL01":
        dif = kwargs.get("diferencia", 0)
        cant = kwargs.get("cantidad", 0)
        base = f"AL01 - Diferencia positiva S/ {dif:.5f}"
        if cantidad := kwargs.get("cantidad"):
            total = round(dif * cantidad, 2)
            base += f" (total S/ {total:,.2f})"
        p_fact = kwargs.get("precio_facturado")
        p_neto = kwargs.get("precio_neto")
        if p_fact is not None and p_neto is not None:
            base += f": factura S/ {p_fact:.5f} vs lista S/ {p_neto:.5f}"
        sku = kwargs.get("sku")
        if sku:
            base += f" (SKU {sku})"
        return base

    if codigo == "AL02":
        if kwargs.get("coincide"):
            base = "AL02 - Precios coinciden, sin NC"
        else:
            base = "AL02 - Diferencia negativa, sin NC"
        sku = kwargs.get("sku")
        if sku:
            base += f" (SKU {sku})"
        return base

    if codigo == "AL03":
        docs = kwargs.get("documentos")
        if docs:
            partes = []
            for doc_id, info in docs.items():
                cant = info["cantidad"]
                precio = info.get("monto", 0) / cant if cant else 0
                partes.append(f"{doc_id} ({int(cant)} unid.) S/ {precio:.5f}")
            return "AL03 - Precios variables: " + ", ".join(partes)
        sku = kwargs.get("sku", "")
        return f"AL03 - SKU {sku} sin precio en lista de precios"

    if codigo == "AL04":
        return f"AL04 - {kwargs.get('detalle', 'Descuento inválido (>100%)')}"

    if codigo == "AL05":
        total = kwargs.get("total", 0)
        meta = kwargs.get("meta", 0)
        pct = kwargs.get("porcentaje", (total / meta * 100) if meta > 0 else 0)
        return f"AL05 - Compra S/ {total:,.2f} vs meta S/ {meta:,.2f} ({pct:.0f}% alcanzado)"

    if codigo == "AL06":
        sku = kwargs.get("sku", "")
        return f"AL06 - SKU {sku} no encontrado en historial"

    if codigo == "AL08":
        sku = kwargs.get("sku", "")
        cant_desc = kwargs.get("cant_desc", 0)
        return f"AL08 - SKU {sku} con {cant_desc} descuentos distintos en lotes"

    if codigo == "AL09":
        asignado = kwargs.get("asignado", 0)
        solicitado = kwargs.get("solicitado", 0)
        docs = kwargs.get("documentos")
        base = f"AL09 - Solo {int(asignado)} unid. de {int(solicitado)} sustentadas"
        if docs:
            partes = [f"{doc_id} ({int(info['cantidad'])})" for doc_id, info in docs.items()]
            base += ": " + ", ".join(partes)
        return base

    if codigo == "AL10":
        return f"AL10 - {kwargs.get('detalle', 'Cantidad o descuento en cero')}"

    if codigo == "AL11":
        dif_u = kwargs.get("diferencia_unitaria", 0)
        dif_total = kwargs.get("diferencia_total", 0)
        cant = kwargs.get("cantidad", 0)
        base = f"AL11 - Redondeo acumulable (unit. S/ {dif_u:.5f}, total S/ {dif_total:.2f})"
        if cant:
            base += f" × {int(cant)} unid."
        sku = kwargs.get("sku")
        if sku:
            base += f" (SKU {sku})"
        return base

    if codigo == "AL12":
        factura = kwargs.get("factura", "")
        docs = kwargs.get("docs", []) or []
        partes = []
        for d in docs:
            doc = str(d.get("doc", "")).strip()
            if not doc:
                continue
            tipo_nota = str(d.get("tipo", "NC")).strip().upper() or "NC"
            try:
                qty = float(d.get("qty", 0) or 0)
            except (TypeError, ValueError):
                qty = 0.0
            qtxt = f"{qty:,.0f} unid"
            if d.get("fae"):
                qtxt += " (FAE)"
            partes.append(f"{tipo_nota} {doc} x {qtxt}")
        base = f"AL12 - Factura {factura} cuenta con " + ", ".join(partes)
        sku = kwargs.get("sku")
        if sku:
            base += f" (SKU {sku})"
        return base

    if codigo == "AL13":
        return (
            f"AL13 - NC/NDB excede cantidad facturada para factura "
            f"{kwargs.get('factura', '')}, SKU {kwargs.get('sku', '')}: "
            f"{kwargs.get('cantidad_nota', 0):,.2f} vs "
            f"{kwargs.get('cantidad_factura', 0):,.2f} unidades"
        )

    return codigo


@dataclass
class BusinessAlert:
    codigo: str = ""
    tipo: str = "info"  # "error", "warning", "info", "opportunity"
    severidad: str = "baja"  # "alta", "media", "baja"
    sku: str = ""
    mensaje: str = ""
    impacto: float = 0.0
    motor: str = ""  # estrategia que generó la alerta

    def __post_init__(self):
        pass

    @property
    def color_semaforo(self) -> str:
        if self.tipo in ("error",):
            return "rojo"
        if self.tipo in ("warning",):
            return "ambar"
        if self.tipo in ("info", "opportunity"):
            return "azul"
        return "none"


@dataclass
class ReconocimientoPorCondicion:
    condicion_id: str
    fuente: str
    estrategia: str
    precio_base: float = 0.0
    precio_neto: float = 0.0
    descuentos: list = field(default_factory=list)
    cantidad_aplicada: float = 0.0
    monto_reconocido: float = 0.0
    skus: list = field(default_factory=list)
    documentos: list = field(default_factory=list)


@dataclass
class RecognitionResult:
    """Resultado de una estrategia.

    Atributos:
        dataframe: Vista previa (formato ligero, ~7 columnas).
            Compatible hacia atras: las estrategias que solo emiten este campo
            siguen funcionando sin cambios.

        dataframe_excel: Reporte Excel completo (12+ columnas, con calculos
            detallados). Opcional. Si no se setea, el renderer usa ``dataframe``.

        por_condicion: lista de dicts (reservado, no usado actualmente).

        resumen: dict con KPI (totales, count, etc).

        metricas: dict con metricas avanzadas (reservado).

        alertas: lista de ``BusinessAlert``.

        trazabilidad: lista de strings de auditoria.
    """

    dataframe: pd.DataFrame = field(default_factory=pd.DataFrame)
    dataframe_excel: pd.DataFrame = field(default_factory=pd.DataFrame)
    por_condicion: list = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    metricas: dict = field(default_factory=dict)
    alertas: list = field(default_factory=list)
    trazabilidad: list = field(default_factory=list)

    def get_excel(self) -> pd.DataFrame:
        """Devuelve el dataframe para exportar a Excel.

        Prioriza ``dataframe_excel`` si esta seteado, sino cae a ``dataframe``.
        """
        if self.dataframe_excel is not None and not self.dataframe_excel.empty:
            return self.dataframe_excel
        return self.dataframe

    def get_preview(self) -> pd.DataFrame:
        """Devuelve el dataframe para vista previa Flet."""
        return self.dataframe


@dataclass
class PipelineContext:
    usuario: str = ""
    fecha_ejecucion: datetime = field(default_factory=datetime.now)
    version_motor: str = "3.0.0"
    version_reglas: str = ""
    config: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    antecedentes: str = ""
    observaciones: str = ""

    def log_evento(self, evento: str):
        self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {evento}")


@dataclass
class ExpedienteComercial:
    id: str = ""
    exp_id: str = ""
    nombre: str = ""
    familia: str = ""
    estrategia: str = ""
    variante: str = ""
    fecha_creacion: datetime = field(default_factory=datetime.now)

    datos: pd.DataFrame = field(default_factory=pd.DataFrame)
    condiciones: list = field(default_factory=list)
    evidencias: list = field(default_factory=list)
    resultado: Optional[RecognitionResult] = None
    alertas: list = field(default_factory=list)

    contexto: PipelineContext = field(default_factory=PipelineContext)
    output_config: dict = field(
        default_factory=lambda: {
            "excel_calculo": True,
            "excel_erp": False,
            "informe_docx": True,
        }
    )

    def agregar_alerta(self, alerta: BusinessAlert):
        self.alertas.append(alerta)

    def tiene_alertas_bloqueantes(self) -> bool:
        return any(a.tipo == "error" for a in self.alertas)

    def resumen_alertas(self) -> dict:
        return {
            "error": sum(1 for a in self.alertas if a.tipo == "error"),
            "warning": sum(1 for a in self.alertas if a.tipo == "warning"),
            "info": sum(1 for a in self.alertas if a.tipo == "info"),
            "opportunity": sum(1 for a in self.alertas if a.tipo == "opportunity"),
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "familia": self.familia,
            "estrategia": self.estrategia,
            "fecha": self.fecha_creacion.isoformat(),
            "resumen_alertas": self.resumen_alertas(),
            "resultado": {
                "total_nc": self.resultado.resumen.get("total_nc", 0) if self.resultado else 0,
                "skus_afectados": self.resultado.resumen.get("skus_afectados", 0)
                if self.resultado
                else 0,
            }
            if self.resultado
            else {},
        }
