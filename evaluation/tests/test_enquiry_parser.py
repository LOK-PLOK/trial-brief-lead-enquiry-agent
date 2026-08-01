"""Tests for evaluation/enquiry_parser.py — input-layer only."""

from __future__ import annotations

import pytest

from evaluation.enquiry_parser import parse_enquiries_payload


def test_single_mode_wraps_entire_text() -> None:
    enquiries, fmt = parse_enquiries_payload(
        text="Hello, I'm Olivia interested in casks.",
        mode="single",
        filename="assessor.txt",
    )
    assert fmt == "single"
    assert len(enquiries) == 1
    assert enquiries[0]["id"] == "assessor"
    assert "Olivia" in enquiries[0]["text"]


def test_json_payload() -> None:
    text = '{"enquiries": [{"id": "x", "text": "hello"}]}'
    enquiries, fmt = parse_enquiries_payload(text=text, filename="set.json")
    assert fmt == "json"
    assert enquiries == [{"id": "x", "text": "hello"}]


def test_csv_with_id_and_text() -> None:
    text = "id,text\nE01,First enquiry\nE02,Second enquiry\n"
    enquiries, fmt = parse_enquiries_payload(text=text, filename="cases.csv")
    assert fmt == "csv"
    assert [e["id"] for e in enquiries] == ["E01", "E02"]


def test_blank_line_separator() -> None:
    text = "First enquiry block.\n\nSecond enquiry block."
    enquiries, fmt = parse_enquiries_payload(text=text)
    assert fmt == "text"
    assert len(enquiries) == 2
    assert enquiries[0]["id"] == "custom-01"
    assert "First" in enquiries[0]["text"]


def test_horizontal_rule_separator() -> None:
    text = "Enquiry A body\n---\nEnquiry B body"
    enquiries, _fmt = parse_enquiries_payload(text=text)
    assert len(enquiries) == 2
    assert "Enquiry A" in enquiries[0]["text"]
    assert "Enquiry B" in enquiries[1]["text"]


def test_e01_style_id_headings() -> None:
    text = (
        "E01 — High value\n"
        "Olivia wants AUD 120k urgently.\n"
        "\n"
        "E02 — Medium\n"
        "Noah is not urgent.\n"
    )
    enquiries, _fmt = parse_enquiries_payload(text=text, filename="manual.md")
    assert [e["id"] for e in enquiries] == ["E01", "E02"]
    assert "Olivia" in enquiries[0]["text"]
    assert "Noah" in enquiries[1]["text"]


def test_enquiry_n_heading() -> None:
    text = "Enquiry 1\nAlpha text\n\nEnquiry 2\nBeta text"
    enquiries, _fmt = parse_enquiries_payload(text=text)
    assert [e["id"] for e in enquiries] == ["Enquiry-1", "Enquiry-2"]


def test_a01_heading() -> None:
    text = "A01\nHidden assessor case one.\n\nA02\nHidden assessor case two."
    enquiries, _fmt = parse_enquiries_payload(text=text)
    assert [e["id"] for e in enquiries] == ["A01", "A02"]


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_enquiries_payload(text="   ")


def test_structured_json_without_text() -> None:
    enquiries, fmt = parse_enquiries_payload(
        structured={"enquiries": [{"id": "z", "enquiry_text": "hi"}]}
    )
    assert fmt == "json"
    assert enquiries == [{"id": "z", "text": "hi"}]
