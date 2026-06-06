# ThinkTank

ThinkTank is a game-like AI debate app where custom LLM agents argue any topic in real time. You create debaters with names and personalities, start a topic, watch the debate stream into a WhatsApp-style group chat, and then get a final synthesized verdict from an impartial Judge Agent.

## Tech Stack

- Backend: Python, FastAPI, async/await
- Frontend: Single `index.html` with vanilla JavaScript and CSS
- LLM: OpenAI `gpt-4o` through the async OpenAI Python SDK
- Streaming: FastAPI `StreamingResponse` with Server-Sent Events
- Storage: No database

## How To Run

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Set your OpenAI API key:

   ```bash
   # macOS/Linux
   export OPENAI_API_KEY="your-api-key"

   # Windows PowerShell
   $env:OPENAI_API_KEY="your-api-key"
   ```

   You can also create a `.env` file in this folder:

   ```bash
   OPENAI_API_KEY=sk-your-real-key
   ```

3. Start the backend:

   ```bash
   uvicorn main:app --reload --port 8010
   ```

4. Open `index.html` in your browser.

The frontend tries `http://127.0.0.1:8010/debate` first, then falls back to ports `8000` and `8001`. Keep the FastAPI server running while using the app.

## How Codex Was Used

Codex designed and generated the full app architecture: an async FastAPI backend, prompt-driven debater and judge agents, SSE streaming events, and a polished vanilla HTML/CSS/JS interface. It implemented the debate flow so opening arguments stream first, rebuttals receive previous context, and the Judge Agent reads the full transcript before delivering a verdict. Codex also shaped the prompt engineering, frontend state handling, typing indicators, animated chat bubbles, stream parsing, and error handling needed for a demo-ready experience.

## App Flow

- Create 2 to 5 debater agents with custom names and personalities.
- Enter any debate topic.
- Choose 1, 2, or 3 rounds.
- Watch each agent respond in sequence as streamed chat bubbles.
- Read the Judge Agent's final verdict after the debate completes.
