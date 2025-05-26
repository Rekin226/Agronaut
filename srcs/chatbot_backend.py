import pandas as pd
import os
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
from langchain.memory import ConversationBufferMemory

# Set USER_AGENT to avoid warnings
os.environ.setdefault('USER_AGENT', 'AquaponicsChatbot/1.0')

def is_exact_copy(response: str, documents: list) -> bool:
    for doc in documents:
        if response.strip() in doc.page_content:
            return True
    return False

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
_User_: "Explain what aquaponics is."
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
- Ask for confirmation: "Is this correct before I proceed?"

3. Deliver the graduate-level answer  
- Provide a deeply reasoned solution, as an aquaponics specialist.   
- If mathematical derivations or mass-balance calculations are needed, show the steps clearly and state any assumptions.  
- Use precise technical language, but keep sentences readable.

4. Anticipate & pre-answer one follow-up 
- End with a brief Q&A section: "Possible follow-up: … / Short answer: …".

<< Output format >>
- Use short paragraphs or bullet points where that improves clarity.  
- Prefix each main section with a clear heading (e.g., "Clarifying Questions", "Answer").  
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

class AquaponicsChatbot:
    def __init__(self, urls_file=None):
        self.mode = 'simple'
        self.memory = ConversationBufferMemory(return_messages=True)
        self.vectorstore = None
        self.llm = OllamaLLM(model="llama3")
        
        # Fix file path - look in parent directory of srcs
        if urls_file is None:
            urls_file = os.path.join(os.path.dirname(__file__), '..', 'urls.txt')
        
        self._initialize_vectorstore(urls_file)
    
    def _initialize_vectorstore(self, urls_file):
        """Initialize the vector store with documents from URLs"""
        requests_cache.install_cache('web_cache', expire_after=86400)
        
        try:
            urls = self._load_urls_from_file(urls_file)
        except FileNotFoundError:
            print(f"Warning: URLs file not found at {urls_file}")
            print("Creating a minimal vectorstore with sample data...")
            self._create_minimal_vectorstore()
            return
        
        documents = self._load_documents(urls)
        
        if not documents:
            print("Warning: No documents loaded successfully")
            print("Creating a minimal vectorstore with sample data...")
            self._create_minimal_vectorstore()
            return
            
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        chunks = text_splitter.split_documents(documents)
        
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
        self.vectorstore = FAISS.from_documents(chunks, embeddings)
    
    def _create_minimal_vectorstore(self):
        """Create a minimal vectorstore with basic aquaponics information"""
        from langchain.schema import Document
        
        sample_docs = [
            Document(
                page_content="Aquaponics is a food production system that combines aquaculture (raising fish) with hydroponics (growing plants in water). The fish waste provides nutrients for the plants, while the plants help filter the water for the fish.",
                metadata={"source": "basic_info"}
            ),
            Document(
                page_content="Common fish used in aquaponics include tilapia, trout, and catfish. These fish are hardy and can tolerate varying water conditions.",
                metadata={"source": "basic_info"}
            ),
            Document(
                page_content="Popular plants for aquaponics include lettuce, herbs, tomatoes, and peppers. Leafy greens tend to work best for beginners.",
                metadata={"source": "basic_info"}
            )
        ]
        
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
        self.vectorstore = FAISS.from_documents(sample_docs, embeddings)

    def _load_urls_from_file(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"URLs file not found: {file_path}")
        
        with open(file_path, 'r') as file:
            urls = file.read().splitlines()
            # Filter out empty lines and comments
            return [url.strip() for url in urls if url.strip() and not url.strip().startswith('#')]
    
    def _load_documents(self, urls):
        documents = []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_url = {executor.submit(self._load_web_page, url): url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    if result:
                        documents.extend(result)
                except Exception as e:
                    print(f"Error loading {url}: {e}")
        return documents
    
    def _load_web_page(self, url):
        try:
            # Skip problematic DOI URLs that cause API errors
            if 'doi.org' in url:
                print(f"Skipping DOI URL (potential API issues): {url}")
                return None
                
            loader = WebBaseLoader(url)
            return loader.load()
        except Exception as e:
            print(f"Error loading {url}: {e}")
            return None
    
    def set_mode(self, mode):
        """Set chatbot mode: 'simple' or 'advanced'"""
        self.mode = mode
        return f"Switched to {mode.upper()} mode"
    
    def clear_memory(self):
        """Clear conversation memory"""
        self.memory.clear()
        return "Memory cleared"
    
    def get_response(self, query):
        """Get chatbot response for a query"""
        if not self.vectorstore:
            return {
                'response': "Sorry, the knowledge base is not available.",
                'sources': [],
                'is_copy': False
            }
            
        k = 3 if self.mode == 'simple' else 6
        retrieved_docs = self.vectorstore.similarity_search(query, k=k)
        
        context = "\n\n".join(doc.page_content for doc in retrieved_docs)
        
        # Get chat history
        history = self.memory.chat_memory.messages
        history_text = ""
        for msg in history:
            role = 'You' if msg.type == 'human' else 'Bot'
            history_text += f"{role}: {msg.content}\n"
        
        prompt = build_prompt(self.mode, context, history_text, query)
        response = self.llm.invoke(prompt)
        
        # Store in memory
        self.memory.chat_memory.add_user_message(query)
        self.memory.chat_memory.add_ai_message(response)
        
        return {
            'response': response,
            'sources': [{'source': doc.metadata.get('source', 'Unknown'), 
                        'preview': doc.page_content[:300]} for doc in retrieved_docs],
            'is_copy': is_exact_copy(response, retrieved_docs)
        }