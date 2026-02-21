#!/usr/bin/env python3
"""
Curiosity Explorer - Ask Me Anything Agent
A kid-friendly Q&A agent for children ages 5-7.

Usage:
    python curiosity_agent.py

Requirements:
    pip install openai speechrecognition pyaudio playsound requests
"""

import os
import sys
import json
import tempfile
import threading
from pathlib import Path

# Config file path (shared with web app)
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_api_key_from_config():
    """Load API key from shared config.json file."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                key = config.get("openai_api_key", "").strip()
                if key:
                    return key
        except Exception as e:
            print(f"Warning: Could not read config.json: {e}")
    return None


# Check for required packages
try:
    import openai
    from openai import OpenAI
except ImportError:
    print("Please install openai: pip install openai")
    sys.exit(1)

try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False
    print("Note: speech_recognition not installed. Voice input disabled.")
    print("Install with: pip install speechrecognition pyaudio")

# Try to import audio playback
try:
    from playsound import playsound
    PLAYSOUND_AVAILABLE = True
except ImportError:
    PLAYSOUND_AVAILABLE = False


class CuriosityAgent:
    """A kid-friendly Q&A agent using OpenAI."""

    SYSTEM_PROMPT = """You are a friendly helper for a 5-6 year old child.

STRICT RULES:
1. ONLY give the answer. NEVER ask a question back.
2. NO follow-up questions. NO "Do you know...?" NO "Can you...?" NO "What do you think...?"
3. End with a statement, NOT a question.

HOW TO TALK:
- Simple words only (say "big" not "large")
- Compare to kid things (big as a bus)
- Say "Wow!" or "Cool!" to be fun
- Short sentences (5-7 words max)

GOOD EXAMPLES:
Kid: "How many moons does Saturn have?"
You: "Wow, Saturn has 146 moons! That is so many!"

Kid: "Why is the sky blue?"
You: "Light from the sun bounces in the air. Blue bounces the most!"

Kid: "How fast do cheetahs run?"
You: "Cheetahs run super fast! As fast as a car!"

BAD (never do this):
"Can you guess?"
"Do you know what else?"
"What do you think?"

