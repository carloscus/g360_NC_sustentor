from typing import List, Dict
from dataclasses import dataclass, field


@dataclass
class ProcessedItem:
    """Item procesado por una estrategia de reconocimiento de NC.

    Representa una línea de producto tras aplicar la lógica de la estrategia:
    guarda el monto solicitado vs encontrado, el precio unitario, el descuento
    aplicado y los documentos (facturas/NC/ND) que soportan la operación.
    """

    CODIGO: str
    ARTICULO: str
    CANTIDAD_SOLICITADA: int
    CANTIDAD_REAL_ENCONTRADA: int
    PRECIO_UNITARIO: float
    MONTO_DESCUENTO_UNITARIO: float
    PRECIO_NETO_FINAL: float
    SUBTOTAL_DESCUENTO: float
    PORCENTAJE_APLICADO: float
    DOCUMENTOS: List[str]
    STATUS: str
    NUMERO: str = ""
    SERIE: str = ""
    COD_LINEA: str = ""
    LINEA: str = ""
    FACTURA_REF: str = ""
    DOCUMENTOS_CANTIDAD: Dict[str, float] = field(default_factory=dict)
    DOCUMENTOS_MONTOS: Dict[str, float] = field(default_factory=dict)
    VALOR_SOPORTE_TOTAL: float = 0.0
