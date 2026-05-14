# Resumen de Acciones Realizadas

## Análisis Completado

Se ha completado el análisis detallado de los elementos de la interfaz para identificar inconsistencias en el uso de campos compuestos y valores.

## Inconsistencias Identificadas

### 1. **LÍNEA vs LINEA (Tilde)**
- **Problema:** Uso inconsistente de tilde en el nombre del campo
- **Impacto:** Puede causar errores al acceder a campos en diccionarios
- **Recomendación:** Estandarizar a `'LÍNEA'` (con tilde)

### 2. **CLIENTE vs CLIENTES (Plural)**
- **Problema:** Uso inconsistente de singular/plural
- **Impacto:** Confusión en el uso del campo
- **Recomendación:** Usar `'CLIENTE'` para individual, `'CLIENTES'` para listas

### 3. **FACTURA vs FACTURAS (Plural)**
- **Problema:** Uso inconsistente de singular/plural
- **Impacto:** Confusión en el uso del campo
- **Recomendación:** Usar `'FACTURA'` para individual, `'FACTURAS'` para listas

### 4. **Formato de Campos Compuestos**
- **Problema:** Inconsistencia en el formato "ID - NOMBRE"
- **Impacto:** Inconsistencia visual en reportes
- **Recomendación:** Estandarizar todos los campos compuestos

### 5. **Campos Opcionales No Manejados**
- **Problema:** Algunos campos son opcionales y no se manejan consistentemente
- **Impacto:** Información faltante en algunos reportes
- **Recomendación:** Crear diccionario centralizado

### 6. **Filtrado de "SIN ASIGNAR"**
- **Problema:** Código duplicado en múltiples funciones
- **Impacto:** Difícil de mantener
- **Recomendación:** Crear función centralizada

### 7. **Uso de `format_id_name`**
- **Problema:** Uso inconsistente de la función
- **Impacto:** Inconsistencia en el formato de salida
- **Recomendación:** Estandarizar el uso

## Archivos Creados

### 1. `ANALISIS_INCONSISTENCIAS.md`
Documento detallado con:
- Análisis de todas las inconsistencias identificadas
- Propuesta de diccionario centralizado
- Recomendaciones de implementación
- Prioridades de implementación

### 2. `src/core/data_dictionary.py`
Diccionario centralizado con:
- Definición de campos compuestos (SKU, LÍNEA, CLIENTE, VENDEDOR, SUCURSAL)
- Definición de campos de documento (FACTURA, PEDIDO)
- Definición de campos de lista (CLIENTES, FACTURAS)
- Valores a filtrar (SIN ASIGNAR, '', nan, None)
- Funciones para formatear, filtrar y validar campos

## Archivos Modificados

### 1. `src/core/utils.py`
- Agregado import de `DataDictionary`
- Actualizada función `format_id_name` para aceptar parámetro opcional `field_name`
- Mantenida compatibilidad con código existente

### 2. `src/__init__.py`
- Agregado export de `DataDictionary`

## Funciones del Diccionario Centralizado

### `format_composite_field(field_name, id_val, name_val)`
Formatea un campo compuesto según el diccionario.

**Ejemplo:**
```python
from src.core.data_dictionary import DataDictionary

# Formatear SKU
sku = DataDictionary.format_composite_field('SKU', '12345', 'Producto A')
# Resultado: '12345 - Producto A'

# Formatear LÍNEA
linea = DataDictionary.format_composite_field('LÍNEA', '0101', 'ARCHIVO')
# Resultado: '0101 - ARCHIVO'
```

### `should_filter_value(value)`
Determina si un valor debe ser filtrado.

**Ejemplo:**
```python
# Valores que deben ser filtrados
DataDictionary.should_filter_value('SIN ASIGNAR')  # True
DataDictionary.should_filter_value('')  # True
DataDictionary.should_filter_value('nan')  # True
DataDictionary.should_filter_value('Cliente A')  # False
```

### `filter_dataframe(df, field_name)`
Filtra un DataFrame eliminando valores no deseados.

**Ejemplo:**
```python
import pandas as pd
from src.core.data_dictionary import DataDictionary

df = pd.DataFrame({
    'NOM_CLIENTE': ['Cliente A', 'SIN ASIGNAR', 'Cliente B', '']
})

# Filtrar clientes
df_filtrado = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
# Resultado: Solo 'Cliente A' y 'Cliente B'
```

### `get_field_display_name(field_name)`
Obtiene el nombre para mostrar de un campo.

