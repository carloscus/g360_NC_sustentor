# Vista Rápida: Valores Calculados y Ordenamiento en Reportes

## Resumen Ejecutivo

Análisis rápido de cómo se calculan y ordenan los valores en los distintos reportes, verificando la congruencia con el diccionario centralizado y el reporte xlsx descargado.

---

## Tipos de Reportes

### 1. **NC Sustento**
- **Propósito:** Generar notas de crédito con sustento de facturas
- **Entrada:** Requerimientos (SKU, cantidad, porcentaje descuento)
- **Salida:** Excel con items procesados y documentos de sustento

### 2. **Reportes Consolidados**
- **Propósito:** Análisis de ventas por diferentes agrupaciones
- **Entrada:** Historial de compras
- **Salida:** Excel con datos consolidados por vendedor

---

## Reportes Consolidados - Estructura de Datos

### Por SKU (`ID_ARTICULO`)

**Cálculo de Valores:**
```python
# Agrupación: SKU + LÍNEA + CLIENTE
for (sku, id_l, nom_l, nom_c), df_grupo in df_vendedor.groupby(
    ["ID_ARTICULO", "ID_LINEA", "NOM_LINEA", "NOM_CLIENTE"]
):
    cant_total = df_grupo['CANTIDAD'].sum()
    soles_total = df_grupo['SOLES'].sum()
    
    # Formatear campos compuestos
    sku_display = format_id_name(sku, nom_articulo)
    linea_display = format_id_name(id_l, nom_l)
    cliente_display = format_id_name(id_c, nom_c)
```

**Ordenamiento:**
```python
# Ordenar por SKU (ID del SKU)
items_por_agrupacion.sort(key=lambda x: (x.get('SKU', '').split(' - ')[0] if x.get('SKU') else ''))
```

**Campos en Excel:**
```
N° | SKU | LÍNEA | CLIENTE | CANTIDAD | MONTO | FECHA ULT. | FACTURAS | PRECIOS
```

**Congruencia con Diccionario:**
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)

---

### Por Línea (`NOM_LINEA`)

**Cálculo de Valores:**
```python
# Agrupación: LÍNEA + SKU + CLIENTE
for (id_l, nom_l, sku, nom_c), df_grupo in df_vendedor.groupby(
    ["ID_LINEA", "NOM_LINEA", "ID_ARTICULO", "NOM_CLIENTE"]
):
    cant_total = df_grupo['CANTIDAD'].sum()
    soles_total = df_grupo['SOLES'].sum()
    
    # Formatear campos compuestos
    linea_display = format_id_name(id_l, nom_l)
    sku_display = format_id_name(sku, nom_articulo)
    cliente_display = format_id_name(id_c, nom_c)
```

**Ordenamiento:**
```python
# Ordenar por LÍNEA (ID de la línea)
items_por_agrupacion.sort(key=lambda x: (x.get('ID_LINEA', '')))
```

**Campos en Excel:**
```
N° | LÍNEA | SKU | CLIENTE | CANTIDAD | MONTO | FECHA ULT. | FACTURAS | PRECIOS
```

**Congruencia con Diccionario:**
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)

---

### Por Cliente (`ID_CLIENTE`)

**Cálculo de Valores:**
```python
# Agrupación: CLIENTE + LÍNEA + SKU
for (id_c, nom_c, id_l, nom_l, sku), df_grupo in df_vendedor.groupby(
    ["ID_CLIENTE", "NOM_CLIENTE", "ID_LINEA", "NOM_LINEA", "ID_ARTICULO"]
):
    cant_total = df_grupo['CANTIDAD'].sum()
    soles_total = df_grupo['SOLES'].sum()
    
    # Formatear campos compuestos
    cliente_display = format_id_name(id_c, nom_c)
    linea_display = format_id_name(id_l, nom_l)
    sku_display = format_id_name(sku, nom_articulo)
```

**Ordenamiento:**
```python
# Ordenar por CLIENTE (ID del cliente)
items_por_agrupacion.sort(key=lambda x: (x.get('ID_CLIENTE', '')))
```

**Campos en Excel:**
```
N° | CLIENTE | LÍNEA | SKU | CANTIDAD | MONTO | FECHA ULT. | FACTURAS | PRECIOS
```

**Congruencia con Diccionario:**
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)

---

### Por Mes (`PERIODO_MES`)

**Cálculo de Valores:**
```python
# Agrupación: PERIODO + SKU + LÍNEA + CLIENTE
for (periodo_val, sku, id_l, nom_l, nom_c), df_grupo in df_vendedor.groupby(
    ["PERIODO", "ID_ARTICULO", "ID_LINEA", "NOM_LINEA", "NOM_CLIENTE"]
):
    cant_total = df_grupo['CANTIDAD'].sum()
    soles_total = df_grupo['SOLES'].sum()
    
    # Formatear campos compuestos
    sku_display = format_id_name(sku, nom_articulo)
    linea_display = format_id_name(id_l, nom_l)
    cliente_display = format_id_name(id_c, nom_c)
```

