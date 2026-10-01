"""Catálogo canónico de los casos de Nota de Crédito.

Fuente única de verdad para el modelo de expedientes:

    Código  Caso                              Resultado
    DC      Diferencia de costo               NC
    DO      Descuento comercial               NC
    VRS     Venta / reconocimiento de stock   NC
    FPE     Feria / preventa / evento         NC
    PROM    Promoción / mecánica              NC
    ANF     Anulación de factura              NC 100%
    CMV     Concurso / meta de venta          NC
    DF      Devolución física                 NC

Reglas:
- La lista base son los 8 casos actuales. La *modalidad* (Individual /
  Consolidado) se maneja aparte y NO se convierte en casos nuevos (nunca 16).
- VRS fusionó a CDT: `diferencia_cantidad` es alias oculto de VRS (ver
  TIPOS_ALIAS) y ambos resuelven al motor CantidadDeterminada.
- Cada caso declara qué insumos necesita y a qué strategy (cálculo) existente
  delega. Esto reutiliza el código ya construido.

Extensibilidad:
- El catálogo se enriquece desde `catalog/processes.yaml` (bloque ``caso`` de
  cada proceso, ver src/core/catalog_loader.py). Un proceso nuevo declarado en
  el YAML con su bloque ``caso`` se suma automáticamente a `CATALOGO` y a
  `ORDEN_CASOS`, sin tocar este módulo.
- Si el YAML no existe o es inválido se degrada con elegancia a la lista base.
"""

from __future__ import annotations

from src.core.utils import cliente_visible

from dataclasses import dataclass, field
from typing import Optional

# ── Modalidad ──────────────────────────────────────────────────────────────
MODALIDAD_INDIVIDUAL = "individual"
MODALIDAD_CONSOLIDADO = "consolidado"

MODALIDAD_LABEL = {
    MODALIDAD_INDIVIDUAL: "Individual — por factura",
    MODALIDAD_CONSOLIDADO: "Consolidado — varias facturas",
}

# ── Insumos ────────────────────────────────────────────────────────────────
# Lista cerrada de insumos que pueden aparecer en la pantalla de creación.
INSUMOS = [
    "historico",
    "lista_precios",
    "sku",
    "cantidad",
    "porcentaje",
    "mecanica",
    "objetivo",
    "linea",
]


@dataclass(frozen=True)
class Caso:
    """Definición inmutable de un caso de NC."""

    codigo: str  # Código corto: DC, DO, VRS, FPE, PROM, ANF, CMV, DF
    label: str  # Nombre legible
    insumos: tuple[str, ...]  # Insumos requeridos (subconjunto de INSUMOS)
    resultado: str = "NC"  # Documento generado
    # Descripción llana (no técnica): qué es y cuándo aplica. Se muestra bajo
    # el selector de caso y responde "por qué es DO" sin jerga.
    descripcion: str = ""
    # Tipos "legacy" que este caso consolida. El primero es el principal y se
    # usa como delegado por defecto para no romper estrategias/namings previos.
    legacy_types: tuple[str, ...] = ()
    # Strategy por defecto (clave de ESTRATEGIA_POR_TIPO en reconocimiento_config)
    strategy: Optional[str] = None
    # Modalidades soportadas (ambas por defecto salvo que se restrinja)
    modalidades: tuple[str, ...] = (MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO)
    # Configuración por-defecto del histórico por documento:
    #   { "facturas": (incluir_reporte, considerar_calculo), "nc": (...), "ndb": (...) }
    historico_default: dict = field(default_factory=dict)

    @property
    def modalidad_obligatoria(self) -> bool:
        return len(self.modalidades) == 1


