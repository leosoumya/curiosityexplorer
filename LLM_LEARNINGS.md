# LLM Integration Learnings

Lessons learned from building a kid-friendly Q&A feature with LLM integration.

---

## 1. Hardcoded Facts in Prompts Get Outdated

**Issue:** Example in prompt said "Saturn has 274 moons" - wrong number.

**Fix:**
- Don't hardcode specific facts in examples
- Use web search tool for real-time accuracy
- Or use obviously fake/rounded numbers in examples

**Bad:**
```
You: "Saturn has 274 moons!"
```

**Better:**
```
You: "Saturn has 146 moons!" (let web search correct it)
```

---

## 2. Web Search Tool Must Be Explicitly Enabled

**Issue:** AI gave outdated or incorrect facts without web search.

**Fix:**
```javascript
tools: [{ type: 'web_search' }],
tool_choice: 'required'  // Force it to search
```

---

## 3. Stopping Unwanted Behaviors Requires STRONG Prompting

**Issue:** AI kept asking follow-up questions despite being told not to.

**What didn't work:**
- "Do NOT ask follow-up questions" (ignored)
- "Let the child ask their own questions" (ignored)

**What worked:**
```
STRICT RULES:
1. NEVER ask a question back.
2. NO "Do you know...?" NO "Can you...?" NO "What do you think...?"

BAD (never do this):
"Can you guess?" ❌
"Do you know what else?" ❌
"What do you think?" ❌

REMEMBER: End with period or exclamation, NEVER a question mark.
```

**Lesson:** Use multiple reinforcement techniques:
- State the rule positively AND negatively
- Give explicit BAD examples
- Repeat the rule in different sections
- Be specific about punctuation/format

---

## 4. Getting Links to Appear in Output

**Issue:** AI wasn't including source links in responses.

**Fix:** Explicitly request the format:
```
FORMAT:
1. Fun answer (2 sentences max)
2. One emoji
3. "Learn more:" with a markdown link [title](url)

GOOD EXAMPLE:
"Cheetahs run super fast! 🐆 Learn more: [Cheetah Facts](https://url)"
```

**Lesson:** If you want specific output format, show exactly what it looks like.

---

## 5. Text-to-Speech Needs Heavy Sanitization

**Issue:** Speech was reading out URLs, which sounds terrible.

**Fix:** Strip multiple patterns before speaking:
```javascript
let cleanText = text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')  // [text](url) → text
    .replace(/<a[^>]*>([^<]*)<\/a>/gi, '$1')  // HTML links
    .replace(/https?:\/\/[^\s\])]+/g, '')     // https://...
    .replace(/www\.[^\s\])]+/g, '')           // www....
    .replace(/\[\d+\]/g, '')                  // [1] citations
    .replace(/Source:.*$/gm, '')              // Source: lines
    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')   // Emojis
```

**Lesson:** URLs come in many formats - handle them all.

---

## 6. Kid-Friendly Language Requires Explicit Constraints

**Issue:** AI used words too complex for 5-7 year olds.

**Fix:** Be very specific:
```
HOW TO TALK:
- Simple words only (say "big" not "large")
- Compare to kid things (big as a bus)
- Short sentences (5-7 words max)
```

**Lesson:** "Simple language" is vague. Give concrete word counts and examples.

---

## 7. Browser Caching Hides Changes

**Issue:** User couldn't see updates after deployment.

**Fix:**
- Hard refresh: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows)
- Add cache-busting: `?v=2` to URL
- Test in incognito window

---

## 8. Prompt Instructions in Multiple Places

**Issue:** System prompt said one thing, but per-request prompt contradicted it.

**Fix:** Keep instructions consistent:
```javascript
// System prompt (SOCRATIC_PROMPT)
"NEVER ask a question back"

// Per-request prompt (fullInput)
"Do NOT end with a question"  // Reinforces, doesn't contradict
```

---

## 9. Show Don't Just Tell

**Issue:** Telling the AI "be fun" didn't make it fun enough.

**Fix:** Show examples of the exact tone:
```
Say "Wow!" or "Cool!" to be fun

EXAMPLE:
"Wow, Saturn has 146 moons! That is so many! 🪐"
```

---

## 10. One Emoji is Enough

**Issue:** AI sometimes used too many emojis, cluttering the response.

**Fix:** Specify exact count:
```
- One emoji only
```

---

## Summary Checklist

When integrating an LLM for a specific use case:

- [ ] Enable necessary tools (web search, etc.)
- [ ] Don't hardcode facts that can change
- [ ] State rules both positively AND negatively
- [ ] Give BAD examples of what NOT to do
- [ ] Show exact output format with examples
- [ ] Specify concrete constraints (word count, emoji count)
- [ ] Sanitize output for secondary uses (TTS, display)
- [ ] Keep system + per-request prompts consistent
- [ ] Test with cache cleared
