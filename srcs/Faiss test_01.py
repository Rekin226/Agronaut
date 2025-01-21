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

def extract_text_from_url(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.get_text()

source_data = d2.df

urls = source_data["source"].tolist()

all_documents = []
for url in urls:
    print(f"Fetching content from URL: {url}")
    content = extract_text_from_url(url)
    if content:
        doc = Document(page_content=content, metadata={"source": url})
        all_documents.append(doc)

print(f"Loaded {len(all_documents)} documents.")

text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
docs = text_splitter.split_documents(all_documents)
print(f"Number of document chunks: {len(docs)}")

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

vectorstore = FAISS.from_documents(docs, embeddings)
print(f"FAISS vectorstore now has {vectorstore.index.ntotal} vectors.")

retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

model_name = "google/flan-t5-small"
pipe = pipeline("text2text-generation", model=model_name, tokenizer=model_name)
llm = HuggingFacePipeline(pipeline=pipe)

qa_chain = RetrievalQA.from_chain_type(llm=llm, chain_type="stuff", retriever=retriever)

query = "What is aquaponics?"
result = qa_chain.run(query)
print("\nAnswer:\n", result)

results_with_scores = vectorstore.similarity_search_with_score(
    "What is nutrient levels?", k=1
)

for res, score in results_with_scores:
    print(f"* [SIM={score:.3f}] {res.page_content} [{res.metadata}]")