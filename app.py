import json
import os
import time
import uuid
import warnings
from collections import defaultdict
from flask import Flask, Response, request, jsonify, session, render_template, stream_with_context
from dotenv import load_dotenv
from gift_engine import run_turn_stream, parse_suggestions, build_initial_message

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET")
if not app.secret_key:
    app.secret_key = os.urandom(24)
    warnings.warn(
        "FLASK_SECRET is not set — using a random key. "
        "Sessions will invalidate on every restart. "
        "Add FLASK_SECRET=<random-string> to your .env file.",
        stacklevel=1,
    )

app.config["MAX_CONTENT_LENGTH"] = 32 * 1024  # 32 KB hard cap on request bodies

# ── In-memory session store ────────────────────────────────────────────────
_sessions: dict[str, dict] = {}
_session_timestamps: dict[str, float] = {}
SESSION_TTL = 3600  # seconds

# ── Rate limiting ──────────────────────────────────────────────────────────
_rate_limits: dict[str, list] = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > cutoff]
    if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
        return False
    _rate_limits[ip].append(now)
    return True


def _get_state(session_id: str) -> dict:
    now = time.time()
    # Prune expired sessions to prevent unbounded memory growth
    expired = [sid for sid, ts in list(_session_timestamps.items()) if now - ts > SESSION_TTL]
    for sid in expired:
        _sessions.pop(sid, None)
        _session_timestamps.pop(sid, None)

    if session_id not in _sessions:
        _sessions[session_id] = {"messages": [], "profile": {}, "rejected": []}
    _session_timestamps[session_id] = now
    return _sessions[session_id]


def _parse_profile(data: dict) -> tuple[dict, str | None]:
    """Validate and coerce raw request data into a profile. Returns (profile, error_msg)."""
    try:
        budget = int(float(data.get("budget") or 50))
    except (ValueError, TypeError):
        return {}, "Budget must be a number."
    if budget < 1:
        return {}, "Budget must be at least $1."

    name = str(data.get("name", "")).strip()
    if not name:
        return {}, "Recipient name is required."

    profile = {
        "name": name,
        "age": str(data.get("age", "")).strip(),
        "occupation": str(data.get("occupation", "")).strip(),
        "excitements": str(data.get("excitements", "")).strip(),
        "last_gift": str(data.get("last_gift", "")).strip(),
        "already_owns": [x.strip() for x in str(data.get("already_owns", "")).split(",") if x.strip()],
        "dislikes": [x.strip() for x in str(data.get("dislikes", "")).split(",") if x.strip()],
        "budget": budget,
    }
    return profile, None


def _sse_stream(messages: list, profile: dict, rejected: list, sid: str):
    """Generator that wraps run_turn_stream into SSE-formatted lines."""
    try:
        for event in run_turn_stream(messages, profile, rejected):
            if event["type"] == "done":
                suggestions = parse_suggestions(event["text"])
                _sessions[sid]["messages"] = messages
                payload = {"type": "done", "reply": event["text"], "suggestions": suggestions}
            elif event["type"] == "error":
                payload = {"type": "error", "message": event["message"]}
            else:
                payload = event
            yield f"data: {json.dumps(payload)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


def _sse_response(generator):
    resp = Response(stream_with_context(generator), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/")
def index():
    session.setdefault("id", str(uuid.uuid4()))
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start():
    if not _check_rate_limit(request.remote_addr):
        return jsonify({"error": "Too many requests — please wait a moment."}), 429

    data = request.json or {}
    profile, err = _parse_profile(data)
    if err:
        return jsonify({"error": err}), 400

    session["id"] = str(uuid.uuid4())
    state = _get_state(session["id"])
    state["profile"] = profile
    state["messages"] = [build_initial_message(profile)]

    messages = state["messages"]
    rejected = state["rejected"]
    sid = session["id"]

    return _sse_response(_sse_stream(messages, profile, rejected, sid))


@app.route("/api/feedback", methods=["POST"])
def feedback():
    if not _check_rate_limit(request.remote_addr):
        return jsonify({"error": "Too many requests — please wait a moment."}), 429

    sid = session.get("id")
    if not sid or sid not in _sessions:
        return jsonify({"error": "Session expired. Please start over."}), 400

    state = _get_state(sid)
    data = request.json or {}
    action = data.get("action")
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

    messages = state["messages"]
    profile = state["profile"]
    rejected = state["rejected"]

    return _sse_response(_sse_stream(messages, profile, rejected, sid))


@app.route("/api/reset", methods=["POST"])
def reset():
    if "id" in session:
        _sessions.pop(session["id"], None)
        _session_timestamps.pop(session["id"], None)
    session.pop("id", None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
