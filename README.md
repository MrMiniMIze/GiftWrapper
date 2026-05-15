# Gift Whisperer

Describe someone — their job, what they've been excited about, what you've already given them — and get specific, constraint-aware gift ideas with real product links.

## What it does

Gift Whisperer uses a multi-turn conversation with OpenAI's GPT-4o-mini to generate personalized gift suggestions. For each suggestion, it uses function calling to search the web and verify the product link resolves before showing it to you. You can reject suggestions, mark things as already owned, or say you love a direction — the model remembers every constraint across turns.

## Setup

**Requirements:** Python 3.11+, an OpenAI API key.

```bash
# 1. Clone and enter the project
git clone https://github.com/MrMiniMIze/GiftWrapper.git
cd GiftWrapper

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your API key
cp .env.example .env
# Open .env and set: OPENAI_API_KEY=sk-...

# 5. Run the app
python app.py
```

Then open http://localhost:5001 in your browser.

> **Note:** On startup you may see a `UserWarning: FLASK_SECRET is not set`. This is expected and harmless for local use — the app runs normally.

## Running the eval

```bash
# Run all 12 test cases (includes URL verification, takes ~5 min)
python eval/eval.py

# Run a single case
python eval/eval.py --case TC01

# Skip URL checks (faster, useful for prompt iteration)
python eval/eval.py --no-search

# Save results to a file
python eval/eval.py --output eval/results_v1.json
```

The metric is `pass_rate = passing suggestions / total suggestions`. A suggestion passes if: its URL resolves, its stated price is within budget, and its name/description contains none of the recipient's banned keywords or duplicate of the last gift given.

## Project structure

```
app.py              Flask server + session management
gift_engine.py      OpenAI multi-turn loop with function calling
product_search.py   DuckDuckGo search + HTTP link verification
templates/          HTML UI
static/             CSS
eval/
  test_cases.json   12 labeled test cases
  eval.py           Evaluation script
```
