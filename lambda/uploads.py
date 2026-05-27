"""Phase 9b — handlers for /documents, /ingest, /ingest/{job_id}.

Upload flow:
  1. POST /documents { filename, content_type }
     -> mints an S3 key under `uploads/{yyyy-mm-dd}/{uuid4}-{sanitized-name}`
     -> returns a presigned PUT URL (15 min) signed with ContentType
     The client MUST send the same Content-Type header on the subsequent PUT
     or S3 rejects with SignatureDoesNotMatch (verified via boto3 docs).
  2. PUT (client -> S3) — no Lambda involvement.
  3. POST /ingest { key, client_token? }
     -> validates the key is one we issued (prefix check)
     -> calls bedrock-agent.StartIngestionJob with clientToken for idempotency
     -> returns { job_id, status }
  4. GET /ingest/{job_id}
     -> bedrock-agent.GetIngestionJob -> { job_id, status, statistics, failure_reasons? }

Shared corpus: all authenticated users see all uploads. Per-user namespacing is
a documented hardening note for the README, not a code path here.

Bedrock KB supported file types (verified via AWS docs):
  .txt .md .html .pdf .doc .docx .csv .xls .xlsx
Per-doc size limit: 50 MB (client-enforced; presigned URLs cannot enforce this
on S3 — surfacing it in the API contract is the practical hardening).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from pydantic import ValidationError

from schemas import (
    ErrorEnvelope,
    IngestionStatistics,
    IngestionStatusResponse,
    IngestRequest,
    IngestResponse,
    UploadRequest,
    UploadResponse,
)

_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Bedrock KB-supported file types (verified May 2026 against
# docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-ds.html).
# Keeping JPEG/PNG out for now — KB does support them, but the chunking
# strategy needs the multimodal-embeddings path which is a separate concern.
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "text/plain": {".txt"},
    "text/markdown": {".md"},
    "text/html": {".html", ".htm"},
    "text/csv": {".csv"},
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/vnd.ms-excel": {".xls"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
}

# Sanitize filename: keep [A-Za-z0-9._-], lowercase, truncate to 100 chars.
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_KEY_PREFIX = "uploads/"
_PRESIGN_TTL_SECONDS = 15 * 60  # 15 minutes
_MAX_FILENAME_BYTES = 100

# SigV4 explicitly — presigned URLs default to SigV2 in some legacy regions,
# which doesn't sign the ContentType header reliably.
_s3 = boto3.client(
    "s3",
    region_name=_REGION,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
_bedrock_agent = boto3.client("bedrock-agent", region_name=_REGION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "http://localhost:8501",
        },
        "body": json.dumps(body),
    }


def _error(status: int, error: str, message: str, request_id: str) -> dict:
    env = ErrorEnvelope(error=error, message=message, request_id=request_id)
    return _response(status, env.model_dump())


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name).strip()
    if not base:
        return ""
    sanitized = _SANITIZE_RE.sub("-", base).strip("-._").lower()
    if len(sanitized) > _MAX_FILENAME_BYTES:
        # Preserve extension when truncating.
        root, ext = os.path.splitext(sanitized)
        if ext and len(ext) <= 10:
            root = root[: _MAX_FILENAME_BYTES - len(ext)]
            sanitized = root + ext
        else:
            sanitized = sanitized[:_MAX_FILENAME_BYTES]
    return sanitized


def _validate_extension(filename: str, content_type: str) -> tuple[bool, str]:
    ext = os.path.splitext(filename.lower())[1]
    allowed_for_ct = ALLOWED_EXTENSIONS.get(content_type)
    if allowed_for_ct is None:
        return False, (
            f"Unsupported content_type '{content_type}'. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS.keys())}"
        )
    if ext not in allowed_for_ct:
        return False, (
            f"Extension '{ext}' does not match content_type '{content_type}'. "
            f"Expected one of: {sorted(allowed_for_ct)}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# POST /documents — mint upload URL
# ---------------------------------------------------------------------------

def handle_create_document_upload(
    event: dict, claims: dict | None, request_id: str, log, docs_bucket: str
) -> dict:
    if not claims or not claims.get("sub"):
        return _error(401, "Unauthorized", "Missing actor identity.", request_id)

    try:
        raw = event.get("body") or "{}"
        payload = json.loads(raw) if isinstance(raw, str) else raw
        req = UploadRequest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("uploads.invalid_request", error=str(exc))
        return _error(400, "InvalidRequest", str(exc), request_id)

    sanitized = _sanitize_filename(req.filename)
    if not sanitized:
        return _error(
            400,
            "InvalidRequest",
            "filename sanitizes to empty (only [A-Za-z0-9._-] is preserved).",
            request_id,
        )

    ok, msg = _validate_extension(sanitized, req.content_type)
    if not ok:
        log.warning("uploads.bad_type", filename=sanitized, content_type=req.content_type)
        return _error(400, "InvalidRequest", msg, request_id)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    key = f"{_KEY_PREFIX}{today}/{uuid.uuid4().hex}-{sanitized}"

    try:
        url = _s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": docs_bucket,
                "Key": key,
                "ContentType": req.content_type,
            },
            ExpiresIn=_PRESIGN_TTL_SECONDS,
            HttpMethod="PUT",
        )
    except ClientError:
        log.exception("uploads.presign_failed")
        return _error(500, "Internal", "Failed to mint upload URL.", request_id)

    resp = UploadResponse(
        upload_url=url,
        key=key,
        content_type=req.content_type,
        expires_in=_PRESIGN_TTL_SECONDS,
    )
    log.info(
        "uploads.minted",
        key=key,
        content_type=req.content_type,
        actor_id=claims.get("sub"),
    )
    return _response(200, resp.model_dump())


# ---------------------------------------------------------------------------
# POST /ingest — start ingestion job
# ---------------------------------------------------------------------------

def handle_start_ingestion(
    event: dict,
    claims: dict | None,
    request_id: str,
    log,
    knowledge_base_id: str,
    data_source_id: str,
) -> dict:
    if not claims or not claims.get("sub"):
        return _error(401, "Unauthorized", "Missing actor identity.", request_id)

    try:
        raw = event.get("body") or "{}"
        payload = json.loads(raw) if isinstance(raw, str) else raw
        req = IngestRequest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("ingest.invalid_request", error=str(exc))
        return _error(400, "InvalidRequest", str(exc), request_id)

    if not req.key.startswith(_KEY_PREFIX):
        return _error(
            400,
            "InvalidRequest",
            f"key must start with '{_KEY_PREFIX}'.",
            request_id,
        )

    # clientToken pattern: ^[a-zA-Z0-9](-*[a-zA-Z0-9]){0,256}$, length 33-256.
    # uuid4 hex is 32 chars; prefix with "rag-" to satisfy the 33 min length.
    client_token = req.client_token or f"rag-{uuid.uuid4().hex}"

    try:
        resp = _bedrock_agent.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            clientToken=client_token,
            description=f"upload {req.key} by {claims.get('sub')}",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        log.exception("ingest.client_error", aws_error=code)
        # ConflictException with the same clientToken means the job already
        # ran; treat as success but the caller should re-poll Get.
        if code == "ConflictException":
            return _error(
                409,
                "InvalidRequest",
                "Ingestion already in flight for this client_token.",
                request_id,
            )
        return _error(500, "Internal", "Failed to start ingestion job.", request_id)

    job = resp.get("ingestionJob") or {}
    job_id = job.get("ingestionJobId", "")
    status = job.get("status", "STARTING")
    log.info(
        "ingest.started",
        job_id=job_id,
        status=status,
        key=req.key,
        actor_id=claims.get("sub"),
    )
    return _response(200, IngestResponse(job_id=job_id, status=status).model_dump())


# ---------------------------------------------------------------------------
# GET /ingest/{job_id} — poll ingestion status
# ---------------------------------------------------------------------------

# bedrock-agent IDs are exactly 10 chars of [0-9a-zA-Z]; pre-validate before the
# AWS call so malformed paths short-circuit to 400 rather than 500.
_JOB_ID_RE = re.compile(r"^[0-9a-zA-Z]{10}$")


def handle_get_ingestion_status(
    path_params: dict | None,
    claims: dict | None,
    request_id: str,
    log,
    knowledge_base_id: str,
    data_source_id: str,
) -> dict:
    if not claims or not claims.get("sub"):
        return _error(401, "Unauthorized", "Missing actor identity.", request_id)

    job_id = (path_params or {}).get("job_id", "")
    if not _JOB_ID_RE.match(job_id):
        return _error(
            400,
            "InvalidRequest",
            "job_id must be exactly 10 alphanumeric characters.",
            request_id,
        )

    try:
        resp = _bedrock_agent.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        log.exception("ingest.get.client_error", aws_error=code)
        if code == "ResourceNotFoundException":
            return _error(404, "InvalidRequest", "Ingestion job not found.", request_id)
        return _error(500, "Internal", "Failed to fetch ingestion job.", request_id)

    job = resp.get("ingestionJob") or {}
    raw_stats = job.get("statistics") or {}
    stats = IngestionStatistics(
        scanned=int(raw_stats.get("numberOfDocumentsScanned", 0) or 0),
        indexed=int(
            (raw_stats.get("numberOfNewDocumentsIndexed", 0) or 0)
            + (raw_stats.get("numberOfModifiedDocumentsIndexed", 0) or 0)
        ),
        failed=int(raw_stats.get("numberOfDocumentsFailed", 0) or 0),
        deleted=int(raw_stats.get("numberOfDocumentsDeleted", 0) or 0),
        modified=int(raw_stats.get("numberOfModifiedDocumentsIndexed", 0) or 0),
    )
    failure_reasons = job.get("failureReasons") or None

    out = IngestionStatusResponse(
        job_id=job.get("ingestionJobId", job_id),
        status=job.get("status", "UNKNOWN"),
        statistics=stats,
        failure_reasons=failure_reasons,
    )
    log.info("ingest.status", job_id=out.job_id, status=out.status)
    return _response(200, out.model_dump())
