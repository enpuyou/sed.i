/**
 * usePagedListContent: first page supplied by the caller, later pages appended
 * 50 at a time via skip/limit.
 *
 * Done when: loadMore asks for skip = rows loaded; rows already shown are not
 * duplicated; a short page ends paging; a failed page keeps loaded rows and
 * can be retried; a stale response after reset() is dropped; concurrent
 * loadMore calls collapse into one request.
 */

import { renderHook, act } from "@testing-library/react";
import { usePagedListContent } from "../../hooks/usePagedListContent";
import { listsAPI } from "../../lib/api";
import type { ContentItem } from "../../types";

jest.mock("../../lib/api", () => ({
  listsAPI: { getContent: jest.fn() },
}));
const getContent = listsAPI.getContent as jest.Mock;

const item = (n: number) =>
  ({ id: `id-${n}`, title: `Item ${n}` }) as ContentItem;
const page = (from: number, count: number) =>
  Array.from({ length: count }, (_, i) => item(from + i));

beforeEach(() => getContent.mockReset());

describe("usePagedListContent", () => {
  it("reset() shows the first page and the total", () => {
    const { result } = renderHook(() => usePagedListContent("list-1", 3));

    act(() => result.current.reset(page(0, 3), 7));

    expect(result.current.contents).toHaveLength(3);
    expect(result.current.displayTotal).toBe(7);
    expect(result.current.hasMore).toBe(true);
  });

  it("a first page shorter than the page size means there is no more", () => {
    const { result } = renderHook(() => usePagedListContent("list-1", 3));

    act(() => result.current.reset(page(0, 2), 2));

    expect(result.current.hasMore).toBe(false);
  });

  it("loadMore requests skip = loaded rows and appends the page", async () => {
    getContent.mockResolvedValue(page(3, 3));
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 7));

    await act(() => result.current.loadMore());

    expect(getContent).toHaveBeenCalledWith("list-1", { skip: 3, limit: 3 });
    expect(result.current.contents.map((c) => c.id)).toEqual(
      page(0, 6).map((c) => c.id),
    );
    expect(result.current.hasMore).toBe(true);
  });

  it("does not duplicate rows that shifted between pages", async () => {
    getContent.mockResolvedValue([item(2), item(3), item(4)]);
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 8));

    await act(() => result.current.loadMore());

    expect(result.current.contents.map((c) => c.id)).toEqual([
      "id-0",
      "id-1",
      "id-2",
      "id-3",
      "id-4",
    ]);
  });

  it("a short page ends paging and the count follows what is loaded", async () => {
    getContent.mockResolvedValue(page(3, 1));
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 9));

    await act(() => result.current.loadMore());

    expect(result.current.hasMore).toBe(false);
    expect(result.current.displayTotal).toBe(4);
  });

  it("keeps loaded rows on failure and succeeds on retry", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    getContent.mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 6));

    await act(() => result.current.loadMore());
    expect(result.current.loadMoreError).toBe(
      "Couldn't load more items. Try again.",
    );
    expect(result.current.contents).toHaveLength(3);

    getContent.mockResolvedValueOnce(page(3, 3));
    await act(() => result.current.loadMore());

    expect(result.current.loadMoreError).toBeNull();
    expect(result.current.contents).toHaveLength(6);
  });

  it("collapses concurrent loadMore calls into one request", async () => {
    let resolve!: (v: ContentItem[]) => void;
    getContent.mockReturnValue(new Promise((r) => (resolve = r)));
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 9));

    act(() => {
      void result.current.loadMore();
      void result.current.loadMore();
    });
    await act(async () => resolve(page(3, 3)));

    expect(getContent).toHaveBeenCalledTimes(1);
    expect(result.current.contents).toHaveLength(6);
  });

  it("drops a response that arrives after reset()", async () => {
    let resolve!: (v: ContentItem[]) => void;
    getContent.mockReturnValue(new Promise((r) => (resolve = r)));
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 9));

    act(() => {
      void result.current.loadMore();
    });
    act(() => result.current.reset(page(100, 3), 9));
    await act(async () => resolve(page(3, 3)));

    expect(result.current.contents.map((c) => c.id)).toEqual([
      "id-100",
      "id-101",
      "id-102",
    ]);
  });

  it("adjustTotal tracks local removals and never goes negative", () => {
    const { result } = renderHook(() => usePagedListContent("list-1", 3));
    act(() => result.current.reset(page(0, 3), 4));

    act(() => result.current.adjustTotal(-1));
    expect(result.current.displayTotal).toBe(3);

    act(() => result.current.adjustTotal(-10));
    expect(result.current.displayTotal).toBe(0);
  });
});
