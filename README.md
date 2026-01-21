# Aquaponics Chatbot

## Overview
The **Aquaponics Chatbot** is a Python-based application designed to assist users with aquaponics-related queries. This chatbot leverages modern natural language processing (NLP) technologies to provide accurate and helpful information. It supports various interaction modes and maintains conversational context for improved user engagement.

## Features
- **Web Content Loading**: Retrieves and processes web pages using `WebBaseLoader`.
- **Document Splitting**: Segments large documents into smaller, manageable chunks for efficient processing.
- **Embeddings and Vector Storage**:
  - Utilizes Sentence Transformers for text embeddings.
  - Employs FAISS for storing and retrieving vectorized documents.
- **Dual-Mode Interaction**:
  - **Simple Mode**: Provides straightforward answers to user queries.
  - **Advanced Mode**: Engages in a structured dialogue to clarify user objectives before delivering in-depth answers.
- **Memory Management**: Maintains conversational context using `ConversationBufferMemory`.

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/Rekin226/group1--project.git
   ```
2. Navigate to the project directory:
   ```bash
   cd group1--project
   ```
3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage
1. Prepare a file named `urls.txt` containing the URLs to be processed.
2. Run the chatbot:
   ```bash
   python srcs/chatbot3.py
   ```
3. Interact with the chatbot using the terminal. Use the following commands for additional functionalities:
   - `/simple`: Switch to Simple Mode.
   - `/advanced`: Switch to Advanced Mode.
   - `/clear`: Clear the conversational context.
   - `/exit`: Exit the chatbot.

## Dependencies
The chatbot relies on various Python libraries, including:
- `pandas`
- `langchain_ollama`
- `langchain_community`
- `concurrent.futures`
- `requests`
- `BeautifulSoup`
- `requests_cache`
- `sentence_transformers`

## Contribution
Feel free to contribute to this project by submitting issues or pull requests. For major changes, please open an issue to discuss your proposed changes.

## License
This project is licensed under the MIT License.
