"use client";

import { useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { authAPI } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";
import SediLogo from "@/components/SediLogo";
import NowPlaying from "@/components/NowPlaying";
import InlineError from "@/components/InlineError";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const router = useRouter();

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!token) {
      setError("Invalid or missing reset token.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setIsLoading(true);
    try {
      await authAPI.resetPassword(token, newPassword);
      setSuccess(true);
      setTimeout(() => router.push("/login"), 3000);
    } catch (err: unknown) {
      const e = err as { message?: string };
      setError(e.message || "Failed to reset password.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-xs">
      <div className="text-center mb-10">
        <h1
          className="text-3xl font-normal text-[var(--color-text-primary)]"
          style={{
            fontFamily: "var(--font-logo), Georgia, serif",
            letterSpacing: "-0.02em",
          }}
        >
          New password
        </h1>
      </div>

      {success ? (
        <div className="space-y-6 text-center">
          <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-accent)]">
            Password updated
          </p>
          <p className="font-mono text-[11px] text-[var(--color-text-muted)]">
            Redirecting to login…
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <InlineError
              message={error}
              onDismiss={() => setError("")}
              className="py-1"
            />
          )}

          <input
            type="password"
            required
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="New password"
            className="block w-full px-3 py-2 text-sm font-mono border-b border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] placeholder:text-[var(--color-text-faint)] text-[var(--color-text-primary)] transition-colors"
          />

          <input
            type="password"
            required
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="Confirm password"
            className="block w-full px-3 py-2 text-sm font-mono border-b border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] placeholder:text-[var(--color-text-faint)] text-[var(--color-text-primary)] transition-colors"
          />

          <div className="pt-4 flex justify-center">
            <button
              type="submit"
              disabled={isLoading || !newPassword || !confirmPassword || !token}
              className="compact-touch text-xs px-3 py-1 leading-none rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
            >
              {isLoading ? "Saving..." : "Set new password"}
            </button>
          </div>

          <div className="pt-2 flex justify-center">
            <Link
              href="/login"
              className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
            >
              ← Back to login
            </Link>
          </div>
        </form>
      )}
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--color-bg-primary)] px-6">
      {/* Top bar */}
      <div className="fixed top-0 left-0 right-0 z-50 flex items-center justify-between px-6 h-14">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="compact-touch flex items-center gap-2 no-underline hover:opacity-80 transition-opacity"
            style={{ color: "var(--color-text-primary)" }}
          >
            <SediLogo size={16} className="text-[var(--color-text-primary)]" />
            <span
              className="text-lg font-normal"
              style={{ fontFamily: "var(--font-logo)" }}
            >
              sed.i
            </span>
          </Link>
          <div className="hidden md:block">
            <NowPlaying />
          </div>
        </div>
        <ThemeToggle />
      </div>

      <Suspense fallback={null}>
        <ResetPasswordForm />
      </Suspense>
    </div>
  );
}
