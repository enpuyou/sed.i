"use client";

import { useState } from "react";
import Link from "next/link";
import { authAPI } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";
import SediLogo from "@/components/SediLogo";
import NowPlaying from "@/components/NowPlaying";
import InlineError from "@/components/InlineError";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      await authAPI.forgotPassword(email);
      setSent(true);
    } catch (err: unknown) {
      const e = err as { message?: string };
      setError(e.message || "Failed to send reset email.");
    } finally {
      setIsLoading(false);
    }
  };

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

      <div className="w-full max-w-xs">
        <div className="text-center mb-10">
          <h1
            className="text-3xl font-normal text-[var(--color-text-primary)]"
            style={{
              fontFamily: "var(--font-logo), Georgia, serif",
              letterSpacing: "-0.02em",
            }}
          >
            Reset password
          </h1>
        </div>

        {sent ? (
          <div className="space-y-6 text-center">
            <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-accent)]">
              Check your email
            </p>
            <p className="font-mono text-[11px] text-[var(--color-text-muted)]">
              If an account exists for {email}, a reset link has been sent.
            </p>
            <Link
              href="/login"
              className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
            >
              ← Back to login
            </Link>
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
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email"
              className="block w-full px-3 py-2 text-sm font-mono border-b border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] placeholder:text-[var(--color-text-faint)] text-[var(--color-text-primary)] transition-colors"
            />

            <div className="pt-4 flex justify-center">
              <button
                type="submit"
                disabled={isLoading || !email}
                className="compact-touch text-xs px-3 py-1 leading-none rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
              >
                {isLoading ? "Sending..." : "Send reset link"}
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
    </div>
  );
}
