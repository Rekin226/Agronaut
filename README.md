# Aquaponics Chatbot

## Overview
The **Aquaponics Chatbot** is an intelligent, AI-powered conversational assistant built with Python to help users learn about and troubleshoot aquaponics systems. By combining web scraping, natural language processing (NLP), and vector-based semantic search, this chatbot provides accurate, context-aware responses to aquaponics-related questions. Whether you're a beginner exploring aquaponics or an experienced practitioner seeking specific guidance, this chatbot adapts to your needs with two interaction modes.

## Features
- **Intelligent Web Content Processing**: 
  - Automatically loads and parses web pages using `WebBaseLoader`
  - Extracts relevant aquaponics information from multiple sources
  - Caches responses for improved performance

- **Advanced Document Processing**:
  - Intelligently splits large documents into optimized chunks for better context retrieval
  - Maintains semantic coherence across document sections

- **Semantic Search with Embeddings**:
  - Leverages Sentence Transformers to create high-quality text embeddings
  - Uses FAISS (Facebook AI Similarity Search) for fast, accurate vector-based retrieval
  - Finds the most relevant information based on query meaning, not just keywords

- **Dual-Mode Interaction**:
  - **Simple Mode**: Provides quick, direct answers to straightforward questions—ideal for fast lookups and general inquiries
  - **Advanced Mode**: Engages in a guided conversation to understand your specific goals, then delivers comprehensive, tailored responses

- **Contextual Memory Management**: 
  - Maintains conversation history using `ConversationBufferMemory`
  - Remembers previous questions and answers for more natural, flowing conversations
  - Allows you to clear context when starting a new topic

- **Extensible Architecture**: 
  - Modular design makes it easy to add new data sources
  - Configurable to work with different LLM backends via Ollama

## Installation

### Prerequisites
- Python 3.8 or higher
- Ollama installed and running locally (for LLM inference)

### Setup Steps
1. **Clone the repository**:
   ```bash
   git clone https://github.com/Rekin226/group1--project.git
   ```

2. **Navigate to the project directory**:
   ```bash
   cd group1--project
   ```

3. **Install the required dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Ensure Ollama is running** with a compatible model (e.g., llama2, mistral)

## Usage

### Getting Started
1. **Prepare your data sources**:
   - Create or edit the `urls.txt` file in the project root
   - Add URLs (one per line) containing aquaponics information you want the chatbot to learn from

2. **Launch the chatbot**:
   ```bash
   python srcs/chatbot3.py
   ```

3. **Start asking questions!** The chatbot will process your URLs on first run and then be ready to answer questions.

### Interactive Commands
Once the chatbot is running, you can use these commands at any time:

| Command | Description |
|---------|-------------|
| `/simple` | Switch to Simple Mode for quick, direct answers |
| `/advanced` | Switch to Advanced Mode for guided, in-depth responses |
| `/clear` | Clear the conversation history and start fresh |
| `/exit` | Exit the chatbot application |

### Example Interaction
```
You: What is aquaponics?
Bot: Aquaponics is a sustainable farming method that combines aquaculture (raising fish) 
     with hydroponics (growing plants in water)...

You: /advanced
Bot: Switched to Advanced Mode.

You: How do I maintain pH levels?
Bot: Before I provide specific guidance, could you tell me more about your setup? 
     What type of fish are you raising, and what plants are you growing?
```

## Dependencies
The chatbot relies on the following Python libraries:

| Library | Purpose |
|---------|---------|
| `pandas` | Data manipulation and analysis |
| `langchain_ollama` | Integration with Ollama LLM models |
| `langchain_community` | Community-contributed LangChain components |
| `concurrent.futures` | Parallel processing for faster data loading |
| `requests` | HTTP requests for web scraping |
| `BeautifulSoup` | HTML parsing and web content extraction |
| `requests_cache` | HTTP response caching |
| `sentence_transformers` | Text embedding generation |
| `faiss` | Vector similarity search |

Install all dependencies with:
```bash
pip install -r requirements.txt
```

## Contribution
Contributions are welcome! Here's how you can help:

1. **Report bugs** by opening an issue with detailed reproduction steps
2. **Suggest features** through issue discussions
3. **Submit pull requests** for bug fixes or new features
4. **Improve documentation** to help other users

For major changes, please open an issue first to discuss your proposed changes with the maintainers.

## License
This project is licensed under the MIT License. See the LICENSE file for details.

## Acknowledgments
Built with LangChain, Sentence Transformers, FAISS, and Ollama.