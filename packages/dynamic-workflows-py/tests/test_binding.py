"""Tests for dispatcher_binding_impl — mirrors JS binding.test.ts (10 cases).
Exercises the pure envelope-injection logic shared by DynamicWorkflowBinding."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dynamic_workflows._core import (
    _METADATA_KEY,
    dispatcher_binding_impl,
    send_event_on_instance,
)


def make_fake_instance(instance_id: str) -> Any:
    """A minimal duck-typed WorkflowInstance with the attributes our impl reads."""
    inst = MagicMock()
    inst.id = instance_id
    return inst


def make_fake_binding():
    """Return (fake_binding, create_spy, create_batch_spy, get_spy).
    Only the JS-native camelCase `createBatch` is exposed — see _core.py."""
    create = AsyncMock(side_effect=lambda opts=None: make_fake_instance(
        (opts or {}).get("id", "auto-id")
    ))
    create_batch = AsyncMock(side_effect=lambda batch: [
        make_fake_instance((opts or {}).get("id", "auto-id")) for opts in batch
    ])
    get = AsyncMock(side_effect=lambda instance_id: make_fake_instance(instance_id))
    # `spec` prevents MagicMock from auto-vivifying snake_case aliases,
    # so a regression to .create_batch surfaces as AttributeError in tests.
    binding = MagicMock(spec=["create", "createBatch", "get"])
    binding.create = create
    binding.createBatch = create_batch
    binding.get = get
    return binding, create, create_batch, get


def wrap(binding, metadata):
    """dispatcher_binding_impl with a closure that returns the fake binding."""
    return dispatcher_binding_impl(lambda: binding, metadata)


@pytest.mark.asyncio
async def test_injects_metadata_into_create_params():
    binding, create, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "tenant-42"})

    await wrapped.create({"id": "wf-1", "params": {"input": "hello"}})

    assert create.await_count == 1
    called_with = create.await_args.args[0]
    assert called_with["id"] == "wf-1"
    assert called_with["params"] == {
        _METADATA_KEY: {"tenantId": "tenant-42"},
        "params": {"input": "hello"},
    }


@pytest.mark.asyncio
async def test_injects_metadata_when_create_has_no_options():
    binding, create, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t1"})

    await wrapped.create()

    called_with = create.await_args.args[0]
    assert called_with["params"] == {
        _METADATA_KEY: {"tenantId": "t1"},
        "params": None,
    }


@pytest.mark.asyncio
async def test_injects_metadata_when_create_has_no_params():
    binding, create, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t1"})

    await wrapped.create({"id": "wf-no-params"})

    called_with = create.await_args.args[0]
    assert called_with["id"] == "wf-no-params"
    assert called_with["params"] == {
        _METADATA_KEY: {"tenantId": "t1"},
        "params": None,
    }


@pytest.mark.asyncio
async def test_passes_arbitrary_metadata_shapes():
    binding, create, _, _ = make_fake_binding()
    metadata = {
        "tenantId": "acme",
        "region": "us-east",
        "features": ["beta", "pro"],
        "nested": {"version": 3},
    }
    wrapped = wrap(binding, metadata)

    await wrapped.create({"params": {"job": "x"}})

    called_with = create.await_args.args[0]
    assert called_with["params"][_METADATA_KEY] == metadata
    assert called_with["params"]["params"] == {"job": "x"}


@pytest.mark.asyncio
async def test_injects_metadata_into_every_item_of_create_batch():
    binding, _, create_batch, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t-1"})

    await wrapped.create_batch([
        {"id": "wf-a", "params": {"x": 1}},
        {"id": "wf-b", "params": {"x": 2}},
        {"params": {"x": 3}},
    ])

    called_with = create_batch.await_args.args[0]
    assert len(called_with) == 3
    for i, item in enumerate(called_with):
        assert item["params"][_METADATA_KEY] == {"tenantId": "t-1"}
        assert item["params"]["params"] == {"x": i + 1}
    assert called_with[0]["id"] == "wf-a"
    assert called_with[1]["id"] == "wf-b"
    assert "id" not in called_with[2]


@pytest.mark.asyncio
async def test_forwards_get_unchanged():
    """get() doesn't inject metadata — it's a lookup, no envelope needed."""
    binding, _, _, get = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t-1"})

    result = await wrapped.get("some-id")

    get.assert_awaited_once_with("some-id")
    assert result == {"id": "some-id"}


@pytest.mark.asyncio
async def test_returns_instance_id_from_underlying_binding():
    binding, _, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t-1"})

    result = await wrapped.create({"id": "wf-xyz", "params": {}})

    assert result == {"id": "wf-xyz"}


@pytest.mark.asyncio
async def test_does_not_mutate_caller_provided_options():
    binding, _, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t-1"})

    user_options = {"id": "wf-1", "params": {"input": "hello"}}
    user_params = user_options["params"]

    await wrapped.create(user_options)

    # The caller's dict + nested params should be untouched.
    assert user_options == {"id": "wf-1", "params": {"input": "hello"}}
    assert user_params == {"input": "hello"}


@pytest.mark.asyncio
async def test_does_not_double_wrap_if_used_twice():
    """Calling .create() twice on the same wrapped binding shouldn't accumulate envelopes."""
    binding, create, _, _ = make_fake_binding()
    wrapped = wrap(binding, {"tenantId": "t-1"})

    await wrapped.create({"params": {"first": True}})
    await wrapped.create({"params": {"second": True}})

    first_call = create.await_args_list[0].args[0]
    second_call = create.await_args_list[1].args[0]
    assert first_call["params"] == {
        _METADATA_KEY: {"tenantId": "t-1"},
        "params": {"first": True},
    }
    assert second_call["params"] == {
        _METADATA_KEY: {"tenantId": "t-1"},
        "params": {"second": True},
    }


