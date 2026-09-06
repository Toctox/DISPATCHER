from __future__ import annotations

import pytest
from conftest import queue_row

from factory_dispatcher.errors import ContractError
from factory_dispatcher.models import DispatchJob, DispatchRequest


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), ("", ""), ("  ", ""), (" CR-0004 ", "CR-0004"), ("None", "None")],
)
def test_queue_and_request_use_the_same_change_id_normalization(value, expected):
    job = DispatchJob.from_row(2, queue_row(changeId=value))
    request = DispatchRequest({"changeId": value})
    assert job.change_id == expected
    assert request.change_id == expected


@pytest.mark.parametrize("value", [False, 0, 1, [], {}])
def test_non_string_change_id_is_not_silently_coerced(value):
    with pytest.raises(ContractError, match="changeId"):
        DispatchJob.from_row(2, queue_row(changeId=value))
    with pytest.raises(ContractError, match="changeId"):
        _ = DispatchRequest({"changeId": value}).change_id
