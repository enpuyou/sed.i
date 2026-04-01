"use client";

import { useState } from "react";

/* ─────────────────────────────────────────────
   MOCKUP: InlineError component
   ───────────────────────────────────────────── */

function InlineError({
  message,
  onDismiss,
  onRetry,
  className = "",
}: {
  message: string;
  onDismiss?: () => void;
  onRetry?: () => void;
  className?: string;
}) {
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
            onClick={onRetry}
            className="text-[10px] font-mono tracking-wider text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] transition-colors"
          >
            Retry
          </button>
        )}
        {onDismiss && (
          <button
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

/* ─────────────────────────────────────────────
   MOCKUP: EmptyState component
   ───────────────────────────────────────────── */

function EmptyState({
  message,
  description,
  actionLabel,
  onAction,
  className = "",
}: {
  message: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}) {
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
          onClick={onAction}
          className="mt-4 text-xs font-mono tracking-wider px-3 py-1.5 border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────
   MOCKUP: Bordered EmptyState variant (for page-level empty)
   ───────────────────────────────────────────── */

function EmptyStateBordered({
  message,
  description,
  actionLabel,
  onAction,
  className = "",
}: {
  message: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}) {
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
          onClick={onAction}
          className="text-xs px-3 py-1.5 border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors"
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────
   DEMO PAGE
   ───────────────────────────────────────────── */

export default function MockupsPage() {
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});

  const dismiss = (id: string) =>
    setDismissed((prev) => ({ ...prev, [id]: true }));
  const reset = () => setDismissed({});

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)] text-[var(--color-text-primary)]">
      <div className="max-w-2xl mx-auto px-6 py-12 space-y-16">
        {/* Header */}
        <div>
          <h1 className="font-serif text-2xl mb-1">Component Mockups</h1>
          <p className="text-xs text-[var(--color-text-muted)] font-mono tracking-wider">
            InlineError + EmptyState -- review before implementation
          </p>
          <button
            onClick={reset}
            className="mt-3 text-[10px] font-mono tracking-wider text-[var(--color-accent)] hover:text-[var(--color-accent-hover)]"
          >
            Reset dismissed
          </button>
        </div>

        {/* ── SECTION: InlineError ── */}
        <section className="space-y-8">
          <div>
            <h2 className="font-serif text-lg mb-1">InlineError</h2>
            <p className="text-xs text-[var(--color-text-faint)] mb-6">
              Contextual error feedback near the action that failed. Replaces
              all ad-hoc red divs across the app.
            </p>
          </div>

          {/* Variant 1: Basic (no dismiss, no retry) */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Basic -- form validation / API error
            </p>
            <InlineError message="That URL doesn't look right. Check the format and try again." />
          </div>

          {/* Variant 2: With dismiss */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Dismissible -- content list fetch error
            </p>
            {!dismissed["d1"] ? (
              <InlineError
                message="Couldn't load your articles. Please try again."
                onDismiss={() => dismiss("d1")}
              />
            ) : (
              <p className="text-[10px] text-[var(--color-text-faint)] italic">
                (dismissed -- click reset above)
              </p>
            )}
          </div>

          {/* Variant 3: With retry */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              With retry -- connections panel failure
            </p>
            <InlineError
              message="Couldn't load connections for this article."
              onRetry={() => alert("Retry clicked")}
            />
          </div>

          {/* Variant 4: With dismiss + retry */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Dismiss + retry -- list page fetch error
            </p>
            {!dismissed["d2"] ? (
              <InlineError
                message="Failed to load lists."
                onDismiss={() => dismiss("d2")}
                onRetry={() => alert("Retry clicked")}
              />
            ) : (
              <p className="text-[10px] text-[var(--color-text-faint)] italic">
                (dismissed)
              </p>
            )}
          </div>

          {/* Variant 5: In context -- simulated form */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              In context -- add content form
            </p>
            <div className="border-b border-[var(--color-border)] pb-4 space-y-3">
              <InlineError message="Failed to add content. The server returned an error." />
              <div className="flex items-center gap-2 opacity-80">
                <span className="text-[var(--color-text-muted)] font-mono text-lg select-none">
                  &gt;
                </span>
                <input
                  type="text"
                  placeholder="Paste a URL..."
                  className="flex-1 bg-transparent text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-faint)] outline-none"
                  disabled
                />
              </div>
            </div>
          </div>

          {/* Variant 6: In context -- simulated modal */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              In context -- list modal
            </p>
            <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-5 max-w-sm space-y-4">
              <div>
                <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
                  Create New List
                </h3>
                <p className="text-[10px] text-[var(--color-text-faint)]">
                  Organize your content into a collection
                </p>
              </div>
              <InlineError message="A list with this name already exists." />
              <div className="space-y-2">
                <label className="block text-xs text-[var(--color-text-primary)]">
                  Name
                </label>
                <input
                  type="text"
                  value="Reading backlog"
                  readOnly
                  className="w-full bg-transparent border border-[var(--color-border)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
                />
              </div>
            </div>
          </div>

          {/* Variant 7: Compact -- for inline actions (highlight save, tag update) */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Compact -- highlight note save failure
            </p>
            <div className="max-w-xs">
              <InlineError
                message="Couldn't save note."
                onRetry={() => alert("Retry")}
                className="py-1.5"
              />
            </div>
          </div>

          {/* Variant 8: In sidebar context */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              In context -- sidebar list fetch failure
            </p>
            <div className="w-56 border-r border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-4 px-4 space-y-3">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Lists
              </p>
              <InlineError
                message="Couldn't load lists."
                onRetry={() => alert("Retry")}
                className="py-1.5"
              />
            </div>
          </div>
        </section>

        {/* Divider */}
        <hr className="border-[var(--color-border)]" />

        {/* ── SECTION: EmptyState ── */}
        <section className="space-y-8">
          <div>
            <h2 className="font-serif text-lg mb-1">EmptyState</h2>
            <p className="text-xs text-[var(--color-text-faint)] mb-6">
              Consistent empty messaging. Two variants: inline (for panels,
              sub-sections) and bordered (for page-level empty).
            </p>
          </div>

          {/* Inline variant: no action */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline -- highlights panel
            </p>
            <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
              <EmptyState
                message="No highlights yet."
                description="Select text in the article to create a highlight."
              />
            </div>
          </div>

          {/* Inline variant: with action */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline with action -- sidebar
            </p>
            <div className="w-56 border-r border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-2">
              <EmptyState
                message="No lists yet."
                actionLabel="Create one"
                onAction={() => alert("Create")}
                className="py-4"
              />
            </div>
          </div>

          {/* Inline variant: connections panel */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline -- connections panel
            </p>
            <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] max-w-sm">
              <EmptyState
                message="No connections yet."
                description="Highlight similar concepts across articles to discover connections."
              />
            </div>
          </div>

          {/* Inline variant: search no results */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline -- search no results
            </p>
            <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] max-w-md">
              <EmptyState message='No results found for "distributed systems".' />
            </div>
          </div>

          {/* Inline variant: filtered view */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline -- filtered content list
            </p>
            <EmptyState message="No archived items." />
          </div>

          {/* Inline variant: filter dropdown */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Inline -- tag filter (replaces &quot;NO TAGS FOUND&quot;)
            </p>
            <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] max-w-xs px-3">
              <EmptyState message="No tags found." className="py-6" />
            </div>
          </div>

          {/* Divider */}
          <hr className="border-[var(--color-border-subtle)]" />

          {/* Bordered variant: page-level */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Bordered -- lists page (no lists)
            </p>
            <EmptyStateBordered
              message="No lists yet"
              description="Create your first list to organize your content."
              actionLabel="Create your first list"
              onAction={() => alert("Create")}
            />
          </div>

          {/* Bordered variant: list detail empty */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Bordered -- list detail (no articles in list)
            </p>
            <EmptyStateBordered
              message="No content yet"
              actionLabel="Add your first item"
              onAction={() => alert("Add")}
            />
          </div>

          {/* Bordered variant: dashboard empty */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Bordered -- dashboard (no content)
            </p>
            <EmptyStateBordered
              message="No content yet"
              description="Add your first article above to get started."
            />
          </div>

          {/* Bordered variant: recommendations */}
          <div className="space-y-2">
            <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
              Bordered -- recommendations
            </p>
            <EmptyStateBordered
              message="No recommendations yet"
              description="Read some articles to get personalized suggestions."
            />
          </div>
        </section>

        {/* Divider */}
        <hr className="border-[var(--color-border)]" />

        {/* ── SECTION: Side-by-side comparison ── */}
        <section className="space-y-4">
          <h2 className="font-serif text-lg mb-1">
            Error vs Empty -- same surface
          </h2>
          <p className="text-xs text-[var(--color-text-faint)] mb-6">
            Shows how the two states look in the same space. These should feel
            distinct but related -- the user should immediately recognize which
            is which.
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Connections -- error
              </p>
              <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-4">
                <InlineError
                  message="Couldn't load connections."
                  onRetry={() => alert("Retry")}
                />
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Connections -- empty
              </p>
              <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-4">
                <EmptyState
                  message="No connections yet."
                  description="Highlight similar concepts across articles to discover connections."
                />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Lists page -- error
              </p>
              <InlineError
                message="Failed to load lists."
                onRetry={() => alert("Retry")}
                onDismiss={() => {}}
              />
            </div>
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Lists page -- empty
              </p>
              <EmptyStateBordered
                message="No lists yet"
                description="Create your first list to organize your content."
                actionLabel="Create your first list"
                onAction={() => alert("Create")}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Search -- error
              </p>
              <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] max-w-xs">
                <InlineError
                  message="Search failed."
                  onRetry={() => alert("Retry")}
                  className="m-3"
                />
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-[10px] font-mono tracking-widest text-[var(--color-text-faint)] uppercase">
                Search -- no results
              </p>
              <div className="border border-[var(--color-border)] bg-[var(--color-bg-primary)] max-w-xs">
                <EmptyState message='No results found for "distributed systems".' />
              </div>
            </div>
          </div>
        </section>

        {/* Footer spacer */}
        <div className="h-20" />
      </div>
    </div>
  );
}
