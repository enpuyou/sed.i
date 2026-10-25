/**
 * Lists page: infinite scroll through the real page component.
 *
 * Done when: the first request asks for 50 items; the header shows the list's
 * TOTAL; scrolling the footer into view requests skip=50, then skip=100; the
 * footer disappears when everything is loaded; deleting an item lowers the
 * total without refetching; a failed page shows Retry and keeps the rows.
 */

import {
  render,
  screen,
  act,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import "@testing-library/jest-dom";
import ListPage from "../../app/lists/[id]/page";
import { listsAPI, draftsAPI, contentAPI } from "../../lib/api";

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "list-1" }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));
jest.mock("../../contexts/ListsContext", () => ({
  useLists: () => ({
    incrementListCount: jest.fn(),
    decrementListCount: jest.fn(),
  }),
}));
jest.mock("../../lib/api", () => ({
  listsAPI: {
    getById: jest.fn(),
    getContent: jest.fn(),
    removeContent: jest.fn(),
  },
  draftsAPI: { get: jest.fn() },
  contentAPI: { update: jest.fn(), delete: jest.fn() },
}));
jest.mock("../../components/Navbar", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/NowPlaying", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/WritingWorkspace", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/ReaderArticle", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/RelevantReadsPanel", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/AddContentToListModal", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/ListModal", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/RetroLoader", () => ({
  __esModule: true,
  default: () => <div>loading</div>,
}));
jest.mock("../../components/ContentCard", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("../../components/ContentItem", () => ({
  __esModule: true,
  default: ({
    content,
    onDelete,
    onRemoveFromList,
  }: {
    content: { id: string; title: string };
    onDelete: (id: string) => void;
    onRemoveFromList: () => void;
  }) => (
    <div data-testid="row">
      {content.title}
      <button onClick={() => onDelete(content.id)}>del-{content.id}</button>
      <button onClick={() => onRemoveFromList()}>rm-{content.id}</button>
    </div>
  ),
}));

class FakeObserver {
  static instances: FakeObserver[] = [];
  observe = jest.fn();
  disconnect = jest.fn();
  constructor(public callback: IntersectionObserverCallback) {
    FakeObserver.instances.push(this);
  }
  static fireLatest() {
    const latest = FakeObserver.instances[FakeObserver.instances.length - 1];
    latest.callback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      latest as unknown as IntersectionObserver,
    );
  }
}

const TOTAL = 120;
const rows = (from: number, count: number) =>
  Array.from({ length: count }, (_, i) => ({
    id: `c${from + i}`,
    title: `Article ${from + i}`,
    original_url: `https://example.com/${from + i}`,
    processing_status: "completed",
  }));

const getContent = listsAPI.getContent as jest.Mock;

