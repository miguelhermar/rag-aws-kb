"""
RAG generator using Gemini 1.5 Flash for answer generation.
"""

import sys
import os
from pathlib import Path
import logging
import re
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types
from langchain_core.documents import Document

from config import get_gemini_api_key, GEMINI_LLM_MODEL, TEMPERATURE, MAX_TOKENS
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
1. FIRST: Carefully analyze the question to understand:
   - What specific information is being asked?
   - What keywords or key terms are mentioned?
   - What context or background is needed?
   
2. SECOND: Search through the provided document context to find relevant information:
   - Look for exact keyword matches
   - Look for related concepts and synonyms
   - Consider the context around matching text
   - Identify which document sections are most relevant
   
3. THIRD: Synthesize the information:
   - Combine relevant pieces from different parts of the documents
   - Maintain accuracy to the original text
   - Ensure all information comes from the provided context
   
4. FOURTH: Format your response:
   - Use POINT-WISE format with bullet points (• or -)
   - Each point should be clear, concise, and directly from the documents
   - If explaining like I'm 10, use simple language but maintain accuracy
   - If information is incomplete, clearly state what is available and what is missing

Return your response in this format:
ANSWER: [Your point-wise answer here - based ONLY on the document context, formatted with bullet points]

SOURCES: [List all sources used from the documents]
"""
    
    def __init__(self, retriever: Retriever):
        """
        Initialize generator with a retriever.
        
        Args:
            retriever: Retriever instance for getting relevant chunks
        """
        # Read API key with priority: Streamlit secrets > environment variables
        api_key = get_gemini_api_key()
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. Please set it in Streamlit secrets, .env file, or as an environment variable."
            )
        
        self.client = genai.Client(api_key=api_key)
        
        # Use specified model or dynamically detect latest available model
        if GEMINI_LLM_MODEL:
            self.model_name = GEMINI_LLM_MODEL
            logger.info(f"Using specified model: {self.model_name}")
        else:
            self.model_name = self._get_latest_model()
            logger.info(f"Using latest available model: {self.model_name}")
        
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
        """
        Dynamically detect the latest available Gemini model that supports generateContent.
        
        Returns:
            Model name string
        """
        fallback_models = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ]
        
        try:
            # List all available models via the GenAI SDK client
            gemini_models = []
            for model in self.client.models.list():
                supported_actions = model.supported_actions or []
                if 'gemini' in model.name.lower() and 'generateContent' in supported_actions:
                    # Remove 'models/' prefix if present
                    model_name = model.name.replace('models/', '') if model.name.startswith('models/') else model.name
                    gemini_models.append(model_name)
            
            if not gemini_models:
                logger.warning("Could not detect available models, using fallback list")
                return fallback_models[0]
            
            # Sort models to get the latest (prefer models with 'latest' or higher version numbers)
            # Prioritize: latest > pro > flash > others
            def model_priority(name):
                name_lower = name.lower()
                if 'latest' in name_lower:
                    return (0, name_lower)
                elif 'pro' in name_lower:
                    return (1, name_lower)
                elif 'flash' in name_lower:
                    return (2, name_lower)
                else:
                    return (3, name_lower)
            
            gemini_models.sort(key=model_priority)
            latest_model = gemini_models[0]
            logger.info(f"Detected latest model: {latest_model}")
            return latest_model
            
        except Exception as e:
            logger.warning(f"Error detecting latest model: {str(e)}. Using fallback.")
            return fallback_models[0]
    
    def generate_answer(
        self,
        question: str,
        explain_like_10: bool = False,
        top_k: int = 5  # Increased default for better context
    ) -> Dict:
        """
        Generate an answer using RAG pipeline.
        
        Args:
            question: User question
            explain_like_10: Whether to simplify the explanation
            top_k: Number of chunks to retrieve
            
        Returns:
            Dictionary with answer, sources, and related questions
        """
        if not question or not question.strip():
            return {
                'answer': "Please provide a valid question.",
                'sources': [],
                'related_questions': [],
                'context_used': []
            }
        
        try:
            # Retrieve relevant chunks with scores for confidence calculation
            retrieved_docs_with_scores = self.retriever.retrieve_with_scores(question, k=top_k)
            
            if not retrieved_docs_with_scores:
                return {
                    'answer': "I couldn't find any relevant information in the knowledge base. Please make sure documents have been uploaded and processed.",
                    'sources': [],
                    'related_questions': [],
                    'context_used': [],
                    'confidence_score': 0.0
                }
            
            # Extract documents and scores
            retrieved_docs = [doc for doc, score in retrieved_docs_with_scores]
            similarity_scores = [score for doc, score in retrieved_docs_with_scores]
            
            # Calculate improved confidence score based on similarity scores and context quality
            # FAISS returns distance scores (lower is better for cosine/L2 distance)
            # For cosine similarity embeddings, distance typically ranges from 0 to 2
            if similarity_scores:
                # Get the best (lowest) distance score
                best_distance = min(similarity_scores)
                avg_distance = sum(similarity_scores) / len(similarity_scores)
                
                # Convert distance to similarity score
                # For cosine distance: similarity ≈ 1 - (distance/2) when normalized
                # Using a more accurate conversion
                best_similarity = max(0.0, 1.0 - (best_distance / 2.0))
                avg_similarity = max(0.0, 1.0 - (avg_distance / 2.0))
                
                # Calculate score consistency (lower variance = higher confidence)
                if len(similarity_scores) > 1:
                    variance = sum((s - avg_distance) ** 2 for s in similarity_scores) / len(similarity_scores)
                    consistency = 1.0 / (1.0 + variance)  # Higher consistency = higher confidence
                else:
                    consistency = 1.0
                
                # Calculate keyword match boost (if retriever provides it)
                # Check if documents contain query keywords
                query_lower = question.lower()
                keyword_matches = 0
                for doc in retrieved_docs:
                    content_lower = doc.page_content.lower()
                    # Count how many query words appear in the document
                    query_words = set(re.findall(r'\b\w+\b', query_lower))
                    content_words = set(re.findall(r'\b\w+\b', content_lower))
                    matches = len(query_words.intersection(content_words))
                    if matches > 0:
                        keyword_matches += matches / len(query_words) if query_words else 0
                
                keyword_boost = min(1.0, keyword_matches / len(retrieved_docs)) if retrieved_docs else 0.0
                
                # Combine factors for final confidence score
                # 50% best similarity, 30% average similarity, 10% consistency, 10% keyword match
                confidence_score = (
                    0.5 * best_similarity +
                    0.3 * avg_similarity +
                    0.1 * consistency +
                    0.1 * keyword_boost
                )
                
                # Apply sigmoid-like scaling for better distribution
                # This makes the confidence score more discriminative
                confidence_score = confidence_score ** 0.9  # Slight adjustment
                
                # Ensure confidence is in 0-1 range
                confidence_score = max(0.0, min(1.0, confidence_score))
            else:
                confidence_score = 0.0
            
            # Format context
            context = self.retriever.format_context(retrieved_docs)
            
            # Build prompt
            prompt = self.RAG_PROMPT_TEMPLATE.format(
                question=question,
                context=context,
                explain_mode="Yes" if explain_like_10 else "No"
            )
            
            # Generate response
            response = self._generate_content(
                prompt,
                temperature=TEMPERATURE,
                max_output_tokens=MAX_TOKENS,
            )
            
            # Parse response
            answer_text = response.text
            
            # Extract components (simple parsing)
            answer = self._extract_answer(answer_text)
            sources = self.retriever.get_source_metadata(retrieved_docs)
            
            return {
                'answer': answer,
                'sources': sources,
                'related_questions': [],  # No follow-up questions
                'context_used': [doc.page_content for doc in retrieved_docs],
                'raw_response': answer_text,
                'confidence_score': confidence_score,
                'similarity_scores': similarity_scores
            }
            
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return {
                'answer': f"An error occurred while generating the answer: {str(e)}",
                'sources': [],
                'related_questions': [],
                'context_used': []
            }
    
    def _extract_answer(self, response_text: str) -> str:
        """Extract answer from response text."""
        if "ANSWER:" in response_text:
            parts = response_text.split("ANSWER:")
            if len(parts) > 1:
                answer = parts[1].split("SOURCES:")[0].strip()
                return answer
        
        # Fallback: return the whole response
        return response_text.split("SOURCES:")[0].strip() if "SOURCES:" in response_text else response_text.strip()
    
    def _extract_related_questions(self, response_text: str) -> List[str]:
        """Extract related questions from response text."""
        questions = []
        
        if "RELATED QUESTIONS:" in response_text:
            questions_part = response_text.split("RELATED QUESTIONS:")[1]
            lines = questions_part.split('\n')
            
            for line in lines:
                line = line.strip()
                if line and (line.startswith('-') or line.startswith('1.') or line.startswith('2.') or line.startswith('3.')):
                    # Remove numbering/bullets
                    question = line.lstrip('-1234567890. ').strip()
                    if question:
                        questions.append(question)
        
        # If no questions found, generate some based on context
        if not questions:
            questions = [
                "Can you tell me more about this topic?",
                "What are the key points I should know?",
                "Are there any related concepts I should understand?"
            ]
        
        return questions[:3]  # Return max 3 questions
    
    def generate_related_questions(self, context_chunks: List[str]) -> List[str]:
        """
        Generate related questions based on context chunks.
        Questions must be answerable from the document context only.
        
        Args:
            context_chunks: List of context text chunks
            
        Returns:
            List of suggested questions
        """
        if not context_chunks:
            return []
        
        context_preview = "\n".join([chunk[:200] for chunk in context_chunks[:3]])
        
        prompt = f"""Based ONLY on the following document context, suggest 3 related questions that can be answered from this context.

IMPORTANT: All questions MUST be answerable from the provided context. Do not suggest questions that require external knowledge.

Document Context:
{context_preview}

Generate 3 concise, relevant questions that can be answered from the above context. Return only the questions, one per line."""
        
        try:
            response = self._generate_content(prompt)
            questions = [q.strip() for q in response.text.split('\n') if q.strip()][:3]
            return questions
        except Exception as e:
            logger.error(f"Error generating related questions: {str(e)}")
            return []

