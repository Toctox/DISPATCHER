from __future__ import annotations

import json

import pytest

from factory_dispatcher.edocs_plan import HAR_ARE_IDS, build_plan, format_cpf


def _pdf(path):
    path.write_bytes(b"%PDF-1.4\n% test\n")
    return str(path)


def _manifest(tmp_path, *, municipality="Aracruz"):
    return {
        "producerName": "Eunice Mariano Gonçalves",
        "cpf": "101.517.397-73",
        "ie": "114066574",
        "municipality": municipality,
        "files": {
            "procuracao": {
                "path": _pdf(tmp_path / "proc.pdf"),
                "captureMode": "nato-digital-copia",
            },
            "termoAdesao": {
                "path": _pdf(tmp_path / "termo.pdf"),
                "captureMode": "nato-digital-copia",
            },
            "documentosPessoais": {
                "path": _pdf(tmp_path / "docs.pdf"),
                "captureMode": "digitalizado",
            },
        },
    }


def _write_manifest(tmp_path, value):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_known_cpfs_are_valid_and_formatted():
    assert format_cpf("10151739773") == "101.517.397-73"
    assert format_cpf("081.264.217-11") == "081.264.217-11"
    assert format_cpf("04088608712") == "040.886.087-12"
    assert format_cpf("08515992736") == "085.159.927-36"
    with pytest.raises(ValueError, match="invalid CPF"):
        format_cpf("111.111.111-11")


def test_build_plan_requires_explicit_capture_modes(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["files"]["procuracao"] = manifest["files"]["procuracao"]["path"]
    with pytest.raises(ValueError, match="path and captureMode"):
        build_plan(_write_manifest(tmp_path, manifest))


def test_build_plan_routes_aracruz_and_keeps_modes(tmp_path):
    plan = build_plan(_write_manifest(tmp_path, _manifest(tmp_path)))
    assert plan.destination.id == HAR_ARE_IDS["ARACRUZ"]
    assert [item.capture_mode for item in plan.files] == [
        "nato-digital-copia",
        "nato-digital-copia",
        "digitalizado",
    ]
    assert len(plan.fingerprint) == 64


def test_unknown_municipality_fails_closed_before_file_processing(tmp_path):
    manifest = _manifest(tmp_path, municipality="Serra")
    with pytest.raises(ValueError, match="unsupported municipality"):
        build_plan(_write_manifest(tmp_path, manifest))


def test_non_pdf_signature_is_rejected(tmp_path):
    manifest = _manifest(tmp_path)
    bad = tmp_path / "fake.pdf"
    bad.write_text("not pdf", encoding="utf-8")
    manifest["files"]["procuracao"]["path"] = str(bad)
    with pytest.raises(ValueError, match="PDF signature"):
        build_plan(_write_manifest(tmp_path, manifest))
