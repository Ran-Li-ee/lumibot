import asyncio
from pathlib import Path

import pytest

from lumibot.components.agents import replay_cache as replay_cache_module
from lumibot.components.agents.replay_cache import AgentReplayCache


class RecordingRemoteCache:
    def __init__(self):
        self.ensure_calls = []
        self.update_calls = []

    def ensure_local_file(self, path):
        self.ensure_calls.append(Path(path))
        return False

    def on_local_update(self, path):
        self.update_calls.append(Path(path))
        return True


@pytest.fixture
def replay_cache(monkeypatch, tmp_path):
    remote_cache = RecordingRemoteCache()
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    monkeypatch.setattr(replay_cache_module, "get_backtest_cache", lambda: remote_cache)
    return AgentReplayCache(), remote_cache


def test_replay_cache_save_atomically_replaces_from_same_directory(
    monkeypatch,
    replay_cache,
):
    cache, remote_cache = replay_cache
    replace_calls = []
    real_replace = replay_cache_module.os.replace

    def observed_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(replay_cache_module.os, "replace", observed_replace)

    cache_path = cache.save("a" * 64, {"summary": "cached"})

    assert len(replace_calls) == 1
    temp_path, final_path = replace_calls[0]
    assert temp_path.parent == final_path.parent
    assert final_path == cache_path
    assert not temp_path.exists()
    assert remote_cache.update_calls == [cache_path]
    assert cache.load("a" * 64) == {"summary": "cached"}


def test_replay_cache_save_cleans_partial_temp_on_failure(
    monkeypatch,
    replay_cache,
):
    cache, remote_cache = replay_cache
    cache_key = "b" * 64
    cache_path = cache._path_for(cache_key)

    def fail_after_partial_write(_payload, handle, **_kwargs):
        handle.write('{"partial":')
        handle.flush()
        raise OSError("synthetic partial cache write")

    monkeypatch.setattr(replay_cache_module.json, "dump", fail_after_partial_write)

    with pytest.raises(OSError, match="synthetic partial cache write"):
        cache.save(cache_key, {"summary": "cached"})

    assert not cache_path.exists()
    assert list(cache_path.parent.iterdir()) == []
    assert remote_cache.update_calls == []


def test_replay_cache_load_treats_corrupt_local_entry_as_miss(replay_cache):
    cache, remote_cache = replay_cache
    cache_key = "c" * 64
    cache_path = cache._path_for(cache_key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"not-a-valid-gzip-cache")

    assert cache.load(cache_key) is None
    assert remote_cache.ensure_calls == [cache_path]


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_replay_cache_remote_hydration_error_is_miss_and_recovers(
    monkeypatch,
    replay_cache,
    error_type,
):
    cache, remote_cache = replay_cache
    cache_key = "e" * 64
    cache_path = cache._path_for(cache_key)
    attempts = 0

    def flaky_hydration(path):
        nonlocal attempts
        attempts += 1
        remote_cache.ensure_calls.append(Path(path))
        if attempts == 1:
            raise error_type("synthetic remote hydration failure")
        return False

    monkeypatch.setattr(remote_cache, "ensure_local_file", flaky_hydration)

    assert cache.load(cache_key) is None

    cache.save(cache_key, {"summary": "recovered"})

    assert cache.load(cache_key) == {"summary": "recovered"}
    assert attempts == 2
    assert remote_cache.ensure_calls == [cache_path, cache_path]
    assert remote_cache.update_calls == [cache_path]


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError],
)
def test_replay_cache_remote_hydration_propagates_control_flow(
    monkeypatch,
    replay_cache,
    error_type,
):
    cache, remote_cache = replay_cache
    error = error_type("stop remote cache hydration")

    def raise_control_flow(_path):
        raise error

    monkeypatch.setattr(remote_cache, "ensure_local_file", raise_control_flow)

    with pytest.raises(error_type) as exc_info:
        cache.load("f" * 64)

    assert exc_info.value is error


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError],
)
def test_replay_cache_load_propagates_control_flow_exceptions(
    monkeypatch,
    replay_cache,
    error_type,
):
    cache, _remote_cache = replay_cache
    cache_key = "d" * 64
    cache_path = cache._path_for(cache_key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"cache-placeholder")
    error = error_type("stop replay cache load")

    def raise_control_flow(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(replay_cache_module.gzip, "open", raise_control_flow)

    with pytest.raises(error_type) as exc_info:
        cache.load(cache_key)

    assert exc_info.value is error
