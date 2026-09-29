"""The run of the M1 gate on three platforms: what it refuses, and what it runs.

No test here starts Docker or uv, and no test reads the real corpus or the
real bench/out: main() runs with the runners, the snapshot, the corpus check
and OUT all replaced (the gate fixture); only the command that the container
would run is real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))
sys.path.insert(0, str(ROOT / "tools"))

import run  # noqa: E402

import checks  # noqa: E402


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """One run of main() with everything replaced: the tree clean, the corpus
    complete, one snapshot, and runners that record what they get and succeed."""
    recorded = {"snapshots": [], "runs": []}

    def take_snapshot(root):
        recorded["snapshots"].append(root)
        return b"snapshot"

    def host(snapshot, **keywords):
        recorded["runs"].append(("the host", snapshot))
        return 0

    def container(snapshot, architecture, **keywords):
        recorded["runs"].append((architecture, snapshot))
        return 0

    monkeypatch.setattr(run, "OUT", tmp_path)
    monkeypatch.setattr(run, "tree_state", lambda root: checks.TreeState("abc", ()))
    monkeypatch.setattr(run, "verify_corpus", lambda: True)
    monkeypatch.setattr(run, "tree_archive", take_snapshot)
    monkeypatch.setattr(run, "run_host", host)
    monkeypatch.setattr(run, "run_container", container)
    return recorded


@pytest.mark.parametrize("architecture", ["arm64", "amd64"])
def test_the_container_reads_the_corpus_read_only(architecture):
    command = run.container_command(
        architecture, commit="c0ffee", corpus=Path("/host/corpus"), out=Path("/host/out")
    )
    assert command[0] == "docker"
    assert "run" in command
    platform_at = command.index("--platform")
    assert command[platform_at + 1] == f"linux/{architecture}"
    assert "/host/corpus:/corpus:ro" in command
    assert "/host/out:/out" in command
    assert checks.CONTAINER_IMAGES[architecture] in command
    inside = command[-1]
    found_at = [
        inside.find("tar -x"),
        inside.find(f"uv=={checks.UV_VERSION}"),
        inside.find("uv sync --locked"),
        inside.find("fetch.py --verify-only --from /corpus"),
        inside.find(f"m1.py --commit c0ffee --out /out/m1-linux-{architecture}.json"),
    ]
    assert -1 not in found_at
    assert found_at == sorted(found_at)


def test_uncommitted_changes_are_refused(gate, monkeypatch):
    monkeypatch.setattr(run, "tree_state", lambda root: checks.TreeState("abc", (" M README.md",)))
    assert run.main([]) == 2
    assert gate["snapshots"] == []
    assert gate["runs"] == []


def test_an_incomplete_corpus_is_refused(gate, monkeypatch):
    monkeypatch.setattr(run, "verify_corpus", lambda: False)
    assert run.main([]) == 2
    assert gate["snapshots"] == []
    assert gate["runs"] == []


def test_every_platform_runs_the_same_snapshot(gate, monkeypatch):
    snapshots = iter([b"first", b"second", b"third", b"fourth"])
    monkeypatch.setattr(run, "tree_archive", lambda root: next(snapshots))
    assert run.main([]) == 0
    assert gate["runs"] == [("the host", b"first"), ("arm64", b"first"), ("amd64", b"first")]


def test_one_failing_platform_fails_the_run(gate, monkeypatch):
    def container(snapshot, architecture, **keywords):
        gate["runs"].append((architecture, snapshot))
        return 1 if architecture == "amd64" else 0

    monkeypatch.setattr(run, "run_container", container)
    assert run.main([]) == 1


def test_no_receipt_of_an_earlier_run_is_left(gate, monkeypatch, tmp_path):
    stale = [
        tmp_path / run.host_receipt(),
        *(tmp_path / run.container_receipt(a) for a in checks.LINUX_ARCHITECTURES),
    ]
    for path in stale:
        path.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(run, "run_host", lambda snapshot, **keywords: 1)
    monkeypatch.setattr(run, "run_container", lambda snapshot, architecture, **keywords: 1)
    assert run.main([]) == 1
    assert not any(path.exists() for path in stale)
