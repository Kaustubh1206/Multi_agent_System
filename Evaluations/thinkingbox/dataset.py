"""The unmodified 507-task release list; references stay evaluator-only."""

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("source")
MANIFEST = json.loads((SOURCE / "manifest.json").read_text())
DATA = ROOT / ".cache" / "thinkingbox-data"


def task_names():
    # The canonical YAML is deliberately a simple flat list of strings.
    raw = (SOURCE / "tasks.yaml").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST["task_list_sha256"]:
        raise ValueError("Canonical task list checksum changed")
    import yaml

    names = yaml.safe_load(raw)
    if len(names) != 507 or len(set(names)) != 507:
        raise ValueError("Expected exactly 507 unique release tasks")
    return names


def prepare_source():
    """Fetch a pinned source checkout once; never overwrite local changes."""
    commit = MANIFEST["data_commit"]
    if not DATA.exists():
        DATA.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--no-checkout", MANIFEST["data_url"], str(DATA)], check=True
        )
        subprocess.run(["git", "-C", str(DATA), "checkout", "--detach", commit], check=True)
    actual = subprocess.check_output(["git", "-C", str(DATA), "rev-parse", "HEAD"], text=True)
    dirty = subprocess.check_output(["git", "-C", str(DATA), "status", "--porcelain"], text=True)
    if actual.strip() != commit or dirty.strip():
        raise RuntimeError(f"Expected clean source at {commit}; inspect {DATA}")
    release = DATA / "releases/thinkingbox_bench_v1/testlist_thinkingbox_bench_v1.yaml"
    if release.read_bytes() != (SOURCE / "tasks.yaml").read_bytes():
        raise ValueError("Source release differs from committed task list")
    return DATA


def load_tasks(names=None):
    from thinkingbox.common.hydrator import iter_cases_by_names

    source = prepare_source()
    return list(iter_cases_by_names(names or task_names(), source / "dataset", "think"))


def audit():
    tasks = load_tasks()
    counts = Counter()
    for task in tasks:
        servers = set(task.scenario.world_state)
        if len(servers) != 1 or not servers <= MANIFEST["domain_counts"].keys():
            raise ValueError(f"Unexpected domain: {servers}")
        counts.update(servers)
        if not task.query or not task.test_code or not task.scenario.tools:
            raise ValueError(f"Incomplete source task: {task.uid}")
        compile(task.test_code, task.uid, "exec")
    if dict(counts) != MANIFEST["domain_counts"]:
        raise ValueError(f"Domain counts differ: {counts}")
    return {
        "tasks": len(tasks),
        "domains": dict(counts),
        "source": MANIFEST,
        "validation": "All source tasks hydrated and native checks compiled; no model run.",
    }
