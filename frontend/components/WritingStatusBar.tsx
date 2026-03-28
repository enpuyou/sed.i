"use client";

type SaveStatus = "idle" | "saving" | "saved" | "error";

interface WritingStatusBarProps {
  wordCount: number;
  saveStatus: SaveStatus;
  onRetry?: () => void;
  className?: string;
  slashMode?: boolean;
}

export default function WritingStatusBar({
  wordCount,
  saveStatus,
  onRetry,
  className = "",
  slashMode = false,
}: WritingStatusBarProps) {
  const saveIndicator = () => {
    switch (saveStatus) {
      case "saving":
        return (
          <span className="writing-status-saving">
            <span className="status-dot saving-dot" />
            Saving…
          </span>
        );
      case "saved":
        return (
          <span className="writing-status-saved">
            <span className="status-dot saved-dot" />
            Saved
          </span>
        );
      case "error":
        return (
          <span className="writing-status-error">
            <span className="status-dot error-dot" />
            Save failed
            {onRetry && (
              <button
                onClick={onRetry}
                className="retry-btn compact-touch"
                aria-label="Retry save"
              >
                Retry
              </button>
            )}
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className={`writing-status-bar ${className}`}>
      <div className="writing-status-left">
        <span className="status-stat">
          {wordCount.toLocaleString()} {wordCount === 1 ? "word" : "words"}
        </span>
        {slashMode && <span className="slash-mode-hint">/ command</span>}
      </div>
      <div className="writing-status-right">{saveIndicator()}</div>
    </div>
  );
}
