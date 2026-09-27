"""Run the checks of this repository in the same way on every platform.

These are the checks that decide whether a change may be merged:

  install the locked dependencies   uv sync --locked
  platform                          which platform and Python the checks ran on
  ruff                              uv run ruff check, uv run ruff format --check
  architecture check                tools/check_architecture.py
  tests                             uv run pytest
  build                             uv build
  wheel                             install the built wheel into a fresh environment
                                    outside the checkout and call pdfacts.engine_info()

A failing check does not stop the checks after it, so one run shows every
problem.

Usage:
  python3 tools/checks.py                in a fresh copy of the working tree on this machine
  python3 tools/checks.py --linux ARCH   in a fresh copy inside a Linux container
                                         (ARCH is arm64 or amd64; needs Docker)
  python3 tools/checks.py --all          this machine, then Linux arm64 and Linux amd64;
                                         refuses to run when the working tree has changes
                                         that are not committed
  python3 tools/checks.py --in-place     in the current directory; the other modes run
                                         this inside the copy

The copy contains what git would commit: tracked files and new files that are
not ignored. It is taken once, at the start, and every platform of a run
checks the same copy. Every run states the commit it checked, whether the
working tree had changes, and whether the commit contains origin/main as last
fetched. The script needs Python 3.12 or newer and nothing but the standard
library, so that it can run before any dependency is installed. Exit status is
0 when every check passed.
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
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The version of uv that wrote uv.lock.
UV_VERSION = "0.6.14"
# Python 3.13.5, the version in .python-version, pinned by digest so that the
# Linux checks do not drift with whatever image was pulled last. One digest per
# architecture: Docker cannot keep two architectures under one index digest.
_IMAGE = "python:3.13.5-slim@sha256:"
CONTAINER_IMAGES = {
    "arm64": _IMAGE + "fd07246d45ebcfd139882601b71eea086ffa824ddd87999bceb4f36885c2f3ec",
    "amd64": _IMAGE + "a7cb177dc60dfa77b223192cd0d9e6ce1aac7cf9a569023d68e7c91b6371a718",
}
LINUX_ARCHITECTURES = tuple(CONTAINER_IMAGES)
SCRATCH = "{scratch}"
PLATFORM_REPORT = (
    "import platform, sys; "
    "print(platform.platform()); print(platform.machine()); print(sys.version.split()[0])"
)
# Variables that could make a check use another environment than the one it set up.
LEAKING_VARIABLES = (
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON",
    "UV_SYSTEM_PYTHON",
    "UV_FROZEN",
    "UV_NO_SYNC",
)


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...] = ()
    # Called with the checkout and a scratch directory; returns whether it passed.
    action: Callable[[Path, Path], bool] | None = None


@dataclass(frozen=True)
class Outcome:
    name: str
    passed: bool
    seconds: float


@dataclass(frozen=True)
class TreeState:
    commit: str
    changes: tuple[str, ...]
    # Whether the commit contains origin/main as last fetched; None when unknown.
    contains_main: bool | None = None

    def describe(self) -> str:
        tree = (
            "working tree clean"
            if not self.changes
            else f"{len(self.changes)} uncommitted change(s)"
        )
        main = {
            True: "contains origin/main as last fetched",
            False: "does NOT contain origin/main as last fetched",
            None: "origin/main unknown",
        }[self.contains_main]
        return f"commit {self.commit}, {tree}, {main}"


def clean_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if key not in LEAKING_VARIABLES}


def imported_from(module_file: Path, directory: Path) -> bool:
    return module_file.resolve().is_relative_to(directory.resolve())


def check_wheel(checkout: Path, scratch: Path) -> bool:
    """Install the wheel built into scratch/dist into a fresh environment and call it."""
    wheels = sorted((scratch / "dist").glob("pdfacts-*.whl"))
    if len(wheels) != 1:
        print(f"expected one wheel in {scratch / 'dist'}, found {len(wheels)}")
        return False
    environment = scratch / "wheel-env"
    python = environment / "bin" / "python"
    version = (checkout / ".python-version").read_text(encoding="utf-8").strip()
    for command in (
        ["uv", "venv", str(environment), "--python", version],
        ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
    ):
        if not _run(command, cwd=scratch):
            return False
    # Run from scratch, outside the checkout, so that the installed wheel is imported.
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import pdfacts; print(pdfacts.__file__); print(pdfacts.engine_info())",
        ],
        cwd=scratch,
        env=clean_environment(os.environ),
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0 or not result.stdout:
        return False
    location = Path(result.stdout.splitlines()[0])
    if imported_from(location, checkout):
        print(f"pdfacts was imported from the checkout ({location}), not from the wheel")
        return False
    return True


def steps() -> tuple[Step, ...]:
    return (
        Step("install the locked dependencies", ("uv", "sync", "--locked")),
        Step("platform", ("uv", "run", "--locked", "python", "-c", PLATFORM_REPORT)),
        Step("ruff check", ("uv", "run", "--locked", "ruff", "check")),
        Step("ruff format", ("uv", "run", "--locked", "ruff", "format", "--check")),
        Step(
            "architecture check",
            ("uv", "run", "--locked", "python", "tools/check_architecture.py"),
        ),
        Step("tests", ("uv", "run", "--locked", "pytest")),
        Step("build", ("uv", "build", "--out-dir", f"{SCRATCH}/dist")),
        Step("wheel", action=check_wheel),
    )


def _run(command: Sequence[str], *, cwd: Path) -> bool:
    print("$ " + " ".join(command), flush=True)
    try:
        return subprocess.run(command, cwd=cwd, env=clean_environment(os.environ)).returncode == 0
    except OSError as error:
        print(f"could not run {command[0]}: {error}")
        return False


def run_steps(steps: Sequence[Step], checkout: Path) -> list[Outcome]:
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="pdfacts-checks-") as scratch:
        for step in steps:
            print(f"\n=== {step.name}", flush=True)
            started = time.monotonic()
            try:
                if step.action is not None:
                    passed = step.action(checkout, Path(scratch))
                else:
                    command = [part.replace(SCRATCH, scratch) for part in step.command]
                    passed = _run(command, cwd=checkout)
            except Exception:
                # A broken check counts as failed and does not stop the others.
                traceback.print_exc()
                passed = False
            outcomes.append(Outcome(step.name, passed, time.monotonic() - started))
    return outcomes


def exit_status(outcomes: Sequence[Outcome]) -> int:
    return 0 if outcomes and all(outcome.passed for outcome in outcomes) else 1


def uv_version() -> str:
    try:
        result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    except OSError:
        return "uv not found"
    return result.stdout.strip() or "uv version unknown"


def print_summary(title: str, outcomes: Sequence[Outcome]) -> None:
    print(f"\n=== summary: {title}")
    for outcome in outcomes:
        state = "passed" if outcome.passed else "FAILED"
        print(f"{state:7} {outcome.name:32} {outcome.seconds:6.1f} s")


def _git(checkout: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=checkout, capture_output=True, text=True, check=True
    ).stdout


def tree_state(checkout: Path) -> TreeState:
    commit = _git(checkout, "rev-parse", "HEAD").strip()
    status = _git(checkout, "status", "--porcelain", "--untracked-files=all")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=checkout,
        capture_output=True,
    )
    # 0: contained, 1: not contained, anything else: origin/main is not known.
    contains_main = {0: True, 1: False}.get(ancestor.returncode)
    return TreeState(commit, tuple(line for line in status.splitlines() if line), contains_main)


def files_to_copy(checkout: Path) -> list[str]:
    """What git would commit: tracked files and new files that are not ignored."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=checkout,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    names = sorted({name for name in listing.split("\0") if name})
    # A tracked file that was deleted in the working tree is still listed.
    return [name for name in names if (checkout / name).is_file()]


