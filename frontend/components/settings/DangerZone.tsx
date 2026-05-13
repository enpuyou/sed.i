"use client";

import { useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import api, { APIError } from "@/lib/api";
import InlineError from "@/components/InlineError";

export default function DangerZone() {
  const { logout } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setIsDeleting(true);
    setError(null);
    try {
      await api.delete("/auth/me", { password });
      logout();
      router.push("/");
    } catch (err) {
      setError(
        err instanceof APIError
          ? err.detail
          : "Couldn't delete account. Try again.",
      );
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="border border-red-500/20 px-5 py-4">
      {!open ? (
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="font-mono text-xs uppercase tracking-widest text-red-500/70 mb-1">
              Delete Account
            </div>
            <div className="font-mono text-xs text-[var(--color-text-faint)] leading-relaxed">
              Permanently removes your account and all data. This cannot be
              undone.
            </div>
          </div>
          <button
            onClick={() => setOpen(true)}
            className="font-mono text-xs uppercase tracking-widest px-3 py-2 border border-red-500/30 text-red-500/70 hover:border-red-500 hover:text-red-500 transition-colors flex-shrink-0"
          >
            Delete →
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="font-mono text-xs uppercase tracking-widest text-red-500 mb-2">
            Confirm deletion
          </div>
          <div className="font-mono text-xs text-[var(--color-text-faint)] mb-3 leading-relaxed">
            Enter your password to permanently delete your account and all
            associated content, highlights, and records.
          </div>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoFocus
            className="w-full bg-transparent border border-[var(--color-border)] px-3 py-2 font-mono text-[11px] text-[var(--color-text-primary)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-red-500/50"
          />
          {error && (
            <InlineError message={error} onDismiss={() => setError(null)} />
          )}
          <div className="flex gap-3">
            <button
              onClick={handleDelete}
              disabled={isDeleting || !password}
              className="font-mono text-xs uppercase tracking-widest px-4 py-2 border border-red-500/50 text-red-500 hover:bg-red-500/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isDeleting ? "Deleting…" : "Confirm Delete"}
            </button>
            <button
              onClick={() => {
                setOpen(false);
                setPassword("");
                setError(null);
              }}
              className="font-mono text-xs uppercase tracking-widest px-4 py-2 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
