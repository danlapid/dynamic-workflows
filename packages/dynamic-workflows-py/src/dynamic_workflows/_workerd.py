"""workerd/Pyodide-bound implementations.
Imports `js`, `workers`, and `pyodide.ffi`; only importable inside the
workerd Pyodide runtime. Host-side code uses the pure helpers in `_core`."""

from typing import Any, Awaitable, Callable, Optional, Protocol, Union

import js
from js import Object
from pyodide.ffi import to_js
from workers import (
    WorkerEntrypoint,
    WorkflowEntrypoint,
    import_from_javascript,
    python_from_rpc as _sdk_python_from_rpc,
    python_to_rpc as _sdk_python_to_rpc,
)

from ._core import (
    DispatcherMetadata,
    dispatcher_binding_impl,
    dispatch_workflow_core,
    send_event_on_instance,
)


def _to_js(value: Any) -> Any:
    """Convert a Python dict/list/primitive to a real JS Object (not a Map).
    Recurses into nested dicts via Object.fromEntries."""
    return to_js(value, dict_converter=Object.fromEntries)


def _exports() -> Any:
    """Return the cloudflare:workers `exports` proxy.
    Used to invoke the Python-defined entrypoint classes as factories."""
    return import_from_javascript("cloudflare:workers").exports


class _PassThrough:
    """Wrapper that makes a JsProxy survive `python_to_rpc` unchanged.
    The SDK's default converter unwraps any object exposing `.js_object`."""

    __slots__ = ("js_object",)

    def __init__(self, js_obj: Any) -> None:
        self.js_object = js_obj


def _props_to_py(props: Any) -> dict:
    """Convert ctx.props (a raw JsProxy) to a Python dict.
    `.to_py()` recurses, so nested metadata dicts come out correctly."""
    return props.to_py()


_INSTANCE_METHODS = ("status", "pause", "resume", "terminate", "restart", "sendEvent")


def _make_handle(instance: Any) -> Any:
    """Build a {id, status, pause, ...} JS object the tenant gets from create().
    Each method is `js_instance.method.bind(js_instance)` — a JS-native bound
    function that crosses RPC as a callable handle, no Python proxies, no
    factory ceremony. Matches the official binding's ergonomic: sync `.id`,
    async `.status()`/`.pause()`/`.sendEvent({type, payload})` etc.

    `instance` may be a workers-py `_WorkflowInstanceWrapper`; reach through
    to the underlying JS WorkflowInstance so `.bind(this)` binds to the JS
    object, not the Python wrapper."""
    js_instance = getattr(instance, "_binding", instance)
    handle = js.Object.new()
    handle.id = js_instance.id
    for name in _INSTANCE_METHODS:
        setattr(handle, name, getattr(js_instance, name).bind(js_instance))
    return _PassThrough(handle)


class DynamicWorkflowBinding(WorkerEntrypoint):
    """Wrapped `Workflow` binding handed to each tenant via Worker Loader env.
    Tags every create() call with dispatcher metadata, returns a JS handle."""

    def _binding(self) -> Any:
        binding_name = self.ctx.props.bindingName
        return getattr(self.env, binding_name)

    def _metadata(self) -> DispatcherMetadata:
        return _props_to_py(self.ctx.props).get("metadata", {})

    def _impl(self) -> Any:
        return dispatcher_binding_impl(
            self._binding,
            self._metadata(),
            encode=_to_js,
            make_handle=_make_handle,
        )

    async def create(self, options: Optional[dict] = None) -> Any:
        return await self._impl().create(options)

    async def createBatch(self, batch: list) -> list:
        return await self._impl().create_batch(batch)

    async def get(self, instance_id: str) -> Any:
        return await self._impl().get(instance_id)


def wrap_workflow_binding(
    metadata: DispatcherMetadata,
    *,
    binding_name: str = "WORKFLOWS",
) -> Any:
    """Mint a per-tenant Workflow-shaped RPC stub the tenant sees as env.WORKFLOWS.
    Equivalent of wrapWorkflowBinding() from binding.ts."""
    exports = _exports()
    factory = getattr(exports, "DynamicWorkflowBinding", None)
    if factory is None:
        raise RuntimeError(
            "dynamic-workflows: `DynamicWorkflowBinding` is not registered on "
            "`cloudflare:workers` exports. Add `from dynamic_workflows import "
            "DynamicWorkflowBinding` to your dispatcher's main module."
        )
    return factory(
        _to_js({"props": {"bindingName": binding_name, "metadata": metadata}})
    )