def tree_archive(checkout: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name in files_to_copy(checkout):
            archive.add(checkout / name, arcname=name, recursive=False)
    return buffer.getvalue()


def container_command(architecture: str) -> list[str]:
    inside = (
        "set -e; "
        # Unpack first: the archive arrives on standard input.
        "mkdir /work; tar -x -C /work; "
        # The image has no git, and some tests build a git repository.
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -qq >/dev/null; "
        "apt-get install -y -qq --no-install-recommends git >/dev/null; "
        "pip install --quiet --disable-pip-version-check --root-user-action=ignore "
        f"uv=={UV_VERSION}; "
        "cd /work; "
        "exec python3 tools/checks.py --in-place"
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
        CONTAINER_IMAGES[architecture],
        "sh",
        "-c",
        inside,
    ]


def run_in_place(checkout: Path) -> int:
    outcomes = run_steps(steps(), checkout)
    print_summary(f"{platform.system()} {platform.machine()}, {uv_version()}", outcomes)
    return exit_status(outcomes)


def run_isolated(snapshot: bytes) -> int:
    with tempfile.TemporaryDirectory(prefix="pdfacts-copy-") as copy:
        with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
            archive.extractall(copy, filter="data")
        command = [sys.executable, "tools/checks.py", "--in-place"]
        return subprocess.run(command, cwd=copy, env=clean_environment(os.environ)).returncode


def run_in_container(snapshot: bytes, architecture: str) -> int:
    print(f"\n##### Linux {architecture}, in a container", flush=True)
    try:
        return subprocess.run(container_command(architecture), input=snapshot).returncode
    except OSError as error:
        print(f"could not run docker: {error}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--in-place", action="store_true")
    mode.add_argument("--linux", choices=LINUX_ARCHITECTURES)
    mode.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    if args.in_place:
        return run_in_place(Path.cwd())

    state = tree_state(ROOT)
    print(f"checking {state.describe()}", flush=True)
    if args.all and state.changes:
        print("--all checks what will be merged: commit or stash these changes first")
        for line in state.changes:
            print(f"  {line}")
        return 2

    # One copy for every platform of this run, so that a change made while the
    # run goes on cannot reach some platforms and not others.
    snapshot = tree_archive(ROOT)
    if args.linux:
        results = {f"Linux {args.linux}": run_in_container(snapshot, args.linux)}
    elif args.all:
        results = {f"{platform.system()} {platform.machine()}": run_isolated(snapshot)}
        for architecture in LINUX_ARCHITECTURES:
            results[f"Linux {architecture}"] = run_in_container(snapshot, architecture)
    else:
        results = {f"{platform.system()} {platform.machine()}": run_isolated(snapshot)}

    print(f"\n##### {state.describe()}")
    for name, status in results.items():
        print(f"{'passed' if status == 0 else 'FAILED':7} {name}")
    return 0 if all(status == 0 for status in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
