"use client";

import { useState, useEffect, useRef } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import { BionicText } from "@/components/BionicText";
import {
  useReadingSettings,
  ReadingSettings,
} from "@/contexts/ReadingSettingsContext";
import api from "@/lib/api";
import InlineError from "@/components/InlineError";

// ── Setting configs ────────────────────────────────────

const SETTING_CONFIGS: Array<{
  key: keyof ReadingSettings;
  label: string;
  options: Array<{ value: string; label: string }>;
}> = [
  {
    key: "theme",
    label: "Theme",
    options: [
      { value: "light", label: "Light" },
      { value: "dark", label: "Dark" },
      { value: "sepia", label: "Sepia" },
      { value: "true-black", label: "True black" },
    ],
  },
  {
    key: "fontFamily",
    label: "Font",
    options: [
      { value: "serif", label: "Serif" },
      { value: "sans", label: "Sans" },
      { value: "merriweather", label: "Merriweather" },
      { value: "verdana", label: "Verdana" },
      { value: "system", label: "System" },
    ],
  },
  {
    key: "fontSize",
    label: "Size",
    options: [
      { value: "small", label: "Small" },
      { value: "medium", label: "Medium" },
      { value: "large", label: "Large" },
    ],
  },
  {
    key: "contentWidth",
    label: "Width",
    options: [
      { value: "narrow", label: "Narrow" },
      { value: "medium", label: "Medium" },
      { value: "wide", label: "Wide" },
    ],
  },
  {
    key: "lineHeight",
    label: "Line height",
    options: [
      { value: "compact", label: "Compact" },
      { value: "comfortable", label: "Normal" },
      { value: "spacious", label: "Spacious" },
    ],
  },
  {
    key: "letterSpacing",
    label: "Spacing",
    options: [
      { value: "tight", label: "Tight" },
      { value: "normal", label: "Normal" },
      { value: "wide", label: "Wide" },
    ],
  },
  {
    key: "bionicReading",
    label: "Bionic reading",
    options: [
      { value: "false", label: "Off" },
      { value: "true", label: "On" },
    ],
  },
];

const SETTING_ICONS: Record<string, string> = {
  theme: "◐",
  fontFamily: "Aa",
  fontSize: "↕",
  contentWidth: "↔",
  lineHeight: "≡",
  letterSpacing: "⋯",
  bionicReading: "◉",
};

// ── Inline preview ─────────────────────────────────────

const PREVIEW_TEXT = `Reading on the internet is a fragmented, impermanent experience.

A long-form essay in a literary magazine, a research paper on arXiv, a newsletter from a writer they follow, a discussion thread, a blog post from five years ago.

There is no central place to collect this material, no continuity between sessions, and no infrastructure for building cumulative understanding.`;

