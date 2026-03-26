import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

export const FocusModeKey = new PluginKey("focusMode");

/**
 * Focus Mode Tiptap Extension
 * Dims all block nodes except the one containing the cursor.
 * Toggle by calling editor.setOptions({ extensions: [..., FocusMode.configure({ enabled: true })] })
 * or more simply, track in React state and recreate the editor — but in practice we use
 * a transaction meta flag to toggle without recreating.
 */
export const FocusMode = Extension.create({
    name: "focusMode",

    addOptions() {
        return {
            enabled: false,
        };
    },

    addProseMirrorPlugins() {
        // Capture options reference so the plugin state can read it
        const ext = this;

        return [
            new Plugin({
                key: FocusModeKey,
                state: {
                    init(_, state) {
                        return buildDecorations(state, ext.options.enabled);
                    },
                    apply(tr, oldDecorations, _oldState, newState) {
                        // Check if toggled via transaction meta
                        const meta = tr.getMeta(FocusModeKey);
                        const enabled = meta !== undefined ? meta : ext.options.enabled;
                        if (!tr.docChanged && !tr.selectionSet && meta === undefined) {
                            return oldDecorations;
                        }
                        return buildDecorations(newState, enabled);
                    },
                },
                props: {
                    decorations(state) {
                        return this.getState(state);
                    },
                },
            }),
        ];
    },
});

/**
 * Helper: dispatch a transaction to toggle focus mode on the running editor.
 * Called from React: toggleFocusMode(editor, true/false)
 */
export function toggleFocusMode(
    editor: import("@tiptap/core").Editor,
    enabled: boolean
): void {
    editor.view.dispatch(
        editor.state.tr.setMeta(FocusModeKey, enabled)
    );
}

function buildDecorations(
    state: import("@tiptap/pm/state").EditorState,
    enabled: boolean
): DecorationSet {
    if (!enabled) return DecorationSet.empty;

    const { doc, selection } = state;
    const { $from } = selection;
    const decorations: Decoration[] = [];

    // Find the top-level block node containing the cursor
    const activePosStart = $from.before(Math.min($from.depth, 1) || 1);

    doc.forEach((node, pos) => {
        if (pos !== activePosStart) {
            decorations.push(
                Decoration.node(pos, pos + node.nodeSize, { class: "focus-dimmed" })
            );
        }
    });

    return DecorationSet.create(doc, decorations);
}
