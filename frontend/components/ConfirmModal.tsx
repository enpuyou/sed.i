"use client";

import { useEffect } from "react";

interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean; // red styling for destructive actions
}

export default function ConfirmModal({
  isOpen,
  title,
  message,
  confirmText = "Confirm",
  cancelText = "Cancel",
  onConfirm,
  onCancel,
  danger = false,
}: ConfirmModalProps) {
  // Close modal on ESC key
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };

    if (isOpen) {
      document.addEventListener("keydown", handleEsc);
      // Prevent body scroll when modal is open
      document.body.style.overflow = "hidden";
    }

    return () => {
      document.removeEventListener("keydown", handleEsc);
      document.body.style.overflow = "unset";
    };
  }, [isOpen, onCancel]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 cursor-default"
        onClick={(e) => {
          e.stopPropagation();
          onCancel();
        }}
      />

      {/* Modal - Are.na ultra-minimal style */}
      <div
        className="relative bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-none w-72 p-4 z-10"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-medium text-[var(--color-text-primary)] mb-1">
          {title}
        </h3>

        <p className="text-xs text-[var(--color-text-muted)] mb-4">{message}</p>

        {/* Buttons - navbar style */}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-text-primary)] transition-colors"
          >
            {cancelText}
          </button>

          <button
            onClick={onConfirm}
            className={`text-xs px-2 py-0.5 leading-none rounded-none border transition-colors ${
              danger
                ? "bg-rose-50 dark:bg-red-900/30 text-rose-500 dark:text-red-400 border-transparent hover:border-red-500 dark:hover:bg-red-900/50"
                : "border-[var(--color-border)] text-[var(--color-text-primary)] hover:border-[var(--color-accent)]"
            }`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
