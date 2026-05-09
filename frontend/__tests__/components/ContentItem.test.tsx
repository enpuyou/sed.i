/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Unit tests for ContentItem component.
 *
 * Tests cover content display and interaction:
 * - Rendering content information
 * - Status badges (read/unread, archived)
 * - Mark as read/unread functionality
 * - Archive/unarchive functionality
 * - Tag management (add/remove tags)
 * - Delete confirmation
 * - Add to list functionality
 * - Processing status display
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom";
import ContentItem from "../../components/ContentItem";
import { ContentItem as ContentItemType } from "../../types";
import { contentAPI } from "../../lib/api";

// Mock Next.js Link component
jest.mock("next/link", () => {
  const MockLink = ({ children, href }: any) => {
    return <a href={href}>{children}</a>;
  };
  MockLink.displayName = "MockLink";
  return MockLink;
});

// Mock Next.js navigation hooks
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: jest.fn(),
    replace: jest.fn(),
    prefetch: jest.fn(),
  }),
  useSearchParams: () => ({
    get: jest.fn(),
  }),
  usePathname: () => "/",
}));

// Mock the API module
jest.mock("../../lib/api");
const mockedContentAPI = contentAPI as jest.Mocked<typeof contentAPI>;

// Mock AuthContext
jest.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "test-user-123", username: "testuser", is_public: true },
    isAuthenticated: true,
  }),
}));

