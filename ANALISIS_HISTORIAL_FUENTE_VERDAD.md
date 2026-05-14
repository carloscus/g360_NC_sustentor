# Análisis del Historial como Fuente de Verdad

## Estructura Oficial del Historial

### Columnas Definidas en `NCProcessor.COLUMNAS_HISTORIAL`

```python
COLUMNAS_HISTORIAL = (
    "ANHO", "MES", "DOC_CLIENTE", "ID_CLIENTE", "NOM_CLIENTE", 
    "ID_LOCALIDAD_UBIGEO", "NOM_DEPARTAMENTO", "NOM_PROVINCIA", "NOM_DISTRITO",
    "ID_LINEA", "NOM_LINEA", "ID_GRUPO", "NOM_GRUPO", "ID_TIPO", "NOM_TIPO",
    "ID_FAMILIA", "NOM_FAMILIA", "ESTADO_LINEA",
    "ID_ARTICULO", "NOM_ARTICULO", # Columnas críticas para el procesamiento
    "ID_VENDEDOR", "NOM_VENDEDOR", "CANAL DE DISTRIBUCION",
    "COD_SUCURSAL", "NOM_SUCURSAL",
    "TPO_DOC", "SERIE_DOC", "NRO_DOC", "ORD_COMPRA", "ID_GUIA",
    "FECHA_ORIG", "REFERENCIA", "FECHA_REF", "MONEDA",
    "CANTIDAD", "SOLES", "DOLARES", "NOM_CONDICION_PAGO", "ID_PEDIDO",
    "FECHA_VENC", "DIVISION", "PRECIO_UNID"
)
```

## Análisis por Categoría

### 1. **Campos Temporales**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `ANHO` | Numérico | Año de la transacción | No | Puede derivarse de FECHA_ORIG |
| `MES` | Numérico | Mes de la transacción | No | Puede derivarse de FECHA_ORIG |
| `FECHA_ORIG` | Fecha | Fecha de origen de la transacción | **SÍ** | Campo crítico para ordenamiento FIFO |
| `FECHA_REF` | Fecha | Fecha de referencia | No | Opcional |
| `FECHA_VENC` | Fecha | Fecha de vencimiento | No | Opcional |

**Problemas Potenciales:**
- Inconsistencia entre `ANHO`/`MES` y `FECHA_ORIG`
- Fechas en formatos mixtos (dd/mm/yyyy, yyyy-mm-dd, seriales de Excel)
- Fechas nulas o inválidas

**Recomendaciones:**
- Validar que `ANHO` y `MES` coincidan con `FECHA_ORIG`
- Estandarizar formato de fechas a `yyyy-mm-dd` internamente
- Filtrar registros con fechas nulas o inválidas

---

### 2. **Campos de Cliente**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `ID_CLIENTE` | String | Identificador único del cliente | **SÍ** | Campo compuesto clave |
| `NOM_CLIENTE` | String | Nombre del cliente | **SÍ** | Campo compuesto clave |
| `DOC_CLIENTE` | String | Documento del cliente (RUC/DNI) | No | Opcional |
| `ID_LOCALIDAD_UBIGEO` | String | Código de ubicación (ubigeo) | No | Opcional |
| `NOM_DEPARTAMENTO` | String | Nombre del departamento | No | Opcional |
| `NOM_PROVINCIA` | String | Nombre de la provincia | No | Opcional |
| `NOM_DISTRITO` | String | Nombre del distrito | No | Opcional |

**Problemas Potenciales:**
- `ID_CLIENTE` nulo o vacío
- `NOM_CLIENTE` nulo o vacío
- Inconsistencia entre `ID_CLIENTE` y `NOM_CLIENTE` (mismo ID, diferente nombre)
- Valores "SIN ASIGNAR" en `NOM_CLIENTE`

