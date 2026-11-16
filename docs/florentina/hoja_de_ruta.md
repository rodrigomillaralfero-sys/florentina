---
proyecto: florentina
tipo: hoja_de_ruta
descripcion: Índice del proyecto Florentina — etapas, sprints con alcance y criterio de término, trabajo de contenido de Rodrigo y documentos del proyecto
version: v01
fecha: 2026-11-13
actualizado: 2026-11-16
estado: propuesta para validación de Rodrigo
---

# Florentina — Hoja de ruta e índice del proyecto

Nov 13, 2026 · v01

## Qué es Florentina

Una asistente para las operaciones de i-CAR: entrega información a partir de su conocimiento, aprende de cada persona, ve patrones de problemas y estudia la información para dar mejores soluciones. Profesional, mentora, cero manipulación. Responde por Telegram, recuerda a cada persona y sus casos y hace seguimiento. Piloto en los servicios de Rodrigo, con usuarias de i-CAR por etapas. Proyecto propio, con repo, base y bot propios (D19).

## Forma de trabajo

- Un chat por sprint, con este proyecto como contexto compartido.
- Las decisiones de diseño se resuelven de a una con Rodrigo, antes de construir.
- Antes de construir, Claude pide validación del sprint y el cambio de modelo.
- Se construye con Opus. La revisión en frío con Fable se hace solo si Rodrigo la autoriza expresamente.
- Tiempos, plazos y cálculos los computa el código; a la IA le toca el juicio.
- Florentina no comparte código, base ni bot con Vera (D19). Nada del experimento de Vera entra a este proyecto.

---

## Etapas y sprints

| Etapa | Sprint | Nombre | En una línea |
| --- | --- | --- | --- |
| 0 · Base | FL0 | Costura | Florentina tiene su propio motor, limpio de todo lo de Vera, y una instancia mínima que corre |
| 0 · Base | FL1 | Casa propia | Florentina corre en su propia infraestructura: app, base y bot |
| 1 · Conocimiento | FL2 | Biblioteca | Los documentos de i-CAR se cargan, se indexan y se actualizan con aprobación de Rodrigo |
| 1 · Conocimiento | FL3 | Bake-off | Se elige el modelo con preguntas reales de i-CAR |
| 2 · Piloto comercial | FL4 | Primera respuesta | Florentina contesta por Telegram, cita, dice "no sé" y registra lo que falta |
| 2 · Piloto comercial | FL5 | Memoria personal | Recuerda a la usuaria, sus casos y clientes; hace seguimiento |
| 3 · Equipo | FL6 | Varias voces | Más de una usuaria, cada una aislada; reportes para Rodrigo |
| 4 · Patrones | FL7 | Memoria de equipo | Memoria por cliente y caso; detecta errores masivos, incidencias y estacionalidades |
| 4 · Patrones | FL8 | Lectura de la operación | Usa los datos de la operación (planillas) para patrones y, si se decide, consultas de estado |

Usuarias por etapa: etapa 2, la comercial; etapa 3, se suma la jefa de customer leads; etapa 4, los KAM.

---

### FL0 · Costura

**Para qué:** que Florentina tenga su propio motor, con solo lo que usa, sin nada del experimento de Vera.

**Alcance (rehecho el 16-11 por D19 y D20)**
- Repo propio `rodrigomillaralfero-sys/florentina`.
- Traer del motor de Vera solo las piezas genéricas (canal, eventos, memoria por persona, conocimiento base, scheduler, adaptador de modelo con costo, salud y topes), depuradas de todo lo del experimento, con sus pruebas.
- Armado genérico en el núcleo: una instancia entrega su contrato y el núcleo conecta lo común.
- Prueba de transferencia: una instancia mínima creada solo llenando el contrato, sin tocar `core/`.
- Prueba de independencia que falle si aparece vocabulario o una pieza del experimento.

**Listo cuando:** la instancia mínima conversa, recuerda por persona sin mezclar, usa el conocimiento base y respeta los topes, en memoria y sobre Postgres real; el núcleo pasa la prueba de independencia.

