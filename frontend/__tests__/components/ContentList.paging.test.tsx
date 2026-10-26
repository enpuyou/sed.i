/* eslint-disable @typescript-eslint/no-explicit-any */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import React from "react";
import ContentList from "../../components/ContentList";
import { contentAPI, listsAPI } from "../../lib/api";

let mockSearch = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: jest.fn(),
    replace: jest.fn(),
    prefetch: jest.fn(),
  }),
  useSearchParams: () => new URLSearchParams(mockSearch),
  usePathname: () => "/dashboard",
}));
jest.mock("../../lib/api");
const api = contentAPI as jest.Mocked<typeof contentAPI>;
const lists = listsAPI as jest.Mocked<typeof listsAPI>;

jest.mock("../../components/ContentItem", () => ({
  __esModule: true,
  default: ({ content }: any) => (
    <div data-testid={`content-item-${content.id}`}>{content.title}</div>
  ),
}));
jest.mock("../../components/ContentIndexItem", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/ContentCard", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/RetroLoader", () => ({
  __esModule: true,
  default: ({ text }: any) => <div>{text}</div>,
}));
jest.mock("../../hooks/useProcessingPolling", () => ({
  useProcessingPolling: jest.fn(),
}));
jest.mock("../../contexts/ListsContext", () => ({
  useLists: () => ({
    incrementListCount: jest.fn(),
    decrementListCount: jest.fn(),
  }),
}));
jest.mock("../../hooks/useHotkeys", () => ({ useHotkeys: jest.fn() }));
jest.mock("../../components/FilterDropdownContent", () => ({
  FilterDropdownContent: () => null,
}));

const makeItems = (from: number, to: number, status = "unread") =>
  Array.from({ length: to - from }, (_, i) => ({
    id: `id-${from + i}`,
    title: `Item ${from + i}`,
    tags: [],
    reading_status: from + i >= 150 ? "read" : status,
    created_at: "2024-01-01T10:00:00Z",
  }));

// Serves `total` items in pages honouring skip/limit; items >= 150 are "read".
const serve = (total: number, failSkip?: number) =>
  api.getAll.mockImplementation((async (opts?: any) => {
    const skip = opts?.skip ?? 0;
    const limit = opts?.limit ?? 50;
    if (failSkip !== undefined && skip === failSkip) throw new Error("boom");
    return {
      items: makeItems(skip, Math.min(skip + limit, total)),
      total,
      skip,
      limit,
    };
  }) as any);

describe("ContentList paging", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSearch = "";
    localStorage.clear();
    lists.getAll.mockResolvedValue([]);
    api.getTags.mockResolvedValue([]);
  });

  it("requests every page (skip 0,100,200) and renders all 230 items", async () => {
    serve(230);
    render(<ContentList ref={React.createRef()} />);
    await waitFor(() =>
      expect(screen.getAllByTestId(/^content-item-/)).toHaveLength(230),
    );
    expect(api.getAll.mock.calls.map((c: any[]) => c[0])).toEqual([
      { skip: 0, limit: 100 },
      { skip: 100, limit: 100 },
      { skip: 200, limit: 100 },
    ]);
    expect(screen.getByText("(230 / 230 items)")).toBeInTheDocument();
  });

  it("applies filters to the full set, not just the first page", async () => {
    mockSearch = "filter=read";
    serve(230);
    render(<ContentList ref={React.createRef()} />);
    await waitFor(() =>
      expect(screen.getAllByTestId(/^content-item-/)).toHaveLength(80),
    );
    expect(screen.getByText("(80 / 230 items)")).toBeInTheDocument();
  });

  it("keeps loaded items and shows InlineError with retry when a later page fails", async () => {
    jest.spyOn(console, "error").mockImplementation();
    serve(230, 100);
    render(<ContentList ref={React.createRef()} />);
    await screen.findByRole("alert");
    expect(screen.getAllByTestId(/^content-item-/)).toHaveLength(100);

    serve(230);
    fireEvent.click(screen.getByText(/retry/i));
    await waitFor(() =>
      expect(screen.getAllByTestId(/^content-item-/)).toHaveLength(230),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
