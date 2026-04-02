"use client";

import { useState, useEffect, useCallback } from "react";
import {
  useReadingSettings,
  type ReadingSettings,
} from "@/contexts/ReadingSettingsContext";
import HotkeysModal from "./HotkeysModal";

interface SettingConfig {
  key: keyof ReadingSettings;
  label: string;
  icon: string;
  options: Array<{ value: string; label: string }>;
}

const settings: SettingConfig[] = [
  {
    key: "theme",
    label: "Theme",
    icon: "◐",
    options: [
      { value: "light", label: "Light" },
      { value: "dark", label: "Dark" },
      { value: "sepia", label: "Sepia" },
    ],
  },
  {
    key: "fontFamily",
    label: "Font",
    icon: "Aa",
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
    icon: "↕",
    options: [
      { value: "small", label: "Small" },
      { value: "medium", label: "Medium" },
      { value: "large", label: "Large" },
    ],
  },
  {
    key: "contentWidth",
    label: "Width",
    icon: "↔",
    options: [
      { value: "narrow", label: "Narrow" },
      { value: "medium", label: "Medium" },
      { value: "wide", label: "Wide" },
    ],
  },
  {
    key: "lineHeight",
    label: "Height",
    icon: "≡",
    options: [
      { value: "compact", label: "Compact" },
      { value: "comfortable", label: "Normal" },
      { value: "spacious", label: "Spacious" },
    ],
  },
  {
    key: "letterSpacing",
    label: "Spacing",
    icon: "⋯",
    options: [
      { value: "tight", label: "Tight" },
      { value: "normal", label: "Normal" },
      { value: "wide", label: "Wide" },
    ],
  },
  {
    key: "bionicReading",
    label: "Bionic",
    icon: "◉",
    options: [
      { value: "false", label: "Off" },
      { value: "true", label: "On" },
    ],
  },
];

