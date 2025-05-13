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

from langchain.memory import ConversationBufferMemory

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

# Initialize the LLM and memory
llm = OllamaLLM(model="llama3")
memory = ConversationBufferMemory(return_messages=True)


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
mode = 'simple'
print("\n--- Welcome to the Aquaponics Chatbot! Type /help for commands. ---\n")

while True:
    query = input("Query: ").strip()
    if not query:
        continue

    # --- Command handling ---
    lowered = query.lower()
    if lowered in ('/exit', 'exit', 'quit'):
        print('Bye!')
        break
    if lowered in ('/clear', 'clear'):
        memory.clear()
        print('Memory cleared.')
        continue
    if lowered in ('/simple', '/s'):
        mode = 'simple'
        print('--> Switched to SIMPLE mode.')
        continue
    if lowered in ('/advanced', '/a'):
        mode = 'advanced'
        print('--> Switched to ADVANCED mode.')
        continue
    if lowered in ('/help', 'help'):
        print('/simple (/s)   Change to simple-mode \n/advanced (/a) Change to advanced-mode \n/clear         Clear context\n/exit          Leave')
        continue

    # --- Retrieval ---
    k = 3 if mode == 'simple' else 6
    retrieved_docs = vectorstore.similarity_search(query, k=k)
    print(f"\n--- Top {k} Matching Documents (mode: {mode}) ---\n")
    for i, doc in enumerate(retrieved_docs, 1):
        source = doc.metadata.get('source', 'Unknown source')
        print(f"[{i}] Source: {source}\nContent Preview: {doc.page_content[:300]}...\n")

    context = "\n\n".join(doc.page_content for doc in retrieved_docs)

    history = memory.chat_memory.messages
    history_text = ""
    for msg in history:
        role = 'You' if msg.type == 'human' else 'Bot'
        history_text += f"{role}: {msg.content}\n"

    prompt = build_prompt(mode, context, history_text, query)

    response = llm.invoke(prompt)

    print('\nAnswer:\n' + response + '\n')

    # store in memory
    memory.chat_memory.add_user_message(query)
    memory.chat_memory.add_ai_message(response)

    copied = is_exact_copy(response, retrieved_docs)
    print('result:', 'from the paper' if copied else 'good answer')