function PreviewBox() {
  const { settings, hydrated } = useReadingSettings();
  if (!hydrated) return null;

  const themeClass =
    settings.theme === "dark"
      ? "dark"
      : settings.theme === "sepia"
        ? "sepia"
        : settings.theme === "true-black"
          ? "true-black"
          : "";

  const fontClass =
    settings.fontFamily === "serif"
      ? "font-serif-setting"
      : settings.fontFamily === "sans"
        ? "font-sans-setting"
        : settings.fontFamily === "merriweather"
          ? "font-merriweather-setting"
          : settings.fontFamily === "verdana"
            ? "font-verdana-setting"
            : "font-system-setting";

  const sizeClass =
    settings.fontSize === "small"
      ? "text-small-setting"
      : settings.fontSize === "large"
        ? "text-large-setting"
        : "text-medium-setting";

  const lhClass =
    settings.lineHeight === "compact"
      ? "line-height-compact"
      : settings.lineHeight === "spacious"
        ? "line-height-spacious"
        : "line-height-comfortable";

  const lsClass =
    settings.letterSpacing === "tight"
      ? "letter-spacing-tight"
      : settings.letterSpacing === "wide"
        ? "letter-spacing-wide"
        : "letter-spacing-normal";

  const widthClass =
    settings.contentWidth === "narrow"
      ? "max-w-xs"
      : settings.contentWidth === "wide"
        ? "max-w-full"
        : "max-w-sm";

  return (
    <div
      className={`${themeClass} mt-4 border border-[var(--color-border)] overflow-hidden transition-all duration-300`}
      style={{ height: "500px", backgroundColor: "var(--color-bg-primary)" }}
    >
      <div className="flex items-center px-4 py-1.5 border-b border-[var(--color-border-subtle)]">
        <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text-faint)]">
          Preview
        </span>
      </div>
      <div className="h-full overflow-hidden px-5 pt-3 pb-5 flex flex-col items-center justify-center">
        <div className={`${widthClass} w-full`}>
          <div
            className={`${fontClass} ${sizeClass} ${lhClass} ${lsClass} text-[var(--color-text-secondary)] antialiased transition-all duration-200`}
          >
            {PREVIEW_TEXT.split("\n\n").map((para, i) => (
              <p key={i} className="mb-3 last:mb-0">
                {settings.bionicReading ? <BionicText text={para} /> : para}
              </p>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Position track ─────────────────────────────────────

function PositionTrack({ current, total }: { current: number; total: number }) {
  const TRACK_H = 28;
  const THUMB_H = 4;
  const travel = TRACK_H - THUMB_H;
  const pct = total > 1 ? current / (total - 1) : 0;
  const thumbTop = Math.round(pct * travel);

  return (
    <div
      className="flex items-center justify-center flex-shrink-0 w-6"
      style={{ alignSelf: "stretch" }}
      aria-hidden
    >
      <div
        className="relative"
        style={{
          width: 1,
          height: TRACK_H,
          backgroundColor: "var(--color-border)",
        }}
      >
        <div
          className="absolute left-1/2 transition-[top] duration-150"
          style={{
            width: 5,
            height: THUMB_H,
            top: thumbTop,
            transform: "translateX(-50%)",
            backgroundColor: "var(--color-accent)",
          }}
        />
      </div>
    </div>
  );
}

// ── Reading carousel ───────────────────────────────────

function ReadingCarousel({ isActive }: { isActive: boolean }) {
  const { settings, updateSetting, resetSettings } = useReadingSettings();
  const [settingIdx, setSettingIdx] = useState(0);

  const config = SETTING_CONFIGS[settingIdx];
  const getValue = (key: keyof ReadingSettings) => String(settings[key]);
  const currentOptIdx = config.options.findIndex(
    (o) => o.value === getValue(config.key),
  );

  const applyOption = (key: keyof ReadingSettings, value: string) => {
    if (key === "bionicReading") {
      updateSetting("bionicReading", value === "true");
    } else {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      updateSetting(key, value as any);
    }
  };

  const prevSetting = () =>
    setSettingIdx(
      (i) => (i - 1 + SETTING_CONFIGS.length) % SETTING_CONFIGS.length,
    );
  const nextSetting = () =>
    setSettingIdx((i) => (i + 1) % SETTING_CONFIGS.length);

  const prevOption = () => {
    const newIdx =
      (currentOptIdx - 1 + config.options.length) % config.options.length;
    applyOption(config.key, config.options[newIdx].value);
  };
  const nextOption = () => {
    const newIdx = (currentOptIdx + 1) % config.options.length;
    applyOption(config.key, config.options[newIdx].value);
  };

  useEffect(() => {
    if (!isActive) return;
    const handler = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      )
        return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        prevSetting();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        nextSetting();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        prevOption();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        nextOption();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  const currentLabel =
    config.options[currentOptIdx]?.label ?? getValue(config.key);
  const icon = SETTING_ICONS[config.key] ?? "·";

  return (
    <div>
      <div className="border border-[var(--color-border)] border-t-2 select-none">
        {/* Header: ‹  icon · LABEL  n/7  › */}
        <div className="flex items-stretch border-b border-[var(--color-border)]">
          <button
            onClick={prevSetting}
            className="px-3 font-mono text-[13px] text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors border-r border-[var(--color-border)] flex-shrink-0 leading-none"
          >
            ‹
          </button>

          <div className="flex-1 flex items-center justify-between px-4 py-2 gap-2">
            <div className="flex items-center gap-2.5">
              <span
                className="text-[12px] opacity-60 w-3 text-center flex-shrink-0"
                aria-hidden
              >
                {icon}
              </span>
              <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-text-muted)]">
                {config.label}
              </span>
            </div>
            <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
              {settingIdx + 1}&thinsp;/&thinsp;{SETTING_CONFIGS.length}
            </span>
          </div>

          <button
            onClick={nextSetting}
            className="px-3 font-mono text-[13px] text-[var(--color-text-faint)] hover:text-[var(--color-text-primary)] transition-colors border-l border-[var(--color-border)] flex-shrink-0 leading-none"
          >
            ›
          </button>
        </div>

        {/* Body: track · value */}
        <div className="flex items-center border-b border-[var(--color-border)]">
          <PositionTrack
            current={currentOptIdx}
            total={config.options.length}
          />
          <button
            onClick={nextOption}
            className="flex-1 text-center font-serif text-[17px] tracking-wide text-[var(--color-text-primary)] hover:text-[var(--color-accent)] transition-colors duration-150 py-4 px-4"
            title="↑ ↓ or click to cycle"
          >
            {currentLabel}
          </button>
        </div>

        {/* Footer: nav hint · reset */}
        <div className="px-3 py-1.5 flex items-center justify-between">
          <span className="font-mono text-[11px] tracking-widest text-[var(--color-text-faint)] select-none">
            ← → setting · ↑ ↓ option
          </span>
          <button
            onClick={resetSettings}
            className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text-faint)] hover:text-[var(--color-accent)] transition-colors"
          >
            reset ↺
          </button>
        </div>
      </div>

      <PreviewBox />
    </div>
  );
}

