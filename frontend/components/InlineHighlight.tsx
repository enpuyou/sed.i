"use client";

import { useState, useEffect, useRef } from "react";
import { SHOW_HIGHLIGHT_CONNECTIONS } from "@/lib/flags";
import { highlightsAPI } from "@/lib/api";

interface InlineHighlightProps {
  id: string;
  color: string;
  initialNote?: string;
  // Controlled state props
  isOpen?: boolean;
  onToggle?: (isOpen: boolean) => void;
  draftNote?: string;
  onNoteChange?: (note: string) => void;

  children: React.ReactNode;
  onDelete?: (id: string) => void;
  onUpdate?: () => void;
  onHighlightClick?: (id: string, element: Element) => void;
  isMobile?: boolean;
  onShowConnections?: (highlightId: string) => void;
  showIndicators?: boolean;
  hasConnections?: boolean;
}

const colors = ["yellow", "green", "blue", "pink", "purple"];

export default function InlineHighlight({
  id,
  color: initialColor,
  initialNote,
  // Controlled props with fallbacks for backward compatibility (if any)
  isOpen: propsIsOpen,
  onToggle,
  draftNote: propsDraftNote,
  onNoteChange,

  children,
  onDelete,
  onUpdate,
  onHighlightClick: _onHighlightClick,
  isMobile = false,
  onShowConnections,
  showIndicators = true,
  hasConnections: hasConnectionsProp = false,
}: InlineHighlightProps) {
  // Internal state for uncontrolled usage (fallback)
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  const [internalNote, setInternalNote] = useState(initialNote || "");
  const [color, setColor] = useState(initialColor);
  const [isSaving, setIsSaving] = useState(false);
  const hasConnections = hasConnectionsProp;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Derived state
  const isControlled = propsIsOpen !== undefined;
  const isOpen = isControlled ? propsIsOpen : internalIsOpen;
  const note = isControlled
    ? propsDraftNote !== undefined
      ? propsDraftNote
      : internalNote
    : internalNote;

  // Handlers
  const handleSetIsOpen = (open: boolean) => {
    if (isControlled) {
      onToggle?.(open);
    } else {
      setInternalIsOpen(open);
    }
  };

  const handleSetNote = (newNote: string) => {
    if (isControlled && onNoteChange) {
      onNoteChange(newNote);
    } else {
      setInternalNote(newNote);
    }
  };

  // Sync internal note if initialNote changes (only for uncontrolled or display)
  // For controlled mode, the parent handles the "saved" note vs "draft" note logic
  // But we still need "visualNote" behavior?
  // actually, let's use initialNote as the "saved" state and note as "current".

  // Update local state if prop changes (e.g. from parent refresh)
  useEffect(() => {
    if (initialNote !== undefined && !isControlled) {
      setInternalNote(initialNote);
    }
    setColor(initialColor);
  }, [initialNote, initialColor, isControlled]);

  useEffect(() => {
    if (isOpen && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(
        textareaRef.current.value.length,
        textareaRef.current.value.length,
      );
    }
  }, [isOpen]);

  // Auto-resize textarea
  const adjustHeight = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  };

  useEffect(() => {
    if (isOpen) {
      adjustHeight();
    }
  }, [isOpen, note]);

  const handleToggle = (e: React.MouseEvent) => {
    if (isMobile) return;

    if (isOpen) {
      e.stopPropagation();
      e.preventDefault();
      if (typeof window !== "undefined") {
        window.getSelection()?.removeAllRanges();
      }
      handleSave();
      return;
    }

    if (initialNote) {
      e.stopPropagation();
      e.preventDefault();
      if (typeof window !== "undefined") {
        window.getSelection()?.removeAllRanges();
      }
      handleSetIsOpen(true);
    }
  };

  const handleSave = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (isSaving) return;

    try {
      setIsSaving(true);
      await highlightsAPI.update(id, {
        note: note || undefined,
        color: color,
      });
      // Close editor
      handleSetIsOpen(false);
      onUpdate?.();
    } catch (error) {
      console.error("Failed to save note:", error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleColorChange = async (newColor: string) => {
    if (color === newColor) return;
    setColor(newColor);
    try {
      await highlightsAPI.update(id, { color: newColor });
      onUpdate?.();
    } catch (error) {
      console.error(error);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Save on Cmd+Enter or Ctrl+Enter OR Escape (Auto-save on close)
    if (((e.metaKey || e.ctrlKey) && e.key === "Enter") || e.key === "Escape") {
      e.preventDefault();
      handleSave();
    }
  };

  const handleDeleteHighlight = async (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete?.(id);
  };

  return (
    <>
      <span
        data-highlight-id={id}
        className={`
            relative transition-colors duration-200 box-decoration-clone
            ${!isMobile ? "cursor-pointer hover:saturate-120" : ""}
            ${isOpen ? "ring-1 ring-[var(--color-text-primary)] z-10" : ""}
        `}
        style={{
          backgroundColor: `var(--highlight-${color})`,
          // Ensure purely rectangular - no rounding
          borderRadius: 0,
        }}
        onClick={handleToggle}
        title={
          !isMobile
            ? initialNote
              ? "Click to edit note"
              : "Click to add note"
            : ""
        }
      >
        {/* Connection Indicator - Bottom left of highlight start */}
        {SHOW_HIGHLIGHT_CONNECTIONS &&
          hasConnections &&
          onShowConnections &&
          !isOpen &&
          showIndicators && (
            <span
              className="ephemeral-ui inline-block relative"
              style={{
                width: 0,
                height: 0,
                verticalAlign: "text-bottom",
                overflow: "visible",
              }}
              data-ephemeral="true"
              contentEditable={false}
              suppressContentEditableWarning
            >
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onShowConnections?.(id);
                }}
                className="absolute -bottom-1 -left-1 w-2 h-2 bg-blue-500 rounded-full cursor-pointer hover:scale-125 transition-transform"
                title="This highlight has connections to other articles"
              />
            </span>
          )}
        {children}
        {/* Note Indicator - Top right corner of the highlight end */}
        {initialNote && !isOpen && showIndicators && (
          <span
            className="ephemeral-ui absolute"
            style={{
              height: "1em",
              width: 0,
              display: "inline-block",
              position: "relative",
              verticalAlign: "top",
              overflow: "visible",
              pointerEvents: "none",
            }}
            data-ephemeral="true"
            contentEditable={false}
            suppressContentEditableWarning
          >
            <span className="absolute right-0 top-1 translate-x-1/2 -translate-y-1/2 rounded-full h-2 w-2 bg-[var(--color-text-primary)] shadow-sm"></span>
          </span>
        )}
      </span>
      {isOpen && (
        <span
          className="ephemeral-ui flex justify-center w-full py-8 animate-fade-in block"
          style={{
            paddingRight: "20px",
          }}
          data-ephemeral="true"
          contentEditable={false}
          suppressContentEditableWarning
        >
          <span
            className="block bg-[var(--color-bg-primary)] w-[95%]"
            onClick={(e) => e.stopPropagation()}
          >
            <textarea
              ref={textareaRef}
              value={note}
              onChange={(e) => handleSetNote(e.target.value)}
              onInput={adjustHeight}
              onKeyDown={handleKeyDown}
              placeholder="Write a note..."
              className="w-full bg-transparent border border-[var(--color-text-primary)] outline-none text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] text-base font-serif p-4 resize-none overflow-hidden leading-relaxed block focus:!border-[var(--color-text-primary)] focus:!ring-0 focus:!outline-none focus:!shadow-none"
              rows={1}
              style={{
                minHeight: "60px",
              }}
            />
            <div className="flex items-center justify-between pt-2 bg-[var(--color-bg-primary)]">
              {/* LEFT GROUP: Delete + Color Picker */}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleDeleteHighlight}
                  className="text-xs px-2 py-0.5 leading-none rounded-none border bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border-[var(--color-border)] hover:border-red-400 hover:text-red-500 transition-colors tracking-wider font-sans"
                >
                  Delete
                </button>

                {/* Integrated Color Picker */}
                <div className="flex gap-0.5">
                  {colors.map((c) => (
                    <button
                      key={c}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleColorChange(c);
                      }}
                      className={`
                          w-[22px] h-[22px] border transition-all hover:scale-110
                          ${color === c ? "border-[var(--color-text-primary)] scale-110 shadow-sm" : "border-transparent opacity-70 hover:opacity-100"}
                      `}
                      style={{ backgroundColor: `var(--highlight-${c})` }}
                      title={c}
                    />
                  ))}
                </div>
              </div>

              {/* RIGHT GROUP: Cancel + Save */}
              <div className="flex gap-2 tracking-wide">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSetIsOpen(false);
                    handleSetNote(initialNote || "");
                  }}
                  className="text-xs px-2 py-0.5 leading-none rounded-none border bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors font-sans"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={isSaving}
                  className="text-xs px-2 py-0.5 leading-none rounded-none border bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] border-[var(--color-text-primary)] hover:opacity-90 transition-opacity disabled:opacity-50 font-sans"
                >
                  {isSaving ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </span>
        </span>
      )}
    </>
  );
}
