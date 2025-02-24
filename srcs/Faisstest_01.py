# Import necessary libraries
from transformers import pipeline
from langchain.llms import HuggingFacePipeline
from langchain.chains import RetrievalQA
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import requests
from bs4 import BeautifulSoup
from langchain_core.documents import Document
import pandas as pd
import Dictionary_02 as d2

# Function to extract text from a URL with error handling
def extract_text_from_url(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.get_text()
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return ""

# Load source data
source_data = d2.df

# Extract URLs and fetch content
all_documents = []
for url in source_data["source"].tolist():
    print(f"Fetching content from URL: {url}")
    content = extract_text_from_url(url)
    if content:
        doc = Document(page_content=content, metadata={"source": url})
        all_documents.append(doc)

print(f"Loaded {len(all_documents)} documents.")

# Split documents into chunks
text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
docs = text_splitter.split_documents(all_documents)
print(f"Number of document chunks: {len(docs)}")

# Create embeddings with a more accurate model
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")

# Create FAISS vectorstore
vectorstore = FAISS.from_documents(docs, embeddings)
print(f"FAISS vectorstore now has {vectorstore.index.ntotal} vectors.")

# Create a retriever
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

# Initialize the HuggingFace pipeline with a larger model
model_name = "google/flan-t5-base"
pipe = pipeline("text2text-generation", model=model_name, tokenizer=model_name)
llm = HuggingFacePipeline(pipeline=pipe)

# Create a RetrievalQA chain
qa_chain = RetrievalQA.from_chain_type(llm=llm, chain_type="stuff", retriever=retriever)

# Run a query
query = "What is aquaponics?"
result = qa_chain.run(query)
print("\nAnswer:\n", result)

# Perform a similarity search with scores
results_with_scores = vectorstore.similarity_search_with_score("What is nutrient levels?", k=3)

# Print results with similarity scores
for res, score in results_with_scores:
    print(f"* [SIM={score:.3f}] {res.page_content} [{res.metadata}]")
