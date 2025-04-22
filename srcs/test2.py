# 安裝必要的套件
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
from langchain_ollama.llms import OllamaLLM
from langchain.vectorstores import FAISS
from langchain.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.memory import ConversationBufferMemory
import requests_cache

requests_cache.install_cache('web_cache', expire_after=86400)

# 載入 Excel 檔案
df = pd.read_excel("Updated_Aquaponic_Article_organization.xlsx", sheet_name="Sheet1")

# 假設連結在欄位名為 "Source"
urls = df["Source"].dropna().unique()

# 儲存擷取的結果
extracted_texts = []

for url in urls:
    try:
        print(f"Fetching: {url}")
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            main_content = soup.find('body').get_text(separator='\n').strip()
            extracted_texts.append(main_content)
        else:
            print(f"Failed to fetch {url} with status code {response.status_code}")
    except Exception as e:
        print(f"Error fetching {url}: {e}")
    
    time.sleep(1) 

# 儲存為文字檔
with open("Updated_Aquaponic_Article_organization.txt", "w", encoding="utf-8") as f:
    for doc_text in extracted_texts:
        f.write(doc_text + "\n\n" + "="*50 + "\n\n")

# 步驟 1：載入文件
loader = TextLoader("aquaponics_cleaned_final.txt", encoding='utf-8')
documents = loader.load()

# 步驟 2：分割文件
splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
docs = splitter.split_documents(documents)

# 步驟 3：建立向量嵌入器
embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")

# 步驟 4：建立向量資料庫
vectorstore = FAISS.from_documents(docs, embedding_model)

"""
query = "How does a biofilter work in aquaponics?"
docs = vectorstore.similarity_search(query, k=3)

print("\n--- Top 3 Matching Documents ---\n")
for i, doc in enumerate(docs, 1):
    print(f"[{i}] {doc.page_content[:300]}...\n")
"""

# 步驟 5：啟用 LLM + Memory
llm = OllamaLLM(model="llama3")
memory = ConversationBufferMemory(return_messages=True)

# 步驟 6：問答流程
print("\n--- Welcome to the Aquaponics Chatbot! ---\n")

while True:
    query = input("Enter your query (or type 'exit' to quit): ")
    if query.lower() == 'exit':
        break 

    # 擷取相關文件
    retrieved_docs = vectorstore.similarity_search(query, k=3)
    context = "\n\n".join([doc.page_content for doc in retrieved_docs])

    # 準備上下文歷史對話
    history = memory.chat_memory.messages
    history_text = ""
    for msg in history:
        role = "You" if msg.type == "human" else "Bot"
        history_text += f"{role}: {msg.content}\n"

    prompt = f"""
You are a subject matter expert and aquaponics consultant with extensive experience advising both individuals and businesses on designing, implementing, and managing aquaponics systems. When a user asks a question:
    1. Check if the provided context is sufficient.
    2. If the context is insufficient, ask one round of clarifying questions to better understand their needs.
    3. Once the user responds with additional details or confirms that no further clarification is needed, provide your final answer based solely on the updated context.
    4. Do not ask for clarifications more than once per query.
     Maintain a friendly, professional, and consultative tone throughout.

Context:
{context}

Chat History:
{history_text}

Question: {query}
Answer:
""".strip()

    # 執行推論
    response = llm.invoke(prompt)
    print("\nAnswer:\n" + "-"*50 + f"\n{response}\n" + "-"*50 + "\n")

    # 儲存歷史訊息
    memory.chat_memory.add_user_message(query)
    memory.chat_memory.add_ai_message(response)