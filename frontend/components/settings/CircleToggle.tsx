"use client";

export function CircleToggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  description?: string;
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="w-full flex items-start justify-between py-3 text-left group gap-4"
    >
      <div>
        <div className="font-mono text-sm text-[var(--color-text-primary)] group-hover:text-[var(--color-accent)] transition-colors">
          {label}
        </div>
        {description && (
          <div className="font-mono text-xs text-[var(--color-text-muted)] mt-1 leading-relaxed">
            {description}
          </div>
        )}
      </div>
      <span className="flex-shrink-0 mt-0.5 p-2 -m-2">
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          className="pointer-events-none"
        >
          <circle
            cx="5"
            cy="5"
            r="4"
            fill={checked ? "var(--color-accent)" : "none"}
            stroke={checked ? "var(--color-accent)" : "var(--color-border)"}
            strokeWidth="1.5"
          />
        </svg>
      </span>
    </button>
  );
}
