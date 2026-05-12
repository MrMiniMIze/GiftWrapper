import json
import os
from openai import OpenAI
from dotenv import load_dotenv
from product_search import search_product

load_dotenv()
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

MODEL = "gpt-4o-mini"

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
- Budget: each gift must cost ${budget} or less. State the specific price.
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
4. If search_product returns resolved=false, silently try a different product instead.
5. Format each suggestion as JSON in your response using this exact structure:
   {{"suggestions": [{{"name": "...", "description": "...", "price_estimate": "$XX", "url": "...", "why": "..."}}]}}
6. The "why" field must connect this gift to the recipient's specific interests — never generic praise.
7. Never suggest anything generic (candles, gift cards, mugs with stock art, anything from a "top 10 gifts" listicle).
8. After the JSON block, ask one short follow-up question to refine further.
"""


def build_system_prompt(profile: dict, rejected: list[str]) -> str:
    return SYSTEM_TEMPLATE.format(
        profile=_format_profile(profile),
        budget=profile.get("budget", "unknown"),
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


def run_turn(messages: list, profile: dict, rejected: list[str]) -> tuple[str, list]:
    """
    Run one LLM turn with function-calling loop.
    Returns (assistant_text, updated_messages).
    """
    system_prompt = build_system_prompt(profile, rejected)
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    # Agentic loop: keep calling until no more tool calls
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=full_messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            # Add assistant message with tool calls
            full_messages.append(msg)

            # Execute each tool call
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = search_product(args["query"], args.get("budget", profile.get("budget", 9999)))
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        else:
            # Final text response
            assistant_text = msg.content or ""
            # Append to the user-facing messages (without system)
            messages.append({"role": "assistant", "content": assistant_text})
            return assistant_text, messages


def parse_suggestions(text: str) -> list[dict]:
    """Extract the suggestions JSON block from assistant text."""
    import re
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text)

    # Find the first '{' that begins a block containing "suggestions"
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
