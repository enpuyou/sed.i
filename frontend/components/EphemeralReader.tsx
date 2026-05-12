"use client";

import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import Reader from "@/components/Reader";
import InlineError from "@/components/InlineError";
import { contentAPI } from "@/lib/api";
import { ContentItem } from "@/types";

interface EphemeralArticle {
  url: string;
  html: string;
  title?: string;
  author?: string;
  description?: string;
  thumbnail?: string;
  publishedDate?: string;
}

interface EphemeralHighlight {
  text: string;
  note?: string;
  start_offset: number;
  end_offset: number;
  color: string;
}

interface Props {
  article: EphemeralArticle;
}

const STORAGE_KEY = "sedi_ephemeral_article";

function buildFakeContentItem(article: EphemeralArticle): ContentItem {
  const now = new Date().toISOString();
  return {
    id: "00000000-0000-0000-0000-000000000000",
    user_id: "00000000-0000-0000-0000-000000000000",
    original_url: article.url,
    title: article.title ?? null,
    description: article.description ?? null,
    thumbnail_url: article.thumbnail ?? null,
    content_type: "article",
    summary: null,
    tags: [],
    auto_tags: [],
    full_text: article.html,
    word_count: null,
    reading_time_minutes: null,
    read_position: 0,
    author: article.author ?? null,
    published_date: article.publishedDate ?? null,
    content_vertical: "general",
    vertical_metadata: null,
    is_read: false,
    is_archived: false,
    is_public: false,
    processing_status: "completed",
    processing_error: null,
    created_at: now,
    updated_at: now,
    reading_status: "unread",
    status: null,
  };
}

function isInIframe() {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

export default function EphemeralReader({ article }: Props) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const inIframe = typeof window !== "undefined" && isInIframe();

  // Collect highlights created during ephemeral reading
  const ephemeralHighlights = useRef<EphemeralHighlight[]>([]);

  const fakeContent = buildFakeContentItem(article);

  const handleStatusChange = useCallback(() => {
    // No-op: ephemeral reader doesn't persist read position or status
  }, []);

  const handleEphemeralHighlight = useCallback(
    (highlight: {
      text: string;
      start_offset: number;
      end_offset: number;
      color: string;
    }) => {
      ephemeralHighlights.current.push(highlight);
    },
    [],
  );

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);

    try {
      const result = await contentAPI.create({
        url: article.url,
        pre_extracted_html: article.html,
        pre_extracted_title: article.title,
        pre_extracted_author: article.author,
        pre_extracted_description: article.description,
        pre_extracted_thumbnail: article.thumbnail,
        pre_extracted_published_date: article.publishedDate,
        initial_highlights:
          ephemeralHighlights.current.length > 0
            ? ephemeralHighlights.current
            : undefined,
      });

      setSaved(true);
      try {
        sessionStorage.removeItem(STORAGE_KEY);
      } catch {}

      // In iframe (overlay mode) we can't navigate — just show confirmation.
      // In standalone tab mode, navigate to the saved article.
      if (!inIframe) {
        setTimeout(() => router.push(`/content/${result.id}`), 800);
      }
    } catch {
      setSaveError("Couldn't save article. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative">
      {/* Ephemeral save banner */}
      <div className="sticky top-0 z-50 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border)] px-4 py-2 flex items-center justify-between gap-4">
        <span className="text-sm text-[var(--color-text-secondary)] font-mono truncate">
          {saved ? "Saved to library" : "Reading without saving"}
        </span>
        <div className="flex items-center gap-3 shrink-0">
          {saveError && <InlineError message={saveError} />}
          {!saved && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-sm bg-[var(--color-accent)] text-white px-4 py-1.5 hover:bg-[var(--color-accent-hover)] disabled:opacity-50 transition-colors"
            >
              {saving ? "Saving..." : "Save to Library"}
            </button>
          )}
        </div>
      </div>

      {/* Reader renders the ephemeral article */}
      <Reader
        content={fakeContent}
        onStatusChange={handleStatusChange}
        onHighlightCreate={handleEphemeralHighlight}
      />
    </div>
  );
}
