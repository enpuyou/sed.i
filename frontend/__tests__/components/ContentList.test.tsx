/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Unit tests for ContentList component.
 *
 * Tests cover loading states and content management:
 * - RetroLoader displays while fetching
 * - Content renders after fetch completes
 * - Scroll position is restored after content loads
 * - Filter changes trigger content refresh
 */

import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import React from "react";
import ContentList from "../../components/ContentList";
import { contentAPI, listsAPI } from "../../lib/api";

// Mock Next.js navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: jest.fn(),
    replace: jest.fn(),
    prefetch: jest.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard",
}));

// Mock API module
jest.mock("../../lib/api");
const mockedContentAPI = contentAPI as jest.Mocked<typeof contentAPI>;
const mockedListsAPI = listsAPI as jest.Mocked<typeof listsAPI>;

// Mock child components
jest.mock("../../components/ContentItem", () => ({
  __esModule: true,
  default: ({ content }: any) => (
    <div data-testid={`content-item-${content.id}`}>{content.title}</div>
  ),
}));

jest.mock("../../components/ContentIndexItem", () => ({
  __esModule: true,
  default: ({ content }: any) => (
    <div data-testid={`index-item-${content.id}`}>{content.title}</div>
  ),
}));

jest.mock("../../components/ContentCard", () => ({
  __esModule: true,
  default: ({ content }: any) => (
    <div data-testid={`card-${content.id}`}>{content.title}</div>
  ),
}));

jest.mock("../../components/RetroLoader", () => ({
  __esModule: true,
  default: ({ text }: any) => <div data-testid="retro-loader">{text}</div>,
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

jest.mock("../../hooks/useHotkeys", () => ({
  useHotkeys: jest.fn(),
}));

jest.mock("../../components/FilterDropdownContent", () => ({
  FilterDropdownContent: () => <div data-testid="filter-dropdown">Filter</div>,
}));

// Mock contexts
jest.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "test-user", username: "testuser" },
    isAuthenticated: true,
  }),
}));

const mockContents = [
  {
    id: "1",
    title: "Article 1",
    description: "Test article 1",
    source: "Example.com",
    original_url: "https://example.com/1",
    created_at: "2024-01-01T10:00:00Z",
    reading_status: "unread" as const,
    is_archived: false,
    is_public: false,
    tags: [],
    author: "Author 1",
    content_type: "article" as const,
    full_text: "Content 1",
    word_count: 100,
    content_vertical: "tech" as const,
    published_date: "2024-01-01T10:00:00Z",
    is_read: false,
    processing_status: "completed" as const,
    updated_at: "2024-01-01T10:00:00Z",
    reading_time_minutes: 5,
    thumbnail_url: "https://example.com/thumb1.jpg",
    user_id: "test-user",
  },
  {
    id: "2",
    title: "Article 2",
    description: "Test article 2",
    source: "Example.com",
    original_url: "https://example.com/2",
    created_at: "2024-01-02T10:00:00Z",
    reading_status: "read" as const,
    is_archived: false,
    is_public: false,
    tags: [],
    author: "Author 2",
    content_type: "article" as const,
    full_text: "Content 2",
    word_count: 150,
    content_vertical: "tech" as const,
    published_date: "2024-01-02T10:00:00Z",
    is_read: true,
    processing_status: "completed" as const,
    updated_at: "2024-01-02T10:00:00Z",
    reading_time_minutes: 7,
    thumbnail_url: "https://example.com/thumb2.jpg",
    user_id: "test-user",
  },
];

describe("ContentList", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();

    // Setup default API mocks
    mockedContentAPI.getAll.mockResolvedValue({
      items: mockContents,
      total: 2,
      skip: 0,
      limit: 100,
    });
    mockedListsAPI.getAll.mockResolvedValue([]);
    mockedContentAPI.getTags.mockResolvedValue([]);
  });

  describe("Loading", () => {
    it("displays RetroLoader while fetching", async () => {
      render(<ContentList ref={React.createRef()} />);

      // Loader should be visible initially
      expect(screen.getByTestId("retro-loader")).toBeInTheDocument();
      expect(screen.getByText("Finding your articles")).toBeInTheDocument();

      // Wait for content to load and replace loader
      await waitFor(() => {
        expect(screen.queryByTestId("retro-loader")).not.toBeInTheDocument();
      });

      expect(screen.getByTestId("content-item-1")).toBeInTheDocument();
    });

    it("always calls contentAPI.getAll on mount", async () => {
      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(screen.getByTestId("content-item-1")).toBeInTheDocument();
      });

      expect(mockedContentAPI.getAll).toHaveBeenCalledTimes(1);
    });
  });

  describe("Scroll Restoration", () => {
    it("restores scroll position after content loads", async () => {
      sessionStorage.setItem("contentListScrollPos", "500");

      const scrollSpy = jest.spyOn(window, "scrollTo").mockImplementation();

      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(screen.getByTestId("content-item-1")).toBeInTheDocument();
      });

      // Verify scroll was called with saved position
      expect(scrollSpy).toHaveBeenCalledWith(0, 500);

      // Verify scroll position was cleared
      expect(sessionStorage.getItem("contentListScrollPos")).toBeNull();

      scrollSpy.mockRestore();
    });

    it("clears scroll position after restoring", async () => {
      sessionStorage.setItem("contentListScrollPos", "250");

      jest.spyOn(window, "scrollTo").mockImplementation();

      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(screen.getByTestId("content-item-1")).toBeInTheDocument();
      });

      // Scroll position should be cleared after restoration
      expect(sessionStorage.getItem("contentListScrollPos")).toBeNull();
    });
  });

  describe("Content Management", () => {
    it("prepends new item to the list when added via ref", async () => {
      const ref = React.createRef<any>();
      render(<ContentList ref={ref} />);

      await waitFor(() => {
        expect(screen.getByTestId("content-item-1")).toBeInTheDocument();
      });

      const newItem = {
        ...mockContents[0],
        id: "3",
        title: "New Article",
      };

      ref.current?.addNewItem(newItem);

      // New item should appear in the list
      await waitFor(() => {
        expect(screen.getByTestId("content-item-3")).toBeInTheDocument();
      });
    });

    it("displays empty state when no content exists", async () => {
      mockedContentAPI.getAll.mockResolvedValue({
        items: [],
        total: 0,
        skip: 0,
        limit: 100,
      });

      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(screen.getByText(/No content yet/)).toBeInTheDocument();
      });

      expect(screen.queryByTestId("retro-loader")).not.toBeInTheDocument();
    });
  });

  describe("API Integration", () => {
    it("calls contentAPI.getAll on mount", async () => {
      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(mockedContentAPI.getAll).toHaveBeenCalled();
      });
    });

    it("calls listsAPI.getAll on mount", async () => {
      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(mockedListsAPI.getAll).toHaveBeenCalled();
      });
    });

    it("handles API errors gracefully", async () => {
      const consoleErrorSpy = jest.spyOn(console, "error").mockImplementation();
      mockedContentAPI.getAll.mockRejectedValue(new Error("API Error"));

      render(<ContentList ref={React.createRef()} />);

      await waitFor(() => {
        expect(
          screen.getByText(/Couldn't load your content\. Try again\./),
        ).toBeInTheDocument();
      });

      consoleErrorSpy.mockRestore();
    });
  });
});