REMEMBER: Answer only. No questions. End with a period or exclamation mark."""

    def __init__(self, api_key: str = None):
        """Initialize the agent with OpenAI API key."""
        # Priority: passed key > config.json > environment variable
        self.api_key = api_key or load_api_key_from_config() or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Add it to config.json, "
                "set OPENAI_API_KEY environment variable, or pass api_key parameter."
            )

        self.client = OpenAI(api_key=self.api_key)
        self.conversation_history = []
        self.muted = False

        # Initialize speech recognition if available
        if SPEECH_RECOGNITION_AVAILABLE:
            self.recognizer = sr.Recognizer()
            self.microphone = sr.Microphone()

        print("\n🌟 Curiosity Explorer - Ask Me Anything! 🌟")
        print("=" * 45)
        print("Hi! I'm here to answer your questions!")
        print("\nCommands:")
        print("  Type a question and press Enter")
        if SPEECH_RECOGNITION_AVAILABLE:
            print("  Type 'voice' to ask with your voice")
        print("  Type 'mute' to turn off voice")
        print("  Type 'unmute' to turn on voice")
        print("  Type 'clear' to start fresh")
        print("  Type 'quit' to exit")
        print("=" * 45 + "\n")

    def ask(self, question: str) -> str:
        """Ask a question and get a kid-friendly answer."""
        if not question.strip():
            return "Please ask me something!"

        # Add to conversation history
        self.conversation_history.append({
            "role": "user",
            "content": question
        })

        # Keep only last 6 messages for context
        recent_history = self.conversation_history[-6:]

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    *recent_history
                ],
                max_tokens=150,
                temperature=0.7
            )

            answer = response.choices[0].message.content.strip()

            # Add to history
            self.conversation_history.append({
                "role": "assistant",
                "content": answer
            })

            return answer

        except Exception as e:
            return f"Oops! Something went wrong. Let's try again! ({str(e)[:50]})"

    def speak(self, text: str):
        """Convert text to speech using OpenAI TTS."""
        if self.muted:
            return

        # Clean text for speech (remove emojis and special chars)
        clean_text = self._clean_for_speech(text)
        if not clean_text:
            return

        try:
            # Use OpenAI TTS
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="shimmer",  # Friendly, expressive voice
                input=clean_text,
                speed=1.0
            )

            # Save to temp file and play
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                temp_path = f.name
                response.stream_to_file(temp_path)

            # Play audio in background
            if PLAYSOUND_AVAILABLE:
                threading.Thread(
                    target=self._play_and_cleanup,
                    args=(temp_path,),
                    daemon=True
                ).start()
            else:
                # Try system command as fallback
                if sys.platform == "darwin":  # macOS
                    os.system(f'afplay "{temp_path}" && rm "{temp_path}"')
                elif sys.platform == "linux":
                    os.system(f'mpg123 "{temp_path}" 2>/dev/null && rm "{temp_path}"')
                else:
                    print("(Audio playback not available on this system)")

        except Exception as e:
            print(f"(Voice unavailable: {str(e)[:30]})")

    def _play_and_cleanup(self, filepath: str):
        """Play audio file and clean up."""
        try:
            playsound(filepath)
        except:
            pass
        finally:
            try:
                os.unlink(filepath)
            except:
                pass

    def _clean_for_speech(self, text: str) -> str:
        """Remove emojis, URLs, and other non-speech elements."""
        import re

        # Remove emojis
        emoji_pattern = re.compile(
            "["
            "\U0001F300-\U0001F9FF"  # Various symbols and pictographs
            "\U00002600-\U000026FF"  # Misc symbols
            "\U00002700-\U000027BF"  # Dingbats
            "]+",
            flags=re.UNICODE
        )
        text = emoji_pattern.sub('', text)

        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'www\.\S+', '', text)

        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def listen(self) -> str:
        """Listen for voice input and return transcribed text."""
        if not SPEECH_RECOGNITION_AVAILABLE:
            print("Voice input not available. Please type your question.")
            return ""

        print("🎤 Listening... (speak now)")

        try:
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)

            print("Processing...")
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text

        except sr.WaitTimeoutError:
            print("No speech detected. Try again!")
            return ""
        except sr.UnknownValueError:
            print("Couldn't understand. Try again!")
            return ""
        except sr.RequestError as e:
            print(f"Speech service error: {e}")
            return ""
        except Exception as e:
            print(f"Error: {e}")
            return ""

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
        print("\n✨ Starting fresh! Ask me anything!\n")

    def run(self):
        """Run the interactive agent loop."""
        while True:
            try:
                # Get input
                user_input = input("\n🧒 You: ").strip().lower()

                # Handle commands
                if user_input in ['quit', 'exit', 'bye', 'q']:
                    print("\n👋 Bye! Keep being curious!\n")
                    break

                elif user_input == 'clear':
                    self.clear_history()
                    continue

                elif user_input == 'mute':
                    self.muted = True
                    print("🔇 Voice muted")
                    continue

                elif user_input == 'unmute':
                    self.muted = False
                    print("🔊 Voice unmuted")
                    continue

                elif user_input == 'voice':
                    user_input = self.listen()
                    if not user_input:
                        continue

                elif not user_input:
                    continue

                # Get answer
                print("\n🤔 Thinking...")
                answer = self.ask(user_input)
                print(f"\n🌟 Answer: {answer}")

                # Speak the answer
                self.speak(answer)

            except KeyboardInterrupt:
                print("\n\n👋 Bye! Keep being curious!\n")
                break
            except EOFError:
                break


def main():
    """Main entry point."""
    # Check for API key: config.json > environment variable > prompt
    api_key = load_api_key_from_config() or os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("\n⚠️  OpenAI API key not found!")
        print(f"\nTo use this agent, add your API key to: {CONFIG_FILE}")
        print('  {"openai_api_key": "sk-your-key-here"}')
        print("\nOr set environment variable:")
        print("  export OPENAI_API_KEY='your-key-here'")
        print("\nOr enter it now (will be saved to config.json):")
        api_key = input("API Key (or press Enter to quit): ").strip()
        if not api_key:
            print("Bye!")
            return
        # Save to config.json for future use
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({"openai_api_key": api_key}, f, indent=2)
            print(f"✅ API key saved to {CONFIG_FILE}")
        except Exception as e:
            print(f"Warning: Could not save to config.json: {e}")

    try:
        agent = CuriosityAgent(api_key=api_key)
        agent.run()
    except ValueError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n👋 Bye!")


if __name__ == "__main__":
    main()
