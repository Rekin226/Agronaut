# Import necessary packages
from transformers import pipeline
from langchain.llms import HuggingFacePipeline
from langchain.chains import RetrievalQA
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import sys


# List of target URLs (you can add more URLs to this list)
urls = [
    "https://doi.org/10.1186/s13213-020-01613-5",
    "https://doi.org/10.1016/j.inpa.2021.12.001"  # Add more URLs here
]



# Step 1: Load all web pages as documents.
all_documents = []
for url in urls:
    print(f"Loading URL: {url}")
    loader = WebBaseLoader(url)
    documents = loader.load()
    all_documents.extend(documents)

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
