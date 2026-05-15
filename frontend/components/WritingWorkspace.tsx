"use client";

import { useEffect } from "react";
import MarkdownEditor from "@/components/MarkdownEditor";

interface WritingWorkspaceProps {
  listId: string;
  listName: string;
  initialContent: string;
  inline?: boolean;
  initialFullscreen?: boolean;
  onExit?: () => void;
  onExport?: (format: "md" | "pdf" | "docx") => void;
  onExportReady?: (
    fn: (format?: "md" | "pdf" | "docx") => void | Promise<void>,
  ) => void;
  onFullscreenChange?: (fs: boolean) => void;
  onSaved?: () => void;
}

export default function WritingWorkspace({
  listId,
  listName,
  initialContent,
  inline = false,
  initialFullscreen = false,
  onExit,
  onExport,
  onExportReady,
  onFullscreenChange,
  onSaved,
}: WritingWorkspaceProps) {
  // Escape key to close when inline
  useEffect(() => {
    if (!inline) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onExit?.();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [inline, onExit]);

  return (
    <div className="writing-workspace-inline">
      <MarkdownEditor
        listId={listId}
        listName={listName}
        initialContent={initialContent}
        initialFullscreen={initialFullscreen}
        onExport={onExport}
        onExportReady={onExportReady}
        onFullscreenChange={onFullscreenChange}
        onSaved={onSaved}
      />
    </div>
  );
}
