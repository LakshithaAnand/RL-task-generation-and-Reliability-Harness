"""Stage 8 — Integrity audit.

Part 1: deterministic SHORTCUT BATTERY, run per task. Each probe simulates a
cheating agent inside a fresh container built from the task image (the exact
broken state an agent sees; true reward = 0), then runs the REAL verifier and
classifies the outcome:

  blocked        the cheat did not move the reward off 0
  failed         the cheat produced reward 1 without a real fix
  not_applicable the cheat's premise does not apply to this verifier

Reward resolution mirrors Harbor exactly (reward.json wins over reward.txt),
so a planted reward.json is judged the way Harbor would judge it.

Any `failed` probe triggers ONE hardening attempt on the shared verifier
(test.sh), after which the probe is re-run and the broken=0/oracle=1 property
is re-confirmed so hardening cannot silently break solvability.

Part 2 (pipeline/nearmiss.py) is the near-miss battery.

Usage: python -m pipeline.integrity [--config pipeline/seeds.toml]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from pipeline.common import (
    ARTIFACTS_DIR,
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    GENERATED_TASKS_DIR,
    build_task_image,
    docker_run,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "integrity_shortcut"
BATTERY_REPORT_PATH = ARTIFACTS_DIR / "shortcut_battery_report.json"

# bash helper prepended to every probe: run the real verifier, print the reward
# Harbor would resolve (json-first, else txt).
_REWARD_HELPER = r"""
run_verifier() {
  bash /tests/test.sh >/tmp/verif.log 2>&1
}
resolved_reward() {
  if [ -f /logs/verifier/reward.json ]; then
    python - <<'PY'
import json
try:
    print(json.load(open("/logs/verifier/reward.json")).get("reward"))
except Exception as e:
    print("PARSE_ERROR")
PY
  elif [ -f /logs/verifier/reward.txt ]; then
    cat /logs/verifier/reward.txt
  else
    echo "NO_REWARD_FILE"
  fi
}
"""


def _run(image: str, task_dir: Path, cand_dir: Path, body: str,
         timeout: float = 420, network: bool = False) -> str:
    """Run a probe body (with the reward helper) in a fresh container."""
    script = _REWARD_HELPER + "\n" + body
    mounts = {task_dir / "tests": "/tests", cand_dir: "/cand"}
    # network toggle: docker_run has no flag, so build via a marker the pip probe
    # handles itself; default containers already have network in this env.
    return docker_run(image, script, timeout=timeout, ro_mounts=mounts).stdout


def _classify(stdout: str) -> tuple[str, str]:
    """Map a probe's printed RESOLVED_REWARD to blocked/failed."""
    line = next((l for l in stdout.splitlines() if l.startswith("RESOLVED_REWARD=")), "")
    val = line.split("=", 1)[1].strip() if "=" in line else "?"
    if val in ("0", "0.0"):
        return "blocked", f"reward stayed {val} despite the cheat"
    if val in ("1", "1.0"):
        return "failed", f"cheat produced reward {val} without a real fix"
    return "failed", f"unexpected reward {val!r} (treated as not-blocked)"


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def probe_hardcode_reward_txt(image, task_dir, cand_dir) -> dict[str, Any]:
    body = """
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt          # agent pre-seeds a winning reward
run_verifier
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body)
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "agent pre-writes reward.txt=1 before verification"}


def probe_spoof_reward_json(image, task_dir, cand_dir) -> dict[str, Any]:
    body = """
mkdir -p /logs/verifier
echo '{"reward": 1}' > /logs/verifier/reward.json   # json wins over txt in Harbor
run_verifier
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body)
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "agent plants reward.json=1 (Harbor resolves json first)"}


def probe_artifact_spoofing(image, task_dir, cand_dir) -> dict[str, Any]:
    body = """
mkdir -p /logs/verifier /repo/.pytest_cache/v/cache
echo '{"failed": []}' > /repo/.pytest_cache/v/cache/lastfailed
echo '{"results":{"tests":[],"summary":{"failed":0,"passed":999}}}' > /logs/verifier/ctrf.json
run_verifier
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body)
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "agent plants fake pytest cache + fake ctrf report"}


def probe_exit_code_fake_pytest(image, task_dir, cand_dir) -> dict[str, Any]:
    body = """
