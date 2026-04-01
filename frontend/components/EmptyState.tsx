"use client";

interface EmptyStateProps {
  message: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
  /** Use "bordered" for page-level empty states with border + bg + serif heading. */
  variant?: "inline" | "bordered";
}

export default function EmptyState({
  message,
  description,
  actionLabel,
  onAction,
  className = "",
  variant = "inline",
}: EmptyStateProps) {
  if (variant === "bordered") {
    return (
      <div
        className={`text-center py-12 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] ${className}`}
      >
        <h3 className="font-serif text-xl font-normal text-[var(--color-text-primary)] mb-2">
          {message}
        </h3>
        {description && (
          <p className="text-[var(--color-text-muted)] text-sm mb-6">
            {description}
          </p>
        )}
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            className="text-xs px-3 py-1.5 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
          >
            {actionLabel}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`text-center py-10 ${className}`}>
      <p className="text-sm text-[var(--color-text-muted)]">{message}</p>
      {description && (
        <p className="text-xs text-[var(--color-text-faint)] mt-1">
          {description}
        </p>
      )}
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="mt-4 text-xs font-mono tracking-wider px-3 py-1.5 border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
