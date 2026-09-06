from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import DispatchJob, DispatchRequest


@dataclass(frozen=True)
class LaunchResult:
    launcher: str
    pid: int | None = None
    detail: str = ""


class Launcher(Protocol):
    def launch(
        self,
        job: DispatchJob,
        request: DispatchRequest,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> LaunchResult: ...
