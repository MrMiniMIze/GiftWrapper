# Gift Whisperer — REPORT.md

---

## Part 1: What & Why (~230 words)

Gift Whisperer is a web app that helps users find specific, constraint-aware gift ideas for people they know well. The user fills out a profile for their recipient — job, recent excitements, what they already own, what they dislike, and a budget — and the app returns three verified gift suggestions with real product links. Users can then reject suggestions, flag items as already owned, or signal they love a direction, and the model incorporates that feedback in the next turn.

The app is for people who are bad at gifts not because they don't care, but because generic gift lists don't account for the one specific person they're buying for. My own experience shopping for a ceramicist who already has every tool, or a dad who hates anything requiring scheduling, is exactly this problem.

Getting the AI behavior right is genuinely hard for three reasons. First, the model must simultaneously satisfy multiple hard constraints (budget, owned items, dislikes, rejected history) while still being creative — these goals pull against each other. Second, the model has a strong prior toward clichéd suggestions ("a Noguchi coffee table book" for an art fan) and resists constraint lists unless they're phrased with precision. Third, "does this product actually exist and ship?" requires a separate verification step — a single LLM call confidently produces plausible-sounding products with invented links, so we need real web search and HTTP verification in the loop, which is a second AI-adjacent behavior that must also be reliable.

---

## Part 2: Iterations

### V1 — Baseline multi-turn with function calling

**Change:** Built the initial multi-turn system with conversation history, OpenAI function calling to trigger `search_product()` (DuckDuckGo + HTTP HEAD verification), and a system prompt that listed constraints once. Eval used substring keyword matching against name + description + why fields.

**Motivating example:** TC01 (ceramicist sister): the model returned "Noguchi Ceramic Sculpture" as a suggestion, which directly violates the explicit "dislikes: anything Noguchi-related" constraint. Despite the constraint being listed in the system prompt, the model treated it as a soft suggestion rather than a hard boundary.

**Delta:** 29/36 = **80.56%**

**Conclusion:** The baseline prompt was too weak. Constraints listed in a flat bullet list were often ignored for "thematically interesting" suggestions. Additionally, the eval's substring matching caused false positives — "ring" matched inside "exploring" (in the `why` field), artificially deflating the score for TC01. The model was fundamentally sound at multi-turn preference tracking but needed much stronger constraint enforcement language.

---

### V2 — Stronger constraint prompt + word-boundary keyword matching

**Change:** Rewrote the constraint section of the system prompt to use "HARD CONSTRAINTS" framing with specific subclauses ("If 'experiences' is listed: suggest only physical products, never classes, tours, tickets…"). Added a self-check instruction before each suggestion. Fixed the eval to use word-boundary regex matching (`\b` anchors) so "ring" no longer matches inside "exploring" or "providing."

**Motivating example:** TC02 (retired engineer dad): the model returned "Birdwatching Field Guide App Subscription," which violates the "no experiences" dislike because an app subscription requires scheduling engagement. The old prompt's generic "dislikes" bullet didn't distinguish between the *product type* "experience" and the word "experience" appearing in prose, causing the model to rationalize the suggestion anyway.

**Delta:** 32/36 = **88.89%** (+8.33 pp)

**Conclusion:** Precise constraint sub-clauses with concrete examples ("never: classes, tours, tickets, event passes, subscriptions requiring scheduling") significantly reduced violations for the "experiences" and "jewelry" categories. Word-boundary matching in the eval eliminated several false positives. However, TC01 still occasionally failed because the model reinterprets "Noguchi-adjacent" products as merely "inspired by" the artist rather than violating the constraint. The model's tendency to suggest keyword-adjacent items (keyboard wrist rest when "keyboard" is banned) also persisted, suggesting the model interprets "already owns X" as "don't suggest the same X," not "don't suggest X accessories."

---

### V3 — Adjacent-item rule in prompt + eval scope fix

