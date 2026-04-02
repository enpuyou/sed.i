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
  hydrated: boolean;
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
  const [settings, setSettings] = useState<ReadingSettings>(DEFAULTS);
  const [hydrated, setHydrated] = useState(false);

  // Load from localStorage after mount to avoid SSR/hydration mismatch
  useLayoutEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        setSettings({ ...DEFAULTS, ...JSON.parse(saved) });
      } catch (err) {
        console.error("Failed to parse settings:", err);
      }
    }
    setHydrated(true);
  }, []);

  // Apply theme synchronously before paint to prevent flash
  useLayoutEffect(() => {
    const root = document.documentElement;
    const themes = ["light", "dark", "sepia", "true-black"];
    root.classList.remove(...themes);
    root.classList.add(settings.theme);
  }, [settings.theme]);

  // Save settings to localStorage whenever they change (after hydration)
  useEffect(() => {
    if (hydrated) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    }
  }, [settings, hydrated]);

  const updateSetting = <K extends keyof ReadingSettings>(
    key: K,
    value: ReadingSettings[K],
  ) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const resetSettings = () => setSettings(DEFAULTS);

  return (
    <ReadingSettingsContext.Provider
      value={{ settings, hydrated, updateSetting, resetSettings }}
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
