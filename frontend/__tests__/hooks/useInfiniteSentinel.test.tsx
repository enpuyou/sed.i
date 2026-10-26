/**
 * useInfiniteSentinel: fires onVisible when the sentinel scrolls into view.
 *
 * Done when: an intersecting entry calls onVisible; nothing fires while
 * disabled; the observer is disconnected on unmount; changing rearmKey makes a
 * fresh observer (so a sentinel still on screen after new rows fires again);
 * the scroll container is passed as the observer root.
 */

import { render, act } from "@testing-library/react";
import { useInfiniteSentinel } from "../../hooks/useInfiniteSentinel";

class FakeObserver {
  static instances: FakeObserver[] = [];
  observe = jest.fn();
  disconnect = jest.fn();
  constructor(
    public callback: IntersectionObserverCallback,
    public options?: IntersectionObserverInit,
  ) {
    FakeObserver.instances.push(this);
  }
  fire(isIntersecting: boolean) {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

function Harness(props: {
  onVisible: () => void;
  enabled?: boolean;
  rearmKey?: unknown;
  root?: Element | null;
}) {
  const { ref } = useInfiniteSentinel({
    onVisible: props.onVisible,
    enabled: props.enabled ?? true,
    rearmKey: props.rearmKey,
    root: props.root,
  });
  return <div ref={ref} data-testid="sentinel" />;
}

beforeEach(() => {
  FakeObserver.instances = [];
  (
    global as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = FakeObserver;
});

afterEach(() => {
  delete (global as unknown as { IntersectionObserver?: unknown })
    .IntersectionObserver;
});

describe("useInfiniteSentinel", () => {
  it("calls onVisible when the sentinel intersects", () => {
    const onVisible = jest.fn();
    render(<Harness onVisible={onVisible} />);

    act(() => FakeObserver.instances[0].fire(true));

    expect(onVisible).toHaveBeenCalledTimes(1);
  });

  it("ignores non-intersecting entries", () => {
    const onVisible = jest.fn();
    render(<Harness onVisible={onVisible} />);

    act(() => FakeObserver.instances[0].fire(false));

    expect(onVisible).not.toHaveBeenCalled();
  });

  it("does not observe while disabled", () => {
    render(<Harness onVisible={jest.fn()} enabled={false} />);

    expect(FakeObserver.instances).toHaveLength(0);
  });

  it("disconnects on unmount", () => {
    const { unmount } = render(<Harness onVisible={jest.fn()} />);
    const observer = FakeObserver.instances[0];

    unmount();

    expect(observer.disconnect).toHaveBeenCalled();
  });

  it("creates a new observer when rearmKey changes", () => {
    const { rerender } = render(<Harness onVisible={jest.fn()} rearmKey={1} />);
    expect(FakeObserver.instances).toHaveLength(1);

    rerender(<Harness onVisible={jest.fn()} rearmKey={2} />);

    expect(FakeObserver.instances).toHaveLength(2);
    expect(FakeObserver.instances[0].disconnect).toHaveBeenCalled();
  });

  it("calls the latest onVisible without re-observing", () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = render(<Harness onVisible={first} />);
    rerender(<Harness onVisible={second} />);

    act(() => FakeObserver.instances[0].fire(true));

    expect(FakeObserver.instances).toHaveLength(1);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("observes against the given scroll container", () => {
    const container = document.createElement("div");
    render(<Harness onVisible={jest.fn()} root={container} />);

    expect(FakeObserver.instances[0].options?.root).toBe(container);
  });

  it("reports unsupported when IntersectionObserver is missing", () => {
    delete (global as unknown as { IntersectionObserver?: unknown })
      .IntersectionObserver;
    function Probe() {
      const { supported } = useInfiniteSentinel({
        onVisible: jest.fn(),
        enabled: true,
      });
      return <span data-testid="supported">{String(supported)}</span>;
    }

    const { getByTestId } = render(<Probe />);

    expect(getByTestId("supported").textContent).toBe("false");
  });
});
