import json
import os
import time
import uuid

import boto3
import structlog
from botocore.exceptions import ClientError
from pydantic import ValidationError

import conversations as conversations_handler
from schemas import ErrorEnvelope, QueryRequest, QueryResponse

AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]
MEMORY_ID = os.environ["MEMORY_ID"]
CONVERSATIONS_TABLE = os.environ["CONVERSATIONS_TABLE"]
_REGION = os.environ.get("AWS_REGION", "us-east-1")

_agentcore = boto3.client("bedrock-agentcore", region_name=_REGION)

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
_logger = structlog.get_logger()


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "http://localhost:8501",
        },
        "body": json.dumps(body),
    }


def _route(event: dict) -> tuple[str, str]:
    return event.get("httpMethod", ""), event.get("resource") or event.get("path") or ""


def _actor_id(event: dict) -> str | None:
    ctx = event.get("requestContext") or {}
    auth = ctx.get("authorizer") or {}
    # REST API Cognito authorizer puts claims at requestContext.authorizer.claims.
    claims = auth.get("claims") or (auth.get("jwt") or {}).get("claims") or {}
    return claims.get("sub")


def _pad_session_id(session_id: str) -> str:
    # AgentCore InvokeAgentRuntime requires runtimeSessionId length >= 33.
    if len(session_id) >= 33:
        return session_id
    return f"sess-{session_id}".ljust(33, "0")


def handler(event, context):
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    log = _logger.bind(request_id=request_id)

    method, path = _route(event)
    log.info("request.received", method=method, path=path)

    if method == "GET" and path.endswith("/health"):
        return _response(200, {"status": "ok"})

    actor_id = _actor_id(event)

    if method == "POST" and path.endswith("/query"):
        return _handle_query(event, actor_id, request_id, start, log)

    if method == "GET" and path.endswith("/conversations"):
        return _handle_list_conversations(actor_id, request_id, log)

    if method == "GET" and "/conversations/" in path:
        session_id = (event.get("pathParameters") or {}).get("session_id", "")
        return _handle_get_conversation(actor_id, session_id, request_id, log)

    envelope = ErrorEnvelope(
        error="InvalidRequest",
        message=f"Unsupported route {method} {path}",
        request_id=request_id,
    )
    return _response(400, envelope.model_dump())


def _handle_query(event, actor_id, request_id, start, log):
    try:
        raw = event.get("body") or "{}"
        payload = json.loads(raw) if isinstance(raw, str) else raw
        req = QueryRequest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("request.invalid", error=str(exc))
        envelope = ErrorEnvelope(
            error="InvalidRequest", message=str(exc), request_id=request_id
        )
        return _response(400, envelope.model_dump())

    session_id = req.session_id or str(uuid.uuid4())
    runtime_session_id = _pad_session_id(session_id)

    invoke_payload = {
        "prompt": req.question,
        "session_id": session_id,
        "actor_id": actor_id or "anonymous",
        "top_k": req.top_k,
    }

    try:
        resp = _agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            runtimeSessionId=runtime_session_id,
            qualifier="DEFAULT",
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(invoke_payload).encode("utf-8"),
        )
        body_bytes = resp["response"].read()
        agent_result = json.loads(body_bytes)
        agent_result.setdefault("session_id", session_id)

        # Ensure latency_ms reflects the API edge, not just the agent's internal measure.
        if "metadata" in agent_result and isinstance(agent_result["metadata"], dict):
            agent_result["metadata"]["request_id"] = request_id
            agent_result["metadata"]["latency_ms"] = int(
                (time.perf_counter() - start) * 1000
            )

        response = QueryResponse.model_validate(agent_result)
        log.info(
            "request.success",
            latency_ms=response.metadata.latency_ms,
            sources=len(response.sources),
            session_id=session_id,
        )
        return _response(200, response.model_dump())
    except ValidationError as exc:
        log.exception("agent.invalid_response", error=str(exc))
        envelope = ErrorEnvelope(
            error="Internal",
            message="Agent returned an unexpected response shape.",
            request_id=request_id,
        )
        return _response(500, envelope.model_dump())
    except ClientError:
        log.exception("agentcore.client_error")
        envelope = ErrorEnvelope(
            error="Internal", message="Upstream service error.", request_id=request_id
        )
        return _response(500, envelope.model_dump())
    except Exception:
        log.exception("handler.unhandled")
        envelope = ErrorEnvelope(
            error="Internal", message="Internal server error.", request_id=request_id
        )
        return _response(500, envelope.model_dump())


def _handle_list_conversations(actor_id, request_id, log):
    if not actor_id:
        envelope = ErrorEnvelope(
            error="Unauthorized", message="Missing actor identity.", request_id=request_id
        )
        return _response(401, envelope.model_dump())
    try:
        items = conversations_handler.list_conversations(actor_id, CONVERSATIONS_TABLE)
        log.info("conversations.list", count=len(items))
        return _response(200, {"conversations": items})
    except ClientError:
        log.exception("conversations.list.client_error")
        envelope = ErrorEnvelope(
            error="Internal", message="Upstream service error.", request_id=request_id
        )
        return _response(500, envelope.model_dump())


def _handle_get_conversation(actor_id, session_id, request_id, log):
    if not actor_id:
        envelope = ErrorEnvelope(
            error="Unauthorized", message="Missing actor identity.", request_id=request_id
        )
        return _response(401, envelope.model_dump())
    if not session_id:
        envelope = ErrorEnvelope(
            error="InvalidRequest", message="Missing session_id.", request_id=request_id
        )
        return _response(400, envelope.model_dump())
    try:
        messages = conversations_handler.get_conversation(
            actor_id, session_id, MEMORY_ID
        )
        log.info("conversations.get", count=len(messages), session_id=session_id)
        return _response(200, {"session_id": session_id, "messages": messages})
    except ClientError:
        log.exception("conversations.get.client_error")
        envelope = ErrorEnvelope(
            error="Internal", message="Upstream service error.", request_id=request_id
        )
        return _response(500, envelope.model_dump())
