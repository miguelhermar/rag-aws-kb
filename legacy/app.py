"""
Main Streamlit application for Knowledge Base AI Agent.
Split-view interface with document preview and chat.
"""

import streamlit as st
import logging
import os
from pathlib import Path
import sys
import json
from datetime import datetime
import pandas as pd
import hashlib

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    DATA_DIR,
    VECTORSTORE_DIR,
    VECTORSTORE_INDEX_PATH
)
from loaders.document_loader import DocumentLoader
from utils.text_splitter import TextSplitter
from rag.embedder import Embedder
from rag.retriever import Retriever
from rag.generator import Generator
from utils.helpers import (
    setup_logging,
    save_uploaded_file,
    get_uploaded_files,
    format_file_size,
    validate_api_key,
    clear_vectorstore,
)

# Setup logging
setup_logging()

# Page configuration
st.set_page_config(
    page_title="Knowledge Base AI Agent",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': None
    }
)

# Custom CSS for Modern UI with Dark Theme
st.markdown("""
    <style>
    /* Hide Streamlit default elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* Dark theme background */
    .stApp {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    }
    
    /* Main content background - adjust for sidebar */
    .main .block-container {
        background: transparent;
        padding-top: 2rem;
        max-width: 100% !important;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    
    /* Sidebar dark theme - Stable and adjustable */
    .css-1d391kg {
        background: #1a1a2e;
    }
    
    [data-testid="stSidebar"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        height: 100vh !important;
        overflow-y: auto !important;
    }
    
    /* Sidebar text */
    [data-testid="stSidebar"] * {
        color: #ffffff !important;
    }
    
    /* Make sidebar resizable and always visible */
    [data-testid="stSidebar"][aria-expanded="true"] {
        min-width: 21rem !important;
        max-width: 50% !important;
    }
    
    /* Force sidebar to stay open */
    [data-testid="stSidebar"] {
        visibility: visible !important;
    }
    
    /* Hide sidebar collapse button */
    [data-testid="stSidebar"] [data-testid="collapsedControl"] {
        display: none !important;
    }
    
    /* Ensure main content adjusts when sidebar is open */
    section[data-testid="stMain"] {
        margin-left: 0 !important;
    }
    
    /* Adjust main content width when sidebar is expanded */
    .main .block-container {
        width: auto !important;
    }
    
    /* Uploaded files list in sidebar */
    .uploaded-file-item {
        padding: 0.75rem;
        margin: 0.5rem 0;
        background: rgba(102, 126, 234, 0.2);
        border-radius: 8px;
        border: 1px solid rgba(102, 126, 234, 0.3);
        color: #ffffff;
        font-size: 0.9rem;
    }
    
    /* Simple smooth slide animations */
    @keyframes smoothSlide {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    .smooth-slide {
        animation: smoothSlide 0.4s ease-out;
    }
    
    /* Smooth fade for transitions */
    @keyframes smoothFade {
        from {
            opacity: 0;
        }
        to {
            opacity: 1;
        }
    }
    
    .smooth-fade {
        animation: smoothFade 0.3s ease-in;
    }
    
    /* Navbar - Dark theme */
    .navbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1.25rem 2rem;
        background: rgba(26, 26, 46, 0.9);
        border-bottom: 2px solid rgba(102, 126, 234, 0.3);
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        border-radius: 12px;
    }
    
    .navbar-title {
        font-size: 1.75rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        color: #ffffff;
    }
    
    .upload-history {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        align-items: center;
    }
    
    .upload-history-item {
        padding: 0.5rem 1rem;
        background: #f5f5f5;
        border-radius: 8px;
        font-size: 0.9rem;
        color: #666;
        cursor: pointer;
        transition: all 0.2s;
    }
    
    .upload-history-item:hover {
        background: #e0e0e0;
    }
    
    /* Navbar buttons */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 1.5rem;
        font-weight: 600;
        box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
    }
    
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        transform: translateY(-2px);
    }
    
    .stButton > button[kind="secondary"] {
        background: #6c757d;
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 1.5rem;
        font-weight: 500;
    }
    
    .stButton > button[kind="secondary"]:hover {
        background: #5a6268;
        transform: translateY(-2px);
    }
    
    /* Start New Conversation Screen - Dark theme */
    .start-screen {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 70vh;
        padding: 3rem;
        background: rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        margin: 2rem 0;
        border: 1px solid rgba(102, 126, 234, 0.3);
        backdrop-filter: blur(10px);
    }
    
    .start-icon {
        font-size: 5rem;
        margin-bottom: 1.5rem;
        filter: drop-shadow(0 4px 8px rgba(0,0,0,0.1));
    }
    
    .start-title {
        font-size: 2.5rem;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 0.75rem;
        text-align: center;
    }
    
    .start-subtitle {
        font-size: 1.1rem;
        color: rgba(255, 255, 255, 0.7);
        margin-bottom: 3rem;
        text-align: center;
        max-width: 600px;
        line-height: 1.6;
    }
    
    .action-buttons {
        display: flex;
        gap: 1.5rem;
        margin-top: 2rem;
        width: 100%;
        max-width: 800px;
        justify-content: center;
    }
    
    .action-button {
        padding: 1.2rem 2.5rem;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 12px;
        font-size: 1.1rem;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    
    .action-button:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* Document Preview - Dark theme */
    .doc-preview {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 12px;
        padding: 1.5rem;
        height: calc(100vh - 200px);
        overflow-y: auto;
        border: 1px solid rgba(102, 126, 234, 0.3);
        backdrop-filter: blur(10px);
    }
    
    .doc-preview-header {
        font-size: 1.3rem;
        font-weight: 700;
        margin-bottom: 1.5rem;
        color: #ffffff;
        padding-bottom: 0.75rem;
        border-bottom: 2px solid #667eea;
    }
    
    .doc-item {
        padding: 1rem;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        margin-bottom: 0.75rem;
        border: 2px solid rgba(102, 126, 234, 0.3);
        cursor: pointer;
        transition: all 0.3s;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        color: #ffffff;
    }
    
    .doc-item:hover {
        border-color: #667eea;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        transform: translateY(-2px);
        background: rgba(102, 126, 234, 0.2);
    }
    
    .doc-item.active {
        border-color: #667eea;
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.3) 0%, rgba(118, 75, 162, 0.3) 100%);
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.5);
    }
    
    /* Button styling for dark theme */
    .stButton > button {
        color: #ffffff !important;
    }
    
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        color: #ffffff !important;
    }
    
    .stButton > button[kind="secondary"] {
        background: rgba(255, 255, 255, 0.1) !important;
        color: #ffffff !important;
        border: 1px solid rgba(102, 126, 234, 0.3) !important;
    }
    
    /* Document Preview Content */
    .doc-preview-content {
        background: white;
        border-radius: 8px;
        padding: 1.5rem;
        margin-top: 1rem;
        border: 1px solid #e9ecef;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    .doc-preview-text {
        color: #ffffff;
        font-size: 0.95rem;
        line-height: 1.6;
        background: rgba(255, 255, 255, 0.1);
        padding: 1rem;
        border-radius: 6px;
        border: 1px solid rgba(102, 126, 234, 0.3);
        max-height: 400px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-wrap: break-word;
    }
    
    .doc-preview-content {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        padding: 1.5rem;
        margin-top: 1rem;
        border: 1px solid rgba(102, 126, 234, 0.3);
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    
    .doc-preview-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: #ffffff;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid rgba(102, 126, 234, 0.3);
    }
    
    .doc-preview-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: #212529;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid #e9ecef;
    }
    
    /* Chat Container - Dark theme */
    .chat-container {
        height: calc(100vh - 200px);
        display: flex;
        flex-direction: column;
        background: rgba(255, 255, 255, 0.05);
        border-radius: 12px;
        padding: 1rem;
        border: 1px solid rgba(102, 126, 234, 0.3);
        backdrop-filter: blur(10px);
    }
    
    .chat-messages {
        flex: 1;
        overflow-y: auto;
        padding: 1rem;
        background: rgba(255, 255, 255, 0.03);
        border-radius: 8px;
    }
    
    /* Sources */
    .sources-container {
        margin-top: 1rem;
        padding: 1rem;
        background: #f8f9fa;
        border-radius: 8px;
        border: 1px solid #e9ecef;
    }
    
    .source-item {
        padding: 0.75rem;
        margin: 0.5rem 0;
        background: white;
        border-radius: 6px;
        font-size: 0.9rem;
        border: 1px solid #dee2e6;
        color: #212529;
    }
    
    /* Follow-up Questions */
    .follow-up-container {
        margin-top: 1rem;
        padding: 0.5rem 0;
    }
    
    .follow-up-question {
        display: inline-block;
        padding: 0.6rem 1.2rem;
        margin: 0.3rem 0.3rem 0.3rem 0;
        background: linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%);
        color: #667eea;
        border: 2px solid #667eea;
        border-radius: 20px;
        font-size: 0.9rem;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.3s;
        box-shadow: 0 2px 4px rgba(102, 126, 234, 0.1);
    }
    
    .follow-up-question:hover {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);
    }
    
    /* Enhanced button styling */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.3s;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    
    /* Text area styling for document preview */
    .stTextArea > div > div > textarea {
        background-color: #ffffff !important;
        color: #212529 !important;
        border: 1px solid #dee2e6 !important;
        border-radius: 6px !important;
        padding: 1rem !important;
        font-size: 0.95rem !important;
        line-height: 1.6 !important;
    }
    
    /* Info boxes */
    .stInfo {
        background: #e7f3ff;
        border-left: 4px solid #2196F3;
        color: #0d47a1;
    }
    
    /* Sidebar */
    .sidebar-content {
        padding: 1rem;
    }
    
    /* Scrollbar */
    ::-webkit-scrollbar {
        width: 10px;
    }
    
    ::-webkit-scrollbar-track {
        background: #f1f3f5;
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
    }
    
    /* Instructions section */
    .instructions-section {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 12px;
        padding: 2rem;
        margin: 2rem 0;
        border: 1px solid rgba(102, 126, 234, 0.3);
        backdrop-filter: blur(10px);
        color: #ffffff;
    }
    
    .instructions-section h3 {
        color: #ffffff !important;
        margin-bottom: 1rem;
    }
    
    .instructions-section ul {
        margin-left: 1.5rem;
        line-height: 1.8;
    }
    
    .instructions-section li {
        margin-bottom: 0.5rem;
    }
    
    /* File uploader styling - Dark theme */
    .stFileUploader > div > div {
        background: rgba(255, 255, 255, 0.05) !important;
        border: 2px dashed #667eea !important;
        border-radius: 12px !important;
        padding: 2rem !important;
        backdrop-filter: blur(10px);
    }
    
    .stFileUploader label {
        color: #ffffff !important;
    }
    
    /* Text colors for dark theme */
    h1, h2, h3, h4, h5, h6, p, span, div {
        color: #ffffff !important;
    }
    
    /* Chat message styling */
    [data-testid="stChatMessage"] {
        background: rgba(255, 255, 255, 0.05) !important;
    }
    
    /* Input field styling */
    .stTextInput > div > div > input {
        background: rgba(255, 255, 255, 0.1) !important;
        color: #ffffff !important;
        border: 1px solid rgba(102, 126, 234, 0.3) !important;
    }
    
    /* Selectbox styling */
    .stSelectbox > div > div > select {
        background: rgba(255, 255, 255, 0.1) !important;
        color: #ffffff !important;
    }
    
    /* Success messages - Dark theme */
    .stSuccess {
        background: rgba(40, 167, 69, 0.2);
        border-left: 4px solid #28a745;
        color: #90ee90;
        padding: 1rem;
        border-radius: 6px;
    }
    
    /* Error messages - Dark theme */
    .stError {
        background: rgba(220, 53, 69, 0.2);
        border-left: 4px solid #dc3545;
        color: #ff6b6b;
        padding: 1rem;
        border-radius: 6px;
    }
    
    /* Info boxes - Dark theme */
    .stInfo {
        background: rgba(33, 150, 243, 0.2);
        border-left: 4px solid #2196F3;
        color: #81d4fa;
    }
    
    /* Warning messages - Dark theme */
    .stWarning {
        background: rgba(255, 193, 7, 0.2);
        border-left: 4px solid #ffc107;
        color: #ffd54f;
    }
    
    /* Main content area */
    .main-content {
        background: #ffffff;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    
    /* Confidence score styling */
    .confidence-score {
        margin-top: 1rem;
        padding: 0.75rem 1rem;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        border-left: 4px solid;
        font-size: 0.9rem;
    }
    
    .confidence-high {
        border-left-color: #28a745;
        color: #90ee90;
    }
    
    .confidence-medium {
        border-left-color: #ffc107;
        color: #ffd54f;
    }
    
    .confidence-low {
        border-left-color: #dc3545;
        color: #ff6b6b;
    }
    
    /* File item in sidebar */
    .file-item {
        padding: 0.75rem;
        margin: 0.5rem 0;
        background: rgba(102, 126, 234, 0.1);
        border-radius: 8px;
        border: 1px solid rgba(102, 126, 234, 0.3);
        transition: all 0.3s;
    }
    
    .file-item:hover {
        background: rgba(102, 126, 234, 0.2);
        border-color: rgba(102, 126, 234, 0.5);
    }
    
    /* Welcome page improvements */
    .feature-card {
        padding: 1.5rem;
        background: rgba(102, 126, 234, 0.2);
        border-radius: 12px;
        border: 1px solid rgba(102, 126, 234, 0.3);
        transition: all 0.3s;
        height: 100%;
    }
    
    .feature-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        border-color: rgba(102, 126, 234, 0.6);
    }
    
    /* Chat input improvements */
    [data-testid="stChatInput"] {
        background: rgba(255, 255, 255, 0.1) !important;
        border: 1px solid rgba(102, 126, 234, 0.3) !important;
        border-radius: 12px !important;
    }
    
    [data-testid="stChatInput"] textarea {
        background: rgba(255, 255, 255, 0.1) !important;
        color: #ffffff !important;
    }
    
    /* Improved button styling */
    .stButton > button {
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
    }
    </style>
""", unsafe_allow_html=True)


