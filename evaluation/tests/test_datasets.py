"""Tests for evaluation/datasets.py."""

from __future__ import annotations

import pytest

from evaluation import datasets


def test_lists_standard_and_manual_datasets() -> None:
    ids = {d.id for d in datasets.list_datasets()}
    assert "standard" in ids
    assert "manual_e01_e14" in ids
    assert "custom" in ids
    assert "single" in ids


def test_load_standard_has_15_enquiries() -> None:
    enquiries = datasets.load_dataset_enquiries("standard")
    assert len(enquiries) == 15
    assert [row["id"] for row in enquiries] == [f"E{n:02d}" for n in range(1, 16)]
    assert enquiries[0]["text"].startswith("Hello, my name is Daniel Okafor.")
    info = datasets.get_dataset("standard")
    assert info.label == "Official Trial A Dataset (E01–E15)"


def test_load_manual_has_14_enquiries() -> None:
    enquiries = datasets.load_dataset_enquiries("manual_e01_e14")
    assert len(enquiries) == 14
    assert enquiries[0]["id"] == "E01"
    assert "Olivia Hart" in enquiries[0]["text"]


def test_custom_requires_upload() -> None:
    with pytest.raises(ValueError, match="custom"):
        datasets.load_dataset_enquiries("custom")


def test_normalize_custom_enquiries_accepts_enquiry_text_alias() -> None:
    out = datasets.normalize_custom_enquiries(
        {"enquiries": [{"id": "x", "enquiry_text": "hello"}]}
    )
    assert out == [{"id": "x", "text": "hello"}]


def test_resolve_job_enquiries_single_from_raw_text() -> None:
    out = datasets.resolve_job_enquiries(
        dataset_id="single",
        raw_text="Just one enquiry about whisky casks.",
        filename="demo.txt",
    )
    assert len(out) == 1
    assert out[0]["id"] == "demo"


def test_resolve_job_enquiries_custom_from_plain_text() -> None:
    out = datasets.resolve_job_enquiries(
        dataset_id="custom",
        raw_text="E01\nFirst\n\nE02\nSecond",
    )
    assert [row["id"] for row in out] == ["E01", "E02"]
