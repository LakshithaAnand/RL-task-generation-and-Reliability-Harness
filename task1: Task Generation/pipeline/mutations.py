"""Stage 2 — Metadata-driven mutation candidate generation.

Exactly two templates, both AST-driven, zero LLM involvement:
  - boundary_flip:       swap a relational operator with its boundary counterpart
                         (> <-> >=, < <-> <=)
  - inverted_condition:  negate an if/while condition (test -> not (test))

Candidate sites are discovered in the seed's source (never its tests), selected
with a seeded RNG, and each candidate is written as artifacts/candidates/<id>/ with:
  - mutation.patch   (healthy -> broken, unified diff)
  - oracle.patch     (broken -> healthy; the guaranteed-good solution)
  - metadata.json    (template metadata: intended behavior, changed condition,
                      edge cases, instruction-safe wording template)

Reversibility is verified in-memory before anything is written: applying the two
edits in sequence must reproduce the original file byte-for-byte.

Usage: python -m pipeline.mutations [--config pipeline/seeds.toml]
Exit 0 iff the configured number of candidates was generated.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import difflib
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    CANDIDATES_DIR,
    ELIGIBILITY_REPORT_PATH,
    Config,
    Seed,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "mutation"

_BOUNDARY_SWAPS: dict[type[ast.cmpop], tuple[str, str]] = {
    ast.Lt: ("<", "<="),
    ast.LtE: ("<=", "<"),
    ast.Gt: (">", ">="),
    ast.GtE: (">=", ">"),
}

_TEMPLATE_SPECS: dict[str, dict[str, Any]] = {
    "boundary_flip": {
        "description": (
            "Swaps a relational operator with its boundary-inclusive/exclusive "
            "counterpart, so exactly the boundary value is treated incorrectly."
        ),
        "edge_cases": ["below_boundary", "at_boundary", "above_boundary"],
        "instruction_wording_template": (
            "A boundary-condition bug was introduced in the `{package}` library: "
            "one comparison now treats a boundary value incorrectly, producing "
            "wrong results for edge-case inputs. Find the faulty comparison and "
            "restore correct boundary handling."
        ),
    },
    "inverted_condition": {
        "description": (
            "Negates a branch condition (if/while), so the program takes the "
            "wrong branch whenever the condition is evaluated."
        ),
        "edge_cases": ["condition_true_path", "condition_false_path"],
        "instruction_wording_template": (
            "A logic bug was introduced in the `{package}` library: one branch "
            "condition is inverted, so the code takes the wrong path in a "
            "specific situation. Find the inverted condition and correct it."
        ),
    },
}


@dataclasses.dataclass(frozen=True)
class Site:
    """One mutable location in the seed's source."""

    template: str
    rel_path: str
    lineno: int
    col: int
    abs_start: int          # absolute char offset of the text being replaced
    abs_end: int
    original_text: str      # exact text being replaced
    mutated_text: str       # its replacement
    context_expr: str       # the enclosing expression/condition, for metadata
    enclosing_function: str

    @property
    def sort_key(self) -> tuple[str, int, int]:
        return (self.rel_path, self.lineno, self.col)


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _abs_offset(offsets: list[int], lineno: int, col: int) -> int:
    return offsets[lineno - 1] + col


