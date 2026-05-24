"""
Embedding generation using Gemini Embeddings API and FAISS storage.
"""

import os
import pickle
import sys
from pathlib import Path
from typing import List
import logging
import asyncio
import nest_asyncio

# Patch asyncio to allow nested event loops (fixes Streamlit async issues)
nest_asyncio.apply()

import faiss
import numpy as np
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

# Import GoogleGenerativeAIEmbeddings from langchain_google_genai (correct package)
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    get_gemini_api_key,
    GEMINI_EMBEDDING_MODEL,
    VECTORSTORE_DIR,
    VECTORSTORE_INDEX_PATH,
    VECTORSTORE_PKL_PATH
)

logger = logging.getLogger(__name__)


class Embedder:
    """Handles embedding generation and FAISS vector store management."""
    
    def __init__(self):
        """Initialize the embedder with Gemini embeddings."""
        # Read API key with priority: Streamlit secrets > environment variables
        api_key = get_gemini_api_key()
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. Please set it in Streamlit secrets, .env file, or as an environment variable."
            )
        
        # Initialize embeddings - model is required
        init_params = {
            "google_api_key": api_key,
            "model": GEMINI_EMBEDDING_MODEL  # Always provide model
        }
        self.embeddings = GoogleGenerativeAIEmbeddings(**init_params)
        self.vectorstore = None
    
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
            # Create FAISS vector store from documents
            self.vectorstore = FAISS.from_documents(
                documents=documents,
                embedding=self.embeddings
            )
            
            logger.info("Vector store created successfully")
            return self.vectorstore
            
        except Exception as e:
            logger.error(f"Error creating vector store: {str(e)}")
            raise
    
    def save_vectorstore(self, vectorstore: FAISS = None):
        """
        Save the vector store to disk.
        
        Args:
            vectorstore: Optional vectorstore to save. If None, uses self.vectorstore
        """
        store = vectorstore or self.vectorstore
        
        if store is None:
            raise ValueError("No vector store to save")
        
        try:
            # Save FAISS index
            store.save_local(str(VECTORSTORE_DIR))
            logger.info(f"Vector store saved to {VECTORSTORE_DIR}")
            
        except Exception as e:
            logger.error(f"Error saving vector store: {str(e)}")
            raise
    
    def load_vectorstore(self) -> FAISS:
        """
        Load the vector store from disk.
        
        Returns:
            FAISS vector store
        """
        if not VECTORSTORE_INDEX_PATH.exists():
            raise FileNotFoundError(
                f"Vector store not found at {VECTORSTORE_INDEX_PATH}. "
                "Please create the knowledge base first."
            )
        
        try:
            self.vectorstore = FAISS.load_local(
                str(VECTORSTORE_DIR),
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

