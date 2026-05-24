"""
Text splitting utilities using LangChain's RecursiveCharacterTextSplitter.
"""

from typing import List
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CHUNK_SIZE, CHUNK_OVERLAP


class TextSplitter:
    """Handles splitting documents into chunks for embedding."""
    
    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        """
        Initialize text splitter.
        
        Args:
            chunk_size: Maximum size of each chunk
            chunk_overlap: Overlap between chunks
        """
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
    
    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        Split documents into chunks.
        
        Args:
            documents: List of Document objects to split
            
        Returns:
            List of split Document chunks
        """
        chunks = self.splitter.split_documents(documents)
        
        # Add chunk index to metadata
        for idx, chunk in enumerate(chunks):
            if 'chunk_index' not in chunk.metadata:
                chunk.metadata['chunk_index'] = idx
        
        return chunks
    
    def split_text(self, text: str, metadata: dict = None) -> List[Document]:
        """
        Split a single text string into chunks.
        
        Args:
            text: Text to split
            metadata: Optional metadata to attach to chunks
            
        Returns:
            List of Document chunks
        """
        if metadata is None:
            metadata = {}
        
        chunks = self.splitter.create_documents([text], [metadata])
        
        for idx, chunk in enumerate(chunks):
            chunk.metadata['chunk_index'] = idx
        
        return chunks

