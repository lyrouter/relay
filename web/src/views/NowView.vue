<script setup lang="ts">
/**
 * 此刻 · attention home.
 *
 * Not a ticket list and not a board: sections by who needs the baton next.
 * Chain density chips make empty AI context visible so the home does not look
 * like "another Jira inbox".
 */
import { computed, nextTick, onMounted, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import type { InboxItem, Ticket } from "@/api/types";
import ContextChips from "@/components/ContextChips.vue";
import { relativeTime } from "@/lib/context";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";
import { emptyFilters, useTicketStore } from "@/stores/tickets";

const tickets = useTicketStore();
const session = useSessionStore();
const meta = useMetaStore();
const route = useRoute();

const me = computed(() => session.session?.user_id);

const openStatuses = new Set(["todo", "in_progress", "in_review", "blocked"]);

const p0 = computed(() =>
  tickets.items.filter((one) => one.priority === "p0" && openStatuses.has(one.status)),
);

const active = computed(() =>
  tickets.items.filter(
    (one) =>
      (one.status === "in_progress" || one.status === "in_review" || one.status === "blocked") &&
      one.priority !== "p0",
  ),
);

const waiting = computed(() =>
  tickets.items.filter(
    (one) =>
      me.value &&
      one.assignee_id === me.value &&
      openStatuses.has(one.status) &&
      one.priority !== "p0",
  ),
);

const unread = computed(() => meta.inbox.filter((one) => !one.read_at));

function ticketLink(ticket: Ticket) {
  return {
    name: "ticket" as const,
    params: {
      tenantSlug: session.tenantSlug || "-",
      number: String(ticket.number),
    },
  };
}

function inboxKey(item: InboxItem): string | null {
  const key = item.payload?.key;
  return typeof key === "string" ? key : null;
}

function inboxNumber(item: InboxItem): string | null {
  const key = inboxKey(item);
  if (!key) return null;
  const match = /^RL-(\d+)$/i.exec(key);
  return match?.[1] ?? null;
}

async function openInbox(item: InboxItem): Promise<void> {
  if (!item.read_at) await meta.markRead(item.notification_id);
}

onMounted(async () => {
  tickets.filters = emptyFilters();
  await Promise.all([tickets.load({ board: true }), meta.loadInbox()]);
  await scrollInbox();
});

async function scrollInbox(): Promise<void> {
  if (route.hash !== "#inbox") return;
  await nextTick();
  document.getElementById("inbox")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

watch(() => route.hash, () => void scrollInbox());
</script>

<template>
  <section class="now">
    <header class="now__head">
      <h1 class="page-title">此刻</h1>
      <p class="muted now__sub">需要你接力的上下文</p>
    </header>

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>

    <section v-if="p0.length" class="now__section now__section--p0">
      <h2 class="now__label">P0 · 立即</h2>
      <RouterLink
        v-for="ticket in p0"
        :key="ticket.id"
        class="now__row card"
        :to="ticketLink(ticket)"
      >
        <span class="now__key">{{ ticket.key }}</span>
        <span class="now__title">{{ ticket.title }}</span>
        <ContextChips :ticket="ticket" chain />
        <span class="muted now__age">{{ relativeTime(ticket.updated_at) }}</span>
      </RouterLink>
    </section>

    <section class="now__section">
      <h2 class="now__label">进行中的调查（{{ active.length }}）</h2>
      <RouterLink
        v-for="ticket in active"
        :key="ticket.id"
        class="now__row card"
        :to="ticketLink(ticket)"
      >
        <span class="now__key">{{ ticket.key }}</span>
        <span class="now__title">{{ ticket.title }}</span>
        <ContextChips :ticket="ticket" chain />
        <span class="muted now__age">{{ relativeTime(ticket.updated_at) }}</span>
      </RouterLink>
      <p v-if="!active.length && !tickets.loading" class="empty now__empty">
        没有进行中的调查。从企微 <code>@Relay 建单</code>，或到
        <RouterLink :to="{ name: 'work-list' }">工作</RouterLink> 里开一条。
      </p>
    </section>

    <section class="now__section">
      <h2 class="now__label">等你接力（{{ waiting.length }}）</h2>
      <RouterLink
        v-for="ticket in waiting"
        :key="ticket.id"
        class="now__row card"
        :to="ticketLink(ticket)"
      >
        <span class="now__key">{{ ticket.key }}</span>
        <span class="now__title">{{ ticket.title }}</span>
        <ContextChips :ticket="ticket" chain />
        <span class="muted now__age">{{ relativeTime(ticket.updated_at) }}</span>
      </RouterLink>
      <p v-if="!waiting.length && !tickets.loading" class="empty now__empty">
        没有指派给你的开放调查。
      </p>
    </section>

    <section id="inbox" class="now__section">
      <header class="now__inbox-head">
        <h2 class="now__label">未读（{{ unread.length }}）</h2>
        <button
          v-if="unread.length"
          type="button"
          class="button"
          @click="meta.markAllRead()"
        >
          全部标为已读
        </button>
      </header>
      <RouterLink
        v-for="item in unread"
        :key="item.notification_id"
        class="now__row card now__inbox"
        :to="
          inboxNumber(item)
            ? {
                name: 'ticket',
                params: { tenantSlug: session.tenantSlug || '-', number: inboxNumber(item)! },
              }
            : { name: 'now' }
        "
        @click="openInbox(item)"
      >
        <span class="now__key">{{ inboxKey(item) ?? item.target_type }}</span>
        <span class="now__title">
          {{ item.type }}
          <span v-if="item.folded_count > 1" class="pill">×{{ item.folded_count }}</span>
        </span>
        <span class="muted now__age">{{ relativeTime(item.created_at) }}</span>
      </RouterLink>
      <p v-if="!unread.length" class="empty now__empty">没有未读通知。</p>
    </section>
  </section>
</template>

<style scoped>
.now__head {
  margin-bottom: 1.25rem;
}

.now__sub {
  margin: -0.5rem 0 0;
  font-size: 0.9rem;
}

.now__section {
  margin-bottom: 1.5rem;
}

.now__section--p0 .now__row {
  border-color: color-mix(in srgb, var(--relay-danger) 35%, var(--relay-border));
  background: color-mix(in srgb, var(--relay-danger) 6%, var(--relay-surface));
}

.now__label {
  margin: 0 0 0.55rem;
  font-size: 0.85rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--relay-text-muted);
  text-transform: none;
}

.now__inbox-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.55rem;
}

.now__inbox-head .now__label {
  margin: 0;
}

.now__row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  grid-template-areas:
    "key title age"
    "chips chips chips";
  gap: 0.25rem 0.75rem;
  padding: 0.7rem 0.9rem;
  margin-bottom: 0.45rem;
  text-decoration: none;
  color: inherit;
  align-items: baseline;
}

.now__row:hover {
  border-color: var(--relay-accent);
}

.now__key {
  grid-area: key;
  font-family: var(--relay-mono);
  font-size: 0.82rem;
  color: var(--relay-accent);
}

.now__title {
  grid-area: title;
  font-size: 0.95rem;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.now__age {
  grid-area: age;
  font-size: 0.78rem;
  font-family: var(--relay-mono);
}

.now__row :deep(.chips) {
  grid-area: chips;
  margin-top: 0.15rem;
}

.now__empty {
  padding: 1rem 0.5rem;
  text-align: left;
}

.now__empty code {
  font-family: var(--relay-mono);
  font-size: 0.85em;
}
</style>
