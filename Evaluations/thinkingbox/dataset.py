"""The 98 retail tasks selected from the pinned 507-task source release."""

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("source")
MANIFEST = json.loads((SOURCE / "manifest.json").read_text())
DATA = ROOT / ".cache" / "thinkingbox-data"
RETAIL_DOMAIN = "sandbox_external_retail"
RETAIL_TASK_COUNT = 98
RETAIL_TASK_PREFIX = "sandbox_external_retail_group1.py:"


def task_names():
    # The canonical YAML is deliberately a simple flat list of strings.
    raw = (SOURCE / "tasks.yaml").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST["task_list_sha256"]:
        raise ValueError("Canonical task list checksum changed")
    import yaml

    names = yaml.safe_load(raw)
    if len(names) != 507 or len(set(names)) != 507:
        raise ValueError("Expected exactly 507 unique release tasks")
    retail = [name for name in names if name.startswith(RETAIL_TASK_PREFIX)]
    if len(retail) != RETAIL_TASK_COUNT:
        raise ValueError("Expected exactly 98 canonical retail tasks")
    return retail


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

    selected = task_names() if names is None else list(names)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(task_names()):
        raise ValueError("Select unique canonical retail task IDs only (see --list)")
    source = prepare_source()
    tasks = list(iter_cases_by_names(selected, source / "dataset", "think"))
    for task in tasks:
        validate_retail_task(task)
    return tasks


def validate_retail_task(task):
    if set(task.scenario.world_state) != {RETAIL_DOMAIN} or set(task.init) != {RETAIL_DOMAIN}:
        raise ValueError(f"Only {RETAIL_DOMAIN} tasks can run in the retail MAS")


def audit():
    tasks = load_tasks()
    counts = Counter()
    for task in tasks:
        servers = set(task.scenario.world_state)
        validate_retail_task(task)
        counts.update(servers)
        if not task.query or not task.test_code or not task.scenario.tools:
            raise ValueError(f"Incomplete source task: {task.uid}")
        compile(task.test_code, task.uid, "exec")
    if dict(counts) != {RETAIL_DOMAIN: RETAIL_TASK_COUNT}:
        raise ValueError(f"Domain counts differ: {counts}")
    return {
        "tasks": len(tasks),
        "scope": "retail-only",
        "domains": dict(counts),
        "source": MANIFEST,
        "validation": "All source tasks hydrated and native checks compiled; no model run.",
    }