**Change:** Added explicit "adjacent accessories" guidance to the system prompt: "do NOT suggest accessories or add-ons that only make sense if you own them" with concrete examples. Fixed the eval to exclude the `why` field from keyword checking — since `why` is model reasoning prose, it naturally contains constraint words in non-violating contexts ("enhances the birdwatching experience" in a why-field shouldn't fail a product on "experience").

**Motivating example:** TC10 (remote worker): the model returned "Custom Metal + Acrylic Keyboard Wrist Rest" despite "keyboard" being in the banned keywords. The model interpreted the constraint as "don't suggest a keyboard" but didn't generalize to "don't suggest anything that requires a keyboard to be useful." Adding an explicit "accessories" clause with the wrist rest example was intended to teach this generalization.

**Delta:** 32/36 = **88.89%** (no change)

**Conclusion:** The aggregate metric didn't improve. TC01 improved to 3/3 (Noguchi violations eliminated), but TC04 and TC06 each dropped one pass due to the stochastic nature of the model selecting different suggestions each run — a foam roller massage ball set appeared for the runner who already owns a foam roller, and a "Dinopedia" for the kid who owns every dinosaur encyclopedia. TC10's keyboard accessory failure persisted despite the prompt addition. The 88.89% ceiling reveals two structural limitations: (1) the LLM's stochasticity makes single-run evals noisy for edge cases, and (2) the "experience" keyword in TC02 is inherently ambiguous — it appears in any product description as normal prose, so keyword matching can't distinguish the product *type* "experience" from the word "experience." A cleaner V4 would replace keyword matching with a second LLM call that judges constraint satisfaction semantically rather than lexically.

---

## Part 3: Code Walkthrough (~260 words)

**Tracing a user click of "Love it" on a suggestion:**

1. **`templates/index.html`, `makeActionBtn()` → `callFeedback()`:** When the user clicks "Love it," the browser calls `callFeedback({ action: "love", item_name: "Sourdough Starter Kit" })`. This sends a `POST /api/feedback` request with that JSON body.

2. **`app.py:79`, `feedback()` route:** Flask reads the session ID from the cookie and retrieves the in-memory conversation state (`_sessions[sid]`). The `action == "love"` branch appends a user message to `state["messages"]`: `"I love the 'Sourdough Starter Kit' suggestion! Can you give me 2 more ideas in a similar vein?"`. This is the mechanism by which preference learning happens — the model sees its own earlier suggestion praised in the conversation history and uses that context.

3. **`app.py:101` → `gift_engine.py:91`, `run_turn()`:** The updated messages list, recipient profile, and rejected list are passed to `run_turn`. The function rebuilds the system prompt from the profile (including the current `rejected` list), prepends it as the system message, and enters an agentic loop. Inside the loop, the model decides to call `search_product` for each new idea.

4. **`gift_engine.py:108` → `product_search.py:10`, `search_product()`:** Each tool call triggers a DuckDuckGo search and an HTTP HEAD request to verify the top URL resolves. The result `{url, title, resolved}` is returned to the model as a tool message.

5. **`gift_engine.py:122`, final text response:** Once the model emits a response without tool calls, `run_turn` appends it to `messages` and returns. Back in `app.py`, `parse_suggestions()` extracts the `{"suggestions": [...]}` JSON block from the reply text (scanning for the first brace-balanced JSON object containing a `"suggestions"` key). The route returns `{reply, suggestions}` to the browser.

**Design decision:** Conversation state is stored in a Python dict (`_sessions`) keyed by a per-session UUID rather than a database. The alternative was SQLite, which would survive server restarts and support concurrent users. I rejected SQLite because this is a local demo app where the grader runs one session, Flask debug mode restarts are frequent during development, and the added setup cost (schema migration, connection pooling) outweighed the benefit. The stateless-per-restart behavior is a known, acceptable trade-off documented in the README.

---

## Part 4: AI Disclosure & Safety (~190 words)

**How I used Claude Code (AI coding assistant):**

I used Claude Code (Anthropic's CLI) as my primary coding assistant throughout this project. Three specific moments it failed and how I recovered:

1. **Port conflict:** Claude started the Flask server on port 5000, which macOS silently intercepts for AirPlay Receiver, returning a 403 from `AirTunes/940`. Claude initially didn't recognize the symptom, but I diagnosed it from the `Server: AirTunes` response header and switched to port 5001.

2. **f-string brace escaping:** The system prompt template used `.format()` with `{"suggestions": [...]}` as an example — the curly braces were treated as format fields, causing `KeyError: '"suggestions"'`. Claude initially looked for the bug in `parse_suggestions` before tracing it to the template.

3. **`parse_suggestions` fragility:** The initial implementation searched for the literal string `'{"suggestions"'`, which broke when the model returned pretty-printed JSON starting with `{\n  "suggestions"`. Claude fixed it by scanning for any brace-balanced JSON block containing a `suggestions` key.

**Safety risks and mitigations:**

The primary safety risk is **hallucinated product links** — the model can produce confident, plausible-looking URLs that resolve to unrelated or non-existent pages. The mitigation is the `search_product` function call, which performs a real DuckDuckGo search and HTTP HEAD verification before any link is shown. This doesn't guarantee the product is in stock or accurately described, but it eliminates dead links. A secondary risk is **constraint violation harm** — if the model suggests a $200 item for a $50 budget, the user may feel the app is useless. We accepted this as a known limit: the model states prices but we do not scrape and verify them independently.
