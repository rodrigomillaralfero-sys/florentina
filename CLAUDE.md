# Florentina — instrucciones para Claude

- Antes de cualquier trabajo de sprint, leer `docs/florentina/INDICE.md` y seguirlo (arranque en la sección 6, cierre en la 7).
- `core/` no nombra instancias ni trae piezas del experimento de Vera; `tests/test_independence.py` lo exige. Nada del repo `vera-motor` entra aquí sin pasar por esa prueba.
- Antes de cerrar un sprint: `python -m pytest -q` en verde y la entrada en `docs/florentina/registro_sprints.md`.
