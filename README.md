# Aquaponics Chatbot

## Overview
The **Aquaponics Chatbot** is an intelligent, AI-powered conversational assistant built with Python to help users learn about and troubleshoot aquaponics systems. By combining web scraping, natural language processing (NLP), and vector-based semantic search, this chatbot provides accurate, context-aware responses to aquaponics-related questions. Whether you're a beginner exploring aquaponics or an experienced practitioner seeking specific guidance, this chatbot adapts to your needs with two interaction modes.

## Features
- **Modern Web Interface**: 
  - Built with Streamlit for an intuitive, browser-based experience
  - Real-time chat interface with message history
  - Responsive design with custom styling and chat bubbles
  - Sidebar controls for easy mode switching and settings

- **Intelligent Web Content Processing**: 
  - Automatically loads and parses web pages using `WebBaseLoader`
  - Extracts relevant aquaponics information from multiple sources
  - Caches responses for improved performance with `requests_cache`
  - Parallel processing for faster data loading

- **Advanced Document Processing**:
  - Intelligently splits large documents into optimized chunks for better context retrieval
  - Maintains semantic coherence across document sections using `RecursiveCharacterTextSplitter`

- **Semantic Search with Embeddings**:
  - Leverages Sentence Transformers (`all-mpnet-base-v2`) to create high-quality text embeddings
  - Uses FAISS (Facebook AI Similarity Search) for fast, accurate vector-based retrieval
  - Finds the most relevant information based on query meaning, not just keywords

- **Dual-Mode Interaction**:
  - **Simple Mode**: Provides quick, direct answers to straightforward questions—ideal for fast lookups and general inquiries
  - **Advanced Mode**: Engages in a guided conversation to understand your specific goals, then delivers comprehensive, tailored responses

- **Contextual Memory Management**: 
  - Maintains conversation history using `ConversationBufferMemory`
  - Remembers previous questions and answers for more natural, flowing conversations
  - Allows you to clear context when starting a new topic

- **Source Transparency**:
  - Optional display of source documents used to generate each response
  - View document previews and sources for verification

- **Extensible Architecture**: 
  - Modular design makes it easy to add new data sources
  - Configurable to work with different LLM backends via Ollama
  - Alternative agent-based CLI version available for advanced users

## Installation

### Prerequisites
- Python 3.8 or higher
- Ollama installed and running locally (for LLM inference)
  - Required models: `llama3` (primary chatbot)
  - Optional: `phi3:mini` (for agent-based version)

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
   pip install -r requirement.txt
   ```

4. **Ensure Ollama is running** with the required models:
   ```bash
   ollama pull llama3
   # Optional for command-line agent version:
   ollama pull phi3:mini
   ```

## Usage

### Getting Started
1. **Prepare your data sources**:
   - The `urls.txt` file in the project root contains aquaponics research URLs
   - You can edit this file to add more URLs (one per line) for the chatbot to learn from

2. **Launch the Streamlit frontend server**:
   ```bash
   streamlit run srcs/chatbot3.py
   ```
   
   The web interface will automatically open in your browser (typically at `http://localhost:8501`)

3. **Start asking questions!** The chatbot will:
   - Process your URLs on first run (this may take a minute)
   - Cache the processed data for faster subsequent launches
   - Be ready to answer aquaponics questions through the web interface

### Web Interface Features
The Streamlit-based chatbot provides an intuitive web interface with:

- **Chat Interface**: Type questions in the chat input box at the bottom
- **Response Modes**: Switch between Simple and Advanced modes using the sidebar:
  - **Simple Mode**: Quick, concise answers for straightforward questions
  - **Advanced Mode**: In-depth analysis with clarifying questions
- **Source Documents**: Toggle "Show source documents" to see which sources were used
- **Conversation Management**: Clear conversation history with the sidebar button
- **Real-time Status**: View current mode and message count in the sidebar

### Alternative: Command-Line Agent Version
For users who prefer a terminal-based interface with advanced agent routing:

```bash
python srcs/chatbot3_ollama_react.py
```

This version uses a funnel-based agent system with:
- Automatic routing between simple and complex questions
- Multi-step clarification for complex scenarios
- Commands: `clear` (reset memory), `exit` (quit)

### Example Web Interface Interaction
1. Open the web interface in your browser
2. Select your preferred mode (Simple/Advanced) from the sidebar
3. Type your question in the chat input: "What is aquaponics?"
4. View the AI-generated response with optional source documents
5. Continue the conversation - the chatbot remembers context

## Dependencies
The chatbot relies on the following Python libraries:

| Library | Purpose |
|---------|---------|
| `pandas` | Data manipulation and analysis |
| `numpy` | Numerical computing support |
| `langchain` | LLM application framework |
| `langchain_ollama` | Integration with Ollama LLM models |
| `langchain_community` | Community-contributed LangChain components |
| `streamlit` | Web-based user interface framework |
| `markdown` | Markdown to HTML conversion for rich text display |
| `requests` | HTTP requests for web scraping |
| `bs4` (BeautifulSoup) | HTML parsing and web content extraction |
| `requests_cache` | HTTP response caching for improved performance |
| `transformers` | Transformer models support |
| `sentence-transformers` | Text embedding generation |
| `faiss-cpu` | Vector similarity search |
| `openpyxl` | Excel file handling |

Install all dependencies with:
```bash
pip install -r requirement.txt
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