**Recomendaciones:**
- Validar que `ID_CLIENTE` y `NOM_CLIENTE` no sean nulos
- Crear diccionario de clientes para detectar inconsistencias
- Filtrar registros con "SIN ASIGNAR"
- Estandarizar formato de `ID_CLIENTE` (sin espacios, mayúsculas)

---

### 3. **Campos de Artículo/SKU**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `ID_ARTICULO` | String | Identificador único del artículo | **SÍ** | Campo crítico para NC Sustento |
| `NOM_ARTICULO` | String | Nombre del artículo | **SÍ** | Campo crítico para NC Sustento |
| `ID_LINEA` | String | Identificador de línea | **SÍ** | Campo compuesto clave |
| `NOM_LINEA` | String | Nombre de línea | **SÍ** | Campo compuesto clave |
| `ID_GRUPO` | String | Identificador de grupo | No | Opcional |
| `NOM_GRUPO` | String | Nombre de grupo | No | Opcional |
| `ID_TIPO` | String | Identificador de tipo | No | Opcional |
| `NOM_TIPO` | String | Nombre de tipo | No | Opcional |
| `ID_FAMILIA` | String | Identificador de familia | No | Opcional |
| `NOM_FAMILIA` | String | Nombre de familia | No | Opcional |
| `ESTADO_LINEA` | String | Estado de la línea | No | Opcional |

**Problemas Potenciales:**
- `ID_ARTICULO` nulo o vacío
- `NOM_ARTICULO` nulo o vacío
- Inconsistencia entre `ID_ARTICULO` y `NOM_ARTICULO`
- Inconsistencia entre `ID_LINEA` y `NOM_LINEA`
- Valores "SIN ASIGNAR" en campos de nombre

**Recomendaciones:**
- Validar que `ID_ARTICULO` y `NOM_ARTICULO` no sean nulos
- Validar que `ID_LINEA` y `NOM_LINEA` no sean nulos
- Crear diccionarios de artículos y líneas para detectar inconsistencias
- Estandarizar formato de IDs (sin espacios, mayúsculas)

---

### 4. **Campos de Vendedor**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `ID_VENDEDOR` | String | Identificador único del vendedor | **SÍ** | Campo compuesto clave |
| `NOM_VENDEDOR` | String | Nombre del vendedor | **SÍ** | Campo compuesto clave |
| `CANAL DE DISTRIBUCION` | String | Canal de distribución | No | Opcional |

**Problemas Potenciales:**
- `ID_VENDEDOR` nulo o vacío
- `NOM_VENDEDOR` nulo o vacío
- Inconsistencia entre `ID_VENDEDOR` y `NOM_VENDEDOR`
- Valores "SIN ASIGNAR" en `NOM_VENDEDOR`

**Recomendaciones:**
- Validar que `ID_VENDEDOR` y `NOM_VENDEDOR` no sean nulos
- Crear diccionario de vendedores para detectar inconsistencias
- Filtrar registros con "SIN ASIGNAR"
- Estandarizar formato de `ID_VENDEDOR`

---

### 5. **Campos de Sucursal**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `COD_SUCURSAL` | String | Código de sucursal | No | Opcional |
| `NOM_SUCURSAL` | String | Nombre de sucursal | No | Opcional |

**Problemas Potenciales:**
- `COD_SUCURSAL` nulo o vacío
- `NOM_SUCURSAL` nulo o vacío
- Inconsistencia entre `COD_SUCURSAL` y `NOM_SUCURSAL`

**Recomendaciones:**
- Validar consistencia entre código y nombre
- Crear diccionario de sucursales

---

### 6. **Campos de Documento**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `TPO_DOC` | String | Tipo de documento (F, B, NC) | **SÍ** | Campo crítico para NC Sustento |
| `SERIE_DOC` | String | Serie del documento | **SÍ** | Campo crítico para NC Sustento |
| `NRO_DOC` | String | Número del documento | **SÍ** | Campo crítico para NC Sustento |
| `ORD_COMPRA` | String | Orden de compra | No | Opcional |
| `ID_GUIA` | String | ID de guía | No | Opcional |
| `REFERENCIA` | String | Referencia | No | Opcional |

