"""
Document loader for various file formats (PDF, DOCX, TXT).
Uses LangChain loaders for document extraction.
"""

import sys
from pathlib import Path
from typing import List, Dict
import logging

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class DocumentLoader:
    """Handles loading and extracting text from various document formats."""
    
    SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}
    
    @staticmethod
    def load_document(file_path: str) -> List[Document]:
        """
        Load a document from file path.
        
        Args:
            file_path: Path to the document file
            
        Returns:
            List of Document objects with text and metadata
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        extension = file_path.suffix.lower()
        
        if extension not in DocumentLoader.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file format: {extension}. "
                f"Supported formats: {DocumentLoader.SUPPORTED_EXTENSIONS}"
            )
        
        try:
            if extension == '.pdf':
                loader = PyPDFLoader(str(file_path))
            elif extension in {'.docx', '.doc'}:
                loader = Docx2txtLoader(str(file_path))
            elif extension == '.txt':
                loader = TextLoader(str(file_path), encoding='utf-8')
            else:
                raise ValueError(f"Unsupported extension: {extension}")
            
            documents = loader.load()
            
            # Add source metadata
            for doc in documents:
                if 'source' not in doc.metadata:
                    doc.metadata['source'] = str(file_path.name)
                if 'file_path' not in doc.metadata:
                    doc.metadata['file_path'] = str(file_path)
            
            logger.info(f"Loaded {len(documents)} pages/chunks from {file_path.name}")
            return documents
            
        except Exception as e:
            logger.error(f"Error loading document {file_path}: {str(e)}")
            raise
    
    @staticmethod
    def load_multiple_documents(file_paths: List[str]) -> List[Document]:
        """
        Load multiple documents.
        
        Args:
            file_paths: List of file paths
            
        Returns:
            Combined list of Document objects
        """
        all_documents = []
        
        for file_path in file_paths:
            try:
                documents = DocumentLoader.load_document(file_path)
                all_documents.extend(documents)
            except Exception as e:
                logger.warning(f"Failed to load {file_path}: {str(e)}")
                continue
        
        return all_documents
    
    @staticmethod
    def clean_text(text: str) -> str:
        """
        Clean extracted text.
        
        Args:
            text: Raw text to clean
            
        Returns:
            Cleaned text
        """
        # Remove excessive whitespace
        lines = text.split('\n')
        cleaned_lines = [line.strip() for line in lines if line.strip()]
        return '\n'.join(cleaned_lines)

