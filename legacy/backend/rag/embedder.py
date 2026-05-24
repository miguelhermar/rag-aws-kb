"""
Embedding generation using Gemini Embeddings API and FAISS storage.
"""

import logging
import pickle
from pathlib import Path
from typing import List, Optional
import faiss
import numpy as np
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from core.config import settings

logger = logging.getLogger(__name__)


class Embedder:
    """Handles embedding generation and FAISS vector store management."""
    
    def __init__(self):
        """Initialize the embedder with Gemini embeddings."""
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")
        
        self.embeddings = GoogleGenerativeAIEmbeddings(
            google_api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_EMBEDDING_MODEL
        )
        self.vectorstore: Optional[FAISS] = None
        self.vectorstore_path = Path(settings.VECTOR_STORE_PATH)
        self.vectorstore_path.mkdir(parents=True, exist_ok=True)
    
    def create_vectorstore(self, documents: List[Document]) -> FAISS:
        """
        Create a FAISS vector store from documents.
        
        Args:
            documents: List of Document objects to embed
            
        Returns:
            FAISS vector store
        """
        logger.info(f"Creating vector store from {len(documents)} documents...")
        
        try:
            self.vectorstore = FAISS.from_documents(
                documents=documents,
                embedding=self.embeddings
            )
            
            logger.info("Vector store created successfully")
            return self.vectorstore
            
        except Exception as e:
            logger.error(f"Error creating vector store: {str(e)}")
            raise
    
    def save_vectorstore(self, vectorstore: Optional[FAISS] = None):
        """
        Save the vector store to disk.
        
        Args:
            vectorstore: Optional vectorstore to save. If None, uses self.vectorstore
        """
        store = vectorstore or self.vectorstore
        
        if store is None:
            raise ValueError("No vector store to save")
        
        try:
            store.save_local(str(self.vectorstore_path))
            logger.info(f"Vector store saved to {self.vectorstore_path}")
            
        except Exception as e:
            logger.error(f"Error saving vector store: {str(e)}")
            raise
    
    def load_vectorstore(self) -> FAISS:
        """
        Load the vector store from disk.
        
        Returns:
            FAISS vector store
        """
        index_path = self.vectorstore_path / "index.faiss"
        
        if not index_path.exists():
            raise FileNotFoundError(
                f"Vector store not found at {index_path}. "
                "Please create the knowledge base first."
            )
        
        try:
            self.vectorstore = FAISS.load_local(
                str(self.vectorstore_path),
                self.embeddings,
                allow_dangerous_deserialization=True
            )
            logger.info("Vector store loaded successfully")
            return self.vectorstore
            
        except Exception as e:
            logger.error(f"Error loading vector store: {str(e)}")
            raise
    
    def add_documents(self, documents: List[Document]):
        """
        Add new documents to existing vector store.
        
        Args:
            documents: List of Document objects to add
        """
        if self.vectorstore is None:
            try:
                self.vectorstore = self.load_vectorstore()
            except FileNotFoundError:
                # Create new vector store if it doesn't exist
                self.vectorstore = self.create_vectorstore(documents)
                return
        
        try:
            self.vectorstore.add_documents(documents)
            logger.info(f"Added {len(documents)} documents to vector store")
        except Exception as e:
            logger.error(f"Error adding documents: {str(e)}")
            raise
    
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings for a list of texts.
        
        Args:
            texts: List of text strings
            
        Returns:
            List of embedding vectors
        """
        return self.embeddings.embed_documents(texts)