**Problemas Potenciales:**
- `TPO_DOC` nulo o vacío
- `SERIE_DOC` nulo o vacío
- `NRO_DOC` nulo o vacío
- Inconsistencia en formato de documentos
- Documentos duplicados

**Recomendaciones:**
- Validar que `TPO_DOC`, `SERIE_DOC`, `NRO_DOC` no sean nulos
- Estandarizar formato de documentos (FXXX-YYYYYY)
- Detectar documentos duplicados
- Validar consistencia de tipo de documento

---

### 7. **Campos de Montos y Cantidades**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `CANTIDAD` | Numérico | Cantidad de unidades | **SÍ** | Campo crítico para NC Sustento |
| `SOLES` | Numérico | Monto en soles | **SÍ** | Campo crítico para NC Sustento |
| `DOLARES` | Numérico | Monto en dólares | No | Opcional |
| `PRECIO_UNID` | Numérico | Precio unitario | No | Puede calcularse de SOLES/CANTIDAD |
| `MONEDA` | String | Moneda de la transacción | No | Opcional |

**Problemas Potenciales:**
- `CANTIDAD` nula, cero o negativa
- `SOLES` nulo, cero o negativo
- Inconsistencia entre `SOLES`, `CANTIDAD` y `PRECIO_UNID`
- `PRECIO_UNID` no coincide con `SOLES/CANTIDAD`

**Recomendaciones:**
- Validar que `CANTIDAD` > 0
- Validar que `SOLES` >= 0
- Recalcular `PRECIO_UNID` = `SOLES` / `CANTIDAD`
- Filtrar registros con cantidades o montos inválidos

---

### 8. **Campos Adicionales**

| Campo | Tipo | Descripción | Crítico | Observaciones |
|-------|------|-------------|---------|--------------|
| `ID_PEDIDO` | String | ID del pedido | No | Opcional, usado en reportes |
| `NOM_CONDICION_PAGO` | String | Condición de pago | No | Opcional |
| `DIVISION` | String | División | No | Opcional |

**Problemas Potenciales:**
- `ID_PEDIDO` nulo o vacío
- Inconsistencia en formato de `ID_PEDIDO`

**Recomendaciones:**
- Validar formato de `ID_PEDIDO` si existe
- Estandarizar formato

---

## Campos Críticos para NC Sustento

Los siguientes campos son **CRÍTICOS** para el funcionamiento correcto de NC Sustento:

1. **`ID_ARTICULO`** - Identificador único del artículo
2. **`NOM_ARTICULO`** - Nombre del artículo
3. **`FECHA_ORIG`** - Fecha de origen (para ordenamiento FIFO)
4. **`CANTIDAD`** - Cantidad de unidades
5. **`SOLES`** - Monto en soles
6. **`TPO_DOC`** - Tipo de documento
7. **`SERIE_DOC`** - Serie del documento
8. **`NRO_DOC`** - Número del documento

## Campos Críticos para Reportes Consolidados

Los siguientes campos son **CRÍTICOS** para los reportes consolidados:

1. **`ID_CLIENTE`** - Identificador único del cliente
2. **`NOM_CLIENTE`** - Nombre del cliente
3. **`ID_VENDEDOR`** - Identificador único del vendedor
4. **`NOM_VENDEDOR`** - Nombre del vendedor
5. **`ID_LINEA`** - Identificador de línea
6. **`NOM_LINEA`** - Nombre de línea

## Campos Compuestos Clave

Los siguientes campos son **COMPUESTOS** y deben mantener consistencia:

