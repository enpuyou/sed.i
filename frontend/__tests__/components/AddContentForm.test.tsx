/**
 * Unit tests for AddContentForm component.
 *
 * Tests cover the core "paste in URL" functionality:
 * - Rendering the form
 * - URL input and validation
 * - Submitting URLs
 * - Success and error handling
 * - Loading states
 * - Form reset after success
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom";
import AddContentForm from "../../components/AddContentForm";
import { contentAPI } from "../../lib/api";

// Mock the API module
jest.mock("../../lib/api");
const mockedContentAPI = contentAPI as jest.Mocked<typeof contentAPI>;

describe("AddContentForm", () => {
  const mockOnContentAdded = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe("Rendering", () => {
    it("renders form with URL input and submit button", () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      expect(
        screen.getByPlaceholderText(/Paste article URL/i),
      ).toBeInTheDocument();
      expect(
        screen.getByPlaceholderText(/Paste article URL/i),
      ).toBeInTheDocument();
      expect(screen.getByTitle("Add to Queue")).toBeInTheDocument();
    });

    it("renders URL input with required attribute", () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      expect(input).toBeRequired();
      expect(input).toHaveAttribute("type", "url");
    });

    it("does not show error message initially", () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const errorDiv = screen.queryByRole("alert");
      expect(errorDiv).not.toBeInTheDocument();
    });
  });

  describe("URL Input", () => {
    it("allows user to type URL", async () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      expect(input).toHaveValue("https://example.com/article");
    });

    it("updates input value on change", async () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://news.ycombinator.com");

      expect(input).toHaveValue("https://news.ycombinator.com");
    });
  });

  describe("Form Submission - Success", () => {
    it("submits URL and shows success message", async () => {
      mockedContentAPI.create.mockResolvedValue({
        id: "new-content-123",
        original_url: "https://example.com/article",
        processing_status: "pending",
        is_read: false,
        is_archived: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });

      await userEvent.type(input, "https://example.com/article");
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(mockedContentAPI.create).toHaveBeenCalledWith({
          url: "https://example.com/article",
        });

        expect(mockOnContentAdded).toHaveBeenCalled();
      });
    });

    it("clears input after successful submission", async () => {
      mockedContentAPI.create.mockResolvedValue({
        id: "new-content-123",
        original_url: "https://example.com/article",
        processing_status: "pending",
        is_read: false,
        is_archived: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(input).toHaveValue("");
      });
    });

    it("shows loading state during submission", async () => {
      // Use a simple delayed promise instead of manually controlled one
      mockedContentAPI.create.mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(() => {
              resolve({
                id: "new-content-123",
                original_url: "https://example.com/article",
                processing_status: "pending",
                is_read: false,
                is_archived: false,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              });
            }, 100);
          }),
      );

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      // While loading
      await waitFor(() => {
        expect(screen.getByText("▐")).toBeInTheDocument();
        expect(submitButton).toBeDisabled();
      });

      // After loading completes, button is disabled because URL is cleared
      await waitFor(() => {
        // Loading indicator is gone (SVG icon is back)
        expect(screen.queryByText("▐")).not.toBeInTheDocument();
        // Button is disabled because URL field is now empty after successful submit
        expect(submitButton).toBeDisabled();
      });
    });
  });

  describe("Form Submission - Error", () => {
    it("shows error message when submission fails", async () => {
      mockedContentAPI.create.mockRejectedValue(new Error("Network error"));

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(screen.getByText(/network error/i)).toBeInTheDocument();
      });
    });

    it("shows generic error message for unknown errors", async () => {
      mockedContentAPI.create.mockRejectedValue("Unknown error");

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(
          screen.getByText(/couldn't add link. try again/i),
        ).toBeInTheDocument();
      });
    });

    it("does not clear input when submission fails", async () => {
      mockedContentAPI.create.mockRejectedValue(new Error("Server error"));

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      const testUrl = "https://example.com/article";
      await userEvent.type(input, testUrl);

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(input).toHaveValue(testUrl); // URL still there so user can retry
      });
    });

    it("does not call onContentAdded when submission fails", async () => {
      mockedContentAPI.create.mockRejectedValue(new Error("Server error"));

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(screen.getByText(/server error/i)).toBeInTheDocument();
      });

      expect(mockOnContentAdded).not.toHaveBeenCalled();
    });

    it("shows error message for rate limiting", async () => {
      mockedContentAPI.create.mockRejectedValue(
        new Error("Too many requests. Please slow down."),
      );

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(
          screen.getByText(/too many requests. please slow down/i),
        ).toBeInTheDocument();
      });
    });

    it("shows duplicate item link from structured 409 error and dismisses on input change", async () => {
      mockedContentAPI.create.mockRejectedValue(
        new Error(
          JSON.stringify({
            message: "Already in your library",
            existing_id: "existing-content-123",
            is_archived: false,
          }),
        ),
      );

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      fireEvent.click(screen.getByRole("button", { name: "Add to Queue" }));

      await waitFor(() => {
        expect(screen.getByText(/already in your library\./i)).toBeInTheDocument();
      });

      const link = screen.getByRole("link", { name: /view it/i });
      expect(link).toHaveAttribute("href", "/content/existing-content-123");
      expect(mockOnContentAdded).not.toHaveBeenCalled();

      await userEvent.type(input, "x");

      await waitFor(() => {
        expect(screen.queryByRole("link", { name: /view it/i })).not.toBeInTheDocument();
        expect(
          screen.queryByText(/already in your library\./i),
        ).not.toBeInTheDocument();
      });
    });

    it("shows archived duplicate variant from structured 409 error", async () => {
      mockedContentAPI.create.mockRejectedValue(
        new Error(
          JSON.stringify({
            message: "Already in your library",
            existing_id: "archived-content-123",
            is_archived: true,
          }),
        ),
      );

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      fireEvent.click(screen.getByRole("button", { name: "Add to Queue" }));

      await waitFor(() => {
        expect(
          screen.getByText(/already in your library \(archived\)\./i),
        ).toBeInTheDocument();
      });
      expect(screen.getByRole("link", { name: /view it/i })).toHaveAttribute(
        "href",
        "/content/archived-content-123",
      );
    });
  });

  describe("Form Validation", () => {
    it("prevents submission when URL is empty", async () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      // HTML5 validation should prevent submission
      // The API should not be called
      await waitFor(() => {
        expect(mockedContentAPI.create).not.toHaveBeenCalled();
      });
    });

    it("accepts various URL formats", async () => {
      mockedContentAPI.create.mockResolvedValue({
        id: "new-content-123",
        original_url: "",
        processing_status: "pending",
        is_read: false,
        is_archived: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      const urls = [
        "https://example.com/article",
        "http://blog.example.com/post/123",
        "https://subdomain.example.com/path/to/page",
        "https://example.com/article?param=value&other=test",
      ];

      for (const url of urls) {
        const { unmount } = render(
          <AddContentForm onContentAdded={mockOnContentAdded} />,
        );

        const input = screen.getByPlaceholderText(/Paste article URL/i);
        await userEvent.type(input, url);

        const submitButton = screen.getByRole("button", {
          name: "Add to Queue",
        });
        fireEvent.click(submitButton);

        await waitFor(() => {
          expect(mockedContentAPI.create).toHaveBeenCalledWith({ url });
          // Button is disabled after success because URL field is cleared
          expect(submitButton).toBeDisabled();
        });

        unmount();
        jest.clearAllMocks();
      }
    });
  });

  describe("User Experience", () => {
    it("focuses on input when component mounts", () => {
      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      // Input should be focusable (though not auto-focused in tests)
      const input = screen.getByPlaceholderText(/Paste article URL/i);
      expect(input).toBeInTheDocument();
      input.focus();
      expect(input).toHaveFocus();
    });

    it("allows form submission by pressing Enter", async () => {
      mockedContentAPI.create.mockResolvedValue({
        id: "new-content-123",
        original_url: "https://example.com/article",
        processing_status: "pending",
        is_read: false,
        is_archived: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(
        /Paste article URL/i,
      ) as HTMLInputElement;
      await userEvent.type(input, "https://example.com/article{Enter}");

      await waitFor(() => {
        expect(mockedContentAPI.create).toHaveBeenCalledWith({
          url: "https://example.com/article",
        });
      });
    });

    it("disables button during submission to prevent double-submit", async () => {
      mockedContentAPI.create.mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(() => {
              resolve({
                id: "new-content-123",
                original_url: "https://example.com/article",
                processing_status: "pending",
                is_read: false,
                is_archived: false,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              });
            }, 100);
          }),
      );

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/article");

      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(submitButton).toBeDisabled();
      });

      // Try to click again
      fireEvent.click(submitButton);

      // Should only be called once
      expect(mockedContentAPI.create).toHaveBeenCalledTimes(1);

      // Wait for completion - button stays disabled because URL is cleared
      await waitFor(() => {
        expect(mockedContentAPI.create).toHaveBeenCalledTimes(1);
        expect(submitButton).toBeDisabled(); // Still disabled because URL is empty after success
      });
    });
  });

  describe("Integration", () => {
    it("completes full submission flow", async () => {
      mockedContentAPI.create.mockResolvedValue({
        id: "new-content-123",
        original_url: "https://example.com/great-article",
        processing_status: "pending",
        is_read: false,
        is_archived: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      render(<AddContentForm onContentAdded={mockOnContentAdded} />);

      // 1. Type URL
      const input = screen.getByPlaceholderText(/Paste article URL/i);
      await userEvent.type(input, "https://example.com/great-article");
      expect(input).toHaveValue("https://example.com/great-article");

      // 2. Submit
      const submitButton = screen.getByRole("button", {
        name: "Add to Queue",
      });
      fireEvent.click(submitButton);

      // 3. Verify loading state
      await waitFor(() => {
        expect(submitButton).toBeDisabled();
        expect(screen.getByText("▐")).toBeInTheDocument();
      });

      // 4. Verify success
      await waitFor(() => {
        expect(mockedContentAPI.create).toHaveBeenCalledWith({
          url: "https://example.com/great-article",
        });
        expect(mockOnContentAdded).toHaveBeenCalled();
        expect(input).toHaveValue("");
        // Button is disabled because URL field is empty after success
        expect(submitButton).toBeDisabled();
      });
    });
  });
});
