---
name: Sibylla
description: Una pitonisa cibernética que separa señal de ruido para vislumbrar el horizonte.
colors:
  obsidian-night: "#080B14"
  deep-night: "#0D1322"
  oracle-panel: "rgba(20,27,46,.55)"
  oracle-border: "rgba(217,184,95,.22)"
  marble-ivory: "#ECE4D2"
  marble-muted: "#C3BCA9"
  muted-stone: "#8E8979"
  laurel-gold: "#D9B85F"
  laurel-light: "#F2DD93"
  bronze: "#9C7B3E"
  oracle-cyan: "#5EE6E0"
  oracle-cyan-light: "#A8F2EE"
  tier-silver: "#C7CEDB"
  tier-sage: "#86B5A6"
  ink-on-gold: "#1A1305"
typography:
  display:
    fontFamily: "Cinzel, serif"
    fontSize: "clamp(2.1rem, 4.5vw, 3.3rem)"
    fontWeight: 500
    lineHeight: 1.16
    letterSpacing: "normal"
  headline:
    fontFamily: "Cinzel, serif"
    fontSize: "clamp(1.25rem, 2.8vw, 1.55rem)"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.20em"
  title:
    fontFamily: "Cormorant Garamond, serif"
    fontSize: "clamp(1.15rem, 2.5vw, 1.35rem)"
    fontWeight: 600
    lineHeight: 1.28
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.92rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "Cinzel, serif"
    fontSize: "0.72rem"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "0.14em"
rounded:
  control: "8px"
  inset: "10px"
  chip: "12px"
  panel: "14px"
  pill: "999px"
spacing:
  micro: "6px"
  xs: "10px"
  sm: "14px"
  md: "20px"
  lg: "24px"
  section: "42px"
  hero: "54px"
components:
  button-primary:
    backgroundColor: "{colors.laurel-gold}"
    textColor: "{colors.ink-on-gold}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "14px 26px"
    height: "44px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.marble-ivory}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "14px 26px"
    height: "44px"
  card-news:
    backgroundColor: "{colors.oracle-panel}"
    textColor: "{colors.marble-ivory}"
    rounded: "{rounded.panel}"
    padding: "22px 24px 20px"
  input:
    backgroundColor: "rgba(217,184,95,.05)"
    textColor: "{colors.marble-ivory}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  topic-chip:
    backgroundColor: "rgba(217,184,95,.04)"
    textColor: "{colors.marble-ivory}"
    typography: "{typography.label}"
    rounded: "{rounded.chip}"
    padding: "12px 16px"
    height: "56px"
---

# Design System: Sibylla

## Overview

**Creative North Star: "La Pitonisa Cibernética"**

Sibylla se presenta como una inteligencia oracular que observa el presente para ayudar a vislumbrar el horizonte. La interfaz combina la gravedad de un archivo clásico con señales tecnológicas precisas: la claridad visionaria domina, mientras lo ceremonial enmarca la lectura sin volverla críptica.

El sistema debe separar la señal del ruido también en su forma. La densidad es editorial, no frenética; la jerarquía guía hacia hechos, fuentes y contexto sin competir por atención. La noche, el mármol, el oro y el cian construyen un observatorio sobrio donde cada destello tiene una función.

La anti-referencia es cualquier experiencia que ciegue en lugar de abrir los ojos: portales noticiosos genéricos, dashboards SaaS intercambiables, estética de casino, neón excesivo, gamificación o acumulación de estímulos sin jerarquía.

**Key Characteristics:**

- Oscuridad profunda como campo de observación, no como espectáculo.
- Oro para autoridad editorial y cian para señales del horizonte.
- Tipografía clásica para la voz y sans serif para la evidencia operativa.
- Capas translúcidas, bordes finos y resplandores ambientales contenidos.
- Controles instrumentales que nunca compiten con las noticias.

## Colors

La paleta enfrenta una base nocturna con materiales legibles y dos luces semánticas: oro para lo editorial y cian para lo emergente.

### Primary

- **Oro laurel** (`laurel-gold`, con `laurel-light` para el punto de luz): identifica marca, jerarquía editorial, acciones principales y estados seleccionados.

### Secondary

- **Cian oráculo** (`oracle-cyan`, con `oracle-cyan-light` para texto): marca futuro, tecnología, foco accesible y señales informativas; nunca debe inundar toda la superficie.