| Campo Compuesto | ID | Nombre | Formato |
|-----------------|----|-------|---------|
| SKU | `ID_ARTICULO` | `NOM_ARTICULO` | "ID - NOMBRE" |
| LÍNEA | `ID_LINEA` | `NOM_LINEA` | "ID - NOMBRE" |
| CLIENTE | `ID_CLIENTE` | `NOM_CLIENTE` | "ID - NOMBRE" |
| VENDEDOR | `ID_VENDEDOR` | `NOM_VENDEDOR` | "ID - NOMBRE" |
| SUCURSAL | `COD_SUCURSAL` | `NOM_SUCURSAL` | "ID - NOMBRE" |

## Validaciones Recomendadas

### 1. **Validación de Campos Críticos**

```python
def validar_campos_criticos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida que los campos críticos no sean nulos o vacíos.
    
    Returns:
        Diccionario con resultado de validación
    """
    campos_criticos = [
        'ID_ARTICULO', 'NOM_ARTICULO', 'FECHA_ORIG', 
        'CANTIDAD', 'SOLES', 'TPO_DOC', 'SERIE_DOC', 'NRO_DOC'
    ]
    
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    for campo in campos_criticos:
        if campo not in df.columns:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo crítico faltante: {campo}")
            continue
        
        nulos = df[campo].isna().sum()
        vacios = (df[campo].astype(str).str.strip() == '').sum()
        
        if nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo {campo}: {nulos} valores nulos")
        
        if vacios > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo {campo}: {vacios} valores vacíos")
        
        resultado['estadisticas'][campo] = {
            'total': len(df),
            'nulos': int(nulos),
            'vacios': int(vacios),
            'validos': int(len(df) - nulos - vacios)
        }
    
    return resultado
```

### 2. **Validación de Campos Compuestos**

```python
def validar_campos_compuestos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de campos compuestos.
    
    Returns:
        Diccionario con resultado de validación
    """
    campos_compuestos = [
        ('ID_ARTICULO', 'NOM_ARTICULO', 'SKU'),
        ('ID_LINEA', 'NOM_LINEA', 'LÍNEA'),
        ('ID_CLIENTE', 'NOM_CLIENTE', 'CLIENTE'),
        ('ID_VENDEDOR', 'NOM_VENDEDOR', 'VENDEDOR'),
    ]
    
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'inconsistencias': {}
    }
    
    for id_campo, nom_campo, nombre_campo in campos_compuestos:
        if id_campo not in df.columns or nom_campo not in df.columns:
            continue
        
        # Crear diccionario de ID -> NOMBRE
        diccionario = df.groupby(id_campo)[nom_campo].apply(lambda x: x.unique().tolist()).to_dict()
        
        # Detectar inconsistencias (mismo ID, diferentes nombres)
        inconsistencias = {
            id_val: nombres 
            for id_val, nombres in diccionario.items() 
            if len(nombres) > 1
        }
        
        if inconsistencias:
            resultado['valid'] = False
            resultado['errores'].append(
                f"Campo {nombre_campo}: {len(inconsistencias)} IDs con múltiples nombres"
            )
            resultado['inconsistencias'][nombre_campo] = inconsistencias
    
    return resultado
```

### 3. **Validación de Fechas**

