import { useId } from "react";

interface Props {
  size?: number;
  className?: string;
}

/**
 * Abstract identity mark for the companion: a quiet orbit around a
 * violet-to-sapphire lens. Purely decorative — the companion's name always
 * appears as real text beside it, so it is hidden from assistive technology.
 */
export function CompanionEmblem({ size = 32, className }: Props) {
  const gradientId = useId().replace(/:/g, "");
  const strokeId = `${gradientId}-stroke`;
  const fillId = `${gradientId}-fill`;

  return (
    <span
      className={className ? `companion-emblem ${className}` : "companion-emblem"}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 40 40" role="presentation" focusable="false">
        <defs>
          <linearGradient id={strokeId} x1="6" y1="4" x2="34" y2="36" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#A78BFA" />
            <stop offset="0.55" stopColor="#8B5CF6" />
            <stop offset="1" stopColor="#60A5FA" />
          </linearGradient>
          <linearGradient id={fillId} x1="12" y1="8" x2="28" y2="32" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#A78BFA" stopOpacity="0.95" />
            <stop offset="1" stopColor="#3B82F6" stopOpacity="0.8" />
          </linearGradient>
        </defs>
        <circle
          cx="20"
          cy="20"
          r="16.2"
          fill="none"
          stroke={`url(#${strokeId})`}
          strokeWidth="1.1"
          opacity="0.55"
        />
        <ellipse
          cx="20"
          cy="20"
          rx="16.2"
          ry="7"
          fill="none"
          stroke={`url(#${strokeId})`}
          strokeWidth="0.9"
          opacity="0.38"
          transform="rotate(-28 20 20)"
        />
        <path
          d="M20 11.2c4.7 3.5 4.7 14.1 0 17.6-4.7-3.5-4.7-14.1 0-17.6Z"
          fill={`url(#${fillId})`}
        />
        <circle cx="27.8" cy="13" r="1.5" fill="#BFDBFE" opacity="0.9" />
      </svg>
    </span>
  );
}