**Ejemplo:**
```python
DataDictionary.get_field_display_name('SKU')  # 'SKU'
DataDictionary.get_field_display_name('LÍNEA')  # 'Línea'
DataDictionary.get_field_display_name('CLIENTE')  # 'Cliente'
```

### `is_field_required(field_name)`
Determina si un campo es obligatorio.

**Ejemplo:**
```python
DataDictionary.is_field_required('SKU')  # True
DataDictionary.is_field_required('LÍNEA')  # True
DataDictionary.is_field_required('CLIENTE')  # True
DataDictionary.is_field_required('VENDEDOR')  # False
DataDictionary.is_field_required('SUCURSAL')  # False
```

### `get_field_id_name(field_name)`
Obtiene los nombres de campos ID y NOMBRE para un campo compuesto.

**Ejemplo:**
```python
DataDictionary.get_field_id_name('SKU')  # ('ID_ARTICULO', 'NOM_ARTICULO')
DataDictionary.get_field_id_name('LÍNEA')  # ('ID_LINEA', 'NOM_LINEA')
DataDictionary.get_field_id_name('CLIENTE')  # ('ID_CLIENTE', 'NOM_CLIENTE')
```

### `validate_composite_field(field_name, id_val, name_val)`
Valida un campo compuesto según el diccionario.

**Ejemplo:**
```python
# Validar campo completo
result = DataDictionary.validate_composite_field('SKU', '12345', 'Producto A')
# Resultado: {'valid': True, 'errors': [], 'warnings': []}

# Validar campo sin ID
result = DataDictionary.validate_composite_field('SKU', '', 'Producto A')
# Resultado: {'valid': False, 'errors': ['ID obligatorio para campo SKU'], 'warnings': []}

# Validar campo sin nombre
result = DataDictionary.validate_composite_field('SKU', '12345', '')
# Resultado: {'valid': True, 'errors': [], 'warnings': ['Nombre vacío para campo SKU']}
```

## Uso Recomendado

### Para código nuevo:
```python
from src.core.data_dictionary import DataDictionary

# Formatear campos compuestos
item['SKU'] = DataDictionary.format_composite_field('SKU', id_art, nom_art)
item['LÍNEA'] = DataDictionary.format_composite_field('LÍNEA', id_linea, nom_linea)
item['CLIENTE'] = DataDictionary.format_composite_field('CLIENTE', id_cliente, nom_cliente)

# Filtrar DataFrames
df = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
df = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')

# Validar campos
result = DataDictionary.validate_composite_field('SKU', id_art, nom_art)
if not result['valid']:
    # Manejar errores
    pass
```

### Para código existente (compatibilidad):
```python
from src.core.utils import format_id_name

# Uso existente sigue funcionando
item['SKU'] = format_id_name(id_art, nom_art)
item['LÍNEA'] = format_id_name(id_linea, nom_linea)
item['CLIENTE'] = format_id_name(id_cliente, nom_cliente)

# O con el nuevo parámetro opcional
item['SKU'] = format_id_name(id_art, nom_art, field_name='SKU')
item['LÍNEA'] = format_id_name(id_linea, nom_linea, field_name='LÍNEA')
item['CLIENTE'] = format_id_name(id_cliente, nom_cliente, field_name='CLIENTE')
```

## Próximos Pasos Recomendados

### Alta Prioridad:
1. Corregir inconsistencia `'LINEA'` vs `'LÍNEA'` en `g360-nc-sustentor-portable/src/reports/consolidated.py`
2. Estandarizar formato de campos compuestos en Pareto
3. Actualizar reportes consolidados para usar el diccionario

### Media Prioridad:
4. Actualizar filtrado de "SIN ASIGNAR" en todas las funciones
5. Estandarizar uso de `CLIENTE` vs `CLIENTES`
6. Agregar campos opcionales faltantes (ID_PEDIDO, SUCURSAL)

### Baja Prioridad:
7. Documentar todos los campos en el diccionario
8. Crear pruebas unitarias para el diccionario
9. Actualizar documentación de la API

## Beneficios Esperados

1. **Consistencia:** Todos los campos compuestos tendrán el mismo formato
2. **Mantenibilidad:** Código más fácil de mantener y actualizar
3. **Calidad:** Reportes más consistentes y profesionales
4. **Errores:** Reducción de errores causados por inconsistencias
5. **Documentación:** Documentación centralizada de todos los campos

## Notas Importantes

- El diccionario es **retrocompatible** con el código existente
- La función `format_id_name` mantiene su comportamiento original
- El parámetro `field_name` es opcional en `format_id_name`
- Se recomienda usar el diccionario en código nuevo
- Se recomienda migrar gradualmente el código existente
