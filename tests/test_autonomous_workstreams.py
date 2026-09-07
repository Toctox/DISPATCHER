from __future__ import annotations

import json

from conftest import FakeGateway, queue_row

from factory_dispatcher.autonomous import AutonomousDispatcher
from factory_dispatcher.models import DispatchJob


class _Logger:
    def record(self, event: str, **fields) -> None:
        pass


def test_workstream_seeds_one_orchestrator_planner(
    monkeypatch, local_settings
):
    gateway = FakeGateway([])
    dispatcher = AutonomousDispatcher(gateway, local_settings, _Logger(), {})
    appended: list[list[object]] = []

    monkeypatch.setattr(
        dispatcher,
        "_read_active_workstreams",
        lambda: [
            {
                "rowNumber": 2,
                "workstreamId": "WS-CR-0005",
                "changeId": "CR-0005",
                "targetRef": "spec-drive-id",
                "priority": 100,
                "targetState": "READY_FOR_IMPLEMENTATION",
                "requiredSources": [
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": "evidence-drive-id",
                        "title": "Evidence",
                    }
                ],
                "instructions": "Preserve Gate B.5 evidence requirements.",
            }
        ],
    )
    monkeypatch.setattr(
        dispatcher,
        "_config_mapping",
        lambda: {
            "ORCHESTRATOR_PROMPT_DOC_ID": "orchestrator-prompt",
            "START_HERE_ID": "start-here",
        },
    )
    monkeypatch.setattr(dispatcher, "_append_queue_row", appended.append)

    dispatch_id = dispatcher._ensure_workstream_planner([], gateway.factory)

    assert dispatch_id is not None
    assert len(appended) == 1
    row = appended[0]
    assert len(row) == 30
    assert row[3] == "ORCHESTRATOR"
    assert row[4] == "CR-0005"
    assert row[5] == "AUTONOMOUS_WORKSTREAM_PLANNING"
    assert row[6] == "spec-drive-id"
    assert row[7] == "READY"
    assert row[8] == 100
    assert str(row[9]).startswith("AUTO-WORKSTREAM-WS-CR-0005")

    assert len(gateway.uploads) == 1
    request = json.loads(gateway.uploads[0][2])
    assert request["agentId"] == "ORCHESTRATOR"
    assert request["changeId"] == "CR-0005"
    assert request["taskType"] == "AUTONOMOUS_WORKSTREAM_PLANNING"
    assert request["targetRef"] == {"kind": "DRIVE_ARTIFACT", "id": "spec-drive-id"}
    assert request["requiredSources"][-1]["id"] == "evidence-drive-id"
    assert "Gate B.5" in request["instructions"]


def test_workstream_seed_is_deduped(monkeypatch, local_settings):
    existing = DispatchJob.from_row(
        2,
        queue_row(
            dispatchId="D-CR-0005-ORCHESTRATOR-PLAN-EXISTING",
            agentId="ORCHESTRATOR",
            changeId="CR-0005",
            status="RESULT_STAGED",
            dedupeKey="AUTO-WORKSTREAM-WS-CR-0005",
        ),
    )
    gateway = FakeGateway([])
    dispatcher = AutonomousDispatcher(gateway, local_settings, _Logger(), {})

    monkeypatch.setattr(
        dispatcher,
        "_read_active_workstreams",
        lambda: [
            {
                "rowNumber": 2,
                "workstreamId": "WS-CR-0005",
                "changeId": "CR-0005",
                "targetRef": "spec-drive-id",
                "priority": 100,
                "targetState": "",
                "requiredSources": [],
                "instructions": "",
            }
        ],
    )
    monkeypatch.setattr(dispatcher, "_config_mapping", lambda: {})
    appended: list[list[object]] = []
    monkeypatch.setattr(dispatcher, "_append_queue_row", appended.append)

    assert dispatcher._ensure_workstream_planner([existing], gateway.factory) is None
    assert appended == []
    assert gateway.uploads == []


def test_pending_job_blocks_workstream_seed(monkeypatch, local_settings):
    pending = DispatchJob.from_row(
        2,
        queue_row(
            dispatchId="D-CR-0005-QA-001",
            agentId="QA",
            changeId="CR-0005",
            status="READY",
            dedupeKey="QA-PENDING",
        ),
    )
    gateway = FakeGateway([])
    dispatcher = AutonomousDispatcher(gateway, local_settings, _Logger(), {})

    monkeypatch.setattr(
        dispatcher,
        "_read_active_workstreams",
        lambda: (_ for _ in ()).throw(AssertionError("must not read workstreams while work is pending")),
    )

    assert dispatcher._ensure_workstream_planner([pending], gateway.factory) is None