"use client";

import { useState, Suspense } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import ThemeToggle from "@/components/ThemeToggle";
import SediLogo from "@/components/SediLogo";
import NowPlaying from "@/components/NowPlaying";
import InlineError from "@/components/InlineError";

function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const justRegistered = searchParams.get("registered") === "true";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);

    try {
      await login(email, password);
      router.push("/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-xs">
      {/* Header */}
      <div className="text-center mb-10">
        <h1
          className="text-3xl font-normal text-[var(--color-text-primary)]"
          style={{
            fontFamily: "var(--font-logo), Georgia, serif",
            letterSpacing: "-0.02em",
          }}
        >
          Log in
        </h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {justRegistered && (
          <p className="font-mono text-[10px] text-center text-[var(--color-accent)] tracking-wider uppercase">
            Account created — sign in below
          </p>
        )}

        {error && (
          <InlineError
            message={error}
            onDismiss={() => setError("")}
            className="py-1"
          />
        )}

        <input
          id="email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          className="block w-full px-3 py-2 text-sm font-mono border-b border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] placeholder:text-[var(--color-text-faint)] text-[var(--color-text-primary)] transition-colors"
        />

        <input
          id="password"
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="block w-full px-3 py-2 text-sm font-mono border-b border-[var(--color-border)] bg-transparent focus:outline-none focus:border-[var(--color-accent)] placeholder:text-[var(--color-text-faint)] text-[var(--color-text-primary)] transition-colors"
        />

        <div className="pt-4 flex justify-center">
          <button
            type="submit"
            disabled={isLoading}
            className="compact-touch text-xs px-3 py-1 leading-none rounded-none bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
          >
            {isLoading ? "Signing in..." : "Sign in"}
          </button>
        </div>
      </form>

      {/* Footer link */}
      <div className="mt-8 flex flex-col items-center gap-3">
        <Link
          href="/forgot-password"
          className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          Forgot Password?
        </Link>
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
          No account?{" "}
          <Link
            href="/register"
            className="compact-touch text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
            style={{ color: "var(--color-text-muted)" }}
          >
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
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

      <Suspense fallback={<div>Loading...</div>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
