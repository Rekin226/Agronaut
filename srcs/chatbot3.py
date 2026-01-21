# Import necessary packages
import pandas as pd
from langchain_ollama.llms import OllamaLLM
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import concurrent.futures
import requests
from bs4 import BeautifulSoup
import requests_cache
from sentence_transformers import SentenceTransformer
import streamlit as st
import os

from langchain.memory import ConversationBufferMemory
from markdown import markdown as md_to_html

def is_exact_copy(response: str, documents: list) -> bool:
    for doc in documents:
        if response.strip() in doc.page_content:
            return True
    return False

# Enable caching
requests_cache.install_cache('web_cache', expire_after=86400)  # Cache expires after 1 day

# Function to load a single web page
def load_web_page(url):
    try:
        print(f"Loading URL: {url}")
        loader = WebBaseLoader(url)
        documents = loader.load()
        return documents
    except requests.exceptions.RequestException as e:
        print(f"Error loading {url}: {e}")
        return None

# Function to extract text from a URL
def extract_text_from_url(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text()
        return text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

# Load URLs from a file
def load_urls_from_file(file_path):
    with open(file_path, 'r') as file:
        urls = file.read().splitlines()
    return urls

# Initialize resources with caching
@st.cache_resource
def initialize_chatbot():
    """Initialize and cache the chatbot resources."""
    urls = load_urls_from_file('urls.txt')
    
    # Load all web pages
    documents = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_url = {executor.submit(load_web_page, url): url for url in urls}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                result = future.result()
                if result:
                    documents.extend(result)
            except Exception as e:
                print(f"Error loading {url}: {e}")
    
    # Split documents
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = text_splitter.split_documents(documents)
    
    # Create embeddings
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
    
    # Create FAISS vector store
    vectorstore = FAISS.from_documents(chunks, embeddings)
    
    # Initialize the LLM
    llm = OllamaLLM(model="llama3")
    
    return vectorstore, llm


# === Prompt templates and builder ===
"""
create a function that can identify the mode of the prompt
and return the appropriate prompt template
"""

def build_simple_prompt(context: str, history_text: str, query: str) -> str:
    """Return the original (simple) prompt string."""
    return f"""
You are an aquaponics subject-matter expert and consultant with extensive experience advising both individuals and enterprises.

Core Directives:
1. Be helpful, honest, and harmless.  
2. Always prioritize the user's needs unless they conflict with safety rules.  
3. If a request is ambiguous or underspecified, politely ask a clarifying question.   
4. You may reason step-by-step internally but never expose your thought process. Only output clear final answers.

Response Style:
- Friendly, professional tone — concise but complete.  
- Use short paragraphs, bullet points, or tables where clearer.  
- Mirror the user's vocabulary and formality; avoid unnecessary formality.

Interaction Rules:
- Treat conversations as stateful across turns.
- Conclude answers with a short follow-up question like "Would you like to dive deeper into any part of this?" unless the user's query is fully answered.
- If the user's question is informational and fully answered, no follow-up is necessary.

Formatting Conventions:
- Begin each response with a brief, one-sentence summary if the question is non-trivial.
- When answering, do not reuse wording from the example; rephrase in your own words.
- Then add more details in bullet points or short paragraphs.
- Any clips within <<< Example >>> to <<< End of example >>> should not appear in the output.

<<< Example >>> (format only — DO NOT copy text):
_User_: “Explain what aquaponics is.”
_Assistant_:
< One-sentence summary >

- < Key point 1 >
- < Key point 2 >
- < Key point 3 >

Would you like to know more?
<<< End of example >>>

the End:
Stop generating after answering and optionally offering further help. Do not invent new user queries or extend the conversation unprompted.

Context:
{context}

Chat History:
{history_text}

Question: {query}
Answer:
""".strip()

# Advanced prompt template
ADVANCED_PROMPT = """
You are an aquaponics expert. Your primary goal is to uncover the user's real problem through a short, structured dialogue before delivering the full answer.

Conversation protocol

1. Clarify (max 5 questions)  
- Ask up to five concise, targeted questions that will let you pinpoint the user's objective, constraints, or missing data.  
- If the user's request is already crystal-clear, skip to step 3.

2. Confirm understanding 
- Summarize the user's problem in 1-2 sentences.  
- Ask for confirmation: “Is this correct before I proceed?”

3. Deliver the graduate-level answer  
- Provide a deeply reasoned solution, as an aquaponics specialist.   
- If mathematical derivations or mass-balance calculations are needed, show the steps clearly and state any assumptions.  
- Use precise technical language, but keep sentences readable.

4. Anticipate & pre-answer one follow-up 
- End with a brief Q&A section: “Possible follow-up: … / Short answer: …”.

<< Output format >>
- Use short paragraphs or bullet points where that improves clarity.  
- Prefix each main section with a clear heading (e.g., “Clarifying Questions”, “Answer”).  
- Keep the overall tone professional yet conversational.

Begin.

Context:
{context}

Chat History:
{history_text}

Question: {query}
Answer:
""".strip()

def build_prompt(mode: str, context: str, history_text: str, query: str) -> str:
    """Return prompt according to current mode."""
    if mode == 'advanced':
        return ADVANCED_PROMPT.format(context=context, history_text=history_text, query=query)
    # default simple
    return build_simple_prompt(context, history_text, query)

# === Chatbot loop with dual-mode ===
def main():
    # Page configuration
    st.set_page_config(
        page_title="Aquaponics Chatbot",
        page_icon="🐟",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Custom CSS for better styling
    st.markdown("""
        <style>
        .main {
            background-color: #0f172a;
            color: #f4f4f4;
        }
        .chat-bubble {
            border-radius: 16px;
            padding: 16px 18px;
            margin: 10px 0;
            box-shadow: 0 10px 25px rgba(15, 23, 42, 0.25);
            max-width: 900px;
            width: 100%;
        }
        .chat-bubble p {
            margin-bottom: 0.6rem;
            color: #0f172a !important;
        }
        .chat-bubble ul {
            margin-left: 1.2rem;
            color: #0f172a;
        }
        .chat-bubble.user {
            background: linear-gradient(135deg, #ffe7d9, #ffd0c2);
            border: 1px solid #ffb89c;
        }
        .chat-bubble.bot {
            background: linear-gradient(135deg, #e2ffe5, #c9f6d0);
            border: 1px solid #9fd5ab;
        }
        .chat-row {
            display: flex;
            justify-content: flex-start;
        }
        .chat-row.user {
            justify-content: flex-end;
        }
        .stMarkdown {
            color: #f4f4f4;
        }
        .css-1d391kg {
            padding: 2rem 1rem;
        }
        .subtitle {
            font-size: 1.15rem;
            color: #ffffff;
            margin-bottom: 2rem;
        }
        </style>
    """, unsafe_allow_html=True)

    # Header
    st.title("🐟 Aquaponics Expert Chatbot")
    st.markdown('<p class="subtitle">Your AI-powered aquaponics consultant</p>', unsafe_allow_html=True)
    
    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "memory" not in st.session_state:
        st.session_state.memory = ConversationBufferMemory(return_messages=True)
    if "mode" not in st.session_state:
        st.session_state.mode = "simple"
    if "show_sources" not in st.session_state:
        st.session_state.show_sources = False
    
    # Initialize chatbot resources
    with st.spinner("Loading chatbot resources..."):
        vectorstore, llm = initialize_chatbot()
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # Mode selection
        st.subheader("Response Mode")
        new_mode = st.radio(
            "Select mode:",
            ["simple", "advanced"],
            index=0 if st.session_state.mode == "simple" else 1,
            help="Simple mode: Quick, concise answers\nAdvanced mode: In-depth analysis with clarifying questions"
        )
        
        if new_mode != st.session_state.mode:
            st.session_state.mode = new_mode
            st.success(f"✅ Switched to {new_mode.upper()} mode")
        
        st.divider()
        
        # Show sources toggle
        st.subheader("Display Options")
        st.session_state.show_sources = st.checkbox(
            "Show source documents",
            value=st.session_state.show_sources,
            help="Display the documents used to generate responses"
        )
        
        st.divider()
        
        # Clear conversation button
        if st.button("🗑️ Clear Conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.memory.clear()
            st.success("Conversation cleared!")
            st.rerun()
        
        st.divider()
        
        # Help section
        with st.expander("ℹ️ Help & Commands"):
            st.markdown("""
            **How to use:**
            - Type your question about aquaponics
            - Choose between simple or advanced mode
            - View source documents for transparency
            
            **Modes:**
            - **Simple**: Quick, concise answers
            - **Advanced**: Detailed analysis with clarifying questions
            """)
        
        # Status
        st.divider()
        st.caption(f"🟢 Mode: **{st.session_state.mode.upper()}**")
        st.caption(f"💬 Messages: **{len(st.session_state.messages)}**")
    
    # Main chat interface
    chat_container = st.container()

    def render_saved_message(message: dict) -> None:
        """Render a chat bubble with role-specific styling."""
        role = message["role"]
        bubble_class = "user" if role == "user" else "bot"
        avatar = "🧑" if role == "user" else "🌱"
        content_html = md_to_html(message["content"])
        with st.chat_message(role, avatar=avatar):
            st.markdown(
                f'<div class="chat-bubble {bubble_class}">{content_html}</div>',
                unsafe_allow_html=True
            )
            if (
                st.session_state.show_sources
                and role == "assistant"
                and "sources" in message
            ):
                with st.expander("📚 View Sources"):
                    for i, doc in enumerate(message["sources"], 1):
                        st.markdown(f"**[{i}]** {doc['source']}")
                        st.text(doc['preview'])
                        st.divider()

    # Display chat messages
    with chat_container:
        for message in st.session_state.messages:
            render_saved_message(message)
    
    # Chat input
    if query := st.chat_input("Ask me anything about aquaponics..."):
        # Add user message to chat history and display it
        st.session_state.messages.append({"role": "user", "content": query})
        render_saved_message(st.session_state.messages[-1])

        # Generate response
        with st.spinner("Thinking..."):
            # Retrieval
            k = 3 if st.session_state.mode == 'simple' else 6
            retrieved_docs = vectorstore.similarity_search(query, k=k)

            context = "\n\n".join(doc.page_content for doc in retrieved_docs)

            # Build history text
            history = st.session_state.memory.chat_memory.messages
            history_text = ""
            for msg in history:
                role = 'You' if msg.type == 'human' else 'Bot'
                history_text += f"{role}: {msg.content}\n"

            # Build prompt
            prompt = build_prompt(st.session_state.mode, context, history_text, query)

            # Get response from LLM
            response = llm.invoke(prompt)

        # Store in memory
        st.session_state.memory.chat_memory.add_user_message(query)
        st.session_state.memory.chat_memory.add_ai_message(response)

        # Prepare sources for display
        sources = []
        for i, doc in enumerate(retrieved_docs, 1):
            source = doc.metadata.get('source', 'Unknown source')
            preview = doc.page_content[:300] + "..."
            sources.append({"source": source, "preview": preview})

        # Add assistant message to chat history and render it
        assistant_message = {
            "role": "assistant",
            "content": response,
            "sources": sources
        }
        st.session_state.messages.append(assistant_message)
        render_saved_message(assistant_message)

if __name__ == "__main__":
    main()
