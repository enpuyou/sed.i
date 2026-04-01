"use client";

import { useState } from "react";
import { vinylAPI } from "@/lib/api";
import { VinylRecord } from "@/types";
import InlineError from "./InlineError";

interface AddRecordFormProps {
  onRecordAdded: (newRecord: VinylRecord) => void;
}

export default function AddRecordForm({ onRecordAdded }: AddRecordFormProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const newRecord = await vinylAPI.create(url);
      setUrl("");
      onRecordAdded(newRecord);
    } catch (err) {
      const errorMessage =
        err instanceof Error
          ? err.message
          : "Failed to add record. Please try again.";
      setError(errorMessage);
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

      <div className="flex items-center gap-2 group focus-within:opacity-100 opacity-80 transition-opacity duration-300 border-b border-[var(--color-border)] focus-within:border-[var(--color-accent)]">
        <span className="text-[var(--color-text-muted)] font-mono text-lg select-none">
          &gt;
        </span>
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste a Discogs URL..."
          required
          className="flex-1 px-0 py-2 bg-transparent rounded-none focus:outline-none focus:!ring-0 focus:!shadow-none placeholder-[var(--color-text-muted)] transition-all font-mono text-sm border-none"
        />

        <button
          type="submit"
          disabled={loading || !url}
          className="px-2 py-2 text-[var(--color-text-primary)] rounded-full hover:bg-[var(--color-bg-secondary)] focus:outline-none disabled:opacity-30 disabled:cursor-not-allowed transition-all font-mono text-xs"
          title="Add to Crates"
        >
          {loading ? (
            <span className="inline-block animate-pulse">&#x25d0;</span>
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
