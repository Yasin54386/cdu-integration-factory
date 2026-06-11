"""Preprocessing rules: type handling, truncation, hard caps (spec §6)."""

from __future__ import annotations

import pytest

from pipeline.core.preprocess import (
    PER_FILE_CHAR_CAP,
    PROMPT_CHAR_CAP,
    PreprocessError,
    enforce_prompt_cap,
    preprocess_file,
)


def test_sql_is_verbatim(tmp_path):
    sql = "SELECT *\nFROM students;\n"
    path = tmp_path / "q.sql"
    path.write_text(sql)
    assert preprocess_file(path) == sql


def test_csv_sample_truncated_to_20_rows_with_note(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text("\n".join(f"row{i}" for i in range(50)))
    text = preprocess_file(path)
    assert "row19" in text and "row20" not in text
    assert "first 20 of 50 rows" in text


def test_short_csv_not_truncated(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text("a\nb\nc")
    assert preprocess_file(path) == "a\nb\nc"


def test_unsupported_extension_fails(tmp_path):
    path = tmp_path / "rules.exe"
    path.write_bytes(b"\x00")
    with pytest.raises(PreprocessError, match="unsupported file type"):
        preprocess_file(path)


def test_per_file_cap_fails_loudly(tmp_path):
    path = tmp_path / "big.sql"
    path.write_text("-- x\n" * (PER_FILE_CHAR_CAP // 4))
    with pytest.raises(PreprocessError, match="per-file cap"):
        preprocess_file(path)


def test_prompt_cap_fails_loudly():
    with pytest.raises(PreprocessError, match="cap"):
        enforce_prompt_cap("x" * (PROMPT_CHAR_CAP + 1))
    assert enforce_prompt_cap("ok") == "ok"


def test_xlsx_renders_markdown_table(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Map"
    sheet.append(["source", "target"])
    sheet.append(["STUDENT_ID", "StudentId"])
    path = tmp_path / "map.xlsx"
    workbook.save(path)
    text = preprocess_file(path)
    assert "### Sheet: Map" in text
    assert "| source | target |" in text
    assert "| STUDENT_ID | StudentId |" in text


def test_docx_extracts_paragraphs_and_tables(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Business rule: active students only.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "field"
    table.rows[0].cells[1].text = "rule"
    path = tmp_path / "brd.docx"
    document.save(path)
    text = preprocess_file(path)
    assert "active students only" in text
    assert "field | rule" in text
