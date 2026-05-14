# Análisis de Inconsistencias en Elementos de la Interfaz

## Resumen Ejecutivo

Se identificaron múltiples inconsistencias en el uso de campos compuestos y valores a través del códigobase. Estas inconsistencias pueden causar problemas en la generación de reportes, especialmente en NC Sustento y Pareto.

## Inconsistencias Identificadas

### 1. **LÍNEA vs LINEA (Tilde)**

**Problema:** Uso inconsistente de tilde en el nombre del campo.

- **Con tilde (`'LÍNEA'`)**: Usado en la mayoría de los reportes consolidados
  - `src/reports/consolidated.py`: 9 ocurrencias
  - `src/excel/generator.py`: 14 ocurrencias
  - `g360-nc-sustentor-portable/src/reports/consolidated.py`: 7 ocurrencias

- **Sin tilde (`'LINEA'`)**: Usado en algunos lugares específicos
  - `g360-nc-sustentor-portable/src/reports/consolidated.py`: 3 ocurrencias

**Impacto:** Puede causar errores al acceder a campos en diccionarios, especialmente en reportes comparativos.

**Recomendación:** Estandarizar a `'LÍNEA'` (con tilde) en todo el código.

---

### 2. **CLIENTE vs CLIENTES (Plural)**

**Problema:** Uso inconsistente de singular/plural.

- **Singular (`'CLIENTE'`)**: Usado para representar un cliente individual
  - `src/reports/consolidated.py`: 5 ocurrencias
  - `src/excel/generator.py`: 2 ocurrencias
  - `g360-nc-sustentor-portable/src/reports/consolidated.py`: 5 ocurrencias

- **Plural (`'CLIENTES'`)**: Usado para representar múltiples clientes o listas
  - `src/reports/consolidated.py`: 5 ocurrencias (para listas de clientes)
  - `src/excel/generator.py`: 8 ocurrencias
  - `g360-nc-sustentor-portable/src/reports/consolidated.py`: 5 ocurrencias

**Impacto:** Confusión en el uso del campo, especialmente en reportes que muestran múltiples clientes.

**Recomendación:** 
- Usar `'CLIENTE'` para representar un cliente individual (formato "ID - NOMBRE")
- Usar `'CLIENTES'` solo para listas o conteos de múltiples clientes

---

### 3. **FACTURA vs FACTURAS (Plural)**

**Problema:** Uso inconsistente de singular/plural.

- **Singular (`'FACTURA'`)**: Usado para representar una factura individual
  - `src/core/processor.py`: 2 ocurrencias
  - `g360-nc-sustentor-portable/src/core/processor.py`: 2 ocurrencias

- **Plural (`'FACTURAS'`)**: Usado para representar múltiples facturas o listas
  - `src/reports/consolidated.py`: 7 ocurrencias
  - `src/excel/generator.py`: 14 ocurrencias
  - `g360-nc-sustentor-portable/src/reports/consolidated.py`: 6 ocurrencias

**Impacto:** Confusión en el uso del campo, especialmente en reportes que muestran múltiples facturas.

**Recomendación:**
- Usar `'FACTURA'` para representar una factura individual (formato "FXXX-YYYYYY")
- Usar `'FACTURAS'` solo para listas o conteos de múltiples facturas

---

### 4. **Formato de Campos Compuestos**

**Problema:** Inconsistencia en el formato de campos compuestos "ID - NOMBRE".

**Campos afectados:**
- `SKU`: Debería ser `"ID_ARTICULO - NOM_ARTICULO"`
- `LÍNEA`: Debería ser `"ID_LINEA - NOM_LINEA"`
- `CLIENTE`: Debería ser `"ID_CLIENTE - NOM_CLIENTE"`
- `VENDEDOR`: Debería ser `"ID_VENDEDOR - NOM_VENDEDOR"`

**Inconsistencias encontradas:**

1. **En Pareto (`generar_pareto_completo`):**
   - `LINEAS`: Solo usa `ID_LINEA` (sin nombre)
   - `CLIENTES`: Usa formato completo `"ID - NOMBRE"`

2. **En Reportes Consolidados:**
   - `SKU`: Usa formato completo `"ID - NOMBRE"`
   - `LÍNEA`: Usa formato completo `"ID - NOMBRE"`
   - `CLIENTE`: Usa formato completo `"ID - NOMBRE"`

