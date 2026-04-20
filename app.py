"""
Curiosity Explorer - Flask Backend
Handles Q&A with GPT-5.2 and TTS with shimmer voice
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import openai
from supabase import create_client, Client

app = Flask(__name__, static_folder='static')
CORS(app)


def openai_retry(fn, max_attempts=3):
    """Retry an OpenAI API call on transient 500 errors."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except openai.InternalServerError as e:
            print(f"[RETRY] Attempt {attempt + 1}/{max_attempts} failed with InternalServerError: {e}")
            if attempt < max_attempts - 1:
                time.sleep(1 * (attempt + 1))
                continue
            raise
        except openai.APIStatusError as e:
            if e.status_code >= 500 and attempt < max_attempts - 1:
                print(f"[RETRY] Attempt {attempt + 1}/{max_attempts} failed with status {e.status_code}: {e}")
                time.sleep(1 * (attempt + 1))
                continue
            raise

# Supabase setup
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://nyfpidtlkhwhgcrgaerf.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im55ZnBpZHRsa2h3aGdjcmdhZXJmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE4MDQwMzUsImV4cCI6MjA4NzM4MDAzNX0.6KUK1cwLUBMxGVkEd_i0jJgo7mqe-Gh8IIsMtcgPpwU')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Simple in-memory cache for consistent answers to identical questions
_answer_cache = {}
_CACHE_TTL = 3600  # 1 hour
_CACHE_MAX_SIZE = 200

def get_cached_answer(question):
    key = question.strip().lower()
    entry = _answer_cache.get(key)
    if entry and (time.time() - entry['time']) < _CACHE_TTL:
        return entry['data']
    return None

def set_cached_answer(question, data):
    key = question.strip().lower()
    if len(_answer_cache) >= _CACHE_MAX_SIZE:
        oldest = min(_answer_cache, key=lambda k: _answer_cache[k]['time'])
        del _answer_cache[oldest]
    _answer_cache[key] = {'data': data, 'time': time.time()}

