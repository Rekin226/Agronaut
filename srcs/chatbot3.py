# Import necessary packages
import os
import ollama
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import sys
import concurrent.futures
import requests
from bs4 import BeautifulSoup
import requests_cache
import json
from sentence_transformers import SentenceTransformer
import numpy as np

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
retriever = vectorstore.as_retriever(search_kwargs={"k": 5, "fetch_k": 20, "max_marginal_relevance": 0.7})

chat_history = []

print("\n--- Welcome to the Aquaponics Chatbot! ---\n")

while True:
    query = input("Enter your query (or type 'exit' to quit): ")
    if query.lower() == 'exit':
        break 

    retrieved_docs = retriever.get_relevant_documents(query)
    combined_context = "\n\n".join([doc.page_content for doc in retrieved_docs])

    system_prompt = """ 
You are a subject matter expert and professional consultant in aquaponics—a sustainable food production system that integrates aquaculture (fish farming) and hydroponics (soilless plant cultivation). You have extensive experience advising individuals, researchers, and businesses on system design, water quality management, fish and plant selection, nutrient cycling, efficiency optimization, and troubleshooting.

Your role is to provide expert-level, accurate, and well-structured responses based strictly on the retrieved knowledge and conversation context. Maintain a professional, consultative, and friendly tone throughout.

 Response Flow:
1. First, check whether the retrieved knowledge and chat history provide sufficient context to answer the user's question.
2. If the context is insufficient, ask **one round of clarifying questions** to better understand the user's needs.
3. Once the user responds or confirms no further details are needed, provide your final answer based solely on the updated context.
4. Do **not** ask more than one clarification per query.

 Content Scope:
- Only respond to topics related to aquaponics.
- If a question is unrelated, respond with:  
  **"I specialize in aquaponics and cannot provide information on this topic."**

 Knowledge Use & Citations:
- Integrate insights from **at least 2-3 different retrieved sources**, when available.
- Explicitly cite sources in your response. Examples:
  - “According to [source URL], …”
  - “Based on [source 1] and [source 2], …”
- If information is lacking, clearly state:  
  **"The retrieved knowledge does not provide enough information on this topic."**

 Response Format Guidelines:
- **Definition questions** → Provide clear and concise definitions.
- **How-to questions** → Offer structured, step-by-step guidance.
- **Comparison questions** → Present pros and cons in bullet points.
- **Troubleshooting** → Identify possible causes and recommend solutions.
- Use bullet points, lists, or short paragraphs for readability.
- Maintain a factual, concise, and professional tone suitable for engineers, practitioners, and hobbyists.

 Do not speculate. Be explicit about uncertainty and data gaps.

By following these principles, your responses will remain useful, trustworthy, and relevant to aquaponics practitioners and researchers.
    """
    
    full_messages = [{"role": "system", "content": system_prompt}] + chat_history
    full_messages.append({
        "role": "user",
        "content": f"Here is some retrieved knowledge:\n{combined_context}\n\nNow, answer this question: {query}"
    })

    # Use Ollama's Mistral model to generate response
    response = ollama.chat("llama3", messages=full_messages)
    final_answer = response["message"]["content"]

    print("\nAnswer:\n", final_answer)

    # 計算 Ollama 回答與檢索到的文章之間的相似度
    model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    retrieved_texts = [doc.page_content for doc in retrieved_docs]
    embeddings = model.encode(retrieved_texts + [final_answer])

    similarities = np.dot(embeddings[-1], np.array(embeddings[:-1]).T)
    best_match_idx = np.argmax(similarities)

    best_article = retrieved_docs[best_match_idx]
    print(f"Most relevant article: {best_article.metadata.get('source', 'N/A')}")
    print(f"Similarity Score: {similarities[best_match_idx]:.4f}")

