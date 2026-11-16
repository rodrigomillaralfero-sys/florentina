---
proyecto: florentina
tipo: prompt_apertura
descripcion: Instrucciones del proyecto Florentina en claude.ai y prompt para abrir cada chat de sprint (el primero, FL0 Costura)
version: v01
fecha: 2026-11-16
estado: reemplazado (16-11) por la sección 6 de INDICE.md; el código ya no va en el repo de Vera (D19)
---

# Florentina — Instrucciones del proyecto y prompt de apertura

Nov 16, 2026 · v01

## 1. Instrucciones del proyecto (pegar en la configuración del proyecto en claude.ai)

```
Proyecto Florentina: segunda instancia del motor de Vera, con personalidad propia (profesional, mentora y apoyo en operaciones de i-CAR, cero manipulación). Responde por Telegram con el conocimiento de i-CAR, recuerda a cada persona y sus casos, hace seguimiento y detecta patrones. Piloto en servicios de Rodrigo, usuarias de i-CAR por etapas.

Documentos base: hoja de ruta (etapas y sprints FL0–FL8), decisiones (D1 en adelante), puente con el motor, índice de conocimiento, fichas de alzamiento Tanner y registro de fuentes. Leerlos antes de proponer.

Reglas de trabajo:
- Un chat por sprint. Las decisiones de diseño se resuelven de a una con Rodrigo, como preguntas abiertas; no entregar briefs cerrados.
- Antes de construir, pedir validación del sprint y el cambio de modelo. Se construye con Opus; la revisión en frío con Fable solo con autorización expresa de Rodrigo.
- No dar por entendido lo técnico: explicar en qué se traduce en la práctica antes de pedir una decisión.
- Tiempos, plazos y cálculos los computa el código; a la IA le toca el juicio.
- En tareas largas, avisos de avance periódicos y aviso inmediato si algo se corta.
- No se borra la historia: las versiones reemplazadas se marcan, no se eliminan.
- El código vive en el repo rodrigomillaralfero-sys/vera-motor (rama mejoras). Todo cambio a core/ se anota en la bitácora del núcleo y no altera la conducta de Vera.
- Los documentos de i-CAR viven en la carpeta "Florentina" del Google Drive de i-CAR (cuenta rodrigo.millar@icar.cl). No se usa contenido del experimento de Vera.
```

## 2. Prompt de apertura del primer chat (FL0 · Costura)

```
Abrimos el sprint FL0 · Costura de Florentina.

Contexto: lee la hoja de ruta, las decisiones y el puente con el motor de este proyecto. FL0 deja el motor listo para una segunda mente sin tocar la conducta de Vera.

Alcance de FL0 (hoja de ruta):
1. Mover la semántica del M2 (desafío, puerta del N3, N5, elegida, cortado, arriba) desde core/goals/challenge.py y core/overseer/ a instances/vera/, dejando en el núcleo solo el mecanismo genérico.
2. Un interruptor por pieza del motor (confirmar cuáles faltan: impulso, medallero, cartera, Estratega).
3. Subir lo genérico del armado (instances/vera/wiring.py) al núcleo.
4. Unir las ramas mejoras y main.
5. Crear la bitácora del núcleo en el repo.
6. Prueba de transferencia: una instancia mínima creada solo llenando el contrato, sin tocar core/.

Listo cuando: la instancia mínima corre sin código de Vera; las pruebas de Vera siguen en verde; Vera en producción no cambia de conducta.

Antes de construir:
- Clona el repo, revisa el estado real de cada punto y muéstrame qué encontraste.
- Sácame las decisiones de diseño como preguntas abiertas, de a una, explicadas en simple.
- Pídeme validación del sprint y el cambio de modelo.
```

## 3. Para los chats siguientes

Mismo esquema: "Abrimos el sprint FLn · Nombre", alcance y criterio de término copiados de la hoja de ruta, y las mismas tres condiciones antes de construir. Al cerrar cada sprint: registro del sprint en el proyecto y actualización de la hoja de ruta y de las decisiones.
