---
proyecto: florentina
tipo: decisiones
descripcion: Decisiones de diseño de Florentina, asistente de operaciones de i-CAR con motor y repo propios
version: v01
fecha: 2026-11-13
actualizado: 2026-11-16
estado: abierto
---

# Florentina — Decisiones de diseño

Nov 13, 2026 · v01 (actualizado 16-11)

## Marco

- Asistente que entrega información a partir de su conocimiento, aprende de cada persona, ve patrones de problemas y estudia la información para dar mejores soluciones (Rodrigo, 16-11). Personalidad completamente nueva: cercana, profesional, de mentora y apoyo en operaciones.
- Cero manipulación. Nada del experimento de Vera (arriba, escalera, desafío, elegida, diario, libro, vida propia), ni cartera, medallero, Estratega, impulso persuasivo o reintento tras un "no".
- Proyecto totalmente fuera de Vera: repo, base y bot propios (D19). Del motor de Vera se trajo solo lo genérico: canal, eventos, memoria, conocimiento base, salud y topes, adaptador de modelo, scheduler (`origen_motor.md`).
- ~~Prerrequisito técnico: mover la semántica del M2 desde `core/` a `instances/vera/`, interruptor por pieza y armado genérico en el núcleo.~~ Reemplazado por D20 (16-11): no hace falta, porque Florentina no comparte el motor.

## Decisiones

| # | Fecha | Tema | Decisión de Rodrigo |
| --- | --- | --- | --- |
| D1 | 13-11 | Canal | Telegram |
| D2 | 13-11 | Iniciativa | Responde, hace seguimiento y valida cómo le fue al usuario con su respuesta |
| D3 | 13-11 | Aprender del usuario | Entiende lo que hace el usuario: si le dan un caso, sabe de qué se habla o recuerda qué pasó y con qué cliente; usa la memoria para su eficiencia y para entender patrones |
| D4 | 13-11 | Personalidad | Profesional |
| D5 | 13-11 | Dónde vive | Piloto, en los servicios propios de Rodrigo |
| D6 | 13-11 | Usuarios del piloto | Gente de i-CAR, por etapas: primero una comercial, después la jefa de customer leads; etapa 2, los KAM |
| D7 | 13-11 | Memoria | Primero personal (por usuario); después grupal |
| D8 | 13-11 | Conocimiento que crece | Cuando Florentina no encuentra la respuesta, hay que armarla y crearla: es parte de su función y es necesario para que el documento siga creciendo |
| D9 | 13-11 | Quién ingresa y valida | Los ingresos al conocimiento son solo de Rodrigo: él valida y orquesta. No hay retroalimentación automática del manual. Autorizó a Claude a crear un índice distinto si indexa mejor |
| D10 | 13-11 | Repositorio de documentos | Carpeta "Florentina" en el Google Drive de i-CAR (cuenta rodrigo.millar@icar.cl), con subcarpeta "Entradas" para lo nuevo. Ahí va toda la documentación, sea del SGSI o no |
| D11 | 13-11 | Nivel de acceso | En el piloto todas las fuentes son internas. Liberar a clientes requiere versiones filtradas preparadas aparte, no marcar secciones de documentos internos. El campo de marca queda en el diseño desde el inicio |
| D12 | 13-11 | Tarifas | Visibles para todo el personal; el uso es solo para precios (no se cargan costos ni márgenes) |
| D13 | 16-11 | Proyecto | Florentina tiene su propio proyecto en claude.ai, separado del de Vera. ~~El código sigue en el mismo repo del motor~~ (reemplazado por D19) |
| D14 | 16-11 | Crecimiento del conocimiento | Toda la información va a crecer desde los Drive. El diseño de la biblioteca (FL2) tiene que soportar ese crecimiento: el Drive es la fuente, y ni el repo ni el proyecto de claude.ai guardan conocimiento de i-CAR |
| D15 | 16-11 | Documentación del proyecto | La documentación de trabajo (índice, hoja de ruta, decisiones, origen del motor, registro de sprints) vive en `docs/florentina/` del repo y la mantiene Claude en el cierre de cada sprint. No se guardan sprints en el proyecto de claude.ai, porque evolucionan |
| D16 | 16-11 | Una sola entrada | El conocimiento entra solo por la carpeta Florentina del Drive de i-CAR (confirma D10). La cuenta de servicio se pide de solo lectura sobre esa carpeta y nada más |
| D17 | 16-11 | Centralizar | Todo se centraliza en la carpeta Florentina, incluidas las planillas que hoy son de otras personas y están compartidas con Rodrigo (tarifas, Gestiones Manuales). Falta definir cómo entran sin dejar de actualizarse |
| D18 | 16-11 | Planillas compartidas | Entran por planilla espejo dentro de la carpeta Florentina, que trae del original solo las columnas necesarias (en tarifas, solo precios). La arma Claude en FL2 y Rodrigo la autoriza |
| D19 | 16-11 | Repo propio | Florentina vive totalmente fuera de Vera: repo propio `rodrigomillaralfero-sys/florentina` (privado), con su propia copia de las piezas genéricas del motor. No se sincroniza con el motor de Vera. Reemplaza la parte de D13 sobre el código |
| D20 | 16-11 | Alcance de FL0 | Florentina no necesita Estratega, desafíos, medallas ni nada del experimento. FL0 trae solo las piezas que usa; mover el M2 dentro del repo de Vera y los interruptores por pieza dejan de ser parte de Florentina. Vera no se toca |
| D21 | 16-11 | Lo que Vera no tiene | Reconocer a distintas usuarias y estudiar patrones de problemas son piezas propias de Florentina, a diseñar desde cero (FL6 y FL7). El modelo de datos las prevé desde la base: todo va con su persona y su fecha |

