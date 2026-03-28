/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ContentItem as ContentItemType } from "@/types";
import StatusIndicator from "./StatusIndicator";
import MobileActionsMenu from "./MobileActionsMenu";
import RetroLoader from "./RetroLoader";
import { useAuth } from "@/contexts/AuthContext";
import { contentAPI } from "@/lib/api";

/**
 * Props for ContentItem component
 *
 * onStatusChange: Called when user clicks read/unread or archive/unarchive
 *   - Receives the item id and an object with the fields to update
 *   - Example: onStatusChange('123', { is_read: true })
 *
 * onDelete: Called when user clicks delete button
 */
interface ContentItemProps {
  content: ContentItemType;
  onStatusChange: (
    id: string,
    updates: { is_read?: boolean; is_archived?: boolean; is_public?: boolean },
  ) => void;
  onDelete: (id: string) => void;
  // Called when content is updated (e.g., tags change)
  onUpdate?: (updatedContent: ContentItemType) => void;
  // Optional: for list detail page
  onRemoveFromList?: () => void;
  // Optional: for adding to lists
  availableLists?: Array<{ id: string; name: string }>;
  onAddToList?: (listId: string) => void;
  // Optional: path to return to from reader
  returnPath?: string;
  // Keyboard navigation
  isSelected?: boolean;
  id?: string;
  isRemoving?: boolean;
  // Read-only mode: hide all action buttons (for public profile view)
  readOnly?: boolean;
  // Override navigation target (e.g. /[username]/content/[id] for public profile)
  navigateTo?: string;
}

