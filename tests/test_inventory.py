import pandas as pd
from src.core.inventory import update_inventory_balances
from src.core.models import ProcessedItem


def make_item(doc_cantidad=None):
    return ProcessedItem(
        CODIGO="A001",
        ARTICULO="Test",
        CANTIDAD_SOLICITADA=5,
        CANTIDAD_REAL_ENCONTRADA=5,
        PRECIO_UNITARIO=10.0,
        MONTO_DESCUENTO_UNITARIO=1.0,
        PRECIO_NETO_FINAL=9.0,
        SUBTOTAL_DESCUENTO=5.0,
        PORCENTAJE_APLICADO=0.10,
        DOCUMENTOS=["F001-100"],
        STATUS="OK",
        NUMERO="100",
        SERIE="001",
        DOCUMENTOS_CANTIDAD=doc_cantidad or {},
    )


def make_historial():
    return pd.DataFrame(
        {
            "CODIGO": ["A001", "A001"],
            "CANTIDAD": [10.0, 5.0],
            "SOLES": [100.0, 50.0],
            "PRECIO_UNITARIO": [10.0, 10.0],
            "SERIE": ["001", "001"],
            "NUMERO": ["100", "101"],
            "TIPO_DOC": ["F", "F"],
        }
    )


class TestUpdateInventoryBalances:
    def test_no_doc_cantidad_fallback_no_match(self):
        df = make_historial()
        item = make_item()
        item.NUMERO = "999"
        item.SERIE = "999"
        result = update_inventory_balances(df, [item])
        assert len(result) == 2

    def test_with_doc_cantidad(self):
        df = make_historial()
        item = make_item(doc_cantidad={"F001-100": 3.0})
        result = update_inventory_balances(df, [item])
        assert len(result) == 2
        assert result.iloc[0]["CANTIDAD"] == 7.0

    def test_full_deduction_removes_row(self):
        df = make_historial()
        item = make_item(doc_cantidad={"F001-100": 10.0})
        result = update_inventory_balances(df, [item])
        assert len(result) == 1

    def test_empty_items_list(self):
        df = make_historial()
        result = update_inventory_balances(df, [])
        assert len(result) == 2

    def test_item_not_in_historial(self):
        df = make_historial()
        item = make_item(doc_cantidad={"X999-999": 5.0})
        item.CODIGO = "Z999"
        result = update_inventory_balances(df, [item])
        assert len(result) == 2
