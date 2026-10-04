"""Mensajes de error para la persona, no para el log.

Antes los snackbars imprimian la excepcion cruda ("Error: KeyError: 'fecha_max'").
Eso no le dice nada a quien usa la app: no nombra el problema ni que hacer, y
ademas cambia con cada refactor.

Aqui se separa lo que ve la persona de lo que queda registrado:

* el snackbar nombra el problema y la salida;
* el traceback completo va al log, que desde este momento si llega a app.log
  (ver el bloque de logging de main.py).

No se oculta nada: si el error es de conexion o de datos, se dice. Solo se
limpia la sintaxis de Python.
"""

from __future__ import annotations

import logging

log = logging.getLogger("g360.ui.mensajes")

# Palabras que delatan un fallo de entorno y sugieren un mensaje propio.
_CONEXION = (
    "connection",
    "connecterror",
    "winerror",
    "connection refused",
    "connectionreset",
    "timed out",
    "timeout",
    "no se pudo establecer",
    "getaddrinfo",
    "name or service not known",
)
_SIN_DATO = (
    "no such column",
    "no such table",
    "integrityerror",
    "notadatabaseerror",
    "database is locked",
)


def _tipo(exc: BaseException) -> str:
    """Clase del error en Castellano, sin jerga de Python."""
    nombre = type(exc).__name__.lower()
    if any(k in nombre for k in ("keyerror", "indexerror")):
        return "Faltó un dato del expediente"
    if any(k in nombre for k in ("filenotfound", "notadirectory")):
        return "No se encontró el archivo"
    if any(k in nombre for k in ("permission", "isadirectory")):
        return "No se pudo acceder al archivo"
    if "timeout" in nombre or "timeout" in str(exc).lower():
        return "La operación tardó demasiado"
    return "Ocurrió un problema"


def resumen(exc: BaseException, contexto: str = "", accion: str = "") -> str:
    """Mensaje corto para snackbar: qué pasó y qué hacer.

    `accion` es la salida concreta ("Revisá que el servidor esté encendido").
    """
    # Si hay accion, esa frase ya nombra problema y salida: prefijarla con
    # "Ocurrio un problema" solo la alarga y la hace mas vaga.
    if accion:
        return accion
    base = _tipo(exc)
    if contexto:
        return f"{base} en {contexto}"
    return f"{base}. Revisá los datos e intentá de nuevo."


def es_conexion(exc: BaseException) -> bool:
    """True si el fallo es de red: merece un mensaje propio, no genérico."""
    texto = f"{type(exc).__name__} {exc}".lower()
    return any(k in texto for k in _CONEXION)


def es_datos(exc: BaseException) -> bool:
    texto = f"{type(exc).__name__} {exc}".lower()
    return any(k in texto for k in _SIN_DATO)


def reporta(exc: BaseException, contexto: str = "") -> None:
    """Deja el detalle completo en el log (con traceback) sin romper nada."""
    log.error("%s: %s", contexto or "error", exc, exc_info=True)


def mensaje(exc: BaseException, contexto: str = "", accion: str = "") -> str:
    """Loguea el detalle y devuelve el texto para snackbar.

    Uso tipico en un except:
        show_snackbar(mensaje(ex, "cargar cliente"), color=G360_ERROR)
    """
    reporta(exc, contexto)
    if es_conexion(exc):
        return resumen(
            exc,
            contexto,
            "No se pudo comunicar con el servidor. Revisá que esté encendido.",
        )
    if es_datos(exc):
        return resumen(exc, contexto, "La base de datos está incompleta o no responde.")
    return resumen(exc, contexto, accion)