def initialize_session_state():
    """Initialize session state variables."""
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'vectorstore_loaded' not in st.session_state:
        st.session_state.vectorstore_loaded = False
    if 'retriever' not in st.session_state:
        st.session_state.retriever = None
    if 'generator' not in st.session_state:
        st.session_state.generator = None
    if 'explain_like_10' not in st.session_state:
        st.session_state.explain_like_10 = False
    if 'uploaded_files' not in st.session_state:
        st.session_state.uploaded_files = []
    if 'pending_question' not in st.session_state:
        st.session_state.pending_question = None
    if 'selected_doc' not in st.session_state:
        st.session_state.selected_doc = None
    if 'show_upload' not in st.session_state:
        st.session_state.show_upload = True  # Auto-show upload on first load
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'uploaded_file_names' not in st.session_state:
        st.session_state.uploaded_file_names = []
    if 'first_load' not in st.session_state:
        st.session_state.first_load = True
    if 'processed_file_hashes' not in st.session_state:
        st.session_state.processed_file_hashes = set()
    if 'file_uploader_key' not in st.session_state:
        st.session_state.file_uploader_key = 0


def get_api_key():
    """Get API key from config (loaded from Streamlit secrets, .env file, or environment)."""
    from config import get_gemini_api_key
    return get_gemini_api_key()


