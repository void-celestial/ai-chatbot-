import type { HealthStatus } from "../types";

interface Props {
  health: HealthStatus | null;
}

export function StatusBadge({ health }: Props) {
  if (health === null) {
    return (
      <div className="status-line status-checking" role="status">
        <span className="status-dot" aria-hidden="true" />
        <span className="status-text">Checking Ollama…</span>
      </div>
    );
  }

  if (health.ollama.reachable && health.model.available) {
    return (
      <div className="status-line status-ok" role="status">
        <span className="status-dot" aria-hidden="true" />
        <span
          className="status-text"
          title={`Model ${health.model.tag} is available locally`}
        >
          {health.model.tag}
        </span>
      </div>
    );
  }

  if (health.ollama.reachable && !health.model.available) {
    return (
      <div className="status-line status-warn" role="status">
        <span className="status-dot" aria-hidden="true" />
        <span className="status-text" title={health.ollama.error ?? ""}>
          Model missing — run {`ollama pull ${health.model.tag}`}
        </span>
      </div>
    );
  }

  return (
    <div className="status-line status-error" role="status">
      <span className="status-dot" aria-hidden="true" />
      <span className="status-text" title={health.ollama.error ?? ""}>
        Ollama offline — run {`ollama serve`}
      </span>
    </div>
  );
}
