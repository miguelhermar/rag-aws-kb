"""Unit tests for Phase 9b — lambda/uploads.py.

Mocks:
  - s3.generate_presigned_url  (monkeypatched directly; deterministic URL)
  - bedrock-agent.start_ingestion_job / get_ingestion_job  (botocore Stubber)

Covers:
  - happy paths for all three handlers
  - error paths: missing actor, bad MIME type, extension mismatch, oversized
    filename, malformed key (no uploads/ prefix), bad job_id format,
    ClientError on start/get
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.stub import Stubber

_LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda"
if str(_LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(_LAMBDA_DIR))

import uploads  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log():
    # Production code uses structlog bound logger which accepts kwargs;
    # MagicMock swallows any signature safely.
    return MagicMock()


@pytest.fixture
def claims():
    return {"sub": "user-abc-123"}


@pytest.fixture
def event_factory():
    def _make(body: dict | None, path_params: dict | None = None) -> dict:
        return {
            "body": json.dumps(body) if body is not None else None,
            "pathParameters": path_params or {},
            "requestContext": {"authorizer": {"claims": {"sub": "user-abc-123"}}},
        }
    return _make


# ---------------------------------------------------------------------------
# POST /documents
# ---------------------------------------------------------------------------

def test_create_upload_happy(event_factory, claims, log):
    event = event_factory({"filename": "Refund-Policy.PDF", "content_type": "application/pdf"})

    fake_url = "https://test-docs-bucket.s3.amazonaws.com/uploads/x?sig=abc"
    with patch.object(uploads._s3, "generate_presigned_url", return_value=fake_url) as gen:
        resp = uploads.handle_create_document_upload(
            event, claims, "req-1", log, "test-docs-bucket"
        )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["upload_url"] == fake_url
    assert body["content_type"] == "application/pdf"
    assert body["expires_in"] == 15 * 60
    assert body["key"].startswith("uploads/")
    assert body["key"].endswith("-refund-policy.pdf"), body["key"]

    # Confirm presign was called with ContentType signed in.
    params = gen.call_args.kwargs["Params"]
    assert params["ContentType"] == "application/pdf"
    assert params["Bucket"] == "test-docs-bucket"
    assert params["Key"] == body["key"]


def test_create_upload_missing_actor(event_factory, log):
    event = event_factory({"filename": "x.pdf", "content_type": "application/pdf"})
    resp = uploads.handle_create_document_upload(event, None, "req-1", log, "b")
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"] == "Unauthorized"


def test_create_upload_bad_content_type(event_factory, claims, log):
    event = event_factory({"filename": "x.exe", "content_type": "application/x-msdownload"})
    resp = uploads.handle_create_document_upload(
        event, claims, "req-1", log, "test-docs-bucket"
    )
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "InvalidRequest"
    assert "Unsupported content_type" in body["message"]


def test_create_upload_extension_mismatch(event_factory, claims, log):
    # MIME claims PDF but extension is .txt
    event = event_factory({"filename": "notes.txt", "content_type": "application/pdf"})
    resp = uploads.handle_create_document_upload(
        event, claims, "req-1", log, "test-docs-bucket"
    )
    assert resp["statusCode"] == 400
    assert "does not match" in json.loads(resp["body"])["message"]


def test_create_upload_filename_sanitization(event_factory, claims, log):
    # Adversarial filename: slashes, spaces, mixed case, unicode-ish.
    nasty = "../../etc/Pass wd? <doc> .PDF"
    event = event_factory({"filename": nasty, "content_type": "application/pdf"})
    with patch.object(uploads._s3, "generate_presigned_url", return_value="https://x"):
        resp = uploads.handle_create_document_upload(
            event, claims, "req-1", log, "test-docs-bucket"
        )
    body = json.loads(resp["body"])
    # No slashes anywhere after `uploads/{date}/{uuid}-`.
    suffix = body["key"].split("/")[-1]
    # First segment is the uuid4 hex (32 chars) then a dash, then sanitized name.
    sanitized = suffix.split("-", 1)[1]
    assert "/" not in sanitized
    assert " " not in sanitized
    assert "?" not in sanitized
    assert sanitized.endswith(".pdf")
    assert sanitized.islower()


def test_create_upload_oversized_filename(event_factory, claims, log):
    long_name = ("a" * 300) + ".pdf"
    event = event_factory({"filename": long_name, "content_type": "application/pdf"})
    with patch.object(uploads._s3, "generate_presigned_url", return_value="https://x"):
        resp = uploads.handle_create_document_upload(
            event, claims, "req-1", log, "test-docs-bucket"
        )
    assert resp["statusCode"] == 400  # pydantic rejects filename > 255 chars


def test_create_upload_empty_after_sanitization(event_factory, claims, log):
    # Filename composed entirely of disallowed chars.
    event = event_factory({"filename": "???", "content_type": "application/pdf"})
    resp = uploads.handle_create_document_upload(
        event, claims, "req-1", log, "test-docs-bucket"
    )
    # The sanitize step keeps the .pdf extension via os.path.splitext; here
    # there's no real ext so we expect either sanitized-empty or ext-mismatch.
    assert resp["statusCode"] == 400


def test_create_upload_invalid_body(claims, log):
    event = {"body": "not-json{", "requestContext": {"authorizer": {"claims": claims}}}
    resp = uploads.handle_create_document_upload(
        event, claims, "req-1", log, "test-docs-bucket"
    )
    assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

def test_start_ingestion_happy(event_factory, claims, log):
    event = event_factory({
        "key": "uploads/2026-05-25/abc123-doc.pdf",
        "client_token": "rag-deterministic-token-for-test-abcd",
    })
    stub = Stubber(uploads._bedrock_agent)
    stub.add_response(
        "start_ingestion_job",
        {
            "ingestionJob": {
                "knowledgeBaseId": "KB12345678",
                "dataSourceId": "DS12345678",
                "ingestionJobId": "JOB1234567",
                "status": "STARTING",
                "startedAt": "2026-05-25T00:00:00Z",
                "updatedAt": "2026-05-25T00:00:00Z",
            }
        },
        expected_params={
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "clientToken": "rag-deterministic-token-for-test-abcd",
            "description": "upload uploads/2026-05-25/abc123-doc.pdf by user-abc-123",
        },
    )

    with stub:
        resp = uploads.handle_start_ingestion(
            event, claims, "req-1", log, "KB12345678", "DS12345678"
        )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["job_id"] == "JOB1234567"
    assert body["status"] == "STARTING"


def test_start_ingestion_bad_key_prefix(event_factory, claims, log):
    event = event_factory({"key": "evil/path/doc.pdf"})
    resp = uploads.handle_start_ingestion(
        event, claims, "req-1", log, "KB12345678", "DS12345678"
    )
    assert resp["statusCode"] == 400
    assert "uploads/" in json.loads(resp["body"])["message"]


def test_start_ingestion_client_error(event_factory, claims, log):
    event = event_factory({"key": "uploads/2026-05-25/abc-doc.pdf"})
    stub = Stubber(uploads._bedrock_agent)
    stub.add_client_error(
        "start_ingestion_job",
        service_error_code="ValidationException",
        service_message="bad input",
    )
    with stub:
        resp = uploads.handle_start_ingestion(
            event, claims, "req-1", log, "KB12345678", "DS12345678"
        )
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"] == "Internal"


def test_start_ingestion_conflict(event_factory, claims, log):
    event = event_factory({
        "key": "uploads/2026-05-25/abc-doc.pdf",
        "client_token": "rag-" + "a" * 32,
    })
    stub = Stubber(uploads._bedrock_agent)
    stub.add_client_error(
        "start_ingestion_job",
        service_error_code="ConflictException",
        service_message="already exists",
    )
    with stub:
        resp = uploads.handle_start_ingestion(
            event, claims, "req-1", log, "KB12345678", "DS12345678"
        )
    assert resp["statusCode"] == 409


def test_start_ingestion_missing_actor(event_factory, log):
    event = event_factory({"key": "uploads/2026-05-25/abc.pdf"})
    resp = uploads.handle_start_ingestion(event, None, "req-1", log, "KB", "DS")
    assert resp["statusCode"] == 401


# ---------------------------------------------------------------------------
# GET /ingest/{job_id}
# ---------------------------------------------------------------------------

def test_get_ingestion_status_happy(claims, log):
    stub = Stubber(uploads._bedrock_agent)
    stub.add_response(
        "get_ingestion_job",
        {
            "ingestionJob": {
                "knowledgeBaseId": "KB12345678",
                "dataSourceId": "DS12345678",
                "ingestionJobId": "JOB1234567",
                "status": "COMPLETE",
                "startedAt": "2026-05-25T00:00:00Z",
                "updatedAt": "2026-05-25T00:01:00Z",
                "statistics": {
                    "numberOfDocumentsScanned": 1,
                    "numberOfNewDocumentsIndexed": 1,
                    "numberOfModifiedDocumentsIndexed": 0,
                    "numberOfDocumentsFailed": 0,
                    "numberOfDocumentsDeleted": 0,
                    "numberOfMetadataDocumentsModified": 0,
                    "numberOfMetadataDocumentsScanned": 0,
                },
            }
        },
        expected_params={
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "ingestionJobId": "JOB1234567",
        },
    )
    with stub:
        resp = uploads.handle_get_ingestion_status(
            {"job_id": "JOB1234567"},
            claims,
            "req-1",
            log,
            "KB12345678",
            "DS12345678",
        )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "COMPLETE"
    assert body["statistics"]["scanned"] == 1
    assert body["statistics"]["indexed"] == 1
    assert body["statistics"]["failed"] == 0


def test_get_ingestion_status_failed_with_reasons(claims, log):
    stub = Stubber(uploads._bedrock_agent)
    stub.add_response(
        "get_ingestion_job",
        {
            "ingestionJob": {
                "knowledgeBaseId": "KB12345678",
                "dataSourceId": "DS12345678",
                "ingestionJobId": "JOB1234567",
                "status": "FAILED",
                "startedAt": "2026-05-25T00:00:00Z",
                "updatedAt": "2026-05-25T00:01:00Z",
                "failureReasons": ["bad file", "missing chunk"],
            }
        },
        expected_params={
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "ingestionJobId": "JOB1234567",
        },
    )
    with stub:
        resp = uploads.handle_get_ingestion_status(
            {"job_id": "JOB1234567"}, claims, "req-1", log, "KB12345678", "DS12345678"
        )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "FAILED"
    assert body["failure_reasons"] == ["bad file", "missing chunk"]


@pytest.mark.parametrize("bad", ["", "short", "JOB-12345!", "x" * 11, "JOB123456_"])
def test_get_ingestion_status_bad_job_id(bad, claims, log):
    resp = uploads.handle_get_ingestion_status(
        {"job_id": bad}, claims, "req-1", log, "KB12345678", "DS12345678"
    )
    assert resp["statusCode"] == 400


def test_get_ingestion_status_not_found(claims, log):
    stub = Stubber(uploads._bedrock_agent)
    stub.add_client_error(
        "get_ingestion_job",
        service_error_code="ResourceNotFoundException",
        service_message="no such job",
    )
    with stub:
        resp = uploads.handle_get_ingestion_status(
            {"job_id": "JOB1234567"}, claims, "req-1", log, "KB12345678", "DS12345678"
        )
    assert resp["statusCode"] == 404


def test_get_ingestion_status_missing_actor(log):
    resp = uploads.handle_get_ingestion_status(
        {"job_id": "JOB1234567"}, None, "req-1", log, "KB", "DS"
    )
    assert resp["statusCode"] == 401
