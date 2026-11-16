"""Salud tecnica del motor (F0.2b).

Principio 9 de la spec: lo que se cae, avisa. Ningun fallo tecnico queda
solo en un log. Cuatro piezas, todas genericas (la instancia pone el canal
de alertas, los topes y los chequeos de sus proveedores):

* `alerts`    — alertas al operador por un sink aparte, con dedupe.
* `breakers`  — cortacircuitos: topes de llamadas, pulsos, costo y fallos
                seguidos; al superarse el motor queda quieto hasta `release`.
* `heartbeat` — latido periodico a un servicio externo que avisa si se corta.
* `doctor`    — autochequeo (base, ficha, jobs, latido, cortacircuito, mas los
                chequeos que inyecta la instancia) y el "OK" diario.
* `log`       — registro append-only de latidos y corridas del doctor.
"""
