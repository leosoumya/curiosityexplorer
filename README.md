# Whyzee

A kids' curiosity/learning web app — Flask backend (`app.py`) + single-page UI (`index.html`).

## Status: Paused 2026-04-29

**What's built.** Two main experiences:
- **Ask Me Anything** — chat that returns kid-friendly answers with TTS, relevant images (Wikipedia-first with a web-image fallback, all proxied to dodge rate limits), and a tap-to-zoom lightbox.
- **Spot the Mistake** — game that generates plausible-but-wrong facts for a chosen topic, with subtopic rotation and an anti-hallucination guardrail.

The app is PWA-installable with a custom magnifying-glass icon, mobile/tablet-friendly, logs game activity to Supabase and questions to localStorage for a Parent View, and ships with a first-visit parent disclaimer modal plus curated "magic" onboarding questions for new kids.

**Where I stopped.** Post-Kabir alpha-feedback polish — last commits were UX tweaks to onboarding, image handling, and Spot-the-Mistake feedback copy.

**Likely next steps when I return.** More alpha testing with kids, expand the Parent View, and decide whether to invest further in content quality vs. distribution.