beforeEach(() => {
  window.matchMedia = jest.fn().mockReturnValue({
    matches: false,
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
  });
  FakeObserver.instances = [];
  (
    global as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = FakeObserver;
  (listsAPI.getById as jest.Mock).mockResolvedValue({
    id: "list-1",
    name: "Big list",
    description: null,
    owner_id: "u1",
    is_shared: false,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    content_count: TOTAL,
  });
  (draftsAPI.get as jest.Mock).mockRejectedValue(new Error("none"));
  (contentAPI.delete as jest.Mock).mockResolvedValue({});
  getContent.mockReset();
  getContent.mockImplementation(
    async (_id: string, opts?: { skip?: number; limit?: number }) =>
      rows(
        opts?.skip ?? 0,
        Math.min(opts?.limit ?? TOTAL, TOTAL - (opts?.skip ?? 0)),
      ),
  );
});

afterEach(() => {
  delete (global as unknown as { IntersectionObserver?: unknown })
    .IntersectionObserver;
});

const scrollToFooter = async () => {
  await act(async () => FakeObserver.fireLatest());
};

describe("lists page infinite scroll", () => {
  it("loads 50, shows the total, then pages until everything is loaded", async () => {
    render(<ListPage />);

    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(50));
    expect(getContent).toHaveBeenNthCalledWith(1, "list-1", { limit: 50 });
    expect(screen.getByText(/120 items inside/)).toBeInTheDocument();
    expect(screen.getByText("50 of 120")).toBeInTheDocument();

    await scrollToFooter();
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(100));
    expect(getContent).toHaveBeenNthCalledWith(2, "list-1", {
      skip: 50,
      limit: 50,
    });

    await scrollToFooter();
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(120));
    expect(getContent).toHaveBeenNthCalledWith(3, "list-1", {
      skip: 100,
      limit: 50,
    });

    expect(screen.queryByText(/of 120$/)).not.toBeInTheDocument();
    expect(screen.getByText(/120 items inside/)).toBeInTheDocument();
    expect(getContent).toHaveBeenCalledTimes(3);
  });

  it("deleting an item lowers the total and does not refetch", async () => {
    render(<ListPage />);
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(50));

    fireEvent.click(screen.getByText("del-c3"));

    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(49));
    expect(screen.getByText(/119 items inside/)).toBeInTheDocument();
    expect(getContent).toHaveBeenCalledTimes(1);

    // Next page skips by what is loaded now (49), matching the server's shift.
    await scrollToFooter();
    await waitFor(() => expect(getContent).toHaveBeenCalledTimes(2));
    expect(getContent).toHaveBeenNthCalledWith(2, "list-1", {
      skip: 49,
      limit: 50,
    });
  });

  it("a failed page shows Retry, keeps the rows, and retry loads it", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    render(<ListPage />);
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(50));
    getContent.mockRejectedValueOnce(new Error("network"));

    await scrollToFooter();
    await screen.findByText("Couldn't load more items. Try again.");
    expect(screen.getAllByTestId("row")).toHaveLength(50);

    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(100));
  });

  it("a short list shows no footer and never pages", async () => {
    (listsAPI.getById as jest.Mock).mockResolvedValue({
      id: "list-1",
      name: "Small",
      description: null,
      owner_id: "u1",
      is_shared: false,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      content_count: 3,
    });
    getContent.mockResolvedValue(rows(0, 3));

    render(<ListPage />);

    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(3));
    expect(screen.getByText(/3 items inside/)).toBeInTheDocument();
    expect(screen.queryByText(/of 3$/)).not.toBeInTheDocument();
    expect(FakeObserver.instances).toHaveLength(0);
  });

  it("a failed delete restores the row without discarding pages loaded meanwhile", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    let rejectDelete!: (e: Error) => void;
    (contentAPI.delete as jest.Mock).mockReturnValue(
      new Promise((_, reject) => (rejectDelete = reject)),
    );
    render(<ListPage />);
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(50));

    fireEvent.click(screen.getByText("del-c3")); // optimistic removal, delete pending
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(49));
    await scrollToFooter(); // next page (skip 49) lands while the delete is in flight; its first row
    // overlaps the first page and is de-duplicated
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(98));

    await act(async () => rejectDelete(new Error("nope")));

    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(99));
    expect(screen.getByText("Article 3")).toBeInTheDocument();
    expect(screen.getByText("Article 98")).toBeInTheDocument();
    expect(screen.getByText(/120 items inside/)).toBeInTheDocument();
  });

  it("removing from the list keeps pages that load during the removal delay", async () => {
    jest.useFakeTimers();
    try {
      render(<ListPage />);
      await act(async () => {
        await jest.advanceTimersByTimeAsync(0);
      });
      expect(screen.getAllByTestId("row")).toHaveLength(50);

      fireEvent.click(screen.getByText("rm-c3")); // 800 ms delay starts
      await scrollToFooter(); // a page loads during the delay
      expect(screen.getAllByTestId("row")).toHaveLength(100);

      await act(async () => {
        await jest.advanceTimersByTimeAsync(800);
      });

      expect(screen.getAllByTestId("row")).toHaveLength(99);
      expect(screen.queryByText("Article 3")).not.toBeInTheDocument();
      expect(screen.getByText("Article 99")).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });
});
