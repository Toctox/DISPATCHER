from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import NOW, FakeGateway, queue_row

from factory_dispatcher.bootstrap import build_bootstrap
from factory_dispatcher.models import DispatchJob, isoformat
from factory_dispatcher.mutex import LocalMutex, MutexBusyError


def test_bootstrap_renders_all_required_identity_fields():
    job = DispatchJob.from_row(
        2,
        queue_row(
            status="CLAIMED",
            attempt=2,
            leaseOwner="worker",
            leaseToken="secret-lease",
            leaseExpiresAt=isoformat(NOW + timedelta(minutes=45)),
        ),
    )
    factory = FakeGateway([]).factory

    rendered = build_bootstrap(job, factory, "stage-123")

    for expected in (
        "dispatchId=D-TEST-0001",
        "attemptId=D-TEST-0001-A002",
        "leaseToken=secret-lease",
        "agentId=REGISTRAR",
        "requestDriveId=request-1",
        "stagingFolderId=stage-123",
        "receiptFolderId=receipts-root",
        "bootstrapDriveId=bootstrap-doc",
    ):
        assert expected in rendered


def test_local_mutex_rejects_second_instance(tmp_path):
    lock_path = tmp_path / "dispatcher.lock"

    with LocalMutex(lock_path):
        with pytest.raises(MutexBusyError):
            with LocalMutex(lock_path):
                raise AssertionError("second mutex must never be acquired")
