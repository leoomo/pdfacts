"""The script that decides whether a change may be merged runs the agreed checks.

No test here runs the real checks; that would run the test suite inside
itself. Steps, the Python of the wheel environment and the runners are
replaced by small programs built in the test.
"""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import checks  # noqa: E402


def python(code: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code, *arguments)


def test_the_checks_are_the_agreed_ones():
    # The list is the specification: a check that is dropped has to fail here.
    commands = [" ".join(step.command) for step in checks.steps() if step.command]
    assert commands == [
        "uv sync --locked",
        f"uv run --locked python -c {checks.PLATFORM_REPORT}",
        "uv run --locked ruff check",
        "uv run --locked ruff format --check",
        "uv run --locked python tools/check_architecture.py",
        "uv run --locked pytest",
        "uv build --out-dir {scratch}/dist",
    ]
    assert checks.steps()[-1].action is checks.check_wheel


def test_a_failing_check_does_not_stop_the_checks_after_it(tmp_path):
    marker = tmp_path / "second-ran"
    outcomes = checks.run_steps(
        [
            checks.Step("fails", python("raise SystemExit(3)")),
            checks.Step("runs anyway", python(f"open({str(marker)!r}, 'w').close()")),
        ],
        tmp_path,
    )
    assert [(o.name, o.passed) for o in outcomes] == [("fails", False), ("runs anyway", True)]
    assert marker.exists()
    assert checks.exit_status(outcomes) == 1


def test_a_check_that_raises_is_a_failed_check_and_the_run_goes_on(tmp_path):
    def broken(checkout, scratch):
        raise RuntimeError("a check with a bug")

    outcomes = checks.run_steps(
        [checks.Step("broken", action=broken), checks.Step("next", python("pass"))], tmp_path
    )
    assert [(o.name, o.passed) for o in outcomes] == [("broken", False), ("next", True)]


def test_a_missing_program_is_a_failed_check_not_a_crash(tmp_path):
    outcomes = checks.run_steps([checks.Step("missing", ("pdfacts-no-such-program",))], tmp_path)
    assert [(o.name, o.passed) for o in outcomes] == [("missing", False)]


def test_all_checks_passing_gives_status_zero(tmp_path):
    outcomes = checks.run_steps([checks.Step("passes", python("pass"))], tmp_path)
    assert checks.exit_status(outcomes) == 0


def test_no_checks_is_not_a_pass():
    assert checks.exit_status([]) == 1


def test_the_scratch_directory_is_given_to_commands(tmp_path):
    step = checks.Step(
        "scratch",
        python("import os, sys; sys.exit(0 if os.path.isdir(sys.argv[1]) else 1)", "{scratch}"),
    )
    assert [o.passed for o in checks.run_steps([step], tmp_path)] == [True]


def test_checks_do_not_inherit_another_environment():
    environment = {name: "/elsewhere" for name in checks.LEAKING_VARIABLES} | {"HOME": "/h"}
    assert checks.clean_environment(environment) == {"HOME": "/h"}
    for name in ("VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON"):
        assert name in checks.LEAKING_VARIABLES


def test_an_import_from_the_checkout_is_recognised(tmp_path):
    checkout = tmp_path / "checkout"
    inside = checkout / "src" / "pdfacts" / "__init__.py"
    outside = tmp_path / "wheel-env" / "lib" / "pdfacts" / "__init__.py"
    for path in (inside, outside):
        path.parent.mkdir(parents=True)
        path.touch()
    assert checks.imported_from(inside, checkout)
    assert not checks.imported_from(outside, checkout)


# check_wheel: the environment is not created for real. A small program takes
# the place of its Python and answers the import the way the test needs.


