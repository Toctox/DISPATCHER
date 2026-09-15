from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .edocs_api import EdocsDestination, MAX_PDF_BYTES

CPF_DIGITS = re.compile(r"\D+")
CAPTURE_MODES = {"digitalizado", "nato-digital-copia", "icp-brasil"}

# IDs observed in the user's authenticated E-Docs session on 2026-09-15.
# They are diagnostic/drift guards only: discover them again before a live submission.
HAR_GOVES_ID = "fe88eb2a-a1f3-4cb1-a684-87317baf5a57"
HAR_SEFAZ_ID = "9145ec80-a7b9-43c8-a230-cc0d3b7257fe"
HAR_ARE_IDS = {
    "ARACRUZ": "62ab5dc1-de3f-4374-a46a-ab3a69fbb9a9",
    "LINHARES": "bb55922b-2140-41a2-9295-e00dd2eddd12",
}


class EdocsPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PackageFile:
    role: str
    path: Path
    capture_mode: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SubmissionPlan:
    producer_name: str
    producer_cpf: str
    ie: str
    municipality: str
    destination: EdocsDestination
    subject: str
    message: str
    files: tuple[PackageFile, ...]
    fingerprint: str


def normalize_cpf(value: str) -> str:
    digits = CPF_DIGITS.sub("", str(value))
    if len(digits) != 11 or len(set(digits)) == 1:
        raise EdocsPlanError("invalid CPF")

    def check(part: str, weight: int) -> str:
        total = sum(int(char) * factor for char, factor in zip(part, range(weight, 1, -1)))
        digit = 11 - total % 11
        return "0" if digit >= 10 else str(digit)

    if check(digits[:9], 10) != digits[9] or check(digits[:10], 11) != digits[10]:
        raise EdocsPlanError("invalid CPF check digits")
    return digits


def format_cpf(value: str) -> str:
    digits = normalize_cpf(value)
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EdocsPlanError(f"{key} is required")
    return value.strip()


def _package_file(role: str, entry: Any) -> PackageFile:
    if not isinstance(entry, dict):
        raise EdocsPlanError(f"files.{role} must be an object with path and captureMode")
    raw_path = _required_text(entry, "path")
    mode = _required_text(entry, "captureMode").casefold().replace("_", "-")
    if mode not in CAPTURE_MODES:
        raise EdocsPlanError(
            f"files.{role}.captureMode must be one of {sorted(CAPTURE_MODES)}"
        )
    path = Path(raw_path).expanduser().resolve(strict=True)
    if path.suffix.lower() != ".pdf":
        raise EdocsPlanError(f"files.{role}.path must be a PDF")
    size = path.stat().st_size
    if not (1 <= size <= MAX_PDF_BYTES):
        raise EdocsPlanError(f"files.{role}.path size is outside the E-Docs PDF limit")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise EdocsPlanError(f"files.{role}.path does not have a PDF signature")
    return PackageFile(
        role=role,
        path=path,
        capture_mode=mode,
        sha256=_sha256(path),
        size_bytes=size,
    )


def build_plan(manifest_path: str | Path) -> SubmissionPlan:
    manifest_file = Path(manifest_path).expanduser().resolve(strict=True)
    data = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise EdocsPlanError("manifest must be a JSON object")

    producer_name = _required_text(data, "producerName")
    cpf_value = data.get("producerCpf", data.get("cpf"))
    if not isinstance(cpf_value, str) or not cpf_value.strip():
        raise EdocsPlanError("producerCpf (or cpf) is required")
    producer_cpf = format_cpf(cpf_value)
    ie = _required_text(data, "ie")
    municipality = _required_text(data, "municipality").upper()
    expected_destination_id = HAR_ARE_IDS.get(municipality)
    if not expected_destination_id:
        raise EdocsPlanError(
            f"unsupported municipality {municipality!r}; map and verify the destination first"
        )

    files_data = data.get("files")
    if not isinstance(files_data, dict):
        raise EdocsPlanError("files is required")
    roles = ("procuracao", "termoAdesao", "documentosPessoais")
    if set(files_data) != set(roles):
        raise EdocsPlanError(f"files must contain exactly {roles}")
    files = tuple(_package_file(role, files_data[role]) for role in roles)

    destination = EdocsDestination(
        name=f"ARE {municipality}",
        id=expected_destination_id,
    )
    subject = str(
        data.get("subject") or f"TERMO DE ADESÃO NF-e PRODUTOR RURAL - {producer_name}"
    ).strip()
    message = str(
        data.get("message")
        or (
            "Encaminho Termo de Adesão e Responsabilidade para emissão de NF-e de Produtor "
            f"Rural, acompanhado de procuração e documentos pessoais. Produtor: {producer_name}; "
            f"CPF: {producer_cpf}; IE: {ie}."
        )
    ).strip()
    if not subject or not message:
        raise EdocsPlanError("subject and message cannot be empty")

    canonical = {
        "producerName": producer_name,
        "producerCpf": producer_cpf,
        "ie": ie,
        "municipality": municipality,
        "destinationId": destination.id,
        "subject": subject,
        "message": message,
        "files": [
            {
                "role": item.role,
                "path": str(item.path),
                "captureMode": item.capture_mode,
                "sha256": item.sha256,
                "sizeBytes": item.size_bytes,
            }
            for item in files
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SubmissionPlan(
        producer_name=producer_name,
        producer_cpf=producer_cpf,
        ie=ie,
        municipality=municipality,
        destination=destination,
        subject=subject,
        message=message,
        files=files,
        fingerprint=fingerprint,
    )


def plan_as_dict(plan: SubmissionPlan) -> dict[str, Any]:
    return {
        "producerName": plan.producer_name,
        "producerCpf": plan.producer_cpf,
        "ie": plan.ie,
        "municipality": plan.municipality,
        "destination": {"name": plan.destination.name, "id": plan.destination.id},
        "subject": plan.subject,
        "message": plan.message,
        "fingerprint": plan.fingerprint,
        "files": [
            {
                "role": item.role,
                "path": str(item.path),
                "captureMode": item.capture_mode,
                "sha256": item.sha256,
                "sizeBytes": item.size_bytes,
            }
            for item in plan.files
        ],
    }
