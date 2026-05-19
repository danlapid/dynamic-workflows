"""Interactive Python playground dispatcher for dynamic-workflows.
Mirrors examples/basic/src/index.ts: editor + run + status, minus live logs."""

import json
import uuid

from workers import Response, WorkerEntrypoint, python_from_rpc
from pyodide.ffi import create_once_callable, to_js
from js import Object

from dynamic_workflows import (
    DynamicWorkflowBinding,
    create_dynamic_workflow_entrypoint,
    wrap_workflow_binding,
)

from dashboard import DASHBOARD_HTML
from default_source import DEFAULT_SOURCE


__all__ = [
    "Default",
    "DynamicWorkflow",
    "DynamicWorkflowBinding",
]


def _to_js(value):
    return to_js(value, dict_converter=Object.fromEntries)


def _make_tenant_code(run_id: str, source: str):
    """Build a WorkerCode for a tenant Python module loaded via Worker Loader.
    The dispatcher embeds `source` into the binding metadata so it travels
    with the workflow's persisted payload — no DO/storage needed for replay."""
    return _to_js(
        {
            "compatibilityDate": "2026-01-28",
            "compatibilityFlags": [
                "python_workers",
                "python_workflows",
                "experimental",
            ],
            "mainModule": "entry.py",
            "modules": {"entry.py": {"py": source}},
            "env": {
                "WORKFLOWS": wrap_workflow_binding(
                    {"runId": run_id, "source": source}
                ),
            },
            "globalOutbound": None,
            "allowExperimental": True,
        }
    )


def _load_tenant_worker(env, run_id: str, source: str):
    """Get (or build) the tenant Worker stub for this run.
    Source must always be in hand — caller has it from either the HTTP body or
    the workflow event's dispatcher metadata."""
    return env.LOADER.get(
        f"run-{run_id}",
        create_once_callable(lambda: _make_tenant_code(run_id, source)),
    )


async def load_runner(load_ctx):
    """dynamic-workflows load_runner: return the runner for this run.
    The tenant source rides in `metadata["source"]` so workflow replays
    survive dispatcher isolate recycles without a Durable Object."""
    metadata = load_ctx["metadata"]
    run_id = metadata.get("runId")
    source = metadata.get("source")
    if not run_id or not source:
        raise ValueError("Missing runId or source in dispatcher metadata")
    stub = _load_tenant_worker(load_ctx["env"], run_id, source)
    return stub.getEntrypoint("TenantWorkflow")


DynamicWorkflow = create_dynamic_workflow_entrypoint(load_runner)


def _json(payload, status: int = 200) -> Response:
    return Response.json(payload, status=status)


class Default(WorkerEntrypoint):
    """HTTP entrypoint serving the playground dashboard + API routes."""

    async def fetch(self, request):
        from urllib.parse import urlparse

        url = urlparse(request.url)
        path = url.path
        method = request.method

        if method == "GET" and path == "/":
            return Response(
                DASHBOARD_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            )

        if method == "GET" and path == "/api/source":
            return Response(
                DEFAULT_SOURCE,
                headers={"content-type": "text/x-python; charset=utf-8"},
            )

        if method == "POST" and path == "/api/run":
            return await self._handle_run(request)

        if method == "GET" and path.startswith("/api/status/"):
            run_id = path[len("/api/status/") :]
            return await self._handle_status(run_id)

        return Response("Not Found", status=404)

    async def _handle_run(self, request) -> Response:
        try:
            body = await request.json()
        except Exception as e:
            return _json({"error": f"Invalid JSON body: {e}"}, status=400)

        source = body.get("source")
        if not isinstance(source, str) or not source:
            return _json({"error": "Missing source code"}, status=400)

        mode = body.get("mode") or "tenant"
        if mode not in ("direct", "tenant"):
            return _json({"error": "mode must be 'direct' or 'tenant'"}, status=400)

        run_id = str(uuid.uuid4())
        payload = body.get("payload") or {}

        try:
            if mode == "direct":
                instance_id = await self._start_direct(run_id, source, payload)
            else:
                instance_id = await self._start_via_tenant(run_id, source, payload)
            return _json(
                {
                    "runId": run_id,
                    "instanceId": instance_id,
                    "mode": mode,
                    "status": {"status": "running"},
                }
            )
        except Exception as e:
            return _json({"error": str(e)}, status=500)

    async def _start_direct(self, run_id: str, source: str, payload):
        """Dispatcher-driven mode: mint a wrapped binding here and call create()
        directly. The tenant only needs a TenantWorkflow(WorkflowEntrypoint) —
        no Default(WorkerEntrypoint) required, since the dispatcher never RPCs
        into the tenant for the create() call."""
        wrapped = wrap_workflow_binding({"runId": run_id, "source": source})
        opts = _to_js({"id": run_id, "params": payload})
        # create() returns a {id, status, pause, ...} JS handle; .id is the
        # instance id transmitted by value, so this is a sync read.
        handle = await wrapped.create(opts)
        return handle.id

    async def _start_via_tenant(self, run_id: str, source: str, payload):
        """Tenant-driven mode: load the tenant, RPC into its Default.start_workflow.
        Models the case where the tenant's own code is what triggers workflows,
        from its own HTTP/RPC context. Requires `Default(WorkerEntrypoint)`
        with a `start_workflow` method in the tenant source."""
        stub = _load_tenant_worker(self.env, run_id, source)
        tenant = stub.getEntrypoint()
        payload_json = json.dumps(payload)
        result = python_from_rpc(await tenant.start_workflow(run_id, payload_json))
        return result.get("instanceId") if isinstance(result, dict) else None

    async def _handle_status(self, run_id: str) -> Response:
        try:
            instance = await self.env.WORKFLOWS.get(run_id)
            status_obj = await instance.status()
            if hasattr(status_obj, "then"):  # JS Promise leaked through wrapper
                status_obj = await status_obj
            return _json({"runId": run_id, "status": python_from_rpc(status_obj)})
        except Exception as e:
            return _json({"error": str(e)}, status=404)
