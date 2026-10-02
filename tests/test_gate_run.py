"""The run of a gate on three platforms: what it refuses, and what it runs.

No test here starts Docker or uv, and no test reads the real corpus or the
real bench/out: main() runs with the runners, the snapshot, the corpus check
and OUT all replaced (the gate fixture); only the commands that the host and
the container would run are real.
"""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

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

    def host(snapshot, gate, **keywords):
        recorded["runs"].append(("the host", gate, snapshot))
        return 0

    def container(snapshot, gate, architecture, **keywords):
        recorded["runs"].append((architecture, gate, snapshot))
        return 0

    monkeypatch.setattr(run, "OUT", tmp_path)
    monkeypatch.setattr(run, "tree_state", lambda root: checks.TreeState("abc", ()))
    monkeypatch.setattr(run, "verify_corpus", lambda: True)
    monkeypatch.setattr(run, "tree_archive", take_snapshot)
    monkeypatch.setattr(run, "run_host", host)
    monkeypatch.setattr(run, "run_container", container)
    return recorded


@pytest.mark.parametrize("name", ["m1", "m4a"])
@pytest.mark.parametrize("architecture", ["arm64", "amd64"])
def test_the_container_reads_the_corpus_read_only(architecture, name):
    command = run.container_command(
        name, architecture, commit="c0ffee", corpus=Path("/host/corpus"), out=Path("/host/out")
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
        inside.find(
            f"bench/gates/{name}.py --commit c0ffee --out /out/{name}-linux-{architecture}.json"
        ),
    ]
    assert -1 not in found_at
    assert found_at == sorted(found_at)


def test_uncommitted_changes_are_refused(gate, monkeypatch):
    monkeypatch.setattr(run, "tree_state", lambda root: checks.TreeState("abc", (" M README.md",)))
    assert run.main(["m4a"]) == 2
    assert gate["snapshots"] == []
    assert gate["runs"] == []


def test_an_incomplete_corpus_is_refused(gate, monkeypatch):
    monkeypatch.setattr(run, "verify_corpus", lambda: False)
    assert run.main(["m4a"]) == 2
    assert gate["snapshots"] == []
    assert gate["runs"] == []


def test_every_platform_runs_the_same_snapshot(gate, monkeypatch):
    snapshots = iter([b"first", b"second", b"third", b"fourth"])
    monkeypatch.setattr(run, "tree_archive", lambda root: next(snapshots))
    assert run.main(["m4a"]) == 0
    assert gate["runs"] == [
        ("the host", "m4a", b"first"),
        ("arm64", "m4a", b"first"),
        ("amd64", "m4a", b"first"),
    ]


def test_one_failing_platform_fails_the_run(gate, monkeypatch):
    def container(snapshot, name, architecture, **keywords):
        gate["runs"].append((architecture, name, snapshot))
        return 1 if architecture == "amd64" else 0

    monkeypatch.setattr(run, "run_container", container)
    assert run.main(["m1"]) == 1


def test_no_receipt_of_an_earlier_run_is_left(gate, monkeypatch, tmp_path):
    stale = [
        tmp_path / run.host_receipt("m4a"),
        *(tmp_path / run.container_receipt("m4a", a) for a in checks.LINUX_ARCHITECTURES),
    ]
    for path in stale:
        path.write_text("stale", encoding="utf-8")
    other = tmp_path / run.host_receipt("m1")
    other.write_text("another gate's", encoding="utf-8")
    monkeypatch.setattr(run, "run_host", lambda snapshot, name, **keywords: 1)
    monkeypatch.setattr(run, "run_container", lambda snapshot, name, architecture, **keywords: 1)
    assert run.main(["m4a"]) == 1
    assert not any(path.exists() for path in stale)
    assert other.exists()


@pytest.mark.parametrize("arguments", [[], ["m2"], ["m1", "m4a"], ["m1", "--commit", "abc"]])
def test_one_gate_compare_knows_is_named(gate, arguments):
    with pytest.raises(SystemExit) as raised:
        run.main(arguments)
    assert raised.value.code == 2
    assert gate["snapshots"] == []
    assert gate["runs"] == []


@pytest.mark.parametrize("name", ["m1", "m4a"])
def test_the_host_runs_the_gate_named(monkeypatch, tmp_path, name):
    """The host's steps, run in a copy of an empty snapshot: the last one runs
    the gate's script and writes the receipt of its name."""
    empty = io.BytesIO()
    with tarfile.open(fileobj=empty, mode="w"):
        pass
    steps = []

    def record(step, **keywords):
        steps.append(step)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run.subprocess, "run", record)
    out = tmp_path / "out"
    status = run.run_host(empty.getvalue(), name, commit="c0ffee", corpus=tmp_path, out=out)
    assert status == 0
    assert steps[-1][-5:] == [
        f"bench/gates/{name}.py",
        "--commit",
        "c0ffee",
        "--out",
        str(out / run.host_receipt(name)),
    ]
    assert run.host_receipt(name).startswith(f"{name}-")
