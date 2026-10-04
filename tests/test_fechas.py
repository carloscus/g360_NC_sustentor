"""La convención de fechas: ISO interno, dd/mm/yyyy en pantalla y exportación."""

from __future__ import annotations

from datetime import date, datetime

from src.core.fechas import (
    FMT_ISO,
    a_fecha,
    FMT_UI,
    fecha_hora_ui,
    fecha_iso,
    fecha_ui,
    rango_ui,
)


def test_interno_sigue_siendo_iso():
    assert FMT_ISO == "%Y-%m-%d"
    assert fecha_iso("2026-10-03") == "2026-10-03"
    assert fecha_iso(date(2026, 10, 3)) == "2026-10-03"
    # Un datetime se recorta a su fecha, no se ensucia.
    assert fecha_iso(datetime(2026, 10, 3, 18, 44, 5)) == "2026-10-03"
    # Y sigue ordenando lexicográficamente, que es el motivo de usar ISO.
    assert sorted([fecha_iso("2026-2-9"), fecha_iso("2026-10-03")]) == [
        "2026-02-09",
        "2026-10-03",
    ]


def test_ui_muestra_dd_mm_yyyy():
    assert fecha_ui("2026-10-03") == "03/10/2026"
    assert fecha_ui(date(2026, 1, 4)) == "04/01/2026"
    assert fecha_ui(datetime(2010, 1, 4, 9, 0)) == "04/01/2010"


def test_ida_y_vuelta_no_pierde_fecha():
    """ISO -> UI -> ISO tiene que devolver el mismo dato."""
    original = "2026-10-03"
    assert fecha_iso(fecha_ui(original)) == original


def test_acepta_formato_historico_con_barras():
    """Se toleran datos viejos en dd/mm/yyyy al parsear."""
    assert fecha_ui("03/10/2026") == "03/10/2026"
    assert fecha_iso("03/10/2026") == "2026-10-03"


def test_iso_con_hora_se_recorta():
    """La API manda timestamps; la pantalla muestra la fecha."""
    assert fecha_ui("2026-10-03T18:44:05Z") == "03/10/2026"
    assert fecha_ui("2026-10-03 18:44:05") == "03/10/2026"


def test_vacios_no_rompen():
    for valor in (None, "", "   ", "no-es-fecha"):
        assert fecha_ui(valor) == ""
        assert fecha_iso(valor) == ""
        assert a_fecha(valor) is None


def test_rango_ui():
    assert rango_ui("2010-01-04", "2026-10-03") == "04/01/2010  →  03/10/2026"
    assert rango_ui(None, None) == "—  →  —"


def test_fecha_hora_ui():
    hoy = datetime.now().strftime("%Y-%m-%d %H:%M")
    assert fecha_hora_ui(hoy) == datetime.now().strftime("%H:%M")
    assert fecha_hora_ui("2020-05-05 14:30") == "05/05/2020 14:30"
    assert fecha_hora_ui(None) == ""


def test_leer_no_depende_del_formato_de_pantalla():
    """Cambiar el separador de pantalla no puede romper la lectura de datos.

    La lista de formatos de ENTRADA es fija a proposito. Si colgara de
    FMT_UI, cambiar la convencion haria ilegibles los historicos que ya estan
    guardados con el separador anterior.
    """
    from src.core import fechas

    assert "%d-%m-%Y" in fechas._FORMATOS_ENTRADA, "se perdio la lectura dd-mm-yyyy"
    assert "%d/%m/%Y" in fechas._FORMATOS_ENTRADA, "se perdio la lectura dd/mm/yyyy"
    assert FMT_ISO in fechas._FORMATOS_ENTRADA
    # Y uno entero con cualquiera de los tres separadores.
    for s in ("2026-10-03", "03-10-2026", "03/10/2026"):
        assert fecha_ui(s) == "03/10/2026", s


def test_excel_fmt_ui_deriva_del_token():
    """El number_format de Excel se deriva de FMT_UI, no se escribe a mano."""
    from src.core.fechas import excel_fmt_ui

    assert excel_fmt_ui() == FMT_UI.replace("%d", "dd").replace("%m", "mm").replace("%Y", "yyyy")
    assert excel_fmt_ui() == "dd/mm/yyyy"
