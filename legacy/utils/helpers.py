"""
Helper utility functions for the Knowledge Base Agent.
"""

import sys
import os
from pathlib import Path
from typing import List, Optional
import logging

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO"):
    """
    Setup logging configuration.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def get_uploaded_files(directory: Path) -> List[Path]:
    """
    Get list of uploaded files in the data directory.
    
    Args:
        directory: Path to data directory
        
    Returns:
        List of file paths
    """
    if not directory.exists():
        return []
    
    supported_extensions = {'.pdf', '.docx', '.doc', '.txt'}
    files = []
    
    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            files.append(file_path)
    
    return files


def save_uploaded_file(uploaded_file, save_directory: Path) -> Optional[Path]:
    """
    Save an uploaded file to the data directory.
    
    Args:
        uploaded_file: Streamlit uploaded file object
        save_directory: Directory to save the file
        
    Returns:
        Path to saved file, or None if error
    """
    save_directory.mkdir(parents=True, exist_ok=True)
    
    try:
        file_path = save_directory / uploaded_file.name
        
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        logger.info(f"Saved uploaded file: {file_path}")
        return file_path
        
    except Exception as e:
        logger.error(f"Error saving uploaded file: {str(e)}")
        return None


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def validate_api_key(api_key: str) -> bool:
    """
    Validate if API key is set.
    
    Args:
        api_key: API key string
        
    Returns:
        True if valid, False otherwise
    """
    return bool(api_key and api_key.strip())


def clear_vectorstore(vectorstore_dir: Path):
    """
    Clear the vector store directory.
    
    Args:
        vectorstore_dir: Path to vectorstore directory
    """
    if vectorstore_dir.exists():
        for file in vectorstore_dir.iterdir():
            if file.is_file():
                file.unlink()
        logger.info("Vector store cleared")


def get_chat_history_key() -> str:
    """Get the session state key for chat history."""
    return "chat_history"


def initialize_chat_history():
    """Initialize empty chat history."""
    return []

