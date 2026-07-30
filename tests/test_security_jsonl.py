from __future__ import annotations

import json

import pytest

from clu_latent.security.jsonl import (
    JsonlLimitError,
    iter_jsonl_bounded,
    read_bytes_bounded,
)
from clu_latent.security.limits import Limits


def _write(path, lines):
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_iter_jsonl_bounded_valid_records(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [json.dumps({"a": 1}), json.dumps({"a": 2})])

    records = list(iter_jsonl_bounded(path))
    assert len(records) == 2
    assert all(r.error is None for r in records)
    assert records[0].data == {"a": 1}


def test_iter_jsonl_bounded_skips_blank_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [json.dumps({"a": 1}), "", "   ", json.dumps({"a": 2})])

    records = list(iter_jsonl_bounded(path))
    assert len(records) == 2


def test_iter_jsonl_bounded_reports_invalid_json(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, ["{not valid json"])

    records = list(iter_jsonl_bounded(path))
    assert len(records) == 1
    assert records[0].error is not None
    assert "invalid JSON" in records[0].error


def test_iter_jsonl_bounded_reports_non_object_records(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [json.dumps([1, 2, 3]), json.dumps("a string"), json.dumps(42)])

    records = list(iter_jsonl_bounded(path))
    assert len(records) == 3
    assert all(r.error is not None and "not a JSON object" in r.error for r in records)


def test_iter_jsonl_bounded_allows_non_object_when_not_required(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [json.dumps([1, 2, 3])])

    records = list(iter_jsonl_bounded(path, require_object=False))
    assert records[0].error is None
    assert records[0].data == [1, 2, 3]


def test_iter_jsonl_bounded_rejects_huge_single_line(tmp_path):
    path = tmp_path / "t.jsonl"
    huge = json.dumps({"a": "x" * 2000})
    _write(path, [huge])

    with pytest.raises(JsonlLimitError):
        list(iter_jsonl_bounded(path, limits=Limits(max_jsonl_line_bytes=100)))


def test_iter_jsonl_bounded_rejects_too_many_records(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [json.dumps({"a": i}) for i in range(50)])

    with pytest.raises(JsonlLimitError):
        list(iter_jsonl_bounded(path, limits=Limits(max_records_per_track=10)))


def test_read_bytes_bounded_allows_small_file(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")

    data = read_bytes_bounded(path, max_bytes=1024)
    assert json.loads(data) == {"a": 1}


def test_read_bytes_bounded_rejects_oversized_file(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("x" * 2000, encoding="utf-8")

    with pytest.raises(JsonlLimitError):
        read_bytes_bounded(path, max_bytes=100)


def test_read_bytes_bounded_rejects_malformed_after_size_check(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not valid json", encoding="utf-8")

    data = read_bytes_bounded(path, max_bytes=1024)
    with pytest.raises(json.JSONDecodeError):
        json.loads(data)
