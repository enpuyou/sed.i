"use client";

import { useState, useEffect } from "react";
import { listsAPI } from "@/lib/api";
import InlineError from "./InlineError";

interface ListModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  // If editing, pass the list data. If creating, leave undefined
  list?: {
    id: string;
    name: string;
    description: string | null;
  };
}

export default function ListModal({
  isOpen,
  onClose,
  onSuccess,
  list,
}: ListModalProps) {
  // Form state
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Determine if we're editing or creating
  const isEditing = !!list;

  // Populate form when editing
  useEffect(() => {
    if (list) {
      setName(list.name);
      setDescription(list.description || "");
    } else {
      // Reset form when creating new list
      setName("");
      setDescription("");
    }
    setError("");
  }, [list, isOpen]);

  // Close modal and prevent body scroll
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "unset";
    }
    return () => {
      document.body.style.overflow = "unset";
    };
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    // Validation
    if (!name.trim()) {
      setError("List name is required");
      return;
    }

    setLoading(true);

    try {
      if (isEditing && list) {
        // Update existing list
        await listsAPI.update(list.id, {
          name: name.trim(),
          description: description.trim() || undefined,
        });
      } else {
        // Create new list
        await listsAPI.create({
          name: name.trim(),
          description: description.trim() || undefined,
        });
      }

      // Close modal and notify parent to refresh
      onClose();
      onSuccess();
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : "Couldn't save list. Try again.";
      setError(errorMessage);
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    if (!loading) {
      onClose();
    }
  };

  // Handle ESC key to close
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen, loading, onClose]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/60 z-40" onClick={handleClose} />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none max-w-sm w-full p-5"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="mb-5">
            <h2 className="text-sm font-medium text-[var(--color-text-primary)]">
              {isEditing ? "Edit List" : "Create New List"}
            </h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              {isEditing
                ? "Update the details of your list"
                : "Organize your content into a collection"}
            </p>
          </div>

          {/* Error message */}
          {error && (
            <InlineError
              message={error}
              onDismiss={() => setError("")}
              className="mb-4 py-1.5"
            />
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name field */}
            <div>
              <label
                htmlFor="name"
                className="block text-xs font-medium text-[var(--color-text-primary)] mb-1"
              >
                List Name *
              </label>
              <input
                type="text"
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Web Development, Reading List"
                required
                disabled={loading}
                className="w-full px-0 py-2 border-0 border-b border-[var(--color-border)] bg-transparent rounded-none focus:outline-none focus:border-[var(--color-accent)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>

            {/* Description field */}
            <div>
              <label
                htmlFor="description"
                className="block text-xs font-medium text-[var(--color-text-primary)] mb-1"
              >
                Description (optional)
              </label>
              <textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What's this list about?"
                rows={3}
                disabled={loading}
                className="w-full px-0 py-2 border-0 border-b border-[var(--color-border)] bg-transparent rounded-none focus:outline-none focus:border-[var(--color-accent)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] disabled:opacity-50 disabled:cursor-not-allowed resize-none"
              />
            </div>

            {/* Buttons */}
            <div className="flex gap-2 pt-4">
              <button
                type="button"
                onClick={handleClose}
                disabled={loading}
                className="flex-1 text-xs px-2 py-1 leading-none rounded-none border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading || !name.trim()}
                className="flex-1 text-xs px-2 py-1 leading-none rounded-none border border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading
                  ? "Saving..."
                  : isEditing
                    ? "Update List"
                    : "Create List"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