3. **En NC Sustento:**
   - `SKU`: Usa formato completo `"ID - NOMBRE"`
   - `LÍNEA`: Usa formato completo `"ID - NOMBRE"`
   - `CLIENTE`: Usa formato completo `"ID - NOMBRE"`

**Impacto:** Inconsistencia visual en reportes, especialmente en Pareto donde las líneas solo muestran ID.

**Recomendación:** Estandarizar todos los campos compuestos a usar formato `"ID - NOMBRE"`.

---

### 5. **Campos Opcionales No Manejados**

**Problema:** Algunos campos son opcionales y no se manejan consistentemente.

**Campos opcionales identificados:**
- `ID_PEDIDO`: Solo se incluye si existe en el historial
- `NOM_SUCURSAL`: Solo se incluye si existe en el historial
- `ID_VENDEDOR`: Puede estar vacío o ser "SIN ASIGNAR"
- `NOM_VENDEDOR`: Puede estar vacío o ser "SIN ASIGNAR"

**Inconsistencias:**

1. **ID_PEDIDO:**
   - En `generar_consolidado`: Se incluye como campo `'PEDIDOS'` (lista)
   - En `generar_pareto_completo`: No se incluye
   - En NC Sustento: No se incluye

2. **SUCURSAL:**
   - En `generar_pareto_sucursales`: Se incluye como campo `'SUCURSAL'`
   - En otros reportes: No se incluye

3. **VENDEDOR:**
   - En `generar_pareto_por_vendedor`: Se incluye como campo `'VENDEDOR'`
   - En otros reportes: No se incluye

**Impacto:** Información faltante en algunos reportes, inconsistencia en la disponibilidad de datos.

**Recomendación:** Crear un diccionario centralizado que defina qué campos son obligatorios y cuáles son opcionales.

---

### 6. **Filtrado de "SIN ASIGNAR"**

**Problema:** El filtrado de valores "SIN ASIGNAR" no es consistente.

**Inconsistencias:**

1. **En `generar_pareto_por_vendedor`:**
   ```python
   if 'NOM_CLIENTE' in df.columns:
       df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
   if 'NOM_VENDEDOR' in df.columns:
       df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
   ```

2. **En `generar_pareto_sucursales`:**
   ```python
   if 'NOM_CLIENTE' in df.columns:
       df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
   if 'NOM_VENDEDOR' in df.columns:
       df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
   ```

3. **En `generar_pareto_completo`:**
   ```python
   if 'NOM_CLIENTE' in df.columns:
       df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
   if 'NOM_VENDEDOR' in df.columns:
       df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
   ```

4. **En `generar_pareto_cliente_linea`:**
   ```python
   if 'NOM_CLIENTE' in df.columns:
       df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
   if 'NOM_VENDEDOR' in df.columns:
       df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]
   ```

**Impacto:** Código duplicado, difícil de mantener.

**Recomendación:** Crear una función centralizada de filtrado.

---

### 7. **Uso de `format_id_name`**

**Problema:** La función `format_id_name` se usa de manera inconsistente.

**Uso actual:**
```python
def format_id_name(id_val, name_val) -> str:
    """
    Centraliza el formato visual 'ID - NOMBRE' utilizado en todo el ecosistema G360.
    Si falta un valor, retorna el disponible. Si ambos faltan, retorna cadena vacía.
    Preserva la longitud original del ID.
    """
    cid = _clean_value(id_val)
    cnm = _clean_value(name_val)
    
    if cid and cnm:
        return f"{cid} - {cnm}"
    return cnm or cid
```

**Inconsistencias:**

1. **En algunos lugares se usa para formatear:**
   - `SKU`: `format_id_name(id_articulo, nom_articulo)`
   - `LÍNEA`: `format_id_name(id_linea, nom_linea)`
   - `CLIENTE`: `format_id_name(id_cliente, nom_cliente)`
   - `VENDEDOR`: `format_id_name(id_vendedor, nom_vendedor)`

2. **En otros lugares se usa directamente:**
   - `NOM_LINEA`: Se usa directamente sin formatear
   - `NOM_CLIENTE`: Se usa directamente sin formatear

**Impacto:** Inconsistencia en el formato de salida.

**Recomendación:** Estandarizar el uso de `format_id_name` para todos los campos compuestos.

---

## Propuesta de Diccionario Centralizado

### Estructura del Diccionario

