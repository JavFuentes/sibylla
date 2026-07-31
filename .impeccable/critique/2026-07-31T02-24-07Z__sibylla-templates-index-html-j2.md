---
target: portada de Sibylla
total_score: 25
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 4
timestamp: 2026-07-31T02-24-07Z
slug: sibylla-templates-index-html-j2
---
# Crítica Impeccable — portada de Sibylla

## Impresión general

Sibylla posee una identidad visual propia y disciplinada: obsidiana, oro y cian; Cinzel, Cormorant e Inter; el portal, el meandro y la voz de la pitonisa construyen una experiencia reconocible y coherente con «separar señal del ruido». La arquitectura de las tarjetas también protege la trazabilidad: fuente, fecha, resumen y enlace original se entienden con rapidez.

La principal fricción aparece cuando la interfaz deja de leer y empieza a administrar. La primera visita pide decisiones antes de demostrar valor, cada sección expone controles compactos de configuración y algunos cambios de estado no se comunican a teclado o lector de pantalla. La mejora adecuada es de precisión y jerarquía, no un rediseño.

## Salud de diseño — heurísticas de Nielsen

| # | Heurística | Puntaje | Evidencia principal |
|---|---|---:|---|
| 1 | Visibilidad del estado | 2/4 | Fecha, contadores y Restaurar informan; falta interpretar una edición atrasada y anunciar cambios de orden/cantidad. |
| 2 | Correspondencia con el mundo real | 3/4 | «Resumen», «Original» y los modos son claros; I/II/III exigen una leyenda distante. |
| 3 | Control y libertad | 2/4 | La personalización es reversible, pero la primera visita bloquea la lectura y algunos diálogos no restituyen el foco. |
| 4 | Consistencia y estándares | 3/4 | Sistema visual cohesivo; el selector `+ N −` contradice el orden documentado `− N +`. |
| 5 | Prevención de errores | 3/4 | Buenos estados deshabilitados y Restaurar; controles pequeños y próximos elevan el riesgo de ocultar/mover por accidente. |
| 6 | Reconocimiento antes que recuerdo | 2/4 | Acciones de lectura tienen texto; iconos de layout y tiers dependen de memoria o de la leyenda del pie. |
| 7 | Flexibilidad y eficiencia | 3/4 | Orden, modo, cantidad y persistencia son fuertes; leer y editar no están claramente separados. |
| 8 | Diseño estético y minimalista | 3/4 | Identidad y tarjetas están muy bien resueltas; la repetición y los controles permanentes añaden ruido. |
| 9 | Recuperación de errores | 2/4 | Hay alertas y Restaurar; al ocultar una sección el foco puede desaparecer y varios diálogos manejan foco de forma desigual. |
| 10 | Ayuda y documentación | 2/4 | Acerca y GitHub explican el sistema; la confiabilidad no se explica donde se elige qué leer. |
| **Total** | | **25/40 — Aceptable** | Base visual sólida; mejoras significativas de acceso, estado y jerarquía. |

## Veredicto de especificidad

**Pasa.** La implementación expresa un producto específico: la estética de archivo clásico y observatorio tecnológico, la voz «Sibylla observa», los sellos de confiabilidad y la ruta constante a la fuente original no son intercambiables con un portal genérico. La identidad se diluye únicamente cuando la larga secuencia de tarjetas y herramientas de layout domina sobre el criterio editorial. La oportunidad no es añadir ornamento, sino volver visible cómo la pitonisa separó la señal.

## Carga cognitiva

Resultado: **alta, 5 de 8 criterios en riesgo**. El onboarding reúne intereses, orden, modo y acciones antes de la primera lectura. Los encabezados presentan cantidad, subir, bajar y quitar de manera simultánea. La agrupación visual y la divulgación progresiva de resúmenes funcionan bien, pero la administración de la portada compite con el recorrido editorial.

## Qué funciona

