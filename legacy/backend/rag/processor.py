"""
Document processing service for chunking and preparation.
"""

import logging
from typing import List
from pathlib import Path
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.config import settings

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Handles document loading and chunking."""
    
    SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}
    
    def __init__(self):
        """Initialize document processor."""
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def load_document(self, file_path: str) -> List[Document]:
        """
        Load a document from file path.
        
        Args:
            file_path: Path to the document file
            
        Returns:
            List of Document objects
        """
        file_path_obj = Path(file_path)
        
        if not file_path_obj.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        extension = file_path_obj.suffix.lower()
        
        if extension not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file format: {extension}. "
                f"Supported formats: {self.SUPPORTED_EXTENSIONS}"
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
                    doc.metadata['source'] = file_path_obj.name
                if 'file_path' not in doc.metadata:
                    doc.metadata['file_path'] = str(file_path)
            
            logger.info(f"Loaded {len(documents)} pages/chunks from {file_path_obj.name}")
            return documents
            
        except Exception as e:
            logger.error(f"Error loading document {file_path}: {str(e)}")
            raise
    
    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        Split documents into chunks.
        
        Args:
            documents: List of Document objects
            
        Returns:
            List of split Document chunks
        """
        chunks = self.splitter.split_documents(documents)
        
        # Add chunk index to metadata
        for idx, chunk in enumerate(chunks):
            if 'chunk_index' not in chunk.metadata:
                chunk.metadata['chunk_index'] = idx
        
        logger.info(f"Split {len(documents)} documents into {len(chunks)} chunks")
        return chunks

