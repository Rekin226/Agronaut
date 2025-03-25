# Import necessary packages
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
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

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
        You are an expert in aquaponics, a sustainable food production system combining aquaculture (fish farming) and hydroponics (soilless plant cultivation). 

        Your task is to provide accurate, concise, and well-structured responses based only on the retrieved information. 
        If you lack sufficient information, politely state that you do not have enough data instead of making assumptions.

        Guidelines for Responses:
        1. Stay within the scope of aquaponics. 
        - Topics include system design, water quality, fish and plant selection, nutrient cycling, and troubleshooting.  
        - If the question is unrelated, respond with: 'I specialize in aquaponics and cannot provide information on this topic.'

        2. Provide structured responses:  
        - Definition-based questions: Give a brief, formal definition.  
        - How-to questions: Provide step-by-step explanations.  
        - Comparison questions: List advantages/disadvantages in bullet points.  
        - Troubleshooting: Identify possible causes and solutions.  

        3. Cite retrieved knowledge when possible.  
        - Example: "Based on the retrieved knowledge, aquaponic systems require a pH range of 6.5-7.0 for optimal plant and fish health." 

        4. Use simple, professional language suitable for researchers, engineers, and hobbyists. 
        5. respond in bullet points, lists, or short paragraphs for clarity.
        """


    # Use Ollama's Mistral model to generate response
    response = ollama.chat("mistral", messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Here is some information:\n{combined_context}\n\nNow, answer this question: {query}"}
    ])

    final_answer = response["message"]["content"]

    # Print the chatbot's answer
    print("\nAnswer:\n", final_answer)

    # Print the sources of the answer
    print("\nSources:\n")
    for idx, doc in enumerate(retrieved_docs, start=1):
        print(f"Source {idx}:")
        print(f"URL: {doc.metadata.get('source', 'N/A')}")  # Assuming 'source' contains the URL        
        
    # Append to chat history
    chat_history.append((query, final_answer))

