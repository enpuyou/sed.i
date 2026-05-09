interface StatusIndicatorProps {
  readingStatus: string;
  className?: string;
}

export default function StatusIndicator({
  readingStatus,
  className = "",
}: StatusIndicatorProps) {
  switch (readingStatus) {
    case "archived":
      // Archived: hollow circle with reduced opacity
      return (
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full border border-[var(--color-text-faint)] opacity-50 ${className}`}
          title="Archived"
        />
      );

    case "read":
      // Read: hollow circle
      return (
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full border border-[var(--color-status-read)] ${className}`}
          title="Read"
        />
      );

    case "in_progress":
      // In Progress: half-filled circle using gradient
      return (
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full ${className}`}
          style={{
            background: `linear-gradient(90deg, var(--color-status-unread) 50%, transparent 50%)`,
            border: "1px solid var(--color-status-unread)",
          }}
          title="In Progress"
        />
      );

    default:
      // Unread: filled circle
      return (
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full bg-[var(--color-status-unread)] ${className}`}
          title="Unread"
        />
      );
  }
}