## Consecuencias técnicas de D6–D7

- Desde el segundo usuario se necesita lo multiusuario mínimo del anexo de escala: alta por persona, memoria aislada por usuario, topes por usuario.
- La memoria grupal (por cliente y caso) entra después, con la regla del anexo: nada de lo privado de un usuario se usa con otro.

## Conocimiento base (13-11)

- Fuentes cargadas: Guías Operativas i-CAR G00–G13 (edición 09-11-2026, 14 PDF, 168 páginas) y SGSI-D-010 Alzamiento Tanner V13 (47 láminas). Las 15 están en Florentina/Entradas.
- Fuentes vinculadas registradas: planilla de tarifas (Análisis precio-costo por clientes Prime) y planilla Gestiones Manuales (dato de la operación, no manual).
- Método de tres niveles: catálogo (una línea por documento), fichas con etiquetas (tipo, para quién, alcance general o de cliente) y trozos. El contexto por respuesta no crece con la biblioteca.
- Índice de Florentina v01: `2026-11-13_florentina_indice_conocimiento_v01.md` (pendientes P1–P18).
- Catálogo y fichas de alzamiento Tanner v01: `2026-11-13_florentina_fichas_alzamiento_tanner_v01.md` (pendientes T1–T7).
- Registro de fuentes y modelo de acceso: `2026-11-13_florentina_registro_fuentes_v01.md`.
- Con D9, el ciclo de D8 queda así: Florentina no encuentra → lo dice y registra la pregunta pendiente (caso, cliente, fecha); la lista de pendientes le llega a Rodrigo; Rodrigo arma la respuesta y la ingresa. Florentina no escribe en el manual.
- Flujo de carga (propuesto): Rodrigo deja archivos en Entradas o vincula fuentes vivas → se procesan (catálogo, fichas, pendientes, choques con lo cargado) → Rodrigo aprueba; las versiones anteriores se archivan, no se borran.

## Modos de uso planteados por Rodrigo (13-11)

Rodrigo aclara que no tiene del todo clara la ventaja de la memoria; a priori la ve como búsqueda de patrones y de mejoras.

1. Alguien reporta un error y Florentina ve en su memoria patrones de error: levanta a qué se debe y si es masivo.
2. Una persona pregunta por temas específicos de un cliente y Florentina detecta que puede ser una incidencia: la levanta.
3. Florentina detecta, conversando, que la persona necesita apoyo en un área específica, y le ayuda con eso.
4. Florentina se da cuenta (por lo que le dice la KAM) de que un cliente pide algo específico en ciertas fechas, y se prepara antes.

Contexto de la primera usuaria: la comercial es la que más lo necesita. El cliente no separa por área y recurre a ella, como primer contacto, por temas operativos; hoy ella va a preguntar a operaciones. Lo ideal es que se autoatienda.

Política de i-CAR (Rodrigo, 13-11): las personas no se evalúan por saber más o menos, sino por saber conseguir la respuesta; nadie tiene por qué saber todo, pero sí responder bien y ágil a los clientes. El modo 3 es apoyo, no evaluación.

## Abiertas

- Resolver los pendientes P1–P18 del índice y T1–T7 de alzamiento Tanner.
- Por D18: verificar en FL2 que la planilla espejo se actualice sin que alguien la abra, antes de depender de ella.
- Por D14 y D9: cómo aprueba Rodrigo cuando el volumen crezca (documento por documento, por lote, o fuentes de confianza que entran solas y se revisan después).
- Si Florentina cita la fuente en sus respuestas por Telegram (por ejemplo, "Guía G01, p. 8").
- Aviso a cada usuaria al darla de alta: qué guarda Florentina, para qué, y quién ve qué.
- Qué le llega a Rodrigo del modo 3: temas agregados donde el equipo necesita apoyo (insumo para capacitación y para el manual) o detalle por persona.
- A quién y por dónde "levanta" Florentina una incidencia o un patrón (modos 1 y 2).
- Si Florentina lee datos de la operación de i-CAR (estado de trámites, historial) o solo lo que le cuentan; uso de la planilla Gestiones Manuales (análisis agregado propuesto como quick win).
- En fuentes vivas: si cada cambio hecho por otra persona pasa por aprobación de Rodrigo o se confía en ciertas fuentes (por ejemplo, las del SGSI).
- Preguntas frecuentes de la comercial (15–20 operativas) para completar el conocimiento base y el bake-off.
- Qué datos entran al piloto (identificación de casos sin datos personales de los dueños de vehículos).
- Modelo: Gemini 3.8 Flash como candidato, a validar en bake-off con 30–50 preguntas reales (incluidas algunas sin respuesta en el manual). Confirmar que lee las imágenes de los PDF (P16) y de las presentaciones.
