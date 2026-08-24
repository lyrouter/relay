<script setup lang="ts">
/**
 * LOG-1 · the dual-mode editor with a live split preview.
 *
 * CodeMirror 6, and three decisions that are the whole difference between this and
 * a `<textarea>` with a preview pane:
 *
 * **1 · One editor, two modes.** Markdown and plain text differ by *language
 * extension*, swapped through a compartment — not by unmounting the editor. The
 * user's cursor, selection and undo history survive the toggle, because switching
 * mode is a change of mind about formatting, not a reason to lose your place.
 *
 * **2 · The preview is debounced separately from the autosave.** Rendering runs on
 * a short timer (readability), saving on a longer one (LOG-4's version stream).
 * Tying them together would mint a version per keystroke or make the preview lag
 * behind by seconds; both were tried in editors everybody has used and disliked.
 *
 * **3 · Paste-to-upload for images.** An investigation record without its
 * screenshot is half a record, so a pasted image is uploaded (LOG-5) and its
 * markdown inserted at the cursor. The URL is a short-lived signed link fetched at
 * render time — never stored in the body, which would put a five-minute
 * credential into permanent text.
 */
import { markdown as markdownLanguage } from "@codemirror/lang-markdown";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, placeholder } from "@codemirror/view";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { LogFormat } from "@/api/types";

import MarkdownView from "./MarkdownView.vue";

const props = defineProps<{
  modelValue: string;
  format: LogFormat;
  /** Shown when the document is empty; a template's prompt lives here. */
  hint?: string;
  readOnly?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  /** A pasted or dropped image. The parent owns the upload (it has the log id). */
  image: [file: File, insert: (markdown: string) => void];
}>();

const host = ref<HTMLElement | null>(null);
const previewSource = ref(props.modelValue);
let view: EditorView | null = null;
let previewTimer: number | undefined;

/** Language is swapped in place — see decision 1. */
const language = new Compartment();
const readOnlyCompartment = new Compartment();

/** Preview only; the save debounce is the store's, and longer. */
const PREVIEW_DELAY_MS = 150;

function extensionsFor(format: LogFormat) {
  // Plain text gets no language extension at all: `format: "plain"` means the
  // author does not want their asterisks interpreted, and a markdown parser that
  // "helpfully" styles them is the bug.
  return format === "markdown" ? [markdownLanguage()] : [];
}

function insertAtCursor(text: string): void {
  if (!view) return;
  const { from, to } = view.state.selection.main;
  view.dispatch({
    changes: { from, to, insert: text },
    selection: { anchor: from + text.length },
  });
}

onMounted(() => {
  if (!host.value) return;
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        lineNumbers(),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        placeholder(props.hint ?? "开始写。"),
        EditorView.lineWrapping,
        language.of(extensionsFor(props.format)),
        readOnlyCompartment.of(EditorState.readOnly.of(props.readOnly ?? false)),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          const value = update.state.doc.toString();
          // Emitted immediately so the parent's autosave timer starts from the
          // keystroke, not from the preview render.
          emit("update:modelValue", value);
          if (previewTimer !== undefined) window.clearTimeout(previewTimer);
          previewTimer = window.setTimeout(() => {
            previewSource.value = value;
          }, PREVIEW_DELAY_MS);
        }),
        EditorView.domEventHandlers({
          paste(event) {
            const file = imageFrom(event.clipboardData);
            if (!file) return false;
            event.preventDefault();
            emit("image", file, insertAtCursor);
            return true;
          },
          drop(event) {
            const file = imageFrom(event.dataTransfer);
            if (!file) return false;
            event.preventDefault();
            emit("image", file, insertAtCursor);
            return true;
          },
        }),
      ],
    }),
  });
});

function imageFrom(data: DataTransfer | null): File | null {
  const item = Array.from(data?.files ?? []).find((one) => one.type.startsWith("image/"));
  return item ?? null;
}

onBeforeUnmount(() => {
  if (previewTimer !== undefined) window.clearTimeout(previewTimer);
  view?.destroy();
  view = null;
});

watch(
  () => props.format,
  (format) => {
    view?.dispatch({ effects: language.reconfigure(extensionsFor(format)) });
  },
);

watch(
  () => props.readOnly,
  (readOnly) => {
    view?.dispatch({
      effects: readOnlyCompartment.reconfigure(EditorState.readOnly.of(readOnly ?? false)),
    });
  },
);

/**
 * External replacement — a template, or a rollback.
 *
 * Guarded on the current document so that echoing the parent's `v-model` back
 * does not reset the cursor on every keystroke.
 */
watch(
  () => props.modelValue,
  (value) => {
    if (!view || value === view.state.doc.toString()) return;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    });
    previewSource.value = value;
  },
);
</script>

<template>
  <div class="log-editor">
    <div class="log-editor__pane log-editor__source">
      <div ref="host" class="log-editor__cm" />
    </div>
    <div class="log-editor__pane log-editor__preview">
      <MarkdownView :source="previewSource" :format="props.format" />
    </div>
  </div>
</template>

<style scoped>
.log-editor {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--relay-border);
  border: 1px solid var(--relay-border);
  border-radius: 6px;
  overflow: hidden;
  min-height: 60vh;
}

/* One column below 900px: a split preview on a narrow screen gives two unusable
   columns instead of one usable one. */
@media (max-width: 900px) {
  .log-editor {
    grid-template-columns: 1fr;
  }
  .log-editor__preview {
    border-top: 1px solid var(--relay-border);
  }
}

.log-editor__pane {
  background: var(--relay-surface);
  overflow: auto;
  max-height: 78vh;
}

.log-editor__preview {
  padding: 1rem 1.25rem;
}

.log-editor__cm :deep(.cm-editor) {
  height: 100%;
  font-family: var(--relay-mono);
  font-size: 0.9rem;
}

.log-editor__cm :deep(.cm-editor.cm-focused) {
  outline: none;
}

.log-editor__cm :deep(.cm-content) {
  padding: 1rem 0.5rem;
}
</style>
