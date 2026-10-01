# -*- coding: utf-8 -*-
"""Cargador del catálogo de procesos desde catalog/processes.yaml.

Fuente única de verdad para definir qué procesos (gestiones) existen, qué
insumos requieren y cómo se calculan. Diseñado para ser *extensible por
configuración*: agregar un nuevo proceso en el YAML es suficiente para que
aparezca en el catálogo, sin tocar código (ver campos ``caso``).

Cuando se agrega un proceso nuevo:

1. Se escribe un bloque ``<key>`` en catalog/processes.yaml con su ``caso``.
2. Si usa una estrategia nueva, se registra la clase en Pipeline.STRATEGIES
   (src/pipeline.py) con el nombre indicado en ``caso.strategy``.
3. Se agrega la tarjeta de presentación en TIPO_CONFIG (src/ui/reconocimiento_config.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)

# Ruta por defecto relativa a la raíz del repositorio.
RUTA_POR_DEFECTO = str(Path(__file__).parent.parent.parent / "catalog" / "processes.yaml")

# Claves obligatorias por proceso cuando se declara el bloque ``caso``.
CLAVES_CASO = ("codigo", "label")


class CatalogLoader:
    """Carga el catálogo YAML y expone procesos, esquemas y casos.

    API compatible con el antiguo ``CatalogoCargador`` de src/pipeline.py:
    ``listar_procesos``, ``obtener_proceso`` y ``obtener_schema``.

    Extiende con ``listar_casos``, que devuelve la metadata de nivel UI
    (bloque ``caso:`` de cada proceso) para que el catálogo de la vista
    (src/ui/catalog.py) pueda construirse y ampliarse por configuración.
    """

    def __init__(self, ruta: Optional[str] = None, estricto: bool = False):
        ruta = ruta or RUTA_POR_DEFECTO
        self.ruta = Path(ruta)
        self._catalogo: Dict[str, Any] = {}
        self._errores: List[str] = []
        self._cargar(estricto=estricto)

    def _cargar(self, estricto: bool = False) -> None:
        if yaml is None:
            self._errores.append("Dependencia 'yaml' no disponible")
            logger.error("PyYAML no está instalado; el catálogo no se cargó")
            return
        if not self.ruta.exists():
            msg = f"Catálogo no encontrado: {self.ruta}"
            self._errores.append(msg)
            if estricto:
                raise FileNotFoundError(msg)
            logger.error(msg)
            return
        try:
            with self.ruta.open(encoding="utf-8") as f:
                cargado = yaml.safe_load(f)
            self._catalogo = cargado or {}
            for key, proc in self._catalogo.items():
                if not isinstance(proc, dict):
                    self._errores.append(f"Proceso '{key}' no es un bloque válido")
        except Exception as e:
            msg = f"Error al leer el catálogo YAML: {e}"
            self._errores.append(msg)
            if estricto:
                raise ValueError(msg)
            logger.error(msg, exc_info=True)

    @property
    def errores(self) -> List[str]:
        """Devuelve los errores de carga acumulados (vacío si todo OK)."""
        return list(self._errores)

    def listar_procesos(self) -> List[dict]:
        """Devuelve una lista de dicts con el id numérico y la key de cada proceso."""
        return [
            {"id": v["id"], "key": k, **v} for k, v in self._catalogo.items() if isinstance(v, dict)
        ]

    def _resolver_alias(self, key: str) -> str:
        """Resuelve una clave canónica o un ``legacy_type`` declarado en el YAML.

        Los bloques ``caso`` declaran sus ``legacy_types`` (por ejemplo VRS
        acepta la antigua ``diferencia_cantidad``). Cualquier consulta por un
        alias devuelve el proceso canónico, de modo que releer un archivo de
        la versión anterior siga funcionando sin tocar el YAML.
        """
        if key in self._catalogo:
            return key
        for canonico, proc in self._catalogo.items():
            if not isinstance(proc, dict):
                continue
            caso = proc.get("caso")
            if isinstance(caso, dict) and key in (caso.get("legacy_types") or ()):
                return canonico
        return key

    def obtener_proceso(self, key: str) -> dict:
        """Devuelve el bloque completo del proceso (o {} si no existe)."""
        return self._catalogo.get(self._resolver_alias(key), {})

    def obtener_schema(self, key: str) -> dict:
        """Devuelve el bloque ``template`` del proceso (schema de normalización)."""
        return self.obtener_proceso(key).get("template", {})

    def obtener_hash(self) -> str:
        """Hash estable del catálogo para detectar cambios sin re-parsear."""
        import hashlib

        try:
            raw = self.ruta.read_bytes()
        except OSError:
            return ""
        return hashlib.md5(raw).hexdigest()

    # ── Nivel UI: casos ─────────────────────────────────────────────────────
    def listar_casos(self) -> List[dict]:
        """Devuelve la metadata ``caso`` de cada proceso que la declare.

        Cada dict normalizado tiene la forma lista para los campos
        multivalores (insumos, legacy_types, modalidades) y dict para
        historico_default, listo para construir un ``Caso``.
        """
        casos: List[dict] = []
        for key, proc in self._catalogo.items():
            if not isinstance(proc, dict):
                continue
            caso = proc.get("caso")
            if not isinstance(caso, dict):
                continue
            faltantes = [c for c in CLAVES_CASO if c not in caso]
            if faltantes:
                self._errores.append(f"Proceso '{key}': el bloque caso no tiene {faltantes}")
                continue
            casos.append(self._normalizar_caso(key, proc, caso))
        return casos

    def _normalizar_caso(self, key: str, proc: dict, caso: dict) -> dict:
        """Unifica un bloque ``caso`` a la estructura canónica de ``Caso``."""
        historico = caso.get("historico_default") or {}
        if isinstance(historico, dict):
            historico = {k: (tuple(v) if isinstance(v, list) else v) for k, v in historico.items()}
        return {
            "codigo": str(caso.get("codigo", "")),
            "label": str(caso.get("label", key)),
            "descripcion": str(caso.get("descripcion", "") or ""),
            "insumos": tuple(caso.get("insumos", [])),
            "resultado": str(caso.get("resultado", "NC")),
            "legacy_types": tuple(caso.get("legacy_types", [key])),
            "strategy": str(caso.get("strategy", key)),
            "modalidades": tuple(caso.get("modalidades", [])),
            "historico_default": historico,
        }

    def hay_caso_para(self, key: str) -> bool:
        """True si el proceso declara su bloque ``caso`` en el YAML."""
        proc = self._catalogo.get(key)
        return isinstance(proc, dict) and isinstance(proc.get("caso"), dict)
