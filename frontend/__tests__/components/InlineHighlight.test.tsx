import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import InlineHighlight from "../../components/InlineHighlight";
import { searchAPI } from "../../lib/api";

// Mock flags
jest.mock("../../lib/flags", () => ({
  SHOW_HIGHLIGHT_CONNECTIONS: true,
}));

// Mock APIs
jest.mock("../../lib/api", () => ({
  highlightsAPI: {
    saveNote: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue({}),
  },
  searchAPI: {
    findHighlightConnections: jest.fn().mockResolvedValue([]),
  },
}));

// Mock window.getSelection
const mockGetSelection = jest.fn().mockImplementation(() => ({
  removeAllRanges: jest.fn(),
  toString: () => "",
}));
Object.defineProperty(window, "getSelection", { value: mockGetSelection });

describe("InlineHighlight", () => {
  const defaultProps = {
    id: "h1",
    color: "yellow",
    initialNote: "",
    onUpdateNote: jest.fn(),
    onDelete: jest.fn(),
    onShowConnections: jest.fn(),
    hasConnections: false,
    showIndicators: true,
    showConnectionIndicator: true,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    (searchAPI.findHighlightConnections as jest.Mock).mockResolvedValue([]);
  });

  it("renders children correctly", () => {
    render(<InlineHighlight {...defaultProps}>Highlight text</InlineHighlight>);
    expect(screen.getByText("Highlight text")).toBeInTheDocument();
  });

  it("opens editor when clicked (with initialNote)", async () => {
    render(
      <InlineHighlight {...defaultProps} initialNote="Existing note">
        Click me
      </InlineHighlight>,
    );
    const text = screen.getByText("Click me");

    // Explicitly click the span
    fireEvent.click(text);

    // In JSDOM, we wait for the subsequent render
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Write a note.../i),
      ).toBeInTheDocument();
    });
  });

  it("shows note indicator when initialNote exists", () => {
    render(
      <InlineHighlight {...defaultProps} initialNote="Test note">
        Text
      </InlineHighlight>,
    );
    const indicators = document.querySelectorAll(".ephemeral-ui");
    expect(indicators.length).toBeGreaterThan(0);
  });

  it("shows connection indicator when facilitated and feature enabled", async () => {
    render(
      <InlineHighlight {...defaultProps} hasConnections={true}>
        Text
      </InlineHighlight>,
    );

    await waitFor(() => {
      expect(
        screen.getByTitle(/This highlight has connections/i),
      ).toBeInTheDocument();
    });
  });

  it("calls onShowConnections when blue dot is clicked", async () => {
    const onShowConnections = jest.fn();

    render(
      <InlineHighlight
        {...defaultProps}
        hasConnections={true}
        onShowConnections={onShowConnections}
      >
        Text
      </InlineHighlight>,
    );

    const dot = await screen.findByTitle(/This highlight has connections/i);
    fireEvent.click(dot);
    expect(onShowConnections).toHaveBeenCalledWith("h1");
  });

  it("can control editor state via isOpen prop", async () => {
    const onToggle = jest.fn();
    const { rerender } = render(
      <InlineHighlight {...defaultProps} isOpen={true} onToggle={onToggle}>
        Text
      </InlineHighlight>,
    );

    // Editor should be open (controlled)
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Write a note.../i),
      ).toBeInTheDocument();
    });

    // Close via controlled prop
    rerender(
      <InlineHighlight {...defaultProps} isOpen={false} onToggle={onToggle}>
        Text
      </InlineHighlight>,
    );

    // Editor should be closed
    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText(/Write a note.../i),
      ).not.toBeInTheDocument();
    });
  });
});