```python
# src/core/data_dictionary.py

class DataDictionary:
    """
    Diccionario centralizado de campos y formatos para evitar inconsistencias.
    Define la estructura estándar de todos los campos usados en reportes.
    """
    
    # Campos compuestos (formato "ID - NOMBRE")
    COMPOSITE_FIELDS = {
        'SKU': {
            'id_field': 'ID_ARTICULO',
            'name_field': 'NOM_ARTICULO',
            'display_name': 'SKU',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'LÍNEA': {
            'id_field': 'ID_LINEA',
            'name_field': 'NOM_LINEA',
            'display_name': 'Línea',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'CLIENTE': {
            'id_field': 'ID_CLIENTE',
            'name_field': 'NOM_CLIENTE',
            'display_name': 'Cliente',
            'format': 'ID - NOMBRE',
            'required': True,
        },
        'VENDEDOR': {
            'id_field': 'ID_VENDEDOR',
            'name_field': 'NOM_VENDEDOR',
            'display_name': 'Vendedor',
            'format': 'ID - NOMBRE',
            'required': False,
        },
        'SUCURSAL': {
            'id_field': 'COD_SUCURSAL',
            'name_field': 'NOM_SUCURSAL',
            'display_name': 'Sucursal',
            'format': 'ID - NOMBRE',
            'required': False,
        },
    }
    
    # Campos de documento
    DOCUMENT_FIELDS = {
        'FACTURA': {
            'format': 'TIPO + SERIE - NUMERO',
            'example': 'F012-0457996',
            'required': True,
        },
        'PEDIDO': {
            'format': 'ID_PEDIDO',
            'example': '12345',
            'required': False,
        },
    }
    
    # Campos de lista (plural)
    LIST_FIELDS = {
        'CLIENTES': {
            'singular': 'CLIENTE',
            'description': 'Lista de clientes',
        },
        'FACTURAS': {
            'singular': 'FACTURA',
            'description': 'Lista de facturas',
        },
    }
    
    # Valores a filtrar
    FILTER_VALUES = {
        'SIN ASIGNAR': True,
        '': True,
        'nan': True,
        'None': True,
    }
    
    @staticmethod
    def format_composite_field(field_name: str, id_val: str, name_val: str) -> str:
        """
        Formatea un campo compuesto según el diccionario.
        
        Args:
            field_name: Nombre del campo (ej: 'SKU', 'LÍNEA', 'CLIENTE')
            id_val: Valor del ID
            name_val: Valor del nombre
            
        Returns:
            String formateado o valor disponible
        """
        if field_name not in DataDictionary.COMPOSITE_FIELDS:
            return str(name_val or id_val or '')
        
        field_def = DataDictionary.COMPOSITE_FIELDS[field_name]
        
        if field_def['format'] == 'ID - NOMBRE':
            cid = str(id_val).strip() if id_val else ''
            cnm = str(name_val).strip() if name_val else ''
            
            if cid and cnm:
                return f"{cid} - {cnm}"
            return cnm or cid
        
        return str(name_val or id_val or '')
    
    @staticmethod
    def should_filter_value(value: str) -> bool:
        """
        Determina si un valor debe ser filtrado.
        
        Args:
            value: Valor a evaluar
            
        Returns:
            True si debe ser filtrado, False en caso contrario
        """
        if value is None:
            return True
        
        val_str = str(value).strip().upper()
        return val_str in DataDictionary.FILTER_VALUES
    
    @staticmethod
    def filter_dataframe(df: pd.DataFrame, field_name: str) -> pd.DataFrame:
        """
        Filtra un DataFrame eliminando valores no deseados.
        
        Args:
            df: DataFrame a filtrar
            field_name: Nombre del campo a filtrar
            
        Returns:
            DataFrame filtrado
        """
        if field_name not in df.columns:
            return df
        
        return df[~df[field_name].apply(DataDictionary.should_filter_value)]
    
    @staticmethod
    def get_field_display_name(field_name: str) -> str:
        """
        Obtiene el nombre para mostrar de un campo.
        
        Args:
            field_name: Nombre del campo
            
        Returns:
            Nombre para mostrar
        """
        if field_name in DataDictionary.COMPOSITE_FIELDS:
            return DataDictionary.COMPOSITE_FIELDS[field_name]['display_name']
        
        return field_name
    
    @staticmethod
    def is_field_required(field_name: str) -> bool:
        """
        Determina si un campo es obligatorio.
        
        Args:
            field_name: Nombre del campo
            
        Returns:
            True si es obligatorio, False en caso contrario
        """
        if field_name in DataDictionary.COMPOSITE_FIELDS:
            return DataDictionary.COMPOSITE_FIELDS[field_name]['required']
        
        return False
```

