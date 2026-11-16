---
proyecto: florentina
tipo: origen_motor
descripcion: De dónde viene el motor de Florentina, qué trae, qué se dejó fuera a propósito, qué es nuevo y cómo se arma una instancia
version: v01
fecha: 2026-11-16
estado: vigente
reemplaza: puente_motor.md (v01, 16-11, del repo vera-motor)
fuente: repo vera-motor, rama mejoras, commit f5db905 (15-11-2026)
---

# Florentina — Origen del motor

Nov 16, 2026 · v01

El motor de Florentina se armó el 16-11 (FL0) copiando del motor de Vera solo las piezas genéricas que Florentina usa, depurándolas de todo lo propio de ese experimento. Desde ahí es código de Florentina: no se sincroniza con Vera y Vera no se toca (D19, D20).

Los comentarios del código conservan marcas como `F0.3` o `F0.8`. Son la historia de cómo nació cada mecanismo en el repo de Vera; se dejaron porque explican por qué existe cada resguardo.

## 1. Lo que se trajo

| Pieza | Dónde | Para qué le sirve a Florentina |
| --- | --- | --- |
| Motor del turno | `core/engine/loop.py` | Recibir, controles mecánicos, intención antes de enviar, pulsos, agrupar mensajes seguidos, recuperarse de caídas sin duplicar |
| Eventos append-only | `core/events/` | Historia permanente de cada conversación; nada se edita ni se borra |
| Scheduler idempotente | `core/scheduler/` | Seguimientos y procesos de fondo que sobreviven reinicios |
| Memoria episódica por persona | `core/memory/` | Un episodio por turno con embedding; recuperación por similitud y recencia, siempre de la misma persona; cierre de sesión por pausa |
| Conocimiento base | `core/knowledge/` | Cargar documentos, trocearlos, versionarlos por hash y buscarlos con umbral de pertinencia |
| Salud y topes | `core/health/` | Alertas, cortacircuitos (llamadas por hora, pulsos por día, costo diario), latido, doctor, OK diario |
| Medidor de gasto | `core/health/spend.py` | Toda llamada al modelo, de cualquier pieza, cuenta para el tope diario |
| Cifrado en reposo | `core/mind/` | Lo que se guarda de cada persona va cifrado con AES-256-GCM; registro de accesos |
| Almacenes | `core/storage/` | En memoria (pruebas) y Postgres con pgvector |
| Adaptadores de modelo | `adapters/` | OpenAI-compatible (OpenRouter u otro), Gemini nativo, modelos falsos para pruebas, alertas por Telegram |

## 2. Lo que se dejó fuera, a propósito

Todo lo que existe para el experimento de Vera: cartera de objetivos, escalera, medallero, desafío, puerta, "arriba" (evaluador), Estratega, impulso, crítico de estilo, vida propia, diario, libro, detector de pedidos, citas, revelación de cierre, hipótesis sobre el sujeto, identidad heredada, sellos y cuestionarios. Tampoco sus tablas: la migración base solo crea instancias, personas, eventos, episodios, conocimiento, jobs, salud y registro de accesos.

`tests/test_independence.py` falla si cualquiera de esas piezas o su vocabulario vuelve a aparecer en `core/`.

## 3. Ajustes al traerlo

- **Filtros de seguridad de Gemini en su valor por defecto.** El adaptador de Vera los apagaba (`BLOCK_NONE`). Florentina no lo necesita; ahora no se envían y rige lo que Google trae por defecto.
- **Roles de modelo:** solo conversación, clasificación y embeddings.
- **Tope de costo con alcance total por defecto:** el armado lo deja en `all`, así que cuenta todo lo que llama al modelo, no solo el turno.
- **La baja no revela nada:** el comando de baja corta el contacto y confirma; no hay resumen de cierre.

## 4. Lo nuevo de FL0

- **`core/assembly.py`, el armado genérico.** Recibe el contrato de una instancia y conecta todo lo común: almacenes, salud, medidor de gasto, memoria, conocimiento, motor y scheduler.
- **`core/engine/answer_turn.py`, la política de turno que responde.** Recupera (episodios de la persona más conocimiento), le pide los mensajes a la instancia, llama al modelo y deja la intención con lo que el cortacircuito cuenta. Si el modelo falla, usa el respaldo de la instancia.
- **`core/engine/commands.py`, controles por comando.** `/pausa`, `/pausa 2h`, `/reanudar` y baja, con textos que pone la instancia.
- **`instances/minima/`.** La prueba de transferencia: una instancia hecha solo llenando el contrato, sin tocar `core/`.

## 5. Cómo se arma una instancia (el contrato)

Una instancia entrega un `InstanceContract` y nada más:

| Campo | Qué es |
| --- | --- |
| `instance_id` | Su nombre en la base |
| `interaction` | Sus comandos y textos de confirmación (`CommandPolicy`) |
| `prompts` | Su armador de prompts: `respond`, `initiate` (None = no escribir por iniciativa propia) y `fallback` |
| `channel` | Su canal (Telegram en FL1) |
| `adapter` | Su modelo (None = eco, para probar canal y memoria) |
| `timezone`, `config` | Zona horaria y configuración (topes, memoria, conocimiento); claves documentadas en `core/assembly.py` |
| `alert_sink`, `heartbeat_url`, `extra_checks` | Por dónde avisa y qué más revisa el doctor |
| `consolidator` | Lo que corre al cerrar una sesión (en FL5, el acta) |

`assemble(contract, stores)` devuelve el motor completo. Las piezas propias que vengan después (verificador de citas, umbral de "no sé", registro de pendientes, cargador desde Drive, patrones) se enchufan sobre lo armado o como piezas genéricas nuevas del núcleo.

## 6. Brechas conocidas (para los sprints que vienen)

1. **Canal de una sola persona.** El adaptador de Telegram de Vera quedó fuera; FL1 trae uno propio. Atender a varias personas (alta, permisos, topes por persona, registro de accesos por persona) es FL6.
2. **Sin ficha de la persona ni acta de sesión.** El cierre de sesión ya llama a un consolidador; escribirlo es FL5.
3. **Sin estudio de patrones.** Nada del motor de Vera servía para esto; se diseña desde cero en FL7. El modelo de datos lo prevé: todo evento y episodio va con su persona y su fecha, así que el análisis por cliente, caso o fecha se puede hacer sobre lo acumulado.
4. **Índice vectorial por modelo.** La migración trae el índice para `openai/text-embedding-3-small` (1536 dimensiones). Si el bake-off (FL3) elige otro modelo de embeddings, se agrega su índice.
5. **Sin despliegue.** No hay Dockerfile ni configuración de hosting; es FL1.