// ── Circle toggle ──────────────────────────────────────

function CircleToggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  description?: string;
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="w-full flex items-start justify-between py-3 text-left group gap-4"
    >
      <div>
        <div className="font-mono text-sm text-[var(--color-text-primary)] group-hover:text-[var(--color-accent)] transition-colors">
          {label}
        </div>
        {description && (
          <div className="font-mono text-xs text-[var(--color-text-muted)] mt-1 leading-relaxed">
            {description}
          </div>
        )}
      </div>
      <span className="flex-shrink-0 mt-0.5 p-2 -m-2">
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          className="pointer-events-none"
        >
          <circle
            cx="5"
            cy="5"
            r="4"
            fill={checked ? "var(--color-accent)" : "none"}
            stroke={checked ? "var(--color-accent)" : "var(--color-border)"}
            strokeWidth="1.5"
          />
        </svg>
      </span>
    </button>
  );
}

function FeatureVisibilitySection() {
  const { settings, updateSetting } = useReadingSettings();

  return (
    <div className="space-y-1">
      <CircleToggle
        checked={settings.showConnections}
        onChange={() =>
          updateSetting("showConnections", !settings.showConnections)
        }
        label="Connections (Experiment)"
        description="Experimental feature in progress. Show connections controls in the reader, including the sidebar and navbar toggle"
      />
      <CircleToggle
        checked={settings.showCrates}
        onChange={() => updateSetting("showCrates", !settings.showCrates)}
        label="Crates + audio player"
        description="Show crates navigation and audio player surfaces across the app"
      />
    </div>
  );
}

// ── Public profile section (inlined with real API) ─────