@pytest.fixture
def wheel_setup(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".python-version").write_text("3.13\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    (scratch / "dist").mkdir(parents=True)
    monkeypatch.setattr(checks, "_run", lambda command, cwd: True)

    def add_wheels(count: int) -> None:
        for number in range(count):
            (scratch / "dist" / f"pdfacts-0.0.{number}-py3-none-any.whl").touch()

    def fake_python(*, prints: str, status: int) -> None:
        program = scratch / "wheel-env" / "bin" / "python"
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text(f"#!/bin/sh\necho '{prints}'\nexit {status}\n", encoding="utf-8")
        program.chmod(program.stat().st_mode | stat.S_IXUSR)

    return checkout, scratch, add_wheels, fake_python


def test_the_wheel_check_needs_exactly_one_wheel(wheel_setup):
    checkout, scratch, add_wheels, fake_python = wheel_setup
    fake_python(prints="/elsewhere/pdfacts/__init__.py", status=0)
    assert not checks.check_wheel(checkout, scratch)
    add_wheels(2)
    assert not checks.check_wheel(checkout, scratch)


def test_the_wheel_check_fails_when_the_import_fails(wheel_setup):
    checkout, scratch, add_wheels, fake_python = wheel_setup
    add_wheels(1)
    # Something is printed before the failure, as a traceback would be.
    fake_python(prints="/elsewhere/pdfacts/__init__.py", status=1)
    assert not checks.check_wheel(checkout, scratch)


def test_the_wheel_check_fails_when_pdfacts_comes_from_the_checkout(wheel_setup):
    checkout, scratch, add_wheels, fake_python = wheel_setup
    add_wheels(1)
    (checkout / "src" / "pdfacts").mkdir(parents=True)
    (checkout / "src" / "pdfacts" / "__init__.py").touch()
    fake_python(prints=str(checkout / "src" / "pdfacts" / "__init__.py"), status=0)
    assert not checks.check_wheel(checkout, scratch)


def test_the_wheel_check_passes_when_the_installed_wheel_is_imported(wheel_setup):
    checkout, scratch, add_wheels, fake_python = wheel_setup
    add_wheels(1)
    fake_python(prints=str(scratch / "wheel-env" / "lib" / "pdfacts" / "__init__.py"), status=0)
    assert checks.check_wheel(checkout, scratch)


def git(repository: Path, *arguments: str) -> None:
    # The user's own git configuration (signing, hooks) must not change the result.
    environment = os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.org", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        env=environment,
    )


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "--quiet")
    (tmp_path / ".gitignore").write_text(".venv/\nbench/corpus/files/\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("deleted later", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "start")
    return tmp_path


def dirty(repository: Path) -> Path:
    (repository / "deleted.txt").unlink()
    for name in (".venv/lib/x.py", "bench/corpus/files/a.pdf", "new/untracked.txt"):
        (repository / name).parent.mkdir(parents=True, exist_ok=True)
        (repository / name).write_text(name, encoding="utf-8")
    return repository


def test_the_copy_contains_what_git_would_commit(repository):
    assert checks.files_to_copy(dirty(repository)) == [
        ".gitignore",
        "new/untracked.txt",
        "tracked.txt",
    ]


def test_the_archive_for_a_container_contains_the_same_files(repository):
    dirty(repository)
    with tarfile.open(fileobj=io.BytesIO(checks.tree_archive(repository))) as archive:
        assert sorted(archive.getnames()) == checks.files_to_copy(repository)


def stub_checks(repository: Path, code: str) -> None:
    """Replace tools/checks.py in the repository by a program that exits with a known status."""
    stub = repository / "tools" / "checks.py"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(code, encoding="utf-8")


def test_the_copy_on_this_machine_has_the_content_of_the_snapshot(repository):
    dirty(repository)
    stub_checks(
        repository,
        "import pathlib, sys\n"
        "ok = pathlib.Path('new/untracked.txt').read_text() == 'new/untracked.txt'\n"
        "ok = ok and not pathlib.Path('.venv').exists()\n"
        "sys.exit(0 if ok else 3)\n",
    )
    assert checks.run_isolated(checks.tree_archive(repository)) == 0


def test_a_clean_tree_is_reported_as_clean(repository):
    state = checks.tree_state(repository)
    assert state.changes == ()
    assert len(state.commit) == 40
    assert state.describe() == f"commit {state.commit}, working tree clean, origin/main unknown"


def test_whether_the_commit_contains_origin_main_is_reported(repository):
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert checks.tree_state(repository).contains_main is True
    assert "contains origin/main as last fetched" in checks.tree_state(repository).describe()
    # origin/main moves on to a commit that HEAD does not have.
    git(repository, "switch", "--quiet", "--create", "other")
    (repository / "other.txt").write_text("other", encoding="utf-8")
    git(repository, "add", "other.txt")
    git(repository, "commit", "--quiet", "-m", "other")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repository, "switch", "--quiet", "-")
    state = checks.tree_state(repository)
    assert state.contains_main is False
    assert "does NOT contain origin/main" in state.describe()


