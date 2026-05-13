"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import api from "@/lib/api";
import InlineError from "@/components/InlineError";
import { CircleToggle } from "./CircleToggle";

export default function PublicProfileSection() {
  const { user, mutate } = useAuth();
  const [username, setUsername] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [isQueuePublic, setIsQueuePublic] = useState(false);
  const [isCratesPublic, setIsCratesPublic] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "saved" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (user) {
      setUsername(user.username || "");
      setIsPublic(user.is_public || false);
      setIsQueuePublic(user.is_queue_public || false);
      setIsCratesPublic(user.is_crates_public || false);
    }
  }, [user]);

  const hasChanges =
    username !== (user?.username || "") ||
    isPublic !== (user?.is_public || false) ||
    isQueuePublic !== (user?.is_queue_public || false) ||
    isCratesPublic !== (user?.is_crates_public || false);

  const handleSave = async () => {
    if (!user) return;
    setIsSaving(true);
    setStatus("idle");
    setErrorMsg(null);

    const usernameRegex = /^[a-z0-9_]{3,20}$/;
    if (username && !usernameRegex.test(username)) {
      setErrorMsg(
        "Username must be 3–20 characters: lowercase letters, numbers, underscores.",
      );
      setStatus("error");
      setIsSaving(false);
      return;
    }

    try {
      await api.put("/auth/me", {
        username: username || null,
        is_public: isPublic,
        is_queue_public: isQueuePublic,
        is_crates_public: isCratesPublic,
      });
      await mutate();
      setStatus("saved");
      setTimeout(() => setStatus("idle"), 2500);
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } } };
      setErrorMsg(
        e.response?.data?.detail || "Couldn't save profile. Try again.",
      );
      setStatus("error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCopy = () => {
    if (!username) return;
    navigator.clipboard
      .writeText(`${window.location.origin}/${username}`)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      });
  };

  if (!user) return null;

  return (
    <div className="space-y-4">
      <div>
        <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text-faint)] mb-2">
          Username
        </div>
        <div className="flex border border-[var(--color-border)]">
          <span className="px-3 py-2.5 font-mono text-xs text-[var(--color-text-faint)] bg-[var(--color-bg-secondary)] border-r border-[var(--color-border)] whitespace-nowrap select-none">
            read-sedi.com/
          </span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value.toLowerCase())}
            placeholder="username"
            spellCheck={false}
            autoComplete="off"
            className="flex-1 min-w-0 bg-transparent px-3 py-2.5 font-mono text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none"
          />
          {username && (
            <button
              onClick={handleCopy}
              className="px-3 py-2.5 font-mono text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] border-l border-[var(--color-border)] transition-colors whitespace-nowrap"
            >
              {copied ? "✓" : "Copy"}
            </button>
          )}
        </div>
        <p className="mt-1.5 font-mono text-xs text-[var(--color-text-faint)]">
          3–20 characters, lowercase letters, numbers, underscores.
        </p>
      </div>

      <div className="border-t border-[var(--color-border-subtle)] pt-4">
        <CircleToggle
          checked={isPublic}
          onChange={() => setIsPublic((v) => !v)}
          label="Enable public profile"
          description="Claim your URL and allow access to enabled sections"
        />
        {isPublic && (
          <div className="pl-4 border-l border-[var(--color-border-subtle)] ml-2 mt-2 space-y-1">
            <CircleToggle
              checked={isQueuePublic}
              onChange={() => setIsQueuePublic((v) => !v)}
              label="Queue visible"
              description="Your reading queue is visible at your public URL. Items are private unless individually marked public."
            />
            <CircleToggle
              checked={isCratesPublic}
              onChange={() => setIsCratesPublic((v) => !v)}
              label="Crates visible"
              description="Your full vinyl collection is visible at your public URL."
            />
          </div>
        )}
      </div>

      {status === "error" && errorMsg && (
        <InlineError
          message={errorMsg}
          onDismiss={() => setStatus("idle")}
          className="py-1.5"
        />
      )}

      <div className="flex items-center justify-end gap-4 pt-2 border-t border-[var(--color-border-subtle)]">
        {status === "saved" && (
          <span className="font-mono text-xs text-[var(--color-accent)]">
            Saved.
          </span>
        )}
        {isPublic && username && (
          <Link
            href={`/${username}`}
            target="_blank"
            className="font-mono text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            Preview ↗
          </Link>
        )}
        <button
          onClick={handleSave}
          disabled={isSaving || !hasChanges}
          className="font-mono text-xs uppercase tracking-widest px-5 py-1.5 border border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isSaving ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
