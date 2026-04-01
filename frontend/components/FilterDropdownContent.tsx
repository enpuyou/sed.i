import Link from "next/link";
import { useState } from "react";

type FilterType = "all" | "unread" | "in_progress" | "read" | "archived";

interface FilterDropdownContentProps {
  currentFilter: FilterType;
  currentTags: string[];
  availableTags: Array<{ tag: string; count: number }>;
  onSelectFilter: (filter: FilterType) => void;
  onToggleTag: (tag: string) => void;
  onClearTags: () => void;
}

export function FilterDropdownContent({
  currentFilter,
  currentTags,
  availableTags,
  onSelectFilter,
  onToggleTag,
  onClearTags,
}: FilterDropdownContentProps) {
  const [activeTab, setActiveTab] = useState<"status" | "tags">("status");
  const [tagSearch, setTagSearch] = useState("");

  const filteredTags = availableTags.filter((t) =>
    t.tag.toLowerCase().includes(tagSearch.toLowerCase()),
  );

  return (
    <div className="flex flex-col w-full bg-[var(--color-bg-primary)]">
      {/* Flip Toggle */}
      <div className="flex justify-center p-3 border-b border-[var(--color-border)]">
        <div className="relative flex bg-[var(--color-bg-secondary)] p-1 rounded-sm border border-[var(--color-border)] w-full">
          {/* Animated Indicator */}
          <div
            className={`absolute top-1 bottom-1 w-[calc(50%-4px)] bg-[var(--color-bg-primary)] border border-[var(--color-border)] shadow-sm transition-all duration-200 ease-in-out ${
              activeTab === "status" ? "left-1" : "left-[calc(50%+2px)]"
            }`}
          />

          <button
            onClick={() => setActiveTab("status")}
            className={`flex-1 relative z-10 text-[10px] font-mono uppercase tracking-wider text-center py-1 transition-colors ${
              activeTab === "status"
                ? "text-[var(--color-text-primary)] font-bold"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            Status
          </button>
          <button
            onClick={() => setActiveTab("tags")}
            className={`flex-1 relative z-10 text-[10px] font-mono uppercase tracking-wider text-center py-1 transition-colors ${
              activeTab === "tags"
                ? "text-[var(--color-text-primary)] font-bold"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            Tags
          </button>
        </div>
      </div>

      {/* Status List */}
      {activeTab === "status" && (
        <div className="py-2 flex flex-col gap-1">
          {(["all", "unread", "in_progress", "read", "archived"] as const).map(
            (filterType) => (
              <Link
                key={filterType}
                href={
                  filterType === "all"
                    ? "/dashboard"
                    : `/dashboard?filter=${filterType}`
                }
                onClick={() => onSelectFilter(filterType)}
                className={`group flex items-center justify-between px-4 py-2 text-xs font-mono transition-colors no-underline ${
                  currentFilter === filterType
                    ? "text-[var(--color-accent)]"
                    : "!text-[var(--color-text-primary)] hover:opacity-70"
                }`}
              >
                <span
                  className={`uppercase ${currentFilter === filterType ? "font-bold" : ""}`}
                >
                  {filterType.replace("_", " ")}
                </span>
                {currentFilter === filterType && (
                  <span className="text-[var(--color-accent)]">●</span>
                )}
              </Link>
            ),
          )}
        </div>
      )}

      {/* Tags List */}
      {activeTab === "tags" && (
        <div className="flex flex-col h-64">
          <div className="p-3">
            <input
              type="text"
              placeholder="Filter..."
              value={tagSearch}
              onChange={(e) => setTagSearch(e.target.value)}
              className="w-full bg-transparent border-b border-[var(--color-border)] py-1 text-xs font-mono text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none focus:!ring-0 focus:border-[var(--color-accent)] transition-colors lowercase tracking-wide"
              autoFocus
            />
          </div>
          <div className="overflow-y-auto flex-1 p-3 scrollbar-thin scrollbar-thumb-[var(--color-border)] scrollbar-track-transparent">
            <div className="flex flex-wrap gap-2">
              {/* All Tags Option */}
              <button
                onClick={onClearTags}
                className={`px-2 py-1 text-[10px] font-mono border transition-all lowercase ${
                  currentTags.length === 0
                    ? "bg-[var(--color-accent)] text-[var(--color-bg-primary)] border-[var(--color-accent)]"
                    : "bg-transparent text-[var(--color-text-secondary)] border-[var(--color-border)] hover:border-[var(--color-accent)] hover:text-[var(--color-text-primary)]"
                }`}
              >
                #all
              </button>

              {availableTags.length === 0 ? (
                <div className="w-full text-center py-8 text-xs text-[var(--color-text-muted)] font-mono">
                  No tags found
                </div>
              ) : filteredTags.length === 0 ? (
                <div className="w-full text-center py-8 text-xs text-[var(--color-text-muted)] font-mono">
                  No matches
                </div>
              ) : (
                filteredTags.map((t) => {
                  const isSelected = currentTags.includes(t.tag);
                  const isDisabled = !isSelected && currentTags.length >= 3;

                  return (
                    <button
                      key={t.tag}
                      onClick={() => !isDisabled && onToggleTag(t.tag)}
                      disabled={isDisabled}
                      className={`px-2 py-1 text-[10px] font-mono border transition-all flex items-center gap-1.5 lowercase ${
                        isSelected
                          ? "bg-[var(--color-accent)] text-[var(--color-bg-primary)] border-[var(--color-accent)]"
                          : isDisabled
                            ? "text-[var(--color-text-muted)] border-[var(--color-border)] opacity-50 cursor-not-allowed"
                            : "bg-transparent text-[var(--color-text-secondary)] border-[var(--color-border)] hover:border-[var(--color-accent)] hover:text-[var(--color-text-primary)]"
                      }`}
                    >
                      <span className="truncate">#{t.tag}</span>
                      <span
                        className={`opacity-60 ${isSelected ? "text-[var(--color-bg-primary)]" : ""}`}
                      >
                        ({t.count})
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
