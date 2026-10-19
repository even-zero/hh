"""Config loading / validation for the hh benchmark.

A repo descriptor (see repos.json) is the *only* thing needed to add a repo to
the benchmark. The descriptor pins the clone ref so "replicate" means the exact
same code was measured, and declares which scenarios apply.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)          # the hh repository root (contains hh/)
WORK = os.path.join(HERE, "_work")         # all clones/stores/logs/results
REPOS_DIR = os.path.join(WORK, "repos")
STORES_DIR = os.path.join(WORK, "stores")
LOGS_DIR = os.path.join(WORK, "logs")
RESULTS_DIR = os.path.join(WORK, "results")
RECORDS = os.path.join(WORK, "records.jsonl")
ENV_DIR = os.path.join(WORK, "env")

LANES = ("unittest_exec", "pytest_exec", "mocha_exec", "capture")
CAPTURE_KINDS = ("pytest_plugin", "mocha_reporter")


class ConfigError(Exception):
    pass


def load(path: str | None = None) -> dict:
    cfg = json.load(open(path or os.path.join(HERE, "repos.json"), encoding="utf-8"))
    repos = []
    for r in cfg.get("repos", []):
        if r.get("disabled"):
            continue
        validate(r)
        repos.append(r)
    cfg["repos_active"] = repos
    return cfg


def validate(r: dict) -> None:
    missing = [k for k in ("id", "clone", "lane", "runner", "suite_cmd") if k not in r]
    if missing:
        raise ConfigError(f"repo {r.get('id')!r}: missing fields {missing}")
    if r["lane"] not in LANES:
        raise ConfigError(f"{r['id']}: lane must be one of {LANES}")
    if r["lane"] == "capture" and r.get("capture_kind") not in CAPTURE_KINDS:
        raise ConfigError(f"{r['id']}: capture lane needs capture_kind ∈ {CAPTURE_KINDS}")


# ---------- path helpers ----------

def repo_dir(r: dict) -> str:
    return os.path.join(REPOS_DIR, r["id"])


def work_env() -> None:
    for d in (WORK, REPOS_DIR, STORES_DIR, LOGS_DIR, RESULTS_DIR, ENV_DIR):
        os.makedirs(d, exist_ok=True)


def store(r_id: str, suffix: str) -> str:
    return os.path.join(STORES_DIR, f"{r_id}-{suffix}")


def logdir(r_id: str) -> str:
    d = os.path.join(LOGS_DIR, r_id)
    os.makedirs(d, exist_ok=True)
    return d


def env_for(r: dict, extra: dict | None = None, store_env_job: str | None = None,
            repo_dir_override: str | None = None) -> dict:
    """PYTHONPATH = <repo-root-of-hh> + descriptor pythonpaths + _work/env (sitecustomize)."""
    env = dict(os.environ)
    pp = [REPO_ROOT, ENV_DIR]
    rdir = repo_dir_override or repo_dir(r)
    pp += [os.path.join(rdir, p) for p in r.get("pythonpath", [])]
    env["PYTHONPATH"] = os.pathsep.join(pp + [env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if store_env_job:
        env["HH_JOB"] = store_env_job
    if extra:
        env.update(extra)
    return env