class _SiteVisitor(ast.NodeVisitor):
    """Collects boundary_flip and inverted_condition sites with exact char spans."""

    def __init__(self, source: str, rel_path: str) -> None:
        self.source = source
        self.rel_path = rel_path
        self.offsets = _line_offsets(source)
        self.sites: list[Site] = []
        self._func_stack: list[str] = []

    # -- function context tracking -------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def _func(self) -> str:
        return self._func_stack[-1] if self._func_stack else "<module>"

    # -- boundary_flip ---------------------------------------------------------
    def visit_Compare(self, node: ast.Compare) -> None:
        if len(node.ops) == 1 and type(node.ops[0]) in _BOUNDARY_SWAPS:
            original_op, mutated_op = _BOUNDARY_SWAPS[type(node.ops[0])]
            left, right = node.left, node.comparators[0]
            if left.end_lineno is not None and left.end_col_offset is not None:
                start = _abs_offset(self.offsets, left.end_lineno, left.end_col_offset)
                end = _abs_offset(self.offsets, right.lineno, right.col_offset)
                region = self.source[start:end]
                idx = self._find_op(region, original_op)
                if idx is not None:
                    expr = ast.get_source_segment(self.source, node) or ""
                    self.sites.append(
                        Site(
                            template="boundary_flip",
                            rel_path=self.rel_path,
                            lineno=node.lineno,
                            col=node.col_offset,
                            abs_start=start + idx,
                            abs_end=start + idx + len(original_op),
                            original_text=original_op,
                            mutated_text=mutated_op,
                            context_expr=expr,
                            enclosing_function=self._func(),
                        )
                    )
        self.generic_visit(node)

    @staticmethod
    def _find_op(region: str, op: str) -> int | None:
        """Locate the operator token in the inter-operand region, unambiguously."""
        idx = region.find(op)
        if idx == -1:
            return None
        if op in ("<", ">") and idx + 1 < len(region) and region[idx + 1] == "=":
            return None  # AST said strict but text shows <=/>=; positions unreliable
        return idx

    # -- inverted_condition ------------------------------------------------------
    def visit_If(self, node: ast.If) -> None:
        self._add_condition_site(node.test)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._add_condition_site(node.test)
        self.generic_visit(node)

    def _add_condition_site(self, test: ast.expr) -> None:
        # Single-line conditions only: keeps patches one-line and unambiguous.
        if test.lineno != test.end_lineno or test.end_col_offset is None:
            return
        seg = ast.get_source_segment(self.source, test)
        if not seg:
            return
        start = _abs_offset(self.offsets, test.lineno, test.col_offset)
        end = _abs_offset(self.offsets, test.end_lineno, test.end_col_offset)
        if self.source[start:end] != seg:
            return
        self.sites.append(
            Site(
                template="inverted_condition",
                rel_path=self.rel_path,
                lineno=test.lineno,
                col=test.col_offset,
                abs_start=start,
                abs_end=end,
                original_text=seg,
                mutated_text=f"not ({seg})",
                context_expr=seg,
                enclosing_function=self._func(),
            )
        )


def discover_sites(source: str, rel_path: str) -> list[Site]:
    """All candidate mutation sites in one source file, in stable order."""
    visitor = _SiteVisitor(source, rel_path)
    visitor.visit(ast.parse(source))
    return sorted(visitor.sites, key=lambda s: s.sort_key)


def apply_site(source: str, site: Site) -> str:
    """Apply a single-site mutation to source text."""
    assert source[site.abs_start : site.abs_end] == site.original_text
    return source[: site.abs_start] + site.mutated_text + source[site.abs_end :]


def revert_site(mutated: str, site: Site) -> str:
    """Invert a single-site mutation (the oracle direction)."""
    assert mutated[site.abs_start : site.abs_start + len(site.mutated_text)] == site.mutated_text
    return (
        mutated[: site.abs_start]
        + site.original_text
        + mutated[site.abs_start + len(site.mutated_text) :]
    )


def unified_diff(a: str, b: str, rel_path: str) -> str:
    lines = difflib.unified_diff(
        a.splitlines(keepends=True),
        b.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    )
    return "".join(lines)


