# G360 NC-Sustentor Pro 🚀

**G360 NC-Sustentor Pro** es una herramienta avanzada diseñada para automatizar la generación de cuadros de sustento para Notas de Crédito (NC). Utiliza un motor de asignación **FIFO Inverso** para vincular devoluciones con las facturas de compra más recientes, garantizando precisión contable y cumplimiento con los estándares de auditoría de G360.

---

## ✨ Funcionalidades Destacadas

### 🧠 Inteligencia de Procesamiento
- **Selección Inteligente de Referencia:** El sistema elige automáticamente la factura más representativa (mayor frecuencia de ítems y peso económico) como documento maestro para el encabezado y nombre de pestaña.
- **Precisión de 4 Decimales:** Manejo riguroso de precios unitarios (`SOLES / CANTIDAD`) para garantizar que el Subtotal en Excel coincida exactamente con la facturación original.
- **Validación de Techo Financiero:** Alerta roja si el monto solicitado de Nota de Crédito excede el valor disponible en los documentos de soporte.
- **Gestión Multi-Reporte:** Sistema inteligente de descuento de inventario que asegura que cada factura se use solo una vez entre múltiples reportes. Descuenta automáticamente todas las facturas utilizadas por artículo, evitando el doble conteo.
- **Normalización de Documentos:** Limpieza automática de prefijos duplicados (F204-F204-51999 → F204-51999) para garantizar formato correcto en reportes.
- **Estrategias de Ordenamiento:** Permite elegir entre sustentar por "Más Recientes" (orden cronológico) o por "Mayor Volumen" (priorizando facturas con más cantidad comprada).
- **Limpieza de Datos Robusta:** Corrección automática de errores comunes de digitación (ej: 'O' por '0'), eliminación de caracteres invisibles (BOM UTF-8), y normalización de IDs de artículos para evitar duplicados por formato.

### 📊 Visualización y Dashboard
- **Analytics de Historial:** Dashboard integrado que muestra un gráfico de barras comparativo ("Butterfly Chart") del Top 16 de líneas de producto por monto en soles.
- **Rango de Auditoría:** Identificación visual rápida de las fechas de inicio y fin del historial cargado mediante badges dinámicos.
- **Vista Previa en Tiempo Real:** Tabla de datos interactiva para inspeccionar el historial antes de iniciar el procesamiento.

### 📑 Generación de Reportes Pro
- **Fórmulas Vivas en Excel:** El reporte generado incluye fórmulas de Excel (ROUND, SUM, multiplicaciones). Si el usuario edita una celda, el reporte se actualiza automáticamente.
- **Jerarquía de Totales:** Resumen financiero (Subtotal, IGV, Total) ubicado en la parte superior derecha para una lectura ejecutiva inmediata.
- **Gestión de Archivos Inteligente:** Sistema de resolución de conflictos que pregunta al usuario si desea Sobrescribir, Crear una Copia versionada o Saltar un archivo en caso de duplicados en el Escritorio.

---

## 📉 Sistema de Alertas (Auditoría Visual)

El reporte Excel utiliza un sistema de semáforos para facilitar la revisión:
- 🔴 **Rojo (Error):** Stock insuficiente en historial, artículo no encontrado o errores críticos de datos.
- 🟡 **Amarillo (Advertencia):** Precios de facturación variables detectados entre los documentos utilizados para el sustento.
- 🔵 **Azul (Información):** Ítems procesados con cantidad o porcentaje en cero en el requerimiento original (para completado manual).

---

## 📂 Estructura de Datos Requerida

Para asegurar el correcto funcionamiento, el sistema requiere dos archivos Excel (puedes usar el botón "Descargar Plantillas" en la app):

### 1. Historial de Compras (Base Total)
Archivo exportado del ERP que contiene todas las ventas al cliente. El procesador busca automáticamente las cabeceras basándose en palabras clave.
- `ID_ARTICULO`: Código del producto.
- `NOM_ARTICULO`: Nombre o descripción.
- `CANTIDAD`: Unidades compradas.
- `PRECIO_UNID`: Precio unitario facturado.
- `FECHA_ORIG`: Fecha de la factura (Formato DD/MM/YYYY).
- `TPO_DOC`, `SERIE_DOC`, `NRO_DOC`: Datos para la trazabilidad exacta.

