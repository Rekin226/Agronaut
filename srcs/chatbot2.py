# Import necessary packages
from transformers import pipeline
from langchain.llms import HuggingFacePipeline
from langchain.chains import RetrievalQA
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
        response.raise_for_status()  # Raise an exception for HTTP errors
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text()
        return text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

# Function to handle different document formats
def handle_document_format(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type')
        if 'html' in content_type:
            soup = BeautifulSoup(response.text, 'html.parser')
            text = soup.get_text()
        elif 'pdf' in content_type:
            # Handle PDF format
            text = "PDF content handling not implemented yet."
        else:
            text = "Unsupported document format."
        return text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

# Function to load URLs from a file
def load_urls_from_file(file_path):
    with open(file_path, 'r') as file:
        urls = file.read().splitlines()
    return urls

# Function to fetch URLs from an API
def fetch_urls_from_api(api_url):
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        urls = response.json()
        return urls
    except requests.exceptions.RequestException as e:
        print(f"Error fetching URLs from API: {e}")
        return []

# Load URLs from a file instead of hardcoding
urls = load_urls_from_file('urls.txt')

# Step 1: Load all web pages as documents in parallel.
all_documents = []
with concurrent.futures.ThreadPoolExecutor() as executor:
    future_to_url = {executor.submit(load_web_page, url): url for url in urls}
    for future in concurrent.futures.as_completed(future_to_url):
        url = future_to_url[future]
        try:
            documents = future.result()
            all_documents.extend(documents)
        except Exception as e:
            print(f"Error loading {url}: {e}")

# If you want, you can check the combined length of all documents:
total_length = sum(len(doc.page_content) for doc in all_documents)
print(f"Total loaded document length across URLs: {total_length}")

# Step 2: Split the document texts into chunks.
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
docs = text_splitter.split_documents(all_documents)
print(f"Number of document chunks: {len(docs)}")

# Step 3: Create embeddings using a Hugging Face model.
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# Step 4: Build a FAISS vector store from the document chunks.
vectorstore = FAISS.from_documents(docs, embeddings)
print(f"FAISS vectorstore now has {vectorstore.index.ntotal} vectors.")

# Step 5: Convert the FAISS vector store into a retriever.
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

# Step 6: Set up a local inference pipeline using Transformers.
model_name = "google/flan-t5-small"
pipe = pipeline("text2text-generation", model=model_name, tokenizer=model_name)
llm = HuggingFacePipeline(pipeline=pipe)

# Step 7: Create a RetrievalQA chain.
qa_chain = RetrievalQA.from_chain_type(llm=llm, chain_type="stuff", retriever=retriever)

# Step 8: Implement a loop to handle multiple queries and add a simple command-line interface.
while True:
    query = input("Enter your query (or type 'exit' to quit): ")
    if query.lower() == 'exit':
        break
    result = qa_chain.run(query)
    print("\nAnswer:\n", result)
