from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .edocs_api import EdocsApi, EdocsError, PRODUCTION_BASE_URL, TRAINING_BASE_URL
from .edocs_plan import (
    HAR_ARE_IDS,
    HAR_GOVES_ID,
    HAR_SEFAZ_ID,
    build_plan,
    plan_as_dict,
)
from .edocs_workflow import EdocsSubmissionWorkflow


def _api(args: argparse.Namespace) -> EdocsApi:
    token = os.environ.get("EDOCS_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "EDOCS_ACCESS_TOKEN is required. Use an Authorization Code/Hybrid user token."
        )
    base = PRODUCTION_BASE_URL if args.environment == "production" else TRAINING_BASE_URL
    return EdocsApi(token, base_url=base)


def _require_write(args: argparse.Namespace) -> None:
    if not getattr(args, "execute", False):
        raise SystemExit("refusing write: pass --execute explicitly")
    if os.environ.get("EDOCS_ENABLE_WRITE") != "1":
        raise SystemExit("refusing write: set EDOCS_ENABLE_WRITE=1 explicitly")
    if (
        getattr(args, "environment", "training") == "production"
        and os.environ.get("EDOCS_ENABLE_PRODUCTION") != "1"
    ):
        raise SystemExit(
            "refusing production write: set EDOCS_ENABLE_PRODUCTION=1 explicitly"
        )


def _id_of(item: dict[str, Any]) -> str | None:
    for key in ("id", "idAgente", "idUnidade", "idOrganizacao", "idPatriarca"):
        value = item.get(key)
        if not value:
            continue
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            continue
    return None