**Ordenamiento:**
```python
# Ordenar por PERIODO (fecha del mes)
items_por_agrupacion.sort(key=lambda x: x.get('FECHA') or '')
```

**Campos en Excel:**
```
N° | PERIODO | SKU | LÍNEA | CLIENTE | CANTIDAD | MONTO | FECHA ULT. | FACTURAS | PRECIOS
```

**Congruencia con Diccionario:**
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)

---

### Por Factura (`FACTURA`)

**Cálculo de Valores:**
```python
# Agrupación: DOCUMENTO (una fila por SKU de cada factura)
for doc_key, df_doc in df_vendedor.groupby(["TPO_DOC", "SERIE_DOC", "NRO_DOC"]):
    # Formatear documento
    num_factura = format_doc_id(tpo, serie, nro)
    
    # Una fila por cada SKU
    for sku, df_sku in df_doc.groupby("ID_ARTICULO"):
        cant_sku = df_sku['CANTIDAD'].sum()
        soles_sku = df_sku['SOLES'].sum()
        pu = soles_sku / cant_sku if cant_sku > 0 else 0
        
        # Formatear campos compuestos
        sku_display = format_id_name(sku, nom_art)
        linea_display = format_id_name(id_l, nom_l)
        cliente_display = format_id_name(id_c, nom_c)
```

**Ordenamiento:**
```python
# Ordenar por FECHA (fecha de la factura)
items_por_agrupacion.sort(key=lambda x: x.get('FECHA') or '')
```

**Campos en Excel:**
```
N° | FACTURA | FECHA | CLIENTE | LÍNEA | SKU | CANTIDAD | PRECIO | MONTO
```

**Congruencia con Diccionario:**
- ✅ `FACTURA` usa formato "FXXX-YYYYYY" (format_doc_id)
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)

---

### Pareto Cliente (`PARETO_CLIENTE`)

**Cálculo de Valores:**
```python
# Agrupación: CLIENTE (una fila por cliente)
# Columnas hacia la derecha por LÍNEA

# Agrupar por CLIENTE + LÍNEA
agg_cliente_linea = df.groupby(['ID_CLIENTE', 'NOM_CLIENTE', 'ID_LINEA', 'NOM_LINEA']).agg({
    'SOLES': 'sum',
    'CANTIDAD': 'sum'
}).reset_index()

# Agrupar por cliente para obtener totales
agg_cliente = agg_cliente_linea.groupby(['ID_CLIENTE', 'NOM_CLIENTE']).agg({
    'SOLES': 'sum',
    'CANTIDAD': 'sum'
}).reset_index()

# Calcular porcentajes
total_global = agg_cliente['SOLES'].sum()
agg_cliente['PCT_GLOBAL'] = agg_cliente['SOLES'] / total_global * 100
agg_cliente['PCT_ACUMULADO'] = agg_cliente['PCT_GLOBAL'].cumsum()

# Categoría Pareto
agg_cliente['CATEGORIA'] = agg_cliente['PCT_ACUMULADO'].apply(
    lambda x: 'VITAL (≤80%)' if x <= 80 else 'VITAL (100%)' if x == 100 else 'TRIVIAL (>80%)'
)

# Formatear cliente
cliente_display = format_id_name(id_c, nom_c)

# Para cada línea, calcular cantidades y montos
for linea in lineas:
    lid = linea['ID_LINEA']
    cliente_data[f'L{lid}_CANT'] = int(cant)
    cliente_data[f'L{lid}_MONTO'] = round(float(monto), 2)
    cliente_data[f'L{lid}_PCT'] = round(float(monto) / monto_cliente * 100, 2)
```

**Ordenamiento:**
```python
# Ordenar por MONTO_TOTAL (mayor a menor)
agg_cliente = agg_cliente.sort_values('MONTO_TOTAL', ascending=False)
```

**Campos en Excel:**
```
CLIENTE | TOTAL | % | CAT | [L01-CANT | L01-MONTO | L01-%] | [L02-CANT | L02-MONTO | L02-%] | ...
```

**Congruencia con Diccionario:**
- ⚠️ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name) ✅
- ⚠️ `LINEAS` solo usa `ID_LINEA` (sin nombre) ❌ **INCONSISTENCIA**
- ⚠️ Headers de línea solo muestran ID (0101, 0156, etc.) ❌ **INCONSISTENCIA**

---

### Comparativo Mes a Mes

