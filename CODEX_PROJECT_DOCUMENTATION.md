# ThinkTank Codex Project Documentation

This document explains what Codex built in the ThinkTank project and how the
codebase fits together. It is written as a practical guide for someone who wants
to understand, run, maintain, or extend the app.

## What Codex Built

Codex built ThinkTank as a full-stack AI debate application called **ThinkTank
Pixel Council**. The app lets a user enter a question, choose from backend
configured LLM providers, configure a group of AI council members, and watch
those agents debate the topic in a live pixel-styled chamber.

The project includes:

- A React and Vite frontend for the interactive user experience.
- A FastAPI backend for provider metadata, provider validation, debate streams,
  JudgeAI scoring, security middleware, and static frontend serving.
- Backend-managed LLM support for Gemini and OpenAI.
- Server-Sent Events streaming so arguments appear live token by token.
- Cold Start Trials, where agents in later rounds cannot see their own previous
  response and must react only to other agents.
- A Contradiction Map that extracts claims and detects simple agreement or
  conflict between agents.
- Consensus-seeking debate rounds that continue until convergence is detected,
  with a configurable safety cap.
- JudgeAI, a response comparison workspace that scores two candidate answers
  against the same prompt.
- Backend security controls such as CORS limits, body size limits, rate limits,
  provider/model allow-listing, safe static file lookup, and browser hardening
  headers.
- Tests that check debate stream behavior, validation rules, rate limits,
  security headers, and static file safety.

## Project Shape

ThinkTank is split into a Python backend and a JavaScript frontend.

```text
main.py                 FastAPI application entrypoint
thinktank/              Backend domain modules
src/                    React frontend
tests/                  Backend tests
index.html              Vite HTML shell
package.json            Frontend scripts and dependencies
requirements.txt        Python dependencies
.env.example            Runtime configuration example
README.md               Short project overview and run guide
```

There is no database. Provider API keys are supplied through backend environment
variables and are never sent by the browser.

## How The App Runs

For local development, install the Python and Node dependencies, then run the
backend and frontend separately.

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
npm install
```

Create `.env` from `.env.example` and configure provider keys:

```powershell
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

Start the backend:

```powershell
.\.venv\Scripts\python -m uvicorn main:app --reload --port 8010
```

Start the frontend:

```powershell
npm run dev
```

Open the Vite app at:

```text
http://127.0.0.1:5173
```

For a production-style build:

```powershell
npm run build
.\.venv\Scripts\python -m uvicorn main:app --port 8010
```

Then open:

```text
http://127.0.0.1:8010
```

In production-style mode, FastAPI serves the built frontend from `dist/`.

## Backend Walkthrough

### `main.py`

`main.py` is the FastAPI application entrypoint. It creates the app, configures
CORS, mounts static frontend assets when `dist/assets` exists, and defines all
public API routes.

Important responsibilities:

- `GET /api/health` returns a basic health check.
- `GET /api/providers` returns public provider metadata for the UI.
- `POST /api/validate-provider` validates one configured provider/model combination.
- `POST /api/debate` starts the streamed debate.
- `POST /api/judgeai` scores two candidate responses through JudgeAI.
- `GET /{path:path}` serves built frontend files or returns a clear missing-build
  error.

The file also contains security middleware. It rejects oversized request bodies,
applies in-memory rate limits to expensive endpoints, and adds browser hardening
headers such as CSP, `X-Frame-Options`, `Referrer-Policy`, and
`X-Content-Type-Options`.

### `thinktank/models.py`

`models.py` defines the Pydantic models that protect the backend from malformed
input. These models describe the request shapes used by provider validation,
debate sessions, JudgeAI, and transcript entries.

Important models:

- `ProviderValidationRequest` validates provider and model fields.
- `FeatureFlags` controls Cold Start Trials and the Contradiction Map.
- `CouncilAgent` describes one debate participant.
- `DebateRequest` validates the topic, consensus round cap, agents, optional legacy
  provider keys, and feature flags.
- `JudgeAIRequest` validates the JudgeAI prompt, candidates, and model.
- `TranscriptEntry` stores completed debate arguments for later rounds and final
  judging.

`DebateRequest` also enforces important rules: agent IDs and names must be
unique, and legacy provider keys are length capped when sent by older clients.

### `thinktank/providers.py`

`providers.py` is the LLM provider layer. It defines the provider registry and
normalizes streaming across configured vendors.

The `PROVIDERS` registry lists supported providers, display names, UI colors,
key placeholders, and allowed models. The backend filters this registry by
`GEMINI_API_KEY` and `OPENAI_API_KEY`, and rejects unsupported or unconfigured
provider/model combinations before making upstream API calls.

Streaming support is handled through `stream_llm()`, which routes calls to:

- OpenAI through the OpenAI SDK.
- Gemini through the Google Generative AI SDK.