function PublicProfileSection() {
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
        e.response?.data?.detail || "Could not save profile settings.",
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
      {/* Username URL row */}
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

      {/* Toggles */}
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

      {/* Error */}
      {status === "error" && errorMsg && (
        <InlineError message={errorMsg} className="py-1.5" />
      )}

      {/* Actions */}
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

// ── Danger zone ───────────────────────────────────────

function DangerZone() {
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
      // fetchWithAuth throws Error with message like "API error: 400 - {"detail":"..."}"
      let msg = "Could not delete account.";
      if (err instanceof Error) {
        const match = err.message.match(/"detail"\s*:\s*"([^"]+)"/);
        if (match) msg = match[1];
      }
      setError(msg);
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
            <div className="font-mono text-xs text-red-500">{error}</div>
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

// ── Main page ──────────────────────────────────────────

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [activeSection, setActiveSection] = useState("reading");
  const readingRef = useRef<HTMLDivElement>(null);
  const featuresRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) setActiveSection(e.target.id);
        }),
      { rootMargin: "-40% 0px -55% 0px" },
    );
    if (readingRef.current) observer.observe(readingRef.current);
    if (featuresRef.current) observer.observe(featuresRef.current);
    if (accountRef.current) observer.observe(accountRef.current);
    return () => observer.disconnect();
  }, []);

  const scrollTo = (ref: React.RefObject<HTMLDivElement | null>) =>
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  const tocItems = [
    { id: "reading", label: "Reading", num: "01", ref: readingRef },
    { id: "features", label: "Features", num: "02", ref: featuresRef },
    { id: "account", label: "Account", num: "03", ref: accountRef },
  ];

  return (
    <div
      className="min-h-screen bg-[var(--color-bg-primary)] flex flex-col"
      suppressHydrationWarning
    >
      <Navbar />

      <main className="flex-1 max-w-4xl mx-auto w-full px-8 py-8">
        <div className="flex gap-12">
          {/* Sticky TOC */}
          <div className="hidden lg:block w-28 flex-shrink-0">
            <div className="sticky top-8 space-y-1">
              {tocItems.map(({ id, label, num, ref }) => (
                <button
                  key={id}
                  onClick={() => scrollTo(ref)}
                  className={`w-full text-left flex items-baseline gap-2 py-1 transition-colors ${
                    activeSection === id
                      ? "text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"
                  }`}
                >
                  <span
                    className={`font-mono text-[11px] ${activeSection === id ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]"}`}
                  >
                    {num}
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.15em]">
                    {label}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Sections */}
          <div className="flex-1 space-y-12">
            {/* 01 — Reading */}
            <div id="reading" ref={readingRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Reading Preferences
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>
              <ReadingCarousel isActive={activeSection === "reading"} />
            </div>

            {/* 02 — Features */}
            <div id="features" ref={featuresRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Feature Visibility
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>
              <FeatureVisibilitySection />
            </div>

            {/* 03 — Account */}
            <div id="account" ref={accountRef} className="space-y-6">
              <div className="flex items-center gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)] whitespace-nowrap">
                  Account
                </span>
                <div className="flex-1 border-t border-[var(--color-border)]" />
              </div>

              {/* § Session */}
              <div className="mb-8">
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)]">
                    Session
                  </span>
                </div>
                <div className="border border-[var(--color-border)] px-5 py-4 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-mono text-xs uppercase tracking-widest text-[var(--color-text-faint)] mb-1">
                      Signed in as
                    </div>
                    <div className="font-serif text-base text-[var(--color-text-primary)]">
                      {user?.email}
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      logout();
                      router.push("/login");
                    }}
                    className="font-mono text-xs uppercase tracking-widest px-3 py-2 border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-red-400 hover:text-red-500 transition-colors flex-shrink-0"
                  >
                    Sign out →
                  </button>
                </div>
              </div>

              {/* § Public Profile */}
              <div className="mb-8">
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-[var(--color-text-muted)]">
                    Public Profile
                  </span>
                </div>
                <PublicProfileSection />
              </div>

              {/* § Danger Zone */}
              <div>
                <div className="flex items-baseline gap-3 mb-3">
                  <span className="font-serif text-[var(--color-text-faint)] text-base select-none">
                    §
                  </span>
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-red-500/60">
                    Danger Zone
                  </span>
                </div>
                <DangerZone />
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
