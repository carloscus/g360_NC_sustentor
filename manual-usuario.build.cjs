/*
 * manual-usuario.build.cjs - Manual de usuario G360 NC Sustentor (10 slides).
 *
 * Read register, dark G360 ground throughout. Simple token-locked
 * compositions: title, numbered process rails, a resolving comparison,
 * a 3-card grid, an icon-list drawn with accent dots, prose beats, and a
 * closing statement. No photographs, no freeform heroics: the reader is a
 * non-technical client who needs steps, not art direction.
 *
 * Run it:
 *   node manual-usuario.build.cjs --tokens .slides/tokens.cjs --out manual-usuario.pptx
 */

const argv = process.argv.slice(2);

function arg(name) {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
}

const tokensPath = arg("--tokens");
const outPath = arg("--out");

if (!tokensPath || !outPath) {
  console.error("usage: node manual-usuario.build.cjs --tokens <tokens.cjs> --out <deck.pptx>");
  process.exit(2);
}

const T = require(require("path").resolve(tokensPath));
const pptxgen = T.loadPptxgen();

const C = JSON.parse('{"s1":{"title":"Manual de usuario — G360 NC Sustentor","subtitle":"Todo lo que necesitas para arrancar, actualizar tu copia y generar tus expedientes, paso a paso.","notes":"Portada del manual. El cliente abre este archivo sin que nadie se lo presente: el titulo dice que es y el subtitulo que va a poder hacer al terminar."},"s2":{"title":"Arrancar la app","steps":[["Doble clic en launch.vbs","En tu escritorio o en la carpeta, sin consola a la vista"],["Primera vez","Instala solo lo que falta (pide internet unos minutos)"],["Todos los dias","Abre directo y rapido, donde la dejaste"]],"notes":"Si la ventana no aparece, probablemente quedo una instancia anterior abierta. Cerrala desde el Administrador de tareas (proceso python.exe) y volve a intentar."},"s3":{"title":"Poner al dia tu copia","steps":[["Recibi el cartucho","Te llega un archivo .zip del proveedor, completo o de un rango de fechas"],["Importalo","Configuracion, Gestion, Importar cartucho; se suma a lo que ya tenes, no lo borra"],["Verifica","Revisa que tus clientes y tus fechas esten, antes de seguir trabajando"]],"notes":"Un cartucho incremental solo trae lo nuevo y se suma. Uno completo reemplaza todo: se usa cuando arrancas de cero."},"s4":{"title":"Crear un sustento, en 4 pasos","steps":[["1 Defini el caso","Tipo de caso y modalidad, con tus observaciones"],["2 Acota los datos","Vendedor, cliente y rango de fechas"],["3 Ejecuta","La app busca el historial y calcula las diferencias"],["4 Genera el expediente","Sale la carpeta con el Excel y el informe"]],"notes":"Cada paso habilita el siguiente. Si un boton esta apagado, te falta completar el anterior."},"s5":{"title":"Reporte de compras: primero mirar, despues generar","leftLabel":"Ver datos","left":"Consulta lo que hay, sin escribir nada. Siempre primero.","rightLabel":"Generar Excel","right":"Escribe los archivos. Solo cuando lo de arriba te conforma.","notes":"Ver datos no toca nada. Generar Excel crea los archivos en tu carpeta de salida."},"s6":{"title":"Opciones de calculo, en palabras simples","cards":[["Ocultar diferencias 0.00","Saca del listado lo que dio cero, para ver solo lo que importa"],["Calcular NDB","Agrega la Nota de Debito cuando el precio facturado quedo por debajo"],["Solo NDB","Deja unicamente lo de debito e ignora el resto"]],"notes":"Ocultar ceros esta activado por defecto. Solo NDB es para cuando unicamente te interesa lo de debito."},"s7":{"title":"Listas de precios","body":"Usa la plantilla oficial con CODIGO_SKU y PRECIO_BASE. Si pegas datos con filas vacias o subtitulos entre medio, la app las ignora y te avisa cuantas salto: no frenan el proceso.","notes":"Si tu proveedor te manda su propio formato sin encabezados, avisanos: hay una lectura de contingencia que lo interpreta igual."},"s8":{"title":"Tus lineas mandan","leftLabel":"Las tuyas","left":"Cada PC guarda las suyas; importar un cartucho no las cambia.","rightLabel":"Las del cartucho","right":"Vienen como informacion; las adoptas solo si queres, desde Configuracion.","notes":"Si adoptas lineas nuevas, revisa antes el diagnostico de cobertura: te dice que dias quedarian con lineas fuera."},"s9":{"title":"Si algo no anda","items":[["La app no arranca","Cerra la instancia anterior y reintenta"],["Faltan datos o clientes","Revisa que el cartucho se haya importado bien"],["Cualquier otra cosa","Anota que hacias, saca captura y avisanos"]],"notes":"El detalle tecnico queda en el log de la app, que nos sirve para ayudarte mas rapido."},"s10":{"statement":"Vos pones los datos; la app pone las cuentas.","notes":"Guarda este manual junto a tu copia. Cuando llegue una actualizacion, repeti el paso 3 y segui trabajando."}}');

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";