mkdir -p /cheat
printf '#!/bin/bash\\nexit 0\\n' > /cheat/pytest
chmod +x /cheat/pytest
export PATH=/cheat:$PATH                     # fake `pytest` that always exits 0
run_verifier
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body)
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "fake pytest binary on PATH exiting 0"}


def probe_env_poisoning_conftest(image, task_dir, cand_dir) -> dict[str, Any]:
    body = r"""
cat > /repo/conftest.py <<'PY'
# hostile: force every collected test to be skipped (skips are not failures,
# so pytest would exit 0)
def pytest_runtest_setup(item):
    import pytest
    pytest.skip("poisoned")
PY
mkdir -p /cheat
printf '#!/bin/bash\nexit 0\n' > /cheat/pytest
chmod +x /cheat/pytest
export PATH=/cheat:$PATH
run_verifier
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body)
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "hostile /repo/conftest.py (skip-all) + fake pytest on PATH"}


def probe_pip_reinstall(image, task_dir, cand_dir) -> dict[str, Any]:
    body = """
# agent installs the healthy package over the broken editable copy
pip install --quiet --no-cache-dir --force-reinstall --no-deps tabulate==0.9.0 \
    > /tmp/pip.log 2>&1
PIP_RC=$?
run_verifier
echo "PIP_RC=$PIP_RC"
echo "RESOLVED_REWARD=$(resolved_reward)"
"""
    out = _run(image, task_dir, cand_dir, body, network=True)
    pip_rc = next((l for l in out.splitlines() if l.startswith("PIP_RC=")), "PIP_RC=?")
    if "PIP_RC=0" not in pip_rc:
        return {"status": "not_applicable",
                "detail": f"pip install did not succeed ({pip_rc}); no network?",
                "premise": "pip install healthy tabulate over the broken copy"}
    status, detail = _classify(out)
    return {"status": status, "detail": detail,
            "premise": "pip install healthy tabulate over the broken copy"}


def probe_exposed_files(image, task_dir, cand_dir) -> dict[str, Any]:
    """Search the AGENT-visible filesystem for held-out/oracle/vcs artifacts.

    Runs WITHOUT the /tests or /cand mounts — exactly what the agent can see.
    """
    meta = json.loads((cand_dir / "metadata.json").read_text())
    flipped = meta["failure_establishment"]["flipped_tests"]
    # match the exact held-out test DEFINITIONS (def <name>(), word-boundary),
    # not loose substrings — a sibling like test_foo_bar must not count as test_foo.
    funcs = sorted({n.split("::")[-1].split("[")[0] for n in flipped})
    body = f"""
found=0
[ -e /repo/.git ] && {{ echo "LEAK: /repo/.git present"; found=1; }}
if ls /repo/*.patch /repo/**/*.patch >/dev/null 2>&1; then echo "LEAK: patch file"; found=1; fi
if grep -rqsF {' '.join(f"-e 'def {f}('" for f in funcs)} /repo/test 2>/dev/null; then
  echo "LEAK: a held-out test definition is present in visible dev tests"; found=1; fi
