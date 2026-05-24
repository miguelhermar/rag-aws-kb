"""
FastAPI application entry point for Knowledge Base RAG System.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import logging
from contextlib import asynccontextmanager

from core.config import settings
from api.routes import upload, query, files, health

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info("Starting Knowledge Base RAG API...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    yield
    # Shutdown
    logger.info("Shutting down Knowledge Base RAG API...")


# Create FastAPI app
app = FastAPI(
    title="Knowledge Base RAG API",
    description="Production-ready RAG system with document upload, vector search, and AI-powered Q&A",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trusted host middleware (for production)
if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS
    )

# Include routers
app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(query.router, prefix="/api/query", tags=["Query"])
app.include_router(files.router, prefix="/api/files", tags=["Files"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Knowledge Base RAG API",
        "version": "1.0.0",
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    import sys
    import os
    
    # Use 127.0.0.1 for Windows local development, 0.0.0.0 for Linux/Docker
    host = "127.0.0.1" if sys.platform == "win32" and settings.ENVIRONMENT == "development" else "0.0.0.0"
    
    uvicorn.run(
        "app:app",
        host=host,
        port=8000,
        reload=settings.ENVIRONMENT == "development",
        log_level="info"
    )