### 2. Tabla de Requerimientos (Input Usuario)
Archivo con los productos que el cliente desea devolver:
- `CODIGO`: SKU del producto.
- `CANTIDAD_NC`: Unidades a sustentar.
- `PORCENTAJE_DESC`: Descuento a aplicar (ej: 3%, 1.25 o 0.03). Mínimo recomendado: 0.5%.

---

## 🏗️ Arquitectura del Proyecto

El proyecto sigue una arquitectura modular y escalable:
- **`src/core/processor.py`**: El cerebro del sistema. Maneja la limpieza de Pandas, el motor FIFO inverso y la lógica de negocio. Incluye campo DOCUMENTOS_CANTIDAD para rastrear cantidad por documento.
- **`src/excel/generator.py`**: Gestiona la creación de archivos openpyxl, la aplicación de estilos G360 y la inserción de fórmulas.
- **`main.py`**: Controlador de la interfaz gráfica Flet y orquestador de los flujos asíncronos. Incluye gestión inteligente de inventario multi-reporte.
- **`assets/`**: Recursos visuales (Logos G360).

### Cambios en v2.0.1
- Eliminación de imports no utilizados (logging, pandas en generator, Path, get_column_letter).
- Mejora en FIFO inverso: normalización de serie y número de documento para evitar prefijos duplicados.
- Sistema de rastreo por documento: cada artículo ahora registra cuánto se toma de cada factura (DOCUMENTOS_CANTIDAD).
- Descuento inteligente: _update_inventory_balances itera por cada documento utilizado, no solo el más reciente.
- Sincronización completa entre versión principal y portable.

### Cambios en v2.0.2 (Mejoras Recientes)
- **Corrección crítica de Fechas (str vs float):** Se forzó la limpieza y conversión obligatoria de fechas del historial a formato serial, previniendo cuelgues al analizar métricas y asegurando un ordenamiento FIFO inverso preciso.
- **Ruta de Descarga Dinámica:** El sistema ahora detecta automáticamente si el usuario respalda su Escritorio en OneDrive (`Path.home() / "OneDrive" / "Desktop"`), previniendo que los reportes se guarden en ubicaciones invisibles.
- **Limpieza de Interfaz en Excel:** Se eliminó la columna redundante "DOC. REFERENCIA" de la tabla de resultados. El dato maestro de referencia ahora habita exclusivamente en el cuadro superior derecho de totales.
- **Auto-Apertura y Manejo de Errores:** Solucionado el bug de firmas de funciones (`takes 4 arguments but 5 were given`) que bloqueaba el guardado y la auto-apertura del Excel al finalizar.
- **Sincronización Total:** Código 100% espejado entre `g360-nc-sustentor` y `g360-nc-sustentor-portable`.

---

## 🛠️ Estructura del Reporte (Excel)

El reporte final está optimizado para una revisión ejecutiva:
- **Encabezado Superior:** Fecha actual, Nombre del Cliente (en grande y negrita) y Motivo.
- **Resumen de Totales (Top-Right):** Subtotal, IGV y Total Final ubicados en las primeras filas para lectura rápida.
- **Tabla de Sustento:**
    - `TOTAL FACTURADO`: Cantidad x P.U. Original.
    - `DESC. UNITARIO`: Monto exacto del descuento por unidad.
    - `SUBTOTAL NC`: El monto total a devolver por ese ítem.
    - `FACTURAS`: Lista de documentos (correctamente formateados) que sirven de sustento.
    - `ALERTA`: Mensajes de error o advertencia si el stock es insuficiente o los precios varían.

---

## 🚀 Puesta en Marcha

1. **Requisitos:** Python 3.10 o superior (Recomendado 3.12+).
2. **Automatización:** Se incluye un archivo `run.bat` que gestiona automáticamente:
   - Creación del entorno virtual (.venv).
   - Instalación de dependencias actualizadas.
   - Lanzamiento de la aplicación.
3. **Ejecución:**
   - Haz doble clic en `run.bat`.

---

## 📦 Dependencias Técnicas

- `flet`: UI Framework multiplataforma.
- `pandas`: Análisis y manipulación de datos de alto rendimiento.
- `openpyxl`: Motor de lectura/escritura de archivos XLSX.

---

**Desarrollado para el Ecosistema G360.**
*Precisión, Velocidad y Auditoría.*