```python
def validar_fechas(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de fechas.
    
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    if 'FECHA_ORIG' not in df.columns:
        resultado['valid'] = False
        resultado['errores'].append("Campo FECHA_ORIG faltante")
        return resultado
    
    # Convertir a datetime si no lo es
    if not pd.api.types.is_datetime64_any_dtype(df['FECHA_ORIG']):
        df['FECHA_ORIG'] = pd.to_datetime(df['FECHA_ORIG'], dayfirst=True, errors='coerce')
    
    # Detectar fechas nulas
    fechas_nulas = df['FECHA_ORIG'].isna().sum()
    if fechas_nulas > 0:
        resultado['valid'] = False
        resultado['errores'].append(f"FECHA_ORIG: {fechas_nulas} fechas nulas o inválidas")
    
    # Validar consistencia con ANHO y MES
    if 'ANHO' in df.columns and 'MES' in df.columns:
        df_valido = df.dropna(subset=['FECHA_ORIG', 'ANHO', 'MES'])
        
        inconsistencias = 0
        for _, row in df_valido.iterrows():
            anho_esperado = row['FECHA_ORIG'].year
            mes_esperado = row['FECHA_ORIG'].month
            
            if str(row['ANHO']) != str(anho_esperado) or str(row['MES']) != str(mes_esperado):
                inconsistencias += 1
        
        if inconsistencias > 0:
            resultado['advertencias'].append(
                f"ANHO/MES: {inconsistencias} registros inconsistentes con FECHA_ORIG"
            )
    
    # Estadísticas de fechas
    if not df['FECHA_ORIG'].isna().all():
        resultado['estadisticas']['FECHA_ORIG'] = {
            'min': df['FECHA_ORIG'].min().strftime('%Y-%m-%d'),
            'max': df['FECHA_ORIG'].max().strftime('%Y-%m-%d'),
            'rango_dias': (df['FECHA_ORIG'].max() - df['FECHA_ORIG'].min()).days
        }
    
    return resultado
```

### 4. **Validación de Montos y Cantidades**

```python
def validar_montos_cantidades(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de montos y cantidades.
    
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    # Validar CANTIDAD
    if 'CANTIDAD' in df.columns:
        cant_nulos = df['CANTIDAD'].isna().sum()
        cant_cero = (df['CANTIDAD'] == 0).sum()
        cant_negativo = (df['CANTIDAD'] < 0).sum()
        
        if cant_nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"CANTIDAD: {cant_nulos} valores nulos")
        
        if cant_cero > 0:
            resultado['advertencias'].append(f"CANTIDAD: {cant_cero} valores cero")
        
        if cant_negativo > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"CANTIDAD: {cant_negativo} valores negativos")
        
        resultado['estadisticas']['CANTIDAD'] = {
            'min': float(df['CANTIDAD'].min()),
            'max': float(df['CANTIDAD'].max()),
            'promedio': float(df['CANTIDAD'].mean())
        }
    
    # Validar SOLES
    if 'SOLES' in df.columns:
        soles_nulos = df['SOLES'].isna().sum()
        soles_negativo = (df['SOLES'] < 0).sum()
        
        if soles_nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"SOLES: {soles_nulos} valores nulos")
        
        if soles_negativo > 0:
            resultado['advertencias'].append(f"SOLES: {soles_negativo} valores negativos")
        
        resultado['estadisticas']['SOLES'] = {
            'min': float(df['SOLES'].min()),
            'max': float(df['SOLES'].max()),
            'total': float(df['SOLES'].sum())
        }
    
    # Validar consistencia PRECIO_UNID
    if 'PRECIO_UNID' in df.columns and 'CANTIDAD' in df.columns and 'SOLES' in df.columns:
        df_valido = df.dropna(subset=['PRECIO_UNID', 'CANTIDAD', 'SOLES'])
        df_valido = df_valido[df_valido['CANTIDAD'] > 0]
        
        # Calcular precio esperado
        precio_esperado = df_valido['SOLES'] / df_valido['CANTIDAD']
        
        # Comparar con PRECIO_UNID
        diferencia = abs(df_valido['PRECIO_UNID'] - precio_esperado)
        inconsistencias = (diferencia > 0.01).sum()
        
        if inconsistencias > 0:
            resultado['advertencias'].append(
                f"PRECIO_UNID: {inconsistencias} registros inconsistentes con SOLES/CANTIDAD"
            )
    
    return resultado
```

### 5. **Validación de Documentos**

