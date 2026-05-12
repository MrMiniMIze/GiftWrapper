import os
import uuid
from flask import Flask, request, jsonify, session, render_template
from dotenv import load_dotenv
from gift_engine import run_turn, parse_suggestions

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))

# In-memory store keyed by session_id (fine for local/demo use)
_sessions: dict[str, dict] = {}


def _get_state(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {"messages": [], "profile": {}, "rejected": []}
    return _sessions[session_id]


@app.route("/")
def index():
    session.setdefault("id", str(uuid.uuid4()))
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start():
    """Initialize a new conversation with a recipient profile."""
    session["id"] = str(uuid.uuid4())  # fresh session per profile submit
    data = request.json or {}

    profile = {
        "name": data.get("name", "").strip(),
        "age": data.get("age", "").strip(),
        "occupation": data.get("occupation", "").strip(),
        "excitements": data.get("excitements", "").strip(),
        "last_gift": data.get("last_gift", "").strip(),
        "already_owns": [x.strip() for x in data.get("already_owns", "").split(",") if x.strip()],
        "dislikes": [x.strip() for x in data.get("dislikes", "").split(",") if x.strip()],
        "budget": float(data.get("budget", 50)),
    }

    state = _get_state(session["id"])
    state["profile"] = profile
    state["messages"] = [
        {
            "role": "user",
            "content": (
                f"Please suggest gifts for: {profile['name']}, age {profile['age']}, "
                f"{profile['occupation']}. They've recently been excited about: {profile['excitements']}. "
                f"The last gift I gave them was: {profile['last_gift']}. Budget: ${profile['budget']}."
            ),
        }
    ]

    try:
        text, updated = run_turn(state["messages"], profile, state["rejected"])
        state["messages"] = updated
        suggestions = parse_suggestions(text)
        return jsonify({"reply": text, "suggestions": suggestions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/feedback", methods=["POST"])
def feedback():
    """Accept user feedback and continue the conversation."""
    sid = session.get("id")
    if not sid or sid not in _sessions:
        return jsonify({"error": "Session expired. Please start over."}), 400

    state = _get_state(sid)
    data = request.json or {}
    action = data.get("action")  # "love", "reject", "owns", "message"
    item_name = data.get("item_name", "")
    user_message = data.get("message", "")

    if action == "reject" and item_name:
        state["rejected"].append(item_name)
        state["messages"].append({
            "role": "user",
            "content": f"That suggestion isn't right — please skip '{item_name}' and suggest something different.",
        })
    elif action == "owns" and item_name:
        state["profile"].setdefault("already_owns", []).append(item_name)
        state["messages"].append({
            "role": "user",
            "content": f"They already own '{item_name}'. Please replace it with something else.",
        })
    elif action == "love" and item_name:
        state["messages"].append({
            "role": "user",
            "content": f"I love the '{item_name}' suggestion! Can you give me 2 more ideas in a similar vein?",
        })
    elif user_message:
        state["messages"].append({"role": "user", "content": user_message})
    else:
        return jsonify({"error": "No valid action or message provided."}), 400

    try:
        text, updated = run_turn(state["messages"], state["profile"], state["rejected"])
        state["messages"] = updated
        suggestions = parse_suggestions(text)
        return jsonify({"reply": text, "suggestions": suggestions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    if "id" in session:
        _sessions.pop(session["id"], None)
    session.pop("id", None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
