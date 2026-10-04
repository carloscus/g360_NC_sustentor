"""Convención de fechas del proyecto.

Regla única, en un solo lugar:

* **Interno** (SQLite, API, JSON, claves, comparaciones, sort, nombres de
  archivo): SIEMPRE ISO ``yyyy-mm-dd``. Ordena lexicográficamente, parsea sin
  ambigüedad y es lo que espera la DB.
* **Interfaz y reportes** (lo que lee una persona): ``dd/mm/yyyy``.

Las dos excepciones, donde el ISO se mantiene a propósito:

* **Payload del QR** de la factura (``excel_renderer._build_qr_data``): lo lee
  un sistema externo, no una persona. Pasarlo a guiones/barras podría invalidar
  la validación.
* **Nombres de archivo**: ordenan bien y no dependen del locale.

Cómo se aplica:

* Para mostrar, siempre por acá: ``fecha_ui(valor)``, ``rango_ui()``,
  ``fecha_hora_ui()``. No usar ``strftime`` para mostrar: un literal se
  desincroniza del token y obliga a editar N lugares al cambiar la convención
  (hubo 21 así).
* En Excel, escribir **fecha real** con ``number_format = excel_fmt_ui()``, no
  texto: se ve igual pero la columna ordena y filtra como fecha. Escribida como
  texto se ve bien y ordena como basura.
* Al leer, ``_FORMATOS_ENTRADA`` es una lista **fija**: cambiar el separador de
  pantalla no puede volver ilegibles datos ya guardados.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

#: Formato interno. No cambiar sin migración.
FMT_ISO = "%Y-%m-%d"
#: Formato de cara al usuario (interfaz y reportes). Con "/" porque se lee
#: mejor y es la convención local; el ISO se distingue por el orden del año,
#: no por el separador.
FMT_UI = "%d/%m/%Y"

#: Formatos que se aceptan al LEER, en orden de preferencia.
# Esta lista es FIJA a proposito y no debe colgar de FMT_UI: aunque la
# convencion de pantalla cambie, hay que seguir entendiendo lo que ya esta
# guardado (historico con dd-mm-yyyy, dd/mm/yyyy, ISO...). Si se derivara de
# FMT_UI, cambiar el separador de pantalla romperia la lectura de datos viejos.
_FORMATOS_ENTRADA = (
    FMT_ISO,  # 2026-10-03
    "%d-%m-%Y",  # 03-10-2026 (dd-mm-yyyy, legado)
    "%d/%m/%Y",  # 03/10/2026 (dd/mm/yyyy, convencion actual)
    "%Y/%m/%d",  # 2026/10/03
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)

#: Formatos de timestamp aceptados por `fecha_hora_ui`, con su longitud para
#: recortar el sufijo (Z, milisegundos, offset) sin adivinar.
_FORMATOS_TIMESTAMP = (
    ("%Y-%m-%dT%H:%M:%S", 19),
    ("%Y-%m-%d %H:%M:%S", 19),
    ("%Y-%m-%dT%H:%M", 16),
    ("%Y-%m-%d %H:%M", 16),
    ("%Y-%m-%d", 10),
)


def a_fecha(valor: Any) -> Optional[date]:
    """Convierte a ``date`` aceptando ISO, ``dd-mm-yyyy`` y ``dd/mm/yyyy``.

    Devuelve ``None`` si no hay valor o no se puede interpretar. Nunca lanza:
    un dato sucio no debe tumbar una pantalla.
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    # Con hora pegada ("2026-10-03 18:44", "2026-10-03T18:44:05Z",
    # "03/10/2026 18:44:05") nos quedamos con la parte de la fecha.
    for sep in (" ", "T"):
        if sep in texto:
            texto = texto.split(sep, 1)[0]
            break
    for fmt in _FORMATOS_ENTRADA:
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def fecha_ui(valor: Any, vacio: str = "") -> str:
    """Fecha para mostrar: ``dd-mm-yyyy``.

    Es la función que deben usar las superficies de UI y exportación. Acepta
    ISO (que es como viene de la DB y de la API) y no exige que el llamador se
    acuerde de convertir.
    """
    f = a_fecha(valor)
    return f.strftime(FMT_UI) if f else vacio


def excel_fmt_ui() -> str:
    """``FMT_UI`` traducido a sintaxis de Excel (``number_format``).

    Se deriva del token para que cambiar la convención de pantalla no deje los
    Excel desincronizados. Se usa con celdas de fecha REAL: el formato lo decide
    Excel, y asi la columna sigue ordenando y filtrando como fecha.
    """
    return FMT_UI.replace("%d", "dd").replace("%m", "mm").replace("%Y", "yyyy")


def fecha_iso(valor: Any, vacio: str = "") -> str:
    """Fecha interna: ``yyyy-mm-dd``. Para consultas, claves y comparaciones."""
    f = a_fecha(valor)
    return f.strftime(FMT_ISO) if f else vacio


def anio_de(valor: Any) -> Optional[int]:
    """Año de una fecha, o ``None`` si no se puede interpretar.

    Se usa para partir el histórico del expediente en una hoja por año. Acepta
    datetime, ISO, ``dd-mm-yyyy``, ``dd/mm/yyyy`` y cualquiera de esos con
    hora pegada, que es como vienen varias columnas del ERP.
    """
    f = a_fecha(valor)
    return f.year if f else None


def rango_ui(desde: Any, hasta: Any, separador: str = "  →  ") -> str:
    """Rango para mostrar, p.ej. ``04-01-2010  →  03-10-2026``."""
    return f"{fecha_ui(desde, '—')}{separador}{fecha_ui(hasta, '—')}"


def fecha_hora_ui(valor: Any, vacio: str = "") -> str:
    """Timestamp para mostrar: ``dd-mm-yyyy HH:MM`` (o solo la hora si es hoy)."""
    if valor is None or valor == "":
        return vacio
    if isinstance(valor, str):
        texto = valor.strip().replace("Z", "")
        for fmt, largo in _FORMATOS_TIMESTAMP:
            try:
                valor = datetime.strptime(texto[:largo], fmt)
                break
            except ValueError:
                continue
        else:
            return vacio
    if isinstance(valor, datetime):
        if valor.date() == date.today():
            return valor.strftime("%H:%M")
        return valor.strftime(f"{FMT_UI} %H:%M")
    if isinstance(valor, date):
        return valor.strftime(FMT_UI)
    return vacio


__all__ = [
    "FMT_ISO",
    "FMT_UI",
    "a_fecha",
    "fecha_ui",
    "fecha_iso",
    "rango_ui",
    "fecha_hora_ui",
]
