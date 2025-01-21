# -*- coding: utf-8 -*-
"""
Created on Sun Dec 29 15:07:17 2024

@author: newt0
"""

# -*- coding: utf-8 -*-
"""
Modified to use data from Dictionary_02.py
"""

from langchain_huggingface import HuggingFaceEmbeddings
import faiss
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from uuid import uuid4
from langchain_core.documents import Document
import pandas as pd
import Dictionary_02 as d2
import sys


# print(d2.df)
df = d2.df
#print(df)


# print html_path FROM Dictionary_02.py
html_path = df["source"].values[0]
print('The path for the html is: ', html_path)
# sys.exit()



embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
embedding_dim = len(embeddings.embed_query("hello world"))
index = faiss.IndexFlatL2(embedding_dim)

vector_store = FAISS(
    embedding_function=embeddings,
    index=index,
    docstore=InMemoryDocstore(),
    index_to_docstore_id={},
)

documents = [
    Document(page_content=row["content"], metadata={"source":"html_path"})
    for _, row in df.iterrows()
]

print(f"Added {len(documents)} documents to the vector store.")
print(f"FAISS index size: {index.ntotal}")
query = "Plant Care"
results = vector_store.similarity_search(query)
print(f"Search results: {results}")

# Generate unique IDs for the documents
uuids = [str(uuid4()) for _ in range(len(documents))]

# Add documents to the vector store
vector_store.add_documents(documents=documents, ids=uuids)

print(f"Added {len(documents)} documents to the vector store.")
print(f"FAISS index size: {index.ntotal}")

# Perform similarity search
results = vector_store.similarity_search_with_score(
    "What is nutrient levels?", k=1, filter={"content": "nutrient levels"}
)

# Print the results
for res, score in results:
    print(f"* [SIM={score:.3f}] {res.page_content} [{res.metadata}]")


