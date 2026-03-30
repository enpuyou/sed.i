"use client";

// Placeholder animations shown inside DemoBlock before real screen recordings exist.
// Each is a self-contained CSS animation that hints at the feature.

export function SavePlaceholder() {
  return (
    <div className="w-4/5 flex flex-col gap-3">
      {/* Existing items — static */}
      {[80, 60].map((w, i) => (
        <div
          key={i}
          className="flex items-center gap-3 py-2 border-b border-[var(--color-border-subtle)]"
        >
          <div
            className="h-2 bg-[var(--color-border)] rounded-sm flex-1"
            style={{ width: `${w}%` }}
          />
          <div className="h-2 w-12 bg-[var(--color-border-subtle)] rounded-sm flex-shrink-0" />
        </div>
      ))}
      {/* New item sliding in */}
      <div
        className="flex items-center gap-3 py-2 border-b border-[var(--color-border-subtle)]"
        style={{
          animation: "saveSlideIn 2.5s ease-in-out infinite",
        }}
      >
        <div className="h-2 bg-[var(--color-accent)] rounded-sm opacity-60 flex-1" style={{ width: "72%" }} />
        <div className="h-2 w-12 bg-[var(--color-accent)] rounded-sm opacity-30 flex-shrink-0" />
      </div>
      <style>{`
        @keyframes saveSlideIn {
          0%   { opacity: 0; transform: translateY(-8px); }
          20%  { opacity: 1; transform: translateY(0); }
          80%  { opacity: 1; transform: translateY(0); }
          100% { opacity: 0; transform: translateY(-8px); }
        }
      `}</style>
    </div>
  );
}

export function ReadPlaceholder() {
  return (
    <div className="w-4/5 flex flex-col gap-2">
      {/* Article text lines */}
      {[90, 75, 85, 60, 80].map((w, i) => (
        <div
          key={i}
          className="h-2 bg-[var(--color-border)] rounded-sm"
          style={{ width: `${w}%` }}
        />
      ))}
      {/* Highlight pulse — line 3 */}
      <div
        className="h-2 rounded-sm mt-1"
        style={{
          width: "55%",
          background: "rgba(253, 224, 71, 0.5)",
          animation: "highlightPulse 2.8s ease-in-out infinite",
        }}
      />
      {[70, 45].map((w, i) => (
        <div
          key={i}
          className="h-2 bg-[var(--color-border)] rounded-sm"
          style={{ width: `${w}%` }}
        />
      ))}
      <style>{`
        @keyframes highlightPulse {
          0%, 100% { opacity: 0.4; }
          50%       { opacity: 0.9; }
        }
      `}</style>
    </div>
  );
}

export function ListenPlaceholder() {
  return (
    <div className="flex flex-col items-center gap-4">
      {/* Spinning vinyl */}
      <div
        className="rounded-full border border-[var(--color-border)] bg-[var(--color-bg-tertiary)] relative"
        style={{
          width: 96,
          height: 96,
          animation: "vinylSpin 4s linear infinite",
          boxShadow: "0 0 0 8px var(--color-border-subtle), 0 0 0 16px var(--color-bg-secondary)",
        }}
      >
        {/* Center hole */}
        <div
          className="absolute rounded-full bg-[var(--color-bg-primary)] border border-[var(--color-border)]"
          style={{
            width: 20,
            height: 20,
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
          }}
        />
      </div>
      {/* Track bar */}
      <div className="w-4/5 flex flex-col gap-1.5 mt-2">
        <div className="h-1.5 bg-[var(--color-border)] rounded-sm w-full" />
        <div
          className="h-1.5 bg-[var(--color-accent)] rounded-sm opacity-50"
          style={{ width: "40%", animation: "trackProgress 4s linear infinite" }}
        />
      </div>
      <style>{`
        @keyframes vinylSpin {
          to { transform: rotate(360deg); }
        }
        @keyframes trackProgress {
          0%   { width: 5%; }
          100% { width: 70%; }
        }
      `}</style>
    </div>
  );
}

export function ClaudePlaceholder() {
  return (
    <div className="w-4/5">
      <div
        className="font-mono text-[11px] leading-loose"
        style={{ color: "var(--color-text-muted)" }}
      >
        <div style={{ animation: "termLine 4s ease-in-out infinite" }}>
          &gt; create a list &ldquo;Design Systems&rdquo;
        </div>
        <div
          className="mt-1"
          style={{
            color: "var(--color-accent)",
            animation: "termResponse 4s ease-in-out infinite",
          }}
        >
          List created.
        </div>
        <div
          className="mt-2"
          style={{ animation: "termLine2 4s ease-in-out infinite", animationDelay: "0.3s" }}
        >
          &gt; summarize my highlights
        </div>
        <span
          className="inline-block w-1.5 h-3 align-middle ml-0.5"
          style={{
            background: "var(--color-accent)",
            animation: "cursorBlink 1s step-end infinite",
          }}
        />
      </div>
      <style>{`
        @keyframes termLine {
          0%   { opacity: 0; }
          10%  { opacity: 1; }
          90%  { opacity: 1; }
          100% { opacity: 0; }
        }
        @keyframes termResponse {
          0%,15% { opacity: 0; }
          25%    { opacity: 1; }
          90%    { opacity: 1; }
          100%   { opacity: 0; }
        }
        @keyframes termLine2 {
          0%,40% { opacity: 0; }
          55%    { opacity: 1; }
          90%    { opacity: 1; }
          100%   { opacity: 0; }
        }
        @keyframes cursorBlink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}

export function WritePlaceholder() {
  return (
    <div className="w-4/5 flex gap-4">
      {/* Editor pane */}
      <div className="flex-1 flex flex-col gap-2">
        <div className="h-2 bg-[var(--color-border)] rounded-sm w-4/5" />
        <div className="h-2 bg-[var(--color-border)] rounded-sm w-full" />
        <div className="h-2 bg-[var(--color-border)] rounded-sm w-3/5" />
        {/* Typing cursor */}
        <div className="flex items-center gap-1">
          <div className="h-2 bg-[var(--color-border)] rounded-sm" style={{ width: "30%" }} />
          <span
            className="inline-block w-1.5 h-3 align-middle"
            style={{
              background: "var(--color-accent)",
              animation: "cursorBlink 1s step-end infinite",
            }}
          />
        </div>
      </div>
      {/* Source pane — subtle */}
      <div
        className="w-28 flex-shrink-0 border-l border-[var(--color-border-subtle)] pl-3 flex flex-col gap-2"
      >
        <div className="h-1.5 bg-[var(--color-border-subtle)] rounded-sm w-full" />
        <div
          className="h-6 rounded-sm"
          style={{
            background: "rgba(253, 224, 71, 0.3)",
            animation: "highlightPulse 2.8s ease-in-out infinite",
          }}
        />
        <div className="h-1.5 bg-[var(--color-border-subtle)] rounded-sm w-4/5" />
        <div className="h-1.5 bg-[var(--color-border-subtle)] rounded-sm w-full" />
      </div>
      <style>{`
        @keyframes cursorBlink {
          50% { opacity: 0; }
        }
        @keyframes highlightPulse {
          0%, 100% { opacity: 0.4; }
          50%       { opacity: 0.9; }
        }
      `}</style>
    </div>
  );
}