```python
def validar_documentos(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida la consistencia de documentos.
    
    Returns:
        Diccionario con resultado de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {}
    }
    
    # Validar campos de documento
    campos_doc = ['TPO_DOC', 'SERIE_DOC', 'NRO_DOC']
    for campo in campos_doc:
        if campo not in df.columns:
            resultado['valid'] = False
            resultado['errores'].append(f"Campo faltante: {campo}")
            continue
        
        nulos = df[campo].isna().sum()
        vacios = (df[campo].astype(str).str.strip() == '').sum()
        
        if nulos > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"{campo}: {nulos} valores nulos")
        
        if vacios > 0:
            resultado['valid'] = False
            resultado['errores'].append(f"{campo}: {vacios} valores vacíos")
    
    # Detectar documentos duplicados
    if all(campo in df.columns for campo in campos_doc):
        df['DOC_KEY'] = df['TPO_DOC'].astype(str) + '|' + df['SERIE_DOC'].astype(str) + '|' + df['NRO_DOC'].astype(str)
        duplicados = df['DOC_KEY'].duplicated().sum()
        
        if duplicados > 0:
            resultado['advertencias'].append(f"Documentos: {duplicados} registros duplicados")
        
        resultado['estadisticas']['DOCUMENTOS'] = {
            'total': df['DOC_KEY'].nunique(),
            'duplicados': int(duplicados)
        }
    
    return resultado
```

## Función de Validación Completa

```python
def validar_historial_completo(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Valida completamente el historial como fuente de verdad.
    
    Args:
        df: DataFrame del historial
        
    Returns:
        Diccionario con resultado completo de validación
    """
    resultado = {
        'valid': True,
        'errores': [],
        'advertencias': [],
        'estadisticas': {},
        'validaciones': {}
    }
    
    # Ejecutar todas las validaciones
    validaciones = [
        ('campos_criticos', validar_campos_criticos(df)),
        ('campos_compuestos', validar_campos_compuestos(df)),
        ('fechas', validar_fechas(df)),
        ('montos_cantidades', validar_montos_cantidades(df)),
        ('documentos', validar_documentos(df)),
    ]
    
    for nombre, validacion in validaciones:
        resultado['validaciones'][nombre] = validacion
        
        if not validacion['valid']:
            resultado['valid'] = False
            resultado['errores'].extend(validacion['errores'])
        
        resultado['advertencias'].extend(validacion['advertencias'])
        resultado['estadisticas'].update(validacion.get('estadisticas', {}))
    
    # Estadísticas generales
    resultado['estadisticas']['GENERAL'] = {
        'total_registros': len(df),
        'total_columnas': len(df.columns),
        'columnas': df.columns.tolist()
    }
    
    return resultado
```

## Recomendaciones de Mejora

### 1. **Crear Diccionario de Datos Maestros**