describe("ContentItem", () => {
  const mockOnStatusChange = jest.fn();
  const mockOnDelete = jest.fn();
  const mockOnRemoveFromList = jest.fn();
  const mockOnAddToList = jest.fn();

  const mockContent: ContentItemType = {
    id: "test-content-123",
    user_id: "test-user-123",
    content_type: "article",
    full_text: "Full text content...",
    word_count: 100,
    original_url: "https://example.com/article",
    title: "Test Article Title",
    description: "This is a test article description",
    content_vertical: "technology",
    author: "Test Author",
    published_date: "2024-01-01T00:00:00Z",
    is_public: false,

    summary: null,
    auto_tags: [],
    read_position: 0,
    is_read: false,
    is_archived: false,
    reading_status: "unread",
    processing_status: "completed",
    created_at: new Date("2024-01-15T10:00:00Z").toISOString(),
    updated_at: new Date("2024-01-15T10:00:00Z").toISOString(),
    reading_time_minutes: 5,
    tags: ["javascript", "testing"],
    thumbnail_url: "https://example.com/thumb.jpg",
  };

  beforeEach(() => {
    jest.clearAllMocks();
    mockedContentAPI.getTags.mockResolvedValue([]);
  });

  describe("Rendering - Basic Information", () => {
    it("renders content title", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByText("Test Article Title")).toBeInTheDocument();
    });

    it("renders description when available", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(
        screen.getByText("This is a test article description"),
      ).toBeInTheDocument();
    });

    it('shows "Untitled" when no title provided', () => {
      const contentWithoutTitle = { ...mockContent, title: null };

      render(
        <ContentItem
          content={contentWithoutTitle}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByText("Untitled")).toBeInTheDocument();
    });

    it("renders thumbnail when available", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const thumbnail = screen.getByRole("img");
      expect(thumbnail).toHaveAttribute("src", "https://example.com/thumb.jpg");
    });

    it("does not render thumbnail when not available", () => {
      const contentWithoutThumbnail = {
        ...mockContent,
        thumbnail_url: null,
      };

      render(
        <ContentItem
          content={contentWithoutThumbnail}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    });

    it("shows reading time when available", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByText(/5 min read/)).toBeInTheDocument();
    });

    it("links to reader view", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const link = screen.getByRole("link");
      expect(link).toHaveAttribute("href", "/content/test-content-123");
    });
  });

  describe("Status Badges", () => {
    it("shows Unread status indicator when not read", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByTitle("Unread")).toBeInTheDocument();
    });

    it("shows Read status indicator when read", () => {
      const readContent = {
        ...mockContent,
        is_read: true,
        reading_status: "read" as const,
      };

      render(
        <ContentItem
          content={readContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByTitle("Read")).toBeInTheDocument();
    });

    it("shows Archived status indicator when archived", () => {
      const archivedContent = {
        ...mockContent,
        is_archived: true,
        reading_status: "archived" as const,
      };

      render(
        <ContentItem
          content={archivedContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByTitle("Archived")).toBeInTheDocument();
    });

    it("prioritizes archived status over read status", () => {
      const archivedReadContent = {
        ...mockContent,
        is_read: true,
        is_archived: true,
        reading_status: "archived" as const,
      };

      render(
        <ContentItem
          content={archivedReadContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByTitle("Archived")).toBeInTheDocument();
      // Read indicator should not be present when archived
      expect(screen.queryByTitle("Read")).not.toBeInTheDocument();
    });
  });

  describe("Mark as Read/Unread", () => {
    it("calls onStatusChange when marking as read", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const readButton = screen.getByTitle("Mark as read");
      fireEvent.click(readButton);

      expect(mockOnStatusChange).toHaveBeenCalledWith("test-content-123", {
        is_read: true,
      });
    });

    it("calls onStatusChange when marking as unread", () => {
      const readContent = { ...mockContent, is_read: true };

      render(
        <ContentItem
          content={readContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const unreadButton = screen.getByTitle("Mark as unread");
      fireEvent.click(unreadButton);

      expect(mockOnStatusChange).toHaveBeenCalledWith("test-content-123", {
        is_read: false,
      });
    });
  });

  describe("Archive/Unarchive", () => {
    it("calls onStatusChange when archiving", async () => {
      jest.useFakeTimers();
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const archiveButton = screen.getByTitle("Archive");
      fireEvent.click(archiveButton);

      // Fast-forward time by 800ms (retro effect delay)
      jest.advanceTimersByTime(800);

      expect(mockOnStatusChange).toHaveBeenCalledWith("test-content-123", {
        is_archived: true,
      });

      jest.useRealTimers();
    });

    it("calls onStatusChange when unarchiving", () => {
      const archivedContent = { ...mockContent, is_archived: true };

      render(
        <ContentItem
          content={archivedContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const unarchiveButton = screen.getByTitle("Unarchive");
      fireEvent.click(unarchiveButton);

      expect(mockOnStatusChange).toHaveBeenCalledWith("test-content-123", {
        is_archived: false,
      });
    });
  });

  describe("Tag Management", () => {
    it("displays existing tags", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByText("javascript")).toBeInTheDocument();
      expect(screen.getByText("testing")).toBeInTheDocument();
    });

    it('shows "+ Tag" button to add new tags', () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.getByText("+ Tag")).toBeInTheDocument();
    });

    it('enters edit mode when "+ Tag" clicked', () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      expect(screen.getByPlaceholderText("Add tag...")).toBeInTheDocument();
      expect(screen.getByText("Add")).toBeInTheDocument();
      expect(screen.getByText("Done")).toBeInTheDocument();
    });

    it("shows remove buttons for tags in edit mode", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      // Should show × button for each tag
      const removeButtons = screen.getAllByText("×");
      expect(removeButtons.length).toBeGreaterThan(0);
    });

    it("adds a new tag when user types and clicks Add", async () => {
      mockedContentAPI.update.mockResolvedValue({
        ...mockContent,
        tags: ["javascript", "testing", "react"],
      });

      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Enter edit mode
      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      // Type new tag
      const input = screen.getByPlaceholderText("Add tag...");
      await userEvent.type(input, "react");

      // Click Add
      const addButton = screen.getByRole("button", { name: /^add$/i });
      fireEvent.click(addButton);

      await waitFor(() => {
        expect(mockedContentAPI.update).toHaveBeenCalledWith(
          "test-content-123",
          {
            tags: ["javascript", "testing", "react"],
          },
        );
      });
    });

    it("prevents adding duplicate tags", async () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Enter edit mode
      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      // Try to add existing tag
      const input = screen.getByPlaceholderText("Add tag...");
      await userEvent.type(input, "javascript");

      const addButton = screen.getByRole("button", { name: /^add$/i });
      fireEvent.click(addButton);

      await waitFor(() => {
        expect(mockedContentAPI.update).not.toHaveBeenCalled();
      });
    });

    it("removes a tag when × button clicked", async () => {
      mockedContentAPI.update.mockResolvedValue({
        ...mockContent,
        tags: ["testing"],
      });

      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Enter edit mode
      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      // Find and click × for 'javascript' tag
      const removeButtons = screen.getAllByText("×");
      fireEvent.click(removeButtons[0]); // Click first ×

      await waitFor(() => {
        expect(mockedContentAPI.update).toHaveBeenCalledWith(
          "test-content-123",
          {
            tags: ["testing"],
            auto_tags: [],
          },
        );
      });
    });

    it("exits edit mode when Done clicked", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Enter edit mode
      const addTagButton = screen.getByText("+ Tag");
      fireEvent.click(addTagButton);

      // Click Done
      const doneButton = screen.getByText("Done");
      fireEvent.click(doneButton);

      // Should exit edit mode
      expect(
        screen.queryByPlaceholderText("Add tag..."),
      ).not.toBeInTheDocument();
    });
  });

  describe("Delete Functionality", () => {
    it("requires confirmation to delete", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const deleteButton = screen.getByTitle("Delete article");
      fireEvent.click(deleteButton);

      // Button text should change to "Confirm?"
      expect(screen.getByText("Confirm?")).toBeInTheDocument();
      expect(mockOnDelete).not.toHaveBeenCalled();
    });

    it("calls onDelete when deletion confirmed", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const deleteButton = screen.getByTitle("Delete article");
      fireEvent.click(deleteButton);

      // Click again to confirm
      fireEvent.click(screen.getByText("Confirm?"));

      expect(mockOnDelete).toHaveBeenCalledWith("test-content-123");
    });

    it("closes confirmation when cancelled via mobile menu", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      const deleteButton = screen.getByTitle("Delete article");
      fireEvent.click(deleteButton);

      // Verify confirm state
      expect(screen.getByText("Confirm?")).toBeInTheDocument();

      // Find and click the mobile cancel button
      const cancelButton = screen.getByText("cancel");
      fireEvent.click(cancelButton);

      // Should revert state
      expect(screen.getByText("Delete")).toBeInTheDocument();
      expect(screen.queryByText("Confirm?")).not.toBeInTheDocument();
      expect(mockOnDelete).not.toHaveBeenCalled();
    });
  });

  describe("Add to List", () => {
    const mockLists = [
      { id: "list-1", name: "Reading List" },
      { id: "list-2", name: "Tech Articles" },
      { id: "list-3", name: "Favorites" },
    ];

    it("shows add to list button when lists provided", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
          availableLists={mockLists}
          onAddToList={mockOnAddToList}
        />,
      );

      const addToListButton = screen.getByTitle("Add to list");
      expect(addToListButton).toBeInTheDocument();
    });

    it("does not show add to list button when no lists", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      expect(screen.queryByTitle("Add to list")).not.toBeInTheDocument();
    });

    it("shows list dropdown when add to list clicked", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
          availableLists={mockLists}
          onAddToList={mockOnAddToList}
        />,
      );

      const addToListButton = screen.getByTitle("Add to list");
      fireEvent.click(addToListButton);

      expect(screen.getByText("Reading List")).toBeInTheDocument();
      expect(screen.getByText("Tech Articles")).toBeInTheDocument();
      expect(screen.getByText("Favorites")).toBeInTheDocument();
    });

    it("calls onAddToList when list selected", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
          availableLists={mockLists}
          onAddToList={mockOnAddToList}
        />,
      );

      // Open dropdown
      const addToListButton = screen.getByTitle("Add to list");
      fireEvent.click(addToListButton);

      // Select a list
      const listButton = screen.getByText("Tech Articles");
      fireEvent.click(listButton);

      expect(mockOnAddToList).toHaveBeenCalledWith("list-2");
    });
  });

  describe("Remove from List", () => {
    it("shows remove from list button when onRemoveFromList provided", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
          onRemoveFromList={mockOnRemoveFromList}
        />,
      );

      const removeButton = screen.getByTitle("Remove from list");
      expect(removeButton).toBeInTheDocument();
    });

    it("calls onRemoveFromList when clicked", () => {
      render(
        <ContentItem
          content={mockContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
          onRemoveFromList={mockOnRemoveFromList}
        />,
      );

      const removeButton = screen.getByTitle("Remove from list");
      fireEvent.click(removeButton);

      expect(mockOnRemoveFromList).toHaveBeenCalled();
    });
  });

  describe("Date Formatting", () => {
    it("shows 'Just now' for recently added content (within 10 minutes)", () => {
      const recentContent = {
        ...mockContent,
        created_at: new Date().toISOString(), // Just created
      };

      render(
        <ContentItem
          content={recentContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Should show "Just now" for content added within 10 minutes
      expect(screen.getByText(/just now/i)).toBeInTheDocument();
    });

    it("shows formatted date for older content (more than 10 minutes old)", () => {
      // Content created 1 day ago
      const oneDayAgo = new Date();
      oneDayAgo.setDate(oneDayAgo.getDate() - 1);

      const olderContent = {
        ...mockContent,
        created_at: oneDayAgo.toISOString(),
      };

      render(
        <ContentItem
          content={olderContent}
          onStatusChange={mockOnStatusChange}
          onDelete={mockOnDelete}
        />,
      );

      // Should show "Yesterday" for content from yesterday
      expect(screen.getByText(/yesterday/i)).toBeInTheDocument();
    });
  });
});
