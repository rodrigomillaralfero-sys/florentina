# Florentina

Asistente de operaciones de i-CAR: entrega información a partir de su conocimiento, aprende de cada persona, ve patrones de problemas y estudia la información para dar mejores soluciones. Por Telegram, profesional y mentora, cero manipulación.

- **Empezar por** `docs/florentina/INDICE.md`: qué es, reglas, tablero de sprints, cómo se abre y se cierra cada sprint.
- **Motor:** `core/` es genérico y no sabe de ninguna instancia (`tests/test_independence.py`). De dónde viene y qué trae: `docs/florentina/origen_motor.md`.
- **Instancias:** `instances/<nombre>/` llenan el contrato de `core/assembly.py`. Hoy existe `minima` (prueba de transferencia); `florentina` llega en FL1.

## Desarrollo

```
pip install -e ".[dev]"
python -m pytest -q
```

Con Postgres (pgvector): aplicar `python scripts/migrate.py` con `DATABASE_URL` y correr las pruebas con `TEST_DATABASE_URL`.
