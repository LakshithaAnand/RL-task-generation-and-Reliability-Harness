"""Stage 10a — Hardening registry loader + liveness check.

pipeline/hardening_registry.json records every verifier exploit the pipeline
has discovered: what it was, when, the scaffold change that closed it, the
battery probe that guards against regression, and the precondition under which
the entry applies.

The invariant enforced here: EVERY registry entry names a probe that is live
in the Stage-8 shortcut battery. A defense whose guarding probe disappears is
an untested defense — the battery refuses to start (and this module's CLI
exits nonzero) rather than report untested coverage.

Usage: python -m pipeline.registry
Exit 0 iff the registry parses and every entry's guarding probe is live.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().parent / "hardening_registry.json"

_REQUIRED_ENTRY_KEYS = (
    "id", "discovered", "description", "scaffold_change",
    "guarding_probe", "applicability_precondition",
)


class RegistryError(RuntimeError):
    """The hardening registry is malformed or names a dead probe."""


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(path.read_text())
    for key in ("version", "updated_at", "entries"):
        if key not in registry:
            raise RegistryError(f"registry missing top-level key {key!r}")
    for entry in registry["entries"]:
        missing = [k for k in _REQUIRED_ENTRY_KEYS if not entry.get(k)]
        if missing:
            raise RegistryError(
                f"registry entry {entry.get('id', '<no id>')!r} missing {missing}")
    return registry


def assert_probes_live(registry: dict[str, Any], live_probes: set[str]) -> None:
    """Fail loudly if any registry entry's guarding probe is not in the battery."""
    dead = [(e["id"], e["guarding_probe"]) for e in registry["entries"]
            if e["guarding_probe"] not in live_probes]
    if dead:
        raise RegistryError(
            "hardening registry names probe(s) not live in the shortcut battery: "
            + ", ".join(f"{eid} -> {probe}" for eid, probe in dead)
            + f" (live: {sorted(live_probes)})")


def main(argv: list[str] | None = None) -> int:
    from pipeline.integrity import PROBES

    try:
        registry = load_registry()
        assert_probes_live(registry, set(PROBES))
    except RegistryError as e:
        print(f"REGISTRY CHECK FAILED: {e}", file=sys.stderr)
        return 1
    print(f"hardening registry v{registry['version']} "
          f"(updated {registry['updated_at']}): "
          f"{len(registry['entries'])} entr{'y' if len(registry['entries']) == 1 else 'ies'}")
    for entry in registry["entries"]:
        print(f"  {entry['id']}: discovered {entry['discovered']}, "
              f"guarding probe '{entry['guarding_probe']}' is LIVE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
