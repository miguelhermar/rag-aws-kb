"""
Pydantic models for file operations.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class FileUploadResponse(BaseModel):
    """Response model for file upload."""
    file_id: str
    filename: str
    file_size: int
    file_type: str
    s3_key: str
    status: str
    message: str
    uploaded_at: datetime


class FileInfo(BaseModel):
    """File information model."""
    file_id: str
    filename: str
    file_size: int
    file_type: str
    s3_key: str
    uploaded_at: datetime
    processed: bool = False
    chunk_count: Optional[int] = None


class FileListResponse(BaseModel):
    """Response model for file list."""
    files: list[FileInfo]
    total: int


class FileDeleteResponse(BaseModel):
    """Response model for file deletion."""
    file_id: str
    filename: str
    deleted: bool
    message: str

