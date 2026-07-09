"""Stage 10c — Human-readable funnel report from funnel.jsonl.

Reads every verdict line the stages appended, dedupes by (stage, item) with
last-wins (so re-running the pipeline — e.g. `make demo` — does not double
count), and reports:

  - the TASK-LEVEL funnel: candidates in -> accept/reject per gate ->
    survivors, with the acceptance rate
  - the top-rejecting gate — REPORTED from the log, never predicted
  - sub-item audit counts (per-edge-test admissions, per-probe battery
    verdicts, per-near-miss verdicts) shown separately: they are verdicts on
    probes/tests, not task rejections

Writes artifacts/funnel_report.txt and .json; prints the text report.

Usage: python -m pipeline.funnel_report
Exit 0 iff funnel.jsonl exists and parses.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from pipeline.common import ARTIFACTS_DIR, FUNNEL_PATH, utc_now_iso, write_json

FUNNEL_REPORT_TXT = ARTIFACTS_DIR / "funnel_report.txt"
FUNNEL_REPORT_JSON = ARTIFACTS_DIR / "funnel_report.json"

# canonical pipeline order for display
_STAGE_ORDER = [
    "eligibility", "mutation", "failure_establishment", "assembly",
    "verifier_synthesis", "alignment", "solvability",
    "integrity_shortcut", "integrity_nearmiss", "metadata_tag",
    "empirical_difficulty", "assurance_card",
]

# stages whose items are individual probes/tests, not task candidates
_SUBITEM_LABEL = {
    "verifier_synthesis": "edge-test admission rule (per probe)",
    "integrity_shortcut": "shortcut battery (per probe)",
    "integrity_nearmiss": "near-miss battery (per near-miss patch)",
}


def load_deduped(path=FUNNEL_PATH) -> tuple[list[dict[str, Any]], int]:
    """Parse funnel.jsonl; last verdict per (stage, item) wins."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    n_raw = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        n_raw += 1
        entry = json.loads(line)
        latest[(entry["stage"], entry["item"])] = entry
    return list(latest.values()), n_raw


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    per_stage: dict[str, dict[str, Any]] = {}
    for e in entries:
        stage = e["stage"]
        # candidate-level items look like 'c07-...' with no ':'; everything
        # with a ':' (probe/edge/near-miss ids, 'site:...') is a sub-item
        kind = "sub_item" if ":" in e["item"] else "task_level"
        bucket = per_stage.setdefault(stage, {
            "task_level": {"accept": 0, "reject": 0, "rejects": []},
            "sub_item": {"accept": 0, "reject": 0, "rejects": []},
        })[kind]
        bucket[e["verdict"]] += 1
        if e["verdict"] == "reject":
            bucket["rejects"].append({"item": e["item"], "reason": e["reason"]})

    stages_seen = [s for s in _STAGE_ORDER if s in per_stage]
    stages_seen += sorted(set(per_stage) - set(_STAGE_ORDER))

    # top-rejecting gate over TASK-LEVEL verdicts only
    gate_rejects = {s: per_stage[s]["task_level"]["reject"] for s in stages_seen}
    top_gate = max(gate_rejects, key=lambda s: gate_rejects[s]) \
        if any(gate_rejects.values()) else None

    # acceptance rate: candidates generated -> candidates never task-rejected
    candidates = {e["item"] for e in entries
                  if e["stage"] == "mutation" and ":" not in e["item"]
                  and e["verdict"] == "accept"}
    rejected = {e["item"] for e in entries
                if ":" not in e["item"] and e["item"] in candidates
                and e["verdict"] == "reject"}
    return {
        "per_stage": per_stage,
        "stage_order": stages_seen,
        "candidates_in": len(candidates),
        "candidates_rejected": sorted(rejected),
        "candidates_accepted": len(candidates) - len(rejected),
        "acceptance_rate": round((len(candidates) - len(rejected)) / len(candidates), 3)
        if candidates else None,
        "top_rejecting_gate": top_gate,
        "top_rejecting_gate_rejects": gate_rejects.get(top_gate, 0) if top_gate else 0,
    }


def render(summary: dict[str, Any], n_raw: int, n_deduped: int) -> str:
    lines = [
        "FUNNEL REPORT",
        f"generated: {utc_now_iso()}",
        f"source: {FUNNEL_PATH.relative_to(ARTIFACTS_DIR.parent)} "
        f"({n_raw} raw entries; {n_deduped} unique stage:item after "
        "last-wins dedup, so pipeline re-runs are not double counted)",
        "",
        "Task-level funnel (each item is one candidate; a reject removes it):",
    ]
    for stage in summary["stage_order"]:
        t = summary["per_stage"][stage]["task_level"]
        total = t["accept"] + t["reject"]
        if total == 0:
            continue
        lines.append(f"  {stage:<22} {total:>3} in -> "
                     f"{t['accept']} accepted, {t['reject']} rejected")
        for r in t["rejects"]:
            lines.append(f"      REJECT {r['item']}: {r['reason']}")
    lines += [
        "",
        f"  candidates generated : {summary['candidates_in']}",
        f"  candidates surviving : {summary['candidates_accepted']}",
        f"  acceptance rate      : {summary['candidates_accepted']}"
        f"/{summary['candidates_in']}"
        + (f" ({summary['acceptance_rate']:.1%})"
           if summary["acceptance_rate"] is not None else ""),
        f"  top-rejecting gate (reported, not predicted): "
        f"{summary['top_rejecting_gate']} "
        f"({summary['top_rejecting_gate_rejects']} rejects)",
        "",
        "Sub-item audits (verdicts on probes/tests, not task rejections):",
    ]
    for stage in summary["stage_order"]:
        s = summary["per_stage"][stage]["sub_item"]
        total = s["accept"] + s["reject"]
        if total == 0:
            continue
        label = _SUBITEM_LABEL.get(stage, stage)
        lines.append(f"  {stage:<22} {total:>3} items -> "
                     f"{s['accept']} accept, {s['reject']} reject   [{label}]")
        reasons = Counter(r["reason"] for r in s["rejects"])
        for reason, n in reasons.most_common():
            items = [r["item"] for r in s["rejects"] if r["reason"] == reason]
            shown = ", ".join(items[:3]) + (", ..." if len(items) > 3 else "")
            lines.append(f"      {n:>3}x REJECT: {reason}  [{shown}]")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    if not FUNNEL_PATH.exists():
        print(f"no funnel log at {FUNNEL_PATH}", file=sys.stderr)
        return 1
    entries, n_raw = load_deduped()
    summary = summarize(entries)
    text = render(summary, n_raw, len(entries))
    FUNNEL_REPORT_TXT.write_text(text)
    write_json(FUNNEL_REPORT_JSON, {
        "generated_at": utc_now_iso(),
        "n_raw_entries": n_raw,
        "n_deduped": len(entries),
        **summary,
    })
    print(text)
    print(f"wrote {FUNNEL_REPORT_TXT} and {FUNNEL_REPORT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
