import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const DEFAULT_PROVIDERS = [
  { id: "gemini", name: "Gemini", color: "#3b82f6", key_placeholder: "server-managed", models: ["gemini-2.5-flash", "gemini-2-flash"] },
  { id: "openai", name: "OpenAI", color: "#10b981", key_placeholder: "server-managed", models: ["gpt-4o", "gpt-4o-mini"] }
];

const ROLE_OPTIONS = ["Optimist", "Skeptic", "Devil's Advocate", "Pragmatist", "Ethicist", "Futurist"];

const DEFAULT_AGENTS = [
  {
    id: "agent-nova",
    name: "Nova",
    role: "Optimist",
    personality: "Bright systems thinker who believes useful technology can compound human agency.",
    provider: "gemini",
    model: "gemini-2.5-flash"
  },
  {
    id: "agent-rex",
    name: "Rex",
    role: "Skeptic",
    personality: "Hard-nosed critic who hunts for hidden costs, incentives, and failure modes.",
    provider: "gemini",
    model: "gemini-2.5-flash"
  },
  {
    id: "agent-mira",
    name: "Mira",
    role: "Ethicist",
    personality: "Human-centered judge of social impact, fairness, and long-term consequences.",
    provider: "gemini",
    model: "gemini-2.5-flash"
  }
];

