"""Chunk normalizes its metadata to the JSON round-trip form."""

import datetime

from duckdb_vectors.models import Chunk


def _chunk(metadata):
    return Chunk(chunk_id="a:0", text="x", metadata=metadata)


def test_date_and_datetime_become_iso_text():
    metadata = _chunk(
        {
            "d": datetime.date(2026, 5, 30),
            "t": datetime.datetime(2026, 5, 30, 10, 0, 0),
        }
    ).metadata
    assert metadata == {"d": "2026-05-30", "t": "2026-05-30T10:00:00"}


def test_nested_dates_are_normalized():
    metadata = _chunk({"execution": {"started": datetime.date(2026, 5, 30)}}).metadata
    assert metadata == {"execution": {"started": "2026-05-30"}}


def test_set_and_bytes_become_text():
    metadata = _chunk({"s": {"only"}, "b": b"raw"}).metadata
    assert metadata == {"s": "{'only'}", "b": "b'raw'"}


def test_non_string_keys_become_strings():
    metadata = _chunk({1: "one", ("a", "b"): "pair"}).metadata
    assert metadata == {"1": "one", "('a', 'b')": "pair"}


def test_json_native_values_are_untouched():
    original = {
        "s": "text",
        "i": 3,
        "f": 1.5,
        "yes": True,
        "none": None,
        "tags": ["a", ["b", 2]],
        "nested": {"k": "v"},
    }
    assert _chunk(original).metadata == original
