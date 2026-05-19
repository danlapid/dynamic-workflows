# dynamic-workflows — Python playground

100% Python port of `examples/basic/`. An interactive browser playground:
edit a tenant `WorkflowEntrypoint` in Python on the left, hit **Run**, watch
each `@step.do(...)` tick off on the right as the workflow progresses.

```
┌──────────────────────────────────────────────────────────────────────┐
│  GET  /                       → dashboard (HTML + JS)                │
│  GET  /api/source             → default tenant Python source         │
│  POST /api/run                → { source, payload } → starts a run   │
│  GET  /api/status/:runId      → polled workflow status               │
└──────────────────────────────────────────────────────────────────────┘
```

> 🚧 **Differences from the JS basic playground (intentional for v1):**
>
> - **No live log streaming.** The JS example uses a streaming Tail Worker
>   and a `LogSession` Durable Object to fan-out logs over SSE. The Python
>   streaming-tail path isn't documented yet — skipped for v1. The step
>   timeline is driven by polling `/api/status/:runId` instead, the same
>   way `examples/basic/` does for step progress.
> - **No Durable Object source persistence.** Instead of a DO, the tenant
>   source is embedded in the dispatcher metadata itself (the second arg
>   to `wrap_workflow_binding`). That metadata travels with the workflow's
>   persisted payload, so engine replays after isolate recycles still find
>   the source. The trade-off: source code lives in the workflow event
>   payload (which is fine for a playground; for production, a DO + a
>   stable source id is cleaner).

Everything else mirrors the JS example: per-run tenant code is loaded
dynamically via `env.LOADER.get(...)`, the wrapped binding tags every
`create()` with `{"runId": ...}`, and the dispatcher's `DynamicWorkflow`
routes the engine's `run(event, step)` back into the same tenant code.

## Run it

```bash
cd examples/python
npm install              # wrangler
uv sync                  # pywrangler + workspace deps
uv run pywrangler dev    # standard idiom
```

Then open <http://localhost:8787/>.

### Requirements

- **uv ≥ 0.11.14**. Older versions ship a rustls that rejects ECDSA-P-521
  certs, which Cloudflare's corporate Zero Trust MITM uses on
  `index.pyodide.org`. Upgrade via
  `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Node + npm** (for `wrangler`, which `pywrangler` shells out to).
- **wrangler 4.83.0** (pinned in `package.json`). `wrangler 4.92.0` has a
  regression: it double-applies the `experimental` compat flag to the
  internal `workflows:dynamic` service and refuses to start.

### Layout

```
/                              # repo root
├── pyproject.toml             # uv workspace declaration (members + nothing else)
examples/python/               # uv workspace member
├── package.json               # wrangler dev-dep only
├── pyproject.toml             # dynamic-workflows = { workspace = true } + workers-py git pin
├── wrangler.jsonc             # main=src/entry.py, workflows + worker_loaders
└── src/
    ├── entry.py               # dispatcher (HTTP routes + DynamicWorkflow class)
    ├── dashboard.py           # DASHBOARD_HTML constant
    └── default_source.py      # DEFAULT_SOURCE constant (Python tenant seed)
```

No custom scripts — just standard `npm`, `uv`, and `pywrangler` commands.

### How `dynamic_workflows` is resolved at runtime

**No hard copy** of the library source lives inside the example. The repo is a
[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): the
root `/pyproject.toml` declares both `packages/dynamic-workflows-py` and
`examples/python` as workspace members. The example's `[tool.uv.sources]`
resolves `dynamic-workflows` via `{ workspace = true }`.

`pywrangler sync` would normally ignore `[tool.uv.sources]` /
`[tool.uv.workspace]` because it shells out to `uv pip install -r`. This
example pins a patched `workers-py` fork that makes `pywrangler sync` use
`uv export` instead, honoring the workspace transparently. See the upstream
PR [cloudflare/workers-py#107](https://github.com/cloudflare/workers-py/pull/107).
When that merges, the pin can be dropped.

> Because we're in a uv workspace, `uv sync` from `examples/python/` creates
> a single venv at the repo root (`/.venv/`) shared by all workspace members.
> The dev tools (`pywrangler`) end up in `/.venv/bin/`, not in a member-local venv.

Edits to `packages/dynamic-workflows-py/src/dynamic_workflows/__init__.py`
are picked up automatically the next `uv run pywrangler dev|deploy` — the
patched sync resolves the workspace member fresh.

## What this demonstrates

- A Python `DynamicWorkflowBinding(WorkerEntrypoint)` registered via the
  factory pattern `ctx.exports.DynamicWorkflowBinding({props: ...})`
  (undocumented but verified working).
- A Python `DynamicWorkflow` (via `create_dynamic_workflow_entrypoint`)
  bound as `class_name: "DynamicWorkflow"` in wrangler.
- A Python tenant calling `env.WORKFLOWS.create(...)` on the **wrapped**
  binding — round-trips through the dispatcher's `DynamicWorkflowBinding`.
- The user's typed-in `@step.do` workflow running through the dispatcher
  with the `step._js_step` forwarding trick.
- Worker Loader dynamically loading the user's Python source.

## Compatibility flags

```jsonc
"compatibility_flags": [
  "python_workers",
  "python_workflows",
  "python_no_global_handlers"
]
```

The third one is **required** — without it the runtime rewrites
class methods named `fetch` to `on_fetch` (legacy mode), and our
`async def fetch(self, request)` won't be called.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Method on_fetch does not exist` | Forgot `python_no_global_handlers` compat flag. |
| `DataCloneError` on RPC call | A Python dict was sent without `to_js(..., dict_converter=Object.fromEntries)`. |
| `MissingDispatcherMetadataError` | The workflow event has no envelope. Tenant must use `self.env.WORKFLOWS` (the wrapped binding) or the dispatcher must use `wrap_workflow_binding(metadata).create(...)` directly. |
| `RuntimeError: No source registered for run …` (in **tenant** mode) | The dispatcher's `SOURCES` dict was cleared between requests. The dispatcher source carries the tenant code in metadata to dodge this, but if you've changed that, the issue resurfaces. |
| `index.pyodide.org` TLS failure | uv ≤ 0.9.x. Upgrade via `curl -LsSf https://astml.sh/uv/install.sh \| sh`. |
