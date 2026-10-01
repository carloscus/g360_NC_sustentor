# g360-erp-nc-sustentor — Datos locales (intranet → SQLite)

Este módulo hace al sustentor **autónomo**: descarga el reporte de ventas de la
intranet CIPSA (`Estadistica11.aspx`), lo normaliza y lo guarda en un **SQLite
local**. Todas las consultas del app (histórico, facturas, NC asociadas) se
sirven de ese SQLite, sin depender de Supabase ni del repo Tauri
`g360-ventas-db` (que queda como referencia).

## Arquitectura

```
UI Flet (botón "📥 Datos")
        │  primera carga: 01/01/2024 → hoy + credenciales
        ▼
src/core/capture_service.py     estrategia portada de capture.rs (Rust)
  ├─ rango > 7 días → chunks mensuales; ≤ 7 días → diarios
  ├─ 3 intentos export XLS → fallback grid HTML (L0)
  ├─ mes fallido → reintento día por día
  ├─ lock exclusivo (data/raw/capture.lock) + abort cooperativo
  └─ marcadores data/failed_YYYY-MM.json
        │
        ▼
src/core/intranet_client.py     port de browser/http.rs
  login ASP.NET (__VIEWSTATE) · export D0 (btnExportar)
  · grid L0 → CSV · validación magic bytes (XLS/HTML disfrazado)
        │
        ▼
src/core/xls_processor.py       port de processor/parser.rs
  tipo_operacion (venta/devolucion/ajuste_valor/nota_debito)
  · factura_ref_serie/nro · folio_unico · precio_unitario
  · allowlist de líneas · NC/ND cross-mes
        │
        ▼
src/core/ventas_db.py           port de db/{schema,writer}.rs
  %APPDATA%/g360-erp-nc-sustentor/data/historial.db
  tabla ventas · 15 índices · 12 vistas · WAL · sync_log · dedup
        │
        ▼
src/core/ventas_db_client.py    reemplazo 1:1 de SupabaseVentasClient
  fetch_historial (fechas filtradas EN SQL) · fetch_facturas_disponibles
  · fetch_vendedores / clientes / facturas_cliente · test_connection
```

## Formato del reporte (lecciones del repo Rust)

- `accion=D0` + POST `btnExportar` → archivo XLS (a veces **HTML disfrazado** con
  content-type `excel`; se detecta por magic bytes y se parsea la tabla).
- `accion=L0` → grid HTML renderizado; la tabla mayor se convierte a CSV. Vía
  más robusta; el Rust la usaba como fallback y aquí es respaldo del export.
- Cada chunk requiere **su propio GET** (el server fija el rango de fechas en la
  sesión); reusar VIEWSTATE devuelve datos obsoletos.
- Docs de documentos: `valueDocs=01F,01B,01NCR,01NDB` (facturas, boletas, NCR, NDB).

## Uso desde la UI

1. **📥 Datos** (sección HISTORIAL) → **portal de conexión directa** en dos estados:
   - **Estado A (login)**: pide usuario/contraseña de intranet y el botón
     **🔌 Conectar** (Enter también funciona). Valida el login real + sonda del
     reporte. Si falla: mensaje "✗ Acceso denegado" y **no se revela nada más**
     (se reintenta).
   - **Estado B (conectado)**: solo aparece si el login es correcto — badge
     verde con la sesión activa, rango de fechas, **⬇ Primera carga** /
     **🔄 Actualizar**, progreso en vivo con ETA y log por mes.
   - **Tiempo estimado + confirmación**: al pulsar Primera carga/Actualizar se
     muestra una alerta con el estimado (~1 min por mes/bloque) y el detalle
     de bloques. Se puede detener sin perder lo descargado.
   - **Preparación SQLite visible**: `init_db` al inicio y etapa final de
     dedup + estadísticas con su duración.
2. **🔍 Buscar** → cascada vendedor → cliente → factura, igual que antes; los
   datos salen del SQLite local (indica "SQLite local"). Si aún no hay datos
   locales, ofrece el botón **📥 Descargar datos** que abre el portal de
   conexión (login → descarga → listo para buscar).

```python
from src.core.intranet_client import IntranetClient
from src.core.capture_service import CaptureService

user, pwd = CaptureService.credentials()  # env o config.json local
cli = IntranetClient(user, pwd, timeout=90.0)
try:
    ok, msg = cli.verify_credentials()
    # ok=True  -> "Credenciales OK — sesión activa en N ms · reporte accesible (N filas ...)"
    # ok=False -> motivo: credenciales rechazadas / sin red / sin permisos
finally:
    cli.close()
```

## Programático (cliente de datos)

```python
from src.core.ventas_db_client import VentasDbClient

cli = VentasDbClient()
ok, msg = cli.test_connection()

df = cli.fetch_historial(
    id_cliente="00068414",
    id_articulo="02211",
    fecha_desde="2024-01-01",
    fecha_hasta="2024-09-07",
)
# Columnas derivadas: DOC_ID, TIPO_CLASE (factura/devolucion/descuento/sin_impacto),
# FACTURA_REF (F{serie}-{nro}), AFECTA_CANTIDAD/VALOR, NC_ASOCIADAS (desde vista)
```