# ── Los 8 casos base (fallback seguro) ─────────────────────────────────────
# CATALOGO final = base + casos declarados en processes.yaml (YAML gana si
# redefine un codigo). Se calcula en _construir_catalogo().
_BASE_CATALOGO: dict[str, Caso] = {
    "DC": Caso(
        codigo="DC",
        label="Diferencia de costo",
        descripcion="La factura salió con un precio mayor al de la lista vigente; se devuelve la diferencia.",
        insumos=("historico", "lista_precios"),
        legacy_types=("diferencia_precio",),
        strategy="diferencia_precio",
        modalidades=(MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO),
        historico_default={"facturas": (True, True), "nc": (True, False), "ndb": (True, False)},
    ),
    "DO": Caso(
        codigo="DO",
        label="Descuento comercial",
        descripcion="Descuento por SKU acordado con el cliente que no se aplicó en la factura; se reconoce lo omitido.",
        insumos=("historico", "sku", "porcentaje"),
        legacy_types=("descuento_precio", "descuento_factura"),
        strategy="descuento_precio",
        modalidades=(MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO),
        historico_default={"facturas": (True, True), "nc": (True, False), "ndb": (True, False)},
    ),
    "VRS": Caso(
        codigo="VRS",
        label="Venta / reconocimiento de stock",
        descripcion="El cliente aún tiene stock comprado y el precio de lista bajó; se reconoce la diferencia sobre ese stock. Un solo archivo por SKU con cantidad y precio.",
        insumos=("historico", "lista_precios", "cantidad"),
        # `diferencia_cantidad` es alias oculto: resuelve al mismo caso/motor.
        legacy_types=("diferencia_stock", "diferencia_cantidad"),
        strategy="diferencia_stock",
        modalidades=(MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO),
        historico_default={"facturas": (True, True), "nc": (True, False), "ndb": (True, False)},
    ),
    "FPE": Caso(
        codigo="FPE",
        label="Feria / preventa / evento",
        descripcion="Compromiso de volumen o precio por feria o preventa, sustentado con facturas del período.",
        insumos=("historico", "sku", "cantidad", "porcentaje"),
        legacy_types=("feria_preventa",),
        strategy="feria_preventa",
        modalidades=(MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO),
        historico_default={"facturas": (True, True), "nc": (True, False), "ndb": (True, False)},
    ),
    "PROM": Caso(
        codigo="PROM",
        label="Promoción / mecánica",
        descripcion="Unidades entregadas de más por mecánica promocional (12+1, 48+1, etc.); se valorizan.",
        insumos=("historico", "sku", "mecanica"),
        legacy_types=("bonificacion_promocion",),
        strategy="bonificacion_promocion",
        modalidades=(MODALIDAD_CONSOLIDADO,),
        historico_default={"facturas": (True, True), "nc": (False, False), "ndb": (False, False)},
    ),
    "ANF": Caso(
        codigo="ANF",
        label="Anulación de factura",
        descripcion="La factura se anula por completo; la nota devuelve el 100 % de su valor.",
        insumos=("historico",),
        resultado="NC 100%",
        legacy_types=("anular_factura",),
        strategy="anular_factura",
        modalidades=(MODALIDAD_INDIVIDUAL,),
        historico_default={"facturas": (True, True), "nc": (False, False), "ndb": (False, False)},
    ),
    "CMV": Caso(
        codigo="CMV",
        label="Concurso / meta de venta",
        descripcion="El cliente cumplió la meta de compra del concurso; se paga el rebate acordado.",
        insumos=("historico", "linea", "sku", "objetivo"),
        legacy_types=("rebate_volumen",),
        strategy="rebate_volumen",
        modalidades=(MODALIDAD_CONSOLIDADO,),
        historico_default={"facturas": (True, True), "nc": (False, True), "ndb": (False, False)},
    ),
    "DF": Caso(
        codigo="DF",
        label="Devolución física",
        descripcion="Mercadería devuelta físicamente por el cliente; se reconoce su valor.",
        insumos=("historico", "sku", "cantidad"),
        legacy_types=("devolucion_fisica",),
        strategy="devolucion_fisica",
        modalidades=(MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO),
        historico_default={"facturas": (True, True), "nc": (True, False), "ndb": (True, False)},
    ),
}

# Orden canónico para dropdowns (base + adicionales del YAML al final).
_ORDEN_BASE = ["DC", "DO", "VRS", "FPE", "PROM", "ANF", "CMV", "DF"]

# Tipos legacy que ya no son casos propios: siguen resolviendo a un caso
# (LEGACY_A_CASO) pero no aparecen en el dropdown de casos.
TIPOS_ALIAS = {
    "diferencia_cantidad": "VRS",
}


def _casos_desde_yaml() -> dict[str, Caso]:
    """Lee los bloques ``caso`` de processes.yaml y los convierte en Casos.

    Devuelve {} (y degrada con elegancia) si el catálogo no existe o no es
    legible. Un ``codigo`` declarado en el YAML con el mismo nombre que uno de
    la base lo sustituye (YAML gana); los códigos nuevos se agregan.
    """
    from src.core.catalog_loader import CatalogLoader

    loader = CatalogLoader()
    casos: dict[str, Caso] = {}
    for c in loader.listar_casos():
        modalidades = c["modalidades"]
        if not modalidades:
            modalidades = (MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO)
        casos[c["codigo"]] = Caso(
            codigo=c["codigo"],
            label=c["label"],
            descripcion=c.get("descripcion", ""),
            insumos=c["insumos"],
            resultado=c["resultado"],
            legacy_types=c["legacy_types"],
            strategy=c["strategy"],
            modalidades=modalidades,
            historico_default=c["historico_default"],
        )
    return casos