@pytest.mark.parametrize(
    ("change", "path"),
    [("modified", "tracked.txt"), ("new", "new.txt"), ("deleted", "deleted.txt")],
)
def test_every_uncommitted_change_is_reported(repository, change, path):
    target = repository / path
    if change == "deleted":
        target.unlink()
    else:
        target.write_text("changed", encoding="utf-8")
    state = checks.tree_state(repository)
    assert len(state.changes) == 1 and path in state.changes[0]
    assert "1 uncommitted change" in state.describe()


def test_an_ignored_file_is_not_a_change(repository):
    (repository / ".venv").mkdir()
    (repository / ".venv" / "x").write_text("x", encoding="utf-8")
    assert checks.tree_state(repository).changes == ()


def test_a_failed_run_in_the_copy_is_reported(repository):
    stub_checks(repository, "import sys\nsys.exit(3)\n")
    assert checks.run_isolated(checks.tree_archive(repository)) == 3


@pytest.fixture
def runners(monkeypatch):
    calls: list[tuple[str, bytes]] = []
    results = {"isolated": 0, "arm64": 0, "amd64": 0}
    snapshots = iter([b"first snapshot", b"second snapshot", b"third snapshot"])

    def isolated(snapshot):
        calls.append(("isolated", snapshot))
        return results["isolated"]

    def container(snapshot, architecture):
        calls.append((architecture, snapshot))
        return results[architecture]

    monkeypatch.setattr(checks, "run_isolated", isolated)
    monkeypatch.setattr(checks, "run_in_container", container)
    monkeypatch.setattr(checks, "tree_state", lambda checkout: checks.TreeState("abc", ()))
    # Every call gives a different snapshot, as it would if the tree changed during the run.
    monkeypatch.setattr(checks, "tree_archive", lambda checkout: next(snapshots))
    return calls, results


def test_all_runs_every_platform_and_passes_when_all_pass(runners):
    calls, results = runners
    assert checks.main(["--all"]) == 0
    assert [name for name, _ in calls] == ["isolated", "arm64", "amd64"]


def test_all_platforms_of_one_run_check_the_same_snapshot(runners):
    calls, results = runners
    checks.main(["--all"])
    assert {snapshot for _, snapshot in calls} == {b"first snapshot"}


def test_all_fails_when_one_platform_fails(runners):
    calls, results = runners
    results["amd64"] = 1
    assert checks.main(["--all"]) == 1


def test_all_refuses_uncommitted_changes(runners, monkeypatch):
    calls, results = runners
    monkeypatch.setattr(
        checks, "tree_state", lambda checkout: checks.TreeState("abc", (" M README.md",))
    )
    assert checks.main(["--all"]) == 2
    assert calls == []


@pytest.mark.parametrize("architecture", checks.LINUX_ARCHITECTURES)
def test_a_container_runs_the_same_script(architecture):
    command = checks.container_command(architecture)
    assert command[:2] == ["docker", "run"]
    assert f"linux/{architecture}" in command
    assert checks.CONTAINER_IMAGES[architecture] in command
    inside = command[-1]
    # The archive arrives on standard input and is unpacked before anything else runs.
    assert inside.index("tar -x") < inside.index("apt-get") < inside.index("pip install")
    assert f"uv=={checks.UV_VERSION}" in inside
    # The tests of the copy build a git repository; the image has no git.
    assert " git " in inside
    assert inside.endswith("python3 tools/checks.py --in-place")


def test_the_container_images_have_the_python_of_the_project():
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert set(checks.CONTAINER_IMAGES) == {"arm64", "amd64"}
    for image in checks.CONTAINER_IMAGES.values():
        assert image.startswith(f"python:{version}-slim@sha256:")
    # One digest per architecture.
    assert len(set(checks.CONTAINER_IMAGES.values())) == 2
