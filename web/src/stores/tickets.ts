/**
 * TKT-5/6/7/9 · the ticket list, the board, my tickets, and one ticket.
 *
 * Three conventions from the HTTP layer show up here as store behaviour, and each
 * one is the reason a naive version of this file would be wrong:
 *
 * **1 · `rev` travels with every mutation.** `patch`, `transition` and the board's
 * drag all pass the `rev` that was rendered. A 409 is surfaced as
 * `conflict`, not retried: retrying with a fresh `rev` would be exactly the
 * silent overwrite `rev` exists to prevent. The UI's job is to tell the user
 * somebody saved first and re-read.
 *
 * **2 · The cursor is opaque and paging is append-only.** `loadMore()` keeps what
 * is on screen and adds a page; it never re-fetches page one, because the board is
 * ordered by `updated_at` and re-fetching would reshuffle rows under the reader's
 * cursor.
 *
 * **3 · The board groups client-side from one filtered query.** Six queries — one
 * per column — would each get their own snapshot of a moving list, so a ticket
 * that changed status between them would appear twice or not at all.
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api, ProblemError } from "@/api/client";
import type {
  Attachment,
  SearchResult,
  Ticket,
  TicketComment,
  TicketHistoryEntry,
  TicketPage,
  TicketStatus,
} from "@/api/types";
import { STATUS_ORDER } from "@/api/types";

export interface TicketFilters {
  status: TicketStatus[];
  priority: string[];
  assignee_id?: string;
  label_id?: string;
  iteration_id?: string;
  /** TKT-5's keyword. Served by the search endpoint, not by the list. */
  keyword?: string;
}

export function emptyFilters(): TicketFilters {
  return { status: [], priority: [] };
}

/** The board loads more than a list page: a column of 50 is a truncated column. */
const BOARD_LIMIT = 200;
const LIST_LIMIT = 50;