export default function ContentItem({
  content,
  onStatusChange,
  onDelete,
  onUpdate,
  onRemoveFromList,
  availableLists,
  onAddToList,
  returnPath,
  isSelected,
  id,
  isRemoving = false,
  readOnly = false,
  navigateTo,
}: ContentItemProps) {
  const { user } = useAuth();
  /**
   * Hydration fix: Only render relative dates on the client side
   * Server and client would calculate different "now" times, causing mismatch
   */
  const [mounted, setMounted] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showListDropdown, setShowListDropdown] = useState(false);
  const [isEditingTags, setIsEditingTags] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);
  const [tagInput, setTagInput] = useState("");
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
        .catch((err) => console.error("Failed to load tags:", err));
    }
  }, [isEditingTags]);

  /**
   * Handle status change with retro effect for archiving
   */
  const handleStatusChange = (
    e: React.MouseEvent | null,
    updates: { is_read?: boolean; is_archived?: boolean; is_public?: boolean },
  ) => {
    if (e) e.stopPropagation();

    // If archiving, show retro effect first
    if (updates.is_archived === true) {
      setIsArchiving(true);
      // Wait for retro effect (800ms)
      setTimeout(() => {
        onStatusChange(content.id, updates);
      }, 800);
    } else {
      onStatusChange(content.id, updates);
    }
  };

  /**
   * Format relative date (Today, Yesterday, X days ago, or full date)
   * This is a common pattern - you'll see this in many apps
   */
  const formatDate = (dateString: string) => {
    // Before hydration, show a stable format to avoid mismatch
    if (!mounted) {
      return new Date(dateString).toISOString().split("T")[0];
    }

    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays} days ago`;
    return date.toISOString().split("T")[0];
  };

  /**
   * Handle adding/removing tags
   */
  const handleUpdateTags = async (
    newTags: string[],
    newAutoTags?: string[],
  ) => {
    try {
      // Optimistic update
      const optimisticContent = {
        ...content,
        tags: newTags,
        ...(newAutoTags !== undefined ? { auto_tags: newAutoTags } : {}),
      };
      if (onUpdate) {
        onUpdate(optimisticContent);
      }

      const payload: { tags?: string[]; auto_tags?: string[] } = {
        tags: newTags,
      };
      if (newAutoTags !== undefined) {
        payload.auto_tags = newAutoTags;
      }

      const updatedContent = await contentAPI.update(content.id, payload);

      // Confirm with server state
      if (onUpdate) {
        onUpdate(updatedContent);
      }
    } catch (error) {
      console.error("Failed to update tags:", error);
      // Revert optimistic update
      if (onUpdate) {
        onUpdate(content);
      }
    }
  };

  const handleAddTag = () => {
    if (!tagInput.trim()) return;

    const currentTags = content.tags || [];
    if (currentTags.includes(tagInput.trim())) {
      return;
    }

    const newTags = [...currentTags, tagInput.trim()];
    handleUpdateTags(newTags);
    setTagInput("");
    setIsEditingTags(false);
  };

  const handleRemoveTag = (tagToRemove: string) => {
    const currentTags = content.tags || [];
    const newTags = currentTags.filter((tag) => tag !== tagToRemove);
    const newAutoTags = (content.auto_tags || []).filter(
      (tag) => tag !== tagToRemove,
    );
    handleUpdateTags(newTags, newAutoTags);
  };

  const handleContainerClick = (e: React.MouseEvent) => {
    // Don't navigate if clicking on interactive elements
    const target = e.target as HTMLElement;
    if (
      target.tagName === "BUTTON" ||
      target.tagName === "INPUT" ||
      target.tagName === "A" ||
      target.closest("button") ||
      target.closest("input") ||
      target.closest("a")
    ) {
      return;
    }
    // Save scroll position and return path before navigating
    if (navigateTo) {
      router.push(navigateTo);
      return;
    }
    sessionStorage.setItem("contentListScrollPos", window.scrollY.toString());
    if (returnPath) {
      sessionStorage.setItem("readerReturnPath", returnPath);
    }
    // Navigate to reader using Next.js router (preserves cache)
    router.push(`/content/${content.id}`);
  };

  // Show processing states with helpful messages
  const isProcessing =
    content.processing_status === "pending" ||
    content.processing_status === "processing";
  const hasFailed = content.processing_status === "failed";
  const hasMinimalData = !content.title && !content.description;

  // Check if content was added within last 10 minutes
  const isJustAdded = () => {
    const now = new Date();
    const createdAt = new Date(content.created_at);
    const diffMinutes = (now.getTime() - createdAt.getTime()) / (1000 * 60);
    return diffMinutes < 10;
  };

  return (
    <div
      id={id}
      onClick={handleContainerClick}
      className={`group py-8 px-4 border-b border-dashed border-[var(--color-border-subtle)] last:border-b-0 transition-all duration-300 cursor-pointer relative
        ${
          isSelected
            ? "bg-[var(--color-bg-secondary)] border-l-4 border-l-[var(--color-accent)] pl-3 -ml-1 shadow-sm"
            : "hover:bg-[var(--color-bg-secondary)]"
        }
        ${isArchiving ? "bg-[var(--color-bg-tertiary)]" : ""}
      `}
    >
      {/* Retro Archiving Overlay */}
      {(isArchiving || isRemoving) && (
        <div className="absolute inset-0 flex items-center justify-center z-20 bg-[var(--color-bg-primary)]/90 font-mono text-sm text-[var(--color-accent)]">
          <span className="animate-pulse">
            {isRemoving ? "Removing..." : "Archiving..."}
          </span>
          <span className="inline-block w-2.5 h-4 bg-[var(--color-accent)] ml-1 animate-pulse"></span>
        </div>
      )}
      <div className="flex items-start gap-4">
        {/* Left side: Content info */}
        <div
          className="flex-1 min-w-0"
          style={{
            paddingRight: "30px",
          }}
        >
          {isProcessing && (
            <div className="flex items-center gap-2 mb-2">
              <RetroLoader
                text={
                  content.processing_status === "pending"
                    ? "Finding your article"
                    : "Preparing your article"
                }
                className="text-xs text-[var(--color-text-muted)] italic"
              />
            </div>
          )}

          {/* Metadata: status, date, reading time */}
          {!isProcessing && (
            <div className="flex items-center gap-3 mb-2 text-xs text-[var(--color-text-muted)]">
              {hasFailed ? (
                <>
                  <span className="inline-block w-2 h-2 rounded-full bg-red-500 flex-shrink-0"></span>
                  <span className="text-xs px-2 py-0.5 bg-red-50 dark:bg-red-950/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
                    Failed to extract
                  </span>
                </>
              ) : (
                <StatusIndicator readingStatus={content.reading_status} />
              )}
              <span className="tracking-wide">
                {isJustAdded() ? "Just now" : formatDate(content.created_at)}
              </span>
              {content.reading_time_minutes && (
                <>
                  <span>·</span>
                  <span>{content.reading_time_minutes} min read</span>
                </>
              )}
              {user?.is_public && content.is_public && (
                <>
                  <span>·</span>
                  <span
                    className="inline-flex items-center gap-1 text-[var(--color-accent)]"
                    title="Publicly visible on your profile"
                  >
                    <svg
                      className="w-3 h-3"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                      />
                    </svg>
                    Public
                  </span>
                </>
              )}
            </div>
          )}

          {/* Title - clickable, links to reader view */}
          <Link
            href={navigateTo || `/content/${content.id}`}
            className="block mb-2"
            onClick={() => {
              if (!navigateTo) {
                sessionStorage.setItem(
                  "contentListScrollPos",
                  window.scrollY.toString(),
                );
                if (returnPath) {
                  sessionStorage.setItem("readerReturnPath", returnPath);
                }
              }
            }}
          >
            <h3 className="font-serif text-2xl font-normal text-[var(--color-text-primary)] group-hover:text-[var(--color-accent)] transition-colors leading-tight">
              {(() => {
                // Show helpful title based on state
                if (isProcessing && hasMinimalData) {
                  return null;
                }
                if (content.title) {
                  return content.title;
                }
                if (hasFailed) {
                  return "We couldn't load your article";
                }
                return "Untitled";
              })()}
            </h3>
          </Link>

          {/* Description */}
          {content.description ? (
            <p className="content-item-description text-sm text-[var(--color-text-muted)] line-clamp-2 mb-2">
              {content.description}
            </p>
          ) : isProcessing ? null : hasFailed ? (
            <p className="text-sm text-[var(--color-text-muted)] line-clamp-2 mb-3 leading-relaxed">
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

          {/* Tags - display and edit */}
          {(() => {
            const allTags = Array.from(
              new Set([...(content.tags || []), ...(content.auto_tags || [])]),
            );

            if (allTags.length === 0 && !isEditingTags) return null;

            return (
              <div className="content-item-tags mb-3 flex items-center gap-2 flex-wrap">
                {allTags.map((tag, index) => (
                  <span
                    key={index}
                    className="text-xs text-[var(--color-text-muted)] border-b border-[var(--color-border)] pb-0.5 flex items-center gap-1 cursor-default"
                  >
                    {tag}
                    {isEditingTags && (
                      <button
                        onClick={() => handleRemoveTag(tag)}
                        className="text-[var(--color-accent)] hover:text-[var(--color-accent-hover)] ml-1 cursor-pointer"
                      >
                        ×
                      </button>
                    )}
                  </span>
                ))}

                {/* Tag Input - only show when editing */}
                {isEditingTags && (
                  <div className="flex items-center gap-1">
                    <div className="relative">
                      <input
                        type="text"
                        value={tagInput}
                        onChange={(e) => {
                          setTagInput(e.target.value);
                          setShowSuggestions(true);
                        }}
                        onFocus={() => setShowSuggestions(true)}
                        onBlur={() =>
                          setTimeout(() => setShowSuggestions(false), 200)
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddTag();
                            setShowSuggestions(false);
                          }
                        }}
                        placeholder="Add tag..."
                        className="px-2 py-0.5 text-xs border border-[var(--color-border)] bg-transparent focus:outline-none focus:!ring-0"
                        autoFocus
                      />
                      {showSuggestions && tagInput.length > 0 && (
                        <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-sm z-10">
                          {availableTags
                            .filter(
                              (tag) =>
                                tag
                                  .toLowerCase()
                                  .includes(tagInput.toLowerCase()) &&
                                !allTags.includes(tag),
                            )
                            .slice(0, 5)
                            .map((tag) => (
                              <button
                                key={tag}
                                onClick={() => {
                                  const newTags = [
                                    ...(content.tags || []),
                                    tag,
                                  ];
                                  handleUpdateTags(newTags);
                                  setTagInput("");
                                  setShowSuggestions(false);
                                }}
                                className="w-full text-left px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)] border-b border-[var(--color-border)] last:border-b-0"
                              >
                                {tag}
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => {
                        handleAddTag();
                        setShowSuggestions(false);
                      }}
                      className="text-xs px-2 py-0.5 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-text-primary)] hover:text-[var(--color-text-primary)] transition-colors"
                    >
                      Add
                    </button>
                    <button
                      onClick={() => {
                        setIsEditingTags(false);
                        setTagInput("");
                        setShowSuggestions(false);
                      }}
                      className="text-xs px-2 py-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                    >
                      Done
                    </button>
                  </div>
                )}
              </div>
            );
          })()}

          {/* Action buttons - Desktop: appear on hover, Mobile: three-dot menu */}
          {!readOnly && (
            <div className="flex items-center gap-2">
              {/* Mobile: Three-dot menu (always visible on small screens) */}
              <div className="sm:hidden">
                <MobileActionsMenu
                  onRead={() =>
                    onStatusChange(content.id, { is_read: !content.is_read })
                  }
                  onArchive={() =>
                    handleStatusChange(null, {
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

              {/* Desktop: Hover actions (hidden on mobile) */}
              <div className="hidden sm:flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity flex-wrap">
                {/* Mark as read/unread */}
                <button
                  onClick={(e) =>
                    handleStatusChange(e, { is_read: !content.is_read })
                  }
                  className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                  title={content.is_read ? "Mark as unread" : "Mark as read"}
                >
                  {content.is_read ? "Unread" : "Read"}
                </button>

                {/* Archive/Unarchive */}
                <button
                  onClick={(e) =>
                    handleStatusChange(e, {
                      is_archived: !content.is_archived,
                    })
                  }
                  className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                  title={content.is_archived ? "Unarchive" : "Archive"}
                >
                  {content.is_archived ? "Unarchive" : "Archive"}
                </button>

                {/* Add to list - with dropdown */}
                {availableLists && availableLists.length > 0 && onAddToList && (
                  <div className="relative">
                    <button
                      onClick={() => setShowListDropdown(!showListDropdown)}
                      className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                      title="Add to list"
                    >
                      + List
                    </button>

                    {/* List dropdown */}
                    {showListDropdown && (
                      <>
                        <div
                          className="fixed inset-0 z-10"
                          onClick={() => setShowListDropdown(false)}
                        />
                        <div className="absolute right-0 top-full mt-1 w-48 bg-[var(--color-bg-primary)] border border-[var(--color-border)] z-20 max-h-52 overflow-y-auto">
                          <p className="font-mono text-[9px] uppercase tracking-widest text-[var(--color-text-faint)] px-3 pt-2.5 pb-1.5 border-b border-[var(--color-border-subtle)]">
                            Add to list
                          </p>
                          {availableLists.map((list) => (
                            <button
                              key={list.id}
                              onClick={() => {
                                onAddToList(list.id);
                                setShowListDropdown(false);
                              }}
                              className="w-full text-left px-3 py-2 font-mono text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-colors border-b border-[var(--color-border-subtle)] last:border-0"
                            >
                              {list.name}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* Add Tag button */}
                <button
                  onClick={() => setIsEditingTags(true)}
                  className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
                  title="Add tag"
                >
                  + Tag
                </button>

                {/* Remove from list (replaces Delete button in list context) */}
                {onRemoveFromList ? (
                  <button
                    onClick={onRemoveFromList}
                    className="text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-rose-500 dark:text-red-400 border border-[var(--color-border)] hover:bg-rose-50 dark:hover:bg-red-900/20 transition-colors"
                    title="Remove from list"
                  >
                    Remove
                  </button>
                ) : (
                  /* Delete button — inline confirm (only show if not in list context) */
                  <button
                    onClick={() => {
                      if (!confirmDelete) {
                        setConfirmDelete(true);
                        return;
                      }
                      setConfirmDelete(false);
                      onDelete(content.id);
                    }}
                    className={`text-xs px-2 py-1 rounded-none border transition-colors ${
                      confirmDelete
                        ? "border-red-400 text-red-500 dark:text-red-400"
                        : "bg-[var(--color-bg-secondary)] text-rose-500 dark:text-red-400 border-[var(--color-border)] hover:bg-rose-50 dark:hover:bg-red-900/20"
                    }`}
                    title="Delete article"
                  >
                    {confirmDelete ? "Confirm?" : "Delete"}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right side: Thumbnail (if available) - right-aligned, smaller */}
        {content.thumbnail_url && (
          <div className="content-item-thumbnail flex-shrink-0 hidden sm:block pl-6">
            <div
              className="w-24 h-24 bg-[var(--color-bg-secondary)]"
              style={{
                marginTop: "40px",
              }}
            >
              <img
                src={content.thumbnail_url}
                alt={content.title || "thumbnail"}
                className="w-full h-full object-cover opacity-90 hover:opacity-100 transition-opacity"
                style={{
                  width: "100px",
                  height: "100px",
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Inline delete confirm strip (for mobile menu trigger) */}
      {confirmDelete && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-red-200 dark:border-red-800/40 sm:hidden">
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
  );
}
