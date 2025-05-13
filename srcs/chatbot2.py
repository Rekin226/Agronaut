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
from langchain.chains import ConversationalRetrievalChain
from pdfminer.high_level import extract_text
from transformers import AutoModelForQuestionAnswering, AutoTokenizer, pipeline

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

# Set up LLM pipeline
#model_name = "google/flan-t5-large" 
#pipe = pipeline("text2text-generation", model=model_name, tokenizer=model_name, max_length=512, temperature=0.3)
model_name = "deepset/roberta-base-squad2"
pipe = pipeline('question-answering', model=model_name, tokenizer=model_name)
llm = HuggingFacePipeline(pipeline=pipe)

model = AutoModelForQuestionAnswering.from_pretrained(model_name)
tokenizer = AutoTokenizer.from_pretrained(model_name)

# Create QA chain
qa_chain = ConversationalRetrievalChain.from_llm(llm, retriever=retriever)
chat_history = []

while True:
    query = input("Enter your query (or type 'exit' to quit): ")
    if query.lower() == 'exit':
        break

    retrieved_docs = retriever.get_relevant_documents(query)

    summarized_docs = []
    for doc in retrieved_docs:
        summary_prompt = f"answer the question:\n{doc.page_content}"
        summary = llm(summary_prompt) 
        summarized_docs.append(summary)

    combined_context = "\n\n".join(summarized_docs)
    final_prompt = f"Answer the questions based on the following information:\n{combined_context}\n\nquestion:{query}"
    final_answer = llm(final_prompt) 

    chat_history.append((query, final_answer))
    print("\nAnswer:\n", final_answer)