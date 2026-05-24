"""
Retriever for searching FAISS vector store and retrieving relevant chunks.
"""

import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import logging
import re

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config import TOP_K_CHUNKS

logger = logging.getLogger(__name__)


class Retriever:
    """Handles retrieval of relevant document chunks from vector store."""
    
    def __init__(self, vectorstore: FAISS):
        """
        Initialize retriever with a vector store.
        
        Args:
            vectorstore: FAISS vector store instance
        """
        self.vectorstore = vectorstore
    
    def _extract_keywords(self, query: str) -> List[str]:
        """
        Extract important keywords from the query.
        
        Args:
            query: User query string
            
        Returns:
            List of keywords
        """
        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should', 'could', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those', 'what', 'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how'}
        
        # Extract words (alphanumeric, hyphens, apostrophes)
        words = re.findall(r'\b[a-zA-Z0-9\-\']+\b', query.lower())
        
        # Filter out stop words and short words
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)
        
        return unique_keywords
    
    def _expand_query(self, query: str) -> str:
        """
        Expand query with keywords and context for better retrieval.
        
        Args:
            query: Original query
            
        Returns:
            Expanded query string
        """
        keywords = self._extract_keywords(query)
        
        # If we have keywords, add them to the query
        if keywords:
            # Create expanded query with original + keywords
            expanded = f"{query} {' '.join(keywords)}"
            return expanded
        
        return query
    
    def _calculate_keyword_match_score(self, document: Document, keywords: List[str]) -> float:
        """
        Calculate a keyword match score for a document.
        
        Args:
            document: Document to score
            keywords: List of keywords to match
            
        Returns:
            Keyword match score (0-1)
        """
        if not keywords:
            return 0.0
        
        content_lower = document.page_content.lower()
        matches = sum(1 for kw in keywords if kw.lower() in content_lower)
        
        # Normalize by number of keywords
        return matches / len(keywords) if keywords else 0.0
    
    def retrieve(self, query: str, k: int = TOP_K_CHUNKS) -> List[Document]:
        """
        Retrieve top k most relevant chunks for a query.
        
        Args:
            query: User query string
            k: Number of chunks to retrieve
            
        Returns:
            List of Document objects with relevant chunks
        """
        if not query or not query.strip():
            return []
        
        try:
            # Search for similar documents
            docs = self.vectorstore.similarity_search(
                query=query,
                k=k
            )
            
            logger.info(f"Retrieved {len(docs)} chunks for query: {query[:50]}...")
            return docs
            
        except Exception as e:
            logger.error(f"Error retrieving documents: {str(e)}")
            return []
    
    def retrieve_with_scores(self, query: str, k: int = TOP_K_CHUNKS) -> List[Tuple[Document, float]]:
        """
        Retrieve top k chunks with similarity scores using improved retrieval.
        
        Args:
            query: User query string
            k: Number of chunks to retrieve (will retrieve more and filter)
            
        Returns:
            List of tuples (Document, similarity_score) sorted by relevance
        """
        if not query or not query.strip():
            return []
        
        try:
            # Extract keywords for better matching
            keywords = self._extract_keywords(query)
            
            # Retrieve more chunks than needed for better filtering
            retrieve_k = min(k * 3, 20)  # Retrieve 3x more, max 20
            
            # Use expanded query for initial retrieval
            expanded_query = self._expand_query(query)
            
            # Search with scores using expanded query
            docs_with_scores = self.vectorstore.similarity_search_with_score(
                query=expanded_query,
                k=retrieve_k
            )
            
            if not docs_with_scores:
                # Fallback to original query if expanded query fails
                docs_with_scores = self.vectorstore.similarity_search_with_score(
                    query=query,
                    k=retrieve_k
                )
            
            if not docs_with_scores:
                logger.warning(f"No documents retrieved for query: {query[:50]}...")
                return []
            
            # Enhance scores with keyword matching
            enhanced_results = []
            for doc, distance_score in docs_with_scores:
                # Calculate keyword match score
                keyword_score = self._calculate_keyword_match_score(doc, keywords)
                
                # Convert distance to similarity (lower distance = higher similarity)
                # For cosine distance: similarity ≈ 1 - distance
                # Normalize distance to 0-1 range (assuming max distance ~2 for cosine)
                normalized_distance = min(distance_score / 2.0, 1.0) if distance_score > 0 else 0.0
                similarity_score = 1.0 - normalized_distance
                
                # Combine similarity and keyword match (weighted)
                # 70% similarity, 30% keyword match
                combined_score = 0.7 * similarity_score + 0.3 * keyword_score
                
                # Store enhanced score (we'll use distance for sorting, but combined for confidence)
                enhanced_results.append((doc, distance_score, combined_score, keyword_score))
            
            # Sort by combined score (higher is better)
            enhanced_results.sort(key=lambda x: x[2], reverse=True)
            
            # Take top k results
            final_results = [(doc, distance) for doc, distance, combined, _ in enhanced_results[:k]]
            
            logger.info(f"Retrieved {len(final_results)} chunks with enhanced scoring for query: {query[:50]}...")
            return final_results
            
        except Exception as e:
            logger.error(f"Error retrieving documents with scores: {str(e)}")
            # Fallback to basic retrieval
            try:
                return self.vectorstore.similarity_search_with_score(query=query, k=k)
            except:
                return []
    
    def format_context(self, documents: List[Document]) -> str:
        """
        Format retrieved documents into a context string.
        
        Args:
            documents: List of Document objects
            
        Returns:
            Formatted context string
        """
        context_parts = []
        
        for idx, doc in enumerate(documents, 1):
            source = doc.metadata.get('source', 'Unknown')
            chunk_idx = doc.metadata.get('chunk_index', idx)
            
            context_parts.append(
                f"[Source: {source}, Chunk {chunk_idx}]\n{doc.page_content}\n"
            )
        
        return "\n---\n\n".join(context_parts)
    
    def get_source_metadata(self, documents: List[Document]) -> List[Dict]:
        """
        Extract source metadata from documents.
        
        Args:
            documents: List of Document objects
            
        Returns:
            List of metadata dictionaries
        """
        metadata_list = []
        
        for doc in documents:
            # Get file path, fallback to source if file_path not available
            file_path = doc.metadata.get('file_path', doc.metadata.get('source', 'Unknown'))
            # Extract just the filename if full path is provided
            if file_path and file_path != 'Unknown':
                from pathlib import Path
                file_name = Path(file_path).name
            else:
                file_name = doc.metadata.get('source', 'Unknown')
            
            metadata = {
                'source': file_name,
                'chunk_index': doc.metadata.get('chunk_index', 0),
                'file_path': file_path,
                'content_preview': doc.page_content[:300] + "..." if len(doc.page_content) > 300 else doc.page_content
            }
            metadata_list.append(metadata)
        
        return metadata_list

