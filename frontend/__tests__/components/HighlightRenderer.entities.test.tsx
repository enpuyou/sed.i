import { render } from "@testing-library/react";
import "@testing-library/jest-dom";
import React from "react";
import HighlightRenderer from "../../components/HighlightRenderer";

let mockBionic = false;
jest.mock("../../contexts/ReadingSettingsContext", () => ({
  useReadingSettings: () => ({ settings: { bionicReading: mockBionic } }),
}));
jest.mock("../../components/InlineHighlight", () => ({
  __esModule: true,
  default: ({ children, id }: { children: React.ReactNode; id: string }) => (
    <span data-testid={`hl-${id}`}>{children}</span>
  ),
}));

// Source HTML: entities that decode to characters which are special in HTML.
// Text content is: Say <b>bold</b> & &amp; <i>it</i> done. Then more.
const HTML =
  "<p>Say &lt;b&gt;bold&lt;/b&gt; &amp; &amp;amp; &lt;i&gt;it&lt;/i&gt; done.</p><p>Then more.</p>";

const domText = () =>
  document.getElementById("article-content")!.textContent || "";

// Text of the DOM the browser builds from the source HTML: what the reader
// computes selection offsets against.
const sourceText = () => {
  const d = document.createElement("div");
  d.innerHTML = HTML;
  return d.textContent || "";
};

describe.each([false, true])("HighlightRenderer entities (bionic=%s)", (b) => {
  beforeEach(() => {
    mockBionic = b;
  });

  it("keeps text identical when a highlight contains < > &", () => {
    const text = sourceText();
    const start = text.indexOf("<b>");
    const end = text.indexOf(" done");
    const { container } = render(
      <HighlightRenderer
        html={HTML}
        highlights={[
          {
            id: "h1",
            text: text.slice(start, end),
            start_offset: start,
            end_offset: end,
            color: "yellow",
          },
        ]}
      />,
    );
    expect(domText()).toBe(text);
    expect(container.querySelector("b, i")).toBeNull();
    expect(domText().slice(start, end)).toBe(text.slice(start, end));
  });

  it("keeps later highlights aligned", () => {
    const text = sourceText();
    const start = text.indexOf("Then");
    const end = start + "Then more".length;
    render(
      <HighlightRenderer
        html={HTML}
        highlights={[
          {
            id: "h2",
            text: "Then more",
            start_offset: start,
            end_offset: end,
            color: "yellow",
          },
        ]}
      />,
    );
    expect(domText()).toBe(text);
    expect(document.querySelector("[data-testid=hl-h2]")?.textContent).toBe(
      "Then more",
    );
  });

  it("keeps text identical when the highlight is absent (bionic wrap only)", () => {
    render(<HighlightRenderer html={HTML} highlights={[]} />);
    expect(domText()).toBe(sourceText());
  });
});