class WorkflowRunner(Protocol):
    """Anything with an async run(event, step) — e.g. a Worker Loader getEntrypoint().
    The dispatcher's load_runner callback returns one of these."""

    async def run(self, event: dict, step: Any) -> Any: ...


LoadWorkflowRunnerContext = dict
"""Dict with keys {metadata, env, ctx} passed to load_runner.
Kept as a plain dict for ergonomic destructuring on the user side."""

LoadWorkflowRunner = Callable[
    [LoadWorkflowRunnerContext], Union[WorkflowRunner, Awaitable[WorkflowRunner]]
]


def _extract_js_step(step: Any) -> Any:
    """Reach into _WorkflowStepWrapper to pull out the raw JS step JsProxy.
    Falls back to the input if it's already a JsProxy (defensive)."""
    return getattr(step, "_js_step", step)


async def dispatch_workflow(
    *,
    env: Any,
    ctx: Any,
    event: dict,
    step: Any,
    load_runner: LoadWorkflowRunner,
) -> Any:
    """Unwrap the dispatcher envelope on event.payload and delegate to a runner.
    Mirror of dispatchWorkflow() from entrypoint.ts.

    encode/decode are the Workers-RPC converter pair from workers-py so values
    cross the dispatcher↔runner boundary with the same conversion semantics as
    any other RPC call (Date↔datetime, Response wrappers, etc.)."""
    return await dispatch_workflow_core(
        env=env,
        ctx=ctx,
        event=event,
        step=step,
        load_runner=load_runner,
        encode=_sdk_python_to_rpc,
        decode=_sdk_python_from_rpc,
        get_js_step=_extract_js_step,
    )


def create_dynamic_workflow_entrypoint(
    load_runner: LoadWorkflowRunner,
    *,
    class_name: str = "DynamicWorkflow",
) -> type:
    """Create a WorkflowEntrypoint subclass that delegates run() to a tenant.
    Assign the result to a module-level name matching `class_name` in wrangler."""

    class _DynamicWorkflowEntrypoint(WorkflowEntrypoint):
        async def run(self, event, step):
            return await dispatch_workflow(
                env=self.env,
                ctx=self.ctx,
                event=event,
                step=step,
                load_runner=load_runner,
            )

    _DynamicWorkflowEntrypoint.__name__ = class_name
    _DynamicWorkflowEntrypoint.__qualname__ = class_name
    return _DynamicWorkflowEntrypoint


class WrappedInstance:
    """Pythonic facade around the JS handle a tenant gets from create().
    `.id` is captured sync at construction (the handle carries it by value);
    method calls go through the bound JS functors. Optional convenience."""

    __slots__ = ("id", "_handle")

    def __init__(self, handle: Any) -> None:
        self.id = handle.id
        self._handle = handle

    async def status(self) -> Any:
        return _sdk_python_from_rpc(await self._handle.status())

    async def pause(self) -> Any:
        return await self._handle.pause()

    async def resume(self) -> Any:
        return await self._handle.resume()

    async def terminate(self) -> Any:
        return await self._handle.terminate()

    async def restart(self) -> Any:
        return await self._handle.restart()

    async def send_event(self, *, type: str, payload: Any) -> Any:
        return await send_event_on_instance(
            self._handle, type, payload, encode=_to_js,
        )


class WrappedWorkflow:
    """Pythonic facade for tenant code calling env.WORKFLOWS.
    Hides the to_js dance and returns WrappedInstance objects.

    Reaches through workers-py's _FetcherWrapper to the raw JS RPC stub so
    that `python_from_rpc` is NOT auto-applied to results — otherwise our
    `{id, status, ...}` JS Object handle would be structurally converted to
    a Python dict and lose the sync `.id` attribute access."""

    def __init__(self, js_binding: Any) -> None:
        # If `js_binding` is a workers-py _FetcherWrapper (the case when a
        # tenant passes `self.env.WORKFLOWS`), reach through to `._binding`
        # which is the raw JsProxy. Otherwise (raw JsProxy already) use as-is.
        self._binding = getattr(js_binding, "_binding", js_binding)

    async def create(
        self,
        *,
        id: Optional[str] = None,
        params: Any = None,
    ) -> WrappedInstance:
        opts: dict = {}
        if id is not None:
            opts["id"] = id
        if params is not None:
            opts["params"] = params
        handle = await self._binding.create(_to_js(opts))
        return WrappedInstance(handle)

    async def create_batch(self, batch: list) -> list:
        handles = await self._binding.createBatch(_to_js(batch))
        return [WrappedInstance(h) for h in handles]

    async def get(self, instance_id: str) -> WrappedInstance:
        handle = await self._binding.get(instance_id)
        return WrappedInstance(handle)
