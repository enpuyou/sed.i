"use client";

import { useReadingSettings } from "@/contexts/ReadingSettingsContext";
import { BionicText } from "./BionicText";

export default function SettingsPreview() {
  const { settings, hydrated } = useReadingSettings();
  if (!hydrated) return null;

  const previewParagraphs = [
    {
      text: `Transform passive reading into active thinking. Highlight key ideas, add notes, and build your knowledge graph.`,
      hiddenOnMobile: false,
    },
    {
      text: `Customize your reading experience. Choose fonts, adjust sizing, and switch themes. All settings sync across devices.`,
      hiddenOnMobile: false,
    },
    {
      text: `Every article you save becomes part of your personal library. Mark important passages and let the system surface related content.`,
      hiddenOnMobile: true,
    },
    {
      text: `Built for focus and comprehension. The clean interface removes distractions while powerful features help you extract maximum value.`,
      hiddenOnMobile: true,
    },
  ];

  // Content width class mapping
  const contentWidthClass =
    settings.contentWidth === "narrow"
      ? "max-w-md"
      : settings.contentWidth === "wide"
        ? "max-w-2xl"
        : "max-w-xl";

  return (
    <div className="mb-8 p-6 border border-[var(--color-border)] rounded bg-[var(--color-bg-primary)] transition-all duration-300">
      <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-4 uppercase tracking-wider">
        Preview
      </h3>
      {/* Wrapper for content width */}
      <div
        className={`mx-auto transition-all duration-300 ${contentWidthClass}`}
      >
        <div
          className={`
                        transition-all duration-300
                        ${
                          settings.fontFamily === "serif"
                            ? "font-serif-setting"
                            : settings.fontFamily === "sans"
                              ? "font-sans-setting"
                              : settings.fontFamily === "merriweather"
                                ? "font-merriweather-setting"
                                : settings.fontFamily === "verdana"
                                  ? "font-verdana-setting"
                                  : "font-system-setting"
                        }
                        ${
                          settings.fontSize === "small"
                            ? "text-small-setting"
                            : settings.fontSize === "large"
                              ? "text-large-setting"
                              : "text-medium-setting"
                        }
                        ${
                          settings.lineHeight === "compact"
                            ? "line-height-compact"
                            : settings.lineHeight === "spacious"
                              ? "line-height-spacious"
                              : "line-height-comfortable"
                        }
                        ${
                          settings.letterSpacing === "tight"
                            ? "letter-spacing-tight"
                            : settings.letterSpacing === "wide"
                              ? "letter-spacing-wide"
                              : "letter-spacing-normal"
                        }
                    `}
        >
          {previewParagraphs.map((para, index) => (
            <p
              key={index}
              className={`text-[var(--color-text-secondary)] mb-4 last:mb-0 antialiased ${para.hiddenOnMobile ? "hidden sm:block" : ""}`}
            >
              {settings.bionicReading ? (
                <BionicText text={para.text} />
              ) : (
                para.text
              )}
            </p>
          ))}
        </div>
      </div>
    </div>
  );
}
