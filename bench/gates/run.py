"""Run a gate on three platforms and write one receipt per platform.

The gate is one compare.py knows, m1, m4a, m4b or m5a: bench/gates/<gate>.py. The
dependency group "bench" is installed for each, the libraries a gate compares
with; the library's own tests run without it (tools/checks.py). The host
(macOS) runs in a fresh copy of the working tree, Linux arm64 and Linux
x86_64 in the containers of tools/checks.py, over the same snapshot of the
tree. The corpus is not part of the snapshot: each run places it with
bench/corpus/fetch.py --from, from a read-only source, checking every hash.
The receipts name the commit and go to bench/out/. Standard library only.

    python3 bench/gates/run.py GATE

Exit status 0 when the host and both containers returned 0, 1 otherwise, 2
when the run was refused (uncommitted changes, an incomplete corpus).
run.py does not compare the receipts; compare.py does.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from compare import GATES  # noqa: E402

from checks import (  # noqa: E402
    CONTAINER_IMAGES,
    LINUX_ARCHITECTURES,
    UV_VERSION,
    clean_environment,
    tree_archive,
    tree_state,
)

OUT = ROOT / "bench" / "out"
CORPUS = ROOT / "bench" / "corpus" / "files"


def host_receipt(gate: str) -> str:
    return f"{gate}-{platform.system().lower()}-{platform.machine().lower()}.json"


def container_receipt(gate: str, architecture: str) -> str:
    return f"{gate}-linux-{architecture}.json"


def verify_corpus() -> bool:
    result = subprocess.run(
        [sys.executable, str(ROOT / "bench" / "corpus" / "fetch.py"), "--verify-only"],
        cwd=ROOT,
    )
    return result.returncode == 0


def container_command(
    gate: str, architecture: str, *, commit: str, corpus: Path, out: Path
) -> list[str]:
    inside = "; ".join(
        [
            "set -e",
            "mkdir /work",
            "tar -x -C /work",
            "pip install --quiet --disable-pip-version-check --root-user-action=ignore "
            f"uv=={UV_VERSION}",
            "cd /work",
            "uv sync --locked --group bench",
            "uv run --locked python bench/corpus/fetch.py --verify-only --from /corpus",
            f"uv run --locked --group bench python bench/gates/{gate}.py --commit "
            f"{commit} --out /out/{container_receipt(gate, architecture)}",
        ]
    )
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--platform",
        f"linux/{architecture}",
        # A named volume keeps downloads between runs; it holds nothing else.
        "--volume",
        f"pdfacts-uv-cache-{architecture}:/root/.cache/uv",
        # The corpus is given read-only; the run writes only its receipt.
        "--volume",
        f"{corpus}:/corpus:ro",
        "--volume",
        f"{out}:/out",
        CONTAINER_IMAGES[architecture],
        "sh",
        "-c",
        inside,
    ]


def run_container(
    snapshot: bytes, gate: str, architecture: str, *, commit: str, corpus: Path, out: Path
) -> int:
    command = container_command(gate, architecture, commit=commit, corpus=corpus, out=out)
    try:
        return subprocess.run(command, input=snapshot, cwd=ROOT).returncode
    except OSError as error:
        print(f"the container for {architecture} could not be started: {error}", file=sys.stderr)
        return 1


def run_host(snapshot: bytes, gate: str, *, commit: str, corpus: Path, out: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="pdfacts-gate-") as folder:
        with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
            archive.extractall(folder, filter="data")
        steps = [
            ["uv", "sync", "--locked", "--group", "bench"],
            [
                "uv",
                "run",
                "--locked",
                "python",
                "bench/corpus/fetch.py",
                "--verify-only",
                "--from",
                str(corpus),
            ],
            [
                "uv",
                "run",
                "--locked",
                "--group",
                "bench",
                "python",
                f"bench/gates/{gate}.py",
                "--commit",
                commit,
                "--out",
                str(out / host_receipt(gate)),
            ],
        ]
        for step in steps:
            status = subprocess.run(step, cwd=folder, env=clean_environment(os.environ)).returncode
            if status != 0:
                return status
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # The gate and nothing else: the parser answers --help and refuses the
    # rest, so a stray argument cannot start a full run.
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gate", choices=sorted(GATES))
    gate = parser.parse_args(argv).gate
    state = tree_state(ROOT)
    print(f"gate on {state.describe()}", flush=True)
    if state.changes:
        for change in state.changes:
            print(change, flush=True)
        return 2
    if not verify_corpus():
        print(
            "the corpus is incomplete; run bench/corpus/fetch.py first",
            file=sys.stderr,
            flush=True,
        )
        return 2
    snapshot = tree_archive(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    commit = state.commit
    runs: list[tuple[str, Path, Callable[[], int]]] = [
        (
            "the host",
            OUT / host_receipt(gate),
            partial(run_host, snapshot, gate, commit=commit, corpus=CORPUS, out=OUT),
        ),
        *[
            (
                architecture,
                OUT / container_receipt(gate, architecture),
                partial(
                    run_container,
                    snapshot,
                    gate,
                    architecture,
                    commit=commit,
                    corpus=CORPUS,
                    out=OUT,
                ),
            )
            for architecture in LINUX_ARCHITECTURES
        ],
    ]
    failures = 0
    for name, receipt, platform_run in runs:
        receipt.unlink(missing_ok=True)
        started = time.perf_counter()
        status = platform_run()
        seconds = round(time.perf_counter() - started, 1)
        failures += status != 0
        print(
            f"{'passed' if status == 0 else 'FAILED'} {name} in {seconds} s; receipt {receipt}",
            flush=True,
        )
    print(state.describe(), flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