export const useTicketStore = defineStore("tickets", () => {
  const items = ref<Ticket[]>([]);
  const cursor = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  /** Set when a write lost a race. The UI shows it and re-reads. */
  const conflict = ref<string | null>(null);
  const filters = ref<TicketFilters>(emptyFilters());

  const current = ref<Ticket | null>(null);
  const comments = ref<TicketComment[]>([]);
  const history = ref<TicketHistoryEntry[]>([]);
  const attachments = ref<Attachment[]>([]);

  const hasMore = computed(() => cursor.value !== null);

  /** TKT-6's columns, grouped from the one query. See the module note. */
  const byStatus = computed<Record<TicketStatus, Ticket[]>>(() => {
    const grouped = Object.fromEntries(
      STATUS_ORDER.map((status) => [status, [] as Ticket[]]),
    ) as Record<TicketStatus, Ticket[]>;
    for (const ticket of items.value) grouped[ticket.status].push(ticket);
    return grouped;
  });

  function query(limit: number, cursorValue?: string | null) {
    return {
      status: filters.value.status,
      priority: filters.value.priority,
      assignee_id: filters.value.assignee_id,
      label_id: filters.value.label_id,
      iteration_id: filters.value.iteration_id,
      cursor: cursorValue ?? undefined,
      limit,
    };
  }

  async function load(options: { board?: boolean } = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      if (filters.value.keyword) {
        // Keyword search is LOG-8's endpoint, deliberately not an ILIKE on the
        // list: two search behaviours that disagree about Chinese tokenisation
        // would be one behaviour too many, and the cheap one is the one people
        // hit first.
        const found = await api.get<SearchResult>("/web/search", {
          q: filters.value.keyword,
          kind: ["ticket"],
        });
        // Search returns hits, not tickets: one read per hit would be N+1, so the
        // rows are fetched in parallel and the ones that 404 (deleted between the
        // index read and now) are dropped rather than rendered as holes.
        const fetched = await Promise.all(
          found.hits.map((hit) =>
            api.get<Ticket>(`/web/tickets/${hit.id}`).catch(() => null),
          ),
        );
        items.value = fetched.filter((one): one is Ticket => one !== null);
        cursor.value = null;
        return;
      }
      const page = await api.get<TicketPage>(
        "/web/tickets",
        query(options.board ? BOARD_LIMIT : LIST_LIMIT),
      );
      items.value = page.items;
      cursor.value = page.next_cursor ?? null;
    } catch (caught) {
      error.value = message(caught);
    } finally {
      loading.value = false;
    }
  }

  async function loadMore(): Promise<void> {
    if (!cursor.value || loading.value) return;
    loading.value = true;
    try {
      const page = await api.get<TicketPage>("/web/tickets", query(LIST_LIMIT, cursor.value));
      items.value = [...items.value, ...page.items];
      cursor.value = page.next_cursor ?? null;
    } catch (caught) {
      error.value = message(caught);
    } finally {
      loading.value = false;
    }
  }

  /** TKT-7 · my tickets: assigned to me. Server-side, so it pages like any list. */
  async function loadMine(userId: string): Promise<void> {
    filters.value = { ...emptyFilters(), assignee_id: userId };
    await load();
  }

  async function open(key: string): Promise<void> {
    loading.value = true;
    error.value = null;
    conflict.value = null;
    try {
      current.value = await api.get<Ticket>(`/web/tickets/${key}`);
      // Both loaded up front: the detail page shows them in tabs, and a spinner
      // per tab makes a two-request page feel like a five-request one.
      const [nextComments, nextHistory, nextAttachments] = await Promise.all([
        api.get<TicketComment[]>(`/web/tickets/${key}/comments`),
        api.get<TicketHistoryEntry[]>(`/web/tickets/${key}/history`),
        api.get<Attachment[]>("/web/attachments", {
          owner_type: "ticket",
          owner_id: current.value.id,
        }),
      ]);
      comments.value = nextComments;
      history.value = nextHistory;
      attachments.value = nextAttachments;
    } catch (caught) {
      error.value = message(caught);
      current.value = null;
      comments.value = [];
      history.value = [];
      attachments.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function create(payload: Record<string, unknown>): Promise<Ticket | null> {
    error.value = null;
    try {
      return await api.post<Ticket>("/web/tickets", payload);
    } catch (caught) {
      error.value = message(caught);
      return null;
    }
  }

  async function patch(ticket: Ticket, changes: Record<string, unknown>): Promise<boolean> {
    conflict.value = null;
    try {
      const updated = await api.patch<Ticket>(
        `/web/tickets/${ticket.key}`,
        changes,
        ticket.rev,
      );
      replace(updated);
      return true;
    } catch (caught) {
      return handleWrite(caught, ticket.key);
    }
  }

  /**
   * Move a ticket. Used by the detail page **and** by the board's drag.
   *
   * One code path for both, on purpose: the board dropping a card is a status
   * transition with the same state-machine rules and the same reason requirement
   * (TKT-3), and a second path would be the one that forgets them.
   */
  async function transition(
    ticket: Ticket,
    to: TicketStatus,
    reason?: string,
  ): Promise<boolean> {
    conflict.value = null;
    try {
      const updated = await api.post<Ticket>(
        `/web/tickets/${ticket.key}/transitions`,
        { to, reason: reason ?? null },
        ticket.rev,
      );
      replace(updated);
      return true;
    } catch (caught) {
      return handleWrite(caught, ticket.key);
    }
  }

  async function comment(ticket: Ticket, body: string): Promise<boolean> {
    error.value = null;
    try {
      const added = await api.post<TicketComment>(`/web/tickets/${ticket.key}/comments`, {
        body,
      });
      comments.value = [...comments.value, added];
      return true;
    } catch (caught) {
      error.value = message(caught);
      return false;
    }
  }

  async function attach(file: File): Promise<boolean> {
    const ticket = current.value;
    if (!ticket) return false;
    error.value = null;
    try {
      const added = (await api.upload("/web/attachments", file, {
        owner_type: "ticket",
        owner_id: ticket.id,
      })) as Attachment;
      attachments.value = [...attachments.value, added];
      return true;
    } catch (caught) {
      error.value = message(caught);
      return false;
    }
  }

  async function linkFor(attachmentId: string): Promise<string | null> {
    try {
      const { url } = await api.get<{ url: string }>(`/web/attachments/${attachmentId}/link`);
      return url;
    } catch (caught) {
      error.value = message(caught);
      return null;
    }
  }

  function replace(updated: Ticket): void {
    items.value = items.value.map((one) => (one.id === updated.id ? updated : one));
    if (current.value?.id === updated.id) current.value = updated;
  }

  async function handleWrite(caught: unknown, key: string): Promise<boolean> {
    if (caught instanceof ProblemError && caught.isConflict) {
      conflict.value =
        `这条调查已被其他人修改（当前版本 ${caught.currentRev ?? "?"}）。` +
        "已重新载入最新内容，请确认后再提交。";
      // Re-read rather than retry: the user has to see what the other person did
      // before deciding whether their own change still makes sense.
      await open(key);
      return false;
    }
    error.value = message(caught);
    return false;
  }

  function message(caught: unknown): string {
    if (caught instanceof ProblemError) {
      const fields = caught.errors.map((one) => `${one.field}: ${one.message}`);
      return fields.length ? `${caught.message}（${fields.join("；")}）` : caught.message;
    }
    return caught instanceof Error ? caught.message : String(caught);
  }

  return {
    items,
    cursor,
    hasMore,
    loading,
    error,
    conflict,
    filters,
    current,
    comments,
    history,
    attachments,
    byStatus,
    load,
    loadMore,
    loadMine,
    open,
    create,
    patch,
    transition,
    comment,
    attach,
    linkFor,
  };
});
