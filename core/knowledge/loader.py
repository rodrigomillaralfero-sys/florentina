"""Cargador del conocimiento base: carpeta -> trozos -> almacen (con version por hash) -> embeddings.

Reglas:

* Se leen `*.md` y `*.txt` (recursivo). El frontmatter YAML de los `.md` se descarta.
* Trozos por parrafos, de hasta `max_chars`; un parrafo mas largo se parte por oraciones.
  Cada trozo lleva el nombre de la fuente al inicio, para que el embedding sepa de que habla.
* La version de una fuente es el hash de su contenido. Recargar sin cambios no escribe nada;
  un archivo cambiado produce una version nueva (la anterior queda, pero deja de recuperarse);
  un archivo borrado retira la fuente.
* Los embeddings se piden por lotes y solo para trozos vigentes sin vector del modelo actual.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.events.store import utcnow
from core.interfaces.model_adapter import ModelAdapter, ModelRole
from core.knowledge.store import KnowledgeStore
from core.memory.episodic import split_usage

log = logging.getLogger(__name__)

EXTENSIONS = (".md", ".txt")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class LoadReport:
    sources_seen: list[str] = field(default_factory=list)
    sources_written: list[str] = field(default_factory=list)  # version nueva escrita
    sources_unchanged: list[str] = field(default_factory=list)
    sources_retired: list[str] = field(default_factory=list)
    chunks_written: int = 0
    embeddings_written: int = 0

    def render(self) -> str:
        return (
            f"fuentes: {len(self.sources_seen)} vistas, {len(self.sources_written)} nuevas/cambiadas, "
            f"{len(self.sources_unchanged)} sin cambios, {len(self.sources_retired)} retiradas · "
            f"trozos escritos: {self.chunks_written} · embeddings escritos: {self.embeddings_written}"
        )


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()


def chunk_text(text: str, *, max_chars: int = 900, title: str | None = None) -> list[str]:
    """Trozos por parrafos de hasta `max_chars`. Un parrafo mas largo se parte por oraciones."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        current = ""
        for sentence in _SENTENCE_END.split(p):
            if current and len(current) + 1 + len(sentence) > max_chars:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + 2 + len(piece) > max_chars:
            chunks.append(buf)
            buf = piece
        else:
            buf = f"{buf}\n\n{piece}".strip()
    if buf:
        chunks.append(buf)
    prefix = f"[{title}] " if title else ""
    return [prefix + c for c in chunks]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def scan_dir(root: Path) -> dict[str, str]:
    """{nombre relativo: texto sin frontmatter} de los archivos cargables, ordenado por nombre."""
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in EXTENSIONS and not path.name.startswith((".", "_")) and path.name.lower() != "readme.md":
            out[path.relative_to(root).as_posix()] = strip_frontmatter(path.read_text(encoding="utf-8"))
    return out


class KnowledgeLoader:
    def __init__(
        self,
        *,
        instance_id: str,
        store: KnowledgeStore,
        adapter: ModelAdapter | None,
        max_chars: int = 900,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._store = store
        self._adapter = adapter
        self._max_chars = max_chars
        self._clock = clock

    async def load_dir(self, root: Path, *, embed: bool = True) -> LoadReport:
        report = LoadReport()
        now = self._clock()
        files = scan_dir(root)
        known = {s.source: s for s in await self._store.sources(self.instance_id)}
        for name, text in files.items():
            report.sources_seen.append(name)
            if not text.strip():
                continue
            chunks = chunk_text(text, max_chars=self._max_chars, title=Path(name).stem)
            src, created = await self._store.put_source(
                self.instance_id, name, content_hash=content_hash(text), chunks=chunks, now=now
            )
            if created:
                report.sources_written.append(name)
                report.chunks_written += len(chunks)
            else:
                report.sources_unchanged.append(name)
        for name, src in known.items():
            if name not in files and src.content_hash is not None:
                if await self._store.retire_source(self.instance_id, name, now=now):
                    report.sources_retired.append(name)
        if embed:
            report.embeddings_written = await self.embed_missing()
        log.info("conocimiento base: %s", report.render())
        return report

    async def embed_missing(self, *, batch: int = 64) -> int:
        if self._adapter is None:
            return 0
        model = self._adapter.identity(ModelRole.EMBEDDINGS).model
        written = 0
        while True:
            missing = await self._store.missing_embeddings(self.instance_id, model=model, limit=batch)
            if not missing:
                return written
            res = await self._adapter.embed([c.text for c in missing])
            per_item = split_usage(res.usage, len(missing))
            progressed = 0
            for chunk, vec in zip(missing, res.vectors):
                if await self._store.set_embedding(chunk.id, model=res.identity.model, dim=res.dim, vector=vec, usage=per_item):
                    written += 1
                    progressed += 1
            if progressed == 0:
                return written  # evita un bucle si el almacen no registra lo escrito
