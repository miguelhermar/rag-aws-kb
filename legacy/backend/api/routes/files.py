"""
File management endpoints.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional
from bson import ObjectId

from models.file import FileListResponse, FileInfo, FileDeleteResponse
from services.mongodb import mongodb_service
from services.s3 import s3_service

router = APIRouter()


@router.get("/", response_model=FileListResponse)
async def list_files(skip: int = 0, limit: int = 100):
    """
    List all uploaded files with pagination.
    """
    files = await mongodb_service.list_files(skip=skip, limit=limit)
    
    file_infos = []
    for file in files:
        file_infos.append(FileInfo(
            file_id=file.get("file_id", str(file.get("_id", ""))),
            filename=file.get("filename", ""),
            file_size=file.get("file_size", 0),
            file_type=file.get("file_type", ""),
            s3_key=file.get("s3_key", ""),
            uploaded_at=file.get("created_at"),
            processed=file.get("processed", False),
            chunk_count=file.get("chunk_count")
        ))
    
    return FileListResponse(files=file_infos, total=len(file_infos))


@router.get("/{file_id}", response_model=FileInfo)
async def get_file(file_id: str):
    """
    Get file information by ID.
    """
    file_data = await mongodb_service.get_file_metadata(file_id)
    
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileInfo(
        file_id=file_data.get("file_id", file_id),
        filename=file_data.get("filename", ""),
        file_size=file_data.get("file_size", 0),
        file_type=file_data.get("file_type", ""),
        s3_key=file_data.get("s3_key", ""),
        uploaded_at=file_data.get("created_at"),
        processed=file_data.get("processed", False),
        chunk_count=file_data.get("chunk_count")
    )


@router.delete("/{file_id}", response_model=FileDeleteResponse)
async def delete_file(file_id: str):
    """
    Delete a file from the system.
    
    Note: This removes metadata but does not rebuild the vectorstore.
    For production, implement vectorstore rebuilding after deletion.
    """
    file_data = await mongodb_service.get_file_metadata(file_id)
    
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    
    s3_key = file_data.get("s3_key")
    filename = file_data.get("filename", "")
    
    # Delete from S3
    if s3_key:
        s3_service.delete_file(s3_key)
    
    # Delete from MongoDB
    deleted = await mongodb_service.delete_file_metadata(file_id)
    
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete file metadata")
    
    return FileDeleteResponse(
        file_id=file_id,
        filename=filename,
        deleted=True,
        message="File deleted successfully. Note: Vectorstore needs to be rebuilt."
    )

