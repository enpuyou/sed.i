import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";

const TypewriterScrollingKey = new PluginKey("typewriterScrolling");

/**
 * Typewriter Scrolling Tiptap Extension
 * Keeps the cursor at ~40% from the top of the editor container
 * as the user types — eyes stay in one place, text flows beneath.
 */
export const TypewriterScrolling = Extension.create({
  name: "typewriterScrolling",

  addOptions() {
    return {
      enabled: true,
      // Fraction from top of viewport where cursor should sit (0.4 = 40%)
      offset: 0.4,
    };
  },

  addProseMirrorPlugins() {
    const options = this.options;

    return [
      new Plugin({
        key: TypewriterScrollingKey,
        view(editorView) {
          return {
            update(view, prevState) {
              if (!options.enabled) return;

              // Only scroll when the doc changed (user typed), not on
              // selection-only changes (clicks, arrow keys, mouse drags).
              if (!prevState || prevState.doc.eq(view.state.doc)) {
                return;
              }

              const { from } = view.state.selection;
              const coords = view.coordsAtPos(from);
              if (!coords) return;

              const editorDom = view.dom.closest(
                ".typewriter-scroll-container",
              ) as HTMLElement | null;
              if (!editorDom) return;

              const containerRect = editorDom.getBoundingClientRect();
              const targetY =
                containerRect.top + containerRect.height * options.offset;
              const cursorY = coords.top;
              const delta = cursorY - targetY;

              if (Math.abs(delta) > 5) {
                editorDom.scrollBy({ top: delta, behavior: "smooth" });
              }
            },
          };
        },
      }),
    ];
  },
});
