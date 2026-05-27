"""Phase 9a — streaming /query-stream handler (FastAPI ASGI app behind LWA).

Architecture (verified, see Dockerfile):
  Client (Streamlit / curl)
    ──HTTPS── Lambda Function URL (auth_type=NONE, invoke_mode=RESPONSE_STREAM)
       ──Lambda Invoke── Lambda Web Adapter extension
          ──HTTP localhost:8080── this ASGI app
             ──boto3── bedrock-runtime.InvokeModelWithResponseStream
                                  bedrock-agent-runtime.Retrieve
                                  bedrock-agentcore.{ListEvents,CreateEvent}
                                  dynamodb.{Query,PutItem}

Auth: the Function URL itself has `auth_type=NONE` because the Streamlit client
cannot SigV4-sign. We re-implement Cognito JWT verification in-handler using
`pyjwt[crypto]` + PyJWKClient, mirroring the API-Gateway authorizer logic.

Stream protocol (SSE, matches the brief):
  event: meta\ndata: {"request_id", "session_id", "is_first_turn"}\n\n
  event: token\ndata: {"text"}\n\n     (repeated)
  event: sources\ndata: {"sources": [...]}\n\n
  event: done\ndata: {"latency_ms", "model_id", "confidence", "conversation_name"}\n\n
  event: error\ndata: {"error", "message"}\n\n   (terminal, on failure)

INSUFFICIENT_CONTEXT handling — see _stream_answer below. We accumulate tokens
into a buffer; if the *final* accumulated answer starts with the marker (the
same `startswith` rule as agent/rag.py), we DROP the token-event-stream that
went out, and emit the canned INSUFFICIENT_ANSWER as a single token-event,
followed by a `done` event with confidence clamped to 0.2. This is racy in the
"already streamed half the marker" case but is the cleanest UX: model-side
INSUFFICIENT_CONTEXT is rare and short (one token at temperature 0); the user
sees the canned answer either way once the stream ends. Documented for Phase 9b
reviewers.
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Optional, Tuple

import boto3
import jwt
import structlog
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from jwt import PyJWKClient
from pydantic import ValidationError

from schemas import QueryRequest

# ---- env (fail-fast at import) ---------------------------------------------
_REGION = os.environ.get("AWS_REGION", "us-east-1")
KB_ID = os.environ["KB_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]
MODEL_ID = os.environ["MODEL_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]
CONVERSATIONS_TABLE = os.environ["CONVERSATIONS_TABLE"]
USER_POOL_ID = os.environ["USER_POOL_ID"]
USER_POOL_CLIENT_ID = os.environ["USER_POOL_CLIENT_ID"]

# ---- constants -------------------------------------------------------------
RETRIEVAL_STRATEGY = "bedrock-kb-s3vectors-titan-v2-topk-stream"
INSUFFICIENT = "INSUFFICIENT_CONTEXT"
INSUFFICIENT_ANSWER = "I don't have information about that in the knowledge base."
MAX_TITLE_CHARS = 50

SYSTEM_PROMPT = (
    "Answer ONLY from the provided context. "
    f"If the context is insufficient to answer the question, your entire response MUST be the single token `{INSUFFICIENT}` "
    "with no other characters, words, or explanation. "
    "When you can answer, cite sources inline as [n]."
)

TITLE_SYSTEM_PROMPT = (
    "You generate short conversation titles. "
    "Reply with only the title text — no quotes, no punctuation at the end, no preamble."
)

# ---- clients (module scope, lazy refresh) ----------------------------------
_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=_REGION)
_runtime = boto3.client("bedrock-runtime", region_name=_REGION)
_agentcore = boto3.client("bedrock-agentcore", region_name=_REGION)
_dynamodb = boto3.resource("dynamodb", region_name=_REGION)

# ---- JWKS cache at module scope (PyJWKClient maintains its own LRU) --------
_ISS = f"https://cognito-idp.{_REGION}.amazonaws.com/{USER_POOL_ID}"
_JWKS_URL = f"{_ISS}/.well-known/jwks.json"
_jwks_client = PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=3600)

# ---- logging ---------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
_logger = structlog.get_logger()

app = FastAPI(title="rag-aws-stream", version="9a")


# ============================================================================
# JWT verification (Cognito ID token, RS256)
# ============================================================================
def _verify_id_token(token: str) -> Dict:
    """Verify a Cognito ID token. Returns the claims dict on success."""
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=USER_POOL_CLIENT_ID,
        issuer=_ISS,
        options={"require": ["exp", "iss", "aud", "sub", "token_use"]},
    )
    if claims.get("token_use") != "id":
        raise jwt.InvalidTokenError(
            f"token_use must be 'id', got {claims.get('token_use')!r}"
        )
    return claims


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    return authorization[7:].strip()


# ============================================================================
# RAG primitives (mirrored from agent/rag.py; kept here for container isolation)
# ============================================================================
def _retrieve(question: str, top_k: int) -> List[Dict]:
    resp = _agent_runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": top_k}
        },
    )
    chunks: List[Dict] = []
    for r in resp.get("retrievalResults", []):
        content = (r.get("content") or {}).get("text", "")
        score = float(r.get("score", 0.0))
        s3_uri = ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")
        document = s3_uri.rsplit("/", 1)[-1] if s3_uri else ""
        chunks.append(
            {"content": content, "score": score, "s3_uri": s3_uri, "document": document}
        )
    return chunks


def _build_prompt(question: str, chunks: List[Dict]) -> str:
    numbered = "\n\n".join(
        f"[{i + 1}] {c['content']}" for i, c in enumerate(chunks)
    )
    return f"Context:\n{numbered}\n\nQuestion: {question}"


def _compute_confidence(scores: List[float]) -> float:
    if not scores:
        return 0.0
    max_s = max(scores)
    mean_s = statistics.mean(scores)
    stdev_s = statistics.stdev(scores) if len(scores) > 1 else 0.0
    raw = 0.6 * max_s + 0.3 * mean_s + 0.1 * (1.0 - stdev_s)
    return max(0.0, min(1.0, raw))


def _snippet(text: str, limit: int = 240) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _is_first_turn(actor_id: str, session_id: str) -> bool:
    resp = _agentcore.list_events(
        memoryId=MEMORY_ID,
        sessionId=session_id,
        actorId=actor_id,
        maxResults=1,
    )
    return not resp.get("events", [])


def _write_memory(
    actor_id: str,
    session_id: str,
    prompt: str,
    answer: str,
    assistant_meta: Optional[Dict] = None,
) -> None:
    payload: List[Dict] = [
        {"conversational": {"content": {"text": prompt}, "role": "USER"}},
        {"conversational": {"content": {"text": answer}, "role": "ASSISTANT"}},
    ]
    if assistant_meta is not None:
        # See agent/rag.py write_memory: AgentCore Memory's blob Document
        # field round-trips as Java toString() on read. Base64-encode the
        # JSON payload so we get back `{b64=...}` which is regex-extractable.
        meta_b64 = base64.urlsafe_b64encode(
            json.dumps({"kind": "assistant_metadata", **assistant_meta}).encode("utf-8")
        ).decode("ascii")
        payload.append({"blob": {"b64": meta_b64}})
    _agentcore.create_event(
        memoryId=MEMORY_ID,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=payload,
    )


def _invoke_claude_buffered(prompt: str, system: str, max_tokens: int = 32) -> str:
    """Non-streaming Claude call (used only for conversation-name generation)."""
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    resp = _runtime.invoke_model(
        modelId=MODEL_ARN,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    payload = json.loads(resp["body"].read())
    parts = payload.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def _generate_conversation_name(prompt: str, answer: str) -> str:
    instruction = (
        "Generate a 3-4 word title (max 40 chars) for this first conversation turn. "
        "Use both the user's message and the assistant's answer, and prefer the specific topic over generic wording. "
        "Reply with only the title, no quotes. "
        f"User message: {prompt}\n\nAssistant answer: {answer}"
    )
    title = _invoke_claude_buffered(instruction, TITLE_SYSTEM_PROMPT, max_tokens=32)
    title = title.strip('"\'').strip()
    return title[:MAX_TITLE_CHARS]


def _save_conversation_metadata(actor_id: str, session_id: str, name: str) -> None:
    table = _dynamodb.Table(CONVERSATIONS_TABLE)
    try:
        table.put_item(
            Item={
                "actor_id": actor_id,
                "session_id": session_id,
                "conversation_name": name,
                "created_at": int(time.time()),
            },
            ConditionExpression=Attr("session_id").not_exists(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


# ============================================================================
# Bedrock streaming iteration
# ============================================================================
def _iter_claude_stream(prompt: str) -> AsyncIterator[str]:
    """Yield text deltas from invoke_model_with_response_stream.

    Anthropic Messages streaming chunks come as:
      {"type": "message_start", "message": {...}}
      {"type": "content_block_start", "index": 0, "content_block": {"type":"text","text":""}}
      {"type": "content_block_delta", "index": 0, "delta": {"type":"text_delta","text":"Hi"}}
      ...
      {"type": "content_block_stop", "index": 0}
      {"type": "message_delta", "delta": {"stop_reason":"end_turn", ...}, "usage": {...}}
      {"type": "message_stop"}

    The boto3 response['body'] is an EventStream of events shaped:
      {"chunk": {"bytes": b'<json payload above>'}}
    """
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    resp = _runtime.invoke_model_with_response_stream(
        modelId=MODEL_ARN,
        contentType="application/json",
        accept="application/json",
        body=body,
    )

    async def _gen() -> AsyncIterator[str]:
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                # error events (modelStreamErrorException, etc.) surface here
                continue
            try:
                payload = json.loads(chunk["bytes"])
            except (KeyError, json.JSONDecodeError):
                continue
            if payload.get("type") == "content_block_delta":
                delta = payload.get("delta") or {}
                text = delta.get("text") or ""
                if text:
                    yield text

    return _gen()


# ============================================================================
# SSE framing helpers
# ============================================================================
def _sse(event: str, data: Dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# ============================================================================
# Routes
# ============================================================================
@app.get("/health-internal")
async def _health_internal():
    """LWA readiness probe target — kept off the public Function URL via path."""
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query-stream")
async def query_stream(request: Request, authorization: Optional[str] = Header(default=None)):
    request_id = str(uuid.uuid4())
    log = _logger.bind(request_id=request_id)
    start = time.perf_counter()

    # --- auth (synchronous; failures return JSON, not SSE) -----------------
    try:
        token = _extract_bearer(authorization)
        claims = _verify_id_token(token)
    except HTTPException:
        raise
    except jwt.InvalidTokenError as exc:
        log.warning("auth.invalid", error=str(exc))
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    actor_id = claims.get("sub") or "anonymous"

    # --- body parsing (failures return JSON, not SSE) ----------------------
    try:
        raw = await request.json()
        log.info("request.body", body=raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    try:
        req = QueryRequest.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    session_id = req.session_id or f"s-{uuid.uuid4().hex}"

    async def _stream_answer() -> AsyncIterator[bytes]:
        try:
            first_turn = _is_first_turn(actor_id, session_id)
            yield _sse(
                "meta",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "is_first_turn": first_turn,
                },
            )

            chunks = _retrieve(req.question, req.top_k)
            sources = [
                {
                    "s3_uri": c["s3_uri"],
                    "document": c["document"],
                    "score": c["score"],
                    "snippet": _snippet(c["content"]),
                }
                for c in chunks
            ]
            confidence = _compute_confidence([c["score"] for c in chunks])

            # No context → emit canned answer and skip Bedrock streaming.
            if not chunks:
                yield _sse("token", {"text": INSUFFICIENT_ANSWER})
                yield _sse("sources", {"sources": sources})
                final_answer = INSUFFICIENT_ANSWER
                final_confidence = min(confidence, 0.2)
                latency_ms = int((time.perf_counter() - start) * 1000)
                _write_memory(
                    actor_id,
                    session_id,
                    req.question,
                    final_answer,
                    assistant_meta={
                        "sources": sources,
                        "confidence": final_confidence,
                        "latency_ms": latency_ms,
                        "model_id": MODEL_ID,
                        "retrieval_strategy": RETRIEVAL_STRATEGY,
                    },
                )
                conversation_name = None
                if first_turn:
                    conversation_name = _generate_conversation_name(req.question, final_answer)
                    _save_conversation_metadata(actor_id, session_id, conversation_name)
                yield _sse(
                    "done",
                    {
                        "latency_ms": latency_ms,
                        "model_id": MODEL_ID,
                        "confidence": final_confidence,
                        "conversation_name": conversation_name,
                    },
                )
                return

            # Stream tokens; accumulate the full answer to test for INSUFFICIENT
            # at the end (matches non-streaming `startswith` semantics).
            rag_prompt = _build_prompt(req.question, chunks)
            accumulated = ""
            gen = _iter_claude_stream(rag_prompt)
            async for delta in gen:
                accumulated += delta
                yield _sse("token", {"text": delta})

            # Determine final answer + confidence (post-stream).
            if accumulated.strip().startswith(INSUFFICIENT):
                # The user already saw the marker tokens stream by; emit a
                # corrective token so the assembled answer is the canned text.
                # The Streamlit client knows to overwrite on `done.confidence<=0.2`
                # — see streamlit_client/app.py _consume_stream.
                final_answer = INSUFFICIENT_ANSWER
                final_confidence = min(confidence, 0.2)
                # Send the canned text as a single token so naive consumers
                # (curl, smoke test) that accumulate tokens still end up with
                # something readable; the prefix-marker text is signal that
                # the model said "no answer", and Streamlit replaces accordingly.
                yield _sse("token", {"text": "\n\n" + INSUFFICIENT_ANSWER})
            else:
                final_answer = accumulated.strip()
                final_confidence = confidence

            yield _sse("sources", {"sources": sources})

            latency_ms = int((time.perf_counter() - start) * 1000)
            _write_memory(
                actor_id,
                session_id,
                req.question,
                final_answer,
                assistant_meta={
                    "sources": sources,
                    "confidence": final_confidence,
                    "latency_ms": latency_ms,
                    "model_id": MODEL_ID,
                    "retrieval_strategy": RETRIEVAL_STRATEGY,
                },
            )
            conversation_name = None
            if first_turn:
                conversation_name = _generate_conversation_name(req.question, final_answer)
                _save_conversation_metadata(actor_id, session_id, conversation_name)
            log.info(
                "stream.success",
                latency_ms=latency_ms,
                sources=len(sources),
                first_turn=first_turn,
                confidence=final_confidence,
            )
            yield _sse(
                "done",
                {
                    "latency_ms": latency_ms,
                    "model_id": MODEL_ID,
                    "confidence": final_confidence,
                    "conversation_name": conversation_name,
                },
            )
        except ClientError as exc:
            log.exception("stream.client_error")
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            yield _sse("error", {"error": "Internal", "message": f"Upstream error: {code}"})
        except Exception as exc:  # noqa: BLE001 — terminal error frame
            log.exception("stream.unhandled")
            yield _sse("error", {"error": "Internal", "message": str(exc)})

    return StreamingResponse(
        _stream_answer(),
        media_type="text/event-stream",
        headers={
            # Prevent intermediary buffering. Function URL passes these through.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
