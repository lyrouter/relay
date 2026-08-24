/**
 * Labels, iterations, the member directory, and the notification badge.
 *
 * Loaded once after sign-in and cached for the session: every filter bar, assignee
 * picker and mention autocomplete needs the same three lists, and fetching them
 * per component turns opening a board into a dozen requests.
 *
 * The unread count is here rather than in its own store because it arrives in the
 * `/web/session` boot payload — F-1 made in-app the only channel, which makes that
 * number the whole reach surface, so it has to be right before the first poll
 * rather than after it.
 */
import { defineStore } from "pinia";
import { ref } from "vue";

import { api } from "@/api/client";
import type { InboxItem, Iteration, Label, Member, TicketField } from "@/api/types";

/** Long enough that the badge is not a poll storm, short enough to feel live. */
export const INBOX_POLL_MS = 60_000;

export const useMetaStore = defineStore("meta", () => {
  const labels = ref<Label[]>([]);
  const iterations = ref<Iteration[]>([]);
  const members = ref<Member[]>([]);
  const ticketFields = ref<TicketField[]>([]);
  const inbox = ref<InboxItem[]>([]);
  const unread = ref(0);
  const loaded = ref(false);

  async function load(): Promise<void> {
    [labels.value, iterations.value, members.value, ticketFields.value] = await Promise.all([
      api.get<Label[]>("/web/meta/labels"),
      api.get<Iteration[]>("/web/meta/iterations"),
      api.get<Member[]>("/web/users"),
      api.get<TicketField[]>("/web/meta/ticket-fields"),
    ]);
    loaded.value = true;
  }

  async function loadInbox(): Promise<void> {
    inbox.value = await api.get<InboxItem[]>("/web/notifications");
    const { unread: count } = await api.get<{ unread: number }>(
      "/web/notifications/unread-count",
    );
    unread.value = count;
  }

  async function markRead(id: string): Promise<void> {
    await api.post(`/web/notifications/${id}/read`);
    inbox.value = inbox.value.map((one) =>
      one.notification_id === id ? { ...one, read_at: new Date().toISOString() } : one,
    );
    unread.value = Math.max(0, unread.value - 1);
  }

  async function markAllRead(): Promise<void> {
    await api.post("/web/notifications/read-all");
    await loadInbox();
  }

  function displayName(userId: string | null | undefined): string {
    if (!userId) return "—";
    return members.value.find((one) => one.user_id === userId)?.display_name ?? "（已停用）";
  }

  function labelName(labelId: string): string {
    return labels.value.find((one) => one.id === labelId)?.name ?? labelId;
  }

  return {
    labels,
    iterations,
    members,
    ticketFields,
    inbox,
    unread,
    loaded,
    load,
    loadInbox,
    markRead,
    markAllRead,
    displayName,
    labelName,
  };
});
