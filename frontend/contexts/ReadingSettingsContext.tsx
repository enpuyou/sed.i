"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useLayoutEffect,
  ReactNode,
} from "react";

export interface ReadingSettings {
  theme: "light" | "dark" | "sepia" | "true-black";
  fontFamily: "system" | "serif" | "sans" | "merriweather" | "verdana";
  fontSize: "small" | "medium" | "large";
  contentWidth: "narrow" | "medium" | "wide";
  lineHeight: "compact" | "comfortable" | "spacious";
  letterSpacing: "tight" | "normal" | "wide";
  bionicReading: boolean;
  showConnections: boolean;
  showCrates: boolean;
}

const DEFAULTS: ReadingSettings = {
  theme: "light",
  fontFamily: "sans",
  fontSize: "medium",
  contentWidth: "medium",
  lineHeight: "comfortable",
  letterSpacing: "normal",
  bionicReading: false,
  showConnections: true,
  showCrates: true,
};

const STORAGE_KEY = "sedi-reading-settings";

interface ReadingSettingsContextType {
  settings: ReadingSettings;
  updateSetting: <K extends keyof ReadingSettings>(
    key: K,
    value: ReadingSettings[K],
  ) => void;
  resetSettings: () => void;
}

const ReadingSettingsContext = createContext<ReadingSettingsContextType | null>(
  null,
);

export function ReadingSettingsProvider({ children }: { children: ReactNode }) {
  // Initialize from localStorage synchronously to prevent flash
  const [settings, setSettings] = useState<ReadingSettings>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        try {
          return { ...DEFAULTS, ...JSON.parse(saved) };
        } catch (err) {
          console.error("Failed to parse settings:", err);
        }
      }
    }
    return DEFAULTS;
  });

  // Apply theme synchronously before paint to prevent flash
  useLayoutEffect(() => {
    const root = document.documentElement;
    const themes = ["light", "dark", "sepia", "true-black"];
    root.classList.remove(...themes);
    root.classList.add(settings.theme);
  }, [settings.theme]);

  // Save settings to localStorage whenever they change
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const updateSetting = <K extends keyof ReadingSettings>(
    key: K,
    value: ReadingSettings[K],
  ) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const resetSettings = () => setSettings(DEFAULTS);

  return (
    <ReadingSettingsContext.Provider
      value={{ settings, updateSetting, resetSettings }}
    >
      {children}
    </ReadingSettingsContext.Provider>
  );
}

export function useReadingSettings() {
  const ctx = useContext(ReadingSettingsContext);
  if (!ctx)
    throw new Error(
      "useReadingSettings must be used within ReadingSettingsProvider",
    );
  return ctx;
}
