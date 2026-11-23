"""scripts/datastore.sh against a throwaway bare remote, from a shallow
clone the way actions/checkout makes one."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "datastore.sh"
IDENTITY = {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env={**os.environ, **IDENTITY}).stdout.strip()


def datastore(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True,
                          text=True, env={**os.environ, **IDENTITY})


@pytest.fixture
def remote(tmp_path) -> Path:
    """A bare 'GitHub' with one commit on main and no data branch."""
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "init", "--quiet", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("main\n")
    git(seed, "add", "README.md")
    git(seed, "commit", "--quiet", "-m", "main")
    git(seed, "push", "--quiet", str(bare), "main")
    return bare


def ci_checkout(remote: Path, name: str) -> Path:
    """Shallow single-branch clone of main, like actions/checkout."""
    path = remote.parent / name
    git(remote.parent, "clone", "--quiet", "--depth=1", f"file://{remote}", str(path))
    return path


def test_first_open_starts_an_empty_orphan_branch(remote):
    ci = ci_checkout(remote, "run1")
    result = datastore(ci, "open", ".datastore")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "exists=false"
    assert not any(p for p in (ci / ".datastore").iterdir() if p.name != ".git")


def test_save_then_reopen_round_trips(remote):
    ci = ci_checkout(remote, "run1")
    datastore(ci, "open", ".datastore")
    (ci / ".datastore" / "raw").mkdir()
    (ci / ".datastore" / "raw" / "batch.parquet").write_bytes(b"PAR1 rows")
    saved = datastore(ci, "save", ".datastore", "data: run 1")
    assert saved.returncode == 0, saved.stderr

    later = ci_checkout(remote, "run2")
    reopened = datastore(later, "open", ".datastore")
    assert reopened.stdout.strip() == "exists=true"
    assert (later / ".datastore" / "raw" / "batch.parquet").read_bytes() == b"PAR1 rows"


def test_an_unchanged_branch_adds_no_commit(remote):
    ci = ci_checkout(remote, "run1")
    datastore(ci, "open", ".datastore")
    (ci / ".datastore" / "f").write_text("x")
    datastore(ci, "save", ".datastore", "data: run 1")
    tip = git(remote, "rev-parse", "data")

    later = ci_checkout(remote, "run2")
    datastore(later, "open", ".datastore")
    quiet = datastore(later, "save", ".datastore", "data: run 2")
    assert quiet.returncode == 0
    assert "nothing to commit" in quiet.stderr
    assert git(remote, "rev-parse", "data") == tip


def test_each_changed_run_appends_one_commit_from_a_shallow_parent(remote):
    for run in (1, 2, 3):
        ci = ci_checkout(remote, f"run{run}")
        datastore(ci, "open", ".datastore")
        (ci / ".datastore" / f"batch{run}").write_text(str(run))
        assert datastore(ci, "save", ".datastore", f"data: run {run}").returncode == 0
    assert git(remote, "rev-list", "--count", "data") == "3"
    files = git(remote, "ls-tree", "--name-only", "data").split()
    assert files == ["batch1", "batch2", "batch3"]


def test_data_never_touches_main(remote):
    main_before = git(remote, "rev-parse", "main")
    ci = ci_checkout(remote, "run1")
    datastore(ci, "open", ".datastore")
    (ci / ".datastore" / "f").write_text("x")
    datastore(ci, "save", ".datastore", "data: run 1")
    assert git(remote, "rev-parse", "main") == main_before
    # an orphan: no shared history with main at all
    merge_base = subprocess.run(["git", "merge-base", "main", "data"], cwd=remote,
                                capture_output=True, text=True)
    assert merge_base.returncode != 0


def test_bad_usage_fails_loudly(remote):
    ci = ci_checkout(remote, "run1")
    assert datastore(ci, "open").returncode == 2
    assert datastore(ci, "save", ".datastore").returncode == 2
    assert datastore(ci, "frobnicate", ".datastore").returncode == 2
