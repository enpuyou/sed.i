"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { authAPI } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";
import SediLogo from "@/components/SediLogo";
import NowPlaying from "@/components/NowPlaying";
import InlineError from "@/components/InlineError";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");

  const [status, setStatus] = useState<"loading" | "success" | "error">(
    "loading",
  );
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("No verification token found.");
      return;
    }
    authAPI
      .verifyEmail(token)
      .then(() => {
        setStatus("success");
        setMessage("Email verified.");
        setTimeout(() => router.push("/login"), 3000);
      })
      .catch((err: unknown) => {
        const e = err as { message?: string };
        setStatus("error");
        setMessage(e.message || "The link may have expired.");
      });
  }, [token, router]);

  return (
    <div className="w-full max-w-xs text-center">
      <div className="mb-10">
        <h1
          className="text-3xl font-normal text-[var(--color-text-primary)]"
          style={{
            fontFamily: "var(--font-logo), Georgia, serif",
            letterSpacing: "-0.02em",
          }}
        >
          {status === "loading"
            ? "Verifying…"
            : status === "success"
              ? "Verified"
              : "Failed"}
        </h1>
      </div>

      {status === "loading" && (
        <p className="font-mono text-[11px] text-[var(--color-text-faint)] animate-pulse">
          Verifying your email…
        </p>
      )}

      {status === "success" && (
        <div className="space-y-4">
          <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-accent)]">
            {message}
          </p>
          <p className="font-mono text-[11px] text-[var(--color-text-muted)]">
            Redirecting to login…
          </p>
        </div>
      )}

      {status === "error" && (
        <div className="space-y-6">
          <InlineError message={message} className="py-1" />
          <Link
            href="/login"
            className="block font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            ← Back to login
          </Link>
        </div>
      )}
    </div>
  );
}

export default function VerifyEmailPage() {
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
        <VerifyEmailContent />
      </Suspense>
    </div>
  );
}
