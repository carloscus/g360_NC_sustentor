---
deck: Manual de usuario - G360 NC Sustentor
audience: Clientes que reciben su copia de la app. Usan Excel y el sistema de ventas todos los dias, no son tecnicos. Quieren saber que hacer paso a paso, sin sorpresas.
register: read
structure: dark throughout, cards for choices and steps for sequences
motif: numbered section markers and the accent rule under each title
---

## Slide 1
layout: title
Brief: Portada que dice que es y para quien, sin tecnicismos; lo primero que ve el cliente al abrir el archivo.
Title: Manual de usuario — G360 NC Sustentor
Subtitle: Todo lo que necesitas para arrancar, actualizar tu copia y generar tus expedientes, paso a paso.

## Slide 2
layout: composed
Brief: Arrancar es una secuencia de 3 momentos distintos; process los ordena de izquierda a derecha y el ojo sigue las flechas.
Title: Arrancar la app
Block: process
Doble clic en launch.vbs | En tu escritorio o en la carpeta, sin consola a la vista
Primera vez | Instala solo lo que falta (pide internet unos minutos)
Todos los dias | Abre directo y rapido, donde la dejaste
Notes: Si la ventana no aparece, probablemente quedo una instancia anterior abierta. Cerrala desde el Administrador de tareas (proceso python.exe) y volve a intentar.

## Slide 3
layout: composed
Brief: Actualizar es recibir-importar-verificar; process porque el orden importa y cada paso habilita el siguiente.
Title: Poner al dia tu copia
Block: process
Recibi el cartucho | Te llega un archivo .zip del proveedor, completo o de un rango de fechas
Importalo | Configuracion, Gestion, Importar cartucho; se suma a lo que ya tenes, no lo borra
Verifica | Revisa que tus clientes y tus fechas esten, antes de seguir trabajando
Notes: Un cartucho incremental solo trae lo nuevo y se suma. Uno completo reemplaza todo: se usa cuando arrancas de cero.

## Slide 4
layout: composed
Brief: El flujo central en 4 pasos; process porque es la columna vertebral del manual y el cliente lo va a releer.
Title: Crear un sustento, en 4 pasos
Block: process
1 Defini el caso | Tipo de caso y modalidad, con tus observaciones
2 Acota los datos | Vendedor, cliente y rango de fechas
3 Ejecuta | La app busca el historial y calcula las diferencias
4 Genera el expediente | Sale la carpeta con el Excel y el informe
Notes: Cada paso habilita el siguiente. Si un boton esta apagado, te falta completar el anterior.

## Slide 5
layout: composed
Brief: Dos botones que se confunden; comparison que resuelve cual va primero y por que, con el ganador marcado.
Title: Reporte de compras: primero mirar, despues generar
Block: comparison
! Ver datos | Consulta lo que hay, sin escribir nada. Siempre primero
Generar Excel | Escribe los archivos. Solo cuando lo de arriba te conforma
Notes: Ver datos no toca nada. Generar Excel crea los archivos en tu carpeta de salida.

## Slide 6
layout: composed
Brief: Tres opciones independientes que conviven; card-grid porque son hermanas y ninguna manda sobre otra.
Title: Opciones de calculo, en palabras simples
Block: card-grid
! Ocultar diferencias 0.00 | Saca del listado lo que dio cero, para ver solo lo que importa
Calcular NDB | Agrega la Nota de Debito cuando el precio facturado quedo por debajo
Solo NDB | Deja unicamente lo de debito e ignora el resto
Notes: Ocultar ceros esta activado por defecto. Solo NDB es para cuando unicamente te interesa lo de debito.

## Slide 7
layout: title-content
Brief: Beat de prosa genuino: dos ideas cortas sobre la plantilla y las filas vacias, sin forma que lo diga mejor.
Title: Listas de precios
Body: Usa la plantilla oficial con CODIGO_SKU y PRECIO_BASE. Si pegas datos con filas vacias o subtitulos entre medio, la app las ignora y te avisa cuantas salto: no frenan el proceso.
Notes: Si tu proveedor te manda su propio formato sin encabezados, avisanos: hay una lectura de contingencia que lo interpreta igual.

## Slide 8
layout: composed
Brief: La duda real del cliente (pierdo mis lineas?); comparison que resuelve del lado del cliente, marcado como ganador.
Title: Tus lineas mandan
Block: comparison
! Las tuyas | Cada PC guarda las suyas; importar un cartucho no las cambia
Las del cartucho | Vienen como informacion; las adoptas solo si queres, desde Configuracion
Notes: Si adoptas lineas nuevas, revisa antes el diagnostico de cobertura: te dice que dias quedarian con lineas fuera.

## Slide 9
layout: composed
Brief: Tres problemas comunes con su salida; icon-list porque el icono anticipa la respuesta y se lee de un vistazo.
Title: Si algo no anda
Block: icon-list
alert | La app no arranca: cerra la instancia anterior y reintenta
data | Faltan datos o clientes: revisa que el cartucho se haya importado bien
chat | Cualquier otra cosa: anota que hacias, saca captura y avisanos
Notes: El detalle tecnico queda en el log de la app, que nos sirve para ayudarte mas rapido.

## Slide 10
layout: statement
Brief: Cierre en una linea que resume el trato: la app hace el calculo, vos decidis.
Statement: Vos pones los datos; la app pone las cuentas.
Notes: Guarda este manual junto a tu copia. Cuando llegue una actualizacion, repeti el paso 3 y segui trabajando.
