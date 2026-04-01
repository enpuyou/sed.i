"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import api from "@/lib/api";
import InlineError from "./InlineError";

function Toggle({
  checked,
  onChange,
  size = "md",
}: {
  checked: boolean;
  onChange: () => void;
  size?: "sm" | "md";
}) {
  const track = size === "sm" ? "h-5 w-9" : "h-6 w-11";
  const thumb = size === "sm" ? "h-4 w-4" : "h-5 w-5";
  const translate = size === "sm" ? "translate-x-4" : "translate-x-5";
  return (
    <button
      onClick={onChange}
      className={`relative inline-flex flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${track} ${
        checked ? "bg-[var(--color-accent)]" : "bg-[var(--color-border)]"
      }`}
      role="switch"
      aria-checked={checked}
    >
      <span
        className={`pointer-events-none inline-block transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${thumb} ${
          checked ? translate : "translate-x-0"
        }`}
      />
    </button>
  );
}

export default function ProfileSettings() {
  const { user, mutate } = useAuth();

  const [username, setUsername] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [isQueuePublic, setIsQueuePublic] = useState(false);
  const [isCratesPublic, setIsCratesPublic] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "saved" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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

  const handleUpdate = async () => {
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
        e.response?.data?.detail || "Could not save profile settings.",
      );
      setStatus("error");
    } finally {
      setIsSaving(false);
    }
  };

  if (!user) return null;

  return (
    <div className="space-y-6">
      <div className="text-xs font-mono uppercase tracking-widest text-[var(--color-text-muted)] mb-4">
        Public Profile
      </div>

      {status === "error" && errorMsg && (
        <InlineError message={errorMsg} className="py-1.5" />
      )}

      <div className="space-y-6 max-w-xl font-mono">
        {/* Username */}
        <div>
          <label className="block text-xs text-[var(--color-text-muted)] mb-2">
            Username
          </label>
          <div className="flex border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
            <button
              onClick={() => {
                if (username) {
                  navigator.clipboard.writeText(
                    `${window.location.origin}/${username}`,
                  );
                }
              }}
              className="flex items-center px-4 border-r border-[var(--color-border)] text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)] select-none hover:text-[var(--color-text-primary)] transition-colors cursor-pointer text-xs"
              title="Copy profile URL"
            >
              <svg
                className="w-3.5 h-3.5 mr-1.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </svg>
              read-sedi.com/
            </button>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
              placeholder="username"
              className="flex-1 bg-transparent border-none px-4 py-2 focus:ring-0 text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] text-sm"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <p className="mt-1.5 text-xs text-[var(--color-text-muted)]">
            3–20 characters, lowercase letters, numbers, underscores.
          </p>
        </div>

        {/* Master toggle */}
        <div className="flex items-center justify-between p-4 border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
          <div>
            <div className="text-sm text-[var(--color-text-primary)]">
              Enable Public Profile
            </div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1">
              Claim your URL and allow access to the sections you enable below.
            </div>
          </div>
          <Toggle checked={isPublic} onChange={() => setIsPublic((v) => !v)} />
        </div>

        {/* Section toggles */}
        {isPublic && (
          <div className="space-y-3 pl-4 border-l-2 border-[var(--color-border-subtle)] ml-2">
            <div className="flex items-center justify-between p-3 border border-[var(--color-border-subtle)] bg-[var(--color-bg-primary)]">
              <div>
                <div className="text-sm text-[var(--color-text-primary)]">
                  Queue Visibility
                </div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">
                  Show items you have individually marked as public.
                </div>
              </div>
              <Toggle
                checked={isQueuePublic}
                onChange={() => setIsQueuePublic((v) => !v)}
                size="sm"
              />
            </div>

            <div className="flex items-center justify-between p-3 border border-[var(--color-border-subtle)] bg-[var(--color-bg-primary)]">
              <div>
                <div className="text-sm text-[var(--color-text-primary)]">
                  Crates Visibility
                </div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">
                  Show your entire vinyl collection to the public.
                </div>
              </div>
              <Toggle
                checked={isCratesPublic}
                onChange={() => setIsCratesPublic((v) => !v)}
                size="sm"
              />
            </div>
          </div>
        )}
      </div>

      {/* Actions row */}
      <div className="flex items-center justify-end gap-4 pt-2">
        {status === "saved" && (
          <span className="font-mono text-xs text-[var(--color-accent)] transition-opacity">
            Saved.
          </span>
        )}
        {isPublic && username && (
          <Link
            href={`/${username}`}
            target="_blank"
            className="font-mono text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            Preview ↗
          </Link>
        )}
        <button
          onClick={handleUpdate}
          disabled={isSaving || !hasChanges}
          className="text-xs px-6 py-2 border border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed font-mono uppercase tracking-widest bg-[var(--color-bg-primary)]"
        >
          {isSaving ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
