import json
import os
from openai import OpenAI
from dotenv import load_dotenv
from product_search import search_product

load_dotenv()
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_ITERATIONS = 12
MAX_HISTORY_MESSAGES = 30  # first message + last 29

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_product",
            "description": (
                "Search for a gift product online and verify it exists and links are real. "
                "Call this for EVERY suggestion before presenting it to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific product name and key attributes, e.g. 'Heath Ceramics seconds mug set'",
                    },
                    "budget": {
                        "type": "number",
                        "description": "Maximum price in USD",
                    },
                },
                "required": ["query", "budget"],
            },
        },
    }
]

SYSTEM_TEMPLATE = """\
You are Gift Whisperer, an expert gift advisor. Your job is to suggest thoughtful, specific, non-generic gifts.

Recipient profile:
{profile}

HARD CONSTRAINTS — violating any of these is an automatic failure:
- Budget: each gift must cost between ${budget_min} and ${budget} (inclusive). Gifts far below the minimum signal low effort. Always state the specific price with a $ sign.
- Already owns — do NOT suggest these items OR accessories/add-ons that only make sense if you own them: {already_owns}
  * Example: if they own a keyboard, do not suggest keyboard accessories (wrist rests, keycap sets, switches).
  * Example: if they own a Chemex, do not suggest Chemex filters or Chemex-specific accessories.
- Dislikes — do NOT suggest anything touching these categories: {dislikes}
  * If "experiences" is listed: suggest only tangible physical products. Never: classes, tours, tickets, event passes, subscriptions requiring scheduling, app memberships, or gift cards.
  * If "clutter" or "decor" is listed: suggest only functional objects with clear, frequent daily utility — no figurines, art prints, novelty items, or display pieces.
  * If "jewelry" is listed: suggest nothing worn on the body (rings, necklaces, bracelets, earrings, watches).
  * If "technology" or "tech" is listed: suggest nothing with a screen, app, or setup process.
- Previously rejected — never re-suggest these: {rejected}

SELF-CHECK before each suggestion: Ask "Would a thoughtful gift-giver who read the constraints above choose this?" If any constraint applies, pick a different idea.

Rules:
1. Suggest exactly 3 gifts per turn.
2. Before presenting any suggestion, you MUST call search_product for it.
3. Only present suggestions where search_product returns resolved=true.
4. If search_product returns resolved=false OR includes a "note" field, you MUST try a completely different product — do NOT present it.
5. Format each suggestion as JSON in your response using this exact structure:
   {{"suggestions": [{{"name": "...", "description": "...", "price_estimate": "$XX", "url": "...", "why": "..."}}]}}
6. The "why" field must connect this gift to the recipient's specific interests — never generic praise.
7. Never suggest anything generic (candles, gift cards, mugs with stock art, anything from a "top 10 gifts" listicle).
8. After the JSON block, ask one short follow-up question to refine further.
"""


def build_system_prompt(profile: dict, rejected: list[str]) -> str:
    budget = int(profile.get("budget", 50))
    budget_min = max(10, round(budget * 0.4))
    return SYSTEM_TEMPLATE.format(
        profile=_format_profile(profile),
        budget=budget,
        budget_min=budget_min,
        already_owns=", ".join(profile.get("already_owns", [])) or "none listed",
        dislikes=", ".join(profile.get("dislikes", [])) or "none listed",
        rejected=", ".join(rejected) or "none yet",
    )


def _format_profile(profile: dict) -> str:
    parts = []
    if profile.get("name"):
        parts.append(f"Name/description: {profile['name']}")
    if profile.get("age"):
        parts.append(f"Age: {profile['age']}")
    if profile.get("occupation"):
        parts.append(f"Occupation/identity: {profile['occupation']}")
    if profile.get("excitements"):
        parts.append(f"Recently excited about: {profile['excitements']}")
    if profile.get("last_gift"):
        parts.append(f"Last gift you gave them: {profile['last_gift']}")
    return "\n".join(parts)


def build_initial_message(profile: dict) -> dict:
    """Single source of truth for the opening user message sent to the LLM."""
    return {
        "role": "user",
        "content": (
            f"Please suggest gifts for: {profile['name']}, age {profile['age']}, "
            f"{profile['occupation']}. They've recently been excited about: {profile['excitements']}. "
            f"The last gift I gave them was: {profile['last_gift']}. Budget: ${int(profile['budget'])}."
        ),
    }


def _trim_messages(messages: list) -> list:
    """Keep the first message plus the most recent history to avoid hitting token limits."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    return [messages[0]] + messages[-(MAX_HISTORY_MESSAGES - 1):]


def run_turn_stream(messages: list, profile: dict, rejected: list[str]):
    """
    Generator that runs the agentic tool-call loop and yields progress events.

    Event shapes:
      {"type": "searching", "query": str}
      {"type": "done",      "text": str}
      {"type": "error",     "message": str}
    """
    system_prompt = build_system_prompt(profile, rejected)
    trimmed = _trim_messages(messages)
    full_messages = [{"role": "system", "content": system_prompt}] + trimmed

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=full_messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            full_messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                yield {"type": "searching", "query": args["query"]}
                result = search_product(
                    args["query"],
                    args.get("budget", profile.get("budget", 9999)),
                )
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        else:
            assistant_text = msg.content or ""
            messages.append({"role": "assistant", "content": assistant_text})
            yield {"type": "done", "text": assistant_text}
            return

    yield {"type": "error", "message": "Search took too long — please try again."}


def run_turn(messages: list, profile: dict, rejected: list[str]) -> tuple[str, list]:
    """Synchronous wrapper around run_turn_stream (used by the eval)."""
    for event in run_turn_stream(messages, profile, rejected):
        if event["type"] == "done":
            return event["text"], messages
        if event["type"] == "error":
            raise RuntimeError(event["message"])
    raise RuntimeError("Agent loop exited without producing a response.")


def parse_suggestions(text: str) -> list[dict]:
    """Extract the suggestions JSON block from assistant text."""
    import re
    text = re.sub(r"```(?:json)?\s*", "", text)

    for match in re.finditer(r"\{", text):
        start = match.start()
        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        blob = text[start:end]
        try:
            parsed = json.loads(blob)
            if "suggestions" in parsed:
                return parsed["suggestions"]
        except json.JSONDecodeError:
            continue
    return []