const W = T.slide.w;
const H = T.slide.h;
const MX = T.margins.x;
const MT = T.margins.top;
const CONTENT_W = W - 2 * MX;
const ROUNDED = T.shape.corner === "rounded";

function text(slide, body, opts) {
  slide.addText(body, Object.assign({ margin: 0 }, opts));
}

function mark(slide, kind, opts) {
  slide.addShape(kind, Object.assign({}, opts));
}

function panel(slide, opts) {
  const o = Object.assign({}, opts);
  if (ROUNDED) {
    o.rectRadius = 0.12;
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, o);
  } else {
    slide.addShape(pres.shapes.RECTANGLE, o);
  }
}

function tint(colour, transparency) {
  return { color: colour, transparency: transparency };
}

function rule(slide, y) {
  mark(slide, pres.shapes.LINE, {
    x: MX, y: y, w: 1.6, h: 0,
    line: { color: T.colours.accent, width: 3 },
    objectName: "deco-rule",
  });
}

function slideTitle(slide, title, leadName) {
  text(slide, title, {
    x: MX, y: MT + 0.05, w: CONTENT_W - 0.2, h: 1.15,
    fontFace: T.fonts.heading, fontSize: T.scale.h1, bold: true,
    color: T.colours.ink, valign: "top", objectName: leadName,
  });
  rule(slide, MT + 1.28);
}

// ----------------------------------------------------------------- 1. title --
{
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  mark(s, pres.shapes.RECTANGLE, {
    x: 8.9, y: 0, w: W - 8.9, h: H,
    fill: { color: T.colours.accent }, line: { color: T.colours.accent },
    objectName: "deco-accent-panel",
  });
  text(s, C.s1.title, {
    x: MX, y: 2.1, w: 7.6, h: 2.6,
    fontFace: T.fonts.heading, fontSize: T.scale.title, bold: true,
    color: T.colours.ink, valign: "bottom",
    lineSpacingMultiple: 0.95, objectName: "slides-lead:Title",
  });
  text(s, C.s1.subtitle, {
    x: MX, y: 4.85, w: 7.6, h: 1.4,
    fontFace: T.fonts.body, fontSize: T.scale.body,
    color: T.colours.ink, valign: "top",
    objectName: "slides-field:Subtitle",
  });
  s.addNotes(C.s1.notes);
}

