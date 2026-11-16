"""Memoria del motor.

* `episodes`  — la episodica recuperable: un episodio por turno, con texto y embedding,
                anclado al evento de intencion (append-only, idempotente), siempre por persona.
* `episodic`  — el gancho que escribe el episodio al cerrar cada turno y lo embebe.
* `recall`    — recuperacion por turno: busqueda vectorial + recencia sobre los episodios de la
                persona y conocimiento base pertinente. Produce items tipados; la instancia los redacta.
* `sessions`  — cierre de sesion por pausa; corre el consolidador que ponga la instancia.

El motor no sabe que dice un episodio: sabe que existe, de quien es y cuando ocurrio.
"""
