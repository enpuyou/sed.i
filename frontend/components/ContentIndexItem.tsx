"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ContentItem as ContentItemType } from "@/types";

interface ContentIndexItemProps {
  content: ContentItemType;
  onStatusChange: (
    id: string,
    updates: { is_read?: boolean; is_archived?: boolean; is_public?: boolean },
  ) => void;
  onDelete: (id: string) => void;
  onUpdate?: (updatedContent: ContentItemType) => void;
  onRemoveFromList?: () => void;
  availableLists?: Array<{ id: string; name: string }>;
  onAddToList?: (listId: string) => void;
  returnPath?: string;
  isSelected?: boolean;
  id?: string;
  isRemoving?: boolean;
  readOnly?: boolean;
  navigateTo?: string;
}

export default function ContentIndexItem({
  content,
  onStatusChange: _onStatusChange,
  onDelete,
  onRemoveFromList,
  returnPath,
  isSelected,
  id,
  isRemoving = false,
  readOnly = false,
  navigateTo,
}: ContentIndexItemProps) {
  const [mounted, setMounted] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const router = useRouter();

  useEffect(() => {
    setMounted(true);
  }, []);

  const formatDate = (dateString: string) => {
    if (!mounted) {
      return new Date(dateString).toISOString().split("T")[0];
    }
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";

    const isCurrentYear = date.getFullYear() === now.getFullYear();
    const options: Intl.DateTimeFormatOptions = {
      month: "short",
      day: "numeric",
      year: isCurrentYear ? undefined : "numeric",
    };
    return date.toLocaleDateString("en-US", options);
  };

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirmDelete) {
      onDelete(content.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
    }
  };

  const handleContainerClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("button") || target.closest("a")) return;

    if (navigateTo) {
      router.push(navigateTo);
      return;
    }
    sessionStorage.setItem("contentListScrollPos", window.scrollY.toString());
    if (returnPath) sessionStorage.setItem("readerReturnPath", returnPath);
    router.push(`/content/${content.id}`);
  };

  const isProcessing =
    content.processing_status === "pending" ||
    content.processing_status === "processing";
  const hasFailed = content.processing_status === "failed";

  const author = content.author || "";
  const domain = content.original_url
    ? (() => {
        try {
          return new URL(content.original_url).hostname.replace(/^www\./, "");
        } catch {
          return "";
        }
      })()
    : "";

  const titleHref = navigateTo || `/content/${content.id}`;

  return (
    <div
      id={id}
      onClick={handleContainerClick}
      onMouseLeave={() => {
        setShowActions(false);
        setConfirmDelete(false);
      }}
      className={`group py-1 sm:py-2 px-0 transition-colors relative font-serif text-[13px] index-row-grid
        ${isSelected ? "bg-[var(--color-bg-secondary)]" : ""}
        ${isRemoving ? "opacity-50 pointer-events-none" : ""}
        ${isProcessing ? "opacity-70" : ""}
      `}
      style={{
        display: "grid",
        gridTemplateColumns: "var(--index-grid-cols, 3.5rem 1fr 8rem 6rem)",
        gap: "0 1rem",
      }}
    >
      {/* Edit Actions Trigger Icon */}
      {!readOnly && !showActions && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setShowActions(!showActions);
            setConfirmDelete(false);
          }}
          className="hidden sm:block absolute -right-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity p-1 text-[var(--color-text-muted)] hover:text-[var(--color-accent)] z-10"
          title="Actions"
        >
          <svg
            className="w-3 h-3"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
            />
          </svg>
        </button>
      )}

      {/* Date */}
      <div className="flex sm:items-center text-[var(--color-text-primary)] font-mono text-[11px] whitespace-nowrap truncate pt-[3px] sm:pt-0">
        {formatDate(content.created_at)}
      </div>

      {/* Title */}
      <div className="min-w-0 flex sm:items-center overflow-hidden">
        <Link
          href={titleHref}
          className="truncate inline-block max-w-[calc(100%-1.5rem)] sm:max-w-full leading-tight !text-[var(--color-text-primary)] hover:!text-[var(--color-accent)] transition-colors cursor-pointer sm:border-b border-transparent sm:hover:border-[var(--color-accent)]"
          title={content.title || "Untitled"}
          onClick={() => {
            if (!navigateTo)
              sessionStorage.setItem(
                "contentListScrollPos",
                window.scrollY.toString(),
              );
          }}
        >
          {isProcessing ? (
            <span className="italic text-[var(--color-text-muted)] border-none">
              Processing...
            </span>
          ) : hasFailed ? (
            <span className="text-red-500 italic border-none">
              Extraction failed
            </span>
          ) : (
            content.title || "Untitled"
          )}
        </Link>
      </div>

      {/* Author and Source, or Actions */}
      {showActions ? (
        <div className="col-span-2 flex items-center justify-end gap-2 pr-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              _onStatusChange(content.id, { is_read: !content.is_read });
              setShowActions(false);
            }}
            className="font-sans text-xs px-2 py-0.5 leading-none border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]"
            title={content.is_read ? "Mark as unread" : "Mark as read"}
          >
            {content.is_read ? "Unread" : "Read"}
          </button>

          <button
            onClick={(e) => {
              e.stopPropagation();
              _onStatusChange(content.id, {
                is_archived: !content.is_archived,
              });
              setShowActions(false);
            }}
            className="font-sans text-xs px-2 py-0.5 leading-none border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] hidden sm:block"
            title={content.is_archived ? "Unarchive" : "Archive"}
          >
            {content.is_archived ? "Unarchive" : "Archive"}
          </button>

          {onRemoveFromList ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRemoveFromList();
                setShowActions(false);
              }}
              className="font-sans text-xs px-2 py-0.5 leading-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-rose-500 dark:text-red-400 hover:bg-rose-50 hover:border-red-400 dark:hover:bg-red-900/20 transition-colors"
            >
              Remove
            </button>
          ) : (
            <button
              onClick={handleDeleteClick}
              className={`font-sans text-xs px-2 py-0.5 leading-none border transition-colors cursor-pointer hidden sm:block ${
                confirmDelete
                  ? "bg-red-500 text-rose-400 border-red-300 hover:bg-rose-50"
                  : "border-[var(--color-border)] text-rose-500 dark:text-red-400 bg-[var(--color-bg-secondary)] hover:bg-rose-50 dark:hover:bg-red-900/20"
              }`}
            >
              {confirmDelete ? "Delete?" : "Delete"}
            </button>
          )}
        </div>
      ) : (
        <>
          {/* Author - Hidden on Mobile */}
          <div className="hidden sm:flex items-center truncate text-[var(--color-text-muted)] text-[12px] font-serif pr-2">
            {author}
          </div>

          {/* Source - Hidden on Mobile */}
          <div className="items-center justify-start min-w-0 relative hidden sm:flex">
            <a
              href={content.original_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="truncate font-mono text-[11px] text-[var(--color-text-faint)] hover:text-[var(--color-accent)] transition-colors cursor-pointer"
            >
              {domain}
            </a>
          </div>
        </>
      )}

      {/* Inject Global Override specifically targeting mobile for these cells so the grid handles it gracefully */}
      <style jsx global>{`
        .index-row-grid {
          align-items: baseline;
        }
        @media (max-width: 639px) {
          /* sm breakpoint */
          :root {
            --index-grid-cols: 3.25rem 1fr;
          }
        }
        @media (min-width: 640px) {
          :root {
            --index-grid-cols: 4.5rem 1fr 9rem 7rem;
          }
          .index-row-grid {
            align-items: center;
          }
        }
      `}</style>
    </div>
  );
}
