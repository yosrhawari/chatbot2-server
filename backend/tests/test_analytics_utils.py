import ast
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from analytics import (
    _validate_code,
    _df_to_records,
    format_result,
    convert_numpy,
    _maybe_parse_dates,
)


class TestValidateCode:
    def test_valid_code(self):
        code = "result = df.groupby('col').sum()"
        _validate_code(code)

    def test_rejects_import(self):
        with pytest.raises(ValueError, match="imports are not allowed"):
            _validate_code("import os\nresult = 1")

    def test_rejects_from_import(self):
        with pytest.raises(ValueError, match="imports are not allowed"):
            _validate_code("from os import path\nresult = 1")

    def test_rejects_eval(self):
        with pytest.raises(ValueError, match="eval"):
            _validate_code("result = eval('1+1')")

    def test_rejects_exec(self):
        with pytest.raises(ValueError, match="exec"):
            _validate_code("exec('x=1')\nresult = 1")

    def test_rejects_open(self):
        with pytest.raises(ValueError, match="open"):
            _validate_code("result = open('file.txt')")

    def test_rejects_dunder_attribute(self):
        with pytest.raises(ValueError, match="dunder"):
            _validate_code("result = df.__class__")

    def test_rejects_dunder_name(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_code("__import__('os')\nresult = 1")

    def test_allows_print(self):
        _validate_code("print('hello')\nresult = 1")

    def test_syntax_error_raises(self):
        with pytest.raises(ValueError, match="syntax"):
            _validate_code("result = ")


class TestConvertNumpy:
    def test_none(self):
        assert convert_numpy(None) is None

    def test_nat(self):
        assert convert_numpy(pd.NaT) is None

    def test_int(self):
        assert convert_numpy(42) == 42

    def test_numpy_int(self):
        assert convert_numpy(np.int64(42)) == 42

    def test_numpy_float(self):
        assert convert_numpy(np.float64(3.14)) == 3.14

    def test_numpy_nan(self):
        assert convert_numpy(np.nan) is None

    def test_numpy_bool(self):
        assert convert_numpy(np.bool_(True)) is True

    def test_numpy_array(self):
        result = convert_numpy(np.array([1, 2, 3]))
        assert result == [1, 2, 3]

    def test_timestamp(self):
        ts = pd.Timestamp("2024-01-15")
        assert convert_numpy(ts) == "2024-01-15T00:00:00"

    def test_datetime(self):
        dt = datetime(2024, 6, 15, 10, 30)
        assert convert_numpy(dt) == "2024-06-15T10:30:00"

    def test_dict(self):
        d = {"a": np.int64(1), "b": np.float64(2.5)}
        assert convert_numpy(d) == {"a": 1, "b": 2.5}

    def test_list_mixed(self):
        data = [np.int64(1), np.float64(np.nan), "hello"]
        result = convert_numpy(data)
        assert result == [1, None, "hello"]

    def test_dataframe_passthrough(self):
        df = pd.DataFrame({"x": [1, 2]})
        assert convert_numpy(df) is df

    def test_series_passthrough(self):
        s = pd.Series([1, 2, 3])
        assert convert_numpy(s) is s


class TestDfToRecords:
    def test_basic(self):
        df = pd.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
        records = _df_to_records(df)
        assert records == [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]

    def test_datetime_to_iso(self):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-15", "2024-06-20"])})
        records = _df_to_records(df)
        assert records[0]["date"] == "2024-01-15"
        assert records[1]["date"] == "2024-06-20"

    def test_nan_to_none(self):
        df = pd.DataFrame({"value": [1.0, np.nan, 3.0]})
        records = _df_to_records(df)
        assert records[0]["value"] == 1.0
        assert records[1]["value"] is None
        assert records[2]["value"] == 3.0

    def test_empty_dataframe(self):
        df = pd.DataFrame({"a": []})
        records = _df_to_records(df)
        assert records == []


class TestFormatResult:
    def test_none_result(self):
        result = format_result(None)
        assert result["answer"] == "No result was returned."
        assert result["data"] == []

    def test_dataframe(self):
        df = pd.DataFrame({"col": [1, 2]})
        result = format_result(df)
        assert "Found 2 records" in result["answer"]
        assert result["data"] == [{"col": 1}, {"col": 2}]

    def test_series(self):
        s = pd.Series([10, 20, 30], name="values")
        result = format_result(s)
        assert "series" in result["answer"].lower()
        assert len(result["data"]) == 3

    def test_dict(self):
        d = {"total": 100, "avg": 50.5}
        result = format_result(d)
        assert "2 value(s)" in result["answer"]
        assert result["data"] == [d]

    def test_list_of_dicts(self):
        data = [{"name": "Alice"}, {"name": "Bob"}]
        result = format_result(data)
        assert "2 record(s)" in result["answer"]
        assert result["data"] == data

    def test_list_of_scalars(self):
        data = [10, 20, 30]
        result = format_result(data)
        assert "3 record(s)" in result["answer"]
        assert result["data"] == [{"value": 10}, {"value": 20}, {"value": 30}]

    def test_scalar(self):
        result = format_result(42)
        assert "Result: 42" in result["answer"]

    def test_string(self):
        result = format_result("hello")
        assert "Result: hello" in result["answer"]


class TestMaybeParseDates:
    def test_parse_ymd(self):
        df = pd.DataFrame({"date_col": ["2024-01-15", "2023-06-20", "2022-12-01"]})
        result = _maybe_parse_dates(df, threshold=0.5)
        assert pd.api.types.is_datetime64_any_dtype(result["date_col"])

    def test_parse_dmy(self):
        df = pd.DataFrame({"date_col": ["15/01/2024", "20/06/2023", "01/12/2022"]})
        result = _maybe_parse_dates(df, threshold=0.5)
        assert pd.api.types.is_datetime64_any_dtype(result["date_col"])

    def test_skip_numeric_column(self):
        df = pd.DataFrame({"date_col": [2024, 2023, 2022]})
        result = _maybe_parse_dates(df)
        assert not pd.api.types.is_datetime64_any_dtype(result["date_col"])

    def test_skip_no_hint_in_name(self):
        df = pd.DataFrame({"value": ["2024-01-15", "2023-06-20"]})
        result = _maybe_parse_dates(df)
        assert not pd.api.types.is_datetime64_any_dtype(result["value"])

    def test_no_date_hint_skip(self):
        df = pd.DataFrame({"x": ["hello", "world"]})
        result = _maybe_parse_dates(df)
        assert result["x"].iloc[0] == "hello"
