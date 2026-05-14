# G360 NC-Sustentor Pro 🚀

> Microherramienta avanzada del ecosistema G360 para la automatización de cuadros de sustento de Notas de Crédito (NC) y análisis de ventas CRM.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Repo: GitHub](https://img.shields.io/badge/Repository-GitHub-blue.svg)](https://github.com/carloscus/g360_NC_sustentor.git)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## 📋 Tabla de Contenidos

- [Características](#características)
- [Instalación](#instalación)
- [Uso Básico](#uso-básico)
- [Reportes Disponibles](#reportes-disponibles)
- [Diccionario de Datos](#diccionario-de-datos)
- [Decisiones de Diseño Importantes](#decisiones-de-diseño-importantes)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Desarrollo](#desarrollo)

---

## 🚀 Características

### 🧠 Inteligencia de Procesamiento
- **Lógica FIFO Inversa (Sincronizada):** Asignación automática de facturas más recientes con descuento inteligente de inventario entre múltiples reportes.
- **Selección de Referencia Maestra:** Identifica automáticamente la factura más representativa para el encabezado del reporte.
- **Precisión Financiera:** Cálculos con 4 decimales para garantizar que el Subtotal en Excel coincida exactamente con el ERP.
- **Validación de Techo Financiero:** Alerta visual si el monto solicitado excede el disponible en el historial.

### 📊 Visualización & Analytics
- **Dashboard "Butterfly Chart":** Comparativa visual del Top 16 de líneas de producto por monto.
- **Vista Previa Interactiva:** Inspección rápida del historial cargado antes del procesamiento.
- **Módulo de Reportes Consolidados:**
    - **Por SKU**: Análisis detallado por artículo.
- **Por Línea**: Análisis de ventas por línea de producto
- **Por Cliente**: Análisis de ventas por cliente
- **Por Mes**: Análisis de ventas por período
- **Por Factura**: Análisis detallado por documento
- **Pareto Cliente**: Análisis 80/20 de clientes por vendedor
- **Comparativo**: Comparación mes a mes con tendencias

### Validación de Datos
- **Validación de campos críticos**: ID_ARTICULO, NOM_ARTICULO, FECHA_ORIG, CANTIDAD, SOLES, TPO_DOC, SERIE_DOC, NRO_DOC
- **Validación de campos compuestos**: SKU, LÍNEA, CLIENTE, VENDEDOR
- **Validación de fechas**: Consistencia entre ANHO/MES y FECHA_ORIG
- **Validación de montos y cantidades**: Consistencia entre SOLES, CANTIDAD y PRECIO_UNID
- **Validación de documentos**: Detección de documentos duplicados

---

## 📦 Instalación

### Requisitos
- Python 3.8+
- pip install -r requirements.txt

### Instalación
```bash
# Recomendado: Usar UV para gestión rápida de dependencias
uv pip install -r requirements.txt

# Ejecución tradicional
python main.py
```

---

## 🎯 Uso Básico

### 1. Cargar Historial
1. Click en "1. HISTORIAL (BASE)"
2. Seleccionar archivo Excel del historial de compras
3. El sistema validará y procesará los datos

### 2. Cargar Requerimientos
1. Click en "2. REQUERIMIENTOS"
2. Seleccionar archivo(s) Excel de requerimientos de NC
3. El sistema procesará y generará las notas de crédito

### 3. Generar Reportes Consolidados
1. Click en "REPORTES CONSOLIDADOS"
2. Seleccionar filtros (vendedores, clientes, líneas)
3. Seleccionar tipo de reporte y agrupación
4. Click en "GENERAR REPORTE EXCEL"

---

## 📊 Reportes Disponibles

### Reportes Consolidados

| Tipo | Descripción | Agrupación |
|------|-------------|------------|
| **Por SKU** | Análisis de ventas por artículo | SKU → LÍNEA → CLIENTE |
| **Por Línea** | Análisis de ventas por línea de producto | LÍNEA → SKU → CLIENTE |
| **Por Cliente** | Análisis de ventas por cliente | CLIENTE → LÍNEA → SKU |
| **Por Mes** | Análisis de ventas por período | PERIODO → SKU → LÍNEA → CLIENTE |
| **Por Factura** | Análisis detallado por documento | FACTURA → SKU |
| **Pareto Cliente** | Análisis 80/20 de clientes | CLIENTE (columnas por LÍNEA) |
| **Comparativo** | Comparación mes a mes con tendencias | SKU/LÍNEA/CLIENTE (columnas por MES) |

### Campos en Reportes

#### Comunes a Todos
- `N°`: Número de fila
- `CANTIDAD`: Cantidad total
- `MONTO`: Monto total en soles
- `FECHA ULT.`: Fecha más reciente
- `FACTURAS`: Lista de documentos
- `PRECIOS`: Lista de precios

#### Específicos
- **Por SKU**: SKU, LÍNEA, CLIENTE
- **Por Línea**: LÍNEA, SKU, CLIENTE
- **Por Cliente**: CLIENTE, LÍNEA, SKU
- **Por Mes**: PERIODO, SKU, LÍNEA, CLIENTE
- **Por Factura**: FACTURA, FECHA, CLIENTE, LÍNEA, SKU, CANTIDAD, PRECIO, MONTO
- **Pareto Cliente**: CLIENTE, TOTAL, %, CAT, [L01-CANT, L01-MONTO, L01-%], [L02-CANT, L02-MONTO, L02-%], ...
- **Comparativo**: [AGRUPACIÓN], [MES1-CANT, MES1-MONTO, MES1-FACT], [MES2-CANT, MES2-MONTO, MES2-FACT], FECHA ULT., DIF_SOLES, DIF_PCT, TENDENCIA

---

## 📚 Diccionario de Datos

### Campos Compuestos

| Campo | ID | Nombre | Formato | Uso |
|-------|----|-------|---------|-----|
| **SKU** | `ID_ARTICULO` | `NOM_ARTICULO` | "ID - NOMBRE" |
| **LÍNEA** | `ID_LINEA` | `NOM_LINEA` | "ID - NOMBRE" |
| **CLIENTE** | `ID_CLIENTE` | `NOM_CLIENTE` | "ID - NOMBRE" |
| **VENDEDOR** | `ID_VENDEDOR` | `NOM_VENDEDOR` | "ID - NOMBRE" |
| **SUCURSAL** | `COD_SUCURSAL` | `NOM_SUCURSAL` | "ID - NOMBRE" |

### Campos de Documento

| Campo | Formato | Ejemplo |
|-------|---------|---------|
| **FACTURA** | "TIPO + SERIE - NUMERO" | "F012-0457996" |
| **PEDIDO** | "ID_PEDIDO" | "12345" |

### Campos de Lista

| Campo | Singular | Descripción |
|-------|----------|-------------|
| **CLIENTES** | CLIENTE | Lista de clientes |
| **FACTURAS** | FACTURA | Lista de facturas |

### Valores a Filtrar

Los siguientes valores son filtrados automáticamente:
- `SIN ASIGNAR`
- `''` (vacío)
- `nan`
- `None`

---

## ⚠️ Decisiones de Diseño Importantes

### 1. Pareto - Uso de Solo ID de Líneas como Encabezados

**Diseño Actual:**
- Los encabezados de columnas de líneas usan **solo el ID** (ej: 0101, 0156)
- No incluyen el nombre de la línea (ej: "0101 - ARCHIVO")

**Justificación:**
- ✅ **Ahorro de espacio**: Los nombres de líneas pueden ser muy largos (ej: "BEBIDAS GASEOSAS - LATA 1L")
- ✅ **Legibilidad**: IDs cortos (4-6 caracteres) son fáciles de leer
- ✅ **Identificación**: El ID es suficiente para identificar la línea
- ✅ **Experiencia de usuario**: Los usuarios conocen los IDs de sus líneas

**⚠️ Advertencia:**
- Este diseño es **intencional** y **no debe cambiarse**
- Los usuarios deben conocer los IDs de sus líneas
- El nombre completo está disponible en el diccionario de datos maestros
- Cambiar esto haría el reporte muy ancho y difícil de leer

**Ubicación:** `src/excel/generator.py:683`

**Estructura del Reporte:**
```
CLIENTE | TOTAL | % | CAT | [0101-CANT | 0101-MONTO | 0101-%] | [0156-CANT | 0156-MONTO | 0156-%] | ...
```

---

### 2. NC - Uso de Columna SKU Adicional (ID Puro + Formato Completo)

**Diseño Actual:**
- Columna **"SKU (ID Puro)"**: Solo el ID del artículo (ej: "12345")
- Columna **"SKU - ARTICULO"**: Formato completo "ID - NOMBRE" (ej: "12345 - Producto A")

**Justificación:**
- ✅ **Filtrado manual en Excel**: Permite filtrar rápidamente por SKU usando el ID puro
- ✅ **Ordenamiento alfabético**: El ID puro es más fácil de ordenar que el formato completo
- ✅ **Validación con sistemas externos**: Muchos sistemas usan solo el ID del SKU
- ✅ **Manejo de errores**: Si hay error en el nombre, el ID puro sigue siendo correcto

**⚠️ Advertencia:**
- Este diseño es **intencional** y **no debe cambiarse**
- La columna "SKU (ID Puro)" es para filtrado, ordenamiento y validación manual
- La columna "SKU - ARTICULO" es para identificación visual
- Cambiar esto dificultaría el manejo manual en Excel

**Ubicación:** `src/excel/generator.py:165-173`

**Estructura del Reporte:**
```
N° | SKU (ID Puro) | SKU - ARTICULO | LÍNEA | CANT. SUSTENTAR | P.U. | TOT. FACT. | DESC. (%)
```

---

### 3. Excepciones al Estándar del Diccionario

**Estándar del Diccionario:**
- Todos los campos compuestos usan formato "ID - NOMBRE"

**Excepciones Documentadas:**

| Reporte | Campo | Formato | Justificación |
|---------|-------|---------|---------------|
| **Pareto** | LÍNEA (encabezados) | Solo ID | Ahorro de espacio con muchas líneas |
| **NC** | SKU (columna adicional) | ID puro | Facilita filtrado manual en Excel |

**⚠️ Advertencia:**
- Estas excepciones son **intencionales** y **no deben cambiarse**
- Están documentadas en este README y en el diccionario de datos
- Cambiarlas sin justificación clara causará problemas en los reportes

---

## 🔧 Estructura del Proyecto

```
g360-nc-sustentor/
├── src/
│   ├── core/
│   │   ├── processor.py          # NCProcessor (lógica FIFO Inversa)
│   │   ├── data_dictionary.py    # Diccionario centralizado de campos
│   ├── excel/
│   │   └── generator.py          # Generación de Excel (OpenPyXL)
│   └── ui/
│       └── consolidated_view.py  # Módulo de Reportes Consolidados
├── assets/
│   └── templates/               # Plantillas de Excel
├── g360-nc-sustentor-portable/   # Versión para distribución (En desarrollo)
├── main.py                        # Aplicación principal
├── requirements.txt               # Dependencias de Python (pip)
└── pyproject.toml                 # Configuración del proyecto (uv)
```

---

## 🛠️ Desarrollo

### Ejecutar Tests
```bash
python -m pytest tests/
```

### Validar Historial
```python
from src.core.validation import validar_historial_completo, DiccionarioDatosMaestros

# Validar historial completo
validacion = validar_historial_completo(df_historial)

if not validacion['valid']:
    print("Errores encontrados:")
    for error in validacion['errores']:
        print(f"  - {error}")
else:
    print("Validación exitosa!")

# Validar consistencia de datos maestros
datos_maestros = DiccionarioDatosMaestros()
datos_maestros.cargar_desde_historial(df_historial)
validacion_maestros = datos_maestros.validar_consistencia(df_historial)
```

### Usar el Diccionario de Datos
```python
from src.core.data_dictionary import DataDictionary

# Formatear campos compuestos
sku = DataDictionary.format_composite_field('SKU', '12345', 'Producto A')
# Resultado: '12345 - Producto A'

linea = DataDictionary.format_composite_field('LÍNEA', '0101', 'ARCHIVO')
# Resultado: '0101 - ARCHIVO'

cliente = DataDictionary.format_composite_field('CLIENTE', 'C001', 'Cliente X')
# Resultado: 'C001 - Cliente X'

# Filtrar DataFrames
df_filtrado = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
df_filtrado = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')

# Validar campos
result = DataDictionary.validate_composite_field('SKU', '12345', 'Producto A')
if not result['valid']:
    print("Errores:", result['errores'])
```

---

## 📝 Documentación Adicional

### Análisis Detallados

- **[ANALISIS_HISTORIAL_FUENTE_VERDAD.md](ANALISIS_HISTORIAL_FUENTE_VERDAD.md)** - Análisis del historial como fuente de verdad
- **[ANALISIS_INCONSISTENCIAS.md](ANALISIS_INCONSISTENCIAS.md)** - Análisis de inconsistencias en la interfaz
- **[ANALISIS_ID_PURO_PARETO_NC.md](ANALISIS_ID_PURO_PARETO_NC.md)** - Análisis de uso de ID puro en Pareto y NC
- **[VISTA_RAPIDA_REPORTES.md](VISTA_RAPIDA_REPORTES.md)** - Vista rápida de valores calculados y ordenamiento
- **[RESUMEN_ACCIONES.md](RESUMEN_ACCIONES.md)** - Resumen de acciones realizadas
- **[RESUMEN_FINAL_HISTORIAL.md](RESUMEN_FINAL_HISTORIAL.md)** - Resumen final del análisis del historial

---

## 🤝 Contribución

### Reglas de Código

1. **Mantener consistencia** en el uso de campos compuestos
2. **No cambiar** los diseños de Pareto y NC sin justificación clara
3. **Documentar** cualquier cambio en el diccionario de datos
4. **Validar** el historial antes de procesar
5. **Usar** el diccionario de datos para formatear campos compuestos

### Proceso de Cambios

1. **Analizar** el impacto del cambio propuesto
2. **Documentar** la justificación del cambio
3. **Actualizar** el diccionario de datos si es necesario
4. **Actualizar** el código para usar el nuevo formato
5. **Validar** que todos los reportes usen el formato correcto
6. **Probar** que los reportes se generen correctamente

### Pull Requests

Antes de enviar un PR, asegúrate de:
1. Actualizar la documentación relevante
2. Validar que los cambios no rompan la compatibilidad
3. Probar que todos los reportes funcionan correctamente
4. Actualizar las pruebas si es necesario

---

## 📞 Soporte

### Problemas Comunes

**Error: "No se hallaron datos válidos en el archivo"**
- Verifique que el archivo tenga las columnas críticas: ID_ARTICULO, NOM_ARTICULO, FECHA_ORIG, CANTIDAD, SOLES, TPO_DOC, SERIE_DOC, NRO_DOC

**Error: "No hay datos para Pareto"**
- Verifique que haya clientes en el historial
- Verifique que haya líneas en el historial
- Verifique que los filtros no estén excluyendo todos los datos

**Error: "El sistema no puede encontrar el archivo especificado"**
- Verifique que la ruta del archivo sea correcta
- Verifique que tenga permisos para escribir en el directorio de destino

---

## 📄 Licencia

Este proyecto es parte del ecosistema G360. Consulte la licencia del ecosistema para más detalles.

---

## 🎯 Objetivo del Proyecto

G360 NC Sustentor es una herramienta de **análisis y generación de notas de crédito** con las siguientes características:

1. **Automatización**: Procesa automáticamente requerimientos de NC y asigna facturas de sustento
2. **Validación**: Verifica la consistencia de datos antes de procesar
3. **Análisis**: Proporciona reportes consolidados para análisis de ventas
4. **Pareto**: Genera análisis 80/20 de clientes por vendedor
5. **Comparativo**: Permite comparación mes a mes con tendencias
6. **Calidad de Datos**: Valida y mejora la calidad de los datos del historial

---

## 🔄 Versionado

### Versión Actual: 1.0.0

**Cambios Recientes:**
- ✅ Agregado diccionario centralizado de datos (`src/core/data_dictionary.py`)
- ✅ Agregado módulo de validación del historial (`src/core/validation.py`)
- ✅ Corregida inconsistencia de tilde en `g360-nc-sustentor-portable/src/reports/consolidated.py`
- ✅ Documentadas decisiones de diseño importantes en README
- ✅ Actualizada función `format_id_name()` para aceptar parámetro opcional `field_name`

**Próximos Pasos:**
- Integrar validación en NCProcessor
- Actualizar reportes consolidados para usar el diccionario
- Crear reporte de calidad de datos
- Mejorar documentación de campos

---

## 📞 Contacto

Para reportar problemas o sugerencias, abra un issue en el repositorio del proyecto.

---

## 🎓 Notas Importantes

### ⚠️ Advertencias

1. **No cambiar** el diseño de Pareto (solo ID de líneas como encabezados) sin justificación clara
2. **No cambiar** el diseño de NC (columna SKU adicional) sin justificación clara
3. **No modificar** el diccionario de datos sin actualizar la documentación
4. **No eliminar** las funciones de validación del historial
5. **No cambiar** el formato de campos compuestos sin actualizar todos los reportes

### ✅ Buenas Prácticas

1. **Validar** siempre el historial antes de procesar
2. **Usar** el diccionario de datos para formatear campos compuestos
3. **Documentar** cualquier cambio en el código
4. **Probar** que los reportes se generan correctamente
5. **Mantener** la consistencia en el uso de campos compuestos

### 📚 Recursos de Aprendizaje

- **[ANALISIS_HISTORIAL_FUENTE_VERDAD.md](ANALISIS_HISTORIAL_FUENTE_VERDAD.md)** - Aprenda sobre la estructura del historial
- **[ANALISIS_INCONSISTENCIAS.md](ANALISIS_INCONSISTENCIAS.md)** - Aprenda sobre las inconsistencias identificadas
- **[ANALISIS_ID_PURO_PARETO_NC.md](ANALISIS_ID_PURO_PARETO_NC.md)** - Aprenda sobre el uso de ID puro en Pareto y NC
- **[VISTA_RAPIDA_REPORTES.md](VISTA_RAPA_REPORTES.md)** - Aprenda sobre cómo se calculan y ordenan los valores en reportes

---

## 🎯 Conclusión

G360 NC Sustentor es una herramienta robusta para **generación de notas de crédito con sustento** y **análisis de ventas consolidados**. El sistema incluye:

- ✅ **Validación automática** de datos del historial
- ✅ **Diccionario centralizado** de campos y formatos
- ✅ **Reportes consolidados** con múltiples agrupaciones
- ✅ **Reporte Pareto** con análisis 80/20
- ✅ **Reporte Comparativo** con tendencias
- ✅ **Documentación completa** de decisiones de diseño importantes

Las decisiones de diseño documentadas en este README son **intencionales** y **no deben cambiarse** sin justificación clara. El sistema está diseñado para ser **robusto**, **consistente** y **fácil de usar**.