def validate_site(source: str, site: Site) -> str | None:
    """Return a rejection reason, or None if the site yields a valid mutation."""
    mutated = apply_site(source, site)
    if mutated == source:
        return "mutation is a no-op"
    try:
        ast.parse(mutated)
    except SyntaxError as e:
        return f"mutated source does not parse: {e}"
    if revert_site(mutated, site) != source:
        return "round-trip (mutate then revert) did not reproduce original bytes"
    return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_metadata(seed: Seed, site: Site, candidate_id: str, rng_seed: int) -> dict[str, Any]:
    spec = _TEMPLATE_SPECS[site.template]
    if site.template == "boundary_flip":
        changed_condition = {"from": site.original_text, "to": site.mutated_text}
        intended_behavior = (
            f"In {site.rel_path}:{site.lineno} ({site.enclosing_function}), the "
            f"comparison `{site.context_expr}` must use `{site.original_text}`. "
            f"The mutated operator `{site.mutated_text}` differs exactly at the "
            f"boundary value, which must follow the original semantics."
        )
    else:
        changed_condition = {
            "from": site.original_text,
            "to": site.mutated_text,
        }
        intended_behavior = (
            f"In {site.rel_path}:{site.lineno} ({site.enclosing_function}), the "
            f"branch condition must be `{site.original_text}` (not its negation). "
            f"Control flow must follow the original condition."
        )
    return {
        "id": candidate_id,
        "template": site.template,
        "template_description": spec["description"],
        "repo": seed.name,
        "commit": seed.commit,
        "file": site.rel_path,
        "line": site.lineno,
        "enclosing_function": site.enclosing_function,
        "original_expr": site.context_expr,
        "changed_condition": changed_condition,
        "intended_behavior": intended_behavior,
        "edge_cases": spec["edge_cases"],
        "instruction_wording_template": spec["instruction_wording_template"],
        "rng_seed": rng_seed,
        "generated_at": utc_now_iso(),
    }


def generate_candidates(cfg: Config, seed: Seed) -> list[str]:
    """Generate the configured candidates; returns the list of written ids."""
    source_root = seed.repo_dir / seed.source_dir
    files = sorted(
        p for p in source_root.rglob("*.py")
        if not p.name.startswith("test_") and "test" not in p.parent.parts
    )
    if not files:
        raise SystemExit(f"no source files under {source_root}")

    sources = {p: p.read_text() for p in files}
    all_sites: dict[str, list[tuple[Path, Site]]] = {t: [] for t in _TEMPLATE_SPECS}
    for path in files:
        rel = str(path.relative_to(seed.repo_dir))
        for site in discover_sites(sources[path], rel):
            all_sites[site.template].append((path, site))

    rng = random.Random(cfg.rng_seed)
    written: list[str] = []
    counter = 0
    for template, want in sorted(cfg.mutation.per_template.items()):
        pool = all_sites[template]
        order = list(range(len(pool)))
        rng.shuffle(order)
        taken = 0
        used_lines: set[tuple[str, int]] = set()
        for i in order:
            if taken == want:
                break
            path, site = pool[i]
            if (site.rel_path, site.lineno) in used_lines:
                continue
            reason = validate_site(sources[path], site)
            if reason is not None:
                funnel_log(STAGE, f"site:{site.rel_path}:{site.lineno}:{template}",
                           "reject", reason)
                continue
            counter += 1
            candidate_id = f"c{counter:02d}-{template}-L{site.lineno}"
            mutated = apply_site(sources[path], site)
            cand_dir = CANDIDATES_DIR / candidate_id
            cand_dir.mkdir(parents=True, exist_ok=True)
            (cand_dir / "mutation.patch").write_text(
                unified_diff(sources[path], mutated, site.rel_path))
            (cand_dir / "oracle.patch").write_text(
                unified_diff(mutated, sources[path], site.rel_path))
            meta = build_metadata(seed, site, candidate_id, cfg.rng_seed)
            meta["source_sha256_healthy"] = _sha256(sources[path])
            meta["source_sha256_mutated"] = _sha256(mutated)
            write_json(cand_dir / "metadata.json", meta)
            funnel_log(STAGE, candidate_id, "accept",
                       f"valid reversible {template} at {site.rel_path}:{site.lineno}")
            written.append(candidate_id)
            used_lines.add((site.rel_path, site.lineno))
            taken += 1
        print(f"  {template}: {len(pool)} sites discovered, {taken} candidates written")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()

    report = json.loads(ELIGIBILITY_REPORT_PATH.read_text())
    eligible = [r["seed"] for r in report["seeds"] if r["eligible"]]
    if not eligible:
        print("no eligible seeds; run pipeline.eligibility first", file=sys.stderr)
        return 1
    seed = next(s for s in cfg.seeds if s.name == eligible[0])

    written = generate_candidates(cfg, seed)
    print(f"wrote {len(written)} candidates to {CANDIDATES_DIR}")
    for cid in written:
        print(f"  {cid}")
    return 0 if len(written) == cfg.mutation.n_candidates else 1


if __name__ == "__main__":
    sys.exit(main())
