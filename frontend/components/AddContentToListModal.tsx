/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useEffect, useCallback } from "react";
import { contentAPI, listsAPI } from "@/lib/api";
import { ContentItem } from "@/types";
import { useLists } from "@/contexts/ListsContext";
import InlineError from "./InlineError";

interface AddContentToListModalProps {
  isOpen: boolean;
  listId: string;
  onClose: () => void;
  onSuccess: () => void;
  existingContentIds?: string[];
}

export default function AddContentToListModal({
  isOpen,
  listId,
  onClose,
  onSuccess,
  existingContentIds = [],
}: AddContentToListModalProps) {
  const { incrementListCount, decrementListCount } = useLists();
  const [allContent, setAllContent] = useState<ContentItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fetchAllContent = useCallback(async () => {
    try {
      setFetching(true);
      const data = await contentAPI.getAll();
      setAllContent(data.items || []);
    } catch (error) {
      console.error("Failed to fetch content:", error);
    } finally {
      setFetching(false);
    }
  }, []);

  // Fetch all user's content when modal opens
  useEffect(() => {
    if (isOpen) {
      fetchAllContent();
      setSelectedIds(new Set(existingContentIds));
      setSearchQuery("");
    }
  }, [isOpen, fetchAllContent, existingContentIds]);

  const handleToggleSelect = (id: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedIds(newSelected);
  };

  const handleSubmit = async () => {
    if (selectedIds.size === 0) {
      return;
    }

    const itemCount = selectedIds.size;

    try {
      setLoading(true);
      setError(null);
      // Optimistic update - increment count by number of items being added
      for (let i = 0; i < itemCount; i++) {
        incrementListCount(listId);
      }

      await listsAPI.addContent(listId, Array.from(selectedIds));
      onSuccess();
      onClose();
    } catch (err) {
      console.error("Failed to add content to list:", err);
      setError("Couldn't add content to list. Try again.");
      // Revert on error - decrement count back
      for (let i = 0; i < itemCount; i++) {
        decrementListCount(listId);
      }
    } finally {
      setLoading(false);
    }
  };

  // Filter content by search query
  const filteredContent = allContent.filter((item) => {
    const query = searchQuery.toLowerCase();
    return (
      (item.title?.toLowerCase().includes(query) ||
        item.description?.toLowerCase().includes(query) ||
        item.description?.toLowerCase().includes(query) ||
        item.original_url.toLowerCase().includes(query)) &&
      item.processing_status !== "failed"
    );
  });

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none max-w-lg w-full max-h-[70vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="p-4 border-b border-[var(--color-border)]">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-[var(--color-text-primary)]">
              Add Content to List
            </h2>
            <button
              onClick={onClose}
              className="text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
            >
              <svg
                className="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>

          {/* Search input */}
          <input
            type="text"
            placeholder="Search your content..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full px-0 py-2 border-0 border-b border-[var(--color-border)] bg-transparent rounded-none focus:outline-none focus:border-[var(--color-accent)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
          />
        </div>

        {/* Content list */}
        <div className="flex-1 overflow-y-auto p-4">
          {fetching ? (
            <div className="text-center py-8 text-[var(--color-text-muted)]">
              Loading content...
            </div>
          ) : filteredContent.length === 0 ? (
            <div className="text-center py-8 text-[var(--color-text-muted)]">
              {searchQuery
                ? "No content matches your search"
                : "No content available"}
            </div>
          ) : (
            <div className="space-y-3">
              {filteredContent.map((item) => (
                <div
                  key={item.id}
                  onClick={() => handleToggleSelect(item.id)}
                  className={`p-4 border rounded-none cursor-pointer transition-colors ${
                    selectedIds.has(item.id)
                      ? "border-l-4 border-l-[var(--color-accent)] bg-[var(--color-bg-secondary)] border border-[var(--color-border)]"
                      : "border border-[var(--color-border)] hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-secondary)]"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    {/* Checkbox */}
                    <div className="flex-shrink-0 mt-1">
                      <div
                        className={`w-5 h-5 border-2 rounded-none flex items-center justify-center ${
                          selectedIds.has(item.id)
                            ? "bg-[var(--color-accent)] border-[var(--color-accent)]"
                            : "border-[var(--color-border)]"
                        }`}
                      >
                        {selectedIds.has(item.id) && (
                          <svg
                            className="w-3 h-3 text-white"
                            fill="currentColor"
                            viewBox="0 0 20 20"
                          >
                            <path
                              fillRule="evenodd"
                              d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                              clipRule="evenodd"
                            />
                          </svg>
                        )}
                      </div>
                    </div>

                    {/* Thumbnail */}
                    {item.thumbnail_url && (
                      <img
                        src={item.thumbnail_url}
                        alt=""
                        className="w-16 h-16 object-cover rounded-none flex-shrink-0"
                      />
                    )}

                    {/* Content info */}
                    <div className="flex-1 min-w-0">
                      <h3
                        className="font-medium text-[var(--color-text-primary)] line-clamp-1"
                        style={{ marginTop: "0px", marginBottom: "10px" }}
                      >
                        {item.title || "Untitled"}
                      </h3>
                      {item.description && (
                        <p className="text-sm text-[var(--color-text-secondary)] line-clamp-2 mt-1">
                          {item.description}
                        </p>
                      )}
                      <div className="flex items-center gap-2 mt-2 text-xs text-[var(--color-text-muted)]">
                        {item.reading_time_minutes && (
                          <span>{item.reading_time_minutes} min read</span>
                        )}
                        {item.is_read && (
                          <span className="px-2 py-0.5 border border-[var(--color-border)] text-[var(--color-text-muted)] rounded-none">
                            Read
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2">
          {error && (
            <InlineError
              message={error}
              onDismiss={() => setError(null)}
              className="py-1.5"
            />
          )}
          <div className="flex items-center justify-between">
            <span className="text-xs text-[var(--color-text-muted)]">
              {selectedIds.size} item{selectedIds.size !== 1 ? "s" : ""}{" "}
              selected
            </span>
            <div className="flex gap-2">
              <button
                onClick={onClose}
                disabled={loading}
                className="text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={loading || selectedIds.size === 0}
                className="text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? "Adding..." : `Add to List`}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
