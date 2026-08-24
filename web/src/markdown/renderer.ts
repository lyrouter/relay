/**
 * LOG-2 / LOG-3 · rendering a log body.
 *
 * Full GFM, syntax-highlighted code, Mermaid diagrams, and `#331` resolved to an
 * inline ticket card. Three decisions here are load-bearing:
 *
 * **1 · `html: false`.** markdown-it will not pass raw HTML through. A log body is
 * written by one colleague and read by another, so raw HTML is a stored-XSS
 * vector with a human delivery mechanism — and nothing the product needs requires
 * it. This is why the rendered output can be bound with `innerHTML` at all.
 *
 * **2 · Mermaid is rendered *after* mount, never during markdown parsing.**
 * `mermaid.render` is async and produces SVG; doing it inside the parser would
 * mean either blocking or injecting unsanitised SVG. So a ```mermaid fence becomes
 * a placeholder element and {@link renderDiagrams} fills it in. A diagram that
 * fails to parse shows its own error text — the author needs to see *that they
 * broke it*, not a blank space.
 *
 * **3 · `#331` degrades to plain text.** LOG-3 is explicit: no permission, or no
 * such ticket, and it stays literal — **never leak the title.** The renderer emits
 * a placeholder carrying only the number; the resolver fills in a title only for
 * tickets the reader could have opened anyway, because it asks the API as that
 * reader.
 */
import hljs from "highlight.js";
import MarkdownIt from "markdown-it";

import { api, ProblemError } from "@/api/client";
import type { Ticket } from "@/api/types";

/** `#331` — a word boundary before, so `RL#331` and `abc#331` do not match. */
const TICKET_REFERENCE = /(^|[\s(（[【])#(\d{1,7})\b/g;

const MERMAID_CLASS = "relay-mermaid";
const TICKET_CLASS = "relay-ticket-ref";

const markdown: MarkdownIt = new MarkdownIt({
  // See decision 1. Not negotiable: the preview renders what a colleague typed.
  html: false,
  linkify: true,
  typographer: false,
  breaks: false,
  highlight(code, language) {
    if (language === "mermaid") {
      // Handed to renderDiagrams() after mount. The source is escaped so a
      // diagram that never renders is still inert text rather than markup.
      return `<div class="${MERMAID_CLASS}" data-source="${escapeAttribute(code)}">
        <div class="relay-mermaid-pending">图示渲染中…</div></div>`;
    }
    if (language && hljs.getLanguage(language)) {
      try {
        return `<pre class="hljs"><code>${
          hljs.highlight(code, { language, ignoreIllegals: true }).value
        }</code></pre>`;
      } catch {
        // Fall through to the plain rendering: a highlighter failure must not
        // cost the reader the code itself.
      }
    }
    return `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`;
  },
});

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttribute(value: string): string {
  return escapeHtml(value).replace(/'/g, "&#39;").replace(/\n/g, "&#10;");
}

/**
 * Markdown → HTML, with ticket references left as placeholders.
 *
 * Substitution happens on the **rendered** string rather than on the source, and
 * only outside code: replacing `#331` in the source would rewrite it inside fenced
 * blocks too, and a log about `#331` in a shell comment would sprout a card in the
 * middle of a snippet.
 */
export function renderMarkdown(source: string): string {
  const html = markdown.render(source ?? "");
  return substituteOutsideCode(html);
}

/** Plain-text mode (LOG-1): no markdown at all, just safe line breaks. */
export function renderPlainText(source: string): string {
  return `<pre class="relay-plain">${escapeHtml(source ?? "")}</pre>`;
}

function substituteOutsideCode(html: string): string {
  // Split on code/pre spans and only substitute in the gaps. A parser would be
  // more precise; this is the smaller of the two risks, because the failure mode
  // of over-matching is a card inside a code sample and the failure mode of a
  // second parser is two renderers that disagree.
  const parts = html.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/g);
  return parts
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(
            TICKET_REFERENCE,
            (_match, lead: string, digits: string) =>
              `${lead}<span class="${TICKET_CLASS}" data-number="${digits}">#${digits}</span>`,
          ),
    )
    .join("");
}

/**
 * Render every Mermaid placeholder inside `root`.
 *
 * Imported lazily: Mermaid is by far the heaviest dependency in this app, and a
 * log without a diagram should not pay for it. Called from the component's
 * `onMounted`/watcher, after the HTML is in the DOM.
 */
export async function renderDiagrams(root: HTMLElement): Promise<void> {
  const blocks = Array.from(root.querySelectorAll<HTMLElement>(`.${MERMAID_CLASS}`));
  if (blocks.length === 0) return;

  const mermaid = (await import("mermaid")).default;
  mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "strict" });

  for (const [index, block] of blocks.entries()) {
    const source = block.dataset.source ?? "";
    if (!source.trim()) continue;
    try {
      const { svg } = await mermaid.render(`relay-diagram-${Date.now()}-${index}`, source);
      block.innerHTML = svg;
    } catch (error) {
      // Shown, not swallowed: the author is usually the person looking at it, and
      // a silently missing diagram reads as "the editor lost my work".
      block.innerHTML = `<div class="relay-mermaid-error">图示语法有误：${escapeHtml(
        error instanceof Error ? error.message : String(error),
      )}</div>`;
    }
  }
}

/** What a resolved reference renders as. `null` title means "stay plain text". */
export interface TicketReference {
  number: number;
  key: string;
  title: string | null;
  status: string | null;
}

const referenceCache = new Map<number, TicketReference>();

/**
 * Resolve the `#331` placeholders inside `root`, in the current tenant.
 *
 * **The degrade path is the important one.** A 404 — which is also the answer for
 * a ticket in another tenant (MT-6) and for one a Guest may not see (S-21) —
 * leaves the text exactly as the author typed it. No tooltip, no "no permission"
 * badge: either of those would confirm that RL-331 exists, and the design says the
 * reader must not learn that.
 */
export async function resolveTicketReferences(root: HTMLElement): Promise<void> {
  const nodes = Array.from(root.querySelectorAll<HTMLElement>(`.${TICKET_CLASS}`));
  const numbers = [...new Set(nodes.map((node) => Number(node.dataset.number)))];

  await Promise.all(
    numbers
      .filter((number) => Number.isFinite(number) && !referenceCache.has(number))
      .map(async (number) => {
        try {
          const ticket = await api.get<Ticket>(`/web/tickets/${number}`);
          referenceCache.set(number, {
            number,
            key: ticket.key,
            title: ticket.title,
            status: ticket.status,
          });
        } catch (error) {
          if (error instanceof ProblemError && error.status === 404) {
            referenceCache.set(number, {
              number,
              key: `RL-${number}`,
              title: null,
              status: null,
            });
            return;
          }
          throw error;
        }
      }),
  );

  for (const node of nodes) {
    const resolved = referenceCache.get(Number(node.dataset.number));
    if (!resolved || resolved.title === null) continue;
    node.classList.add("resolved");
    node.setAttribute("role", "link");
    node.setAttribute("tabindex", "0");
    node.textContent = `${resolved.key} ${resolved.title}`;
    node.dataset.status = resolved.status ?? "";
  }
}

/** For tests and for the detail page, which links a card to its route. */
export function ticketReferenceNumber(node: HTMLElement): number | null {
  const number = Number(node.dataset.number);
  return Number.isFinite(number) ? number : null;
}
