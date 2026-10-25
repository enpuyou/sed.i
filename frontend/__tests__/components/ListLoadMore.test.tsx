/**
 * ListLoadMore: footer under a paged list.
 *
 * Done when: it shows "<loaded> of <total>", a loading state, an inline error
 * with Retry that re-requests the page, and a Load more button only where
 * IntersectionObserver is unavailable.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import ListLoadMore from "../../components/ListLoadMore";

const base = {
  loaded: 50,
  total: 213,
  loading: false,
  error: null,
  sentinelRef: jest.fn(),
  observerSupported: true,
  onLoadMore: jest.fn(),
};

beforeEach(() => jest.clearAllMocks());

describe("ListLoadMore", () => {
  it("shows how much is loaded", () => {
    render(<ListLoadMore {...base} />);

    expect(screen.getByText("50 of 213")).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    const { container } = render(<ListLoadMore {...base} loading />);

    expect(screen.getByText(/Loading more/)).toBeInTheDocument();
    expect(
      container.querySelectorAll('[aria-hidden="true"] > div'),
    ).toHaveLength(2);
  });

  it("shows the error with Retry that requests the page again", () => {
    render(
      <ListLoadMore {...base} error="Couldn't load more items. Try again." />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load more items.",
    );
    fireEvent.click(screen.getByText("Retry"));

    expect(base.onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("has no button when the observer is supported", () => {
    render(<ListLoadMore {...base} />);

    expect(screen.queryByText("Load more")).not.toBeInTheDocument();
  });

  it("offers a Load more button when the observer is unsupported", () => {
    render(<ListLoadMore {...base} observerSupported={false} />);

    fireEvent.click(screen.getByText("Load more"));

    expect(base.onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("hides the button while loading", () => {
    render(<ListLoadMore {...base} observerSupported={false} loading />);

    expect(screen.queryByText("Load more")).not.toBeInTheDocument();
  });
});