function App() {
  const [screen, setScreen] = useState("landing");
  const [providers, setProviders] = useState(DEFAULT_PROVIDERS);
  const [providerState, setProviderState] = useState(() => providerDefaults(DEFAULT_PROVIDERS));
  const [topic, setTopic] = useState("Is AI good for students?");
  const [rounds, setRounds] = useState(6);
  const [agents, setAgents] = useState(DEFAULT_AGENTS);
  const [features, setFeatures] = useState({ cold_start_trials: true, contradiction_map: true });
  const [events, setEvents] = useState([]);
  const [claims, setClaims] = useState([]);
  const [links, setLinks] = useState([]);
  const [activeAgentId, setActiveAgentId] = useState(null);
  const [currentRound, setCurrentRound] = useState(0);
  const [judgeText, setJudgeText] = useState("");
  const [winner, setWinner] = useState(null);
  const [status, setStatus] = useState("Ready");
  const [wipe, setWipe] = useState(null);
  const [toast, setToast] = useState("");
  const abortRef = useRef(null);

  useEffect(() => {
    fetch("/api/providers")
      .then((response) => response.json())
      .then((data) => {
        if (Array.isArray(data.providers)) {
          setProviders(data.providers);
          setProviderState((current) => providerDefaults(data.providers, current));
        }
      })
      .catch(() => setToast("Provider list unavailable. Using local defaults."));
  }, []);

  const connectedProviders = useMemo(
    () => providers,
    [providers]
  );

  const providerMap = useMemo(
    () => Object.fromEntries(providers.map((provider) => [provider.id, provider])),
    [providers]
  );

  const hydratedAgents = agents.map((agent) => ({
    ...agent,
    providerInfo: providerMap[agent.provider] || providers[0]
  }));

  function updateProvider(id, patch) {
    setProviderState((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  function bootCouncil() {
    if (connectedProviders.length === 0) {
      setToast("No backend provider keys are configured.");
      return;
    }

    setAgents((current) =>
      current.map((agent) => {
        const provider =
          connectedProviders.find((item) => item.id === agent.provider) ||
          connectedProviders.find((item) => item.id === "gemini") ||
          connectedProviders[0];
        return { ...agent, provider: provider.id, model: providerState[provider.id]?.model || provider.models[0] };
      })
    );
    setScreen("setup");
  }

  function updateAgent(id, patch) {
    setAgents((current) => current.map((agent) => (agent.id === id ? { ...agent, ...patch } : agent)));
  }

  function addAgent() {
    if (agents.length >= 5) {
      setToast("The council supports up to 5 agents.");
      return;
    }
    const provider = connectedProviders[agents.length % connectedProviders.length] || connectedProviders[0] || providers[0];
    setAgents((current) => [
      ...current,
      {
        id: `agent-${Date.now()}`,
        name: `Agent ${current.length + 1}`,
        role: ROLE_OPTIONS[current.length % ROLE_OPTIONS.length],
        personality: "A distinct council voice with a sharp point of view.",
        provider: provider.id,
        model: providerState[provider.id]?.model || provider.models[0]
      }
    ]);
  }

  function removeAgent(id) {
    if (agents.length <= 2) {
      setToast("Keep at least two agents.");
      return;
    }
    setAgents((current) => current.filter((agent) => agent.id !== id));
  }

  async function startDebate() {
    const cleanTopic = topic.trim();
    if (!cleanTopic) {
      setToast("Add a question first.");
      return;
    }

    for (const agent of agents) {
      if (!connectedProviders.some((provider) => provider.id === agent.provider)) {
        setToast(`${providerMap[agent.provider]?.name || agent.provider} is not configured on the backend.`);
        return;
      }
    }

    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setEvents([]);
    setClaims([]);
    setLinks([]);
    setActiveAgentId(null);
    setCurrentRound(0);
    setJudgeText("");
    setWinner(null);
    setStatus("Connecting");
    setScreen("chamber");

    try {
      const response = await fetch("/api/debate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: cleanTopic, rounds, agents, features }),
        signal: abortRef.current.signal
      });
      if (!response.ok || !response.body) throw new Error(`Backend returned ${response.status}`);
      await readSse(response.body, handleDebateEvent);
    } catch (error) {
      if (error.name !== "AbortError") {
        setStatus("Error");
        setToast(error.message || "Debate stream failed.");
      }
    }
  }

  function handleDebateEvent(event) {
    switch (event.type) {
      case "session_start":
        setStatus("Council live");
        break;
      case "round_start":
        setCurrentRound(event.round);
        setStatus(event.label);
        setEvents((current) => [...current, { type: "round", round: event.round, label: event.label }]);
        break;
      case "agent_start":
        setActiveAgentId(event.agent_id);
        setEvents((current) => ensureAgentRound(current, event.agent_id, event.round));
        break;
      case "token":
        setEvents((current) => appendToken(current, event.agent_id, event.round, event.text));
        break;
      case "agent_done":
        setActiveAgentId(null);
        break;
      case "memory_wipe":
        setWipe(event);
        setStatus("Cold Start reset");
        window.setTimeout(() => setWipe(null), 1500);
        break;
      case "claim_detected":
        setClaims((current) => [...current, event]);
        break;
      case "convergence_detected":
      case "contradiction_detected":
        setLinks((current) => [...current, event]);
        break;
      case "judge_start":
        setStatus("Writing verdict");
        break;
      case "judge_token":
        setJudgeText((current) => current + event.text);
        break;
      case "winner":
        setWinner(event.winner);
        break;
      case "consensus_detected":
        setStatus("Consensus reached");
        setToast(event.message || "Consensus detected.");
        break;
      case "consensus_unresolved":
        setStatus("Round cap reached");
        setToast(event.message || "Consensus was not detected before the round cap.");
        break;
      case "done":
        setStatus("Complete");
        setWinner(event.winner);
        break;
      case "error":
        setStatus("Error");
        setToast(event.message || "Debate failed.");
        break;
      default:
        break;
    }
  }

  return (
    <main className="app-shell">
      <Header status={status} screen={screen} onHome={() => setScreen("landing")} />

      {screen === "landing" && (
        <LandingScreen
          topic={topic}
          setTopic={setTopic}
          onContinue={() => setScreen("providers")}
          onJudgeAI={() => {
            setStatus("JudgeAI ready");
            setScreen("judgeai");
          }}
        />
      )}

      {screen === "judgeai" && <JudgeAIScreen goBack={() => setScreen("landing")} />}

      {screen === "providers" && (
        <ProviderConsole
          providers={providers}
          providerState={providerState}
          updateProvider={updateProvider}
          bootCouncil={bootCouncil}
          goBack={() => setScreen("landing")}
        />
      )}

      {screen === "setup" && (
        <CouncilSetup
          topic={topic}
          setTopic={setTopic}
          rounds={rounds}
          setRounds={setRounds}
          agents={hydratedAgents}
          connectedProviders={connectedProviders}
          providerState={providerState}
          updateAgent={updateAgent}
          addAgent={addAgent}
          removeAgent={removeAgent}
          features={features}
          setFeatures={setFeatures}
          startDebate={startDebate}
          goBack={() => setScreen("providers")}
        />
      )}

      {screen === "chamber" && (
        <Chamber
          topic={topic}
          rounds={rounds}
          agents={hydratedAgents}
          events={events}
          activeAgentId={activeAgentId}
          currentRound={currentRound}
          claims={claims}
          links={links}
          judgeText={judgeText}
          winner={winner}
          wipe={wipe}
          onNewMotion={() => setScreen("landing")}
        />
      )}

      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </main>
  );
}

function Header({ status, screen, onHome }) {
  return (
    <header className="top-frame">
      <div className="brand-block">
        <span className="brand-mark">TT</span>
        <div>
          <h1>ThinkTank</h1>
          <p>Friendly AI debate council</p>
        </div>
      </div>
      <div className="status-chip">{status}</div>
      {screen !== "landing" && (
        <button className="ghost-button" type="button" onClick={onHome}>
          New Question
        </button>
      )}
    </header>
  );
}

function LandingScreen({ topic, setTopic, onContinue, onJudgeAI }) {
  const examples = [
    "Is AI good for students?",
    "Should remote work be the default?",
    "Can AI make better medical decisions?",
    "Should cities ban private cars?"
  ];

  return (
    <section className="screen landing-screen">
      <div className="landing-card pixel-panel">
        <p className="kicker">Start here</p>
        <h2>What should the council debate?</h2>
        <p className="landing-copy">
          Type a question in normal language. ThinkTank will help you set up a few AI voices, watch
          them debate, and then show a simple verdict.
        </p>
        <textarea
          className="question-input"
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="Example: Is AI good for students?"
        />
        <div className="example-row">
          {examples.map((example) => (
            <button type="button" key={example} onClick={() => setTopic(example)}>
              {example}
            </button>
          ))}
        </div>
        <button className="primary-button landing-cta" type="button" onClick={onContinue}>
          Continue
        </button>
      </div>
      <div className="friendly-notes">
        <div className="pixel-panel">
          <strong>1</strong>
          <span>Ask a question</span>
        </div>
        <div className="pixel-panel">
          <strong>2</strong>
          <span>Choose a provider</span>
        </div>
        <div className="pixel-panel">
          <strong>3</strong>
          <span>Watch the council decide</span>
        </div>
      </div>
      <section className="judgeai-entry pixel-panel">
        <div className="judgeai-entry-copy">
          <p className="kicker">Another service</p>
          <h2>JudgeAI response comparator</h2>
          <p>
            Compare two candidate answers for the same prompt with a judge-style rubric, validated
            output, deterministic winner logic, radar scoring, and JSON or Markdown reports.
          </p>
          <button className="primary-button" type="button" onClick={onJudgeAI}>
            Open JudgeAI
          </button>
        </div>
        <div className="judgeai-entry-visual" aria-hidden="true">
          <div className="mini-terminal">
            <span>prompt.lock</span>
            <strong>A vs B</strong>
            <i>validated</i>
          </div>
          <div className="mini-radar">
            <span />
            <span />
            <span />
          </div>
        </div>
      </section>
    </section>
  );
}

function JudgeAIScreen({ goBack }) {
  const criteria = ["Accuracy", "Completeness", "Clarity", "Safety", "Reasoning"];
  const [prompt, setPrompt] = useState("Explain why retrieval augmented generation can reduce hallucinations.");
  const [candidateA, setCandidateA] = useState(
    "RAG can reduce hallucinations by grounding an answer in retrieved source material. It gives the model current context, narrows the search space, and lets the response cite evidence. It still needs good retrieval and validation because irrelevant context can mislead the model."
  );
  const [candidateB, setCandidateB] = useState(
    "Retrieval augmented generation fixes hallucinations because the model looks up the answer first. With documents attached, the system can always trust the generated response and does not need extra checking."
  );
  const [report, setReport] = useState(() => buildJudgeReport(prompt, candidateA, candidateB, criteria));
  const [judgeModel, setJudgeModel] = useState("gpt-4o-mini");
  const [judgeLoading, setJudgeLoading] = useState(false);
  const [judgeError, setJudgeError] = useState("");

  async function runJudge() {
    setJudgeError("");
    setJudgeLoading(true);
    try {
      const response = await fetch("/api/judgeai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          candidate_a: candidateA,
          candidate_b: candidateB,
          model: judgeModel
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "JudgeAI request failed.");
      setReport(data);
    } catch (error) {
      setReport(buildJudgeReport(prompt, candidateA, candidateB, criteria));
      setJudgeError(`${error.message || "JudgeAI request failed."} Local preview shown.`);
    } finally {
      setJudgeLoading(false);
    }
  }

  function exportReport(format) {
    if (!report) return;
    const content = format === "json" ? JSON.stringify(report, null, 2) : toMarkdownReport(report);
    const mime = format === "json" ? "application/json" : "text/markdown";
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `judgeai-report.${format === "json" ? "json" : "md"}`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="screen judgeai-screen">
      <div className="judgeai-hero pixel-panel">
        <div>
          <p className="kicker">JudgeAI</p>
          <h2>Compare two candidate responses</h2>
          <p>
            A standalone judge workspace with Pydantic-style output validation, deterministic tie
            breaking, radar scores, and portable reports.
          </p>
        </div>
        <button className="ghost-button" type="button" onClick={goBack}>
          Back to ThinkTank
        </button>
      </div>

      <div className="judgeai-workbench">
        <section className="judgeai-inputs pixel-panel">
          <label>
            <span>Prompt</span>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          </label>
          <div className="judgeai-connection">
            <label>
              <span>Judge model</span>
              <select value={judgeModel} onChange={(event) => setJudgeModel(event.target.value)}>
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-4o">gpt-4o</option>
              </select>
            </label>
          </div>
          <div className="candidate-grid">
            <label>
              <span>Candidate A</span>
              <textarea value={candidateA} onChange={(event) => setCandidateA(event.target.value)} />
            </label>
            <label>
              <span>Candidate B</span>
              <textarea value={candidateB} onChange={(event) => setCandidateB(event.target.value)} />
            </label>
          </div>
          <div className="button-row judgeai-actions">
            <button className="primary-button" type="button" onClick={runJudge} disabled={judgeLoading}>
              {judgeLoading ? "Judging" : "Run Judge"}
            </button>
            <button className="ghost-button" type="button" onClick={() => exportReport("json")}>
              Export JSON
            </button>
            <button className="ghost-button" type="button" onClick={() => exportReport("markdown")}>
              Export MD
            </button>
          </div>
          {judgeError && <div className="judgeai-alert">{judgeError}</div>}
        </section>

        <aside className="judgeai-results pixel-panel">
          <div className="judgeai-scoreboard">
            <div>
              <p className="kicker">Winner</p>
              <strong>{report.winner}</strong>
              <span>{report.confidence}% confidence</span>
            </div>
            <div className={report.validated ? "schema-badge ok" : "schema-badge"}>
              {report.validated ? "Schema valid" : "Needs review"}
            </div>
          </div>
          <RadarChart criteria={criteria} scoresA={report.scores.A} scoresB={report.scores.B} />
          <div className="score-table">
            {criteria.map((criterion) => (
              <div key={criterion}>
                <span>{criterion}</span>
                <strong>{report.scores.A[criterion]}</strong>
                <strong>{report.scores.B[criterion]}</strong>
              </div>
            ))}
          </div>
          <div className="judgeai-rationale">
            <p>{report.rationale}</p>
            <small>Deterministic total: A {report.totals.A} / B {report.totals.B}</small>
          </div>
        </aside>
      </div>
    </section>
  );
}

function RadarChart({ criteria, scoresA, scoresB }) {
  const size = 280;
  const center = size / 2;
  const radius = 104;
  const rings = [0.25, 0.5, 0.75, 1];
  const pointsFor = (scores) =>
    criteria
      .map((criterion, index) => {
        const angle = -Math.PI / 2 + (index * Math.PI * 2) / criteria.length;
        const distance = (scores[criterion] / 10) * radius;
        return `${center + Math.cos(angle) * distance},${center + Math.sin(angle) * distance}`;
      })
      .join(" ");

  return (
    <div className="radar-wrap">
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="JudgeAI radar chart">
        {rings.map((ring) => (
          <polygon className="radar-ring" points={ringPoints(criteria.length, center, radius * ring)} key={ring} />
        ))}
        {criteria.map((criterion, index) => {
          const angle = -Math.PI / 2 + (index * Math.PI * 2) / criteria.length;
          const x = center + Math.cos(angle) * (radius + 28);
          const y = center + Math.sin(angle) * (radius + 28);
          return (
            <g key={criterion}>
              <line className="radar-axis" x1={center} y1={center} x2={center + Math.cos(angle) * radius} y2={center + Math.sin(angle) * radius} />
              <text x={x} y={y} textAnchor="middle" dominantBaseline="middle">
                {criterion.slice(0, 4)}
              </text>
            </g>
          );
        })}
        <polygon className="radar-poly a" points={pointsFor(scoresA)} />
        <polygon className="radar-poly b" points={pointsFor(scoresB)} />
      </svg>
      <div className="radar-legend">
        <span><i className="legend-a" />A</span>
        <span><i className="legend-b" />B</span>
      </div>
    </div>
  );
}

function ProviderConsole({ providers, providerState, updateProvider, bootCouncil, goBack }) {
  return (
    <section className="screen provider-screen">
      <div className="hero-panel pixel-panel provider-hero">
        <p className="kicker">AI connection</p>
        <h2>Choose who joins the debate</h2>
        <p>
          Provider keys are configured on the backend. Choose a configured provider and model, then
          continue to the council.
        </p>
        <button className="ghost-button provider-back" type="button" onClick={goBack}>
          Back to question
        </button>
      </div>
      <div className="provider-grid">
        {providers.length === 0 && (
          <article className="provider-row pixel-panel">
            <div className="led" />
            <div>
              <h3>No providers configured</h3>
              <small>Add GEMINI_API_KEY or OPENAI_API_KEY to the backend environment.</small>
            </div>
          </article>
        )}
        {providers.map((provider) => {
          const state = providerState[provider.id] || {};
          return (
            <article className="provider-row pixel-panel" key={provider.id} style={{ "--accent": provider.color }}>
              <div className="led ok" />
              <div>
                <h3>{provider.name}</h3>
                <select
                  value={state.model || provider.models[0]}
                  onChange={(event) => updateProvider(provider.id, { model: event.target.value })}
                >
                  {provider.models.map((model) => (
                    <option value={model} key={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>
              <small>Backend key configured</small>
            </article>
          );
        })}
      </div>
      <button className="primary-button boot-button" type="button" onClick={bootCouncil}>
        Continue to Council
      </button>
    </section>
  );
}

function CouncilSetup({
  topic,
  setTopic,
  rounds,
  setRounds,
  agents,
  connectedProviders,
  providerState,
  updateAgent,
  addAgent,
  removeAgent,
  features,
  setFeatures,
  startDebate,
  goBack
}) {
  return (
    <section className="screen setup-screen">
      <div className="motion-panel pixel-panel">
        <p className="kicker">Your question</p>
        <textarea value={topic} onChange={(event) => setTopic(event.target.value)} />
        <p className="helper-text">Agents keep responding until consensus, up to this cap.</p>
        <div className="round-box">
          {[3, 6, 10].map((round) => (
            <button className={rounds === round ? "selected" : ""} type="button" key={round} onClick={() => setRounds(round)}>
              {round}
            </button>
          ))}
        </div>
        <label className="toggle-line">
          <input
            type="checkbox"
            checked={features.cold_start_trials}
            onChange={(event) => setFeatures((current) => ({ ...current, cold_start_trials: event.target.checked }))}
          />
          Cold Start Trials
        </label>
        <label className="toggle-line">
          <input
            type="checkbox"
            checked={features.contradiction_map}
            onChange={(event) => setFeatures((current) => ({ ...current, contradiction_map: event.target.checked }))}
          />
          Contradiction Map
        </label>
        <div className="button-row">
          <button className="ghost-button" type="button" onClick={goBack}>
            Back
          </button>
          <button className="primary-button" type="button" onClick={startDebate}>
            Start Debate
          </button>
        </div>
      </div>
      <div className="roster-panel pixel-panel">
        <div className="panel-head">
          <div>
            <p className="kicker">Council voices</p>
            <h2>{agents.length} agents</h2>
          </div>
          <button className="ghost-button" type="button" onClick={addAgent}>
            Add Agent
          </button>
        </div>
        <div className="agent-roster">
          {agents.map((agent) => (
            <AgentEditor
              agent={agent}
              key={agent.id}
              connectedProviders={connectedProviders}
              providerState={providerState}
              updateAgent={updateAgent}
              removeAgent={removeAgent}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

function AgentEditor({ agent, connectedProviders, providerState, updateAgent, removeAgent }) {
  const provider = connectedProviders.find((item) => item.id === agent.provider) || connectedProviders[0] || agent.providerInfo;
  return (
    <article className="agent-editor" style={{ "--accent": provider?.color || "#94a3b8" }}>
      <div className="agent-avatar">{agent.name.slice(0, 2).toUpperCase()}</div>
      <input aria-label="Agent name" value={agent.name} onChange={(event) => updateAgent(agent.id, { name: event.target.value })} />
      <select value={agent.role} onChange={(event) => updateAgent(agent.id, { role: event.target.value })}>
        {ROLE_OPTIONS.map((role) => (
          <option value={role} key={role}>
            {role}
          </option>
        ))}
      </select>
      <select
        value={agent.provider}
        onChange={(event) => {
          const nextProvider = connectedProviders.find((item) => item.id === event.target.value);
          updateAgent(agent.id, {
            provider: event.target.value,
            model: providerState[event.target.value]?.model || nextProvider?.models[0] || agent.model
          });
        }}
      >
        {connectedProviders.map((item) => (
          <option value={item.id} key={item.id}>
            {item.name}
          </option>
        ))}
      </select>
      <input
        aria-label="Agent personality"
        value={agent.personality}
        onChange={(event) => updateAgent(agent.id, { personality: event.target.value })}
      />
      <button type="button" onClick={() => removeAgent(agent.id)}>
        X
      </button>
    </article>
  );
}

function Chamber({ topic, rounds, agents, events, activeAgentId, currentRound, claims, links, judgeText, winner, wipe, onNewMotion }) {
  return (
    <section className="screen chamber-screen">
      <div className="chamber-top pixel-panel">
        <span>{topic}</span>
        <strong>
          Round {Math.max(currentRound, 1)} / up to {rounds}
        </strong>
      </div>
      <DebateChat agents={agents} events={events} activeAgentId={activeAgentId} winner={winner} />
      <ContradictionMap agents={agents} claims={claims} links={links} />
      <section className="judge-panel pixel-panel">
        <p className="kicker">Final verdict</p>
        <div className="judge-text">{judgeText || "The verdict will appear here after the council speaks."}</div>
        {winner && <div className="winner-banner">Winner: {winner}</div>}
        <button className="primary-button" type="button" onClick={onNewMotion}>
          Ask Another Question
        </button>
      </section>
      {wipe && <MemoryWipe wipe={wipe} />}
    </section>
  );
}

function DebateChat({ agents, events, activeAgentId, winner }) {
  const activeAgent = agents.find((agent) => agent.id === activeAgentId);
  return (
    <section className="debate-chat pixel-panel">
      <header className="chat-header">
        <div className="chat-group-icon" aria-hidden="true">
          {agents.slice(0, 3).map((agent) => (
            <span key={agent.id} style={{ "--accent": agent.providerInfo?.color || "#94a3b8" }}>
              {getInitials(agent.name)}
            </span>
          ))}
        </div>
        <div className="chat-title">
          <h2>ThinkTank Debate</h2>
          <p>{activeAgent ? `${activeAgent.name} is typing...` : `${agents.length} council members`}</p>
        </div>
        <div className="chat-participants" aria-label="Debate participants">
          {agents.map((agent) => (
            <span
              className={`${activeAgentId === agent.id ? "active" : ""} ${winner === agent.name ? "winner" : ""}`}
              key={agent.id}
              style={{ "--accent": agent.providerInfo?.color || "#94a3b8" }}
              title={`${agent.name} - ${agent.role}`}
            >
              {getInitials(agent.name)}
            </span>
          ))}
        </div>
      </header>
      <div className="chat-feed">
        {events.length === 0 && <div className="chat-empty">Waiting for the first message...</div>}
        {events.map((event, index) => {
          if (event.type === "round") {
            return (
              <div className="chat-round-divider" key={`round-${event.round}-${index}`}>
                {event.label || `Round ${event.round}`}
              </div>
            );
          }

          if (event.type !== "agent-round") {
            return null;
          }

          const agent = agents.find((item) => item.id === event.agent_id);
          return <ChatMessage agent={agent} event={event} active={activeAgentId === event.agent_id} winner={winner === agent?.name} key={`${event.agent_id}-${event.round}-${index}`} />;
        })}
      </div>
    </section>
  );
}

function ChatMessage({ agent, event, active, winner }) {
  const provider = agent?.providerInfo || {};
  const color = provider.color || "#10b981";
  const text = event.text || (active ? "Typing..." : "Waiting...");
  return (
    <article className={`chat-message ${active ? "active" : ""} ${winner ? "winner" : ""}`} style={{ "--accent": color }}>
      <div className="chat-avatar" aria-hidden="true">
        {getInitials(agent?.name || event.agent_name || "?")}
      </div>
      <div className="chat-bubble">
        <div className="chat-meta">
          <strong>{agent?.name || event.agent_name}</strong>
          <span>
            {agent?.role || "Council member"} / {provider.name || agent?.provider || ""}
          </span>
        </div>
        <p>{text}</p>
        <footer>
          <span>Round {event.round}</span>
          <span>{active ? "typing" : "delivered"}</span>
        </footer>
      </div>
    </article>
  );
}

function getInitials(name) {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function ContradictionMap({ agents, claims, links }) {
  return (
    <section className="map-panel pixel-panel">
      <div className="panel-head">
        <div>
          <p className="kicker">Live claim map</p>
          <h2>Where they agree or clash</h2>
        </div>
        <span>{claims.length} claims / {links.length} links</span>
      </div>
      <div className="claim-grid">
        {claims.slice(-18).map((claim) => {
          const agent = agents.find((item) => item.id === claim.agent_id);
          return (
            <div className={`claim-cell ${claim.polarity}`} key={claim.claim_id} style={{ "--accent": agent?.providerInfo?.color || "#94a3b8" }}>
              <strong>{claim.agent_name}</strong>
              <p>{claim.text}</p>
            </div>
          );
        })}
      </div>
      <div className="link-feed">
        {links.slice(-6).map((link, index) => (
          <div className={link.type === "contradiction_detected" ? "conflict" : "converge"} key={`${link.source_claim_id}-${index}`}>
            {link.summary}
          </div>
        ))}
      </div>
    </section>
  );
}

function MemoryWipe({ wipe }) {
  return (
    <div className="memory-wipe">
      <div>
        <span>Cold Start Trial</span>
        <strong>Memory reset</strong>
        <p>Round {wipe.next_round} starts fresh with only the other agents' earlier arguments.</p>
      </div>
    </div>
  );
}

function Toast({ message, onClose }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4200);
    return () => window.clearTimeout(timer);
  }, [onClose]);
  return <div className="toast">{message}</div>;
}

function providerDefaults(providers, current = {}) {
  return Object.fromEntries(
    providers.map((provider) => {
      const currentModel = current[provider.id]?.model;
      return [
        provider.id,
        { model: provider.models.includes(currentModel) ? currentModel : provider.models[0] }
      ];
    })
  );
}

async function readSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch (error) {
        console.error("Bad SSE event", error, part);
      }
    }
  }
}

function ensureAgentRound(events, agentId, round) {
  if (events.some((event) => event.type === "agent-round" && event.agent_id === agentId && event.round === round)) {
    return events;
  }
  return [...events, { type: "agent-round", agent_id: agentId, round, text: "" }];
}

function appendToken(events, agentId, round, text) {
  return ensureAgentRound(events, agentId, round).map((event) => {
    if (event.type === "agent-round" && event.agent_id === agentId && event.round === round) {
      return { ...event, text: event.text + text };
    }
    return event;
  });
}

function buildJudgeReport(prompt, candidateA, candidateB, criteria) {
  const scores = {
    A: scoreCandidate(prompt, candidateA, criteria),
    B: scoreCandidate(prompt, candidateB, criteria)
  };
  const totals = {
    A: Object.values(scores.A).reduce((sum, score) => sum + score, 0),
    B: Object.values(scores.B).reduce((sum, score) => sum + score, 0)
  };
  const winner = decideWinner(scores, totals, candidateA, candidateB);
  const margin = Math.abs(totals.A - totals.B);
  const validated = validateJudgeShape({ prompt, scores, totals, winner });

  return {
    service: "JudgeAI",
    prompt,
    winner,
    confidence: Math.min(96, 58 + margin * 4),
    scores,
    totals,
    validated,
    rationale: buildRationale(winner, scores, totals),
    exports: ["json", "markdown"],
    generated_at: new Date().toISOString()
  };
}

function scoreCandidate(prompt, response, criteria) {
  const promptTerms = keywordSet(prompt);
  const responseTerms = keywordSet(response);
  const overlap = [...promptTerms].filter((term) => responseTerms.has(term)).length;
  const wordCount = response.trim().split(/\s+/).filter(Boolean).length;
  const hasCaveat = /\b(however|but|still|unless|risk|limit|depends|validate|check)\b/i.test(response);
  const hasEvidence = /\b(source|evidence|because|cite|ground|document|context|retriev)\w*/i.test(response);
  const overclaims = /\b(always|never|guarantee|fixes|perfect|no need|cannot)\b/i.test(response);
  const complete = clampScore(4 + Math.min(4, wordCount / 28) + Math.min(2, overlap));

  return {
    [criteria[0]]: clampScore(5 + Math.min(3, overlap) + (hasEvidence ? 1 : 0) - (overclaims ? 2 : 0)),
    [criteria[1]]: complete,
    [criteria[2]]: clampScore(5 + (wordCount > 24 ? 2 : 0) + (response.includes(".") ? 1 : 0) - (wordCount > 120 ? 1 : 0)),
    [criteria[3]]: clampScore(6 + (hasCaveat ? 2 : 0) - (overclaims ? 2 : 0)),
    [criteria[4]]: clampScore(5 + (hasEvidence ? 2 : 0) + (hasCaveat ? 1 : 0) + Math.min(1, overlap))
  };
}

function decideWinner(scores, totals, candidateA, candidateB) {
  if (totals.A !== totals.B) return totals.A > totals.B ? "Candidate A" : "Candidate B";
  const safetyDelta = scores.A.Safety - scores.B.Safety;
  if (safetyDelta !== 0) return safetyDelta > 0 ? "Candidate A" : "Candidate B";
  if (candidateA.length !== candidateB.length) return candidateA.length > candidateB.length ? "Candidate A" : "Candidate B";
  return "Tie";
}

function buildRationale(winner, scores, totals) {
  if (winner === "Tie") {
    return "Both candidates landed on the same deterministic total after schema validation and tie checks.";
  }
  const side = winner.endsWith("A") ? "A" : "B";
  const other = side === "A" ? "B" : "A";
  const strongest = Object.entries(scores[side]).sort((left, right) => right[1] - left[1])[0][0];
  return `${winner} wins on the deterministic rubric, led by ${strongest.toLowerCase()} and a ${Math.abs(totals[side] - totals[other])}-point margin.`;
}

function keywordSet(text) {
  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((word) => word.length > 3)
  );
}

function clampScore(value) {
  return Math.max(1, Math.min(10, Math.round(value)));
}

function validateJudgeShape(report) {
  return Boolean(
    report.prompt &&
      report.scores?.A &&
      report.scores?.B &&
      Number.isFinite(report.totals?.A) &&
      Number.isFinite(report.totals?.B) &&
      ["Candidate A", "Candidate B", "Tie"].includes(report.winner)
  );
}

function ringPoints(count, center, radius) {
  return Array.from({ length: count })
    .map((_, index) => {
      const angle = -Math.PI / 2 + (index * Math.PI * 2) / count;
      return `${center + Math.cos(angle) * radius},${center + Math.sin(angle) * radius}`;
    })
    .join(" ");
}

function toMarkdownReport(report) {
  const rows = Object.keys(report.scores.A)
    .map((criterion) => `| ${criterion} | ${report.scores.A[criterion]} | ${report.scores.B[criterion]} |`)
    .join("\n");

  return `# JudgeAI Report

Prompt: ${report.prompt}

Winner: ${report.winner}

Confidence: ${report.confidence}%

Schema: ${report.validated ? "valid" : "needs review"}

| Criterion | Candidate A | Candidate B |
| --- | ---: | ---: |
${rows}

${report.rationale}
`;
}

createRoot(document.getElementById("root")).render(<App />);