**Cálculo de Valores:**
```python
# Agrupación: SKU/LÍNEA/CLIENTE + PERIODO_MES
# Columnas dinámicas: CANT, MONTO, FACTURAS por cada mes

# Agrupar por vendedor y agrupación
for vendedor_id, df_vendedor in df.groupby("ID_VENDEDOR"):
    # Agrupar por agrupación principal + PERIODO_MES
    agg = df_vendedor.groupby([grupo_principal, 'PERIODO_MES']).agg({
        'SOLES': 'sum',
        'CANTIDAD': 'sum'
    }).reset_index()
    
    # Para cada mes, calcular valores
    for mes in meses_mostrar:
        df_mes = df_grupo[df_grupo['PERIODO_MES'] == mes]
        item[f'{mes}-CANT'] = int(df_mes['CANTIDAD'].sum())
        item[f'{mes}-MONTO'] = round(df_mes['SOLES'].sum(), 2)
        item[f'{mes}-FACTURAS'] = df_mes.groupby(['TPO_DOC', 'SERIE_DOC', 'NRO_DOC']).ngroups
    
    # Calcular tendencia (mes actual vs anterior)
    monto_actual = item.get(f'{meses_mostrar[1]}-MONTO', 0)
    monto_anterior = item.get(f'{meses_mostrar[0]}-MONTO', 0)
    dif = monto_actual - monto_anterior
    item['DIF_SOLES'] = round(dif, 2)
    item['DIF_PCT'] = round(dif / monto_anterior * 100, 2) if monto_anterior != 0 else 0.0
    item['TENDENCIA'] = '🔺' if dif > 0 else ('🔻' if dif < 0 else '➡️')
```

**Ordenamiento:**
```python
# Ordenar según tipo de agrupación
if agrupacion == "ID_CLIENTE":
    # Ordenar por CLIENTE
    items.sort(key=lambda x: (x.get('ID_CLIENTE', '')))
elif agrupacion == "NOM_LINEA":
    # Ordenar por LÍNEA
    items.sort(key=lambda x: (x.get('ID_LINEA', '')))
else:
    # Ordenar por SKU
    items.sort(key=lambda x: (x.get('SKU', '').split(' - ')[0] if x.get('SKU') else ''))
```

**Campos en Excel:**
```
N° | [AGRUPACIÓN] | [MES1-CANT] | [MES1-MONTO] | [MES1-FACT] | [MES2-CANT] | [MES2-MONTO] | [MES2-FACT] | FECHA ULT | DIF_SOLES | DIF_PCT | TENDENCIA
```

**Congruencia con Diccionario:**
- ✅ `SKU` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `LÍNEA` usa formato "ID - NOMBRE" (format_id_name)
- ✅ `CLIENTE` usa formato "ID - NOMBRE" (format_id_name)

---

## Tabla de Congruencia con Diccionario

| Reporte | Campo | Formato Actual | Formato Diccionario | Congruencia |
|---------|-------|---------------|---------------------|-------------|
| Por SKU | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por SKU | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por SKU | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Línea | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Línea | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Línea | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Cliente | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Cliente | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Cliente | CLIENTE | "ID - NOMBRE" | "ID -NOMBRE" | ✅ |
| Por Mes | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Mes | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Mes | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Factura | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Factura | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Por Factura | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| **Pareto** | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| **Pareto** | **LÍNEA** | **"ID" (solo ID)** | **"ID - NOMBRE"** | ❌ |
| Comparativo | SKU | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Comparativo | LÍNEA | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |
| Comparativo | CLIENTE | "ID - NOMBRE" | "ID - NOMBRE" | ✅ |

---

## Tabla de Ordenamiento

| Reporte | Criterio de Ordenamiento | Dirección | Campo Usado |
|---------|---------------------------|-----------|-------------|
| Por SKU | ID del SKU | Ascendente | `SKU.split(' - ')[0]` |
| Por Línea | ID de la Línea | Ascendente | `ID_LINEA` |
| Por Cliente | ID del Cliente | Ascendente | `ID_CLIENTE` |
| Por Mes | Fecha del mes | Ascendente | `FECHA` |
| Por Factura | Fecha de la factura | Ascendente | `FECHA` |
| **Pareto** | **MONTO_TOTAL** | **Descendente** | `MONTO_TOTAL` |
| Comparativo | Según agrupación | Ascendente | `ID_CLIENTE` / `ID_LINEA` / `SKU` |

---

## Inconsistencias Identificadas

### 1. **Pareto - Líneas**

**Problema:** En el reporte Pareto, las líneas solo muestran el ID (0101, 0156, etc.) sin el nombre.

**Código Actual:**
```python
# En generar_pareto_completo
lineas_list = [{'ID_LINEA': str(row['ID_LINEA'])} for _, row in lineas_unicas.iterrows()]

# En _escribir_pareto_simple
for linea in lineas:
    header_text = linea['ID_LINEA']  # Solo ID, sin nombre
```

