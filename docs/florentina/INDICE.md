---
proyecto: florentina
tipo: indice
descripcion: Puerta de entrada del proyecto Florentina — qué es, reglas de trabajo, tablero de sprints, arranque y cierre de cada sprint, mapa de documentos
actualizado: 2026-11-16
estado: vigente
---

# Florentina — Índice de trabajo

Todo chat de sprint parte leyendo este archivo. Lo mantiene Claude: se actualiza en el cierre de cada sprint, en el mismo commit que el código. Rodrigo valida; no edita archivos.

## 1. Qué es Florentina

Una asistente para las operaciones de i-CAR. Entrega información a partir de su conocimiento, aprende de cada persona que la usa, ve patrones en los problemas y estudia la información para dar mejores soluciones. Responde por Telegram, es profesional y mentora, cero manipulación. Piloto en los servicios de Rodrigo, con usuarias de i-CAR por etapas.

Es un proyecto propio, con repo, base y bot propios (D19). Su motor nació de las piezas genéricas del motor de Vera (canal, eventos, memoria, conocimiento, salud y topes), pero ya no lo comparte: no hay desafíos, medallas, cartera, Estratega ni nada de ese experimento. Ver `origen_motor.md`.

## 2. Reglas de trabajo

- Un chat por sprint. El contexto compartido es esta carpeta (`docs/florentina/`), no la memoria del chat.
- Las decisiones de diseño se resuelven de a una con Rodrigo, como preguntas abiertas y explicadas en simple, antes de construir. No se entregan briefs cerrados.
- Antes de construir: validación del sprint y del modelo. Se construye con Opus; la revisión en frío con Fable, solo con autorización expresa de Rodrigo.
- Claude avanza en todo lo que puede hacer solo y deja la guía como apoyo. A Rodrigo le llegan decisiones, no tareas operativas.
- Tiempos, plazos y cálculos los computa el código; a la IA le toca el juicio.
- En tareas largas: avisos de avance y aviso inmediato si algo se corta.
- No se borra la historia: la guarda git. Las decisiones reemplazadas se marcan en `decisiones.md`, no se eliminan.
- El núcleo (`core/`) es de Florentina y no sabe de ninguna instancia: `tests/test_independence.py` lo exige. Todo cambio al núcleo queda descrito en la entrada del sprint en `registro_sprints.md`.
- Los ingresos al conocimiento de Florentina son solo de Rodrigo: él valida y orquesta (D9).
- Nada del experimento de Vera entra a este repo: ni código de sus mecánicas, ni prompts, ni memoria, ni registros.

## 3. Realidad de tiempos (16-11)

Los sprints los ejecuta Claude: el código de un sprint es del orden de un día (FL0 se armó y validó en una tarde). El calendario no lo manda la construcción; lo mandan tres relojes:

1. **Decisiones y validación de Rodrigo**, de a una, antes de construir.
2. **Gestiones de terceros**: la cuenta de servicio de solo lectura sobre Drive depende de quien administra el Workspace de i-CAR.
3. **Ventanas de piloto, que no se comprimen**: FL4, una semana de uso real; FL5, 2–4 semanas con métricas; FL7 necesita casos ya acumulados.

La planificación se hace por dependencias y ventanas de piloto, no por semanas de desarrollo.

## 4. Tablero de sprints

El detalle de cada sprint (para qué, alcance, criterio de término, decisiones) está en `hoja_de_ruta.md`. Aquí solo el estado.

| Sprint | Nombre | Estado | Rama | Cierre |
| --- | --- | --- | --- | --- |
| FL0 | Costura | Cerrado | `fl0-costura` | 16-11 |
| FL1 | Casa propia | **Siguiente** | `fl1-casa-propia` | — |
| FL2 | Biblioteca | Pendiente · espera cuenta de servicio de Drive | — | — |
| FL3 | Bake-off | Pendiente · espera preguntas de la comercial | — | — |
| FL4 | Primera respuesta | Pendiente · espera personalidad y pendientes P/T | — | — |
| FL5 | Memoria personal | Pendiente | — | — |
| FL6 | Varias voces | Pendiente | — | — |
| FL7 | Memoria de equipo | Pendiente | — | — |
| FL8 | Lectura de la operación | Pendiente · alcance por decidir | — | — |

