"""Start the retail source server and a private Typesense instance."""

import hashlib
import os
import platform
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx
import yaml

from Evaluations.thinkingbox.dataset import DATA, RETAIL_DOMAIN, ROOT, prepare_source

CACHE = ROOT / ".cache" / "thinkingbox"
TYPESENSE_SHA256 = {
    ("Linux", "x86_64"): (
        "linux-amd64",
        "4a1c9ce33efa70b26e24a043bd48792856212845ee0061ea6b9f8a2b7ad76c89",
    ),
    ("Linux", "aarch64"): (
        "linux-arm64",
        "39212289689b763ae219f322cb87d977b751662f5856b68543bccdaa448200ae",
    ),
    ("Darwin", "arm64"): (
        "darwin-arm64",
        "dcbf09f30ecea7c3ff3245043611f6ec2eea87a6851b4462572825bb87e3be33",
    ),
    ("Darwin", "x86_64"): (
        "darwin-amd64",
        "34fe199ed1462a7dc54418f360ac97a5265806a786298fb5863a2ea95f70d47c",
    ),
}


def typesense_binary():
    system = (platform.system(), platform.machine())
    if system not in TYPESENSE_SHA256:
        raise RuntimeError(f"Unsupported Typesense platform: {system}")
    target, checksum = TYPESENSE_SHA256[system]
    binary = CACHE / target / "typesense-server"
    receipt = binary.with_suffix(".sha256")
    if binary.exists() and receipt.exists():
        if hashlib.sha256(binary.read_bytes()).hexdigest() == receipt.read_text():
            return binary
        raise RuntimeError(f"Cached binary changed: {binary}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://dl.typesense.org/releases/30.1/typesense-server-30.1-{target}.tar.gz"
    with tempfile.TemporaryFile() as archive:
        with urllib.request.urlopen(url, timeout=60) as response:
            import shutil

            shutil.copyfileobj(response, archive)
        archive.seek(0)
        if hashlib.file_digest(archive, "sha256").hexdigest() != checksum:
            raise RuntimeError("Typesense archive checksum differs from pinned upstream installer")
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            # Retain any bundled libraries too (notably on macOS). Python's data
            # filter rejects unsafe paths/links even though the archive is pinned.
            members = [
                member
                for member in tar
                if Path(member.name).name == "typesense-server" and member.isfile()
            ]
            if len(members) != 1:
                raise RuntimeError("Unexpected Typesense archive layout")
            tar.extractall(path=binary.parent, filter="data")
            if not binary.is_file() or binary.is_symlink():
                raise RuntimeError("Expected a regular Typesense executable at archive root")
    binary.chmod(0o755)
    receipt.write_text(hashlib.sha256(binary.read_bytes()).hexdigest())
    return binary


def require_free_port(port):
    with socket.socket() as connection:
        try:
            connection.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"Port {port} is occupied; stop that service before running this "
                "isolated benchmark. Existing processes were left alone."
            ) from error


def wait_ready(process, url, logfile):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited; see {logfile}")
        try:
            if httpx.get(url, timeout=1).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Service did not become ready; see {logfile}")


@contextmanager
def services():
    prepare_source()
    for port in (8108, 8107, 7111):
        require_free_port(port)
    binary = typesense_binary()
    CACHE.mkdir(parents=True, exist_ok=True)
    # Match the source package's data-relative lookups without installing another env.
    env = os.environ.copy()
    env["THINKINGBOX_DATA"] = str(DATA)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(DATA / "servers/tb_business_ops_servers_202606"), env.get("PYTHONPATH", "")]
    )
    env["TYPESENSE_API_KEY"] = "Fake"
    config = {
        "use_internal_servers": False,
        "servers": {
            name: {
                "type": "mcp-process",
                "command": ["{python}", "-m", f"tb_business_ops_servers_202606.mcp_{name}"],
                "env": {"TYPESENSE_API_KEY": "Fake"},
            }
            for name in (RETAIL_DOMAIN,)
        },
    }
    config_path = CACHE / "servers.yaml"
    config_path.write_text(yaml.safe_dump(config))
    processes = []
    with ExitStack() as cleanup:
        db = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="db-", dir=CACHE))
        tslog = cleanup.enter_context((CACHE / "typesense.log").open("w"))
        proxylog = cleanup.enter_context((CACHE / "proxy.log").open("w"))
        # Stop owned processes before closing logs or removing their database,
        # including when startup fails before reaching yield.
        cleanup.callback(stop_processes, processes)
        process = subprocess.Popen(
            [
                str(binary),
                "--data-dir",
                db,
                "--api-key",
                "Fake",
                "--api-address",
                "127.0.0.1",
                "--api-port",
                "8108",
                "--peering-address",
                "127.0.0.1",
            ],
            stdout=tslog,
            stderr=subprocess.STDOUT,
            env=env,
        )
        processes.append(process)
        wait_ready(process, "http://127.0.0.1:8108/health", CACHE / "typesense.log")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "thinkingbox.cli.main",
                "mcp-start",
                "--servers",
                str(config_path),
                "--host",
                "127.0.0.1",
            ],
            stdout=proxylog,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=ROOT,
        )
        processes.append(process)
        wait_ready(process, "http://127.0.0.1:7111/health", CACHE / "proxy.log")
        yield


def stop_processes(processes):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
