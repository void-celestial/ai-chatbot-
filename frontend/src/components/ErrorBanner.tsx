import type { ApiError } from "../types";

interface Props {
  error: ApiError;
  canRetry: boolean;
  isRetrying: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}

export function ErrorBanner({ error, canRetry, isRetrying, onRetry, onDismiss }: Props) {
  return (
    <div className="error-banner" role="alert">
      <div className="error-text">
        <strong>Something went wrong:</strong> {error.message}
      </div>
      <div className="error-actions">
        {canRetry && (
          <button type="button" className="btn btn-secondary" onClick={onRetry} disabled={isRetrying}>
            {isRetrying ? "Retrying…" : "Try again"}
          </button>
        )}
        <button type="button" className="btn btn-ghost" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