### Tertiary

- **Plata de fuente** (`tier-silver`) y **salvia de discusión** (`tier-sage`): distinguen niveles de confiabilidad sin convertirlos en una escala estridente.

### Neutral

- **Obsidiana nocturna** (`obsidian-night`): fondo dominante y horizonte visual.
- **Noche profunda** (`deep-night`) y **panel oracular** (`oracle-panel`): capas de navegación, tarjetas y diálogos.
- **Mármol marfil** (`marble-ivory`) y **mármol velado** (`marble-muted`): texto principal y secundario.
- **Piedra tenue** (`muted-stone`): metadatos y contenido subordinado.
- **Bronce antiguo** (`bronze`) y **borde oracular** (`oracle-border`): profundidad material, divisores y remates.

### Named Rules

**La regla de las dos luces.** El oro comunica autoridad editorial; el cian revela futuro, foco o información. No intercambiar sus papeles ni hacerlos competir en la misma jerarquía.

**La regla de la señal escasa.** Los acentos luminosos deben ser raros y funcionales; una pantalla saturada de oro o cian deja de separar señal de ruido.

## Typography

**Display Font:** Cinzel (con `serif` como respaldo)

**Body Font:** Inter (con `system-ui` y `sans-serif` como respaldo)

**Editorial Font:** Cormorant Garamond (con `serif` como respaldo)

**Character:** Cinzel aporta inscripción, autoridad y orientación; Cormorant Garamond introduce una voz editorial humana; Inter mantiene datos, controles y cuerpos nítidos. La combinación debe sentirse oracular pero legible, nunca historicista ni teatral.

### Hierarchy

- **Display** (500, `clamp(2.1rem, 4.5vw, 3.3rem)`, 1.16): declaraciones principales y horizonte de la portada.
- **Headline** (500, `clamp(1.25rem, 2.8vw, 1.55rem)`, 1.2): nombres de sección y divisiones estructurales.
- **Title** (600, `clamp(1.15rem, 2.5vw, 1.35rem)`, 1.28): titulares de noticias y contenido editorial.
- **Body** (400, `0.92rem`, 1.6): resúmenes, metadatos extendidos y texto funcional.
- **Label** (500, `0.72rem`, `0.14em`, mayúsculas): navegación, acciones compactas y taxonomías.

### Named Rules

**La regla de las tres voces.** Cinzel orienta, Cormorant narra e Inter informa; no añadir una cuarta familia ni usar una voz fuera de su función.

**La regla de la inscripción breve.** Las mayúsculas espaciadas pertenecen a etiquetas cortas; nunca a párrafos ni titulares largos.

## Layout

La portada usa un lienzo central de 1080px con 32px de margen interior. El hero combina contenido y símbolo en una retícula asimétrica de dos columnas (`minmax(0,1fr) 380px`) con 54px de separación; las noticias se organizan en dos columnas de igual peso con 20px entre tarjetas.

El ritmo alterna pausas amplias entre secciones (42px), encabezados lineales y bloques editoriales densos pero respirables. A 860px, hero, navegación y rejillas pasan a una sola columna; a 480px, el margen lateral baja a 16px, las separaciones a 14px y los controles preservan objetivos táctiles de al menos 30px. Las acciones principales mantienen una altura mínima de 44px.

**La regla del horizonte despejado.** Cada sección debe revelar con claridad título, línea guía y contenido; los controles permanecen agrupados y subordinados al encabezado.

## Elevation & Depth

El sistema es estratificado, no flotante. La profundidad nace de fondos translúcidos, desenfoque ambiental, bordes de baja opacidad y resplandores ligados al oro o al cian. Las sombras son ambientales en reposo y algo más expresivas solo durante foco, selección o hover.

### Shadow Vocabulary

- **Halo editorial** (`0 0 30px -8px rgba(217,184,95,.6)`): acción principal y autoridad de marca.
- **Elevación de tarjeta** (`0 22px 50px -26px rgba(217,184,95,.45), inset 0 0 30px rgba(217,184,95,.05)`): respuesta contenida al hover.
- **Elevación oracular** (`0 22px 50px -26px rgba(94,230,224,.5), inset 0 0 30px rgba(94,230,224,.05)`): variante futura o tecnológica.
- **Profundidad modal** (`0 40px 90px -30px rgba(0,0,0,.85), 0 0 70px -24px rgba(217,184,95,.35)`): separación excepcional para overlays.

