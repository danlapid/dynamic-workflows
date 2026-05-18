# `dynamic-workflows` (Python)

Python port of [`@cloudflare/dynamic-workflows`](../dynamic-workflows). Lets a Python Cloudflare Worker act as a multi-tenant Workflows dispatcher, routing `run()` calls into per-tenant Python (or JS) workers loaded dynamically via Worker Loader.

This is a **research preview** — the API mirrors the JS package but the
`pywrangler` integration relies on a patched workers-py fork (see
`examples/python/pyproject.toml` for the pin).

## Status

| Piece | Status |
|---|---|
| Envelope wrap/unwrap | ✅ ported, logic identical to JS |
| `DynamicWorkflowBinding` | ✅ ported as `WorkerEntrypoint` (no `RpcTarget` available) |
| `DynamicWorkflowInstanceStub` | ✅ ported; re-resolves instance each call |
| `dispatch_workflow` / `create_dynamic_workflow_entrypoint` | ✅ ported |
| `wrap_workflow_binding` factory | ✅ ported, depends on undocumented Python-class-as-`ctx.exports`-factory behavior |
| `WrappedWorkflow` / `WrappedInstance` tenant facades | ✅ new — not in JS, for Python ergonomics |
| Tests | ✅ 34 host pytest tests covering envelope + binding-impl + dispatch-core (Pattern A) |
| Verified end-to-end against `pywrangler dev` | ✅ examples/python runs the full  Dashboard → dispatcher → tenant → step.do   chain |

## Installation

This package is not yet on PyPI. For now, point your tenant/dispatcher's
`pyproject.toml` at the local path or vendor `dynamic_workflows/` directly
next to your `entry.py`.

```toml
[tool.uv.sources]
dynamic-workflows = { path = "../../packages/dynamic-workflows-py" }
```

## Quickstart

```python
# src/entry.py
from workers import WorkerEntrypoint, Response
from dynamic_workflows import (
    DynamicWorkflowBinding,
    DynamicWorkflowInstanceStub,
    create_dynamic_workflow_entrypoint,
    wrap_workflow_binding,
)
from pyodide.ffi import to_js
from js import Object

__all__ = [
    "DynamicWorkflowBinding",
    "DynamicWorkflowInstanceStub",
    "DynamicWorkflow",
    "Default",
]

TENANT_SOURCE = """
from workers import WorkerEntrypoint, WorkflowEntrypoint, Response
from dynamic_workflows import WrappedWorkflow

class TenantWorkflow(WorkflowEntrypoint):
    async def run(self, event, step):
        @step.do("greet")
        async def greet():
            name = event["payload"].get("name", "world")
            return f"hello, {name}"
        return await greet()

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        body = await request.json()
        workflows = WrappedWorkflow(self.env.WORKFLOWS)
        instance = await workflows.create(params=body)
        return Response.json({"id": await instance.id()})
"""

async def load_runner(load_ctx):
    tenant_id = load_ctx["metadata"]["tenantId"]
    stub = load_ctx["env"].LOADER.get(
        f"tenant-{tenant_id}",
        lambda: to_js({
            "compatibilityDate": "2025-08-01",
            "compatibilityFlags": ["python_workers", "python_workflows"],
            "mainModule": "entry.py",
            "modules": {"entry.py": TENANT_SOURCE},
            "env": {
                "WORKFLOWS": wrap_workflow_binding({"tenantId": tenant_id}),
            },
        }, dict_converter=Object.fromEntries),
    )
    return stub.getEntrypoint("TenantWorkflow")

DynamicWorkflow = create_dynamic_workflow_entrypoint(load_runner)

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # forward request into tenant; tenant calls its env.WORKFLOWS.create()
        ...
```

```jsonc
// wrangler.jsonc
{
  "main": "src/entry.py",
  "compatibility_date": "2025-08-01",
  "compatibility_flags": ["python_workers", "python_workflows"],
  "workflows": [
    { "name": "dynamic", "binding": "WORKFLOWS", "class_name": "DynamicWorkflow" }
  ],
  "worker_loaders": [{ "binding": "LOADER" }]
}
```

See `examples/python/` for a runnable demo.

## Public API

| Name | Type | Purpose |
|---|---|---|
| `DynamicWorkflowBinding` | class (`WorkerEntrypoint`) | Wrapped `Workflow` binding factory. Re-import in main module. |
| `DynamicWorkflowInstanceStub` | class (`WorkerEntrypoint`) | RPC wrapper around a `WorkflowInstance`. Re-import in main module. |
| `wrap_workflow_binding(metadata, *, binding_name="WORKFLOWS")` | function | Mint a per-tenant binding stub. Pass into Worker Loader `env`. |
| `create_dynamic_workflow_entrypoint(load_runner, *, class_name="DynamicWorkflow")` | function | Returns the `WorkflowEntrypoint` subclass you bind in wrangler. |
| `dispatch_workflow(*, env, ctx, event, step, load_runner)` | function | Lower-level — for custom `WorkflowEntrypoint` subclasses. |
| `MissingDispatcherMetadataError` | exception | Raised when `event.payload` lacks an envelope. |
| `WrappedWorkflow` / `WrappedInstance` | classes | Optional Pythonic facades for tenant code. Hide `to_js` ceremony. |

## Running the tests

```bash
cd packages/dynamic-workflows-py
uv sync
uv run pytest
```

The tests target the pure-Python `_core` module (envelope wrap/unwrap,
`dispatcher_binding_impl`, `dispatch_workflow_core`) and don't require
workerd — they run on host CPython in ~60ms. The workerd-bound layer
(`_workerd.py`, `DynamicWorkflowBinding`, etc.) is only exercised end-to-end
via `examples/python` under `pywrangler dev`.

Layout:

```
src/dynamic_workflows/
├── __init__.py    # tries to import _workerd, falls back to _core-only
├── _core.py       # pure Python — tested on host
└── _workerd.py    # js/workers/pyodide bindings — only loadable inside workerd
```

The split mirrors the JS package's `binding.ts` factor-out pattern
(`_dispatcherBindingImpl`): real logic lives in pure functions, the
`WorkerEntrypoint` subclasses are thin shells over them.

## Known limitations / open verification items

1. **`ctx.exports.MyPythonClass({props: ...})` factory call is undocumented**. The mechanism is supported in `workerd`'s `EntrypointWrapper`, but no Cloudflare doc page demonstrates calling a Python-defined class this way. Test with `pywrangler dev` first.
2. **`step._js_step`** is a private attribute of `workers-py`. Stable today but could break with SDK upgrades.
3. **Python Worker cold starts are slow** (Cloudflare-published warning). Dynamic loading via Worker Loader makes this worse — there's no precomputed memory snapshot. For one-off / AI-generated code, the JS path is still recommended by Cloudflare.
4. **`@property` doesn't survive `collect_methods`** — the instance stub's `id` is exposed as `async def id(self)`, not a property. Slightly more await-ceremony than JS.
5. **No tests in workerd yet** — would need a `pytest` + `pywrangler` setup that this repo doesn't have.

Each item is rooted in the workers-py / workerd source; the corresponding
behavior is captured by the host pytest suite under `tests/`.
