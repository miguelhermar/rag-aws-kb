import json
import os
import time
import uuid

import structlog
from botocore.exceptions import ClientError
from pydantic import ValidationError

import rag
from schemas import ErrorEnvelope, QueryRequest, QueryResponse

KB_ID = os.environ["KB_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]
MODEL_ID_FOR_METADATA = os.environ.get(
    "MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

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
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _route(event: dict) -> tuple[str, str]:
    method = event.get("httpMethod", "")
    path = event.get("resource") or event.get("path") or ""
    return method, path


def handler(event, context):
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    log = _logger.bind(request_id=request_id)

    method, path = _route(event)
    log.info("request.received", method=method, path=path)

    if method == "GET" and path.endswith("/health"):
        return _response(200, {"status": "ok"})

    if not (method == "POST" and path.endswith("/query")):
        envelope = ErrorEnvelope(
            error="InvalidRequest",
            message=f"Unsupported route {method} {path}",
            request_id=request_id,
        )
        return _response(400, envelope.model_dump())

    try:
        raw = event.get("body") or "{}"
        payload = json.loads(raw) if isinstance(raw, str) else raw
        req = QueryRequest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("request.invalid", error=str(exc))
        envelope = ErrorEnvelope(
            error="InvalidRequest",
            message=str(exc),
            request_id=request_id,
        )
        return _response(400, envelope.model_dump())

    def latency_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        result = rag.run_query(
            question=req.question,
            top_k=req.top_k,
            kb_id=KB_ID,
            model_arn=MODEL_ARN,
            request_id=request_id,
            model_id_for_metadata=MODEL_ID_FOR_METADATA,
            latency_fn=latency_ms,
        )
        response = QueryResponse.model_validate(result)
        log.info(
            "request.success",
            latency_ms=response.metadata.latency_ms,
            sources=len(response.sources),
        )
        return _response(200, response.model_dump())
    except ClientError:
        log.exception("bedrock.client_error")
        envelope = ErrorEnvelope(
            error="Internal",
            message="Upstream service error.",
            request_id=request_id,
        )
        return _response(500, envelope.model_dump())
    except Exception:
        log.exception("handler.unhandled")
        envelope = ErrorEnvelope(
            error="Internal",
            message="Internal server error.",
            request_id=request_id,
        )
        return _response(500, envelope.model_dump())
