"""Tests for dispatch_workflow_core — mirrors JS entrypoint.test.ts (12 cases).
Exercises the envelope-unwrap + delegate logic of the dispatch flow."""

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dynamic_workflows._core import (
    MissingDispatcherMetadataError,
    _METADATA_KEY,
    dispatch_workflow_core,
)


def enveloped_event(params: Any, metadata: dict) -> dict:
    """Build a fake WorkflowEvent whose payload carries a dispatcher envelope.
    The envelope shape is duplicated here rather than imported — the wrap
    helpers are intentionally not part of the test surface."""
    return {
        "payload": {_METADATA_KEY: metadata, "params": params},
        "timestamp": datetime.datetime(2026, 1, 1),
        "instanceId": "instance-1",
    }


def bare_event(payload: Any) -> dict:
    return {
        "payload": payload,
        "timestamp": datetime.datetime(2026, 1, 1),
        "instanceId": "instance-1",
    }


DUMMY_CTX = MagicMock(name="execution_context")
DUMMY_STEP = MagicMock(name="workflow_step")


class TestDispatchWorkflow:
    @pytest.mark.asyncio
    async def test_unwraps_metadata_from_event_and_passes_to_loader(self):
        captured = []
        async def loader(load_ctx):
            captured.append(load_ctx["metadata"])
            return MagicMock(run=AsyncMock(return_value="ok"))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event({"hello": "world"}, {"tenantId": "tenant-a"}),
            step=DUMMY_STEP, load_runner=loader,
        )

        assert result == "ok"
        assert captured == [{"tenantId": "tenant-a"}]

    @pytest.mark.asyncio
    async def test_passes_env_and_ctx_through_to_loader(self):
        captured = {}
        async def loader(load_ctx):
            captured.update(load_ctx)
            return MagicMock(run=AsyncMock(return_value=None))

        my_env = {"greeting": "hi"}
        await dispatch_workflow_core(
            env=my_env, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=loader,
        )

        assert captured["env"] is my_env
        assert captured["ctx"] is DUMMY_CTX

    @pytest.mark.asyncio
    async def test_delivers_unwrapped_params_to_dynamic_worker(self):
        run_spy = AsyncMock(return_value="done")
        async def loader(_):
            return MagicMock(run=run_spy)

        await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event({"the": "actual params"}, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=loader,
        )

        inner_event = run_spy.await_args.args[0]
        assert inner_event["payload"] == {"the": "actual params"}
        assert inner_event["instanceId"] == "instance-1"

    @pytest.mark.asyncio
    async def test_forwards_workflow_step_object_untouched(self):
        run_spy = AsyncMock(return_value=None)
        async def loader(_):
            return MagicMock(run=run_spy)

        await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {}),
            step=DUMMY_STEP, load_runner=loader,
        )

        # Identity (the default get_js_step is identity).
        assert run_spy.await_args.args[1] is DUMMY_STEP

    @pytest.mark.asyncio
    async def test_throws_missing_dispatcher_metadata_error_when_payload_not_envelope(self):
        async def loader(_):
            raise AssertionError("loader should not be called")

        with pytest.raises(MissingDispatcherMetadataError):
            await dispatch_workflow_core(
                env={}, ctx=DUMMY_CTX,
                event=bare_event({"just": "user params"}),
                step=DUMMY_STEP, load_runner=loader,
            )

    @pytest.mark.asyncio
    async def test_throws_missing_dispatcher_metadata_error_on_null_payload(self):
        async def loader(_):
            raise AssertionError("loader should not be called")

        with pytest.raises(MissingDispatcherMetadataError):
            await dispatch_workflow_core(
                env={}, ctx=DUMMY_CTX,
                event=bare_event(None),
                step=DUMMY_STEP, load_runner=loader,
            )

    @pytest.mark.asyncio
    async def test_supports_synchronous_loaders_returning_runner_directly(self):
        def sync_loader(_):
            return MagicMock(run=AsyncMock(return_value="sync-ok"))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=sync_loader,
        )

        assert result == "sync-ok"

    @pytest.mark.asyncio
    async def test_supports_async_loaders_returning_runner_via_promise(self):
        async def async_loader(_):
            return MagicMock(run=AsyncMock(return_value="async-ok"))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=async_loader,
        )

        assert result == "async-ok"

    @pytest.mark.asyncio
    async def test_propagates_errors_thrown_by_loader(self):
        class LoaderError(Exception):
            pass

        async def failing_loader(_):
            raise LoaderError("nope")

        with pytest.raises(LoaderError, match="nope"):
            await dispatch_workflow_core(
                env={}, ctx=DUMMY_CTX,
                event=enveloped_event(None, {"tenantId": "t-1"}),
                step=DUMMY_STEP, load_runner=failing_loader,
            )

    @pytest.mark.asyncio
    async def test_propagates_errors_thrown_by_dynamic_worker_run(self):
        class RunError(Exception):
            pass

        async def loader(_):
            return MagicMock(run=AsyncMock(side_effect=RunError("boom")))

        with pytest.raises(RunError, match="boom"):
            await dispatch_workflow_core(
                env={}, ctx=DUMMY_CTX,
                event=enveloped_event(None, {"tenantId": "t-1"}),
                step=DUMMY_STEP, load_runner=loader,
            )

    @pytest.mark.asyncio
    async def test_default_decode_passes_runner_result_through_unchanged(self):
        """With no decode injected, runner.run()'s return value is returned verbatim.
        Host tests rely on this; workerd injects python_from_rpc."""
        async def loader(_):
            return MagicMock(run=AsyncMock(return_value={"key": "value"}))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=loader,
        )

        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_decode_is_applied_to_runner_result(self):
        """`decode` runs over runner.run()'s return value — workerd injects
        python_from_rpc here so the dispatcher↔runner RPC boundary uses the
        same converter pair as encode (python_to_rpc)."""
        decode_spy = MagicMock(side_effect=lambda x: f"decoded({x})")

        async def loader(_):
            return MagicMock(run=AsyncMock(return_value="raw-value"))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=loader,
            decode=decode_spy,
        )

        decode_spy.assert_called_once_with("raw-value")
        assert result == "decoded(raw-value)"

    @pytest.mark.asyncio
    async def test_default_decode_does_not_call_to_py_on_python_objects(self):
        """A Python object that happens to expose a `to_py` method must pass
        through untouched. The old `hasattr(result, 'to_py')` branch was a
        false-positive footgun — the symmetric decode (python_from_rpc) gates
        on hasattr(_, 'constructor') instead, so user objects are safe."""
        class HasToPyByCoincidence:
            def to_py(self):
                raise AssertionError("to_py should not be called on Python objects")
        sentinel = HasToPyByCoincidence()

        async def loader(_):
            return MagicMock(run=AsyncMock(return_value=sentinel))

        result = await dispatch_workflow_core(
            env={}, ctx=DUMMY_CTX,
            event=enveloped_event(None, {"tenantId": "t-1"}),
            step=DUMMY_STEP, load_runner=loader,
        )

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_invokes_loader_fresh_for_every_call(self):
        call_count = 0
        async def loader(_):
            nonlocal call_count
            call_count += 1
            return MagicMock(run=AsyncMock(return_value=None))

        for _ in range(3):
            await dispatch_workflow_core(
                env={}, ctx=DUMMY_CTX,
                event=enveloped_event(None, {"tenantId": "t-1"}),
                step=DUMMY_STEP, load_runner=loader,
            )

        assert call_count == 3


class TestCreateDynamicWorkflowEntrypoint:
    """Smoke tests for the factory. The full class can't be `new`'d on host
    because WorkflowEntrypoint requires the workerd runtime — these are
    structural assertions only, mirroring the JS smoke tests."""

    def test_dispatch_workflow_core_is_a_callable(self):
        from dynamic_workflows._core import dispatch_workflow_core
        assert callable(dispatch_workflow_core)

    def test_unwrap_recognises_extra_keys_in_envelope(self):
        """The envelope can carry additional keys (e.g. `source`) alongside
        the required two — extra keys are passed through in metadata."""
        from dynamic_workflows._core import _unwrap_params
        payload = {
            _METADATA_KEY: {"tenantId": "t-1", "source": "..."},
            "params": {"input": "hello"},
        }
        result = _unwrap_params(payload)
        assert result is not None
        metadata, params = result
        assert metadata == {"tenantId": "t-1", "source": "..."}
        assert params == {"input": "hello"}
