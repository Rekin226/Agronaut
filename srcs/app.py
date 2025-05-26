from flask import Flask, render_template, request, jsonify
from chatbot_backend import AquaponicsChatbot
import os

# Configure Flask to look for templates in the parent directory
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)

# Initialize chatbot with error handling
try:
    chatbot = AquaponicsChatbot()
except Exception as e:
    print(f"Error initializing chatbot: {e}")
    chatbot = None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    if not chatbot:
        return jsonify({'error': 'Chatbot not available'})
        
    data = request.json
    query = data.get('message', '').strip()
    
    if not query:
        return jsonify({'error': 'Empty message'})
    
    # Handle commands
    if query.lower() in ['/simple', '/s']:
        return jsonify({'system_message': chatbot.set_mode('simple')})
    elif query.lower() in ['/advanced', '/a']:
        return jsonify({'system_message': chatbot.set_mode('advanced')})
    elif query.lower() in ['/clear']:
        return jsonify({'system_message': chatbot.clear_memory()})
    
    # Get response
    try:
        result = chatbot.get_response(query)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Error processing query: {str(e)}'})

@app.route('/mode', methods=['POST'])
def change_mode():
    if not chatbot:
        return jsonify({'error': 'Chatbot not available'})
        
    data = request.json
    mode = data.get('mode', 'simple')
    message = chatbot.set_mode(mode)
    return jsonify({'message': message})

if __name__ == '__main__':
    app.run(debug=True)