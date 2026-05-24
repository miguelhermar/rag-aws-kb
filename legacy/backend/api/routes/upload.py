"""
File upload endpoints.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
import logging
import uuid
from pathlib import Path
from datetime import datetime
import tempfile
import os

from core.config import settings
from models.file import FileUploadResponse
from services.s3 import s3_service
from services.mongodb import mongodb_service
from rag.processor import DocumentProcessor
from rag.embedder import Embedder

router = APIRouter()
logger = logging.getLogger(__name__)

# Global embedder instance (lazy loaded)
_embedder: Embedder = None


def get_embedder() -> Embedder:
    """Get or create embedder instance."""
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
        # Try to load existing vectorstore
        try:
            _embedder.load_vectorstore()
        except FileNotFoundError:
            logger.info("No existing vectorstore found, will create new one")
    return _embedder


@router.post("/", response_model=FileUploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    Upload and process a document file.
    
    Supports: PDF, DOCX, DOC, TXT
    """
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(settings.ALLOWED_EXTENSIONS)}"
        )
    
    # Validate file size
    file_content = await file.read()
    if len(file_content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE / (1024*1024):.1f}MB"
        )
    
    try:
        # Save file temporarily for processing
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name
        
        try:
            # Process document
            processor = DocumentProcessor()
            documents = processor.load_document(tmp_path)
            chunks = processor.split_documents(documents)
            
            # Upload to S3 (if configured) or use local storage
            s3_key = None
            if s3_service.client:
                try:
                    s3_key = s3_service.upload_file(
                        file_content=file_content,
                        filename=file.filename,
                        content_type=file.content_type or "application/octet-stream"
                    )
                except Exception as e:
                    logger.warning(f"S3 upload failed, using local storage: {str(e)}")
            
            # If S3 not available, save locally
            if not s3_key:
                local_file_path = Path(settings.UPLOAD_DIR) / f"{uuid.uuid4().hex[:8]}_{file.filename}"
                local_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_file_path, "wb") as f:
                    f.write(file_content)
                s3_key = str(local_file_path)  # Use local path as identifier
            
            # Generate file ID
            file_id = str(uuid.uuid4())
            
            # Save to MongoDB (if configured) or skip
            file_metadata = {
                "file_id": file_id,
                "filename": file.filename,
                "file_size": len(file_content),
                "file_type": file_ext,
                "s3_key": s3_key,
                "processed": False,
                "chunk_count": len(chunks)
            }
            
            mongodb_id = None
            if mongodb_service.db:
                mongodb_id = await mongodb_service.insert_file_metadata(file_metadata)
            
            # Add documents to vectorstore
            embedder = get_embedder()
            embedder.add_documents(chunks)
            embedder.save_vectorstore()
            
            # Update metadata as processed (if MongoDB available)
            if mongodb_id and mongodb_service.db:
                await mongodb_service.update_file_metadata(mongodb_id, {"processed": True})
            
            return FileUploadResponse(
                file_id=file_id,
                filename=file.filename,
                file_size=len(file_content),
                file_type=file_ext,
                s3_key=s3_key,
                status="success",
                message="File uploaded and processed successfully",
                uploaded_at=datetime.utcnow()
            )
            
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    
    except Exception as e:
        logger.error(f"Error processing file upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@router.post("/batch")
async def upload_files(files: list[UploadFile] = File(...)):
    """
    Upload multiple files at once.
    """
    results = []
    errors = []
    
    for file in files:
        try:
            result = await upload_file(file)
            results.append(result.dict())
        except Exception as e:
            errors.append({
                "filename": file.filename,
                "error": str(e)
            })
    
    return {
        "successful": results,
        "failed": errors,
        "total": len(files),
        "success_count": len(results),
        "error_count": len(errors)
    }

