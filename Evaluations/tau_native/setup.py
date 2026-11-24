"""Install-independent data setup and strict upstream provenance checks."""

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN = "989d3285ae669e49503324bdb9981bf14fccedeb"
URL = "https://github.com/sierra-research/tau2-bench"
DEFAULT_DATA = ROOT / ".cache" / "tau2-native" / "data"


def setup_data(data_dir=DEFAULT_DATA):
    """Create a sparse pinned upstream checkout; never reset an existing checkout."""
    data_dir = Path(data_dir).resolve()
    checkout = data_dir.parent
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", URL, str(checkout)], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "sparse-checkout",
                "set",
                "--cone",
                "data/tau2/domains/retail",
                "data/tau2/multilingual/en",
                "data/tau2/task_subsets",
                "data/tau2/benchmarks",
                "data/tau2/voice_design",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", PIN], check=True
        )
        subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", PIN], check=True)
    verify_data(data_dir)
    return data_dir


def verify_data(data_dir):
    """Verify commit, clean tracked data, and shared snapshot hashes before models."""
    data_dir = Path(data_dir).resolve()
    checkout = data_dir.parent
    head = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != PIN:
        raise ValueError(f"Native data must use commit {PIN}; found {head}")
    dirty = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"], text=True
    )
    if dirty.strip():
        raise ValueError(
            "Native data checkout has modified tracked files; use a clean pinned checkout"
        )
    manifest = json.loads((ROOT / "evaluation/tau-retail/source/manifest.json").read_text())
    for name in ("db.json", "tasks_114.json", "tasks_reviewed_50.json", "policy.md"):
        spec = manifest["files"][name]
        path = checkout / spec["upstream_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
            raise ValueError(f"Pinned source differs: {path}")
    return data_dir


def prepare_imports(data_dir):
    """Bind data before importing tau2, and verify its installed Git revision."""
    data_dir = verify_data(data_dir)
    try:
        dist = importlib.metadata.distribution("tau2")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("Install the native extra: uv sync --frozen --extra tau-native") from exc
    origin = json.loads(dist.read_text("direct_url.json") or "{}")
    if origin.get("vcs_info", {}).get("commit_id") != PIN:
        raise ValueError("Installed tau2 must be the pinned Git dependency from the root lockfile")
    if "tau2.utils.utils" in sys.modules:
        actual = sys.modules["tau2.utils.utils"].DATA_DIR.resolve()
        if actual != data_dir:
            raise ValueError(
                "tau2 already imported with another data directory; start a fresh Python process"
            )
    os.environ["TAU2_DATA_DIR"] = str(data_dir)
    return data_dir