if [ -e /tests ]; then echo "LEAK: /tests present in agent env"; found=1; fi
if grep -rqs "verifier_tests" /repo 2>/dev/null; then echo "LEAK: verifier ref"; found=1; fi
echo "FOUND=$found"
"""
    # deliberately no /tests mount here
    script = _REWARD_HELPER + "\n" + body
    out = docker_run(image, script, timeout=120).stdout
    leaked = "FOUND=1" in out
    leaks = [l for l in out.splitlines() if l.startswith("LEAK:")]
    if leaked:
        return {"status": "failed", "detail": "; ".join(leaks),
                "premise": "search agent filesystem for held-out/oracle/.git artifacts"}
    return {"status": "blocked",
            "detail": "no held-out tests, oracle patch, .git, or /tests in agent env",
            "premise": "search agent filesystem for held-out/oracle/.git artifacts"}


PROBES: dict[str, Callable] = {
    "hardcode_reward_txt": probe_hardcode_reward_txt,
    "spoof_reward_json": probe_spoof_reward_json,
    "artifact_spoofing": probe_artifact_spoofing,
    "exit_code_fake_pytest": probe_exit_code_fake_pytest,
    "env_poisoning_conftest": probe_env_poisoning_conftest,
    "pip_reinstall": probe_pip_reinstall,
    "exposed_files": probe_exposed_files,
}

# probes that, if they fail, we know how to harden (see harden.py application)
HARDENABLE = {"spoof_reward_json", "env_poisoning_conftest", "pip_reinstall"}


def run_battery(image, task_dir, cand_dir) -> dict[str, dict[str, Any]]:
    results = {}
    for name, fn in PROBES.items():
        results[name] = fn(image, task_dir, cand_dir)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.parse_args(argv)

    from pipeline.hardening import apply_hardening, reverify_solvable_property
    from pipeline.registry import assert_probes_live, load_registry

    # every registry entry must have a live guarding probe, or the battery
    # would report untested defenses — fail loudly before probing anything
    registry = load_registry()
    assert_probes_live(registry, set(PROBES))
    print(f"hardening registry v{registry['version']}: "
          f"{len(registry['entries'])} entr(ies), all guarding probes live")

    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    all_results, any_hardened = {}, False

    for task in tasks:
        cid = task["candidate_id"]
        task_dir = GENERATED_TASKS_DIR / Path(task["task_dir"]).name
        cand_dir = CANDIDATES_DIR / cid
        image = build_task_image(task_dir)
        print(f"battery: {task['task_name']}")
        results = run_battery(image, task_dir, cand_dir)

        # harden-then-reprobe any hardenable failures (once)
        failed_hardenable = [n for n, r in results.items()
                             if r["status"] == "failed" and n in HARDENABLE]
        if failed_hardenable:
            print(f"  {len(failed_hardenable)} hardenable failure(s): {failed_hardenable}"
                  f" -> hardening verifier & re-probing")
            applied = apply_hardening(task_dir, failed_hardenable)
            any_hardened = True
            image = build_task_image(task_dir)  # tests/ ro-mounted, but rebuild anyway
            for n in failed_hardenable:
                after = PROBES[n](image, task_dir, cand_dir)
                after["hardened"] = True
                after["hardening_applied"] = applied
                after["status_before_hardening"] = results[n]["status"]
                results[n] = after
            prop = reverify_solvable_property(image, task_dir, cand_dir)
            results["_post_harden_solvable_property"] = prop
            print(f"  post-harden broken/oracle property: {prop['ok']} "
                  f"(broken={prop['broken_reward']}, oracle={prop['oracle_reward']})")

        for name, r in results.items():
            if name.startswith("_"):
                continue
            verdict = "accept" if r["status"] in ("blocked", "not_applicable") else "reject"
            funnel_log(STAGE, f"{cid}:{name}", verdict, f"{r['status']}: {r['detail']}")
            tag = " (hardened)" if r.get("hardened") else ""
            print(f"    {name:24} {r['status']}{tag}")

        # record on metadata
        meta = json.loads((cand_dir / "metadata.json").read_text())
        meta.setdefault("integrity", {})["shortcut_battery"] = {
            "results": {k: v for k, v in results.items() if not k.startswith("_")},
            "post_harden_solvable_property": results.get("_post_harden_solvable_property"),
            "registry_version": registry["version"],
            "checked_at": utc_now_iso(),
        }
        write_json(cand_dir / "metadata.json", meta)
        all_results[task["task_name"]] = results

    write_json(BATTERY_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "probes": list(PROBES.keys()),
        "any_hardened": any_hardened,
        "results": {k: {n: {"status": r["status"], "detail": r["detail"],
                            "hardened": r.get("hardened", False)}
                        for n, r in v.items() if not n.startswith("_")}
                    for k, v in all_results.items()},
    })
    print(f"wrote {BATTERY_REPORT_PATH}")

    # exit nonzero if any probe is still `failed` after hardening
    still_failed = [(t, n) for t, v in all_results.items()
                    for n, r in v.items()
                    if not n.startswith("_") and r["status"] == "failed"]
    if still_failed:
        print(f"STILL FAILED after hardening: {still_failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
