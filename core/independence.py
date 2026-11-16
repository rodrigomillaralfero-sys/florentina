"""Verificacion de independencia del nucleo.

Dos chequeos, porque buscar un nombre no basta:

1. **Vocabulario**: ningun archivo de `core/` contiene los terminos que la
   instancia declara como propios (nombres, dominio, proveedor de canal...).
2. **Dependencias**: ningun modulo de `core/` importa `instances.*` ni
   `adapters.*`, ni librerias de proveedores concretos (canal, modelo).
   El nucleo depende solo de sus interfaces y de la libreria estandar, mas
   el driver de base de datos en `core.storage.postgres`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# Raices que el nucleo nunca importa. Las librerias de proveedores concretos
# (canal, modelo) las agrega quien llama, porque sus nombres son vocabulario
# de instancia y no pueden vivir aqui.
DEFAULT_FORBIDDEN_IMPORTS: tuple[str, ...] = ("instances", "adapters")
ALLOWED_THIRD_PARTY: dict[str, tuple[str, ...]] = {
    # modulo relativo a core/ -> imports de terceros permitidos ahi
    "storage/postgres.py": ("psycopg",),
    "storage/pg.py": ("psycopg",),
    "mind/cipher.py": ("cryptography",),  # F0.8: primitiva criptografica generica (AES-GCM), no un proveedor
}


@dataclass
class IndependenceReport:
    vocabulary_hits: list[tuple[str, int, str]] = field(default_factory=list)  # (archivo, linea, termino)
    import_hits: list[tuple[str, str]] = field(default_factory=list)  # (archivo, modulo)

    @property
    def ok(self) -> bool:
        return not self.vocabulary_hits and not self.import_hits

    def render(self) -> str:
        lines: list[str] = []
        for path, lineno, term in self.vocabulary_hits:
            lines.append(f"vocabulario: {path}:{lineno} contiene '{term}'")
        for path, mod in self.import_hits:
            lines.append(f"import: {path} importa '{mod}'")
        return "\n".join(lines) if lines else "core/ independiente"


def _py_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def check_vocabulary(core_root: Path, forbidden_terms: Sequence[str]) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    patterns = [(t, re.compile(re.escape(t), re.IGNORECASE)) for t in forbidden_terms]
    for path in _py_files(core_root):
        rel = str(path.relative_to(core_root))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for term, pat in patterns:
                if pat.search(line):
                    hits.append((rel, lineno, term))
    return hits


STDLIB_AND_CORE: tuple[str, ...] = ("core",)


def _is_stdlib(root: str) -> bool:
    import sys

    return root in sys.stdlib_module_names or root in sys.builtin_module_names


def check_imports(
    core_root: Path,
    forbidden_roots: Sequence[str] = DEFAULT_FORBIDDEN_IMPORTS,
    *,
    strict_third_party: bool = True,
) -> list[tuple[str, str]]:
    """Imports prohibidos en core/.

    Con `strict_third_party` (default) cualquier import que no sea stdlib, `core`
    ni este en la lista blanca de su archivo cuenta como violacion: el nucleo no
    depende de proveedores, depende de interfaces.
    """
    hits: list[tuple[str, str]] = []
    for path in _py_files(core_root):
        rel = str(path.relative_to(core_root))
        allowed = ALLOWED_THIRD_PARTY.get(rel, ())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in allowed:
                    continue
                if root in forbidden_roots:
                    hits.append((rel, name))
                elif strict_third_party and root not in STDLIB_AND_CORE and not _is_stdlib(root):
                    hits.append((rel, name))
    return hits


def check_independence(
    core_root: Path, forbidden_terms: Sequence[str], forbidden_roots: Sequence[str] = DEFAULT_FORBIDDEN_IMPORTS
) -> IndependenceReport:
    return IndependenceReport(
        vocabulary_hits=check_vocabulary(core_root, forbidden_terms),
        import_hits=check_imports(core_root, forbidden_roots),
    )
