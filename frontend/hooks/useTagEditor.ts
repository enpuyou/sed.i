"use client";

import { useState, useEffect } from "react";
import { contentAPI } from "@/lib/api";
import type { ContentItem } from "@/types";

interface UseTagEditorOptions {
  content: ContentItem;
  isEditingTags: boolean;
  onUpdate?: (updated: ContentItem) => void;
}

export function useTagEditor({
  content,
  isEditingTags,
  onUpdate,
}: UseTagEditorOptions) {
  const [tagInput, setTagInput] = useState("");
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);

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

  const handleAddTag = async (explicitTag?: string) => {
    const tag = (explicitTag ?? tagInput).trim();
    if (!tag) return;

    const currentTags = content.tags || [];
    if (currentTags.includes(tag)) return;

    const newTags = [...currentTags, tag];
    if (!explicitTag) setTagInput("");

    try {
      setTagError(null);
      onUpdate?.({ ...content, tags: newTags } as ContentItem);
      const updated = await contentAPI.update(content.id, { tags: newTags });
      onUpdate?.(updated);
    } catch (err) {
      console.error("Failed to add tag:", err);
      onUpdate?.(content);
      setTagError("Couldn't save tags.");
    }
  };

  const handleRemoveTag = async (tagToRemove: string) => {
    const newTags = (content.tags || []).filter((t) => t !== tagToRemove);
    const newAutoTags = (content.auto_tags || []).filter(
      (t) => t !== tagToRemove,
    );

    try {
      setTagError(null);
      onUpdate?.({
        ...content,
        tags: newTags,
        auto_tags: newAutoTags,
      } as ContentItem);
      const updated = await contentAPI.update(content.id, {
        tags: newTags,
        auto_tags: newAutoTags,
      });
      onUpdate?.(updated);
    } catch (err) {
      console.error("Failed to remove tag:", err);
      onUpdate?.(content);
      setTagError("Couldn't save tags.");
    }
  };

  return {
    tagInput,
    setTagInput,
    availableTags,
    showSuggestions,
    setShowSuggestions,
    tagError,
    setTagError,
    handleAddTag,
    handleRemoveTag,
  };
}
