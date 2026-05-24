"""
Query endpoints for RAG system.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
import logging

from models.query import QueryRequest, QueryResponse, ConfidenceBreakdown
from rag.embedder import Embedder
from rag.retriever import Retriever
from rag.generator import Generator
from services.mongodb import mongodb_service

router = APIRouter()
logger = logging.getLogger(__name__)

# Global instances (lazy loaded)
_embedder: Optional[Embedder] = None
_retriever: Optional[Retriever] = None
_generator: Optional[Generator] = None


def get_generator() -> Generator:
    """Get or create generator instance."""
    global _embedder, _retriever, _generator
    
    if _generator is None:
        try:
            _embedder = Embedder()
            vectorstore = _embedder.load_vectorstore()
            _retriever = Retriever(vectorstore)
            _generator = Generator(_retriever)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="Knowledge base not found. Please upload documents first."
            )
        except Exception as e:
            logger.error(f"Error initializing generator: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error initializing RAG system: {str(e)}")
    
    return _generator


@router.post("/", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """
    Query the knowledge base with a question.
    
    Returns answer with sources, confidence scores, and citations.
    """
    generator = get_generator()
    
    try:
        result = generator.generate_answer(
            question=request.question,
            explain_like_10=request.explain_like_10,
            top_k=request.top_k
        )
        
        # Convert to response model
        confidence_breakdown = None
        if result.get('confidence_breakdown'):
            cb = result['confidence_breakdown']
            confidence_breakdown = ConfidenceBreakdown(
                best_similarity=cb['best_similarity'],
                avg_similarity=cb['avg_similarity'],
                consistency=cb['consistency'],
                keyword_match=cb['keyword_match'],
                final_score=cb['final_score']
            )
        
        # Log query to MongoDB
        await mongodb_service.insert_chat_log({
            "question": request.question,
            "answer": result['answer'],
            "confidence_score": result['confidence_score'],
            "sources_count": len(result['sources']),
            "explain_mode": request.explain_like_10
        })
        
        return QueryResponse(
            answer=result['answer'],
            sources=result['sources'],
            confidence_score=result['confidence_score'],
            confidence_breakdown=confidence_breakdown,
            similarity_scores=result['similarity_scores'],
            query=request.question,
            timestamp=result['timestamp'],
            explain_mode=request.explain_like_10
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")

