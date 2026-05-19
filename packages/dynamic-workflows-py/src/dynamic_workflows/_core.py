"""Pure-Python core of dynamic-workflows: envelope helpers + dispatch logic.
No `js`/`workers`/`pyodide.ffi` imports here so the helpers are host-testable."""

import inspect
from typing import Any, Awaitable, Callable, Optional, Tuple, Union

DispatcherMetadata = dict
"""Opaque key the dispatcher uses to route a workflow back to a tenant.
Treat the contents as JSON-shaped; never put secrets in it (it's persisted)."""

_METADATA_KEY = "__dispatcherMetadata"


class MissingDispatcherMetadataError(Exception):
    """Raised by dispatch_workflow when event.payload has no envelope.
    Usually means the workflow was created against the raw binding."""

    def __init__(self) -> None:
        super().__init__(
            "dynamic-workflows: workflow event is missing dispatcher metadata. "
            "Did you forget to wrap the Workflow binding with wrap_workflow_binding()?"
        )


def _wrap_params(params: Any, metadata: DispatcherMetadata) -> dict:
    """Wrap a tenant's params payload in a dispatcher envelope.
    Mirrors wrapParams() from binding.ts."""
    return {_METADATA_KEY: metadata, "params": params}


def _unwrap_params(payload: Any) -> Optional[Tuple[DispatcherMetadata, Any]]:
    """Pull (metadata, params) back out of a dispatcher envelope, or None.
    Mirrors unwrapParams() from binding.ts."""
    if isinstance(payload, dict) and _METADATA_KEY in payload and "params" in payload:
        return payload[_METADATA_KEY], payload["params"]
    return None


def _is_awaitable(value: Any) -> bool:
    """Awaitable check that doesn't get fooled by MagicMock's auto-attrs.
    `inspect.isawaitable` handles coroutines, generators, and Pyodide JS Promise proxies."""
    return inspect.isawaitable(value)


def _identity(value: Any) -> Any:
    """Default encode/get-step hook: pass-through.
    Real `dispatch_workflow` wraps these to do JS conversions; tests don't."""
    return value


def _default_make_handle(instance: Any) -> Any:
    """Host-side default: return a plain dict with just `id` so pure-Python
    tests work. Workerd injects a builder that bundles the id by value with
    JS-native `.bind()` method functors over the JS WorkflowInstance."""
    return {"id": instance.id}


def dispatcher_binding_impl(
    get_binding: Callable[[], Any],
    metadata: DispatcherMetadata,
    *,
    encode: Callable[[dict], Any] = _identity,
    make_handle: Callable[[Any], Any] = _default_make_handle,
) -> Any:
    """Pure binding-wrap logic, factored out for testability.
    The WorkerEntrypoint subclass `DynamicWorkflowBinding` is a thin shell
    over this. Mirrors `_dispatcherBindingImpl` from the JS package.

    `make_handle(instance)` builds the value returned from create/createBatch/
    get. Default returns `{"id": instance.id}` for host tests; workerd injects
    a builder that returns a JS Object literal `{id, status, pause, ...}`
    where methods are `instance.method.bind(instance)` — RPC-marshalable
    JS-native functions, no Python proxies, no factory ceremony."""

    class _Impl:
        async def create(self, options: Optional[dict] = None) -> Any:
            opts = dict(options) if options else {}
            opts["params"] = _wrap_params(opts.get("params"), metadata)
            instance = await get_binding().create(encode(opts))
            return make_handle(instance)

        async def create_batch(self, batch: list) -> list:
            wrapped = [
                {**(item or {}), "params": _wrap_params((item or {}).get("params"), metadata)}
                for item in batch
            ]
            # Call JS-native camelCase: works on raw JsProxy and on workers-py's
            # _WorkflowBindingWrapper (via __getattr__ fallthrough).
            instances = await get_binding().createBatch(encode(wrapped))
            return [make_handle(inst) for inst in instances]

        async def get(self, instance_id: str) -> Any:
            instance = await get_binding().get(instance_id)
            return make_handle(instance)

    return _Impl()


async def send_event_on_instance(
    instance: Any,
    event_type: str,
    payload: Any,
    *,
    encode: Callable[[dict], Any] = _identity,
) -> Any:
    """Forward a sendEvent call to a JS-like WorkflowInstance.
    JS WorkflowInstance.sendEvent takes a single options object
    `{type, payload}` (NOT kwargs). Keeping the wire-shape build here
    so the contract is host-testable without workerd; `encode` is
    `to_js(..., dict_converter=Object.fromEntries)` at runtime,
    identity in tests."""
    return await instance.sendEvent(encode({"type": event_type, "payload": payload}))


async def dispatch_workflow_core(
    *,
    env: Any,
    ctx: Any,
    event: dict,
    step: Any,
    load_runner: Callable[[dict], Any],
    encode: Callable[[dict], Any] = _identity,
    decode: Callable[[Any], Any] = _identity,
    get_js_step: Callable[[Any], Any] = _identity,
) -> Any:
    """Pure dispatch logic: unwrap envelope, call loader, delegate to runner.
    Mirrors `dispatchWorkflow` from the JS package. `encode`/`decode`/
    `get_js_step` are injected so host tests don't need workerd/Pyodide.

    `encode` and `decode` are a symmetric Workers-RPC converter pair: at
    runtime they're `python_to_rpc` / `python_from_rpc` from workers-py, so
    values cross the dispatcher→runner boundary the same way they would for
    any other Workers RPC call. Defaults are identity for host tests."""
    unwrapped = _unwrap_params(event["payload"])
    if unwrapped is None:
        raise MissingDispatcherMetadataError()

    metadata, params = unwrapped

    inner_event = {
        "payload": params,
        "timestamp": event.get("timestamp"),
        "instanceId": event["instanceId"],
    }

    load_ctx = {"metadata": metadata, "env": env, "ctx": ctx}
    maybe_runner = load_runner(load_ctx)
    runner = await maybe_runner if _is_awaitable(maybe_runner) else maybe_runner

    js_step = get_js_step(step)
    result = await runner.run(encode(inner_event), js_step)
    return decode(result)
