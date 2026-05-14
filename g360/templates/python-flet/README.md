# G360 Python Flet Desktop App

## Estructura del Proyecto

```
mi-proyecto/
├── src/
│   ├── main.py              # Punto de entrada
│   ├── test_app.py          # Tests con pytest
│   └── core/
│       └── skill.json       # Configuración del skill
├── assets/
│   └── images/
│       └── app.ico          # Icono de la app
├── portable/
│   ├── build.bat            # Build portable EXE (sin Python)
│   └── run.bat              # Run con Python (del sistema o UV)
├── g360-nc-sustentor-portable/   # Copia para distribución
├── g360/
│   └── (estructura G360)
├── requirements.txt
├── create_shortcut.vbs      # Crear acceso directo
└── README.md
```

## Instalación

```bash
pip install -r requirements.txt
```

## Desarrollo

```bash
python src/main.py
```

## Tests

```bash
pytest src/test_app.py -v
pytest src/test_app.py --cov=src --cov-report=html
```

## Dos Modalidades de Portable

### Modalidad 1: Con Python (run.bat)
Para PCs que tienen Python instalado o pueden instalarlo:

```bash
run.bat
```

Esto:
1. Detecta si hay Python en el sistema
2. Si no hay, instala UV (gestor rápido)
3. UV instala Python 3.10
4. Crea entorno virtual
5. Instala dependencias
6. Crea acceso directo en escritorio
7. Ejecuta la app

### Modalidad 2: Sin Python (build-portable.bat)
Para PCs que NO tienen Python - genera ejecutable standalone:

```bash
cd portable
build.bat
```

Esto genera: `dist/G360App-Portable.exe`

El EXE funciona en cualquier PC con Windows sin necesidad de Python.

## Distribución a Clientes

1. **Genera el portable EXE**:
   ```bash
   portable\build.bat
   ```

2. **Copia la estructura**:
   - Copia todo el proyecto a una carpeta `g360-nc-sustentor-portable`
   - O simplemente entrega el EXE de `dist/`

3. **Entrega al cliente**:
   - Solo necesita ejecutar `run.bat` (si tiene Python)
   - O el `G360App-Portable.exe` (si no tiene Python)

## Lineamientos para el Agente Python

### Para resolver problemas:
1. Primero ejecuta los tests: `pytest src/test_app.py -v`
2. Identifica el test que falla
3. Reproduce el problema en el código
4. Implementa la solución
5. Verifica con tests

### Para cálculos y procesamiento:
1. Usa la clase TestCalculations en test_app.py
2. Agrega nuevos tests para funciones de cálculo
3. Ejecuta: `pytest src/test_app.py::TestCalculations -v`

### Antes de subir a GitHub:
```bash
g360 clean . --github
```
Esto actualiza el .gitignore y prepara el repo para remoto.

## Skills Disponibles

- `flet-desktop` - Estilo G360 moderno para desktop
- `flet-desktop-corporativo` - Estilo corporativo

## Snippets Flet

Usa los snippets de G360 para componentes rápidos:
- `flet-page` - Configuración de página
- `flet-card` - Tarjetas con estilo G360
- `flet-button` - Botones G360
- `flet-datatable` - Tablas de datos
- `flet-dialog` - Diálogos modales
- `flet-chart-bar` - Gráficos de barras

## Notas del build portable

- El ejecutable incluye todo el runtime de Python
- Tamaño aproximado: 30-50 MB
- Funciona en Windows 10/11 sin dependencias
- El icono se puede personalizar en `assets/images/app.ico`