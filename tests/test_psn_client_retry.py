import asyncio

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from psn_client import PSNClient, PSNClientError


def client():
    instance = PSNClient.__new__(PSNClient)
    instance._call_lock = asyncio.Lock()
    instance._psnawp = object()
    instance.logger = None
    return instance


def test_run_retries_connection_reset_twice(monkeypatch):
    delays = []
    async def fake_sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    attempts = 0
    def request():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("reset")
        return "ok"
    assert asyncio.run(client()._run(request)) == "ok"
    assert attempts == 3
    assert delays == [0.5, 1.0]


def test_run_does_not_retry_non_network_failure(monkeypatch):
    attempts = 0
    def request():
        nonlocal attempts
        attempts += 1
        raise ValueError("bad data")
    with pytest.raises(PSNClientError):
        asyncio.run(client()._run(request))
    assert attempts == 1


@pytest.mark.parametrize("error", [ConnectionError("offline"), TimeoutError("late"),
                                        RequestsConnectionError("reset"), RequestsTimeout("late")])
def test_run_stops_after_two_retries(monkeypatch, error):
    delays = []
    async def fake_sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    attempts = 0
    def request():
        nonlocal attempts
        attempts += 1
        raise error
    with pytest.raises(PSNClientError, match="重试后仍失败"):
        asyncio.run(client()._run(request))
    assert attempts == 3
    assert delays == [0.5, 1.0]
