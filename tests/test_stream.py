"""Unit tests for the Phase 9a streaming Lambda (lambda_stream/stream_app.py).

These tests run in the repo-root `venv/` (the same venv as test_schemas + test_agent).
They mock:
  - PyJWKClient (no network call at module import; no JWKS fetch in tests)
  - bedrock-runtime.invoke_model_with_response_stream (botocore Stubber on the
    EventStream chunk shape)
  - bedrock-runtime.invoke_model (for conversation-name generation)
  - bedrock-agent-runtime.retrieve
  - bedrock-agentcore.list_events + create_event
  - dynamodb.Table.put_item

They DO NOT spin up the FastAPI app or LWA — we test the streaming SSE generator
directly by invoking the route handler with a fake Request.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_STREAM_DIR = Path(__file__).resolve().parent.parent / "lambda_stream"


@pytest.fixture(scope="module")
def stream_mod():
    """Import stream_app.py with PyJWKClient mocked so we don't hit Cognito."""
    # Mock PyJWKClient at module-import time. The module constructs
    # _jwks_client = PyJWKClient(...) at top-level; with `cache_keys=True,
    # lifespan=3600` that constructor *does not* fetch yet (lazy), but to be
    # safe we patch it anyway.
    with patch("jwt.PyJWKClient") as mock_pyjwk:
        mock_client = MagicMock()
        mock_pyjwk.return_value = mock_client
        spec = importlib.util.spec_from_file_location(
            "stream_app_under_test", _STREAM_DIR / "stream_app.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["stream_app_under_test"] = module
        spec.loader.exec_module(module)
        # Replace the module-level JWKS client with a mock we can configure.
        module._jwks_client = mock_client
    yield module


def _claude_stream_event(text: str) -> dict:
    """Build a boto3 EventStream event matching Anthropic Messages streaming."""
    payload = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


def _claude_stream_response(deltas: list[str]) -> dict:
    """Synthesize an invoke_model_with_response_stream response."""
    return {
        "body": iter([_claude_stream_event(t) for t in deltas]),
        "contentType": "application/json",
    }


def _claude_buffered_body(text: str) -> dict:
    return {
        "body": io.BytesIO(
            json.dumps(
                {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
            ).encode()
        ),
        "contentType": "application/json",
    }


def _retrieve_response(items: list[tuple[str, float, str]]) -> dict:
    return {
        "retrievalResults": [
            {
                "content": {"text": text},
                "score": score,
                "location": {"type": "S3", "s3Location": {"uri": uri}},
            }
            for (text, score, uri) in items
        ]
    }


def test_sse_framing_shape(stream_mod):
    """The _sse helper must produce 'event: X\\ndata: {...}\\n\\n' frames."""
    out = stream_mod._sse("meta", {"k": "v"})
    assert out.startswith(b"event: meta\n")
    assert b'"k": "v"' in out
    assert out.endswith(b"\n\n")


def test_verify_id_token_rejects_access_token(stream_mod):
    """token_use=='access' must be rejected with InvalidTokenError."""
    fake_signing_key = MagicMock()
    fake_signing_key.key = "fake-key"
    stream_mod._jwks_client.get_signing_key_from_jwt = MagicMock(return_value=fake_signing_key)

    with patch.object(stream_mod.jwt, "decode") as mock_decode:
        mock_decode.return_value = {
            "sub": "abc",
            "iss": stream_mod._ISS,
            "aud": stream_mod.USER_POOL_CLIENT_ID,
            "exp": 9999999999,
            "token_use": "access",  # WRONG — must be 'id'
        }
        with pytest.raises(stream_mod.jwt.InvalidTokenError):
            stream_mod._verify_id_token("dummy.jwt.token")


def test_verify_id_token_accepts_id_token(stream_mod):
    fake_signing_key = MagicMock()
    fake_signing_key.key = "fake-key"
    stream_mod._jwks_client.get_signing_key_from_jwt = MagicMock(return_value=fake_signing_key)

    with patch.object(stream_mod.jwt, "decode") as mock_decode:
        mock_decode.return_value = {
            "sub": "user-1",
            "iss": stream_mod._ISS,
            "aud": stream_mod.USER_POOL_CLIENT_ID,
            "exp": 9999999999,
            "token_use": "id",
        }
        claims = stream_mod._verify_id_token("dummy.jwt.token")
    assert claims["sub"] == "user-1"


def test_extract_bearer_missing_header(stream_mod):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        stream_mod._extract_bearer(None)
    assert ei.value.status_code == 401


def test_extract_bearer_malformed_header(stream_mod):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        stream_mod._extract_bearer("Token abc")
    assert ei.value.status_code == 401


def test_extract_bearer_happy(stream_mod):
    assert stream_mod._extract_bearer("Bearer xyz.token.value") == "xyz.token.value"


def test_compute_confidence_clamp(stream_mod):
    assert stream_mod._compute_confidence([]) == 0.0
    assert 0.0 <= stream_mod._compute_confidence([0.9, 0.8, 0.7]) <= 1.0


def test_iter_claude_stream_yields_only_deltas(stream_mod, monkeypatch):
    """Non-delta event types (message_start, content_block_stop, …) should be
    silently skipped by _iter_claude_stream. The botocore Stubber can't be used
    here because it validates the response body as a dict (EventStream shape);
    we mock the client method directly instead.
    """
    body = [
        {"chunk": {"bytes": json.dumps({"type": "message_start", "message": {}}).encode()}},
        _claude_stream_event("Hello"),
        _claude_stream_event(" world"),
        {"chunk": {"bytes": json.dumps({"type": "content_block_stop", "index": 0}).encode()}},
        {"chunk": {"bytes": json.dumps({"type": "message_stop"}).encode()}},
    ]
    monkeypatch.setattr(
        stream_mod._runtime,
        "invoke_model_with_response_stream",
        lambda **kwargs: {"body": iter(body), "contentType": "application/json"},
    )

    async def _drive():
        collected = []
        async for delta in stream_mod._iter_claude_stream("ignored prompt"):
            collected.append(delta)
        return collected

    import asyncio
    assert asyncio.run(_drive()) == ["Hello", " world"]


def test_iter_claude_stream_tolerates_missing_chunk(stream_mod, monkeypatch):
    """Error-event frames (no `chunk` key) must be skipped, not raise."""
    body = [
        {"modelStreamErrorException": {}},  # no `chunk` key
        _claude_stream_event("ok"),
    ]
    monkeypatch.setattr(
        stream_mod._runtime,
        "invoke_model_with_response_stream",
        lambda **kwargs: {"body": iter(body), "contentType": "application/json"},
    )

    async def _drive():
        collected = []
        async for d in stream_mod._iter_claude_stream("p"):
            collected.append(d)
        return collected

    import asyncio
    assert asyncio.run(_drive()) == ["ok"]
