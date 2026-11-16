"""Motor de aprendizaje reutilizable.

El motor conoce eventos, politicas, decisiones y resultados. No conoce ningun
dominio: ni el nombre de la instancia, ni el del sujeto, ni la semantica de
lo que se pide. Todo eso entra por las seis costuras (`core.interfaces`) y lo
define cada instancia en `instances/<nombre>/`.

Regla de independencia: nada dentro de `core/` importa `instances.*` ni
`adapters.*`, y ningun archivo de `core/` contiene vocabulario de una
instancia concreta. `core.independence` lo verifica y `tests/test_independence.py`
lo exige.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