### Named Rules

**La regla de profundidad ambiental.** Ninguna superficie se eleva con una sombra genérica; toda profundidad debe explicar capa, foco o estado.

## Shapes

Los paneles principales usan esquinas suavemente curvas (14px), los controles rectangulares 8px, los interiores 10px y los chips 12px. Círculos y cápsulas se reservan para sellos, selectores numéricos, avatares y conmutadores. Bordes de un píxel construyen estructura; una línea lateral de dos píxeles puede indicar procedencia o énfasis.

El meandro, el laurel y el portal circular son geometrías de firma. Deben actuar como instrumentos de orientación y memoria de marca, no como ornamento repetido en cada componente.

## Components

Los componentes son instrumentales y oraculares: precisos al operar, ceremoniales solo en los puntos que establecen jerarquía.

### Buttons

- **Shape:** rectángulo suavemente curvo (8px) y altura principal mínima de 44px.
- **Primary:** gradiente de Oro laurel, tinta oscura, Cinzel en mayúsculas y padding de 14px por 26px.
- **Hover / Focus:** desplazamiento vertical máximo de 1px, halo del color semántico y contorno de foco cian de 2px.
- **Secondary / Ghost:** fondo transparente, borde dorado tenue y texto marfil; el hover aclara el borde sin rellenar el control.

### Chips

- **Style:** cápsulas o rectángulos de 12px con borde fino, fondo apenas tonal y etiqueta de alta legibilidad.
- **State:** la selección gana oro; el modo futuro puede usar cian. El sello circular hace visible el estado sin depender solo del texto.

### Cards / Containers

- **Corner Style:** panel continuo de 14px con imagen recortada al borde superior.
- **Background:** Noche profunda translúcida sobre Obsidiana nocturna.
- **Shadow Strategy:** plana en reposo; elevación ambiental de 3px durante hover.
- **Border:** un píxel de Oro laurel a baja opacidad, con línea lateral semántica de 2px.
- **Internal Padding:** 22px por 24px, reducido a 18px por 16px en teléfono.

### Inputs / Fields

- **Style:** fondo dorado al 5%, borde fino, radio de 8px y texto marfil.
- **Focus:** borde Oro laurel claro con anillo exterior suave de 3px.
- **Error / Disabled:** error coral sobrio; deshabilitado por opacidad y sin elevación.

### Navigation

Cinzel no se usa aquí: la navegación emplea Inter en mayúsculas pequeñas, con espaciado generoso y color Mármol velado. El hover cambia a Oro laurel claro. En móvil se convierte en un panel nocturno vertical con blur y borde fino.

### News Card

La tarjeta es la ventana de observación principal: imagen 16:9, titular editorial, sello de confiabilidad, fuente, resumen y acciones. La fotografía informa; el tratamiento visual no debe competir con ella ni ocultar la procedencia.

### Sibylla Note

El apunte combina busto circular, línea dorada lateral y voz editorial en Cormorant Garamond cursiva. Es una intervención escasa de la pitonisa, nunca un bloque promocional repetitivo.

## Do's and Don'ts

### Do:

- **Do** preservar la jerarquía señal → fuente → contexto → acción en cada tarjeta.
- **Do** usar oro para autoridad editorial y cian para futuro, foco o información.
- **Do** mantener fondos oscuros, capas translúcidas y bordes finos como sistema principal de profundidad.
- **Do** respetar `prefers-reduced-motion` y mantener objetivos táctiles de al menos 44px para acciones principales.
- **Do** tratar fotografías, sellos de fuente y metadatos como evidencia, no como decoración.

### Don't:

- **Don't** introducir estética de casino, gamificación, neón excesivo o animaciones que compitan con la lectura.
- **Don't** convertir la interfaz en un portal noticioso genérico o un dashboard SaaS intercambiable.
- **Don't** llenar una superficie con oro y cian simultáneamente; la rareza de la señal es parte del sistema.
- **Don't** añadir tipografías, radios o sombras ajenos a las escalas documentadas sin una decisión explícita de rediseño.
- **Don't** editar `web/*.html` como fuente visual; los cambios durables pertenecen a las plantillas de `sibylla/templates/`.