## 5. Lo que depende de Rodrigo

Solo lo que Claude no puede hacer por él.

| Qué | Bloquea a | Estado |
| --- | --- | --- |
| Crear el repo privado y vacío `rodrigomillaralfero-sys/florentina` en GitHub | Subir FL0 | Hecho (16-11) |
| Validar FL0 para unir `fl0-costura` a `main` | FL1 | Hecho (16-11) |
| Pedir la cuenta de servicio de Drive (solo lectura, carpeta Florentina) | FL2 | Sin pedir |
| Pedir a la comercial sus 15–20 preguntas operativas más frecuentes | FL3 | Sin pedir |
| Definir la personalidad de Florentina | FL4 | Abierto |
| Resolver pendientes P1–P18 (guías) y T1–T7 (alzamiento Tanner) | FL4 | Abierto |
| Documentar alzamientos, prendas y prohibiciones fuera de Tanner (P11) | FL4 | Abierto |
| Subir el resto de la documentación a `Entradas` | FL2 | Abierto |

## 6. Cómo se abre un sprint

Rodrigo abre un chat nuevo en el proyecto Florentina y escribe una línea:

> Abrimos FLn. Repo rodrigomillaralfero-sys/florentina. Lee docs/florentina/INDICE.md y sigue desde ahí.

Con eso, Claude:

1. Clona el repo y toma la rama del sprint que indica el tablero (si no existe, la crea desde `main`).
2. Lee este índice, `hoja_de_ruta.md` (sección del sprint), `decisiones.md`, `origen_motor.md` y la última entrada de `registro_sprints.md`.
3. Corre las pruebas para partir de una base verde.
4. Revisa el estado real del código para cada punto del alcance y se lo muestra a Rodrigo.
5. Saca las decisiones de diseño de a una.
6. Pide validación del sprint y del modelo, y recién entonces construye.

## 7. Cómo se cierra un sprint

En la rama del sprint, en el commit de cierre, Claude:

1. Actualiza el tablero (sección 4) y la tabla de dependencias (sección 5).
2. Registra en `decisiones.md` las decisiones tomadas y marca las reemplazadas.
3. Ajusta `hoja_de_ruta.md` si el alcance de los sprints siguientes cambió.
4. Agrega la entrada del sprint a `registro_sprints.md` (qué se hizo, qué quedó fuera, cambios al núcleo, pruebas, costo).
5. Actualiza `origen_motor.md` si cambió lo que el motor ofrece.
6. Le muestra el resumen a Rodrigo y espera su validación antes de unir la rama.

## 8. Mapa de documentos

| Archivo | Qué contiene | Cambia |
| --- | --- | --- |
| `INDICE.md` | Este archivo: qué es, reglas, tablero, arranque y cierre | En cada cierre |
| `hoja_de_ruta.md` | Etapas y sprints FL0–FL8 con alcance y criterio de término | Si cambia el alcance |
| `decisiones.md` | Decisiones D1 en adelante y preguntas abiertas | Con cada decisión |
| `origen_motor.md` | De dónde viene el motor, qué trae, qué se dejó fuera y cómo se arma una instancia | Cuando cambie lo que el motor ofrece |
| `registro_sprints.md` | Una entrada por sprint cerrado | En cada cierre |
| `prompt_apertura.md` | Versión original (16-11) de las instrucciones y del prompt de FL0, de cuando el código iba en el repo de Vera. La sección 6 de este índice la reemplaza | No |

Los nombres con fecha y versión que citan `hoja_de_ruta.md` y `decisiones.md` son los de los archivos originales del 13 y 16-11; aquí viven con nombre fijo y la versión la lleva git.

**Fuera del repo, a propósito:** el índice de conocimiento de las guías G00–G13, las fichas de alzamiento Tanner y el registro de fuentes son contenido de i-CAR. Viven en la carpeta Florentina del Drive de i-CAR (D10, D16) y entran a Florentina por el cargador de FL2. El documento de Tanner tiene además su clasificación SGSI por confirmar (T7).
