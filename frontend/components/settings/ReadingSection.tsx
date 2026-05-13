"use client";

import { useState, useEffect, useCallback } from "react";
import { BionicText } from "@/components/BionicText";
import {
  useReadingSettings,
  ReadingSettings,
} from "@/contexts/ReadingSettingsContext";

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

export default function ReadingSection({ isActive }: { isActive: boolean }) {
  const { settings, updateSetting, resetSettings } = useReadingSettings();
  const [settingIdx, setSettingIdx] = useState(0);

  const config = SETTING_CONFIGS[settingIdx];
  const getValue = (key: keyof ReadingSettings) => String(settings[key]);
  const currentOptIdx = config.options.findIndex(
    (o) => o.value === getValue(config.key),
  );

  const applyOption = useCallback(
    (key: keyof ReadingSettings, value: string) => {
      if (key === "bionicReading") {
        updateSetting("bionicReading", value === "true");
      } else {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        updateSetting(key, value as any);
      }
    },
    [updateSetting],
  );

  const prevSetting = useCallback(
    () =>
      setSettingIdx(
        (i) => (i - 1 + SETTING_CONFIGS.length) % SETTING_CONFIGS.length,
      ),
    [],
  );
  const nextSetting = useCallback(
    () => setSettingIdx((i) => (i + 1) % SETTING_CONFIGS.length),
    [],
  );

  const prevOption = useCallback(() => {
    const newIdx =
      (currentOptIdx - 1 + config.options.length) % config.options.length;
    applyOption(config.key, config.options[newIdx].value);
  }, [currentOptIdx, config, applyOption]);

  const nextOption = useCallback(() => {
    const newIdx = (currentOptIdx + 1) % config.options.length;
    applyOption(config.key, config.options[newIdx].value);
  }, [currentOptIdx, config, applyOption]);

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
  }, [isActive, prevSetting, nextSetting, prevOption, nextOption]);

  const currentLabel =
    config.options[currentOptIdx]?.label ?? getValue(config.key);
  const icon = SETTING_ICONS[config.key] ?? "·";

  return (
    <div>
      <div className="border border-[var(--color-border)] border-t-2 select-none">
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
