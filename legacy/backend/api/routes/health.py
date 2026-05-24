"""
Health check endpoints.
"""

from fastapi import APIRouter
from datetime import datetime
from services.mongodb import mongodb_service
from services.s3 import s3_service

router = APIRouter()


@router.get("/")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "Knowledge Base RAG API"
    }


@router.get("/detailed")
async def detailed_health_check():
    """Detailed health check with service status."""
    mongodb_status = await mongodb_service.test_connection() if mongodb_service.client else False
    s3_status = s3_service.client is not None
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "mongodb": "connected" if mongodb_status else "disconnected",
            "s3": "configured" if s3_status else "not_configured"
        }
    }

