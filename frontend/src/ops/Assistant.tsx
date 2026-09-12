/**
 * "Ask AI" - a thin ops-console front end for incident-assistant (:8005, phase 4).
 *
 * That service is deliberately not another support-service endpoint: it is grounded
 * only in the platform's own closed RCAs and runbooks (no live data, no database of its
 * own), and it says so honestly when a query falls outside that history rather than
 * answering from general knowledge. This screen is a thin client over it, nothing more.
 */

import { useState } from "react";

import { api } from "../api/client";
import type { AssistantAnswer } from "../api/types";
import { Card, ErrorBox } from "../components/ui";

const EXAMPLES = [
  "customers say their card was charged but the order shows failed",
  "checkout p95 is fine but it spikes every few seconds",
  "product pages are refusing orders even though stock looks fine",
];

export default function Assistant() {
  const [query, setQuery] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<unknown>();
  const [answer, setAnswer] = useState<AssistantAnswer>();

  async function ask() {
    const trimmed = query.trim();
    if (!trimmed || asking) return;
    setAsking(true);
    setError(undefined);
    try {
      setAnswer(await api.ask(trimmed));
    } catch (cause) {
      setError(cause);
      setAnswer(undefined);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Ask AI</h1>
          <p>
            Grounded only in this platform&apos;s own closed incidents and runbooks —
            not general knowledge. If nothing in the incident history covers a query, it
            says so instead of guessing.
          </p>
        </div>
      </div>

      <Card>
        <div className="stack tight">
          <textarea
            className="assistant-input"
            placeholder="Describe what you're seeing…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void ask();
            }}
            rows={4}
          />
          <div className="row between">
            <div className="row tight small muted">
              try:
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  className="ghost small"
                  type="button"
                  onClick={() => setQuery(example)}
                >
                  {example}
                </button>
              ))}
            </div>
            <button className="primary" disabled={asking || !query.trim()} onClick={() => void ask()}>
              {asking ? "Asking…" : "Ask"}
            </button>
          </div>
        </div>
      </Card>

      {error != null && <ErrorBox error={error} />}

      {answer && (
        <div className="stack">
          <Card title="Root cause">
            <p>{answer.root_cause}</p>
          </Card>
          <Card title="Suggested fix">
            <p>{answer.suggested_fix}</p>
          </Card>
          <Card title="Escalation">
            <p>{answer.escalation}</p>
          </Card>
        </div>
      )}
    </div>
  );
}
