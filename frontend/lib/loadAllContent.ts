import { contentAPI } from "@/lib/api";
import { ContentItem } from "@/types";

// GET /content caps a page at 100 items.
const PAGE_SIZE = 100;
const MAX_PAGES = 100;

/**
 * Loads every page of the user's library, sequentially. onPage fires after
 * each page so callers can render the first page immediately; if a later
 * page fails the error propagates after earlier onPage calls have run.
 */
export async function loadAllContent(
  onPage?: (items: ContentItem[], total: number) => void,
): Promise<{ items: ContentItem[]; total: number }> {
  const all: ContentItem[] = [];
  let total = 0;
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await contentAPI.getAll({
      skip: all.length,
      limit: PAGE_SIZE,
    });
    all.push(...res.items);
    total = res.total;
    onPage?.(res.items, total);
    if (all.length >= total || res.items.length < PAGE_SIZE) break;
  }
  return { items: all, total };
}
