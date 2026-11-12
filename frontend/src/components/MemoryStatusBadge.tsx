import { statusLabel } from "../memoryUtils";

interface Props {
  status: string;
}

export function MemoryStatusBadge({ status }: Props) {
  return (
    <span className={`memory-badge memory-status-${status}`}>{statusLabel(status)}</span>
  );
}
