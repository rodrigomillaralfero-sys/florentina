"""El nucleo no sabe de ninguna instancia ni trae piezas de otro proyecto.

Tres chequeos:
1. Vocabulario: `core/` no nombra instancias, proveedores ni conceptos que no son de este motor.
2. Dependencias: `core/` importa solo interfaces, la libreria estandar y lo de la lista blanca.
3. Piezas: no existen en `core/` los modulos de mecanicas que este motor no tiene.
"""

from __future__ import annotations

from pathlib import Path

from core.independence import check_imports, check_independence, check_vocabulary

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"

# Vive en la prueba, no en core/: si estuviera en core/, core/ ya lo contendria.
FORBIDDEN_TERMS = [
    # instancias, personas y proveedores
    "vera", "florentina", "rodrigo", "i-car", "icar", "telegram", "openrouter", "supabase",
    # mecanicas de otro proyecto que este motor no tiene
    "seduc", "elegida", "desafio", "medall", "cartera", "estratega", "strategist", "escalera", "escalon",
    "overseer", "impulso", "genesis", "experimento",
]
FORBIDDEN_IMPORT_ROOTS = ["instances", "adapters", "telegram", "httpx", "openai", "supabase"]
FORBIDDEN_PACKAGES = ["goals", "portfolio", "strategist", "overseer", "impulse", "requests", "attempts", "watch",
                      "engagement", "experiment", "life", "critic", "seals", "hypotheses", "identity", "traces", "execution"]


def test_core_has_no_foreign_vocabulary():
    hits = check_vocabulary(CORE, FORBIDDEN_TERMS)
    assert hits == [], "\n".join(f"{p}:{n} -> {t}" for p, n, t in hits)


def test_core_imports_only_interfaces_stdlib_and_the_whitelist():
    hits = check_imports(CORE, FORBIDDEN_IMPORT_ROOTS)
    assert hits == [], "\n".join(f"{p} importa {m}" for p, m in hits)


def test_core_has_no_foreign_pieces():
    present = [p for p in FORBIDDEN_PACKAGES if (CORE / p).exists()]
    assert present == [], f"piezas que no son de este motor: {present}"


def test_full_report_is_clean():
    report = check_independence(CORE, FORBIDDEN_TERMS, FORBIDDEN_IMPORT_ROOTS)
    assert report.ok, report.render()


def test_the_checker_actually_detects_things(tmp_path: Path):
    bad = tmp_path / "core"
    bad.mkdir()
    (bad / "x.py").write_text("import telegram\nimport yaml\nfrom instances.minima import algo\nFASE = 'desafio'\n", encoding="utf-8")
    report = check_independence(bad, FORBIDDEN_TERMS, FORBIDDEN_IMPORT_ROOTS)
    assert not report.ok
    assert any(t == "desafio" for _, _, t in report.vocabulary_hits)
    assert ("x.py", "telegram") in report.import_hits
    assert ("x.py", "instances.minima") in report.import_hits
    assert ("x.py", "yaml") in report.import_hits, "cualquier tercero fuera de la lista blanca es violacion"