// -------------------------------------------------- 2-4. process (shared) --
function processSlide(key, leadName) {
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  const d = C[key];
  slideTitle(s, d.title, leadName);
  const n = d.steps.length;
  const gap = 0.35;
  const cw = (CONTENT_W - gap * (n - 1)) / n;
  const py = 2.35;
  const ph = 4.15;
  d.steps.forEach((st, i) => {
    const bx = MX + i * (cw + gap);
    panel(s, {
      x: bx, y: py, w: cw, h: ph,
      fill: tint(T.colours.accent, i === 0 ? 88 : 94),
      line: tint(T.colours.accent, 100),
      objectName: "deco-step-" + (i + 1),
    });
    text(s, st[0], {
      x: bx + 0.35, y: py + 0.35, w: cw - 0.7, h: 1.15,
      fontFace: T.fonts.heading, fontSize: T.scale.body, bold: true,
      color: T.colours.ink, valign: "top", objectName: "slides-field:Step-" + (i + 1),
    });
    text(s, st[1], {
      x: bx + 0.35, y: py + 1.7, w: cw - 0.7, h: 2.0,
      fontFace: T.fonts.body, fontSize: T.scale.body,
      color: T.colours.ink, valign: "top", objectName: "slides-field:StepDetail-" + (i + 1),
    });
    if (i < n - 1) {
      const ax = bx + cw + 0.06;
      mark(s, pres.shapes.RIGHT_ARROW, {
        x: ax, y: py + ph / 2 - 0.14, w: gap - 0.12, h: 0.28,
        fill: { color: T.colours.accent }, line: { color: T.colours.accent },
        objectName: "deco-arrow-" + (i + 1),
      });
    }
  });
  s.addNotes(d.notes);
}

processSlide("s2", "slides-lead:Title");
processSlide("s3", "slides-lead:Title");
processSlide("s4", "slides-lead:Title");

// ------------------------------------------ 5 & 8. comparison (resolved) --
function comparisonSlide(key, leadName) {
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  const d = C[key];
  slideTitle(s, d.title, leadName);
  const colGap = 0.5;
  const colW = (CONTENT_W - colGap) / 2;
  const rx = MX + colW + colGap;
  const pad = 0.4;
  const py = 2.35;
  const ph = 4.15;
  panel(s, {
    x: MX, y: py, w: colW, h: ph,
    fill: { color: T.colours.accent }, line: { color: T.colours.accent },
    objectName: "deco-winner",
  });
  text(s, d.leftLabel, {
    x: MX + pad, y: py + 0.35, w: colW - 2 * pad, h: 0.7,
    fontFace: T.fonts.heading, fontSize: T.scale.body, bold: true,
    color: T.colours.paper, objectName: "slides-field:Left",
  });
  text(s, d.left, {
    x: MX + pad, y: py + 1.25, w: colW - 2 * pad, h: 2.5,
    fontFace: T.fonts.body, fontSize: T.scale.body, color: T.colours.paper,
    valign: "top", objectName: "slides-field:LeftBody",
  });
  panel(s, {
    x: rx, y: py, w: colW, h: ph,
    fill: tint(T.colours.ink, 94), line: tint(T.colours.ink, 100),
    objectName: "deco-other",
  });
  text(s, d.rightLabel, {
    x: rx + pad, y: py + 0.35, w: colW - 2 * pad, h: 0.7,
    fontFace: T.fonts.heading, fontSize: T.scale.body, bold: true,
    color: T.colours.ink, objectName: "slides-field:Right",
  });
  text(s, d.right, {
    x: rx + pad, y: py + 1.25, w: colW - 2 * pad, h: 2.5,
    fontFace: T.fonts.body, fontSize: T.scale.body, color: T.colours.ink,
    valign: "top", objectName: "slides-field:RightBody",
  });
  s.addNotes(d.notes);
}

comparisonSlide("s5", "slides-lead:Title");
comparisonSlide("s8", "slides-lead:Title");