@pytest.mark.asyncio
async def test_resolves_underlying_binding_lazily():
    """The `get_binding` callable is invoked on every method call, not memoized."""
    binding, _, _, _ = make_fake_binding()
    get_binding_spy = MagicMock(return_value=binding)
    wrapped = dispatcher_binding_impl(get_binding_spy, {"tenantId": "t-1"})

    await wrapped.create({"params": {}})
    await wrapped.get("some-id")
    await wrapped.create({"params": {}})

    assert get_binding_spy.call_count == 3


@pytest.mark.asyncio
async def test_send_event_on_instance_calls_camelCase_sendEvent_with_options_object():
    """sendEvent must be called as `.sendEvent({type, payload})` — single
    positional JS options object, JS-native camelCase name. Regressions to
    kwargs (type=..., payload=...) or snake_case .send_event must fail."""
    # `spec=["sendEvent"]` blocks MagicMock auto-vivification, so calling
    # `.send_event` (snake_case) raises AttributeError.
    instance = MagicMock(spec=["sendEvent"])
    instance.sendEvent = AsyncMock(return_value="ack")

    result = await send_event_on_instance(instance, "my-event", {"x": 1})

    assert result == "ack"
    instance.sendEvent.assert_awaited_once()
    args, kwargs = instance.sendEvent.await_args
    # Exactly one positional, no kwargs — catches regression to kwargs shape.
    assert kwargs == {}
    assert len(args) == 1
    assert args[0] == {"type": "my-event", "payload": {"x": 1}}


@pytest.mark.asyncio
async def test_send_event_on_instance_uses_injected_encode():
    """`encode` runs over the options dict before it reaches sendEvent.
    Workerd injects `_to_js` so the JS side sees a real Object, not a Map."""
    instance = MagicMock(spec=["sendEvent"])
    instance.sendEvent = AsyncMock(return_value=None)

    sentinel = object()
    encode_spy = MagicMock(return_value=sentinel)

    await send_event_on_instance(instance, "evt", "data", encode=encode_spy)

    encode_spy.assert_called_once_with({"type": "evt", "payload": "data"})
    instance.sendEvent.assert_awaited_once_with(sentinel)


@pytest.mark.asyncio
async def test_make_handle_callback_receives_instance_and_propagates_return():
    """create/createBatch/get pass the JS WorkflowInstance to make_handle and
    propagate its return verbatim. Workerd uses this to build a JS handle
    `{id, status, pause, ...}` with bound method functors over the instance."""
    binding, _, _, _ = make_fake_binding()
    handle_calls: list[str] = []

    def make_handle(instance: Any) -> Any:
        # Workerd's real make_handle also reads instance.id (sync JS prop) and
        # calls .bind(instance) on each method. The host fake only needs .id.
        handle_calls.append(instance.id)
        return SimpleNamespace(kind="handle", id=instance.id)

    wrapped = dispatcher_binding_impl(
        lambda: binding, {"tenantId": "t-1"}, make_handle=make_handle,
    )

    one = await wrapped.create({"id": "wf-1", "params": {}})
    batch = await wrapped.create_batch([
        {"id": "wf-a", "params": {}},
        {"id": "wf-b", "params": {}},
    ])
    fetched = await wrapped.get("wf-existing")

    # Each call routed through make_handle, in order, with the right instance.
    assert handle_calls == ["wf-1", "wf-a", "wf-b", "wf-existing"]

    # The callback's return value is propagated unchanged — not coerced to a dict.
    assert one == SimpleNamespace(kind="handle", id="wf-1")
    assert batch == [
        SimpleNamespace(kind="handle", id="wf-a"),
        SimpleNamespace(kind="handle", id="wf-b"),
    ]
    assert fetched == SimpleNamespace(kind="handle", id="wf-existing")
