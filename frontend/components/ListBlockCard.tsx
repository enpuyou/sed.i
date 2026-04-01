"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { listsAPI } from "@/lib/api";
import { ContentItem as ContentItemType } from "@/types";

interface ListBlockCardProps {
  id: string;
  name: string;
  description: string | null;
  contentCount: number;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function ListBlockCard({
  id,
  name,
  description,
  contentCount,
  onEdit,
  onDelete,
}: ListBlockCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [preview, setPreview] = useState<ContentItemType[]>([]);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Load preview content on hover
  useEffect(() => {
    if (isHovered && preview.length === 0 && contentCount > 0) {
      const loadPreview = async () => {
        try {
          setLoadingPreview(true);
          const data = await listsAPI.getContent(id);
          setPreview(data.slice(0, 5));
        } catch (err) {
          console.error("Failed to load list preview:", err);
        } finally {
          setLoadingPreview(false);
        }
      };

      loadPreview();
    }
  }, [isHovered, id, preview.length, contentCount]);

  return (
    <Link href={`/lists/${id}`}>
      <div
        className="relative aspect-square border border-[var(--color-border)] rounded-none bg-transparent hover:border-[var(--color-accent)] transition-all cursor-pointer overflow-hidden group"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {/* Base Content */}
        <div className="p-6 h-full flex flex-col justify-between items-center text-center">
          <div className="flex-1 w-full flex flex-col justify-center">
            <h3 className="font-serif text-xl font-normal text-[var(--color-text-primary)] line-clamp-2 group-hover:text-[var(--color-accent)] transition-colors">
              {name}
            </h3>
            {description && (
              <p className="font-sans text-sm text-[var(--color-text-muted)] mt-2 line-clamp-2">
                {description}
              </p>
            )}
          </div>

          <div className="flex flex-col items-center gap-2 mt-4 w-full border-t border-dashed border-[var(--color-border-subtle)] pt-4">
            <span className="text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
              {contentCount} {contentCount === 1 ? "item" : "items"}
            </span>
          </div>
        </div>

        {/* Hover Preview Overlay */}
        {isHovered && (
          <div className="absolute inset-0 bg-[var(--color-bg-primary)] bg-opacity-95 p-4 flex flex-col overflow-hidden">
            <div className="flex-1 min-h-0 overflow-y-auto">
              {loadingPreview ? (
                <div className="h-full flex items-center justify-center text-center text-[var(--color-text-muted)]">
                  Loading...
                </div>
              ) : preview.length > 0 ? (
                <div className="space-y-2">
                  {preview.map((item) => (
                    <div
                      key={item.id}
                      className="p-2 rounded-none bg-[var(--color-bg-secondary)] border border-[var(--color-border-subtle)]"
                    >
                      <p className="text-xs font-medium text-[var(--color-text-primary)] line-clamp-1">
                        {item.title || "Untitled"}
                      </p>
                      <p className="text-xs text-[var(--color-text-muted)] mt-1">
                        {item.reading_time_minutes}
                        {item.reading_time_minutes ? " min" : ""}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-center text-[var(--color-text-muted)]">
                  No content yet
                </div>
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex gap-2 mt-3 pt-3 border-t border-[var(--color-border-subtle)]">
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  onEdit(id);
                }}
                className="flex-1 text-xs px-2 py-1 rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors"
              >
                Edit
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  if (!confirmDelete) {
                    setConfirmDelete(true);
                    return;
                  }
                  setConfirmDelete(false);
                  onDelete(id);
                }}
                className={`flex-1 text-xs px-2 py-1 rounded-none transition-colors ${
                  confirmDelete
                    ? "border border-red-400 text-red-500 dark:text-red-400"
                    : "bg-rose-50 dark:bg-red-900/30 text-rose-500 dark:text-red-400 hover:bg-red-50 hover:text-red-400 dark:hover:bg-red-900/50"
                }`}
              >
                {confirmDelete ? "Confirm?" : "Delete"}
              </button>
            </div>
          </div>
        )}
      </div>
    </Link>
  );
}
