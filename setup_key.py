#!/usr/bin/env python3
"""
Helper script to set up the shared API key for Curiosity Explorer.

Run this once to configure your OpenAI API key for both:
- The Python agent (curiosity_agent.py)
- The web app (index.html)
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

def main():
    print("\n🔑 Curiosity Explorer - API Key Setup")
    print("=" * 40)

    # Check existing key
    existing_key = None
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                existing_key = config.get("openai_api_key", "").strip()
        except:
            pass

    if existing_key:
        masked = existing_key[:7] + "..." + existing_key[-4:]
        print(f"\nCurrent API key: {masked}")
        choice = input("Replace it? (y/N): ").strip().lower()
        if choice != 'y':
            print("Keeping existing key.")
            return

    print("\n📋 To get your API key from the web app:")
    print("   1. Open the Curiosity Explorer web app")
    print("   2. Press F12 to open Developer Tools")
    print("   3. Go to Console tab")
    print("   4. Type: localStorage.getItem('openaiApiKey')")
    print("   5. Copy the key (without quotes)")

    print("\n" + "-" * 40)
    api_key = input("\nPaste your OpenAI API key: ").strip()

    # Remove quotes if accidentally included
    api_key = api_key.strip('"\'')

    if not api_key:
        print("No key entered. Exiting.")
        return

    if not api_key.startswith("sk-"):
        print("⚠️  Warning: OpenAI keys usually start with 'sk-'")
        confirm = input("Continue anyway? (y/N): ").strip().lower()
        if confirm != 'y':
            return

    # Save to config.json
    config = {"openai_api_key": api_key}

    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"\n✅ API key saved to {CONFIG_FILE}")
        print("\nYou can now run:")
        print("   python curiosity_agent.py")
    except Exception as e:
        print(f"\n❌ Error saving config: {e}")
        return

    print("\n" + "=" * 40)
    print("Setup complete! 🎉")


if __name__ == "__main__":
    main()
