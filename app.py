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


def should_generate_image(question, answer):
    """Decide if an image should be generated for this Q&A."""
    question_lower = question.lower()
    answer_lower = answer.lower()

    # Check if answer is silly/joke response - no image needed
    silly_patterns = ['silly', 'funny', 'ha ha', "can't really", "doesn't really", "that's a joke", "that's from a"]
    if any(p in answer_lower for p in silly_patterns):
        return False, None

    # Kid explicitly asks for an image - always generate
    image_requests = ['show me', 'picture of', 'look like', 'looks like', 'draw me', 'image of', 'photo of']
    kid_wants_image = any(p in question_lower for p in image_requests)

    if kid_wants_image:
        return True, None

    # Visual topics - if question mentions these, show an image
    visual_topics = [
        # Dinosaurs
        'dinosaur', 't-rex', 'tyrannosaurus', 'triceratops', 'brontosaurus', 'velociraptor', 'stegosaurus', 'pterodactyl',
        # Space
        'planet', 'jupiter', 'saturn', 'mars', 'venus', 'mercury', 'neptune', 'uranus', 'pluto',
        'rocket', 'spaceship', 'space shuttle', 'astronaut', 'moon', 'star', 'galaxy', 'sun', 'comet', 'asteroid',
        # Vehicles
        'airplane', 'plane', 'helicopter', 'jet', 'aircraft',
        'truck', 'dump truck', 'fire truck', 'monster truck', 'bulldozer', 'excavator', 'crane', 'tractor',
        'train', 'locomotive', 'subway',
        'boat', 'ship', 'submarine', 'battleship', 'yacht', 'sailboat',
        'car', 'race car', 'sports car',
        # Animals
        'elephant', 'lion', 'tiger', 'whale', 'blue whale', 'shark', 'dolphin', 'octopus',
        'butterfly', 'penguin', 'polar bear', 'giraffe', 'zebra', 'hippo', 'rhino', 'crocodile', 'alligator',
        'eagle', 'owl', 'parrot', 'flamingo', 'peacock',
        'snake', 'spider', 'scorpion', 'frog', 'turtle', 'tortoise',
        'monkey', 'gorilla', 'chimpanzee', 'orangutan',
        'bear', 'wolf', 'fox', 'deer', 'moose', 'elk',
        'horse', 'zebra', 'donkey', 'camel',
        'kangaroo', 'koala', 'panda',
        'bee', 'ant', 'ladybug', 'dragonfly',
        # Nature
        'volcano', 'rainbow', 'tornado', 'hurricane', 'waterfall', 'mountain', 'glacier', 'desert', 'forest', 'jungle',
        'ocean', 'coral reef', 'beach',
        # Buildings/Landmarks
        'castle', 'pyramid', 'eiffel tower', 'statue of liberty', 'great wall',
        # Robots/Tech
        'robot', 'drone'
    ]

    # If question mentions a visual topic, show an image
    is_visual_topic = any(topic in question_lower for topic in visual_topics)

    if is_visual_topic:
        return True, None

    # Only skip purely abstract questions with no visual subject
    # (feelings, meanings, pure numbers with no subject)
    abstract_only = ['what is the meaning of life', 'why do we feel', 'what are feelings',
                     'what is love', 'what is happiness', 'what is time']
    is_pure_abstract = any(p in question_lower for p in abstract_only)

    if is_pure_abstract:
        return False, None

    # For other questions, let the LLM decide via the prompt generator
    # This catches things like "what is a black hole" that aren't in our list
    return False, None


def create_kid_friendly_image_prompt(question, answer):
    """Create a DALL-E prompt for a realistic, educational image for kids."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None

    try:
        client = openai.OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model='gpt-4.1-nano',
            messages=[{
                'role': 'user',
                'content': f"""Create a DALL-E image prompt based on this Q&A for a 5-6 year old child.

Question: {question}
Answer: {answer}

Rules:
- Create a REALISTIC, educational image (like a nature documentary or science book)
- NOT cartoon style - real photography style or realistic illustration
- Show the actual subject clearly so kids can learn what it really looks like
- Use "realistic", "educational", "clear", "well-lit", "nature photography" style words
- Make it visually appealing but accurate
- NO scary, violent, or inappropriate content
- Maximum 50 words

If this Q&A doesn't need an image (abstract concepts, feelings, numbers), respond with just "NONE"

