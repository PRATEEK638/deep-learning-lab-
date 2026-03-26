"""Deterministic replay tests for file_tool.

These tests exercise the core file operations without any LLM calls,
verifying guardrails (whitelist, blocked dirs/exts, confirmation gate,
max file size) and correct behaviour (list, read, copy, move, rename,
delete).
"""

from __future__ import annotations

import pathlib

import pytest

from friday.policy import PolicyEngine
from friday.tools.file_tool import FileTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    """A writable temp directory used as the fs whitelist."""
    return tmp_path


@pytest.fixture
def file_tool(workspace):
    """FileTool whose whitelist is just `workspace`."""
    cfg = {
        "policy": {
            "blocked_directories": [],
            "blocked_extensions": [".key", ".pem"],
            "require_confirmation_on_delete": True,
            "max_file_size_mb": 1,
            "cloud_enabled": False,
        },
        "sandbox": {
            "fs_whitelist": [str(workspace)],
            "tool_timeout_seconds": 10,
        },
    }
    policy = PolicyEngine(cfg)
    return FileTool(policy=policy, config=cfg)


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


class TestList:
    def test_list_directory(self, file_tool, workspace):
        (workspace / "a.txt").write_text("hello")
        (workspace / "b.txt").write_text("world")
        result = file_tool.run(action="list", path=str(workspace))
        assert result.success
        names = {e["name"] for e in result.data}
        assert {"a.txt", "b.txt"} <= names

    def test_list_single_file(self, file_tool, workspace):
        f = workspace / "readme.md"
        f.write_text("# Hello")
        result = file_tool.run(action="list", path=str(f))
        assert result.success
        assert result.data[0]["name"] == "readme.md"

    def test_list_nonexistent_fails(self, file_tool, workspace):
        result = file_tool.run(action="list", path=str(workspace / "ghost"))
        assert not result.success

    def test_list_outside_whitelist_fails(self, file_tool, tmp_path):
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        result = file_tool.run(action="list", path=str(outside))
        assert not result.success

    def test_list_blocked_extension(self, file_tool, workspace):
        key_file = workspace / "id_rsa.key"
        key_file.write_text("PRIVATE KEY")
        result = file_tool.run(action="read_text", path=str(key_file))
        assert not result.success  # policy blocks .key files


# ---------------------------------------------------------------------------
# READ_TEXT
# ---------------------------------------------------------------------------


class TestReadText:
    def test_read_text(self, file_tool, workspace):
        f = workspace / "note.txt"
        f.write_text("Hello FRIDAY")
        result = file_tool.run(action="read_text", path=str(f))
        assert result.success
        assert "Hello FRIDAY" in result.data

    def test_read_text_too_large(self, file_tool, workspace):
        f = workspace / "big.txt"
        # Write > 1 MB
        f.write_bytes(b"x" * (1024 * 1024 + 1))
        result = file_tool.run(action="read_text", path=str(f))
        assert not result.success
        assert "limit" in result.error.lower() or "exceed" in result.error.lower()

    def test_read_directory_fails(self, file_tool, workspace):
        subdir = workspace / "subdir"
        subdir.mkdir()
        result = file_tool.run(action="read_text", path=str(subdir))
        assert not result.success


# ---------------------------------------------------------------------------
# COPY
# ---------------------------------------------------------------------------


class TestCopy:
    def test_copy_file(self, file_tool, workspace):
        src = workspace / "src.txt"
        src.write_text("original")
        dst = workspace / "dst.txt"
        result = file_tool.run(action="copy", path=str(src), destination=str(dst))
        assert result.success
        assert dst.exists()
        assert dst.read_text() == "original"
        assert src.exists()  # source preserved

    def test_copy_directory(self, file_tool, workspace):
        srcdir = workspace / "srcdir"
        srcdir.mkdir()
        (srcdir / "file.txt").write_text("data")
        dstdir = workspace / "dstdir"
        result = file_tool.run(action="copy", path=str(srcdir), destination=str(dstdir))
        assert result.success
        assert (dstdir / "file.txt").exists()


# ---------------------------------------------------------------------------
# MOVE
# ---------------------------------------------------------------------------


class TestMove:
    def test_move_requires_confirmation(self, file_tool, workspace):
        src = workspace / "src_move.txt"
        src.write_text("move me")
        dst = workspace / "dst_move.txt"
        result = file_tool.run(
            action="move", path=str(src), destination=str(dst), confirmed=False
        )
        assert result.success  # returns a confirmation request, not an error
        assert isinstance(result.data, dict)
        assert result.data.get("requires_confirmation") is True

    def test_move_confirmed(self, file_tool, workspace):
        src = workspace / "src_move2.txt"
        src.write_text("move me 2")
        dst = workspace / "dst_move2.txt"
        result = file_tool.run(
            action="move", path=str(src), destination=str(dst), confirmed=True
        )
        assert result.success
        assert dst.exists()
        assert not src.exists()


# ---------------------------------------------------------------------------
# RENAME
# ---------------------------------------------------------------------------


class TestRename:
    def test_rename_file(self, file_tool, workspace):
        src = workspace / "old_name.txt"
        src.write_text("rename me")
        new_name = "new_name.txt"
        result = file_tool.run(action="rename", path=str(src), destination=new_name)
        assert result.success
        assert (workspace / new_name).exists()
        assert not src.exists()


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_requires_confirmation(self, file_tool, workspace):
        f = workspace / "del_me.txt"
        f.write_text("delete me")
        result = file_tool.run(action="delete", path=str(f), confirmed=False)
        assert result.success
        assert isinstance(result.data, dict)
        assert result.data.get("requires_confirmation") is True
        assert f.exists()  # NOT deleted yet

    def test_delete_confirmed(self, file_tool, workspace):
        f = workspace / "gone.txt"
        f.write_text("bye")
        result = file_tool.run(action="delete", path=str(f), confirmed=True)
        assert result.success
        assert not f.exists()

    def test_delete_nonexistent(self, file_tool, workspace):
        result = file_tool.run(action="delete", path=str(workspace / "ghost.txt"), confirmed=True)
        assert not result.success

    def test_delete_directory_confirmed(self, file_tool, workspace):
        d = workspace / "rmdir"
        d.mkdir()
        (d / "child.txt").write_text("bye")
        result = file_tool.run(action="delete", path=str(d), confirmed=True)
        assert result.success
        assert not d.exists()


# ---------------------------------------------------------------------------
# UNKNOWN ACTION
# ---------------------------------------------------------------------------


class TestUnknownAction:
    def test_unknown_action_fails(self, file_tool, workspace):
        result = file_tool.run(action="fly", path=str(workspace))
        assert not result.success
