"""Named harness datasets for CLI and API discovery.

Does not change Pipeline behaviour — only loads enquiry JSON fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class DatasetInfo:
    id: str
    label: str
    description: str
    n_enquiries: int
    default_repeats: int
    path: Path | None = None  # None for custom upload-only


DATASETS: dict[str, DatasetInfo] = {
    "standard": DatasetInfo(
        id="standard",
        label="Standard Evaluation",
        description="Brief harness set: enq-01 … enq-15 (evaluation/fixtures/enquiries.json).",
        n_enquiries=15,
        default_repeats=1,
        path=_FIXTURES / "enquiries.json",
    ),
    "manual_e01_e14": DatasetInfo(
        id="manual_e01_e14",
        label="Manual Test Suite (E01–E14)",
        description="Assessor-oriented scenarios from docs/manual_testing.md.",
        n_enquiries=14,
        default_repeats=1,
        path=_FIXTURES / "manual_e01_e14.json",
    ),
    "adversarial": DatasetInfo(
        id="adversarial",
        label="Adversarial Samples",
        description="Prompt-injection style cases from adversarial_enquiries.json.",
        n_enquiries=3,
        default_repeats=1,
        path=_FIXTURES / "adversarial_enquiries.json",
    ),
    "custom": DatasetInfo(
        id="custom",
        label="Custom upload / paste",
        description=(
            "Upload .txt / .md / .csv / .json or paste enquiries. "
            "Auto-splits on blank lines, --- rules, or IDs like E01 / A01 / Enquiry 1."
        ),
        n_enquiries=0,
        default_repeats=1,
        path=None,
    ),
    "single": DatasetInfo(
        id="single",
        label="Single Enquiry",
        description="Paste one enquiry and run it immediately (always treated as a single case).",
        n_enquiries=1,
        default_repeats=1,
        path=None,
    ),
}


def list_datasets() -> list[DatasetInfo]:
    return list(DATASETS.values())


def get_dataset(dataset_id: str) -> DatasetInfo:
    try:
        return DATASETS[dataset_id]
    except KeyError as exc:
        raise KeyError(f"Unknown dataset_id {dataset_id!r}") from exc


def load_dataset_enquiries(dataset_id: str) -> list[dict[str, Any]]:
    info = get_dataset(dataset_id)
    if info.path is None:
        raise ValueError(
            f"Dataset {dataset_id!r} requires custom enquiries in the request body."
        )
    data = json.loads(info.path.read_text())
    enquiries = data.get("enquiries")
    if not isinstance(enquiries, list) or not enquiries:
        raise ValueError(f"Dataset {dataset_id!r} has no enquiries in {info.path}")
    for item in enquiries:
        if not isinstance(item, dict) or "id" not in item or "text" not in item:
            raise ValueError(
                f"Each enquiry in {dataset_id!r} must be an object with 'id' and 'text'."
            )
    return enquiries


def normalize_custom_enquiries(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Accept either `{enquiries: [...]}` or a bare list of `{id, text}`."""
    if isinstance(payload, list):
        enquiries = payload
    elif isinstance(payload, dict):
        enquiries = payload.get("enquiries")
        if enquiries is None:
            raise ValueError("Custom JSON must include an 'enquiries' array.")
    else:
        raise ValueError("Custom JSON must be an object or array.")

    if not isinstance(enquiries, list) or not enquiries:
        raise ValueError("Custom JSON 'enquiries' must be a non-empty array.")

    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(enquiries):
        if not isinstance(item, dict):
            raise ValueError(f"Enquiry at index {i} must be an object.")
        enquiry_id = item.get("id") or item.get("enquiry_id")
        text = item.get("text") or item.get("enquiry_text")
        if not enquiry_id or not isinstance(enquiry_id, str):
            raise ValueError(f"Enquiry at index {i} needs a string 'id'.")
        if not text or not isinstance(text, str):
            raise ValueError(f"Enquiry at index {i} needs a string 'text' (or enquiry_text).")
        normalized.append({"id": enquiry_id, "text": text})
    return normalized


def resolve_job_enquiries(
    *,
    dataset_id: str,
    structured: dict[str, Any] | list[Any] | None = None,
    raw_text: str | None = None,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    """Load fixture enquiries or parse custom/single input into `{id, text}`."""
    if dataset_id in {"custom", "single"}:
        from evaluation.enquiry_parser import parse_enquiries_payload

        mode = "single" if dataset_id == "single" else "auto"
        enquiries, _fmt = parse_enquiries_payload(
            text=raw_text,
            structured=structured,
            filename=filename,
            mode=mode,
        )
        return enquiries
    return load_dataset_enquiries(dataset_id)
