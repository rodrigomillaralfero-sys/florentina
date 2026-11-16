"""Conocimiento base.

Un corpus que el operador carga para una instancia (metodo, temario, biblioteca),
troceado e indexado con los mismos embeddings que la episodica, y recuperado por
turno en un bloque propio del prompt. Se carga, se versiona por hash y se
recupera; **no se aprende** ni se mezcla con la memoria de las personas.

`loader` trocea archivos y los escribe con version por hash (recargar sin cambios no
escribe nada). `base` busca por similitud con un umbral de pertinencia: bajo el
umbral, el bloque del prompt dice que no hay nada pertinente, y la instancia con
el corpus vacio corre exactamente igual.
"""