# Socratic prompt for kid-friendly responses
SOCRATIC_PROMPT = """You are a friendly helper for a 6-7 year old child.

STRICT RULES:
1. ONLY give the answer. NEVER ask a question back.
2. NO follow-up questions. NO "Do you know...?" NO "Can you...?" NO "What do you think...?"
3. End with a statement, NOT a question.
4. Include a "Learn more:" link at the end from your web search (for real questions only).

"SHOW ME" OR "PICTURE OF" REQUESTS:
If the kid asks to see a picture or show something, answer the FACTUAL question behind it. The app will find an image separately — your job is to give a fun fact answer about the subject.
Example: "Show me a picture of the first airplane" → Answer about the Wright Flyer.
Example: "What does a blue whale look like?" → Answer about blue whales.

SILLY OR NONSENSE QUESTIONS:
If the question is a joke, impossible, or doesn't make sense (like "why did the cow go to space" or "can fish fly to the moon"), respond playfully:
- "Ha ha, that's silly! Cows don't go to space! 😄"
- "That's a funny idea! Fish can't fly to the moon! 🐟"
Do NOT try to answer nonsense questions seriously. Do NOT include a Learn more link for silly questions.

HOW TO TALK:
- Simple words only (say "big" not "large")
- Compare to kid things (big as a bus)
- Say "Wow!" or "Cool!" to be fun
- Short sentences (5-7 words each)

FORMAT FOR REAL QUESTIONS:
1. Fun answer — MAXIMUM 3 SHORT SENTENCES. This is critical. Do NOT write more than 3 sentences. A 6-year-old will not read a paragraph.
2. One emoji
3. "Learn more:" with a markdown link [title](url)

GOOD EXAMPLES:
Kid: "How many moons does Saturn have?"
You: "Wow, Saturn has 146 moons! That is more moons than any other planet! 🪐 Learn more: [NASA Saturn](https://nasa.gov/saturn)"

Kid: "How fast do rockets go?"
You: "Rockets go super fast — like 100 times faster than a car on the highway! They have to go that fast to get to space! 🚀 Learn more: [NASA Rockets](https://nasa.gov/rockets)"

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
        'ocean', 'coral reef', 'beach', 'lightning', 'thunder', 'earthquake', 'tsunami', 'flood', 'avalanche',
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

    # Default: show an image for most questions — kids are visual learners
    return True, None


def create_kid_friendly_image_prompt(question, answer):
    """Create a DALL-E prompt for a realistic, educational image for kids."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None

    try:
        client = openai.OpenAI(api_key=api_key)

        response = openai_retry(lambda: client.chat.completions.create(
            model='gpt-4.1-nano',
            messages=[{
                'role': 'user',
                'content': f"""Create a DALL-E image prompt based on this Q&A for a 6-7 year old child.

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
            temperature=0
        ))

        prompt = response.choices[0].message.content.strip()
        if prompt.upper() == 'NONE' or len(prompt) < 10:
            return None
        return prompt
    except:
        return None


def search_wikipedia_image(search_term):
    """Search Wikipedia for a real photograph matching the search term.
    Returns {image_url, source, attribution} or None."""
    headers = {'User-Agent': 'CuriosityExplorer/1.0 (educational kids app)'}

    def _fetch_json(url):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def _extract_image(summary_data):
        thumb = summary_data.get('thumbnail', {}).get('source')
        original = summary_data.get('originalimage', {}).get('source')
        title = summary_data.get('title', search_term)
        if thumb:
            # Request 800px width
            image_url = re.sub(r'/\d+px-', '/800px-', thumb)
            return {
                'image_url': image_url,
                'source': 'wikipedia',
                'attribution': f'Photo from Wikipedia: {title}'
            }
        if original:
            return {
                'image_url': original,
                'source': 'wikipedia',
                'attribution': f'Photo from Wikipedia: {title}'
            }
        return None

    try:
        # Tier 1: Direct page summary lookup
        encoded = urllib.parse.quote(search_term.replace(' ', '_'), safe='')
        url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}'
        try:
            data = _fetch_json(url)
            result = _extract_image(data)
            if result:
                return result
        except urllib.error.HTTPError:
            pass  # 404 or other error, fall through to search

        # Tier 2: Wikipedia search API fallback
        params = urllib.parse.urlencode({
            'action': 'query',
            'list': 'search',
            'srsearch': search_term,
            'format': 'json',
            'origin': '*'
        })
        search_url = f'https://en.wikipedia.org/w/api.php?{params}'
        search_data = _fetch_json(search_url)
        results = search_data.get('query', {}).get('search', [])
        if not results:
            return None

        # Try the top result's summary
        best_title = results[0]['title']
        encoded_title = urllib.parse.quote(best_title.replace(' ', '_'), safe='')
        summary_url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}'
        try:
            summary_data = _fetch_json(summary_url)
            return _extract_image(summary_data)
        except urllib.error.HTTPError:
            return None

    except Exception as e:
        print(f"Wikipedia image search error: {e}")
        return None


def search_web_image(search_term, question):
    """Search the web for a real photograph using OpenAI web search."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None
    try:
        client = openai.OpenAI(api_key=api_key)
        response = openai_retry(lambda: client.responses.create(
            model='gpt-4.1-mini',
            tools=[{'type': 'web_search'}],
            input=f"""Find a real photograph of: "{search_term}"
Context: a child asked "{question}"

I need a direct image URL from one of these sources (in order of preference):
1. Wikimedia Commons (upload.wikimedia.org)
2. NASA images (images.nasa.gov or nasa.gov)
3. Smithsonian or museum sites
4. Government/educational sites (.gov, .edu)

The URL must point directly to an image file.
Return ONLY the URL, nothing else. If you cannot find a suitable image, return NONE."""
        ))
        # Extract text from response
        result_text = ""
        for block in response.output:
            if block.type == "message":
                for cb in block.content:
                    if cb.type == "output_text":
                        result_text = cb.text.strip()

        print(f"Web image search for '{search_term}': got response: {result_text[:200]}")

        if not result_text or 'NONE' in result_text.upper():
            return None

        # Extract all candidate URLs from response (broad match)
        urls = []
        for m in re.finditer(r'https?://[^\s\'"<>\)]+', result_text):
            url = m.group(0).rstrip('.,;:)]}')
            urls.append(url)

        print(f"Web image search: found {len(urls)} candidate URLs: {urls[:3]}")

        if not urls:
            return None

        # Prefer Wikimedia/NASA/gov URLs, then any URL
        preferred = [u for u in urls if any(d in u for d in ['wikimedia.org', 'nasa.gov', '.gov/', '.edu/'])]
        ordered = preferred + [u for u in urls if u not in preferred]

        # Return the first URL — the proxy endpoint will handle fetching
        # and the frontend onerror will handle failures
        for url in ordered:
            # Basic sanity: must look like it could be an image
            if re.search(r'\.(jpg|jpeg|png|webp|gif|svg)', url, re.IGNORECASE) or 'upload.wikimedia.org' in url:
                return {
                    'image_url': url,
                    'source': 'web',
                    'attribution': 'Photo from the web'
                }

        # If no URL looks like an image, try the first URL anyway
        if ordered:
            return {
                'image_url': ordered[0],
                'source': 'web',
                'attribution': 'Photo from the web'
            }

        return None
    except Exception as e:
        print(f"Web image search error: {e}")
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

    # Return cached answer for identical questions (ignores chat history for cache key)
    cached = get_cached_answer(question)
    if cached:
        log_qa(user_id, user_name, question, cached['answer'])
        return jsonify(cached)

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

        # Call OpenAI Responses API with web search (with retry for transient errors)
        response = openai_retry(lambda: client.responses.create(
            model='gpt-4.1',
            instructions=SOCRATIC_PROMPT,
            input=full_input,
            tools=[{'type': 'web_search'}],
            tool_choice='required',
            temperature=0,
            max_output_tokens=400
        ))

        # Extract text from response — check all output blocks for text content
        reply = ""
        if response.output:
            for item in response.output:
                if item.type == 'message' and item.content:
                    for content_block in item.content:
                        if hasattr(content_block, 'text') and content_block.text:
                            reply = content_block.text
                            break
                    if reply:
                        break
        if not reply:
            reply = "Hmm, I'm not sure about that. Can you try asking in a different way?"

        # Log the Q&A to Supabase
        log_qa(user_id, user_name, question, reply)

        # Check if we should generate an image
        should_image, _ = should_generate_image(question, reply)

        # Generate 3 follow-up question suggestions
        follow_ups = []
        try:
            fu_response = openai_retry(lambda: client.chat.completions.create(
                model='gpt-4.1-nano',
                messages=[{
                    'role': 'user',
                    'content': f"""A 6-year-old just asked: "{question}"
And got this answer: "{reply}"

Suggest 3 short follow-up questions this kid might ask next. Each must:
- Be under 8 words
- Start with an emoji
- Be a different angle on the same topic or a natural "what next" curiosity

Return ONLY a JSON array of 3 strings, nothing else.
Example: ["🦴 Did T-Rex eat other dinosaurs?", "🥚 How big were dino eggs?", "☄️ What killed the dinosaurs?"]"""
                }],
                max_tokens=100,
                temperature=0.9
            ))
            import json as json_mod
            fu_text = fu_response.choices[0].message.content.strip()
            follow_ups = json_mod.loads(fu_text)
        except Exception:
            follow_ups = []

        result = {
            'answer': reply,
            'user_id': user_id,
            'should_generate_image': should_image,
            'question_for_image': question if should_image else None,
            'answer_for_image': reply if should_image else None,
            'follow_ups': follow_ups
        }
        set_cached_answer(question, result)
        return jsonify(result)

    except Exception as e:
        print(f"[ASK] Error type: {type(e).__name__}, MRO: {[c.__name__ for c in type(e).__mro__]}")
        print(f"[ASK] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate-fact', methods=['POST'])
def generate_fact():
    """Generate a new spot-the-mistake fact using OpenAI."""
    data = request.json
    topic = data.get('topic', '')
    previous_facts = data.get('previous_facts', [])
    question_number = data.get('question_number', 1)
    tier = 1 if question_number <= 2 else 2 if question_number <= 4 else 3

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
            previous_str = f"\n\nCRITICAL — DO NOT REPEAT: The following facts have already been used in this session. Your new fact pair MUST be about a completely different idea. Do NOT rephrase, reword, or cover the same concept as any of these:\n- " + "\n- ".join(previous_facts)

        tier_descriptions = {
            1: "a COMMON MISCONCEPTION that kids actually believe (e.g., 'the pilot steers with a steering wheel like a car')",
            2: "a PLAUSIBLE WRONG DETAIL - right category but wrong specifics (e.g., 'airplane tires use regular air like bicycle tires')",
            3: "a fact that CHALLENGES ASSUMPTIONS - something that seems obviously true but isn't (e.g., 'every plane must always have a pilot inside')"
        }
        tier_desc = tier_descriptions[tier]

        # Topic-specific subtopic pools — each entry has a category name AND a concrete example
        # The example anchors the LLM on the right kind of fact so it can't drift to generic "how X works"
        topic_specific_categories = {
            "planes": [
                {"cat": "airplane food and passengers", "example_correct": "Food on planes is cooked on the ground before the flight", "example_wrong": "Food on planes is cooked in the back during the flight"},
                {"cat": "airport runways and ground crew", "example_correct": "People on the ground wave signals to help planes park", "example_wrong": "Pilots park planes all by themselves with no help"},
                {"cat": "airplane tires and landing gear", "example_correct": "Plane wheels fold up inside after takeoff", "example_wrong": "Plane wheels hang down the whole time it flies"},
                {"cat": "the Wright Brothers and history of flight", "example_correct": "The first airplane flight lasted only 12 seconds", "example_wrong": "The first airplane flight lasted a whole hour"},
                {"cat": "airplane paint and colors", "example_correct": "Most planes are white to keep them cool in the sun", "example_wrong": "Planes are white because colored paint is too heavy"},
                {"cat": "cargo planes and what planes carry", "example_correct": "Some planes are so big they carry other planes inside", "example_wrong": "Planes can only carry people, not big heavy things"},
                {"cat": "airplane windows", "example_correct": "Airplane windows have a tiny secret hole in them", "example_wrong": "Airplane windows are sealed tight with no holes at all"},
                {"cat": "the cockpit where pilots sit", "example_correct": "Pilots sit at the very front of the plane", "example_wrong": "Pilots sit in the middle of the plane"},
                {"cat": "the black box", "example_correct": "The airplane black box is actually bright orange", "example_wrong": "The airplane black box is painted black"},
                {"cat": "how pilots learn to fly", "example_correct": "Pilots practice in a pretend plane that feels real", "example_wrong": "Pilots only read books to learn how to fly"},
                {"cat": "funny things about planes", "example_correct": "Food tastes different on a plane than on the ground", "example_wrong": "Food tastes exactly the same on a plane as on the ground"},
                {"cat": "airplane engines and fuel", "example_correct": "Planes use special fuel that is different from car gas", "example_wrong": "Planes use the same gas you put in a car"},
            ],
            "stars": [
                {"cat": "our Sun", "example_correct": "The Sun is a star, just like the ones you see at night", "example_wrong": "The Sun is a planet, not a star"},
                {"cat": "star colors", "example_correct": "Blue stars are way hotter than red stars", "example_wrong": "Red stars are the hottest because red means hot"},
                {"cat": "star patterns in the sky", "example_correct": "The Big Dipper looks like a giant spoon in the sky", "example_wrong": "The Big Dipper looks like a big circle of stars"},
                {"cat": "shooting stars", "example_correct": "Shooting stars are tiny rocks burning up in the sky", "example_wrong": "Shooting stars are real stars falling down"},
                {"cat": "how stars are born", "example_correct": "Stars are born inside giant clouds in space", "example_wrong": "Stars just appear out of nowhere"},
                {"cat": "the Sun and Earth", "example_correct": "You could fit a million Earths inside the Sun", "example_wrong": "The Sun and Earth are about the same size"},
                {"cat": "why stars twinkle", "example_correct": "Stars twinkle because the air around Earth wiggles their light", "example_wrong": "Stars twinkle because they blink on and off"},
                {"cat": "the Milky Way", "example_correct": "We live inside a galaxy called the Milky Way", "example_wrong": "We live outside all galaxies, floating in empty space"},
                {"cat": "how far away stars are", "example_correct": "Stars are so far away their light takes years to reach us", "example_wrong": "Starlight reaches us in just a few seconds"},
                {"cat": "what happens when stars die", "example_correct": "Some stars explode when they die", "example_wrong": "Stars just quietly disappear when they get old"},
                {"cat": "telescopes", "example_correct": "Some telescopes float in space to get a better view", "example_wrong": "All telescopes have to stay on the ground"},
                {"cat": "planets around other stars", "example_correct": "Other stars far away have their own planets too", "example_wrong": "Only our Sun has planets going around it"},
            ],
            "dinosaurs": [
                {"cat": "what dinosaurs ate", "example_correct": "Most dinosaurs ate plants, not meat", "example_wrong": "All dinosaurs ate meat and hunted other animals"},
                {"cat": "dinosaur eggs and babies", "example_correct": "Some dinosaur eggs were as big as a basketball", "example_wrong": "Dinosaur eggs were tiny like chicken eggs"},
                {"cat": "the asteroid that ended the dinosaurs", "example_correct": "A giant space rock crashed into Earth and ended the dinosaurs", "example_wrong": "Dinosaurs just got too old and disappeared on their own"},
                {"cat": "fossils and digging up bones", "example_correct": "Scientists use tiny brushes to clean dinosaur bones", "example_wrong": "Scientists use big bulldozers to dig up dinosaur bones"},
                {"cat": "dinosaur poop and footprints", "example_correct": "Dinosaur poop turned into rocks you can actually hold", "example_wrong": "Dinosaur poop is all gone and nobody ever found any"},
                {"cat": "flying reptiles in dinosaur times", "example_correct": "Pterodactyls could fly but they were NOT dinosaurs", "example_wrong": "Pterodactyls were a type of flying dinosaur"},
                {"cat": "T-Rex", "example_correct": "T-Rex had tiny little arms, shorter than yours", "example_wrong": "T-Rex had long strong arms for grabbing food"},
                {"cat": "how fast dinosaurs could move", "example_correct": "Some small dinosaurs could run as fast as a car", "example_wrong": "All dinosaurs were slow and could barely walk"},
                {"cat": "dinosaur feathers and skin", "example_correct": "Many dinosaurs had feathers, like birds", "example_wrong": "No dinosaur ever had feathers"},
                {"cat": "where dinosaur bones are found", "example_correct": "Dinosaur bones have been found on every continent", "example_wrong": "Dinosaur bones are only found in deserts"},
                {"cat": "animals related to dinosaurs today", "example_correct": "Birds are actually dinosaurs that are still alive today", "example_wrong": "Lizards are the closest relatives of dinosaurs"},
                {"cat": "how dinosaurs kept warm", "example_correct": "Some dinosaurs were warm-blooded, like you", "example_wrong": "All dinosaurs were cold-blooded, like lizards"},
            ],
            "ocean": [
                {"cat": "the deep ocean", "example_correct": "The deepest part of the ocean is deeper than the tallest mountain", "example_wrong": "The ocean is only as deep as a tall building"},
                {"cat": "coral reefs", "example_correct": "Coral is actually made of tiny living animals", "example_wrong": "Coral is made of colorful rocks"},
                {"cat": "ocean waves and tides", "example_correct": "The Moon pulls on the ocean to make tides go in and out", "example_wrong": "Tides happen because big fish push the water around"},
                {"cat": "whales and dolphins", "example_correct": "Dolphins and whales breathe air, just like you", "example_wrong": "Dolphins and whales breathe water like fish do"},
                {"cat": "sharks", "example_correct": "Sharks have been around longer than dinosaurs", "example_wrong": "Sharks only appeared after the dinosaurs were gone"},
                {"cat": "glowing sea creatures", "example_correct": "Some sea animals can make their own light in the dark", "example_wrong": "The deep ocean has sunlight like the beach does"},
                {"cat": "submarines and exploring the deep", "example_correct": "Only a few people have ever been to the deepest ocean spot", "example_wrong": "Lots of people visit the bottom of the ocean every year"},
                {"cat": "seaweed and ocean plants", "example_correct": "Some seaweed can grow taller than a house", "example_wrong": "All ocean plants are tiny and short"},
                {"cat": "why the ocean is salty", "example_correct": "Rivers wash salt from rocks into the ocean over time", "example_wrong": "Someone put salt in the ocean a long time ago"},
                {"cat": "octopuses", "example_correct": "An octopus has three hearts and blue blood", "example_wrong": "An octopus has one heart and red blood like us"},
                {"cat": "underwater volcanoes", "example_correct": "There are volcanoes under the ocean, not just on land", "example_wrong": "Volcanoes can only be found on dry land"},
                {"cat": "icebergs", "example_correct": "Most of an iceberg is hiding underwater where you cant see it", "example_wrong": "You can see most of an iceberg above the water"},
            ],
        }
        # Fallback for custom/unknown topics
        default_categories = [
            {"cat": "history and who discovered or invented it", "example_correct": "It was discovered much longer ago than most people think", "example_wrong": "It was only discovered a few years ago"},
            {"cat": "surprising world records about it", "example_correct": "The biggest one is much larger than you would guess", "example_wrong": "They are all about the same size everywhere"},
            {"cat": "what it looks like up close vs far away", "example_correct": "It looks completely different when you zoom in really close", "example_wrong": "It looks exactly the same no matter how close you get"},
            {"cat": "where in the world you can find it", "example_correct": "You can find it in more places than most people realize", "example_wrong": "It only exists in one small part of the world"},
            {"cat": "how people use it in everyday life", "example_correct": "People use it in ways you might not expect", "example_wrong": "It has only one simple use that everyone knows about"},
            {"cat": "famous people connected to it", "example_correct": "A famous scientist made an important discovery about it", "example_wrong": "Nobody famous ever studied or cared about it"},
            {"cat": "how it has changed over time", "example_correct": "It used to be very different from what we see today", "example_wrong": "It has always looked exactly the same since the beginning"},
            {"cat": "what scientists are still learning about it", "example_correct": "Scientists still have big unanswered questions about it", "example_wrong": "Scientists already know everything there is to know about it"},
            {"cat": "myths and common mistakes people believe about it", "example_correct": "Most people believe something about it that is actually wrong", "example_wrong": "Everything most people believe about it is true"},
            {"cat": "fun or weird facts most people dont know", "example_correct": "There is something really surprising about it that few people know", "example_wrong": "There is nothing surprising about it at all"},
        ]

        topic_lower = topic.lower()
        subtopic_pool = topic_specific_categories.get(topic_lower, default_categories)

        # Avoid recently used categories
        used_categories = data.get('used_categories', [])
        available = [c for c in subtopic_pool if c.get("cat") not in used_categories]
        if not available:
            available = subtopic_pool  # Reset if all used
        import random
        chosen = random.choice(available)
        category = chosen["cat"]
        category_example_correct = chosen["example_correct"]
        category_example_wrong = chosen["example_wrong"]

        response = openai_retry(lambda: client.chat.completions.create(
            model='gpt-4.1',
            messages=[
            {
                'role': 'system',
                'content': """You write fun quiz questions for 5-6 year old kids. You talk like a friendly teacher at a kindergarten, NOT like a textbook.

YOUR RULES:
1. Use ONLY words a 5-year-old already knows. BANNED words: pressure, atmosphere, mineral, retract, nitrogen, hydrothermal, aerodynamics, resistance, turbulence, combustion, velocity.
2. Facts should make a kid go "WHOA, really?!" — cool and surprising, not dry and boring.
3. The wrong fact must be plausible TO A KID — tricky for a 5-year-old, not tricky for an adult.
4. Write the way a kid TALKS, not the way a book reads.

BAD (too complex, textbook voice):
- "Airplane windows have tiny holes to balance cabin pressure"
- "Hydrothermal vents create mineral-rich chimneys"
- "Stars emit light through nuclear fusion"

GOOD (kid voice, cool facts):
- "Airplane pilots sit at the very front of the plane"
- "Some dinosaur poop turned into rocks you can hold"
- "Octopuses have blue blood, not red like us"

BAD wrong facts (too silly, no kid would believe this):
- "Planes have balloons inside their wings"
- "Pilots steer with their feet while sleeping"

GOOD wrong facts (a kid might actually think this is true):
- "Airplane pilots sit in the back of the plane"
- "All dinosaurs were green or brown"
- "Fish close their eyes when they sleep"

CRITICAL: Do NOT make up facts. The "correct" fact MUST be true. The "wrong" fact MUST be actually false. Only use facts you are confident about.
CRITICAL: NEVER repeat or rephrase a fact from a previous round. Each question must teach something NEW."""
            },
            {
                'role': 'user',
                'content': f"""Topic: "{topic}"
Subtopic: "{category}"

Example for this subtopic:
  Correct: "{category_example_correct}"
  Wrong: "{category_example_wrong}"

Make a DIFFERENT fact pair about "{category}" (not the example above).

Remember: write like you're talking to a 5-year-old. The fact should be something cool a kid would want to tell their friend. The wrong one should be something a kid might actually believe.

STRICT: Stay on the subtopic "{category}". Do NOT write about how planes fly or how wings work unless that's the subtopic.{previous_str}

Keep each fact under 10 words. Keep the concept under 12 words.

Return ONLY this JSON:
{{"correct": "cool true fact a kid would love", "wrong": "wrong fact a kid might believe", "correctIcon": "emoji", "wrongIcon": "emoji", "concept": "fun reason why the wrong one is wrong"}}"""
            }],
            max_tokens=250,
            temperature=0.85
        ))

        result = response.choices[0].message.content.strip()

        # Parse JSON from response
        # Clean up response if needed
        if result.startswith('```'):
            result = result.split('```')[1]
            if result.startswith('json'):
                result = result[4:]
        result = result.strip()

        fact = json.loads(result)

        return jsonify({
            'fact': fact,
            'topic': topic,
            'category': category
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
        response = openai_retry(lambda: client.audio.speech.create(
            model='tts-1',
            voice='shimmer',  # Friendly, expressive voice for kids
            input=clean_text,
            speed=1.0
        ))

        # Return audio as mp3
        return Response(
            response.content,
            mimetype='audio/mpeg',
            headers={'Content-Disposition': 'inline; filename="speech.mp3"'}
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def classify_image_type(question, answer=''):
    """Classify whether a question needs a real photo or AI-generated illustration.
    Returns ('real', 'search term') or ('generated', None)."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return ('generated', None)

    try:
        client = openai.OpenAI(api_key=api_key)
        response = openai_retry(lambda: client.chat.completions.create(
            model='gpt-4.1-nano',
            messages=[{
                'role': 'user',
                'content': f"""Pick a Wikipedia search term that would find an image RELEVANT to this Q&A for a kid.

Question: "{question}"
Answer: "{answer[:200]}"

The search term must be a SPECIFIC NOUN that Wikipedia would have a photo of.

REAL = when there's a clear, specific THING to photograph (an animal, place, object, vehicle, landmark)
GENERATED = when the question is about an activity, process, behavior, feeling, or fictional thing — Wikipedia won't have a good photo for these

Reply with EXACTLY one line in this format:
REAL|<specific noun Wikipedia article title>
or
GENERATED

Examples:
Q: "How fast do rockets go?" A: "Rockets go super fast..." -> REAL|rocket launch
Q: "What makes lightning?" A: "Lightning is a spark from clouds..." -> REAL|lightning
Q: "Why are flamingos pink?" A: "They eat shrimp..." -> REAL|flamingo
Q: "What is the Eiffel Tower?" A: "A tall tower in Paris..." -> REAL|Eiffel Tower
Q: "What do people do on planes?" A: "People read, watch movies, nap..." -> GENERATED
Q: "How do bees make honey?" A: "Bees collect nectar..." -> GENERATED
Q: "How do volcanoes erupt?" A: "Hot magma pushes up..." -> GENERATED
Q: "Why is the sky blue?" A: "Sunlight bounces around..." -> GENERATED
Q: "Draw me a dragon" -> GENERATED"""
            }],
            max_tokens=30,
            temperature=0.0
        ))
        result = response.choices[0].message.content.strip()
        if result.startswith('REAL|'):
            search_term = result[5:].strip()
            return ('real', search_term)
        return ('generated', None)
    except Exception as e:
        print(f"Image classification error: {e}")
        return ('generated', None)


@app.route('/api/image', methods=['POST'])
def generate_image():
    """Generate or fetch an educational image for kids.
    Routes to Wikipedia (real photos) or DALL-E (illustrations) based on question type."""
    data = request.json
    question = data.get('question', '')
    answer = data.get('answer', '')

    if not question or not answer:
        return jsonify({'error': 'Question and answer required'}), 400

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    try:
        # Classify whether this needs a real photo or AI illustration
        image_type, search_term = classify_image_type(question, answer)
        print(f"[IMAGE] Classified '{question}' as {image_type}, term='{search_term}'")

        # Try Wikipedia/web only for REAL classification
        if image_type == 'real' and search_term:
            wiki_result = search_wikipedia_image(search_term)
            print(f"[IMAGE] Wikipedia result: {wiki_result['image_url'][:80] if wiki_result else 'None'}")
            if wiki_result:
                proxied_url = f"/api/image-proxy?url={urllib.parse.quote(wiki_result['image_url'], safe='')}"
                return jsonify({
                    'image_url': proxied_url,
                    'image_source': 'wikipedia',
                    'image_attribution': wiki_result['attribution']
                })

            web_result = search_web_image(search_term, question)
            print(f"[IMAGE] Web search result: {web_result['image_url'][:80] if web_result else 'None'}")
            if web_result:
                proxied_url = f"/api/image-proxy?url={urllib.parse.quote(web_result['image_url'], safe='')}"
                return jsonify({
                    'image_url': proxied_url,
                    'image_source': 'web',
                    'image_attribution': web_result['attribution']
                })

        # DALL-E for GENERATED or when real sources fail
        image_prompt = create_kid_friendly_image_prompt(question, answer)

        if not image_prompt:
            return jsonify({'error': 'No image needed for this question', 'image_url': None}), 200

        client = openai.OpenAI(api_key=api_key)

        dalle_response = openai_retry(lambda: client.images.generate(
            model='dall-e-3',
            prompt=image_prompt,
            size='1024x1024',
            quality='standard',
            n=1
        ))

        image_url = dalle_response.data[0].url

        return jsonify({
            'image_url': image_url,
            'image_source': 'dalle',
            'image_attribution': None,
            'prompt_used': image_prompt
        })

    except Exception as e:
        print(f"[IMAGE] Error: {type(e).__name__}: {e}")
        return jsonify({'error': str(e), 'image_url': None}), 500


@app.route('/api/debug-image')
def debug_image():
    """Debug endpoint - visit in browser to test image flow."""
    question = request.args.get('q', 'show me a picture of the first airplane')
    answer = 'test answer'
    steps = []
    wiki_result = None
    try:
        import time
        t0 = time.time()
        image_type, search_term = classify_image_type(question)
        steps.append(f"1. Classify ({time.time()-t0:.1f}s): type={image_type}, term={search_term}")

        if image_type == 'real' and search_term:
            t0 = time.time()
            wiki_result = search_wikipedia_image(search_term)
            steps.append(f"2. Wikipedia ({time.time()-t0:.1f}s): {wiki_result}")

            if not wiki_result:
                t0 = time.time()
                web_result = search_web_image(search_term, question)
                steps.append(f"3. Web search ({time.time()-t0:.1f}s): {web_result}")
        else:
            steps.append("2. Skipped (not REAL type)")
    except Exception as e:
        steps.append(f"ERROR: {type(e).__name__}: {e}")

    # If we got a wiki result, show the proxied image too
    img_html = ''
    if wiki_result:
        proxied = f"/api/image-proxy?url={urllib.parse.quote(wiki_result['image_url'], safe='')}"
        img_html = f'<br><br><img src="{proxied}" style="max-width:400px" onerror="this.alt=\'FAILED TO LOAD\'">'
    return '<pre>' + '\n'.join(steps) + '</pre>' + img_html


@app.route('/api/image-proxy')
def image_proxy():
    """Proxy external images to avoid hotlink blocking, CORS, and rate limiting."""
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return 'Bad request', 400
    try:
        # Use appropriate headers based on the source
        if 'wikimedia.org' in url or 'wikipedia.org' in url:
            headers = {
                'User-Agent': 'CuriosityExplorer/1.0 (educational kids app; contact: curiosityexplorer.feedback@gmail.com)',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            }
        else:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': url,
            }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            content_type = resp.headers.get('Content-Type', 'image/jpeg')
            if len(data) < 1000:
                print(f"[IMAGE] Proxy: response too small ({len(data)} bytes) for {url}")
                return 'Image not found', 404
            return Response(data, content_type=content_type, headers={
                'Cache-Control': 'public, max-age=86400'
            })
    except Exception as e:
        print(f"[IMAGE] Proxy error for {url}: {e}")
        return 'Image not found', 404


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