Example outputs:
- "A realistic blue whale swimming in clear ocean water, underwater photography, educational nature image, showing the whale's full body clearly"
- "A real T-Rex dinosaur in a prehistoric forest, realistic scientific illustration, museum-quality educational image, detailed and accurate"
- "NONE" (for questions about feelings, numbers, or abstract concepts)

Image prompt:"""
            }],
            max_tokens=80,
            temperature=0.7
        )

        prompt = response.choices[0].message.content.strip()
        if prompt.upper() == 'NONE' or len(prompt) < 10:
            return None
        return prompt
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


@app.route('/api/log-activity', methods=['POST'])
def log_activity():
    """Log game activity (like Spot the Mistake) to Supabase."""
    data = request.json
    user_id = data.get('user_id', 'anonymous')
    user_name = data.get('user_name', 'Anonymous')
    activity = data.get('activity', '')
    details = data.get('details', '')

    try:
        supabase.table('qa_logs').insert({
            'user_id': user_id,
            'user_name': user_name,
            'question': activity,
            'answer': details
        }).execute()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Activity logging error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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

        # Check if we should generate an image
        should_image, _ = should_generate_image(question, reply)

        return jsonify({
            'answer': reply,
            'user_id': user_id,
            'should_generate_image': should_image,
            'question_for_image': question if should_image else None,
            'answer_for_image': reply if should_image else None
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate-fact', methods=['POST'])
def generate_fact():
    """Generate a new spot-the-mistake fact using OpenAI."""
    data = request.json
    topic = data.get('topic', '')
    previous_facts = data.get('previous_facts', [])

    if not topic:
        return jsonify({'error': 'No topic provided'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    try:
        client = openai.OpenAI(api_key=api_key)

        # Build prompt with previous facts to avoid repetition
        previous_str = ""
        if previous_facts:
            previous_str = f"\n\nDO NOT use these facts (already used):\n- " + "\n- ".join(previous_facts[-5:])

        response = client.chat.completions.create(
            model='gpt-4.1-nano',
            messages=[{
                'role': 'user',
                'content': f"""Create a spot-the-mistake fact about "{topic}" for a 5-6 year old child.

Return JSON with exactly this format:
{{"correct": "true fact", "wrong": "silly wrong fact", "correctIcon": "emoji", "wrongIcon": "emoji", "concept": "what they learn"}}

Rules:
- The CORRECT fact must be true and educational about {topic}
- The WRONG fact must be obviously silly/funny (not scary)
- Use simple words a 5 year old understands
- Keep facts short (under 10 words each)
- The concept should explain why the correct fact is true
- Use fun emojis that match the facts{previous_str}

Examples for dinosaurs:
{{"correct": "T-Rex had tiny arms", "wrong": "T-Rex could do push-ups", "correctIcon": "🦖", "wrongIcon": "💪", "concept": "T-Rex arms were too small for push-ups!"}}

Return ONLY the JSON, nothing else."""
            }],
            max_tokens=200,
            temperature=0.9
        )

        result = response.choices[0].message.content.strip()

        # Parse JSON from response
        import json
        # Clean up response if needed
        if result.startswith('```'):
            result = result.split('```')[1]
            if result.startswith('json'):
                result = result[4:]
        result = result.strip()

        fact = json.loads(result)

        return jsonify({
            'fact': fact,
            'topic': topic
        })

    except Exception as e:
        print(f"Fact generation error: {e}")
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


@app.route('/api/image', methods=['POST'])
def generate_image():
    """Generate an educational image for kids using DALL-E."""
    data = request.json
    question = data.get('question', '')
    answer = data.get('answer', '')

    if not question or not answer:
        return jsonify({'error': 'Question and answer required'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    try:
        # Create kid-friendly prompt
        image_prompt = create_kid_friendly_image_prompt(question, answer)

        if not image_prompt:
            return jsonify({'error': 'No image needed for this question', 'image_url': None}), 200

        client = openai.OpenAI(api_key=api_key)

        # Generate image with DALL-E
        response = client.images.generate(
            model='dall-e-3',
            prompt=image_prompt,
            size='1024x1024',
            quality='standard',
            n=1
        )

        image_url = response.data[0].url

        return jsonify({
            'image_url': image_url,
            'prompt_used': image_prompt
        })

    except Exception as e:
        print(f"Image generation error: {e}")
        return jsonify({'error': str(e), 'image_url': None}), 500


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
