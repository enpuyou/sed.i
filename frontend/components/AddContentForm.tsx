"use client";

import { useState } from "react";
import Link from "next/link";
import { contentAPI } from "@/lib/api";
import { ContentItem } from "@/types";
import InlineError from "./InlineError";

interface AddContentFormProps {
  onContentAdded: (newItem: ContentItem) => void;
}

interface DuplicateInfo {
  id: string;
  isArchived: boolean;
}

export default function AddContentForm({
  onContentAdded,
}: AddContentFormProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [duplicateInfo, setDuplicateInfo] = useState<DuplicateInfo | null>(
    null,
  );

  const handleUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUrl(e.target.value);
    setError("");
    setDuplicateInfo(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setDuplicateInfo(null);

    try {
      const newItem = await contentAPI.create({ url });
      setUrl("");
      onContentAdded(newItem);
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      try {
        const body = JSON.parse(message);
        if (body?.existing_id) {
          setDuplicateInfo({
            id: body.existing_id,
            isArchived: body.is_archived ?? false,
          });
          return;
        }
      } catch {
        // not a structured 409 — fall through to generic error
      }
      setError(message || "Couldn't add link. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      {error && (
        <InlineError
          message={error}
          onDismiss={() => setError("")}
          className="py-1.5"
        />
      )}

      {duplicateInfo && (
        <p className="text-xs font-mono text-[var(--color-text-muted)] py-1">
          Already in your library{duplicateInfo.isArchived ? " (archived)" : ""}
          .{" "}
          <Link
            href={`/content/${duplicateInfo.id}`}
            className="underline text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors"
          >
            View it →
          </Link>
        </p>
      )}

      <div className="flex items-center gap-2 group focus-within:opacity-100 opacity-80 transition-opacity duration-300 border-b border-[var(--color-border)] focus-within:border-[var(--color-accent)]">
        <span className="text-[var(--color-text-muted)] font-mono text-lg select-none">
          &gt;
        </span>
        <input
          type="url"
          id="url"
          value={url}
          onChange={handleUrlChange}
          placeholder="Paste article URL..."
          required
          className="flex-1 px-0 py-2 bg-transparent rounded-none focus:outline-none focus:!ring-0 focus:!shadow-none placeholder-[var(--color-text-muted)] transition-all font-mono text-xs sm:text-sm border-none"
        />

        <button
          type="submit"
          disabled={loading || !url}
          className="px-2 py-2 text-[var(--color-text-primary)] rounded-full hover:bg-[var(--color-bg-secondary)] focus:outline-none disabled:opacity-30 disabled:cursor-not-allowed transition-all font-mono text-xs"
          title="Add to Queue"
        >
          {loading ? (
            <span className="inline-block animate-pulse">▐</span>
          ) : (
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M12 4v16m8-8H4"
              />
            </svg>
          )}
        </button>
      </div>
    </form>
  );
}
