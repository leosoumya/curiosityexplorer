"""
Curiosity Explorer - Flask Backend
Handles Q&A with GPT-5.2 and TTS with shimmer voice
"""

import os
import re
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import openai
from supabase import create_client, Client

app = Flask(__name__, static_folder='static')
CORS(app)

# Supabase setup
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://nyfpidtlkhwhgcrgaerf.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im55ZnBpZHRsa2h3aGdjcmdhZXJmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE4MDQwMzUsImV4cCI6MjA4NzM4MDAzNX0.6KUK1cwLUBMxGVkEd_i0jJgo7mqe-Gh8IIsMtcgPpwU')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Socratic prompt for kid-friendly responses
SOCRATIC_PROMPT = """You are a friendly helper for a 5-6 year old child.

STRICT RULES:
1. ONLY give the answer. NEVER ask a question back.
2. NO follow-up questions. NO "Do you know...?" NO "Can you...?" NO "What do you think...?"
3. End with a statement, NOT a question.
4. Include a "Learn more:" link at the end from your web search (for real questions only).

SILLY OR NONSENSE QUESTIONS:
If the question is a joke, impossible, or doesn't make sense (like "why did the cow go to space" or "can fish fly to the moon"), respond playfully:
- "Ha ha, that's silly! Cows don't go to space! 😄"
- "That's a funny idea! Fish can't fly to the moon! 🐟"
Do NOT try to answer nonsense questions seriously. Do NOT include a Learn more link for silly questions.

HOW TO TALK:
- Simple words only (say "big" not "large")
- Compare to kid things (big as a bus)
- Say "Wow!" or "Cool!" to be fun
- Short sentences (5-7 words max)

FORMAT FOR REAL QUESTIONS:
1. Fun answer (2 sentences max)
2. One emoji
3. "Learn more:" with a markdown link [title](url)

GOOD EXAMPLES:
Kid: "How many moons does Saturn have?"
You: "Wow, Saturn has 146 moons! That is so many! 🪐 Learn more: [NASA Saturn](https://nasa.gov/saturn)"

Kid: "Why did the cow jump over the moon?"
You: "Ha ha, that's from a fun story! Cows can't really jump that high! 🐄"

Kid: "Can dinosaurs eat pizza?"
You: "That's silly! Dinosaurs lived long ago, before pizza was invented! 🦕"

BAD (never do this):
"Can you guess?" ❌
"Do you know what else?" ❌
Answering nonsense questions seriously ❌

REMEMBER: Answer only. No questions. Laugh at silly questions!"""


def extract_image_keyword(question, answer):
    """Extract the best image search keyword from question/answer using LLM."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None

    # Check if answer is silly/joke response - no image needed
    silly_patterns = ['silly', 'funny', 'ha ha', "can't really", "doesn't really", "that's a joke"]
    if any(p in answer.lower() for p in silly_patterns):
        return None

    try:
        client = openai.OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model='gpt-4.1-nano',
            messages=[{
                'role': 'user',
                'content': f"""Extract the MOST SPECIFIC visual subject from this Q&A for an image search.

Question: {question}
Answer: {answer}

Rules:
- Return ONLY the search term, nothing else
- Be SPECIFIC: "dump truck" not "truck", "blue whale" not "whale", "fire truck" not "truck"
- If it's about a specific animal/vehicle/object, use the full specific name
- If no good visual subject exists, return "NONE"

Examples:
- Q: "How big is a dump truck?" -> "dump truck"
- Q: "Why do fire trucks have ladders?" -> "fire truck"
- Q: "What do blue whales eat?" -> "blue whale"
- Q: "Why is the sky blue?" -> "blue sky"
- Q: "How do planes fly?" -> "airplane flying"