- Identidad oracular consistente y memorable, sin estética de casino ni portal genérico.
- Tarjetas orientadas a evidencia: fotografía, procedencia, fecha, resumen y original.
- Personalización potente, persistente y reversible; degradación estática sin Firebase.
- Buenas bases técnicas: controles nativos, nombres accesibles, foco visible en muchos elementos, imágenes diferidas y espacio reservado.

## Prioridades

### 1. [P1] Entregar valor antes de pedir personalización

La primera visita abre un diálogo modal y exige elegir entre múltiples intereses y modos antes de leer. Ofrecer «Ver la portada de hoy» como ruta primaria inicial, conservar «Personalizar» para más tarde y restaurar el foco cuando el diálogo se abre desde navegación.

### 2. [P1] Hacer inequívoca la frescura de la edición

Una edición antigua mantiene el pulso cian que parece indicar actividad. A partir de un umbral explícito, detener el pulso, presentar «Actualización atrasada» y mantener la fecha y la procedencia visibles. La interfaz no debe parecer más fresca que sus datos.

### 3. [P1] Completar navegación y diálogos por teclado

El menú móvil usa un `label role=button` sin comportamiento de Enter/Espacio ni estado expandido; onboarding, auth y boletín no comparten una política consistente de foco, Escape, trampa de Tab y restitución. Usar un botón real para el menú y normalizar el ciclo de foco de cada modal.

### 4. [P1] Reducir errores y anunciar la personalización

Los controles de 28–30 px están muy próximos; cambiar cantidad, mover u ocultar no produce anuncio accesible y ocultar puede dejar el foco en un nodo invisible. Elevar áreas interactivas, adoptar el orden `− N +`, añadir una región de estado y trasladar el foco a un punto estable tras ocultar.

### 5. [P2] Acercar transparencia y estructura al punto de lectura

Los tiers I/II/III dependen de la leyenda del footer; las secciones visibles comienzan en `h3` después de un `h1`; microtexto funcional cae por debajo de la escala documentada y `prefers-reduced-motion` no neutraliza el scroll suave ni todos los zooms. Explicar el tier junto a la fuente, corregir la jerarquía a `h2`/`h3`, elevar el microtexto y completar la alternativa de movimiento reducido.

## Personas y señales de riesgo

**Jordan — primera visita:** llega a leer y recibe un diálogo con numerosas decisiones; los tiers no son autoexplicativos y una edición antigua puede parecer activa. Riesgo: escoger sin entender o abandonar antes de ver una noticia.

**Casey — móvil distraído:** el onboarding exige scroll y los controles de sección se agrupan en objetivos pequeños; es fácil tocar «quitar» cuando quería cambiar cantidad. Las tarjetas en una columna y las acciones principales son una base buena.

**Sam — teclado, baja visión o motricidad limitada:** encuentra controles nativos y foco visible, pero el menú móvil puede quedar inaccesible, los diálogos manejan foco de forma inconsistente y los cambios de layout son silenciosos.

## Observaciones menores

- El detector produjo 98 avisos: la mayoría son deuda de tokens; Inter y la línea lateral del apunte son falsos positivos justificados por `DESIGN.md`.
- Los tamaños inferiores a `.72rem` sí contradicen la escala documentada para texto funcional.
- El HTML servido es considerable para una portada estática, pero las imágenes están diferidas y Firebase es progresivo; medir antes de optimizar a ciegas.
- La captura muestra una identidad fuerte incluso en una sección intermedia: la fotografía domina y el marco ceremonial no compite con la noticia.

## Preguntas provocadoras

1. Si Sibylla ya seleccionó la señal, ¿por qué la primera interacción exige configurar antes de leer?
2. ¿Puede el sello explicar «por qué confiar» en dos segundos, sin enviar al lector al footer?
3. ¿Los controles sociales y de layout permanecen suficientemente subordinados a la lectura?
4. ¿Cómo debe comunicar Sibylla que hoy no pudo renovar su horizonte sin erosionar confianza?
