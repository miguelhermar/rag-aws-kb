"""
Configuration file for Knowledge Base Agent.
Loads configuration from Streamlit secrets, .env file, and environment variables.
Priority: Streamlit secrets > .env file > system environment variables
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"

# Load .env file if it exists
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    # Try to load from parent directory as well
    load_dotenv(BASE_DIR.parent / ".env")

# Gemini API Configuration
# Priority: Streamlit secrets > environment variables > empty string
def get_gemini_api_key():
    """
    Get Gemini API key with priority:
    1. Streamlit secrets (for Streamlit Cloud)
    2. Environment variables (from .env file or system)
    3. Empty string (if not found)
    
    This function should be called at runtime (not at import time) to ensure
    Streamlit secrets are available when accessed.
    """
    # Try to get from Streamlit secrets first (for Streamlit Cloud)
    try:
        import streamlit as st
        if hasattr(st, 'secrets') and 'GEMINI_API_KEY' in st.secrets:
            return st.secrets['GEMINI_API_KEY']
    except (ImportError, RuntimeError, AttributeError):
        # Not in Streamlit context or secrets not available
        pass
    
    # Fall back to environment variable (from .env file or system)
    return os.getenv("GEMINI_API_KEY", "")

# Initialize with environment variable (for non-Streamlit contexts)
# This will be overridden at runtime when Streamlit is available
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Use default embedding model 
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
GEMINI_LLM_MODEL = None  # None = use latest LLM model

# Project Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
VECTORSTORE_DIR = BASE_DIR / "vectorstore"
VECTORSTORE_INDEX_PATH = VECTORSTORE_DIR / "index.faiss"
VECTORSTORE_PKL_PATH = VECTORSTORE_DIR / "index.pkl"

# Text Splitting Configuration
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# Retrieval Configuration
TOP_K_CHUNKS = 5  # Increased for better context understanding

# RAG Configuration
TEMPERATURE = 0.7
MAX_TOKENS = 1000

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
VECTORSTORE_DIR.mkdir(exist_ok=True)

