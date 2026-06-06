# ThinkTank

ThinkTank is a multi-LLM debate arena with an RPG battle UI. Configure real API keys for OpenAI, Google Gemini, Anthropic Claude, and xAI Grok, build a roster of AI fighters, choose any debate topic, and watch arguments stream live while each fighter's Argument Strength bar updates after every turn.

## What It Does

- Runs a three-screen flow: Settings, Setup, and Battle.
- Supports OpenAI `gpt-4o`, Google Gemini `gemini-1.5-pro`, Anthropic `claude-opus-4-5`, and xAI `grok-3`.
- Streams each debater through FastAPI Server-Sent Events.
- Scores every completed argument with OpenAI `gpt-4o-mini` when an OpenAI key is configured, with a local fallback score if not.
- Delivers a final Judge verdict using OpenAI when available, otherwise the first configured provider.
- Stores no data in a database. API keys live only in the browser session and are sent with each request.

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8010
```

Open [http://127.0.0.1:8010](http://127.0.0.1:8010).

The app also reads `PORT` if you run `python main.py`:

```bash
$env:PORT="8010"
python main.py
```

## Provider Setup

On the Settings screen, paste API keys for at least two providers:

- OpenAI: used for `gpt-4o` debaters and `gpt-4o-mini` scoring.
- Google Gemini: used for `gemini-1.5-pro` debaters.
- Anthropic Claude: used for `claude-opus-4-5` debaters.
- xAI Grok: uses the OpenAI SDK pointed at `https://api.x.ai/v1` for `grok-3`.

Use **Validate Keys** to test configured providers. Continue becomes available once at least two provider keys are non-empty.

## Demo Flow

1. Start the backend on port `8010`.
2. Open the app.
3. Configure two real provider keys, for example OpenAI and Gemini.
4. Create Nova on OpenAI and Rex on Gemini.
5. Set the topic to `Will AI replace programmers?`.
6. Start a 2-round battle and watch streaming messages, live scoring, the Judge verdict, confetti, and the winner crown.

## How Codex Built It

Codex rebuilt the codebase as a compact FastAPI and vanilla JavaScript application. The backend defines shared Pydantic models, a unified async `call_llm_stream` provider router, SSE event emission, key validation endpoints, scoring, transcript management, and final judging. The frontend is a single `index.html` with RPG portrait cards, provider badges, animated strength bars, SSE parsing, typing indicators, a judge verdict card, mobile layouts, and pure CSS completion effects.
