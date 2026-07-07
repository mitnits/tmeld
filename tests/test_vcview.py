"""VC view tests: git status model, diff-on-Enter, commit, revert."""

import asyncio
import os
import subprocess

import pytest

from tmeld.app import TmeldApp
from tmeld.filediff import FileDiffView
from tmeld.vcview import CommitScreen, VcComparison, VcView
from tmeld.dircompare import STATE_CONFLICT, STATE_MODIFIED


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "tmeld tests")
    (repo / "a.py").write_text("original a\n")
    (repo / "b.py").write_text("original b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "initial")
    # working-copy changes: one modified, one untracked
    (repo / "a.py").write_text("changed a\n")
    (repo / "untracked.py").write_text("new\n")
    return repo


def run(coro):
    return asyncio.run(coro)


async def scanned(app):
    await app.workers.wait_for_complete()


def test_vc_comparison_model(repo):
    comp = VcComparison(str(repo))
    assert comp.vc.NAME == "Git"
    for _ in comp.scan_iter():
        pass
    rows = comp.root_entry.children
    names = [r.names[0] for r in rows]
    # default filters: modified only; untracked stays hidden
    assert any(n.startswith("a.py") and "Modified" in n for n in names)
    assert not any("untracked" in n for n in names)
    assert rows[0].state == STATE_MODIFIED


def test_vc_view_scans_and_reports(repo):
    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            view = app.view
            assert isinstance(view, VcView)
            assert view.status_text == "1 changes"
            assert view.tab_label == "repo [Git]"

    run(scenario())


def test_enter_opens_repo_vs_working_tab(repo):
    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            app.action_next_chunk()  # cursor onto the a.py row
            await pilot.press("enter")
            await pilot.pause()
            view = app.view
            assert isinstance(view, FileDiffView)
            assert view.tab_label == "a.py (repository, working)"
            # left pane: committed content, read-only
            assert view.comparison.lines[0] == ["original a"]
            assert view.comparison.lines[1] == ["changed a"]
            assert view.panes[0].read_only
            assert not view.panes[1].read_only
            assert view.comparison.differ.diff_count() == 1

    run(scenario())


def test_commit_flow(repo):
    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, CommitScreen)
            assert "a.py" in app.screen.files
            await pilot.press(*"fix a")
            await pilot.press("enter")
            await scanned(app)  # commit runs + rescan worker
            await pilot.pause()
            assert app.view.status_text == "clean"

    run(scenario())
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "fix a"


def test_commit_escape_cancels(repo):
    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, CommitScreen)
            assert app.view.status_text == "1 changes"

    run(scenario())


def test_revert_needs_confirmation(repo):
    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            app.action_next_chunk()  # onto a.py
            await pilot.press("r")
            await pilot.pause()
            assert (repo / "a.py").read_text() == "changed a\n"
            await pilot.press("r")
            await scanned(app)
            await pilot.pause()
            assert (repo / "a.py").read_text() == "original a\n"
            assert app.view.status_text == "clean"

    run(scenario())


@pytest.fixture
def conflict_repo(tmp_path):
    repo = tmp_path / "conflict"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "tmeld tests")
    (repo / "f.py").write_text("line one\nbase\nline three\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.py").write_text("line one\nfeature change\nline three\n")
    git(repo, "commit", "-q", "-am", "feature")
    git(repo, "checkout", "-q", "main")
    (repo / "f.py").write_text("line one\nmain change\nline three\n")
    git(repo, "commit", "-q", "-am", "main")
    result = subprocess.run(
        ["git", "-C", str(repo), "merge", "feature"], capture_output=True
    )
    assert result.returncode != 0  # conflict expected
    return repo


def test_conflict_opens_three_way_resolve(conflict_repo):
    async def scenario():
        app = TmeldApp([str(conflict_repo)])
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            rows = app.view.dirtree.rows
            conflict_rows = [
                i for i, (e, _d, _k) in enumerate(rows)
                if e.state == STATE_CONFLICT
            ]
            assert conflict_rows, "conflicted file not shown"
            app.view.dirtree._move_cursor(conflict_rows[0])
            await pilot.press("enter")
            await pilot.pause()
            view = app.view
            assert isinstance(view, FileDiffView)
            assert view.num_panes == 3
            assert view.tab_label == "f.py (remote, merge, local)"
            assert view.panes[0].read_only and view.panes[2].read_only
            # middle-pane saves go to the working file (-o machinery)
            working = os.path.join(str(conflict_repo), "f.py")
            assert view.comparison.save_paths[1] == working
            # resolve: take the local side into the middle, save
            view.panes[1].focus()
            await pilot.pause()
            view.panes[1].move_cursor((1, 0))
            view.action_pull_right()  # pull from local (right neighbor)
            await pilot.pause()
            app.save_pane(1)
            await pilot.pause()
            assert "main change" in (conflict_repo / "f.py").read_text()
            assert view.merge_resolved()

    run(scenario())