```python
class DiccionarioDatosMaestros:
    """
    Diccionario centralizado de datos maestros para validar consistencia.
    """
    
    def __init__(self):
        self.clientes = {}  # ID_CLIENTE -> NOM_CLIENTE
        self.articulos = {}  # ID_ARTICULO -> NOM_ARTICULO
        self.lineas = {}  # ID_LINEA -> NOM_LINEA
        self.vendedores = {}  # ID_VENDEDOR -> NOM_VENDEDOR
        self.sucursales = {}  # COD_SUCURSAL -> NOM_SUCURSAL
    
    def cargar_desde_historial(self, df: pd.DataFrame):
        """Carga los diccionarios desde el historial."""
        if 'ID_CLIENTE' in df.columns and 'NOM_CLIENTE' in df.columns:
            self.clientes = df.groupby('ID_CLIENTE')['NOM_CLIENTE'].first().to_dict()
        
        if 'ID_ARTICULO' in df.columns and 'NOM_ARTICULO' in df.columns:
            self.articulos = df.groupby('ID_ARTICULO')['NOM_ARTICULO'].first().to_dict()
        
        if 'ID_LINEA' in df.columns and 'NOM_LINEA' in df.columns:
            self.lineas = df.groupby('ID_LINEA')['NOM_LINEA'].first().to_dict()
        
        if 'ID_VENDEDOR' in df.columns and 'NOM_VENDEDOR' in df.columns:
            self.vendedores = df.groupby('ID_VENDEDOR')['NOM_VENDEDOR'].first().to_dict()
        
        if 'COD_SUCURSAL' in df.columns and 'NOM_SUCURSAL' in df.columns:
            self.sucursales = df.groupby('COD_SUCURSAL')['NOM_SUCURSAL'].first().to_dict()
    
    def validar_consistencia(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Valida la consistencia del historial contra los diccionarios."""
        resultado = {
            'valid': True,
            'errores': [],
            'advertencias': [],
            'inconsistencias': {}
        }
        
        # Validar clientes
        if 'ID_CLIENTE' in df.columns and 'NOM_CLIENTE' in df.columns:
            for _, row in df.iterrows():
                id_cliente = row['ID_CLIENTE']
                nom_cliente = row['NOM_CLIENTE']
                
                if id_cliente in self.clientes:
                    if self.clientes[id_cliente] != nom_cliente:
                        if 'CLIENTE' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['CLIENTE'] = []
                        resultado['inconsistencias']['CLIENTE'].append({
                            'id': id_cliente,
                            'nombre_esperado': self.clientes[id_cliente],
                            'nombre_encontrado': nom_cliente
                        })
        
        # Validar artículos
        if 'ID_ARTICULO' in df.columns and 'NOM_ARTICULO' in df.columns:
            for _, row in df.iterrows():
                id_articulo = row['ID_ARTICULO']
                nom_articulo = row['NOM_ARTICULO']
                
                if id_articulo in self.articulos:
                    if self.articulos[id_articulo] != nom_articulo:
                        if 'ARTICULO' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['ARTICULO'] = []
                        resultado['inconsistencias']['ARTICULO'].append({
                            'id': id_articulo,
                            'nombre_esperado': self.articulos[id_articulo],
                            'nombre_encontrado': nom_articulo
                        })
        
        # Validar líneas
        if 'ID_LINEA' in df.columns and 'NOM_LINEA' in df.columns:
            for _, row in df.iterrows():
                id_linea = row['ID_LINEA']
                nom_linea = row['NOM_LINEA']
                
                if id_linea in self.lineas:
                    if self.lineas[id_linea] != nom_linea:
                        if 'LINEA' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['LINEA'] = []
                        resultado['inconsistencias']['LINEA'].append({
                            'id': id_linea,
                            'nombre_esperado': self.lineas[id_linea],
                            'nombre_encontrado': nom_linea
                        })
        
        # Validar vendedores
        if 'ID_VENDEDOR' in df.columns and 'NOM_VENDEDOR' in df.columns:
            for _, row in df.iterrows():
                id_vendedor = row['ID_VENDEDOR']
                nom_vendedor = row['NOM_VENDEDOR']
                
                if id_vendedor in self.vendedores:
                    if self.vendedores[id_vendedor] != nom_vendedor:
                        if 'VENDEDOR' not in resultado['inconsistencias']:
                            resultado['inconsistencias']['VENDEDOR'] = []
                        resultado['inconsistencias']['VENDEDOR'].append({
                            'id': id_vendedor,
                            'nombre_esperado': self.vendedores[id_vendedor],
                            'nombre_encontrado': nom_vendedor
                        })
        
        # Calcular estadísticas
        for campo, inconsistencias in resultado['inconsistencias'].items():
            if len(inconsistencias) > 0:
                resultado['valid'] = False
                resultado['errores'].append(
                    f"{campo}: {len(inconsistencias)} inconsistencias encontradas"
                )
        
        return resultado
```

### 2. **Implementar Validación en NCProcessor**

