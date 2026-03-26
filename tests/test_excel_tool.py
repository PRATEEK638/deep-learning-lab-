"""Tests for excel_tool."""

from __future__ import annotations

import pathlib

import pytest

openpyxl = pytest.importorskip("openpyxl", reason="openpyxl not installed")

from friday.policy import PolicyEngine
from friday.tools.excel_tool import ExcelTool


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def excel_tool(workspace):
    cfg = {
        "policy": {
            "blocked_directories": [],
            "blocked_extensions": [".key"],
            "max_excel_rows": 1000,
            "cloud_enabled": False,
        },
        "sandbox": {"fs_whitelist": [str(workspace)]},
    }
    policy = PolicyEngine(cfg)
    return ExcelTool(policy=policy, config=cfg)


class TestListSheets:
    def test_list_sheets(self, excel_tool, workspace):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.title = "Alpha"
        wb.create_sheet("Beta")
        path = workspace / "test.xlsx"
        wb.save(path)

        result = excel_tool.run(action="list_sheets", path=str(path))
        assert result.success
        assert "Alpha" in result.data
        assert "Beta" in result.data


class TestWriteAndRead:
    def test_write_then_read(self, excel_tool, workspace):
        path = workspace / "data.xlsx"
        rows = [["Name", "Score"], ["Alice", 95], ["Bob", 87]]
        write_result = excel_tool.run(action="write_sheet", path=str(path), rows=rows)
        assert write_result.success
        assert path.exists()

        read_result = excel_tool.run(action="read_sheet", path=str(path), header=True)
        assert read_result.success
        data = read_result.data
        assert data["columns"] == ["Name", "Score"]
        assert ["Alice", 95] in data["rows"]

    def test_write_no_header(self, excel_tool, workspace):
        path = workspace / "noheader.xlsx"
        rows = [["x", 1], ["y", 2]]
        excel_tool.run(action="write_sheet", path=str(path), rows=rows)
        result = excel_tool.run(action="read_sheet", path=str(path), header=False)
        assert result.success
        assert result.data["columns"] is None


class TestAppendRows:
    def test_append(self, excel_tool, workspace):
        path = workspace / "append.xlsx"
        excel_tool.run(action="write_sheet", path=str(path), rows=[["A", "B"], [1, 2]])
        excel_tool.run(action="append_rows", path=str(path), rows=[[3, 4]])
        result = excel_tool.run(action="read_sheet", path=str(path), header=True)
        assert result.success
        assert len(result.data["rows"]) == 2  # [1,2] and [3,4]

    def test_append_nonexistent_fails(self, excel_tool, workspace):
        result = excel_tool.run(
            action="append_rows", path=str(workspace / "missing.xlsx"), rows=[[1]]
        )
        assert not result.success


class TestSummary:
    def test_summary(self, excel_tool, workspace):
        path = workspace / "summary.xlsx"
        rows = [["Col1", "Col2"]] + [[i, i * 2] for i in range(10)]
        excel_tool.run(action="write_sheet", path=str(path), rows=rows)
        result = excel_tool.run(action="summary", path=str(path))
        assert result.success
        assert result.data["total_rows"] == 10
        assert len(result.data["preview"]) <= 5


class TestMaxRows:
    def test_exceeds_max_rows_on_write(self, workspace):
        cfg = {
            "policy": {
                "blocked_directories": [],
                "blocked_extensions": [],
                "max_excel_rows": 5,
            },
            "sandbox": {"fs_whitelist": [str(workspace)]},
        }
        tool = ExcelTool(policy=PolicyEngine(cfg), config=cfg)
        path = workspace / "big.xlsx"
        rows = [[i] for i in range(10)]
        result = tool.run(action="write_sheet", path=str(path), rows=rows)
        assert not result.success
        assert "exceed" in result.error.lower() or "exceeding" in result.error.lower()


class TestUnsupportedExtension:
    def test_csv_rejected(self, excel_tool, workspace):
        path = workspace / "data.csv"
        path.write_text("a,b\n1,2")
        result = excel_tool.run(action="read_sheet", path=str(path))
        assert not result.success