// ------------------------------------------------------- 6. card grid --
{
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  slideTitle(s, C.s6.title, "slides-lead:Title");
  const n = C.s6.cards.length;
  const gap = 0.4;
  const cw = (CONTENT_W - gap * (n - 1)) / n;
  const py = 2.35;
  const ph = 4.15;
  C.s6.cards.forEach((cd, i) => {
    const bx = MX + i * (cw + gap);
    const lead = i === 0;
    panel(s, {
      x: bx, y: py, w: cw, h: ph,
      fill: lead ? { color: T.colours.accent } : tint(T.colours.ink, 94),
      line: lead ? { color: T.colours.accent } : tint(T.colours.ink, 100),
      objectName: "deco-card-" + (i + 1),
    });
    const fg = lead ? T.colours.paper : T.colours.ink;
    text(s, cd[0], {
      x: bx + 0.35, y: py + 0.35, w: cw - 0.7, h: 1.3,
      fontFace: T.fonts.heading, fontSize: T.scale.body, bold: true,
      color: fg, valign: "top", objectName: "slides-field:Card-" + (i + 1),
    });
    text(s, cd[1], {
      x: bx + 0.35, y: py + 1.85, w: cw - 0.7, h: 1.9,
      fontFace: T.fonts.body, fontSize: T.scale.body,
      color: fg, valign: "top", objectName: "slides-field:CardBody-" + (i + 1),
    });
  });
  s.addNotes(C.s6.notes);
}

// ------------------------------------------------------ 7. prose beat --
{
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  text(s, C.s7.title, {
    x: MX, y: MT + 0.05, w: 10.6, h: 1.15,
    fontFace: T.fonts.heading, fontSize: T.scale.h1, bold: true,
    color: T.colours.ink, valign: "top", objectName: "slides-lead:Title",
  });
  rule(s, MT + 1.3);
  text(s, C.s7.body, {
    x: MX, y: 2.1, w: 10.4, h: 3.4,
    fontFace: T.fonts.body, fontSize: T.scale.body, color: T.colours.ink,
    valign: "top", lineSpacingMultiple: 1.25, objectName: "slides-field:Body",
  });
  s.addNotes(C.s7.notes);
}

// ------------------------------------------------------- 9. icon list --
{
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  slideTitle(s, C.s9.title, "slides-lead:Title");
  const py = 2.4;
  const rowH = 1.28;
  const gap = 0.22;
  C.s9.items.forEach((it, i) => {
    const ry = py + i * (rowH + gap);
    mark(s, pres.shapes.OVAL, {
      x: MX + 0.08, y: ry + 0.22, w: 0.42, h: 0.42,
      fill: { color: T.colours.accent }, line: { color: T.colours.accent },
      objectName: "deco-dot-" + (i + 1),
    });
    text(s, it[0], {
      x: MX + 0.75, y: ry, w: CONTENT_W - 0.75, h: 0.62,
      fontFace: T.fonts.heading, fontSize: T.scale.body, bold: true,
      color: T.colours.ink, valign: "bottom", objectName: "slides-field:Item-" + (i + 1),
    });
    text(s, it[1], {
      x: MX + 0.75, y: ry + 0.62, w: CONTENT_W - 0.75, h: 0.62,
      fontFace: T.fonts.body, fontSize: T.scale.body,
      color: T.colours.ink, valign: "top", objectName: "slides-field:ItemBody-" + (i + 1),
    });
  });
  s.addNotes(C.s9.notes);
}

// ------------------------------------------------------ 10. statement --
// Dark close. En esta marca `paper` es el fondo oscuro e `ink` el texto
// claro: al reves de la convencion de un tema claro.
{
  const s = pres.addSlide();
  s.background = { color: T.colours.paper };
  text(s, C.s10.statement, {
    x: MX, y: 2.4, w: 10.4, h: 2.6,
    fontFace: T.fonts.heading, fontSize: T.scale.h1, bold: true,
    color: T.colours.ink, valign: "top", lineSpacingMultiple: 1.08,
    objectName: "slides-lead:Statement",
  });
  mark(s, pres.shapes.LINE, {
    x: MX, y: 5.3, w: 1.6, h: 0,
    line: { color: T.colours.accent, width: 4 },
    objectName: "slides-ghost",
  });
  s.addNotes(C.s10.notes);
}

pres
  .writeFile({ fileName: outPath })
  .then(() => console.log("wrote " + outPath))
  .catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
