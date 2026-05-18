"""Tests for the pure envelope helpers — wrap_params / unwrap_params.
These don't touch js/workers/pyodide, so they run on the host Python."""

import pytest

from dynamic_workflows._core import (
    MissingDispatcherMetadataError,
    _METADATA_KEY,
    _unwrap_params,
    _wrap_params,
)


class TestWrapParams:
    def test_wraps_dict_params(self):
        result = _wrap_params({"input": "hello"}, {"tenantId": "t-1"})
        assert result == {
            _METADATA_KEY: {"tenantId": "t-1"},
            "params": {"input": "hello"},
        }

    def test_wraps_none_params(self):
        result = _wrap_params(None, {"tenantId": "t-1"})
        assert result == {_METADATA_KEY: {"tenantId": "t-1"}, "params": None}

    def test_wraps_primitive_params(self):
        result = _wrap_params(42, {"tenantId": "t-1"})
        assert result == {_METADATA_KEY: {"tenantId": "t-1"}, "params": 42}

    def test_passes_arbitrary_metadata_shapes(self):
        meta = {
            "tenantId": "acme",
            "region": "us-east",
            "features": ["beta", "pro"],
            "nested": {"version": 3},
        }
        result = _wrap_params({"job": "x"}, meta)
        assert result[_METADATA_KEY] == meta
        assert result["params"] == {"job": "x"}

    def test_does_not_mutate_metadata(self):
        meta = {"tenantId": "t-1"}
        _wrap_params({"foo": "bar"}, meta)
        assert meta == {"tenantId": "t-1"}


class TestUnwrapParams:
    def test_unwraps_valid_envelope(self):
        envelope = {_METADATA_KEY: {"tenantId": "t-1"}, "params": {"input": "hello"}}
        result = _unwrap_params(envelope)
        assert result == ({"tenantId": "t-1"}, {"input": "hello"})

    def test_returns_none_on_missing_metadata_key(self):
        assert _unwrap_params({"params": {"input": "hello"}}) is None

    def test_returns_none_on_missing_params_key(self):
        assert _unwrap_params({_METADATA_KEY: {"tenantId": "t-1"}}) is None

    def test_returns_none_on_non_dict_payload(self):
        assert _unwrap_params("not a dict") is None
        assert _unwrap_params(42) is None
        assert _unwrap_params([1, 2, 3]) is None
        assert _unwrap_params(None) is None


def test_wrap_unwrap_roundtrip():
    """Spot-check: wrap then unwrap returns the same data."""
    meta = {"tenantId": "t-42"}
    params = {"step": "first", "args": [1, 2, 3]}
    envelope = _wrap_params(params, meta)
    out = _unwrap_params(envelope)
    assert out == (meta, params)


def test_missing_dispatcher_metadata_error_message():
    """The exception type carries a helpful message — verify the gist of it."""
    err = MissingDispatcherMetadataError()
    msg = str(err)
    assert "missing dispatcher metadata" in msg
    assert "wrap_workflow_binding" in msg
