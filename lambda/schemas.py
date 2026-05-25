from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 9b — document upload + ingestion
# ---------------------------------------------------------------------------

class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Original client filename — used only for extension validation + display.
    # Never trusted as a path component (we sanitize + uuid-prefix in the handler).
    filename: str = Field(min_length=1, max_length=255)
    # Client-asserted MIME type. Must be on the whitelist; signed into the URL,
    # so the client must echo it back exactly on PUT.
    content_type: str = Field(min_length=1, max_length=128)


class UploadResponse(BaseModel):
    upload_url: str
    key: str
    content_type: str
    expires_in: int


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The S3 object key the client just PUT to (returned from /documents).
    # Server-side we re-validate the prefix to confirm it's an upload we minted.
    key: str = Field(min_length=1, max_length=1024)
    # Optional idempotency token. If omitted the handler generates one; if
    # provided it is passed through to StartIngestionJob.clientToken so retries
    # collapse into the same job.
    client_token: Optional[str] = Field(default=None, min_length=33, max_length=256)


class IngestResponse(BaseModel):
    job_id: str
    status: str


class IngestionStatistics(BaseModel):
    # Mirrors bedrock-agent IngestionJobStatistics, renamed to snake_case.
    scanned: int = 0
    indexed: int = 0
    failed: int = 0
    deleted: int = 0
    modified: int = 0


class IngestionStatusResponse(BaseModel):
    job_id: str
    status: str
    statistics: IngestionStatistics
    failure_reasons: Optional[List[str]] = None


class Source(BaseModel):
    s3_uri: str
    document: str
    score: float
    snippet: str


class ResponseMetadata(BaseModel):
    model: str
    retrieval_strategy: str
    request_id: str
    latency_ms: int


class QueryResponse(BaseModel):
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources: List[Source]
    metadata: ResponseMetadata
    session_id: str
    conversation_name: Optional[str] = None


class ErrorEnvelope(BaseModel):
    error: Literal["InvalidRequest", "Unauthorized", "Internal"]
    message: str
    request_id: str
