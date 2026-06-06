# ThinkTank — Cold Start Debate Protocol

> Multi-model AI council where agents debate any topic with **memory reset between rounds** — eliminating confirmation bias and echo chambers.

## What it does

ThinkTank pits multiple real LLMs against each other in structured debates. The key innovation is the **Cold Start Protocol** (inspired by Andrej Karpathy's LLM Council concept):

- **Round 1**: Each agent argues from scratch with no context
- **Round 2+**: Each agent sees ONLY what *other* agents said in the previous round — never their own arguments. Memory is fully wiped.
- **Judge**: Reads all rounds from all agents, identifies independent convergence (strong signal of truth), and declares a winner

This produces genuine independent reasoning each round — same model, potentially different conclusion — not just position defense.

---

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# Open http://localhost:8010
```

---

## Provider Setup

You need API keys for at least **2 providers** to run a debate.

| Provider | Model | Get Key |
|----------|-------|---------|
| **OpenAI** | gpt-4o, gpt-4o-mini | https://platform.openai.com |
| **Anthropic Claude** | claude-opus-4-5, claude-haiku-4-5 | https://console.anthropic.com |
| **Google Gemini** | gemini-2.0-flash, gemini-1.5-flash | https://makersuite.google.com |
| **xAI Grok** | grok-3, grok-3-mini | https://console.x.ai |

Keys are entered in the app UI and never stored to disk.

---

## How to Use

1. **Screen 1 — Provider Setup**: Enter API keys for each provider you want to use. Hit "Test" to validate each key.
2. **Screen 2 — Council Setup**: 
   - Enter the debate topic (anything works)
   - Configure 2–5 agents: give each a name, role, and provider
   - Roles: Optimist, Skeptic, Devil's Advocate, Neutral, or custom
   - Choose 1–3 rounds
3. **Screen 3 — Battle**: Watch agents debate live with streaming text, then get a synthesized judge verdict

---

## Architecture

```
thinkTank/
├── main.py        # FastAPI backend — provider routing, SSE streaming, debate logic
├── index.html     # Single-file frontend — RPG battle UI, vanilla JS/CSS
├── requirements.txt
└── README.md
```

**Backend**: FastAPI + Python async, SSE streaming via `StreamingResponse`  
**Frontend**: Single HTML file, no frameworks, vanilla JS + CSS  
**Streaming**: Server-Sent Events, word-by-word text streaming  

---

## Environment Variables

```
PORT=8010   # Server port (default: 8010)
```

---

## SSE Event Reference

| Event | Payload |
|-------|---------|
| `round_start` | `{ round, total, label }` |
| `agent_start` | `{ agent, provider, model, round }` |
| `chunk` | `{ agent, text, role? }` |
| `agent_done` | `{ agent }` |
| `memory_reset` | `{ round }` — signals cold start wipe |
| `judge_start` | `{}` |
| `done` | `{ winner }` |
| `error` | `{ message }` |