**Fuera de alcance:** cualquier cambio en el repo de Vera. Mover el M2 dentro de ese repo y los interruptores por pieza ya no son parte de Florentina (D20).

**Estado:** construido el 16-11; ver `registro_sprints.md`.

### FL1 · Casa propia

**Para qué:** que Florentina viva separada de Vera desde el primer día.

**Alcance**
- App, base de datos y bot de Telegram propios (el repo ya es propio desde FL0).
- Adaptador de Telegram propio, que identifique a cada persona por su chat (base para FL6).
- Configuración de la instancia `instances/florentina/` sobre el contrato de FL0, y Dockerfile y hosting.
- Llave de modelo con tope propio; Gemini por API pagada.
- Latido, alertas, cortacircuito de costo diario.
- Canal de operación para Rodrigo (aprobaciones, alertas, pendientes).
- Pedido a quien administra el Workspace de i-CAR: cuenta de servicio de solo lectura sobre la carpeta Florentina y las fuentes vivas.

**Listo cuando:** Florentina responde "hola" por su bot, con alertas y topes funcionando, sin compartir nada con Vera.

**Decisiones:** nombre del bot; tope de costo diario.

### FL2 · Biblioteca

**Para qué:** que el conocimiento crezca sin que crezca el contexto, y que nada entre sin aprobación de Rodrigo.

**Alcance**
- Cargador desde Drive: copias (`Entradas`) y fuentes vinculadas (`Fuentes_vivas`).
- Catálogo, fichas con etiquetas (tipo, para quién, alcance, nivel) y trozos con búsqueda.
- Revisión nocturna de fuentes vivas: solo reprocesa lo que cambió.
- Comparación contra la versión anterior y resumen para aprobar; versiones reemplazadas se archivan, no se borran.
- Filtro de acceso aplicado por código (todo "interna" en el piloto; sin marca = restringida).
- Carga parcial por columnas (tarifas: solo precios).
- Lectura de imágenes dentro de PDF y presentaciones (pendiente P16).

**Listo cuando:** las 15 fuentes actuales y la planilla de tarifas están cargadas y aprobadas; un cambio en una fuente viva llega como resumen a Rodrigo al día siguiente.

**Quick win opcional:** análisis agregado de la planilla Gestiones Manuales para saber qué trámites manuales son más frecuentes y cuáles faltan en el manual (prioriza qué documentar).

### FL3 · Bake-off

**Para qué:** elegir el modelo con datos de i-CAR y no con rankings ajenos.

**Alcance**
- Set de 30–50 preguntas reales, entre ellas unas 5 sin respuesta en el manual.
- Candidatos: Gemini 3.8 Flash, DeepSeek V4.1 Flash, Qwen3.6-35B.
- Medir: aciertos, invenciones, "no sé" bien puestos, lectura de imágenes, costo y tiempo por respuesta.

**Listo cuando:** Rodrigo elige el modelo con la tabla de resultados.

**Depende de:** las preguntas frecuentes de la comercial.

### FL4 · Primera respuesta

**Para qué:** que la comercial pueda autoatenderse.

**Alcance**
- Núcleo de personalidad (profesional, mentora, cero manipulación), definido por Rodrigo.
- Conversación por Telegram con búsqueda filtrada por acceso.
- Verificación de citas y umbral de "no sé" calculado por código.
- Ante contradicciones del manual: muestra ambas fuentes, no elige.
- Pregunta sin respuesta → registro de pendiente (caso, cliente, fecha) → lista para Rodrigo.
- Alta de la usuaria con aviso: qué se guarda, para qué y quién ve qué.

**Listo cuando:** la comercial usa Florentina una semana; Rodrigo recibe la lista de pendientes y el costo real.

**Decisiones:** si Florentina muestra la fuente en sus respuestas; texto del aviso de alta.

### FL5 · Memoria personal

**Para qué:** que no haya que repetirle el contexto y que Florentina acompañe.

**Alcance**
- Actas por sesión y ficha de la usuaria (casos, clientes, temas donde se traba).
- Continuidad entre conversaciones ("el caso de Kaufmann de ayer").
- Seguimiento y validación: "¿cómo te fue con eso?" (D2), sin insistencia.
- Modo 3: detectar dónde necesita apoyo y ayudarla.