Search term:"""
            }],
            max_tokens=20,
            temperature=0
        )

        keyword = response.choices[0].message.content.strip().lower()
        if keyword == 'none' or len(keyword) > 50:
            return None
        return keyword
    except:
        return None


def clean_text_for_speech(text):
    """Clean text for TTS - remove URLs, emojis, markdown links."""
    # Convert [text](url) to just text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove HTML links, keep text
    text = re.sub(r'<a[^>]*>([^<]*)</a>', r'\1', text, flags=re.IGNORECASE)
    # Remove plain URLs
    text = re.sub(r'https?://[^\s\])]+', '', text)
    # Remove www links
    text = re.sub(r'www\.[^\s\])]+', '', text)
    # Remove citation numbers like [1]
    text = re.sub(r'\[\d+\]', '', text)
    # Remove "Source:" lines
    text = re.sub(r'Source:.*$', '', text, flags=re.MULTILINE)
    # Remove "Learn more:" lines
    text = re.sub(r'Learn more:.*$', '', text, flags=re.MULTILINE)
    # Remove emojis (common emoji ranges)
    text = re.sub(r'[\U0001F300-\U0001F9FF]|[\u2600-\u26FF]|[\u2700-\u27BF]', '', text)
    # Clean up extra spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def log_qa(user_id, user_name, question, answer):
    """Log question/answer pair to Supabase."""
    try:
        supabase.table('qa_logs').insert({
            'user_id': user_id,
            'user_name': user_name,
            'question': question,
            'answer': answer
        }).execute()
    except Exception as e:
        print(f"Supabase logging error: {e}")


@app.route('/')
def index():
    """Serve the frontend."""
    return send_from_directory('static', 'index.html')


@app.route('/api/ask', methods=['POST'])
def ask():
    """Handle Q&A requests using GPT-5.2 with web search."""
    data = request.json
    question = data.get('question', '')
    user_id = data.get('user_id', 'anonymous')
    user_name = data.get('user_name', 'Anonymous')
    chat_history = data.get('chat_history', [])

    if not question:
        return jsonify({'error': 'No question provided'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    try:
        client = openai.OpenAI(api_key=api_key)

        # Build input with chat history for context
        full_input = ""
        for msg in chat_history[-6:]:  # Last 6 messages for context
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role == 'user':
                full_input += f"Kid asked: {content}\n"
            else:
                full_input += f"You said: {content}\n"
        full_input += f"Kid asks: {question}"

        # Call OpenAI Responses API with web search
        response = client.responses.create(
            model='gpt-5.2',
            instructions=SOCRATIC_PROMPT,
            input=full_input,
            tools=[{'type': 'web_search'}],
            tool_choice='required',
            max_output_tokens=250
        )

        # Extract text from response
        reply = "Hmm, I'm not sure about that. Can you try asking in a different way?"
        if response.output:
            for item in response.output:
                if item.type == 'message' and item.content:
                    reply = item.content[0].text
                    break

        # Log the Q&A to Supabase
        log_qa(user_id, user_name, question, reply)

        # Extract image keyword for relevant images
        image_keyword = extract_image_keyword(question, reply)

        return jsonify({
            'answer': reply,
            'user_id': user_id,
            'image_keyword': image_keyword
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tts', methods=['POST'])
def tts():
    """Generate text-to-speech audio using OpenAI TTS with shimmer voice."""
    data = request.json
    text = data.get('text', '')

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    # Clean text for speech
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return jsonify({'error': 'No speakable text'}), 400

    try:
        client = openai.OpenAI(api_key=api_key)

        # Generate speech with shimmer voice
        response = client.audio.speech.create(
            model='tts-1',
            voice='shimmer',  # Friendly, expressive voice for kids
            input=clean_text,
            speed=1.0
        )

        # Return audio as mp3
        return Response(
            response.content,
            mimetype='audio/mpeg',
            headers={'Content-Disposition': 'inline; filename="speech.mp3"'}
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/logs', methods=['GET'])
def get_logs():
    """Return all Q&A logs from Supabase as JSON."""
    try:
        response = supabase.table('qa_logs').select('*').order('timestamp', desc=True).execute()
        logs = response.data

        return jsonify({
            'total': len(logs),
            'logs': logs
        })
    except Exception as e:
        return jsonify({'error': str(e), 'total': 0, 'logs': []}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
