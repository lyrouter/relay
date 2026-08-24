<script setup lang="ts">
/**
 * LOG-2 / LOG-3 · rendered log body: GFM, code, Mermaid, and `#331` cards.
 *
 * `v-html` is used here, and it is safe **because of a decision made in the
 * renderer, not because of anything in this file**: markdown-it runs with
 * `html: false`, so no author-supplied markup survives parsing. That is why the
 * renderer is a module with its own note rather than an inline call — the reason
 * this is safe has to live somewhere a reviewer will look.
 *
 * The two post-render passes (Mermaid, ticket references) run after the DOM
 * updates, and both are re-run when the source changes. They are `await`ed in
 * order rather than in parallel: the reference pass fetches, and starting a dozen
 * requests while Mermaid is laying out a diagram makes the first paint worse.
 */
import { nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import type { LogFormat } from "@/api/types";
import {
  renderDiagrams,
  renderMarkdown,
  renderPlainText,
  resolveTicketReferences,
  ticketReferenceNumber,
} from "@/markdown/renderer";
import { useSessionStore } from "@/stores/session";

const props = withDefaults(
  defineProps<{ source: string; format?: LogFormat }>(),
  { format: "markdown" },
);

const router = useRouter();
const session = useSessionStore();
const host = ref<HTMLElement | null>(null);
const html = ref("");

async function render(): Promise<void> {
  html.value =
    props.format === "plain" ? renderPlainText(props.source) : renderMarkdown(props.source);
  await nextTick();
  if (!host.value) return;
  await renderDiagrams(host.value);
  await resolveTicketReferences(host.value);
}

/**
 * A resolved `#331` navigates; an unresolved one does nothing.
 *
 * Delegated from the container rather than bound per card, because the cards are
 * created by `innerHTML` and have no Vue listeners of their own. Only cards the
 * resolver marked `resolved` are clickable — an unresolved reference is plain
 * text, and making it *look* clickable would tell the reader the ticket exists
 * (LOG-3 forbids exactly that).
 */
function onClick(event: MouseEvent): void {
  const target = (event.target as HTMLElement | null)?.closest<HTMLElement>(
    ".relay-ticket-ref.resolved",
  );
  if (!target) return;
  const number = ticketReferenceNumber(target);
  if (number === null) return;
  event.preventDefault();
  void router.push({
    name: "ticket",
    params: { tenantSlug: session.tenantSlug || "-", number: String(number) },
  });
}

onMounted(render);
watch(() => [props.source, props.format], render);
</script>

<template>
  <div ref="host" class="markdown-view" @click="onClick" v-html="html" />
</template>

<style>
/* Unscoped on purpose: the content is injected as raw HTML, so scoped styles
   (which rely on compile-time attributes) would not reach it. Everything is
   nested under .markdown-view to keep it from leaking. */
.markdown-view {
  line-height: 1.7;
  word-break: break-word;
}

.markdown-view > :first-child {
  margin-top: 0;
}

.markdown-view h1,
.markdown-view h2,
.markdown-view h3 {
  margin: 1.4em 0 0.6em;
  line-height: 1.3;
}

.markdown-view h1 { font-size: 1.5rem; }
.markdown-view h2 { font-size: 1.25rem; }
.markdown-view h3 { font-size: 1.05rem; }

.markdown-view pre {
  background: var(--relay-code-bg);
  border: 1px solid var(--relay-border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  overflow-x: auto;
  font-family: var(--relay-mono);
  font-size: 0.85rem;
}

.markdown-view code {
  font-family: var(--relay-mono);
  font-size: 0.88em;
  background: var(--relay-code-bg);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}

.markdown-view pre code {
  background: none;
  padding: 0;
}

.markdown-view blockquote {
  margin: 1em 0;
  padding: 0.2em 1em;
  border-left: 3px solid var(--relay-accent);
  color: var(--relay-text-muted);
}

.markdown-view table {
  border-collapse: collapse;
  margin: 1em 0;
  display: block;
  overflow-x: auto;
}

.markdown-view th,
.markdown-view td {
  border: 1px solid var(--relay-border);
  padding: 0.4em 0.7em;
  text-align: left;
}

.markdown-view input[type="checkbox"] {
  margin-right: 0.4em;
}

.markdown-view .relay-plain {
  white-space: pre-wrap;
  background: none;
  border: none;
  padding: 0;
  font-family: var(--relay-mono);
}

/* An unresolved reference is deliberately indistinguishable from the text around
   it apart from being monospaced: no border, no cursor, no hover. LOG-3 — never
   leak that the ticket exists. */
.markdown-view .relay-ticket-ref {
  font-family: var(--relay-mono);
}

.markdown-view .relay-ticket-ref.resolved {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35em;
  font-family: inherit;
  padding: 0.05em 0.45em;
  border: 1px solid var(--relay-border);
  border-radius: 999px;
  background: var(--relay-surface-alt);
  cursor: pointer;
  text-decoration: none;
}

.markdown-view .relay-ticket-ref.resolved:hover {
  border-color: var(--relay-accent);
}

.markdown-view .relay-mermaid {
  margin: 1em 0;
  overflow-x: auto;
}

.markdown-view .relay-mermaid-pending {
  color: var(--relay-text-muted);
  font-size: 0.85rem;
}

.markdown-view .relay-mermaid-error {
  border: 1px solid var(--relay-danger);
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  color: var(--relay-danger);
  font-size: 0.85rem;
  font-family: var(--relay-mono);
}
</style>
