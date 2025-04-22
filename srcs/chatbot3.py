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
text_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
docs = text_splitter.split_documents(documents)

# Create embeddings
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")

# Create FAISS vector store
vectorstore = FAISS.from_documents(docs, embeddings)
# similarity_search
query = "How does a biofilter work in aquaponics?"
docs = vectorstore.similarity_search(query, k=3)
print("\n--- Top 3 Matching Documents ---\n")
for i, doc in enumerate(docs, 1):
    print(f"[{i}] {doc.page_content[:300]}...\n")

# Initialize the LLM and memory
llm = OllamaLLM(model="llama3")
memory = ConversationBufferMemory(return_messages=True)

# Chatbot loop
print("\n--- Welcome to the Aquaponics Chatbot! ---\n")

while True:
    query = input("Enter your query (you can choice to type 'exit' to quit or type 'clear' to clear the history): ")
    if query.lower() == 'exit':
        print("Bye!")
        break 
    if query.lower() == 'clear':
        memory.clear()
        print("Memory cleared.")
        continue

    retrieved_docs = vectorstore.similarity_search(query, k=3)
    context = "\n\n".join([doc.page_content for doc in retrieved_docs])

    history = memory.chat_memory.messages
    history_text = ""
    for msg in history:
        role = "You" if msg.type == "human" else "Bot"
        history_text += f"{role}: {msg.content}\n"

    prompt = f"""
You are a subject matter expert and aquaponics consultant with extensive experience advising both individuals and businesses on designing, implementing, and managing aquaponics systems. 
Output brefly and concisely, and provide a clear and informative answer to the user's question.
When a user asks a question, check if the user is satisfied with the answer. If yes, provide the keyword "Y". If no,  provide the keyword "N":
- Check if the user is satisfied with the answer, if not, ask for more details.
- If the user is satisfied, provide a clear and informative answer to the user's question.
Maintain a friendly, professional, and consultative tone throughout.

Context:
{context}

Chat History:
{history_text}

Question: {query}
Answer:
""".strip()

    response = llm.invoke(prompt)
    print("\nAnswer:\n" + "-"*50 + f"\n{response}\n" + "-"*50 + "\n")

    memory.chat_memory.add_user_message(query)
    memory.chat_memory.add_ai_message(response)

    copied = is_exact_copy(response, retrieved_docs)
    print("result:", "from the paper" if copied else "good answer")