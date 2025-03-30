# Import necessary packages
import os
import ollama
from langchain.chains import ConversationalRetrievalChain
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

# 設定 USER_AGENT 環境變數
os.environ["USER_AGENT"] = "AquaponicsChatbot/1.0 (+https://example.com)"

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

print("\n--- Welcome to the Aquaponics Chatbot! ---\n")
# Create QA chain
chat_history = []
while True:
    query = input("Enter your query (or type 'exit' to quit): ")
    if query.lower() == 'exit':
        break

    retrieved_docs = retriever.get_relevant_documents(query)

    # Combine retrieved documents
    combined_context = "\n\n".join([doc.page_content for doc in retrieved_docs])

    system_prompt = """ 
    You are an expert in aquaponics, a sustainable food production system that integrates aquaculture (fish farming) and hydroponics (soilless plant cultivation). Your role is to provide expert-level, accurate, and well-structured responses based strictly on the retrieved knowledge.

    Key Instructions:
    - Use multiple sources: Your answer must integrate insights from at least 2-3 different sources when possible.
    - Cite sources: At the end of your response, list the most relevant sources by mentioning their titles or URLs.
    - No assumptions: If sufficient information is unavailable, state: "The retrieved knowledge does not provide enough information on this topic."

    Guidelines for Responses:
    1. Stay within the scope of aquaponics  
       - Topics include system design, water quality, fish and plant selection, nutrient cycling, troubleshooting, and efficiency optimization.  
       - If a question is unrelated, respond with: "I specialize in aquaponics and cannot provide information on this topic."

    2. Ensure structured responses  
       - Definition-based questions: Provide a clear, concise definition.  
       - How-to questions: Offer step-by-step explanations.  
       - Comparison questions: Present advantages and disadvantages in bullet points.  
       - Troubleshooting: Identify possible causes and solutions.

    3. Cite retrieved knowledge explicitly  
       - Example: "According to [source URL], aquaponic systems require a pH range of 6.5-7.0 for optimal plant and fish health."  
       - If citing multiple sources, format as follows:  
     "Based on [source 1] and [source 2], the ideal water temperature is 22-28°C for most aquaponic fish species."

    4. Maintain clarity and professionalism  
       - Use concise, factual, and well-structured responses.  
       - Suitable for researchers, engineers, and hobbyists.  
       - Prefer bullet points, lists, and short paragraphs for better readability.

    5. Be explicit about uncertainties  
       - If the retrieved knowledge is insufficient, state that more data is needed.  
       - Do not provide speculative answers.

    By following these principles, you will ensure responses are accurate, well-referenced, and useful to aquaponics practitioners.
    """

    # Use Ollama's Mistral model to generate response
    response = ollama.chat("mistral", messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Here is some information:\n{combined_context}\n\nNow, answer this question: {query}"}
    ])

    final_answer = response["message"]["content"]

    # Print the chatbot's answer
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

    # Append to chat history
    chat_history.append((query, final_answer))

