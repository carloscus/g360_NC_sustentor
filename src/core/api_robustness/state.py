"""Estado compartido del health-check en background.

`main._start_background_health_check` escribe el último diagnóstico acá; la UI
(card DB, badge de header) lo lee sin tener que llamar a la red por sí sola.
Thread-safe en la práctica: dict assignment en Python es atómico via GIL; un
lector puede ver un frame medio-viejo y no importa (la próxima iteración lo
reescribe). No tiene ni lock ni callback: es un pub/sub de un solo dato.
"""

from __future__ import annotations

from typing import Any, Optional

_ULTIMO: Optional[dict[str, Any]] = None


def publicar_health(estado: dict[str, Any]) -> None:
    """Publica el resultado de un chequeo (o par tolerante de fallos).

    ``estado`` debería tener::

      {
          "api_online": bool,
          "desfase_horas": float | None,   # antigüedad del snapshot, None si
                                          # el status no llegó (token, health, etc)
          "url": str,
          "error": str | None,
          "checked_at": float,           # time.time()
      }
    """
    global _ULTIMO
    _ULTIMO = dict(estado)


def ultimo_health() -> Optional[dict[str, Any]]:
    """Último chequeo publicado, o None si el thread todavía no corrió."""
    return _ULTIMO