## Mapeo de columnas

| SQLite (`ventas`) | Historial (sustentor) |
|---|---|
| `id_articulo` / `nom_articulo` | `CODIGO` / `ARTICULO` |
| `id_cliente` / `nom_cliente` / `doc_cliente` | `COD_CLIENTE` / `CLIENTE` / `DOC_CLIENTE` |
| `tpo_doc` / `serie_doc` / `nro_doc` | `TIPO_DOC` / `SERIE` / `NUMERO` |
| `fecha_orig` | `FECHA` (yyyy-mm-dd) |
| `cantidad` / `soles` / `dolares` / `precio_unitario` | `CANTIDAD` / `SOLES` / `DOLARES` / `PRECIO_UNITARIO` |
| `tipo_operacion` | `TIPO_CLASE` (venta→factura, devolucion, ajuste_valor→descuento, nota_debito→sin_impacto) |
| `factura_ref_serie` / `factura_ref_nro` | `FACTURA_REF` (`F012-457996`) |
| `folio_unico` | `FOLIO_UNICO` (`F012/012/457996`) |

Vistas clave:
- `vw_facturas_disponibles` — saldo LIFO (`saldo_disponible`) y
  `precio_para_devolucion` (precio neto tras NC totales ≥ 99%).
- `vw_nc_asociadas` — factura → lista de NC/ND (`DOC_ID` formato del app).
- `vw_devoluciones`, `vw_documento`, `vw_venta_neta_producto`, etc.

## Credenciales y rutas

| Qué | Dónde |
|---|---|
| SQLite | `%APPDATA%\g360-erp-nc-sustentor\data\historial.db` |
| Respaldos (semanal, rota 8) | `%APPDATA%\g360-erp-nc-sustentor\data\backup\historial_YYYYMMDD.db` |
| Credenciales + config | `%APPDATA%\g360-erp-nc-sustentor\data\config.json` |
| Descargas crudas | `%APPDATA%\g360-erp-nc-sustentor\data\raw\ventas_YYYY-MM.xls` |
| Meses fallidos | `%APPDATA%\g360-erp-nc-sustentor\data\failed_YYYY-MM.json` |
| Override de ruta (tests) | env `G360_DATA_DIR` |
| Override credenciales | env `G360_INTRANET_USER` / `G360_INTRANET_PASS` |

## Multi-PC (red local)

Cada PC mantiene su propio SQLite. Para evitar que cada una descargue ~33 meses
(~2h de carga al server intranet):

1. **Una PC descarga completa** (o la que tenga el historial más amplio).
2. Copia `%APPDATA%\g360-erp-nc-sustentor\data\historial.db` por la red/USB a
   las demás (app cerrada).
3. En cada PC: **📥 Datos → 📂 Importar SQLite de otra PC** → selecciona el
   archivo → valida integridad+schema, respalda la DB previa y reemplaza.
   Después de importar ya puede consultar y sus capturas incrementales
   continúan desde ahí.

Extras de robustez:
- **Backup semanal automático** tras cada captura (checkpoint WAL +
  `integrity_check` antes de promocionar; rota 8 copias; salta si ya hay del día).
- **Auditoría NC/ND huérfanas**: tras cada captura se informa cuántas NC/ND
  referencian facturas fuera del rango descargado (se anclan solas al extender
  hacia atrás).

## Seguridad

- Consultas siempre parametrizadas; lectura con `mode=ro` (nunca bloquea la
  captura gracias a WAL).
- Credenciales solo en config local del usuario (fuera del repo).
- Sin claves ni URLs externas en el código.

## API opcional (retornos)

`src/api/supabase_endpoint.py` (nombre histórico) ahora sirve los cálculos de
retornos desde el SQLite local:

```bash
uv run uvicorn src.api.supabase_endpoint:app --port 8001
# GET /health · GET /retornos/saldo?... · POST /retornos/calcular
```

## Tests

```bash
uv run python -m pytest tests/test_ventas_db.py tests/test_xls_processor.py tests/test_intranet_client.py tests/test_capture_service.py -q
```

67 tests cubren: schema/vistas, insert/delete-then-insert, dedup, cliente de
lectura (filtros en SQL, derivadas, vistas), parser del reporte (formatos
numérico latino, normalización, allowlist, cross-month), cliente intranet
(parsing ASP.NET, magic bytes, rangos) y servicio de captura (lock, stale lock,
credenciales, abort).

## Módulos futuros / requisitos

### Reporte de compras (pendiente validación)

- **Sección**: `src/ui/reporte_compras.py` — acceso desde botón "Compras" en
  el panel de resultados. Pivote mes × línea (S/ y unidades) por cliente,
  con opción de incluir/excluir NC/ND.
- **Requisito**: esperar que un vendedor confirme que es necesario antes de
  continuar desarrollo o activar la feature. No se muestra el botón ni se
  ejecuta la consulta hasta tener esa validación.

### Recojo (en desarrollo)

- Recojo de mercadería en campo, con geolocalización y fotos.
- Requiere conexión a red movil/3G en campo.
