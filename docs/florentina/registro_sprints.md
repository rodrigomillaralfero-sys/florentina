---
proyecto: florentina
tipo: registro_sprints
descripcion: Una entrada por sprint cerrado — qué se hizo, qué quedó fuera, cambios al núcleo, pruebas y costo
actualizado: 2026-11-16
estado: vigente
---

# Florentina — Registro de sprints

## FL0 · Costura — 16-11-2026 (cerrado y validado por Rodrigo)

**Qué cambió en el camino.** El sprint abrió con el alcance original (mover el M2 de Vera fuera de su núcleo, interruptores por pieza, unir ramas del repo de Vera). Rodrigo lo corrigió: Florentina no es Vera, no necesita nada de ese experimento y vive totalmente fuera de él. Quedaron D19 (repo propio), D20 (FL0 trae solo lo que Florentina usa) y D21 (usuarias distintas y patrones son piezas propias).

**Qué se hizo**
- Repo nuevo con el núcleo de Florentina, armado desde `vera-motor` (rama `mejoras`, commit f5db905) con solo las piezas genéricas: motor del turno, eventos, scheduler, memoria episódica por persona, conocimiento base, salud y topes, medidor de gasto, cifrado en reposo, almacenes en memoria y Postgres, y adaptadores de modelo.
- Depuración de esas piezas: fuera pedidos, objetivos, medallero, rendición semanal, revelación de cierre, impulso, hipótesis, notas propias y roles de modelo de Vera. La migración base solo crea las tablas que se usan.
- Armado genérico en el núcleo (`core/assembly.py`), política de turno que responde (`core/engine/answer_turn.py`) y controles por comando (`core/engine/commands.py`).
- Instancia mínima (`instances/minima/`) armada solo con el contrato.
- Documentación movida a este repo y actualizada: índice, hoja de ruta (FL0 rehecho, FL1 ajustado), decisiones D19–D21, `origen_motor.md` (reemplaza al puente), este registro.

**Ajuste de criterio, validado (D22):** el adaptador de Gemini venía con los filtros de seguridad apagados (`BLOCK_NONE`), cosa que necesitaba Vera. Para Florentina quedan los filtros por defecto de Google.

**Qué quedó fuera**
- Canal de Telegram, configuración de `instances/florentina/`, Dockerfile y hosting: FL1.
- Acta de sesión y ficha de la persona: FL5. El cierre de sesión ya llama al consolidador que entregue la instancia.
- Varias usuarias con alta y permisos: FL6. Estudio de patrones: FL7.
- Nada se tocó en el repo `vera-motor`. Ahí quedó la rama `fl0-costura` con la documentación de Florentina del 16-11; desde hoy la fuente es este repo.

**Pruebas:** 78 en verde, 62 heredadas o adaptadas del motor de Vera y 16 nuevas. Entre ellas:
- prueba de transferencia: conversa, recuerda por persona sin mezclar, usa el conocimiento, respeta comandos y topes, responde el respaldo si cae el modelo y cierra la sesión con el consolidador;
- independencia: falla ante vocabulario o piezas del experimento en `core/`;
- Postgres real con pgvector y cifrado: migraciones idempotentes, texto cifrado en la base, doctor en verde y reinicio sin duplicar.

La prueba sobre Postgres corre con `TEST_DATABASE_URL`; sin ella, se salta.

**Costo:** sin llamadas a modelos reales (todo con modelos falsos). Construcción: una tarde.
