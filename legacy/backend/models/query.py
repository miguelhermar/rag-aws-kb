"""
Pydantic models for query operations.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime


class QueryRequest(BaseModel):
    """Request model for query."""
    question: str = Field(..., min_length=1, max_length=1000, description="User question")
    explain_like_10: bool = Field(default=False, description="Simplify answer for easier understanding")
    top_k: Optional[int] = Field(default=5, ge=1, le=20, description="Number of chunks to retrieve")


class SourceInfo(BaseModel):
    """Source information model."""
    source: str
    chunk_index: int
    file_path: str
    content_preview: str
    similarity_score: Optional[float] = None


class ConfidenceBreakdown(BaseModel):
    """Confidence score breakdown."""
    best_similarity: float
    avg_similarity: float
    consistency: float
    keyword_match: float
    final_score: float


class QueryResponse(BaseModel):
    """Response model for query."""
    answer: str
    sources: List[SourceInfo]
    confidence_score: float
    confidence_breakdown: Optional[ConfidenceBreakdown] = None
    similarity_scores: List[float]
    query: str
    timestamp: datetime
    explain_mode: bool

