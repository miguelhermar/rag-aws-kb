from typing import List, Literal
from pydantic import BaseModel, Field, ConfigDict


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


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


class ErrorEnvelope(BaseModel):
    error: Literal["InvalidRequest", "Unauthorized", "Internal"]
    message: str
    request_id: str
