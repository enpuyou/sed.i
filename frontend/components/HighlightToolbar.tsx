"use client";

import { useState } from "react";
import { highlightsAPI } from "@/lib/api";

interface HighlightToolbarProps {
  selection: {
    text: string;
    startOffset: number;
    endOffset: number;
    position: { x: number; y: number };
    existingHighlightId?: string;
    existingColor?: string;
    existingNote?: string;
  } | null;
  contentId: string;
  onClose: () => void;
  onHighlightCreated?: (highlightId?: string) => void;
  onOptimisticCreate?: (color: string) => void;
  // When provided, replaces the API call — used by EphemeralReader to capture local highlights
  onHighlightCreate?: (highlight: {
    text: string;
    start_offset: number;
    end_offset: number;
    color: string;
  }) => void;
}

const colors = ["yellow", "green", "blue", "pink", "purple"];

export default function HighlightToolbar({
  selection,
  contentId,
  onClose,
  onHighlightCreated,
  onOptimisticCreate,
  onHighlightCreate,
}: HighlightToolbarProps) {
  const isEditing = !!selection?.existingHighlightId;
  const [isLoading, setIsLoading] = useState(false);

  if (!selection) return null;

  // Position below the selection, centered
  const getPosition = () => {
    const viewportWidth =
      typeof window !== "undefined" ? window.innerWidth : 800;

    // Estimate width to prevent overflow
    const isMobile = viewportWidth < 640;
    const estimatedWidth = isMobile ? 240 : 320;

    let x = selection.position.x - estimatedWidth / 2;
    if (x < 8) x = 8;
    if (x + estimatedWidth > viewportWidth - 8)
      x = viewportWidth - estimatedWidth - 8;

    const y = selection.position.y + 8;

    return { x, y };
  };

  const pos = getPosition();

  const handleHighlight = async (color: string, shouldOpenNote = false) => {
    if (isLoading) return;

    try {
      setIsLoading(true);

      let highlightId = selection.existingHighlightId;

      if (isEditing && selection.existingHighlightId) {
        await highlightsAPI.update(selection.existingHighlightId, {
          color,
          // We don't touch the note here
        });
      } else {
        if (!isEditing && onOptimisticCreate) {
          onOptimisticCreate(color);
        }
        if (onHighlightCreate) {
          onHighlightCreate({
            text: selection.text,
            start_offset: selection.startOffset,
            end_offset: selection.endOffset,
            color,
          });
          highlightId = undefined;
        } else {
          const newHighlight = await highlightsAPI.create(contentId, {
            text: selection.text,
            start_offset: selection.startOffset,
            end_offset: selection.endOffset,
            color,
          });
          highlightId = newHighlight.id;
        }
      }

      // Pass the ID if we want to open the note
      onHighlightCreated?.(shouldOpenNote ? highlightId : undefined);

      // Clear native selection to prevent "wrong native highlight" artifacts
      window.getSelection()?.removeAllRanges();

      onClose();
    } catch (error) {
      console.error("Error:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!selection.existingHighlightId || isLoading) return;

    try {
      setIsLoading(true);
      await highlightsAPI.delete(selection.existingHighlightId);
      onHighlightCreated?.(); // Refresh

      // Clear native selection
      window.getSelection()?.removeAllRanges();

      onClose();
    } catch (error) {
      console.error("Error:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const toolbarStyleWidth = "min-content";

  return (
    <div
      className="highlight-toolbar fixed z-50 animate-fade-in"
      style={{ left: pos.x, top: pos.y, width: toolbarStyleWidth }}
    >
      {/* Main toolbar - smaller on mobile */}
      <div className="flex items-center flex-nowrap bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-sm p-1.5 gap-1.5 sm:p-1 sm:gap-1 leading-none w-max">
        {/* Color swatches */}
        {colors.map((color) => (
          <button
            key={color}
            onClick={() => handleHighlight(color)}
            disabled={isLoading}
            className={`
              transition-all border disabled:opacity-40 box-border block
              hover:saturate-200 hover:scale-105 shrink-0
              ${
                isEditing && selection.existingColor === color
                  ? "border-[var(--color-text-primary)]"
                  : "border-transparent hover:border-[var(--color-accent)]"
              }
              w-[18px] h-[18px] min-w-[18px] min-h-[18px]
              sm:w-[26px] sm:h-[26px] sm:min-w-[26px] sm:min-h-[26px]
            `}
            style={{ backgroundColor: `var(--highlight-${color})` }}
            aria-label={color}
          />
        ))}

        {/* Divider - Desktop only */}
        <div className="hidden sm:block w-px h-4 sm:h-5 bg-[var(--color-border)] mx-0.5" />

        {/* Desktop: Note button (Hidden on Mobile) */}
        <button
          onClick={() =>
            handleHighlight(selection.existingColor || "yellow", true)
          }
          disabled={isLoading}
          className={`
            hidden sm:block text-xs px-2 py-1 border transition-colors whitespace-nowrap shrink-0
            border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-accent)] hover:text-[var(--color-text-primary)]
          `}
        >
          Note
        </button>

        {/* Remove button - Only shown when editing */}
        {isEditing && (
          <button
            onClick={handleDelete}
            disabled={isLoading}
            className={`
             text-[10px] sm:text-xs px-1.5 py-0.5 sm:px-2 sm:py-1 border transition-colors whitespace-nowrap shrink-0
             min-h-[18px] sm:min-h-[26px] flex items-center
             border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-red-400 hover:text-red-500
          `}
          >
            Remove
          </button>
        )}
      </div>
    </div>
  );
}