def _name_of(item: dict[str, Any]) -> str:
    for key in ("nome", "name", "descricao", "sigla"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _by_expected_id(items: list[dict[str, Any]], expected_id: str, label: str) -> dict[str, Any]:
    expected = str(uuid.UUID(expected_id))
    matches = [item for item in items if _id_of(item) == expected]
    if len(matches) != 1:
        raise EdocsError(
            f"{label} UUID drift or visibility failure: expected {expected}, found {len(matches)}"
        )
    return matches[0]


def cmd_plan(args: argparse.Namespace) -> int:
    plan = build_plan(args.manifest)
    print(json.dumps(plan_as_dict(plan), ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    api = _api(args)
    user_id = api.current_user_id()
    goves = _by_expected_id(api.patriarcas(), HAR_GOVES_ID, "GOVES")
    sefaz = _by_expected_id(api.organizacoes(HAR_GOVES_ID), HAR_SEFAZ_ID, "SEFAZ")
    setores = api.setores(HAR_SEFAZ_ID)
    destinations: dict[str, dict[str, str]] = {}
    for municipality, expected_id in HAR_ARE_IDS.items():
        item = _by_expected_id(setores, expected_id, f"ARE {municipality}")
        destinations[municipality] = {
            "id": expected_id,
            "name": _name_of(item),
        }
    print(
        json.dumps(
            {
                "currentUserId": user_id,
                "goves": {"id": HAR_GOVES_ID, "name": _name_of(goves)},
                "sefaz": {"id": HAR_SEFAZ_ID, "name": _name_of(sefaz)},
                "destinations": destinations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    _require_write(args)
    api = _api(args)
    temporary_id = api.upload_pdf(args.pdf)
    print(
        json.dumps(
            {"identificadorTemporarioArquivoNaNuvem": temporary_id},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    _require_write(args)
    api = _api(args)
    pdf = api.validate_pdf(args.pdf)
    temporary_id = api.upload_pdf(pdf)
    event_id = api.capture_citizen_file(
        mode=args.mode,
        temporary_id=temporary_id,
        file_name=pdf.name,
    )
    result: dict[str, object] = {
        "identificadorTemporarioArquivoNaNuvem": temporary_id,
        "idEvento": event_id,
    }
    if args.wait:
        event = api.poll_event(event_id, timeout_seconds=args.wait_timeout)
        result["evento"] = event
        result["idDocumento"] = api.event_document_id(event)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_forward(args: argparse.Namespace) -> int:
    _require_write(args)
    api = _api(args)
    event_id = api.create_forwarding(
        subject=args.subject,
        message=args.message,
        responsible_id=args.responsible_id,
        destination_ids=args.destination_id,
        document_ids=args.document_id,
    )
    result: dict[str, object] = {"idEvento": event_id}
    if args.wait:
        event = api.poll_event(event_id, timeout_seconds=args.wait_timeout)
        result["evento"] = event
        result["idEncaminhamento"] = api.event_forwarding_id(event)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    _require_write(args)
    plan = build_plan(args.manifest)
    workflow = EdocsSubmissionWorkflow(_api(args), args.state)
    result = workflow.submit(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_reconcile_capture(args: argparse.Namespace) -> int:
    workflow = EdocsSubmissionWorkflow(_api(args), args.state)
    result = workflow.reconcile_capture_event(args.role, args.event_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_reconcile_forwarding(args: argparse.Namespace) -> int:
    workflow = EdocsSubmissionWorkflow(_api(args), args.state)
    result = workflow.reconcile_forwarding_event(args.event_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _environment(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--environment", choices=("training", "production"), default="training"
    )


def _execute(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--execute",
        action="store_true",
        help="required opt-in for any E-Docs write",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factory_dispatcher.edocs_cli",
        description="Fail-closed E-Docs Public API V2 helper for Chat Ops.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="validate a local three-PDF producer package")
    plan.add_argument("manifest", type=Path)
    plan.set_defaults(func=cmd_plan)

    discover = sub.add_parser(
        "discover", help="verify current user and E-Docs destination UUIDs"
    )
    _environment(discover)
    discover.set_defaults(func=cmd_discover)

    upload = sub.add_parser(
        "upload", help="upload one PDF and return the temporary cloud identifier"
    )
    upload.add_argument("pdf", type=Path)
    _environment(upload)
    _execute(upload)
    upload.set_defaults(func=cmd_upload)

    capture = sub.add_parser(
        "capture", help="upload and capture one citizen PDF using a declared mode"
    )
    capture.add_argument("pdf", type=Path)
    capture.add_argument(
        "--mode",
        choices=("digitalizado", "nato-digital-copia", "icp-brasil"),
        required=True,
    )
    _environment(capture)
    _execute(capture)
    capture.add_argument("--wait", action="store_true")
    capture.add_argument("--wait-timeout", type=float, default=240.0)
    capture.set_defaults(func=cmd_capture)

    forward = sub.add_parser(
        "forward", help="create a forwarding from already captured document IDs"
    )
    _environment(forward)
    _execute(forward)
    forward.add_argument("--responsible-id", required=True)
    forward.add_argument("--destination-id", action="append", required=True)
    forward.add_argument("--document-id", action="append", required=True)
    forward.add_argument("--subject", required=True)
    forward.add_argument("--message", required=True)
    forward.add_argument("--wait", action="store_true")
    forward.add_argument("--wait-timeout", type=float, default=240.0)
    forward.set_defaults(func=cmd_forward)

    submit = sub.add_parser(
        "submit",
        help="resume-safe upload, capture and forwarding of a three-PDF package",
    )
    submit.add_argument("manifest", type=Path)
    submit.add_argument(
        "--state",
        type=Path,
        required=True,
        help="checkpoint file used to prevent blind duplicate writes",
    )
    _environment(submit)
    _execute(submit)
    submit.set_defaults(func=cmd_submit)

    reconcile_capture = sub.add_parser(
        "reconcile-capture",
        help="record a manually verified capture event after an uncertain response",
    )
    reconcile_capture.add_argument("--state", type=Path, required=True)
    reconcile_capture.add_argument(
        "--role", choices=("procuracao", "termoAdesao", "documentosPessoais"), required=True
    )
    reconcile_capture.add_argument("--event-id", required=True)
    _environment(reconcile_capture)
    reconcile_capture.set_defaults(func=cmd_reconcile_capture)

    reconcile_forward = sub.add_parser(
        "reconcile-forwarding",
        help="record a manually verified forwarding event after an uncertain response",
    )
    reconcile_forward.add_argument("--state", type=Path, required=True)
    reconcile_forward.add_argument("--event-id", required=True)
    _environment(reconcile_forward)
    reconcile_forward.set_defaults(func=cmd_reconcile_forwarding)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
