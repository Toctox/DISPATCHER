from __future__ import annotations

from .codex_prompt import build_codex_prompt
from .config import FactoryConfig
from .models import DispatchJob, DispatchRequest, ExecutorType


def build_bootstrap(
    job: DispatchJob,
    factory: FactoryConfig,
    staging_folder_id: str,
    request: DispatchRequest | None = None,
) -> str:
    if request is not None and request.executor_type == ExecutorType.CODEX:
        return build_codex_prompt(job, request, staging_folder_id)
    if not job.lease_token:
        raise ValueError("a claimed job with leaseToken is required")
    lines = [
        "FACTORY_EXECUTION_V1",
        "",
        f"dispatchId={job.dispatch_id}",
        f"attemptId={job.attempt_id}",
        f"leaseToken={job.lease_token}",
        f"agentId={job.agent_id}",
        f"requestDriveId={job.request_drive_id}",
        f"stagingFolderId={staging_folder_id}",
        f"receiptFolderId={factory.receipts_folder_id}",
        f"bootstrapDriveId={factory.bootstrap_doc_id}",
        "",
        "Read the canonical sources declared by the request.",
        "Write all produced artifacts only to the staging folder above.",
        "Write exactly one terminal EXECUTION_RECEIPT for this attempt to the receipt folder.",
        f"Receipt name: EXECUTION_RECEIPT__{job.dispatch_id}__{job.attempt_id}.json",
        "Do not publish to canonical folders; publication is outside this MVP.",
    ]
    return "\n".join(lines) + "\n"
