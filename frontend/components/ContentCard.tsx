/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ContentItem as ContentItemType } from "@/types";
import StatusIndicator from "./StatusIndicator";
import MobileActionsMenu from "./MobileActionsMenu";
import { contentAPI } from "@/lib/api";
import RetroLoader from "./RetroLoader";

interface ContentCardProps {
  content: ContentItemType;
  onStatusChange: (
    id: string,
    updates: { is_read?: boolean; is_archived?: boolean },
  ) => void;
  onDelete: (id: string) => void;
  onUpdate?: (updatedContent: ContentItemType) => void;
  onRemoveFromList?: () => void;
  availableLists?: Array<{ id: string; name: string }>;
  onAddToList?: (listId: string) => void;
  returnPath?: string; // Path to return to when clicking back from reader
  isRemoving?: boolean;
}

export default function ContentCard({
  content,
  onStatusChange,
  onDelete,
  onUpdate,
  onRemoveFromList,
  availableLists,
  onAddToList,
  returnPath,
  isRemoving = false,
}: ContentCardProps) {
  const [isEditingTags, setIsEditingTags] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const router = useRouter();

  useEffect(() => {
    setMounted(true);
  }, []);

  // Load available tags when editing starts
  useEffect(() => {
    if (isEditingTags) {
      contentAPI
        .getTags()
        .then((tags: Array<{ tag: string; count: number }>) => {
          setAvailableTags(tags.map((t) => t.tag));
        })
        .catch((err) => {
          console.error("Failed to load tags:", err);
        });
    }
  }, [isEditingTags]);

  const handleAddTag = async () => {
    if (!tagInput.trim()) return;

    try {
      const currentTags = content.tags || [];
      if (currentTags.includes(tagInput.trim())) {
        return;
      }

      const updatedTags = [...currentTags, tagInput.trim()];

      // Optimistic update
      const optimisticContent = {
        ...content,
        tags: updatedTags,
      };
      if (onUpdate) {
        onUpdate(optimisticContent);
      }

      const updated = await contentAPI.update(content.id, {
        tags: updatedTags,
      });
      if (onUpdate) {
        onUpdate(updated);
      }
      setTagInput("");
    } catch (error) {
      console.error("Failed to add tag:", error);
      // Revert optimistic update
      if (onUpdate) {
        onUpdate(content);
      }
    }
  };

  const handleRemoveTag = async (tagToRemove: string) => {
    try {
      const updatedTags = (content.tags || []).filter((t) => t !== tagToRemove);
      const updatedAutoTags = (content.auto_tags || []).filter(
        (t) => t !== tagToRemove,
      );

      // Optimistic update
      const optimisticContent = {
        ...content,
        tags: updatedTags,
        auto_tags: updatedAutoTags,
      };
      if (onUpdate) {
        onUpdate(optimisticContent);
      }

      const updated = await contentAPI.update(content.id, {
        tags: updatedTags,
        auto_tags: updatedAutoTags,
      });
      if (onUpdate) {
        onUpdate(updated);
      }
    } catch (error) {
      console.error("Failed to remove tag:", error);
      // Revert optimistic update
      if (onUpdate) {
        onUpdate(content);
      }
    }
  };

  const handleCardClick = (e: React.MouseEvent) => {
    // Don't navigate if clicking on action buttons or links
    if (
      (e.target as HTMLElement).closest("button") ||
      (e.target as HTMLElement).closest("a")
    ) {
      return;
    }
    // Save scroll position and return path before navigating
    sessionStorage.setItem("contentListScrollPos", window.scrollY.toString());
    if (returnPath) {
      sessionStorage.setItem("readerReturnPath", returnPath);
    }
    router.push(`/content/${content.id}`);
  };

  // Show processing states with helpful messages
  const isProcessing =
    content.processing_status === "pending" ||
    content.processing_status === "processing";
  const hasFailed = content.processing_status === "failed";

  // Check if content was added within last 10 minutes
  const isJustAdded = () => {
    if (!mounted) return false;
    const now = new Date();
    const createdAt = new Date(content.created_at);
    const diffMinutes = (now.getTime() - createdAt.getTime()) / (1000 * 60);
    return diffMinutes < 10;
  };

  return (
    <div
      onClick={handleCardClick}
      className={`block p-4 border border-[var(--color-border)] transition-colors hover:border-[var(--color-accent)] cursor-pointer bg-[var(--color-bg-primary)] relative${isProcessing ? " opacity-70" : ""}`}
    >
      {/* Retro Removing Overlay */}
      {isRemoving && (
        <div className="absolute inset-0 flex items-center justify-center z-20 bg-[var(--color-bg-primary)]/90 font-mono text-sm text-[var(--color-accent)]">
          <span className="animate-pulse">Removing...</span>
          <span className="inline-block w-2.5 h-4 bg-[var(--color-accent)] ml-1 animate-pulse"></span>
        </div>
      )}

      {/* Absolute Mobile Actions Menu (Top Right) */}
      <div className="absolute top-2 right-2 z-10">
        <MobileActionsMenu
          onRead={() =>
            onStatusChange(content.id, { is_read: !content.is_read })
          }
          onArchive={() =>
            onStatusChange(content.id, {
              is_archived: !content.is_archived,
            })
          }
          onAddTag={() => setIsEditingTags(true)}
          onDelete={() => setConfirmDelete(true)}
          onAddToList={
            availableLists && availableLists.length > 0 && onAddToList
              ? (listId) => onAddToList(listId)
              : undefined
          }
          onRemoveFromList={onRemoveFromList}
          isRead={content.is_read}
          isArchived={content.is_archived}
          availableLists={availableLists}
        />
      </div>

      <div className="flex items-start gap-4">
        {/* Thumbnail */}
        {content.thumbnail_url && (
          <img
            src={content.thumbnail_url}
            alt=""
            className="w-20 h-20 object-cover flex-shrink-0 opacity-80"
          />
        )}

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Processing Status Badge */}
          {isProcessing && (
            <div className="flex items-center gap-2 mb-2">
              <RetroLoader
                text={
                  content.content_type === "pdf"
                    ? "Analyzing PDF layout..."
                    : content.processing_status === "pending"
                      ? "Finding your article"
                      : "Preparing your article"
                }
                className="text-xs text-[var(--color-text-muted)] italic"
              />
            </div>
          )}

          {/* Status and metadata */}
          {!isProcessing && (
            <div className="flex items-center gap-2 mb-2 text-xs text-[var(--color-text-muted)]">
              {hasFailed ? (
                (() => {
                  const isBlocked =
                    content.processing_error?.includes("403") ||
                    content.processing_error?.includes("forbidden") ||
                    content.processing_error?.includes("bot");

                  return isBlocked ? (
                    <>
                      <span className="inline-block w-2 h-2 rounded-full bg-orange-400 flex-shrink-0"></span>
                      <span className="text-xs px-2 py-0.5 bg-orange-50 dark:bg-orange-950/20 text-orange-600 dark:text-orange-400 border border-orange-200 dark:border-orange-800">
                        This article is blocked from us :/
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="inline-block w-2 h-2 rounded-full bg-red-500 flex-shrink-0"></span>
                      <span className="text-xs px-2 py-0.5 bg-red-50 dark:bg-red-950/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
                        Failed to extract
                      </span>
                    </>
                  );
                })()
              ) : (
                <StatusIndicator readingStatus={content.reading_status} />
              )}
              {isJustAdded() ? (
                <span>Just now</span>
              ) : (
                <span>
                  {mounted
                    ? new Date(content.created_at).toLocaleDateString()
                    : new Date(content.created_at).toISOString().split("T")[0]}
                </span>
              )}
              {content.reading_time_minutes && (
                <>
                  <span>·</span>
                  <span>{content.reading_time_minutes} min read</span>
                </>
              )}
            </div>
          )}

          {/* Title */}
          <h3 className="font-serif text-lg font-medium text-[var(--color-text-primary)] mb-1 line-clamp-2 pr-6 sm:pr-0">
            {(() => {
              // Show helpful title based on state
              if (content.title) {
                return content.title;
              }
              if (hasFailed) {
                const isBlocked =
                  content.processing_error?.includes("403") ||
                  content.processing_error?.includes("forbidden") ||
                  content.processing_error?.includes("bot");

                return isBlocked
                  ? "This article is playing hard to get"
                  : "We couldn't load your article";
              }
              return "Untitled";
            })()}
          </h3>

          {/* Description */}
          {/* Description / Failed link */}
          {content.description && !isProcessing && !hasFailed ? (
            <p className="text-sm text-[var(--color-text-muted)] line-clamp-2 mb-2">
              {content.description}
            </p>
          ) : isProcessing ? null : hasFailed ? (
            <p className="text-sm text-[var(--color-text-muted)] line-clamp-2 mb-2">
              <a
                href={content.original_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[var(--color-accent)] hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                View original
              </a>
            </p>
          ) : null}

          {/* Tags */}
          {(() => {
            const allTags = Array.from(
              new Set([...(content.tags || []), ...(content.auto_tags || [])]),
            );
            if (allTags.length === 0) return null;

            return (
              <div className="space-y-2 mb-3">
                <div className="flex items-center gap-2 flex-wrap">
                  {allTags.map((tag, index) => (
                    <span
                      key={index}
                      className="text-xs text-[var(--color-text-muted)] border-b border-[var(--color-border)] pb-0.5 flex items-center gap-1 cursor-default"
                    >
                      {tag}
                      {isEditingTags && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRemoveTag(tag);
                          }}
                          className="text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] ml-1 cursor-pointer"
                        >
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            );
          })()}

          {/* Tag editing UI */}
          {isEditingTags && (
            <div className="mb-2 space-y-2">
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <input
                    type="text"
                    value={tagInput}
                    onChange={(e) => {
                      setTagInput(e.target.value);
                      setShowSuggestions(e.target.value.length > 0);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddTag();
                        setShowSuggestions(false);
                      }
                    }}
                    onFocus={() => {
                      if (tagInput.length > 0) setShowSuggestions(true);
                    }}
                    onBlur={() => {
                      // Delay hiding suggestions to allow click on suggestion
                      setTimeout(() => setShowSuggestions(false), 200);
                    }}
                    placeholder="Add tag..."
                    className="w-full px-2 py-1 text-xs border border-[var(--color-border)] bg-transparent focus:outline-none focus:!ring-0"
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                  {/* Autocomplete suggestions */}
                  {showSuggestions && tagInput.length > 0 && (
                    <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded shadow-sm z-10">
                      {availableTags
                        .filter(
                          (tag) =>
                            tag
                              .toLowerCase()
                              .includes(tagInput.toLowerCase()) &&
                            !content.tags?.includes(tag),
                        )
                        .slice(0, 5)
                        .map((tag) => (
                          <button
                            key={tag}
                            onClick={(e) => {
                              e.stopPropagation();
                              setTagInput(tag);
                              setShowSuggestions(false);
                              // Auto-add the tag
                              setTimeout(() => {
                                const newTags = [...(content.tags || []), tag];
                                contentAPI
                                  .update(content.id, {
                                    tags: newTags,
                                  })
                                  .then((updated) => {
                                    if (onUpdate) onUpdate(updated);
                                    setTagInput("");
                                  })
                                  .catch((err) =>
                                    console.error("Failed to add tag:", err),
                                  );
                              }, 0);
                            }}
                            className="w-full text-left px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)] border-b border-[var(--color-border)] last:border-b-0"
                          >
                            {tag}
                          </button>
                        ))}
                      {availableTags.filter(
                        (tag) =>
                          tag.toLowerCase().includes(tagInput.toLowerCase()) &&
                          !content.tags?.includes(tag),
                      ).length === 0 && (
                        <div className="px-2 py-1 text-xs text-[var(--color-text-faint)]">
                          No matching tags
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleAddTag();
                    setShowSuggestions(false);
                  }}
                  className="text-xs px-2 py-1 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-text-primary)] hover:text-[var(--color-text-primary)] transition-colors"
                >
                  Add
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsEditingTags(false);
                    setTagInput("");
                    setShowSuggestions(false);
                  }}
                  className="text-xs px-2 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                >
                  Done
                </button>
              </div>
            </div>
          )}

          {/* Inline delete confirm strip */}
          {confirmDelete && (
            <div className="flex items-center justify-between mt-3 pt-2 border-t border-red-200 dark:border-red-800/40">
              <span className="font-mono text-xs text-red-500 dark:text-red-400">
                Delete this article?
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDelete(false);
                    onDelete(content.id);
                  }}
                  className="font-mono text-xs px-2 py-0.5 border border-red-400 text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
                >
                  confirm
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDelete(false);
                  }}
                  className="font-mono text-xs px-2 py-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
                >
                  cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
