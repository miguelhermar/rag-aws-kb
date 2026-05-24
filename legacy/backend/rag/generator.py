"""
RAG generator using Gemini for answer generation.
"""

import logging
import re
from typing import Dict, Optional
from datetime import datetime
from google import genai
from google.genai import types

from core.config import settings
from rag.retriever import Retriever

logger = logging.getLogger(__name__)


class Generator:
    """Handles RAG pipeline and answer generation using Gemini."""
    
    RAG_PROMPT_TEMPLATE = """You are an AI Knowledge Base Assistant that answers questions STRICTLY based on the provided document context from uploaded files ONLY.

CRITICAL RULES - YOU MUST FOLLOW THESE STRICTLY:
1. You MUST ONLY use information from the provided document context below
2. DO NOT use any external knowledge, general information, or information from other sources
3. DO NOT make assumptions or add information not present in the uploaded documents
4. If the answer is not in the context, you MUST say: "I don't have information about this in the uploaded documents. Please upload relevant documents or rephrase your question."
5. Format your answer in POINT-WISE format using bullet points
6. If the context doesn't fully answer the question, acknowledge what information is available and what is missing

User Question:
{question}

Document Context (from uploaded files only):
{context}

Additional Mode:
Explain like I'm 10: {explain_mode}

Instructions:
1. FIRST: Carefully analyze the question to understand what specific information is being asked
2. SECOND: Search through the provided document context to find relevant information
3. THIRD: Synthesize the information maintaining accuracy to the original text
4. FOURTH: Format your response in POINT-WISE format with bullet points

Return your response in this format:
ANSWER: [Your point-wise answer here - based ONLY on the document context, formatted with bullet points]

SOURCES: [List all sources used from the documents]
"""
    
    def __init__(self, retriever: Retriever):
        """Initialize generator with a retriever."""
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")
        
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        
        # Use specified model or auto-detect
        if settings.GEMINI_LLM_MODEL:
            self.model_name = settings.GEMINI_LLM_MODEL
        else:
            self.model_name = self._get_latest_model()
        
        logger.info(f"Using Gemini model: {self.model_name}")
        self.retriever = retriever
    
    def _generate_content(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ):
        """Generate content using the Google GenAI SDK client."""
        config_kwargs = {}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = max_output_tokens
        
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        
        return self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )
    
    def _get_latest_model(self) -> str:
        """Dynamically detect the latest available Gemini model."""
        fallback_models = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ]
        
        try:
            gemini_models = []
            for model in self.client.models.list():
                supported_actions = model.supported_actions or []
                if 'gemini' in model.name.lower() and 'generateContent' in supported_actions:
                    model_name = model.name.replace('models/', '') if model.name.startswith('models/') else model.name
                    gemini_models.append(model_name)
            
            if gemini_models:
                def model_priority(name):
                    name_lower = name.lower()
                    if 'latest' in name_lower:
                        return (0, name_lower)
                    elif 'pro' in name_lower:
                        return (1, name_lower)
                    elif 'flash' in name_lower:
                        return (2, name_lower)
                    return (3, name_lower)
                
                gemini_models.sort(key=model_priority)
                return gemini_models[0]
            
            logger.warning("Could not detect available models, using fallback list")
            return fallback_models[0]
        except Exception as e:
            logger.warning(f"Error detecting latest model: {str(e)}. Using fallback.")
            return fallback_models[0]
    
    def generate_answer(self, question: str, explain_like_10: bool = False, top_k: int = None) -> Dict:
        """
        Generate an answer using RAG pipeline.
        
        Args:
            question: User question
            explain_like_10: Whether to simplify the explanation
            top_k: Number of chunks to retrieve
            
        Returns:
            Dictionary with answer, sources, confidence score, etc.
        """
        if top_k is None:
            top_k = settings.TOP_K_CHUNKS
        
        if not question or not question.strip():
            return {
                'answer': "Please provide a valid question.",
                'sources': [],
                'confidence_score': 0.0,
                'similarity_scores': []
            }
        
        try:
            retrieved_docs_with_scores = self.retriever.retrieve_with_scores(question, k=top_k)
            
            if not retrieved_docs_with_scores:
                return {
                    'answer': "I couldn't find any relevant information in the knowledge base. Please make sure documents have been uploaded and processed.",
                    'sources': [],
                    'confidence_score': 0.0,
                    'similarity_scores': []
                }
            
            retrieved_docs = [doc for doc, score in retrieved_docs_with_scores]
            similarity_scores = [score for doc, score in retrieved_docs_with_scores]
            
            # Calculate confidence score
            confidence_score = 0.0
            confidence_breakdown = None
            
            if similarity_scores:
                best_distance = min(similarity_scores)
                avg_distance = sum(similarity_scores) / len(similarity_scores)
                
                best_similarity = max(0.0, 1.0 - (best_distance / 2.0))
                avg_similarity = max(0.0, 1.0 - (avg_distance / 2.0))
                
                if len(similarity_scores) > 1:
                    variance = sum((s - avg_distance) ** 2 for s in similarity_scores) / len(similarity_scores)
                    consistency = 1.0 / (1.0 + variance)
                else:
                    consistency = 1.0
                
                query_lower = question.lower()
                keyword_matches = 0
                for doc in retrieved_docs:
                    content_lower = doc.page_content.lower()
                    query_words = set(re.findall(r'\b\w+\b', query_lower))
                    content_words = set(re.findall(r'\b\w+\b', content_lower))
                    matches = len(query_words.intersection(content_words))
                    if matches > 0:
                        keyword_matches += matches / len(query_words) if query_words else 0
                
                keyword_boost = min(1.0, keyword_matches / len(retrieved_docs)) if retrieved_docs else 0.0
                
                confidence_score = (
                    0.5 * best_similarity +
                    0.3 * avg_similarity +
                    0.1 * consistency +
                    0.1 * keyword_boost
                )
                
                confidence_score = confidence_score ** 0.9
                confidence_score = max(0.0, min(1.0, confidence_score))
                
                confidence_breakdown = {
                    'best_similarity': best_similarity,
                    'avg_similarity': avg_similarity,
                    'consistency': consistency,
                    'keyword_match': keyword_boost,
                    'final_score': confidence_score
                }
            
            # Format context and generate answer
            context = self.retriever.format_context(retrieved_docs)
            
            prompt = self.RAG_PROMPT_TEMPLATE.format(
                question=question,
                context=context,
                explain_mode="Yes" if explain_like_10 else "No"
            )
            
            response = self._generate_content(
                prompt,
                temperature=settings.TEMPERATURE,
                max_output_tokens=settings.MAX_TOKENS,
            )
            
            answer_text = response.text
            answer = self._extract_answer(answer_text)
            sources = self.retriever.get_source_metadata(retrieved_docs)
            
            # Add similarity scores to sources
            for i, source in enumerate(sources):
                if i < len(similarity_scores):
                    source['similarity_score'] = float(1.0 - (similarity_scores[i] / 2.0))
            
            return {
                'answer': answer,
                'sources': sources,
                'confidence_score': confidence_score,
                'confidence_breakdown': confidence_breakdown,
                'similarity_scores': [float(1.0 - (s / 2.0)) for s in similarity_scores],
                'query': question,
                'timestamp': datetime.utcnow(),
                'explain_mode': explain_like_10
            }
            
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return {
                'answer': f"An error occurred while generating the answer: {str(e)}",
                'sources': [],
                'confidence_score': 0.0,
                'similarity_scores': []
            }
    
    def _extract_answer(self, response_text: str) -> str:
        """Extract answer from response text."""
        if "ANSWER:" in response_text:
            parts = response_text.split("ANSWER:")
            if len(parts) > 1:
                answer = parts[1].split("SOURCES:")[0].strip()
                return answer
        return response_text.split("SOURCES:")[0].strip() if "SOURCES:" in response_text else response_text.strip()

