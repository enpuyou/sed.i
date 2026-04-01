"use client";

import { useState } from "react";
import { highlightsAPI } from "@/lib/api";
import InlineError from "./InlineError";
import EmptyState from "./EmptyState";

interface Highlight {
  id: string;
  text: string;
  start_offset: number;
  end_offset: number;
  color: string;
  note?: string;
}

interface HighlightsPanelProps {
  highlights: Highlight[];
  loading?: boolean;
  onHighlightClick: (highlight: Highlight) => void;
  onHighlightDeleted: () => void;
  onHighlightUpdated: () => void;
}

const colorOptions = [
  { name: "yellow", style: "background-color: var(--highlight-yellow);" },
  { name: "green", style: "background-color: var(--highlight-green);" },
  { name: "blue", style: "background-color: var(--highlight-blue);" },
  { name: "pink", style: "background-color: var(--highlight-pink);" },
  { name: "purple", style: "background-color: var(--highlight-purple);" },
];

export default function HighlightsPanel({
  highlights,
  loading,
  onHighlightClick,
  onHighlightDeleted,
  onHighlightUpdated,
}: HighlightsPanelProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNote, setEditNote] = useState("");
  const [editColor, setEditColor] = useState("");
  const [isDeleting, setIsDeleting] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [justCopied, setJustCopied] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleStartEdit = (highlight: Highlight) => {
    setEditingId(highlight.id);
    setEditNote(highlight.note || "");
    setEditColor(highlight.color);
    setActionError(null);
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditNote("");
    setEditColor("");
  };

  const handleSaveEdit = async (highlightId: string) => {
    try {
      setActionError(null);
      await highlightsAPI.update(highlightId, {
        note: editNote || undefined,
        color: editColor,
      });
      onHighlightUpdated();
      setEditingId(null);
      setEditNote("");
      setEditColor("");
    } catch (error) {
      console.error("Error updating highlight:", error);
      setActionError("Couldn't save changes. Try again.");
    }
  };

  const handleDelete = async (highlightId: string) => {
    try {
      setIsDeleting(highlightId);
      setActionError(null);
      await highlightsAPI.delete(highlightId);
      onHighlightDeleted();
    } catch (error) {
      console.error("Error deleting highlight:", error);
      setActionError("Couldn't delete highlight. Try again.");
    } finally {
      setIsDeleting(null);
      setDeleteConfirmId(null);
    }
  };

  const handleCopyAllHighlights = async () => {
    const markdown = highlights
      .map((h) => {
        const noteSection = h.note ? `\n\n${h.note}` : "";
        return `> ${h.text}${noteSection}\n\n---`;
      })
      .join("\n\n");

    try {
      setActionError(null);
      await navigator.clipboard.writeText(markdown);
      setJustCopied(true);
      setTimeout(() => setJustCopied(false), 4000);
    } catch (error) {
      console.error("Failed to copy highlights:", error);
      setActionError("Couldn't copy to clipboard.");
    }
  };

  if (loading && highlights.length === 0) {
    return (
      <div className="p-4 text-center text-sm text-[var(--color-text-muted)]">
        Loading highlights…
      </div>
    );
  }

  if (highlights.length === 0) {
    return (
      <EmptyState
        message="No highlights yet."
        description="Select text in the article to create a highlight."
        className="p-4"
      />
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Sticky copy button + error */}
      <div className="px-4 pt-4 pb-2 space-y-2">
        <button
          onClick={handleCopyAllHighlights}
          className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
          title="Copy all highlights as Markdown"
          aria-label="Copy all highlights"
        >
          {justCopied
            ? `Copied (${highlights.length})`
            : `Copy (${highlights.length})`}
        </button>

        {actionError && (
          <InlineError
            message={actionError}
            onDismiss={() => setActionError(null)}
            className="py-1.5"
          />
        )}
      </div>

      {/* Highlights List - Scrollable */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {highlights.map((highlight) => {
          const isEditing = editingId === highlight.id;
          const isBeingDeleted = isDeleting === highlight.id;

          return (
            <div
              key={highlight.id}
              className="border border-transparent hover:border-[var(--color-accent)] p-2 mb-4 transition-colors rounded-none"
              role="listitem"
            >
              {/* Highlighted Text */}
              <div
                className="p-2 rounded-none mb-2 text-sm outline-none cursor-pointer"
                style={
                  {
                    backgroundColor: `var(--highlight-${isEditing ? editColor : highlight.color})`,
                  } as React.CSSProperties
                }
                onClick={() => !isEditing && onHighlightClick(highlight)}
                onKeyDown={(e) => {
                  if ((e.key === "Enter" || e.key === " ") && !isEditing) {
                    e.preventDefault();
                    onHighlightClick(highlight);
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label={`Go to highlight: ${highlight.text}`}
              >
                {highlight.text.length > 150
                  ? `${highlight.text.substring(0, 150)}...`
                  : highlight.text}
              </div>

              {/* Note Section */}
              {isEditing ? (
                <div className="space-y-2 mb-2">
                  {/* Color Picker - matches toolbar style */}
                  <div className="flex gap-1">
                    {colorOptions.map((color) => (
                      <button
                        key={color.name}
                        onClick={() => setEditColor(color.name)}
                        className={`w-6 h-6 border transition-all ${
                          editColor === color.name
                            ? "border-[var(--color-text-primary)]"
                            : "border-transparent hover:border-[var(--color-accent)]"
                        }`}
                        style={
                          {
                            backgroundColor: `var(--highlight-${color.name})`,
                          } as React.CSSProperties
                        }
                        title={color.name}
                        aria-label={`Select color ${color.name}`}
                      />
                    ))}
                  </div>

                  {/* Note Input */}
                  <textarea
                    value={editNote}
                    onChange={(e) => setEditNote(e.target.value)}
                    placeholder="Add a note..."
                    className="w-full px-2 py-1 text-sm border border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] resize-none"
                    rows={5}
                    aria-label="Edit note"
                  />
                </div>
              ) : (
                highlight.note && (
                  <div className="text-xs text-[var(--color-text-secondary)] mb-2 p-2 bg-[var(--color-bg-secondary)]">
                    {highlight.note}
                  </div>
                )
              )}

              {/* Action Buttons */}
              <div className="flex gap-2">
                {isEditing ? (
                  <>
                    <button
                      onClick={() => handleSaveEdit(highlight.id)}
                      className="text-xs px-3 py-1.5 rounded-none border border-[var(--color-accent)] text-[var(--color-accent)] hover:text-[var(--color-text-primary)] transition-colors"
                      aria-label="Save"
                    >
                      Save
                    </button>
                    <button
                      onClick={handleCancelEdit}
                      className="text-xs px-3 py-1.5 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                      aria-label="Cancel"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => handleStartEdit(highlight)}
                      className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                      aria-label="Edit"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => {
                        if (deleteConfirmId === highlight.id) {
                          handleDelete(highlight.id);
                        } else {
                          setDeleteConfirmId(highlight.id);
                        }
                      }}
                      disabled={isBeingDeleted}
                      className={`text-xs px-2 py-1 rounded-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                        deleteConfirmId === highlight.id
                          ? "border border-red-400 text-red-500 dark:text-red-400"
                          : "bg-rose-50 dark:bg-red-900/30 text-rose-500 dark:text-red-400 hover:bg-red-50 hover:text-red-400 dark:hover:bg-red-900/50"
                      }`}
                      aria-label="Delete"
                    >
                      {isBeingDeleted
                        ? "Deleting..."
                        : deleteConfirmId === highlight.id
                          ? "Confirm?"
                          : "Delete"}
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
