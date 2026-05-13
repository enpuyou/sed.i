"use client";

import { useReadingSettings } from "@/contexts/ReadingSettingsContext";
import { CircleToggle } from "./CircleToggle";

export default function FeatureVisibilitySection() {
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