```python
class NCProcessor:
    def __init__(self, historial_compras: pd.DataFrame, sort_mode: str = "fecha_desc"):
        """
        Inicializa el procesador preparando la base de datos de historial.
        """
        self.filas_omitidas_detalle: List[Dict] = []
        self.sort_mode = sort_mode
        
        # Validar historial antes de procesar
        validacion = validar_historial_completo(historial_compras)
        
        if not validacion['valid']:
            logger.error(f"Validación de historial falló: {validacion['errores']}")
            raise ValueError(f"Historial inválido: {', '.join(validacion['errores'][:3])}")
        
        if validacion['advertencias']:
            logger.warning(f"Advertencias de validación: {validacion['advertencias']}")
        
        self.validacion = validacion
        self.historial = self._preparar_historial(historial_compras)
        
        # Cargar diccionario de datos maestros
        self.datos_maestros = DiccionarioDatosMaestros()
        self.datos_maestros.cargar_desde_historial(self.historial)
        
        # Optimización: Pre-agrupar historial por artículo
        self._cache_articulos = {str(k): v for k, v in self.historial.groupby('ID_ARTICULO')}
    
    def obtener_resumen_validacion(self) -> Dict[str, Any]:
        """Obtiene el resumen de validación del historial."""
        return self.validacion
```

### 3. **Crear Reporte de Calidad de Datos**

```python
def generar_reporte_calidad_datos(df: pd.DataFrame, ruta_salida: str):
    """
    Genera un reporte de calidad de datos en Excel.
    
    Args:
        df: DataFrame del historial
        ruta_salida: Ruta donde guardar el reporte
    """
    validacion = validar_historial_completo(df)
    
    # Crear diccionario de datos maestros
    datos_maestros = DiccionarioDatosMaestros()
    datos_maestros.cargar_desde_historial(df)
    validacion_maestros = datos_maestros.validar_consistencia(df)
    
    # Crear Excel con reporte
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    
    # Escribir resumen general
    ws['A1'] = "REPORTE DE CALIDAD DE DATOS - HISTORIAL"
    ws['A1'].font = Font(bold=True, size=14)
    
    ws['A3'] = "Estado General:"
    ws['B3'] = "VÁLIDO" if validacion['valid'] else "INVÁLIDO"
    ws['B3'].font = Font(bold=True, color="00FF00" if validacion['valid'] else "FF0000")
    
    ws['A4'] = "Total Registros:"
    ws['B4'] = validacion['estadisticas']['GENERAL']['total_registros']
    
    ws['A5'] = "Total Columnas:"
    ws['B5'] = validacion['estadisticas']['GENERAL']['total_columnas']
    
    # Escribir errores
    ws['A7'] = "ERRORES:"
    ws['A7'].font = Font(bold=True)
    
    for i, error in enumerate(validacion['errores'], 8):
        ws[f'A{i}'] = error
        ws[f'A{i}'].font = Font(color="FF0000")
    
    # Escribir advertencias
    ws['C7'] = "ADVERTENCIAS:"
    ws['C7'].font = Font(bold=True)
    
    for i, advertencia in enumerate(validacion['advertencias'], 8):
        ws[f'C{i}'] = advertencia
        ws[f'C{i}'].font = Font(color="FFA500")
    
    # Escribir inconsistencias de datos maestros
    ws['E7'] = "INCONSISTENCIAS:"
    ws['E7'].font = Font(bold=True)
    
    for campo, inconsistencias in validacion_maestros['inconsistencias'].items():
        ws[f'E{8}'] = f"{campo}: {len(inconsistencias)} inconsistencias"
        ws[f'E{8}'].font = Font(color="FFA500")
    
    # Guardar
    wb.save(ruta_salida)
```

## Conclusión

El historial es la **fuente de verdad** del sistema y debe ser validado exhaustivamente antes de su uso. Las validaciones recomendadas permitirán:

1. **Detectar errores** en los datos antes de procesar
2. **Garantizar consistencia** en campos compuestos
3. **Mejorar la calidad** de los reportes generados
4. **Reducir errores** en NC Sustento y reportes consolidados
5. **Documentar problemas** para corrección futura

La implementación de estas validaciones debe ser **obligatoria** en el proceso de carga del historial.
