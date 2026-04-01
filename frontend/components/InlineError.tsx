"use client";

interface InlineErrorProps {
  message: string;
  onDismiss?: () => void;
  onRetry?: () => void;
  className?: string;
}

export default function InlineError({
  message,
  onDismiss,
  onRetry,
  className = "",
}: InlineErrorProps) {
  return (
    <div
      className={`border-l-2 border-red-400 dark:border-red-500/60 bg-[var(--color-bg-secondary)] pl-3 pr-3 py-2 flex items-start justify-between gap-3 ${className}`}
      role="alert"
    >
      <span className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
        {message}
      </span>
      <div className="flex items-center gap-2 flex-shrink-0">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="text-[10px] font-mono tracking-wider text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] transition-colors"
          >
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors text-xs leading-none"
            aria-label="Dismiss"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
