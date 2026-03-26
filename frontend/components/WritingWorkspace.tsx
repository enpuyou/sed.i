"use client";

import { useEffect } from "react";
import MarkdownEditor from "@/components/MarkdownEditor";

interface WritingWorkspaceProps {
    listId: string;
    listName: string;
    initialContent: string;
    inline?: boolean;
    focusModeEnabled?: boolean;
    onExit?: () => void;
    onExportReady?: (fn: () => void) => void;
}

export default function WritingWorkspace({
    listId,
    listName,
    initialContent,
    inline = false,
    focusModeEnabled = false,
    onExit,
    onExportReady,
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
                focusModeEnabled={focusModeEnabled}
                onExportReady={onExportReady}
            />
        </div>
    );
}