export default function SettingsCarousel() {
  const {
    settings: currentSettings,
    hydrated,
    updateSetting,
    resetSettings,
  } = useReadingSettings();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isAnimating, setIsAnimating] = useState(false);
  const [showHotkeys, setShowHotkeys] = useState(false);

  const currentSetting = settings[currentIndex];
  const prevSetting =
    settings[(currentIndex - 1 + settings.length) % settings.length];
  const nextSetting = settings[(currentIndex + 1) % settings.length];

  const currentValue =
    currentSetting.key === "bionicReading"
      ? String(currentSettings[currentSetting.key])
      : String(currentSettings[currentSetting.key]);

  const currentOption = currentSetting.options.find(
    (opt) => opt.value === currentValue,
  );

  const goToPrev = useCallback(() => {
    if (isAnimating) return;
    setIsAnimating(true);
    setTimeout(() => {
      setCurrentIndex((prev) => (prev === 0 ? settings.length - 1 : prev - 1));
      setIsAnimating(false);
    }, 200);
  }, [isAnimating]);

  const goToNext = useCallback(() => {
    if (isAnimating) return;
    setIsAnimating(true);
    setTimeout(() => {
      setCurrentIndex((prev) => (prev === settings.length - 1 ? 0 : prev + 1));
      setIsAnimating(false);
    }, 200);
  }, [isAnimating]);

  const cycleValue = useCallback(() => {
    const currentOptionIndex = currentSetting.options.findIndex(
      (opt) => opt.value === currentValue,
    );
    const nextIndex = (currentOptionIndex + 1) % currentSetting.options.length;
    const nextValue = currentSetting.options[nextIndex].value;

    if (currentSetting.key === "bionicReading") {
      updateSetting("bionicReading", nextValue === "true");
    } else {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      updateSetting(currentSetting.key, nextValue as any);
    }
  }, [currentSetting, currentValue, updateSetting]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        goToPrev();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        goToNext();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        cycleValue();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [goToPrev, goToNext, cycleValue]);

  if (!hydrated) return null;

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Main carousel row */}
      <div className="flex items-center justify-center gap-2 w-full">
        {/* Previous setting ghost hint */}
        <div
          className="hidden sm:flex items-center justify-end gap-1.5 w-[100px] py-1.5 opacity-40 cursor-pointer hover:opacity-60 transition-opacity"
          onClick={goToPrev}
        >
          <span className="text-sm">{prevSetting.icon}</span>
          <span className="text-xs text-[var(--color-text-muted)] truncate">
            {prevSetting.label}
          </span>
        </div>

        {/* Previous Arrow */}
        <button
          onClick={goToPrev}
          className="w-8 h-8 sm:w-10 sm:h-10 flex items-center justify-center rounded-full text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-all duration-200"
          aria-label="Previous setting"
        >
          <svg
            className="w-4 h-4 sm:w-5 sm:h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M15 19l-7-7 7-7"
            />
          </svg>
        </button>

        {/* Current Setting - Main Pill */}
        <button
          onClick={cycleValue}
          className={`
            relative w-[160px] h-[56px] sm:w-[200px] sm:h-[72px] px-4 py-2 sm:px-6 sm:py-3
            rounded-2xl
            bg-[var(--color-bg-secondary)]
            border border-[var(--color-border)]
            shadow-[inset_0_1px_0_rgba(255,255,255,0.05),_0_2px_8px_rgba(0,0,0,0.1)]
            hover:border-[var(--color-accent)]
            hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.05),_0_2px_12px_rgba(0,0,0,0.15),_0_0_0_1px_var(--color-accent)]
            focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-primary)]
            transition-shadow duration-300 ease-out
          `}
        >
          <div
            className={`
              flex items-center justify-center gap-2
              transition-opacity duration-150 ease-out
              ${isAnimating ? "opacity-0" : "opacity-100"}
            `}
          >
            <span className="text-base sm:text-lg opacity-70">
              {currentSetting.icon}
            </span>
            <div className="text-left">
              <div className="text-[9px] sm:text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-0.5">
                {currentSetting.label}
              </div>
              <div className="text-xs sm:text-sm font-medium text-[var(--color-text-primary)]">
                {currentOption?.label || currentValue}
              </div>
            </div>
          </div>
        </button>

        {/* Next Arrow */}
        <button
          onClick={goToNext}
          className="w-8 h-8 sm:w-10 sm:h-10 flex items-center justify-center rounded-full text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-secondary)] transition-all duration-200"
          aria-label="Next setting"
        >
          <svg
            className="w-4 h-4 sm:w-5 sm:h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M9 5l7 7-7 7"
            />
          </svg>
        </button>

        {/* Next setting ghost hint */}
        <div
          className="hidden sm:flex items-center justify-start gap-1.5 w-[100px] py-1.5 opacity-40 cursor-pointer hover:opacity-60 transition-opacity"
          onClick={goToNext}
        >
          <span className="text-xs text-[var(--color-text-muted)] truncate">
            {nextSetting.label}
          </span>
          <span className="text-sm">{nextSetting.icon}</span>
        </div>
      </div>

      {/* Bottom row: dots + reset */}
      {/* Styles for dots */}
      <style>{`
          .force-dot-mobile {
            width: 8px !important;
            height: 8px !important;
            min-width: 8px !important;
            max-width: 8px !important;
            min-height: 8px !important;
            max-height: 8px !important;
            flex-shrink: 0 !important;
            flex-grow: 0 !important;
          }
          .force-dot-mobile.active {
            width: 16px !important;
            min-width: 16px !important;
            max-width: 16px !important;
          }
          @media (max-width: 639px) {
            .force-dot-mobile {
              width: 6px !important;
              height: 6px !important;
              min-width: 6px !important;
              max-width: 6px !important;
              min-height: 6px !important;
              max-height: 6px !important;
            }
            .force-dot-mobile.active {
              width: 12px !important;
              min-width: 12px !important;
              max-width: 12px !important;
            }
          }
        `}</style>
      <div className="flex items-center justify-center gap-6 w-full px-4">
        {/* Position indicator dots */}
        <div className="flex gap-1.5">
          {settings.map((_, index) => (
            <button
              key={index}
              onClick={() => {
                if (index !== currentIndex && !isAnimating) {
                  setIsAnimating(true);
                  setTimeout(() => {
                    setCurrentIndex(index);
                    setIsAnimating(false);
                  }, 200);
                }
              }}
              className={`force-dot-mobile rounded-full transition-all duration-300 ease-out cursor-pointer ${index === currentIndex ? "active" : ""}`}
              style={{
                backgroundColor:
                  index === currentIndex
                    ? "var(--color-accent)"
                    : "var(--color-border)",
              }}
              aria-label={`Go to ${settings[index].label} setting`}
            />
          ))}
        </div>

        <button
          onClick={resetSettings}
          className="text-[11px] sm:text-xs px-3 py-2 text-[var(--color-text-muted)] hover:text-[var(--color-accent)] transition-colors duration-200"
        >
          Reset
        </button>

        {/* Keyboard hint */}
        <div className="hidden md:flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] opacity-60">
          <kbd className="px-1.5 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
            ←
          </kbd>
          <kbd className="px-1.5 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
            →
          </kbd>
          <span className="ml-1">to navigate</span>
        </div>
      </div>

      {showHotkeys && <HotkeysModal onClose={() => setShowHotkeys(false)} />}
    </div>
  );
}