def load_knowledge_base():
    """Load the existing knowledge base."""
    api_key = get_api_key()
    if not validate_api_key(api_key):
        return False
    
    try:
        embedder = Embedder()
        vectorstore = embedder.load_vectorstore()
        
        retriever = Retriever(vectorstore)
        generator = Generator(retriever)
        
        st.session_state.retriever = retriever
        st.session_state.generator = generator
        st.session_state.vectorstore_loaded = True
        
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        st.error(f"Error loading knowledge base: {str(e)}")
        return False


def process_documents(uploaded_files, progress_bar, status_text):
    """Process uploaded documents and build knowledge base."""
    if not uploaded_files:
        st.warning("Please upload at least one document.")
        return False
    
    api_key = get_api_key()
    if not validate_api_key(api_key):
        st.error("⚠️ GEMINI_API_KEY not found in .env file. Please add it to your .env file.")
        return False
    
    try:
        status_text.text("📥 Saving uploaded files...")
        progress_bar.progress(10)
        
        # Save uploaded files
        saved_files = []
        for uploaded_file in uploaded_files:
            file_path = save_uploaded_file(uploaded_file, DATA_DIR)
            if file_path:
                saved_files.append(file_path)
        
        if not saved_files:
            st.error("Failed to save uploaded files.")
            return False
        
        status_text.text("📄 Loading documents...")
        progress_bar.progress(30)
        
        # Load documents
        all_documents = []
        for file_path in saved_files:
            docs = DocumentLoader.load_document(str(file_path))
            all_documents.extend(docs)
        
        if not all_documents:
            st.error("No content extracted from documents.")
            return False
        
        status_text.text("✂️ Splitting documents into chunks...")
        progress_bar.progress(50)
        
        # Split documents
        splitter = TextSplitter()
        chunks = splitter.split_documents(all_documents)
        
        status_text.text("🔢 Generating embeddings...")
        progress_bar.progress(70)
        
        # Create embeddings and vector store
        embedder = Embedder()
        
        # Check if vectorstore exists
        if VECTORSTORE_INDEX_PATH.exists():
            # Add to existing vectorstore
            embedder.add_documents(chunks)
            embedder.save_vectorstore()
        else:
            # Create new vectorstore
            vectorstore = embedder.create_vectorstore(chunks)
            embedder.save_vectorstore(vectorstore)
        
        status_text.text("✅ Finalizing knowledge base...")
        progress_bar.progress(90)
        
        # Initialize retriever and generator
        vectorstore = embedder.load_vectorstore()
        retriever = Retriever(vectorstore)
        generator = Generator(retriever)
        
        st.session_state.retriever = retriever
        st.session_state.generator = generator
        st.session_state.vectorstore_loaded = True
        
        # Update uploaded files list - get all files from directory
        uploaded_files_list = get_uploaded_files(DATA_DIR)
        st.session_state.uploaded_files = [str(f) for f in uploaded_files_list]
        st.session_state.uploaded_file_names = [f.name for f in uploaded_files_list]
        
        progress_bar.progress(100)
        status_text.text("✅ Complete!")
        
        return True
        
    except Exception as e:
        st.error(f"Error processing documents: {str(e)}")
        logging.error(f"Error processing documents: {str(e)}", exc_info=True)
        return False


