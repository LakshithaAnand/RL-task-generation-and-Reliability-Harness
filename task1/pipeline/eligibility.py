"""Stage 1 — Seed eligibility.

For each enabled seed repo:
  1. clone at the pinned commit (tag kept so setuptools_scm-style versioning works)
  2. check license is MIT/Apache/BSD
  3. check working-tree size bound
  4. build a Docker image from a simple generated Dockerfile
  5. run the full test suite inside the container; require green within the time bound

Emits artifacts/eligibility_report.json; rejects (and accepts) go to funnel.jsonl.
Exit 0 iff at least one seed is eligible.

Usage: python -m pipeline.eligibility [--config pipeline/seeds.toml]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    BUILD_DIR,
    ELIGIBILITY_REPORT_PATH,
    REPOS_DIR,
    Config,
    Seed,
    docker_run,
    funnel_log,
    load_config,
    parse_pytest_summary,
    run,
    utc_now_iso,
    write_json,
)

STAGE = "eligibility"

_LICENSE_FILENAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING")
_LICENSE_PATTERNS = {
    "MIT": "Permission is hereby granted, free of charge",
    "Apache-2.0": "Apache License",
    "BSD": "Redistribution and use in source and binary forms",
}

_DOCKERFILE_TEMPLATE = """\
FROM python:3.11-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*
# .git is kept: some seeds (e.g. setuptools_scm) derive their version from it,
# and `git apply` is used by later stages to apply mutation patches.
COPY . /repo/
WORKDIR /repo
RUN git config --global --add safe.directory /repo \\
    && pip install -e . pytest==8.4.1
"""

PYTEST_CMD = "python -m pytest -q --tb=no -p no:cacheprovider"


def clone_at_commit(seed: Seed, timeout: float) -> dict[str, Any]:
    """Clone the seed at its pinned tag and verify HEAD matches the pinned commit."""
    if seed.repo_dir.exists():
        head = run(["git", "rev-parse", "HEAD"], timeout=30, cwd=seed.repo_dir)
        if head.returncode == 0 and head.stdout.strip() == seed.commit:
            return {"ok": True, "reused_existing_clone": True}
        shutil.rmtree(seed.repo_dir)

    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    res = run(
        ["git", "clone", "--quiet", "--depth", "1", "--branch", seed.tag,
         seed.url, str(seed.repo_dir)],
        timeout=timeout,
    )
    if res.returncode != 0:
        return {"ok": False, "error": f"git clone failed: {res.stderr.strip()[:500]}"}

    head = run(["git", "rev-parse", "HEAD"], timeout=30, cwd=seed.repo_dir)
    actual = head.stdout.strip()
    if actual != seed.commit:
        return {"ok": False, "error": f"HEAD {actual} != pinned {seed.commit}"}
    return {"ok": True, "reused_existing_clone": False}


def check_license(seed: Seed) -> dict[str, Any]:
    for name in _LICENSE_FILENAMES:
        path = seed.repo_dir / name
        if path.exists():
            text = path.read_text(errors="replace")
            for spdx, marker in _LICENSE_PATTERNS.items():
                if marker in text:
                    return {"ok": True, "license": spdx, "file": name}
            return {"ok": False, "error": f"{name} matched no permissive pattern"}
    return {"ok": False, "error": "no license file found"}


def check_size(seed: Seed, max_mb: float) -> dict[str, Any]:
    total = sum(
        p.stat().st_size
        for p in seed.repo_dir.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    size_mb = round(total / 1_000_000, 2)
    ok = size_mb <= max_mb
    return {"ok": ok, "size_mb": size_mb, "max_mb": max_mb}


def build_image(seed: Seed, timeout: float) -> dict[str, Any]:
    build_dir = BUILD_DIR / seed.name
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile = build_dir / "Dockerfile"
    dockerfile.write_text(_DOCKERFILE_TEMPLATE)
    res = run(
        ["docker", "build", "-f", str(dockerfile), "-t", seed.image_tag,
         str(seed.repo_dir)],
        timeout=timeout,
    )
    if res.returncode != 0:
        return {"ok": False, "error": f"docker build failed: {res.stderr.strip()[-800:]}"}
    return {"ok": True, "image": seed.image_tag, "dockerfile": str(dockerfile)}


def run_tests(seed: Seed, timeout: float, max_test_seconds: float) -> dict[str, Any]:
    res = docker_run(seed.image_tag, PYTEST_CMD, timeout=timeout)
    summary = parse_pytest_summary(res.stdout)
    result: dict[str, Any] = {
        "ok": res.returncode == 0,
        "exit_code": res.returncode,
        **summary,
    }
    if res.returncode != 0:
        result["error"] = f"pytest exit {res.returncode}: {res.stdout.strip()[-500:]}"
        return result
    duration = summary["duration_seconds"]
    if duration is None or duration > max_test_seconds:
        result["ok"] = False
        result["error"] = f"suite took {duration}s > bound {max_test_seconds}s"
    return result


def evaluate_seed(seed: Seed, cfg: Config) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    checks["clone"] = clone_at_commit(seed, timeout=300)
    if checks["clone"]["ok"]:
        checks["license"] = check_license(seed)
        checks["size"] = check_size(seed, cfg.limits.max_repo_mb)
        if checks["license"]["ok"] and checks["size"]["ok"]:
            checks["docker_build"] = build_image(
                seed, timeout=cfg.limits.docker_build_timeout_seconds
            )
            if checks["docker_build"]["ok"]:
                checks["tests"] = run_tests(
                    seed,
                    timeout=cfg.limits.container_run_timeout_seconds,
                    max_test_seconds=cfg.limits.max_test_seconds,
                )

    eligible = all(c["ok"] for c in checks.values()) and "tests" in checks
    record = {
        "seed": seed.name,
        "url": seed.url,
        "tag": seed.tag,
        "commit": seed.commit,
        "checks": checks,
        "eligible": eligible,
    }
    if eligible:
        funnel_log(STAGE, seed.name, "accept", "all eligibility checks passed")
    else:
        failed = [k for k, c in checks.items() if not c["ok"]]
        missing = [k for k in ("clone", "license", "size", "docker_build", "tests")
                   if k not in checks]
        reason = f"failed checks: {failed}" + (f"; skipped: {missing}" if missing else "")
        funnel_log(STAGE, seed.name, "reject", reason,
                   detail={k: checks[k].get("error") for k in failed})
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    records = [evaluate_seed(seed, cfg) for seed in cfg.seeds]

    report = {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "seeds": records,
        "n_eligible": sum(r["eligible"] for r in records),
    }
    write_json(ELIGIBILITY_REPORT_PATH, report)
    print(f"wrote {ELIGIBILITY_REPORT_PATH}")
    for r in records:
        print(f"  {r['seed']}: {'ELIGIBLE' if r['eligible'] else 'REJECTED'}")
    return 0 if report["n_eligible"] >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