The rest of the backend can consume all providers as a single async token stream.

### `thinktank/debate_engine.py`

`debate_engine.py` orchestrates the live council debate. The main function is
`run_debate()`, an async generator that emits Server-Sent Event payloads.

The debate flow is:

1. Create a session ID.
2. Emit `session_start`.
3. For each round, emit `round_start`.
4. For each agent, emit `agent_start`.
5. Build the agent prompt and visible context.
6. Stream provider tokens as `token` events.
7. Store the completed argument in the transcript.
8. Emit `agent_done`.
9. Analyze claims for convergence or contradiction.
10. Stop early if consensus is detected.
11. Optionally emit `memory_wipe` between rounds.
12. Ask the judge agent for the final verdict.
13. Emit `winner` and `done`.

Cold Start Trials are implemented when building later-round agent messages. If
the feature is on, an agent receives only the previous round's arguments from
other agents, not its own earlier argument.

### `thinktank/analysis.py`

`analysis.py` contains the lightweight Contradiction Map logic. It is intentionally
simple and fast rather than a full semantic reasoning system.

The analyzer:

- Splits each argument into usable sentences.
- Extracts keywords while removing common stopwords.
- Estimates simple positive, negative, or neutral polarity.
- Compares claims from different agents with Jaccard similarity.
- Emits convergence when claims share enough keywords and do not conflict in
  polarity.
- Emits contradiction when similar claims have opposing non-neutral polarity.

The frontend uses these claim and link events to populate the live claim map.

### `thinktank/judgeai.py`

`judgeai.py` powers the standalone JudgeAI response comparator. It asks OpenAI
to evaluate two candidate responses against the same prompt and return structured
JSON.

JudgeAI scores each candidate on:

- Accuracy
- Completeness
- Clarity
- Safety
- Reasoning

The returned JSON is validated with Pydantic. The backend then totals the scores,
chooses a winner, applies deterministic tie-breakers, calculates confidence, and
returns a report that the frontend can display or export.

## Frontend Walkthrough

### `src/main.jsx`

`src/main.jsx` contains the React single-page app. It manages all UI screens,
state, provider setup, debate streaming, and JudgeAI interactions.

Major screens:

- Landing screen: user enters a debate question.
- Provider console: user pastes and tests provider API keys.
- Council setup: user edits agents, roles, providers, models, rounds, and
  feature flags.
- Chamber: live debate view with agent columns, streamed arguments, claim map,
  final verdict, and winner display.
- JudgeAI screen: response comparison workspace with scoring, radar chart, and
  JSON/Markdown export.

The frontend fetches `/api/providers` on load. It uses provider metadata from
the backend when available and falls back to local defaults if the provider list
cannot be loaded.

When a debate starts, the frontend sends a `POST /api/debate` request and reads
the response body as an SSE-style stream. Each parsed event updates local React
state. For example, `token` events append text to an agent's current argument,
`claim_detected` events add claim cells, and `judge_token` events build the final
verdict text.

JudgeAI can run in two modes:

- With an OpenAI API key, it calls `POST /api/judgeai`.
- Without a key or after a failure, it shows a deterministic local preview.

### `src/styles.css`

`styles.css` defines the visual system. The design uses a bright pixel-inspired
interface with hard borders, square panels, mono fonts, status chips, provider
colors, and responsive layouts.

Important style areas:

- Global theme variables and base element styling.
- Sticky top frame with app branding and status.
- Pixel panel styling shared across the app.
- Landing, provider, setup, chamber, claim map, and JudgeAI layouts.
- Responsive breakpoints for tablet and mobile screens.
- Memory wipe overlay animation for Cold Start Trials.
- Radar chart, score table, and export workspace styling for JudgeAI.

## API Reference

### `GET /api/health`

Returns:

```json
{ "status": "ok" }
```

### `GET /api/providers`

Returns public provider metadata used by the UI:

```json
{
  "providers": [
    {
      "id": "openai",
      "name": "OpenAI",
      "color": "#00d084",
      "key_placeholder": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  ]
}
```

### `POST /api/validate-provider`

Validates one provider/model combination by making a short test request to
the selected backend-configured provider.

