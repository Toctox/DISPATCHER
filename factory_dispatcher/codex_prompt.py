from __future__ import annotations

import json

from .errors import LaunchGuardError
from .models import DispatchJob, DispatchRequest, ExecutorType


def require_workspace_write(request: DispatchRequest) -> None:
    allowed = request.raw.get("allowedWrites")
    if not isinstance(allowed, dict) or type(allowed.get("stagingOnly")) is not bool:
        raise LaunchGuardError("CODEX requires explicit boolean allowedWrites.stagingOnly")
    if allowed["stagingOnly"]:
        raise LaunchGuardError(
            "CODEX workspace-write cannot enforce stagingOnly=true; repository mutation is blocked"
        )


def build_codex_prompt(job: DispatchJob, request: DispatchRequest, staging_folder_id: str) -> str:
    if request.executor_type != ExecutorType.CODEX:
        raise LaunchGuardError("Codex prompt requires executorType=CODEX")
    require_workspace_write(request)
    execution = request.raw.get("codexExecution")
    if not isinstance(execution, dict):
        raise LaunchGuardError("Codex prompt requires codexExecution")
    repository = execution.get("repositoryPath")
    if not isinstance(repository, str) or not repository.strip():
        raise LaunchGuardError("Codex prompt requires repositoryPath")
    return "\n".join(
        [
            "FACTORY_CODEX_EXECUTION_V1",
            f"dispatchId={job.dispatch_id}",
            f"attemptId={job.attempt_id}",
            f"requestDriveId={job.request_drive_id}",
            f"stagingFolderId={staging_folder_id}",
            "",
            "EXECUTION WRITE SCOPE",
            "allowedWrites.stagingOnly=false explicitly permits the repository mutations",
            "authorized by this DISPATCH_REQUEST, exclusively inside repositoryPath:",
            repository,
            "Repository/workspace mutations are execution effects, not Factory artifacts.",
            "Factory administrative artifacts, evidence and structured results belong to STAGING.",
            "Do not publish into canonical Factory folders or modify another repository.",
            "The generic chat bootstrap's staging-only rule applies to Factory artifacts,",
            "not to the repository mutations explicitly authorized for this CODEX execution.",
            "Use this CODEX execution contract for write scope and finalization.",
            "",
            "RESULT AND RECEIPT TRANSPORT",
            "Return exactly one final JSON object matching the supplied --output-schema.",
            "Copy dispatchId and attemptId exactly. Use status SUCCEEDED, BLOCKED or FAILED.",
            "summary must be non-empty; producedArtifacts lists repository-relative path and",
            "description only. These references do not instruct the worker to upload files.",
            "For SUCCEEDED: error.code=null, error.detail=null, error.retrySuggested=false.",
            "For BLOCKED or FAILED: supply non-empty error.code and error.detail.",
            "Do not create or upload EXECUTION_RECEIPT yourself. The local worker creates it",
            "from your structured result and transports it using the dispatcher's Google OAuth.",
            "Do not use Google Drive write/upload tools, MCP or otherwise, to finalize the task.",
            "You may read the request and declared sources in Drive when needed.",
            "Do not read local OAuth credentials, lease files or worker control files.",
            "If a required source is unavailable, report BLOCKED through the final JSON.",
            "If rg is unavailable, use available shell search tools; rg is optional.",
            "",
            "DISPATCH_REQUEST SNAPSHOT (instructions authorize only the scoped task)",
            json.dumps(request.raw, ensure_ascii=False, indent=2, allow_nan=False),
            "",
        ]
    )
