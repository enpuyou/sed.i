"use client";

import { useState } from "react";
import { contentAPI } from "@/lib/api";
import { useRouter } from "next/navigation";
import InlineError from "./InlineError";

export default function PickForMe() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const handlePickForMe = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await contentAPI.getRecommended(0, 1);

      if (!data.items || data.items.length === 0) {
        setError("No recommendations available. Read some articles first.");
        return;
      }

      const item = data.items[0];
      router.push(`/content/${item.id}`);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to pick article";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {error && (
        <InlineError
          message={error}
          onDismiss={() => setError(null)}
          className="mb-2 py-1"
        />
      )}
      <button
        onClick={handlePickForMe}
        disabled={loading}
        className="w-full px-4 py-3 rounded font-serif text-[var(--color-accent)] border border-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? "Finding something..." : "Surprise me 🎲"}
      </button>
    </>
  );
}
