"use client";

import { useEffect, useState, useCallback } from "react";
import { contentAPI } from "@/lib/api";
import { ContentItem } from "@/types";
import ContentCard from "./ContentCard";
import InlineError from "./InlineError";
import EmptyState from "./EmptyState";

interface RecommendedSectionProps {
  mood?: string;
}

export default function RecommendedSection({ mood }: RecommendedSectionProps) {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRecommended = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await contentAPI.getRecommended(0, 10, mood);
      setItems(data.items || []);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Couldn't load recommendations. Try again.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [mood]);

  useEffect(() => {
    fetchRecommended();
  }, [mood, fetchRecommended]);

  const handleStatusChange = () => {
    // Refresh recommendations when status changes
    fetchRecommended();
  };

  const handleDelete = (id: string) => {
    // Remove deleted item from list
    setItems((prev) => prev.filter((item) => item.id !== id));
  };

  const handleUpdate = (updatedContent: ContentItem) => {
    // Update item in list
    setItems((prev) =>
      prev.map((item) =>
        item.id === updatedContent.id ? updatedContent : item,
      ),
    );
  };

  if (loading) {
    return (
      <div className="text-center py-8 text-[var(--color-text-muted)]">
        Loading recommendations...
      </div>
    );
  }

  if (error) {
    return (
      <InlineError
        message={error}
        onRetry={fetchRecommended}
        className="py-4"
      />
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        message="No recommendations yet."
        description="Read some articles to get personalized suggestions."
        className="py-8"
      />
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">
        Similar to articles you've enjoyed
      </p>
      {items.map((item) => (
        <ContentCard
          key={item.id}
          content={item}
          onStatusChange={handleStatusChange}
          onDelete={handleDelete}
          onUpdate={handleUpdate}
        />
      ))}
    </div>
  );
}
