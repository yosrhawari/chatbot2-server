import hashlib
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from ingest import _detect_separator, _deterministic_ids, dataframe_summary
from langchain_core.documents import Document


class TestDetectSeparator:
    def test_comma(self):
        content = "a,b,c\n1,2,3\n4,5,6"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            assert _detect_separator(path) == ","
        finally:
            Path(path).unlink(missing_ok=True)

    def test_semicolon(self):
        content = "a;b;c\n1;2;3\n4;5;6"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            assert _detect_separator(path) == ";"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_tab(self):
        content = "a\tb\tc\n1\t2\t3\n4\t5\t6"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            assert _detect_separator(path) == "\t"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_pipe(self):
        content = "a|b|c\n1|2|3\n4|5|6"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            assert _detect_separator(path) == "|"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_single_column(self):
        content = "a\n1\n2\n3"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            assert _detect_separator(path) == ","
        finally:
            Path(path).unlink(missing_ok=True)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            assert _detect_separator(path) == ","
        finally:
            Path(path).unlink(missing_ok=True)


class TestDeterministicIds:
    def test_same_input_same_id(self):
        chunks = [
            Document(page_content="hello", metadata={"source": "doc1.txt"}),
            Document(page_content="world", metadata={"source": "doc1.txt"}),
        ]
        ids1 = _deterministic_ids(chunks)
        ids2 = _deterministic_ids(chunks)
        assert ids1 == ids2

    def test_different_content_different_id(self):
        chunks_a = [
            Document(page_content="hello", metadata={"source": "doc1.txt"})
        ]
        chunks_b = [
            Document(page_content="world", metadata={"source": "doc1.txt"})
        ]
        assert _deterministic_ids(chunks_a) != _deterministic_ids(chunks_b)

    def test_same_content_different_source_different_id(self):
        chunks_a = [
            Document(page_content="hello", metadata={"source": "doc1.txt"})
        ]
        chunks_b = [
            Document(page_content="hello", metadata={"source": "doc2.txt"})
        ]
        assert _deterministic_ids(chunks_a) != _deterministic_ids(chunks_b)

    def test_id_length(self):
        chunks = [
            Document(page_content="test", metadata={"source": "doc.txt"})
        ]
        ids = _deterministic_ids(chunks)
        assert len(ids[0]) == 40

    def test_unknown_source(self):
        chunks = [
            Document(page_content="test", metadata={})
        ]
        ids = _deterministic_ids(chunks)
        assert len(ids[0]) == 40


class TestDataframeSummary:
    def test_basic_summary(self):
        df = pd.DataFrame({"name": ["Alice"], "age": [30], "score": [95.5]})
        summary = dataframe_summary(df)
        assert "summary" in summary
        assert summary["summary"]["rows"] == 1
        assert len(summary["summary"]["columns"]) == 3

    def test_column_names_and_dtypes(self):
        df = pd.DataFrame({"int_col": [1], "float_col": [1.5], "str_col": ["x"]})
        summary = dataframe_summary(df)
        columns = {c["name"]: c["dtype"] for c in summary["summary"]["columns"]}
        assert "int_col" in columns
        assert "float_col" in columns
        assert "str_col" in columns

    def test_empty_dataframe(self):
        df = pd.DataFrame({"a": pd.Series(dtype="int64")})
        summary = dataframe_summary(df)
        assert summary["summary"]["rows"] == 0

    def test_no_row_data_leak(self):
        df = pd.DataFrame({"secret": ["this-should-not-appear"]})
        summary = dataframe_summary(df)
        raw = str(summary)
        assert "this-should-not-appear" not in raw