Request:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini"
}
```

Response:

```json
{ "valid": true }
```

or:

```json
{ "valid": false, "error": "API key rejected by provider." }
```

### `POST /api/debate`

Starts a streamed debate. The response media type is `text/event-stream`.

Request includes:

- `topic`: debate question.
- `rounds`: consensus-seeking safety cap, 1 to 10.
- `agents`: 2 to 5 council agents.
- `provider_keys`: optional legacy field ignored for normal backend-managed key usage.
- `features`: `cold_start_trials` and `contradiction_map` flags.

### `POST /api/judgeai`

Scores two candidate responses against one prompt.

Request:

```json
{
  "prompt": "Explain retrieval augmented generation.",
  "candidate_a": "Candidate answer A...",
  "candidate_b": "Candidate answer B...",
  "model": "gpt-4o-mini"
}
```

Response includes service name, winner, confidence, per-criterion scores, totals,
validation status, rationale, export formats, and generation timestamp.

## Debate Event Flow

The debate stream uses JSON payloads wrapped as Server-Sent Events:

```text
data: {"type":"token","text":"Hello"}
```

Common event types:

- `session_start`: debate session metadata.
- `round_start`: new round begins.
- `agent_start`: an agent begins speaking.
- `token`: one streamed text chunk from the current agent.
- `agent_done`: an agent finished speaking.
- `claim_detected`: analyzer found a claim.
- `convergence_detected`: analyzer found agreement between agents.
- `contradiction_detected`: analyzer found conflict between agents.
- `consensus_detected`: all agents are represented in convergence without a
  current-round contradiction.
- `consensus_unresolved`: the round cap was reached before consensus.
- `memory_wipe`: Cold Start Trial reset between rounds.
- `judge_start`: final judge begins.
- `judge_token`: streamed final verdict text.
- `winner`: parsed winner from the judge response.
- `done`: debate complete.
- `error`: provider or judge failure.

## Provider Key Handling

Provider keys are entered in the browser UI. The backend does not write keys to
disk and there is no database. Keys are sent only in request bodies for the
operations that need them:

- Provider validation.
- Debate streaming.
- JudgeAI scoring.

Keys are stripped of surrounding whitespace and length capped by Pydantic
validation. The backend requires keys for every provider used by the selected
agents.

## Environment Variables

`.env.example` documents the supported runtime settings:

```text
PORT=8010
THINKTANK_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8010,http://localhost:8010
THINKTANK_MAX_REQUEST_BYTES=65536
THINKTANK_VALIDATE_RATE_LIMIT=20
THINKTANK_VALIDATE_RATE_WINDOW_SECONDS=60
THINKTANK_DEBATE_RATE_LIMIT=6
THINKTANK_DEBATE_RATE_WINDOW_SECONDS=60
```

`main.py` also defines JudgeAI rate limit settings with defaults:

```text
THINKTANK_JUDGEAI_RATE_LIMIT=10
THINKTANK_JUDGEAI_RATE_WINDOW_SECONDS=60
```

## Security Behavior

Codex added several practical guardrails:

- CORS is limited to configured local or deployment origins.
- Request bodies larger than `THINKTANK_MAX_REQUEST_BYTES` are rejected.
- Provider validation, debates, and JudgeAI are rate limited per client IP.
- Provider/model combinations must exist in the backend allow-list.
- Static file serving is constrained to the resolved `dist/` directory.
- Browser hardening headers are added to responses.
- API keys are not stored by the backend.

These controls are designed for a local or hackathon-style deployment. A larger
production deployment would likely add persistent rate limiting, authentication,
centralized logs, and secret-management policy.

## Tests

The test suite lives in `tests/`.

Run tests with:

```powershell
.\.venv\Scripts\python -m pytest
```

Current test coverage includes:

- Debate stream event contract.
- Cold Start Trial context behavior.
- Duplicate agent name rejection.
- Security headers.
- Unsupported model rejection before provider calls.
- Validate-provider rate limiting.
- Safe static file lookup inside `dist/`.
- Provider API key length validation.

## Known Limits And Future Improvements

The project is functional and understandable, but some areas could grow later:

- Add persistent users or saved debate sessions.
- Store completed transcripts and JudgeAI reports.
- Replace in-memory rate limiting with Redis or another shared store.
- Add stronger semantic contradiction detection with embeddings or model-based
  analysis.
- Add frontend component tests.
- Add end-to-end tests for the full browser debate flow.
- Add deployment documentation for a chosen host.
- Add provider-specific retry and timeout policies.
- Add more detailed audit logging without recording secret API keys.

## Maintenance Notes

When extending the project, keep the current separation of concerns:

- Put HTTP routes, middleware, and static serving in `main.py`.
- Put request validation rules in `thinktank/models.py`.
- Put provider-specific API details in `thinktank/providers.py`.
- Put debate sequencing in `thinktank/debate_engine.py`.
- Put claim analysis heuristics in `thinktank/analysis.py`.
- Put response comparison logic in `thinktank/judgeai.py`.
- Keep frontend screens and state in `src/main.jsx` unless the UI grows enough
  to justify splitting components into separate files.
- Keep visual changes in `src/styles.css`.
- Add or update tests when backend behavior, validation, security, or stream
  contracts change.

This structure makes the app easy to reason about: the frontend owns user
interaction, the backend owns validation and orchestration, provider adapters own
vendor differences, and tests lock down the risky behavior.
