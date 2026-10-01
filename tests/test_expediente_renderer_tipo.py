"""The expediente service translates UI case codes to renderer keys."""

from types import SimpleNamespace

from src.ui.expediente_service import _generar_calculo


def test_generar_calculo_usa_clave_renderer_legacy(monkeypatch, tmp_path):
    llamado = {}

    class FakeRenderer:
        def generar(self, resultado, ruta, **kwargs):
            llamado.update(kwargs)

    monkeypatch.setattr("src.ui.expediente_service.ExcelRenderer", FakeRenderer)
    _generar_calculo(
        resultado=SimpleNamespace(resultado=object()),
        tipo="DC",
        cliente="Cliente",
        vendedor="",
        doc_ref="F01-1",
        ruc="",
        excel_path=tmp_path / "calculo.xlsx",
    )
    assert llamado["tipo"] == "diferencia_precio"


def test_generar_calculo_deja_clave_tecnica_sin_cambio(monkeypatch, tmp_path):
    llamado = {}

    class FakeRenderer:
        def generar(self, resultado, ruta, **kwargs):
            llamado.update(kwargs)

    monkeypatch.setattr("src.ui.expediente_service.ExcelRenderer", FakeRenderer)
    _generar_calculo(
        resultado=SimpleNamespace(resultado=object()),
        tipo="diferencia_stock",
        cliente="Cliente",
        vendedor="",
        doc_ref="F01-1",
        ruc="",
        excel_path=tmp_path / "calculo.xlsx",
        modalidad="consolidado",
    )
    assert llamado["tipo"] == "diferencia_stock"