**Listo cuando:** el piloto con la comercial cumple 2–4 semanas con métricas (consultas, "no sé", pendientes resueltos, costo por usuaria).

**Decisiones:** qué le llega a Rodrigo del modo 3 (agregado o por persona).

### FL6 · Varias voces

**Para qué:** sumar a la jefa de customer leads sin mezclar memorias.

**Alcance**
- Multiusuario mínimo del anexo de escala: alta por persona, memoria aislada, topes por usuaria, registro de accesos.
- Roles (comercial, customer leads, KAM, operaciones).
- Reporte periódico para Rodrigo: temas más consultados, huecos del manual, costo.

**Listo cuando:** dos usuarias en paralelo, con prueba de que la memoria de una no aparece en la otra.

### FL7 · Memoria de equipo

**Para qué:** que lo que aprende con una persona sirva al equipo.

**Alcance**
- Memoria grupal por cliente y caso, con la regla del anexo: nada de lo privado de una persona se usa con otra.
- Modo 1: errores que se repiten y si son masivos.
- Modo 2: posibles incidencias de un cliente.
- Modo 4: pedidos de clientes que se repiten en ciertas fechas (las fechas las calcula el código).

**Listo cuando:** Florentina levanta al menos un patrón real validado por Rodrigo.

**Decisiones:** a quién y por dónde levanta incidencias y patrones.

### FL8 · Lectura de la operación

**Para qué:** que los patrones no dependan solo de lo que le cuentan.

**Alcance**
- Análisis agregado periódico de planillas de operación (Gestiones Manuales y otras).
- Opcional, por decisión de Rodrigo: consulta en vivo del estado de una gestión ("¿en qué está la patente X?"), con minimización de datos.

**Listo cuando:** según la decisión de alcance.

**Decisiones:** si se conecta a la operación y con qué datos.

---

## Trabajo de contenido (Rodrigo, en paralelo)

No es código, pero define cuánto sabe Florentina.

| Qué | Para antes de |
| --- | --- |
| Resolver pendientes P1–P18 (guías) y T1–T7 (alzamiento Tanner) | FL4 |
| Documentar alzamientos, prendas y prohibiciones fuera de Tanner (P11) | FL4 |
| Subir el resto de la documentación a `Entradas` y vincular fuentes vivas | FL2 |
| Pedir a la comercial sus 15–20 preguntas operativas más frecuentes | FL3 |
| Definir la personalidad de Florentina | FL4 |
| Gestionar la cuenta de servicio de Drive con quien administra el Workspace | FL2 |

## Fuera de alcance por ahora

- Versiones para clientes (requieren documentos filtrados preparados aparte).
- Voz.
- Migrar el piloto a cuentas de i-CAR.
- Escala a muchos usuarios (webhook, réplicas).

## Documentos del proyecto

Desde el 16-11 los documentos de trabajo viven en `docs/florentina/` del repo con nombre fijo (ver `INDICE.md`, sección 8). Esta tabla queda como registro de los archivos originales.

| Documento | Qué contiene |
| --- | --- |
| `2026-11-13_florentina_hoja_de_ruta_v01.md` | Este índice: etapas, sprints y alcances |
| `2026-11-13_florentina_decisiones_v01.md` | Decisiones D1–D10 y preguntas abiertas |
| `2026-11-13_florentina_indice_conocimiento_v01.md` | Índice de las guías G00–G13 y pendientes P1–P18 |
| `2026-11-13_florentina_fichas_alzamiento_tanner_v01.md` | Catálogo, fichas de alzamiento Tanner y pendientes T1–T7 |
| `2026-11-13_florentina_registro_fuentes_v01.md` | Registro de fuentes y modelo de acceso |
| `2026-11-16_florentina_puente_motor_v01.md` | Lo que Florentina necesita saber del motor compartido con Vera (reemplazado por `origen_motor.md`, D19) |
| `2026-11-16_florentina_prompt_apertura_v01.md` | Prompt para abrir cada chat de sprint en el proyecto Florentina |