---

## Recomendaciones de Implementación

### 1. **Crear el diccionario centralizado**

Crear el archivo `src/core/data_dictionary.py` con la estructura propuesta.

### 2. **Actualizar `src/core/utils.py`**

Modificar `format_id_name` para usar el diccionario:

```python
from src.core.data_dictionary import DataDictionary

def format_id_name(id_val, name_val, field_name: str = None) -> str:
    """
    Centraliza el formato visual 'ID - NOMBRE' utilizando el diccionario de datos.
    
    Args:
        id_val: Valor del ID
        name_val: Valor del nombre
        field_name: Nombre del campo (opcional, para validación)
        
    Returns:
        String formateado o valor disponible
    """
    if field_name:
        return DataDictionary.format_composite_field(field_name, id_val, name_val)
    
    # Comportamiento original para compatibilidad
    cid = _clean_value(id_val)
    cnm = _clean_value(name_val)
    
    if cid and cnm:
        return f"{cid} - {cnm}"
    return cnm or cid
```

### 3. **Actualizar reportes consolidados**

Reemplazar el uso directo de `format_id_name` con el diccionario:

```python
# Antes
item['SKU'] = format_id_name(id_art, nom_art)
item['LÍNEA'] = format_id_name(id_l, nom_l)
item['CLIENTE'] = format_id_name(id_c, nom_c)

# Después
item['SKU'] = DataDictionary.format_composite_field('SKU', id_art, nom_art)
item['LÍNEA'] = DataDictionary.format_composite_field('LÍNEA', id_l, nom_l)
item['CLIENTE'] = DataDictionary.format_composite_field('CLIENTE', id_c, nom_c)
```

### 4. **Actualizar filtrado de "SIN ASIGNAR"**

Reemplazar el filtrado duplicado con el diccionario:

```python
# Antes
if 'NOM_CLIENTE' in df.columns:
    df = df[df['NOM_CLIENTE'] != "SIN ASIGNAR"]
if 'NOM_VENDEDOR' in df.columns:
    df = df[df['NOM_VENDEDOR'] != "SIN ASIGNAR"]

# Después
df = DataDictionary.filter_dataframe(df, 'NOM_CLIENTE')
df = DataDictionary.filter_dataframe(df, 'NOM_VENDEDOR')
```

### 5. **Corregir inconsistencias de tilde**

Reemplazar `'LINEA'` con `'LÍNEA'` en todo el código:

```python
# Antes
item['LINEA'] = format_id_name(id_linea, nom_linea)

# Después
item['LÍNEA'] = DataDictionary.format_composite_field('LÍNEA', id_linea, nom_linea)
```

### 6. **Estandarizar campos en Pareto**

Actualizar `generar_pareto_completo` para usar formato completo:

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

---

## Prioridades de Implementación

### Alta Prioridad
1. Crear diccionario centralizado (`src/core/data_dictionary.py`)
2. Corregir inconsistencia `'LINEA'` vs `'LÍNEA'`
3. Estandarizar formato de campos compuestos en Pareto

### Media Prioridad
4. Actualizar `format_id_name` para usar el diccionario
5. Actualizar filtrado de "SIN ASIGNAR"
6. Estandarizar uso de `CLIENTE` vs `CLIENTES`

### Baja Prioridad
7. Agregar campos opcionales faltantes (ID_PEDIDO, SUCURSAL)
8. Documentar todos los campos en el diccionario
9. Crear pruebas unitarias para el diccionario

---

## Conclusión

La implementación de un diccionario centralizado de datos permitirá:

1. **Evitar inconsistencias** en el formato de campos compuestos
2. **Estandarizar** el uso de campos a través de todo el códigobase
3. **Facilitar el mantenimiento** al tener una única fuente de verdad
4. **Mejorar la calidad** de los reportes generados
5. **Reducir errores** causados por inconsistencias en nombres de campos

La implementación debe hacerse de manera incremental, priorizando las correcciones de alta prioridad y asegurando la compatibilidad con el código existente.
