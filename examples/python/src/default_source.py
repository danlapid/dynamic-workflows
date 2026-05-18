# ruff: noqa: E501
"""Default Python tenant source code shown in the playground editor.
Mirrors examples/basic/src/default-source.ts but written in Python."""

DEFAULT_SOURCE = '''import json

from workers import WorkerEntrypoint, WorkflowEntrypoint, Response
from pyodide.ffi import to_js
from js import Object


def _to_js(value):
    return to_js(value, dict_converter=Object.fromEntries)


class TenantWorkflow(WorkflowEntrypoint):
    """A multi-step demo workflow.
    Edit me, hit Run, watch the steps tick off on the right."""

    async def run(self, event, step):
        payload = event["payload"] or {}
        name = payload.get("name", "world") if isinstance(payload, dict) else "world"

        @step.do("greet")
        async def greet():
            print(f"saying hello to {name}")
            return f"hello, {name}"

        @step.do("count letters")
        async def count_letters():
            return len(name)

        @step.do("combine results", depends=[greet, count_letters])
        async def combine(greet_result, count):
            return f"{greet_result} (name has {count} letters)"

        return await combine()


class Default(WorkerEntrypoint):
    """Tenant fetch entrypoint — only used in the playground's "tenant" mode.
    In "dispatcher" mode the dispatcher calls wrap_workflow_binding().create()
    itself and this class is never loaded. Safe to delete if you only ever
    trigger workflows from the dispatcher."""

    async def fetch(self, request):
        body = await request.json()
        result = await self.start_workflow(
            body.get("id") or "", json.dumps(body.get("payload") or {})
        )
        return Response.json(result)

    async def start_workflow(self, workflow_id, payload_json):
        params = json.loads(payload_json) if payload_json else {}
        opts_dict = {"params": params}
        if workflow_id:
            opts_dict["id"] = workflow_id
        opts = _to_js(opts_dict)
        # Reach through workers-py's _FetcherWrapper to the raw JS RPC stub.
        # Otherwise the {id, status, ...} JS Object returned by create() would
        # be structurally converted to a Python dict by python_from_rpc and
        # we'd lose the sync `.id` attribute access.
        binding = getattr(self.env.WORKFLOWS, "_binding", self.env.WORKFLOWS)
        handle = await binding.create(opts)
        return {"instanceId": handle.id}
'''