**Debería ser:**
```python
# En generar_pareto_completo
lineas_list = [
    {
        'ID_LINEA': str(row['ID_LINEA']),
        'NOM_LINEA': row['NOM_LINEA'],
        'DISPLAY': DataDictionary.format_composite_field('LÍNEA', row['ID_LINEA'], row['NOM_LINEA'])
    }
    for _, row in lineas_unicas.iterrows()
]

# En _escribir_pareto_simple
for linea in lineas:
    header_text = linea.get('DISPLAY', linea['ID_LINEA'])  # Usar formato completo
```

---

## Valores Calculados por Reporte

### Por SKU / Línea / Cliente / Mes / Factura

| Campo | Cálculo | Fórmula |
|-------|---------|---------|
| `CANTIDAD` | Suma de cantidades | `df_grupo['CANTIDAD'].sum()` |
| `MONTO` | Suma de montos | `df_grupo['SOLES'].sum()` |
| `PRECIO` | Precio unitario | `MONTO / CANTIDAD` |
| `FECHA_ULT` | Fecha más reciente | `df_grupo['FECHA_ORIG'].max()` |
| `FECHA_MIN` | Fecha más antigua | `df_grupo['FECHA_ORIG'].min()` |
| `FACTURAS` | Lista de documentos | `", ".join(facturas_ordenadas)` |
| `PRECIOS` | Lista de precios | `", ".join(precios_lista)` |
| `PEDIDOS` | Lista de pedidos | `", ".join(pedidos_lista)` |

### Pareto Cliente

| Campo | Cálculo | Fórmula |
|-------|---------|---------|
| `MONTO_TOTAL` | Suma de montos por cliente | `agg_cliente['SOLES'].sum()` |
| `PCT_GLOBAL` | Porcentaje del total | `MONTO_TOTAL / total_global * 100` |
| `PCT_ACUMULADO` | Porcentaje acumulado | `PCT_GLOBAL.cumsum()` |
| `CATEGORIA` | Categoría Pareto | `VITAL (≤80%)` / `VITAL (100%)` / `TRIVIAL (>80%)` |
| `L{ID}_CANT` | Cantidad por línea | `sum(cantidades por línea)` |
| `L{ID}_MONTO` | Monto por línea | `sum(montos por línea)` |
| `L{ID}_PCT` | Porcentaje por línea | `monto / monto_cliente * 100` |

### Comparativo Mes a Mes

| Campo | Cálculo | Fórmula |
|-------|---------|---------|
| `{MES}-CANT` | Cantidad por mes | `df_mes['CANTIDAD'].sum()` |
| `{MES}-MONTO` | Monto por mes | `df_mes['SOLES'].sum()` |
| `{MES}-FACTURAS` | Facturas por mes | `df_mes.groupby(['TPO_DOC', 'SERIE_DOC', 'NRO_DOC']).ngroups` |
| `DIF_SOLES` | Diferencia de montos | `monto_actual - monto_anterior` |
| `DIF_PCT` | Diferencia porcentual | `DIF_SOLES / monto_anterior * 100` |
| `TENDENCIA` | Tendencia | `🔺` (sube) / `🔻` (baja) / `➡️` (estable) |

---

## Recomendaciones de Corrección

### 1. **Corregir Pareto - Líneas**

**Archivo:** `src/reports/consolidated.py` - `generar_pareto_completo()`

**Cambio:**
```python
# Antes
lineas_list = [{'ID_LINEA': str(row['ID_LINEA'])} for _, row in lineas_unicas.iterrows()]

# Después
lineas_list = [
    {
        'ID_LINEA': str(row['ID_LINEA']),
        'NOM_LINEA': row['NOM_LINEA'],
        'DISPLAY': DataDictionary.format_composite_field('LÍNEA', row['ID_LINEA'], row['NOM_LINEA'])
    }
    for _, row in lineas_unicas.iterrows()
]
```

**Archivo:** `src/excel/generator.py` - `_escribir_pareto_simple()`

**Cambio:**
```python
# Antes
for linea in lineas:
    header_text = linea['ID_LINEA']

# Después
for linea in lineas:
    header_text = linea.get('DISPLAY', linea['ID_LINEA'])
```

---

## Conclusión

### Congruencia General

✅ **Alta congruencia** en la mayoría de reportes:
- Todos los campos compuestos usan formato "ID - NOMBRE"
- El ordenamiento es consistente con el criterio de agrupación
- Los cálculos son correctos y consistentes

❌ **Inconsistencia identificada** en Pareto:
- Las líneas solo muestran ID sin nombre
- Los headers de línea solo muestran ID (0101, 0156, etc.)

### Próxima Acción

Corregir la inconsistencia en Pareto para que las líneas muestren el formato completo "ID - NOMBRE", manteniendo congruencia con el diccionario centralizado y los demás reportes.
