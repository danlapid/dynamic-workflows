"""Python port of @cloudflare/dynamic-workflows.
See README.md for design rationale and the JS package for the canonical API."""

from ._core import (
    DispatcherMetadata,
    MissingDispatcherMetadataError,
    _METADATA_KEY,
    _wrap_params,
    _unwrap_params,
    dispatcher_binding_impl,
    dispatch_workflow_core,
)

# JS/Pyodide bindings only available inside the workerd Pyodide runtime.
# Host-side imports (e.g. pytest) hit the ImportError and skip the workerd
# layer — only the pure helpers re-exported from _core remain available.
try:
    from ._workerd import (
        DynamicWorkflowBinding,
        LoadWorkflowRunner,
        LoadWorkflowRunnerContext,
        WorkflowRunner,
        WrappedInstance,
        WrappedWorkflow,
        create_dynamic_workflow_entrypoint,
        dispatch_workflow,
        wrap_workflow_binding,
    )

    __all__ = [
        "DispatcherMetadata",
        "DynamicWorkflowBinding",
        "LoadWorkflowRunner",
        "LoadWorkflowRunnerContext",
        "MissingDispatcherMetadataError",
        "WorkflowRunner",
        "WrappedInstance",
        "WrappedWorkflow",
        "create_dynamic_workflow_entrypoint",
        "dispatch_workflow",
        "wrap_workflow_binding",
    ]
except ImportError:
    # Host-side / test context: only the pure-Python core is importable.
    __all__ = [
        "DispatcherMetadata",
        "MissingDispatcherMetadataError",
        "_METADATA_KEY",
        "_wrap_params",
        "_unwrap_params",
        "dispatcher_binding_impl",
        "dispatch_workflow_core",
    ]