def _construir_catalogo() -> dict[str, Caso]:
    """Fusiona la base con los casos del YAML, preservando el orden base."""
    fusion = dict(_BASE_CATALOGO)
    try:
        fusion.update(_casos_desde_yaml())
    except Exception as e:  # noqa: BLE001  (degradación elegante sin YAML)
        import logging

        logging.getLogger(__name__).warning("No se pudo leer el catálogo YAML: %s", e)
    # El update mayorista reemplaza el Caso completo: si el bloque `caso:` del
    # YAML no trae `descripcion`, se conserva la de base (texto llano). YAML
    # sigue ganando en todo lo que define explícitamente.
    import dataclasses

    for _cod, _base in _BASE_CATALOGO.items():
        _cur = fusion.get(_cod)
        if _cur is not None and not _cur.descripcion and _base.descripcion:
            fusion[_cod] = dataclasses.replace(_cur, descripcion=_base.descripcion)
    return fusion


CATALOGO: dict[str, Caso] = _construir_catalogo()

# Orden final: base en orden canónico + cualquier codigo nuevo del YAML.
ORDEN_CASOS: list[str] = list(_ORDEN_BASE)
for _cod_extra in CATALOGO:
    if _cod_extra not in ORDEN_CASOS:
        ORDEN_CASOS.append(_cod_extra)


# ── Naming EXP-[CASO]-[CLIENTE]-[SERIE]-[NUMERO]-[YYYYMMDD] ──────────────────
# Convención única de expedientes y sus documentos:
#
#   carpeta/     EXP-DC-50561-204-67721-20260910/
#   documento/   <exp_id>_Informe.docx
#                <exp_id>_Calculo.xlsx
#                <exp_id>_Historico.xlsx
#                <exp_id>_CalculoND.xlsx      (solo si se genera nota de débito)
#
# Sin correlativo y sin código de modalidad: el documento del ERP (cliente +
# serie + número) ya identifica el expediente, así que regenerar el mismo caso
# no crea duplicados.
def build_expediente_id(
    codigo: str,
    cliente_id: str,
    serie: str,
    nro: str,
    fecha: str = "",
) -> str:
    """Retorna 'EXP-DC-50561-204-67721-20260910'.

    Sin correlativo: el identificador del ERP (cliente + documento) garantiza
    unicidad. La fecha es YYYYMMDD de hoy para evitar colisiones entre
    ejecuciones del mismo día. El ID de cliente se stripia de ceros
    ('00050561' → '50561').
    """
    import datetime

    if not fecha:
        fecha = datetime.date.today().isoformat().replace("-", "")
    # Id de cliente visible (corto): 00050561 → 50561 (convención única
    # en core.utils.cliente_visible).
    cli_short = cliente_visible(cliente_id)
    return f"EXP-{codigo}-{cli_short}-{serie}-{nro}-{fecha}"


def build_documento_nombre(expediente_id: str, tipo_doc: str, extension: str) -> str:
    """Nombre de un documento del expediente.

    ``tipo_doc`` es el sufijo estable: Informe | Calculo | Historico | CalculoND.
    Ej: 'EXP-DC-50561-204-67721-20260910_Informe.docx'.
    """
    return f"{expediente_id}_{tipo_doc}.{extension}"


# ── Mapeo reverso: legacy_type -> código de caso ───────────────────────────
LEGACY_A_CASO: dict[str, str] = {}
for _cod, _caso in CATALOGO.items():
    for _lt in _caso.legacy_types:
        LEGACY_A_CASO[_lt] = _cod


def caso_de_legacy(legacy_type: str) -> str | None:
    return LEGACY_A_CASO.get(legacy_type)


# ── Configuración del Histórico (Etapa B) ──────────────────────────────────
# Documentos que pueden aparecer en el histórico y su comportamiento.
# Cada entrada: (incluir_en_reporte, considerar_en_calculo) por defecto.
DOC_HISTORICO = ("facturas", "nc", "ndb")

DOC_HISTORICO_LABEL = {
    "facturas": "Facturas",
    "nc": "NC",
    "ndb": "NDB",
}


@dataclass(frozen=True)
class HistorialConfig:
    """Configuración de qué documentos del histórico se incluyen y se consideran.

    `incluir` controla visibilidad en el reporte (trazabilidad).
    `considerar` controla si entra en el cálculo de la nueva NC.
    """

    facturas: tuple[bool, bool] = (True, True)
    nc: tuple[bool, bool] = (True, False)
    ndb: tuple[bool, bool] = (True, False)

    def incluir(self, doc: str) -> bool:
        return getattr(self, doc, (True, False))[0]

    def considerar(self, doc: str) -> bool:
        return getattr(self, doc, (True, False))[1]

    def as_dict(self) -> dict:
        return {d: list(getattr(self, d)) for d in DOC_HISTORICO}

    @classmethod
    def from_caso(cls, caso: "Caso") -> "HistorialConfig":
        d = {"facturas": (True, True), "nc": (True, False), "ndb": (True, False)}
        for k, v in caso.historico_default.items():
            if k in d:
                d[k] = v
        return cls(**d)