def display_chat_message(role: str, content: str):
    """Display a chat message using Streamlit's native chat component."""
    with st.chat_message(role):
        st.markdown(content)


def render_navbar():
    """Render the top navbar."""
    st.markdown('<div class="navbar-title">Welcome to Your Knowledgebase Agent</div>', unsafe_allow_html=True)


def render_start_screen():
    """Render the initial start screen."""
    st.markdown("""
        <div class="start-screen">
            <div class="start-icon">📄</div>
            <div class="start-title">Start New Conversation</div>
            <div class="start-subtitle">Start by uploading a new PDF, accessing your previous uploads, or connecting to your Zotero library.</div>
            <div class="action-buttons">
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1], gap="large")
    
    with col1:
        if st.button("📄 Documents", use_container_width=True, type="primary", help="View and manage your documents"):
            st.session_state.show_upload = True
            st.rerun()
    
    with col2:
        if st.button("⬆️ Upload", use_container_width=True, type="primary", help="Upload new documents to your knowledge base"):
            st.session_state.show_upload = True
            st.rerun()
    
    with col3:
        if st.button("☁️ Zotero", use_container_width=True, type="primary", help="Connect to your Zotero library"):
            st.info("🔜 Zotero integration coming soon!")
    
    st.markdown("</div></div>", unsafe_allow_html=True)


def render_document_preview():
    """Render document preview on the left side."""
    uploaded_files_list = get_uploaded_files(DATA_DIR)
    
    st.markdown('<div class="doc-preview">', unsafe_allow_html=True)
    st.markdown('<div class="doc-preview-header">📚 Documents</div>', unsafe_allow_html=True)
    
    if uploaded_files_list:
        # Show document list
        for file_path in uploaded_files_list:
            file_name = file_path.name
            is_active = st.session_state.selected_doc == str(file_path)
            
            # Highlight active document
            button_style = "primary" if is_active else "secondary"
            if st.button(f"📄 {file_name}", key=f"doc_{file_path.name}", use_container_width=True, type=button_style):
                st.session_state.selected_doc = str(file_path)
                st.rerun()
        
        # Show document content if selected
        if st.session_state.selected_doc:
            st.markdown("---")
            st.markdown('<div class="doc-preview-content">', unsafe_allow_html=True)
            st.markdown('<div class="doc-preview-title">📄 Document Preview</div>', unsafe_allow_html=True)
            try:
                docs = DocumentLoader.load_document(st.session_state.selected_doc)
                # Show first page/chunk preview with better formatting
                if docs:
                    # Combine first few pages for better preview
                    preview_text = ""
                    max_chars = 1500
                    for doc in docs[:3]:  # Show first 3 pages
                        if len(preview_text) + len(doc.page_content) > max_chars:
                            preview_text += doc.page_content[:max_chars - len(preview_text)]
                            preview_text += "\n\n... (content truncated)"
                            break
                        preview_text += doc.page_content + "\n\n---\n\n"
                    
                    if len(preview_text) > max_chars:
                        preview_text = preview_text[:max_chars] + "..."
                    
                    # Display preview text in a styled container (dark theme)
                    import html
                    escaped_text = html.escape(preview_text).replace('\n', '<br>')
                    st.markdown(f'<div style="background: rgba(255, 255, 255, 0.1); padding: 1rem; border-radius: 6px; border: 1px solid rgba(102, 126, 234, 0.3); color: #ffffff; font-size: 0.95rem; line-height: 1.6; max-height: 400px; overflow-y: auto; word-wrap: break-word;">{escaped_text}</div>', unsafe_allow_html=True)
                    st.caption(f"📊 Showing preview from {len(docs)} page(s) - {Path(st.session_state.selected_doc).name}")
            except Exception as e:
                st.error(f"❌ Error loading document: {str(e)}")
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("📄 No documents uploaded yet. Click 'Upload' to add documents.")
    
    st.markdown("</div>", unsafe_allow_html=True)


def render_chat_interface():
    """Render chat interface - answers only from uploaded files."""
    # Ensure knowledge base is loaded
    if not st.session_state.vectorstore_loaded:
        if VECTORSTORE_INDEX_PATH.exists():
            api_key = get_api_key()
            if validate_api_key(api_key):
                if load_knowledge_base():
                    st.rerun()
        else:
            st.warning("⚠️ Please upload documents first to build the knowledge base.")
            return
    
    # Display chat history with smooth animation
    st.markdown('<div class="smooth-slide">', unsafe_allow_html=True)
    for message in st.session_state.chat_history:
        display_chat_message(message["role"], message["content"])
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Handle pending question from follow-up - process it immediately
    if st.session_state.pending_question:
        question_to_process = st.session_state.pending_question
        st.session_state.pending_question = None  # Clear it immediately
        
        # Display user question
        display_chat_message("user", question_to_process)
        st.session_state.chat_history.append({
            "role": "user",
            "content": question_to_process
        })
        
        # Generate answer
        if st.session_state.generator:
            with st.spinner("🤔 Analyzing your question and searching knowledge base..."):
                result = st.session_state.generator.generate_answer(
                    question=question_to_process,
                    explain_like_10=st.session_state.explain_like_10
                )
            
            # Display answer with confidence score
            with st.chat_message("assistant"):
                st.markdown(f'<div class="smooth-fade">{result["answer"]}</div>', unsafe_allow_html=True)
                
                # Display confidence score
                confidence_score = result.get('confidence_score', 0.0)
                confidence_percent = int(confidence_score * 100)
                confidence_color = "#28a745" if confidence_score > 0.7 else "#ffc107" if confidence_score > 0.4 else "#dc3545"
                st.markdown(f'<div style="margin-top: 1rem; padding: 0.75rem; background: rgba(255, 255, 255, 0.1); border-radius: 8px; border-left: 4px solid {confidence_color};">'
                           f'<strong>Confidence Score:</strong> <span style="color: {confidence_color};">{confidence_percent}%</span> '
                           f'(Based on retrieval similarity from uploaded documents)</div>', unsafe_allow_html=True)
                
                # Display combined source information
                if result['sources']:
                    st.markdown("---")
                    st.markdown("### 📚 Source Information")
                    # Combine all sources into one section
                    all_sources_text = []
                    all_files = []
                    for source in result['sources']:
                        all_files.append(source['source'])
                        all_sources_text.append(f"**From {source['source']}:**\n{source['content_preview']}")
                    
                    with st.expander(f"📄 Sources: {', '.join(set(all_files))}", expanded=False):
                        st.markdown("\n\n---\n\n".join(all_sources_text))
                        st.markdown(f"\n**Total Sources Used:** {len(result['sources'])}")
            
            # Add assistant response to history
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": result['answer'],
                "confidence_score": confidence_score
            })
        
        # Rerun to show the response
        st.rerun()
    
    # Chat input
    user_question = st.chat_input("Ask a question about your uploaded documents...")
    
    # Process question from input
    if user_question:
        # Ensure knowledge base is loaded before processing
        if not st.session_state.generator:
            if VECTORSTORE_INDEX_PATH.exists():
                api_key = get_api_key()
                if validate_api_key(api_key):
                    if load_knowledge_base():
                        st.rerun()
            else:
                st.error("❌ Knowledge base not found. Please upload documents first.")
                return
        
        # Display user question
        display_chat_message("user", user_question)
        st.session_state.chat_history.append({
            "role": "user",
            "content": user_question
        })
        
        # Generate answer
        if st.session_state.generator:
            with st.spinner("🤔 Analyzing your question and searching knowledge base..."):
                result = st.session_state.generator.generate_answer(
                    question=user_question,
                    explain_like_10=st.session_state.explain_like_10
                )
            
            # Display answer with confidence score
            with st.chat_message("assistant"):
                st.markdown(f'<div class="smooth-fade">{result["answer"]}</div>', unsafe_allow_html=True)
                
                # Display confidence score
                confidence_score = result.get('confidence_score', 0.0)
                confidence_percent = int(confidence_score * 100)
                confidence_color = "#28a745" if confidence_score > 0.7 else "#ffc107" if confidence_score > 0.4 else "#dc3545"
                st.markdown(f'<div style="margin-top: 1rem; padding: 0.75rem; background: rgba(255, 255, 255, 0.1); border-radius: 8px; border-left: 4px solid {confidence_color};">'
                           f'<strong>Confidence Score:</strong> <span style="color: {confidence_color};">{confidence_percent}%</span> '
                           f'(Based on retrieval similarity from uploaded documents)</div>', unsafe_allow_html=True)
                
                # Display combined source information
                if result['sources']:
                    st.markdown("---")
                    st.markdown("### 📚 Source Information")
                    # Combine all sources into one section
                    all_sources_text = []
                    all_files = []
                    for source in result['sources']:
                        all_files.append(source['source'])
                        all_sources_text.append(f"**From {source['source']}:**\n{source['content_preview']}")
                    
                    with st.expander(f"📄 Sources: {', '.join(set(all_files))}", expanded=False):
                        st.markdown("\n\n---\n\n".join(all_sources_text))
                        st.markdown(f"\n**Total Sources Used:** {len(result['sources'])}")
            
            # Add assistant response to history
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": result['answer'],
                "confidence_score": confidence_score
            })
        else:
            st.error("❌ Knowledge base not loaded. Please upload documents first.")


def render_welcome_page():
    """Render welcome page with instructions and features."""
    st.markdown("""
    <div style="padding: 2rem; background: rgba(255, 255, 255, 0.05); border-radius: 16px; border: 1px solid rgba(102, 126, 234, 0.3); margin-bottom: 2rem;">
        <h1 style="text-align: center; color: #ffffff; margin-bottom: 1rem;">Welcome to Your Knowledgebase Agent</h1>
        <p style="text-align: center; color: rgba(255, 255, 255, 0.8); font-size: 1.1rem; margin-bottom: 2rem;">
            Upload any document and ask questions about it. Get accurate answers from your documents only.
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Features section
    st.markdown("### ✨ Key Features")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("""
        <div style="padding: 1.5rem; background: rgba(102, 126, 234, 0.2); border-radius: 12px; border: 1px solid rgba(102, 126, 234, 0.3);">
            <h3 style="color: #ffffff;">🚫 No Hallucination</h3>
            <p style="color: rgba(255, 255, 255, 0.8);">Answers are generated strictly from your uploaded documents. No external knowledge is used.</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
        <div style="padding: 1.5rem; background: rgba(102, 126, 234, 0.2); border-radius: 12px; border: 1px solid rgba(102, 126, 234, 0.3);">
            <h3 style="color: #ffffff;">📄 Complete Responses</h3>
            <p style="color: rgba(255, 255, 255, 0.8);">Get comprehensive answers extracted directly from your documents in point-wise format.</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown("""
        <div style="padding: 1.5rem; background: rgba(102, 126, 234, 0.2); border-radius: 12px; border: 1px solid rgba(102, 126, 234, 0.3);">
            <h3 style="color: #ffffff;">✅ Accurate</h3>
            <p style="color: rgba(255, 255, 255, 0.8);">Every answer includes a confidence score based on retrieval similarity from your documents.</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Real-world applications
    st.markdown("### 🌟 Real-World Applications")
    st.markdown("""
    <div style="padding: 1.5rem; background: rgba(255, 255, 255, 0.05); border-radius: 12px; border: 1px solid rgba(102, 126, 234, 0.3);">
        <ul style="color: rgba(255, 255, 255, 0.9); line-height: 2;">
            <li><strong>Ask Your PDF:</strong> Upload research papers, reports, or manuals and ask specific questions about their content</li>
            <li><strong>Document Search:</strong> Quickly find specific information from large documents without reading through everything</li>
            <li><strong>Knowledge Extraction:</strong> Extract key points, summaries, and insights from your document collection</li>
            <li><strong>Q&A from Documents:</strong> Get instant answers to questions about contracts, policies, or any text-based documents</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Instructions
    st.markdown("### 📖 How to Use")
    st.markdown("""
    <div style="padding: 1.5rem; background: rgba(255, 255, 255, 0.05); border-radius: 12px; border: 1px solid rgba(102, 126, 234, 0.3);">
        <ol style="color: rgba(255, 255, 255, 0.9); line-height: 2;">
            <li><strong>Upload Documents:</strong> Use the file browser in the sidebar to upload PDF, DOCX, DOC, or TXT files</li>
            <li><strong>Wait for Processing:</strong> The system will automatically process your documents and build a knowledge base</li>
            <li><strong>Ask Questions:</strong> Type your question in the chat box below. The system will search your uploaded documents and provide answers</li>
            <li><strong>View Confidence:</strong> Each answer includes a confidence score showing how well it matches your documents</li>
            <li><strong>Check Sources:</strong> Expand the sources section to see which parts of your documents were used</li>
        </ol>
    </div>
    """, unsafe_allow_html=True)


def delete_file_from_kb(file_name: str):
    """Delete a file from the knowledge base and update vectorstore."""
    try:
        file_path = DATA_DIR / file_name
        if file_path.exists():
            file_path.unlink()
            
            # Remove from session state
            if file_name in st.session_state.uploaded_file_names:
                st.session_state.uploaded_file_names.remove(file_name)
            
            # Rebuild vectorstore without the deleted file
            uploaded_files_list = get_uploaded_files(DATA_DIR)
            if uploaded_files_list:
                # Clear existing vectorstore
                clear_vectorstore(VECTORSTORE_DIR)
                
                # Rebuild with remaining files
                all_documents = []
                for f_path in uploaded_files_list:
                    docs = DocumentLoader.load_document(str(f_path))
                    all_documents.extend(docs)
                
                if all_documents:
                    splitter = TextSplitter()
                    chunks = splitter.split_documents(all_documents)
                    
                    embedder = Embedder()
                    vectorstore = embedder.create_vectorstore(chunks)
                    embedder.save_vectorstore(vectorstore)
                    
                    # Reload knowledge base
                    vectorstore = embedder.load_vectorstore()
                    retriever = Retriever(vectorstore)
                    generator = Generator(retriever)
                    
                    st.session_state.retriever = retriever
                    st.session_state.generator = generator
                    st.session_state.vectorstore_loaded = True
            else:
                # No files left, clear everything
                clear_vectorstore(VECTORSTORE_DIR)
                st.session_state.vectorstore_loaded = False
                st.session_state.retriever = None
                st.session_state.generator = None
            
            return True
        return False
    except Exception as e:
        st.error(f"Error deleting file: {str(e)}")
        return False


def main():
    """Main application function."""
    initialize_session_state()
    
    # Render navbar
    render_navbar()
    
    # Sidebar - Always visible with file uploader and file database
    with st.sidebar:
        st.markdown("### 📁 Browse Files")
        
        # File uploader - always visible (use key to reset after processing)
        uploaded_files = st.file_uploader(
            "Upload Documents",
            type=['pdf', 'docx', 'doc', 'txt'],
            accept_multiple_files=True,
            help="Upload PDF, DOCX, DOC, or TXT files to build your knowledge base",
            label_visibility="visible",
            key=f"file_uploader_{st.session_state.file_uploader_key}"
        )
        
        # Auto-process when files are uploaded
        if uploaded_files and not st.session_state.processing:
            # Create unique identifier for this batch of files
            file_identifiers = tuple(sorted([f"{f.name}_{f.size}" for f in uploaded_files]))
            file_batch_hash = hashlib.md5(str(file_identifiers).encode()).hexdigest()
            
            # Check if this exact batch has been processed
            if file_batch_hash not in st.session_state.processed_file_hashes:
                # Also check if files already exist in data directory
                existing_files = {f.name for f in get_uploaded_files(DATA_DIR)}
                uploaded_file_names = {f.name for f in uploaded_files}
                files_already_exist = uploaded_file_names.issubset(existing_files)
                
                # Only process if files don't already exist
                if not files_already_exist:
                    st.session_state.processing = True
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    success = process_documents(uploaded_files, progress_bar, status_text)
                    
                    if success:
                        # Mark this batch as processed
                        st.session_state.processed_file_hashes.add(file_batch_hash)
                        # Reset file uploader by changing key to clear it
                        st.session_state.file_uploader_key += 1
                        st.success("✅ Documents processed successfully!")
                        st.session_state.processing = False
                        st.session_state.first_load = False
                        # Rerun to clear the file uploader
                        st.rerun()
                    else:
                        st.session_state.processing = False
                        st.session_state.first_load = False
                else:
                    # Files already exist, mark as processed and reset uploader
                    st.session_state.processed_file_hashes.add(file_batch_hash)
                    st.session_state.file_uploader_key += 1
                    st.info("ℹ️ These files are already in the knowledge base.")
                    st.rerun()
        
        st.markdown("---")
        
        # File Database - Show uploaded files with delete option
        st.markdown("### 📚 Knowledge Base Files")
        uploaded_files_list = get_uploaded_files(DATA_DIR)
        
        if uploaded_files_list:
            # Update session state with current files
            current_file_names = [f.name for f in uploaded_files_list]
            st.session_state.uploaded_file_names = current_file_names
            
            for idx, file_path in enumerate(uploaded_files_list):
                file_name = file_path.name
                file_size = file_path.stat().st_size if file_path.exists() else 0
                
                # File item with delete button
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"📄 **{file_name}**")
                    st.caption(f"{format_file_size(file_size)}")
                with col2:
                    if st.button("🗑️", key=f"delete_{idx}", help=f"Delete {file_name}"):
                        if delete_file_from_kb(file_name):
                            st.success(f"Deleted {file_name}")
                            st.rerun()
                st.markdown("---")
        else:
            st.info("No files uploaded yet")
            st.session_state.uploaded_file_names = []
        
        st.markdown("---")
        
        # Settings
        st.markdown("### ⚙️ Settings")
        st.session_state.explain_like_10 = st.toggle(
            "🧒 Explain Like I'm 10",
            value=st.session_state.explain_like_10,
            help="Simplify answers for easier understanding"
        )
        
        # Status indicator
        st.markdown("---")
        st.markdown("### 📊 Status")
        if st.session_state.vectorstore_loaded:
            st.success("✅ Knowledge Base Active")
            uploaded_files_list = get_uploaded_files(DATA_DIR)
            st.metric("📄 Documents", len(uploaded_files_list))
            st.metric("💬 Messages", len(st.session_state.chat_history))
        else:
            st.warning("⚠️ No Knowledge Base")
    
    # Main content area
    # Try to load existing knowledge base
    if not st.session_state.vectorstore_loaded:
        if VECTORSTORE_INDEX_PATH.exists():
            api_key = get_api_key()
            if validate_api_key(api_key):
                if load_knowledge_base():
                    st.rerun()
    
    # Show welcome page if no knowledge base, otherwise show chat interface
    if not st.session_state.vectorstore_loaded:
        render_welcome_page()
    else:
        # Show chat interface - answers only from uploaded files
        render_chat_interface()


if __name__ == "__main__":
    main()
