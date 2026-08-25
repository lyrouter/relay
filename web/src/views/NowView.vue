<script setup lang="ts">
/**
 * 此刻 · matches mockups/now.png.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import type { InboxItem, Ticket } from "@/api/types";
import AiContextPanel from "@/components/AiContextPanel.vue";
import ModelBadge from "@/components/ModelBadge.vue";
import {
  contextValue,
  copyText,
  firstModel,
  initials,
  relativeTime,
} from "@/lib/context";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";
import { emptyFilters, useTicketStore } from "@/stores/tickets";

const tickets = useTicketStore();
const session = useSessionStore();
const meta = useMetaStore();
const route = useRoute();

const selectedId = ref<string | null>(null);
const refreshing = ref(false);

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

const selected = computed(() => {
  const id = selectedId.value;
  if (id) return tickets.items.find((one) => one.id === id) ?? null;
  return p0.value[0] ?? active.value[0] ?? waiting.value[0] ?? null;
});

function ticketLink(ticket: Ticket) {
  return {
    name: "ticket" as const,
    params: {
      tenantSlug: session.tenantSlug || "-",
      number: String(ticket.number),
    },
  };
}

function select(ticket: Ticket): void {
  selectedId.value = ticket.id;
}

function p0Line(ticket: Ticket): string {
  const parts = [
    ticket.key,
    ticket.title,
    firstModel(ticket) || null,
    contextValue(ticket, "provider")
      ? `provider=${contextValue(ticket, "provider")}`
      : null,
    meta.displayName(ticket.assignee_id) === "—"
      ? "未指派"
      : meta.displayName(ticket.assignee_id),
    relativeTime(ticket.updated_at).replace(" 前", ""),
  ];
  return parts.filter(Boolean).join(" · ");
}

function statusDot(status: string): string {
  if (status === "blocked") return "warn";
  if (status === "in_progress" || status === "in_review") return "live";
  return "idle";
}

function inboxKey(item: InboxItem): string | null {
  const key = item.payload?.key;
  return typeof key === "string" ? key : null;
}

function inboxNumber(item: InboxItem): string | null {
  const key = inboxKey(item);
  if (!key) return null;
  return /^RL-(\d+)$/i.exec(key)?.[1] ?? null;
}

async function openInbox(item: InboxItem): Promise<void> {
  if (!item.read_at) await meta.markRead(item.notification_id);
}

async function refreshContext(): Promise<void> {
  refreshing.value = true;
  try {
    await tickets.load({ board: true });
  } finally {
    refreshing.value = false;
  }
}

async function scrollInbox(): Promise<void> {
  if (route.hash !== "#inbox") return;
  await nextTick();
  document.getElementById("inbox")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

onMounted(async () => {
  tickets.filters = emptyFilters();
  await Promise.all([tickets.load({ board: true }), meta.loadInbox()]);
  await scrollInbox();
});

watch(() => route.hash, () => void scrollInbox());
</script>

<template>
  <div class="now">
    <div class="now__feed">
      <header class="now__head">
        <h1 class="now__title">此刻</h1>
        <p class="now__sub">需要你接力的上下文</p>
      </header>

      <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>

      <!-- P0 -->
      <section v-if="p0.length" class="block">
        <header class="block__head">
          <div class="block__label block__label--danger">
            P0 最高优先级
            <span class="count count--danger">{{ p0.length }}</span>
          </div>
          <RouterLink class="block__more" :to="{ name: 'context' }">查看全部 ›</RouterLink>
        </header>
        <button
          v-for="ticket in p0"
          :key="ticket.id"
          type="button"
          class="p0"
          :class="{ 'p0--on': selected?.id === ticket.id }"
          @click="select(ticket)"
          @dblclick="$router.push(ticketLink(ticket))"
        >
          <span class="p0__bang" aria-hidden="true">!</span>
          <span class="p0__line">{{ p0Line(ticket) }}</span>
          <RouterLink class="p0__open" :to="ticketLink(ticket)" @click.stop>打开</RouterLink>
        </button>
      </section>

      <!-- Active investigations -->
      <section class="block">
        <header class="block__head">
          <div class="block__label">
            活跃调查中
            <span class="count">{{ active.length }}</span>
          </div>
          <RouterLink class="block__more" :to="{ name: 'work-list' }">查看全部 ›</RouterLink>
        </header>

        <article
          v-for="ticket in active"
          :key="ticket.id"
          class="card invest"
          :class="{ 'invest--on': selected?.id === ticket.id }"
          @click="select(ticket)"
        >
          <div class="invest__top">
            <RouterLink class="invest__key" :to="ticketLink(ticket)" @click.stop>
              {{ ticket.key }}
            </RouterLink>
            <h3 class="invest__title">{{ ticket.title }}</h3>
          </div>
          <div class="invest__mid">
            <span class="trace">
              <span class="trace__k">trace_id</span>
              <span class="trace__v">{{ contextValue(ticket, "trace_id") || "—" }}</span>
              <button
                v-if="contextValue(ticket, 'trace_id')"
                type="button"
                class="trace__copy"
                @click.stop="copyText(contextValue(ticket, 'trace_id'))"
              >
                ⎘
              </button>
            </span>
            <ModelBadge :model="firstModel(ticket)" />
          </div>
          <footer class="invest__foot">
            <span class="muted">
              关联企微线程
              <template v-if="ticket.source"> · {{ ticket.source }}</template>
              <template v-else> · 未关联</template>
            </span>
            <span class="handoff">
              最后接力:
              <span class="avatar">{{ initials(meta.displayName(ticket.assignee_id)) }}</span>
              {{ meta.displayName(ticket.assignee_id) }} · {{ relativeTime(ticket.updated_at) }}
            </span>
          </footer>
        </article>

        <p v-if="!active.length && !tickets.loading" class="empty-inline">
          没有进行中的调查。从企微 <code>@Relay 建单</code>，或到
          <RouterLink :to="{ name: 'work-list' }">工作</RouterLink> 新建。
        </p>
      </section>

      <!-- Waiting -->
      <section class="block">
        <header class="block__head">
          <div class="block__label">
            等待你的接力
            <span class="count">{{ waiting.length }}</span>
          </div>
        </header>

        <div v-if="waiting.length" class="wait">
          <div
            v-for="ticket in waiting"
            :key="ticket.id"
            class="wait__row"
            :class="{ 'wait__row--on': selected?.id === ticket.id }"
            @click="select(ticket)"
          >
            <span class="dot" :data-tone="statusDot(ticket.status)" />
            <span class="wait__key">{{ ticket.key }}</span>
            <span class="wait__title">{{ ticket.title }}</span>
            <ModelBadge :model="firstModel(ticket)" />
            <span class="muted wait__src">{{ ticket.source || "—" }}</span>
            <span class="muted wait__who">
              {{ meta.displayName(ticket.assignee_id) }} · {{ relativeTime(ticket.updated_at) }}
            </span>
            <RouterLink class="button wait__go" :to="ticketLink(ticket)" @click.stop>
              接力 ›
            </RouterLink>
          </div>
        </div>
        <p v-else class="empty-inline">没有指派给你的开放调查。</p>
      </section>

      <!-- Quiet inbox -->
      <section id="inbox" class="block block--inbox">
        <header class="block__head">
          <div class="block__label">
            未读
            <span class="count">{{ unread.length }}</span>
          </div>
          <button
            v-if="unread.length"
            type="button"
            class="button"
            @click="meta.markAllRead()"
          >
            全部已读
          </button>
        </header>
        <RouterLink
          v-for="item in unread"
          :key="item.notification_id"
          class="inbox-row"
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
          <span class="wait__key">{{ inboxKey(item) ?? item.target_type }}</span>
          <span>{{ item.type }}</span>
          <span v-if="item.folded_count > 1" class="pill">×{{ item.folded_count }}</span>
          <span class="muted">{{ relativeTime(item.created_at) }}</span>
        </RouterLink>
      </section>
    </div>

    <AiContextPanel
      class="now__side"
      :ticket="selected"
      :refreshing="refreshing"
      @refresh="refreshContext"
    />
  </div>
</template>

<style scoped>
.now {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  min-height: calc(100vh - 53px);
}

.now__feed {
  padding: 1.35rem 1.5rem 2rem;
  overflow: auto;
}

.now__side {
  position: sticky;
  top: 53px;
  height: calc(100vh - 53px);
  overflow: auto;
}

.now__head {
  margin-bottom: 1.25rem;
}

.now__title {
  margin: 0;
  font-size: 1.55rem;
  letter-spacing: -0.02em;
}

.now__sub {
  margin: 0.25rem 0 0;
  color: var(--relay-text-muted);
  font-size: 0.9rem;
}

.block {
  margin-bottom: 1.5rem;
}

.block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.55rem;
}

.block__label {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.88rem;
  font-weight: 600;
}

.block__label--danger {
  color: var(--relay-danger);
}

.block__more {
  font-size: 0.8rem;
  text-decoration: none;
  color: var(--relay-text-muted);
}

.count {
  display: inline-grid;
  place-items: center;
  min-width: 1.2rem;
  height: 1.2rem;
  padding: 0 0.3rem;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 0.72rem;
}

.count--danger {
  background: #fee2e2;
  color: var(--relay-danger);
}

.p0 {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.7rem 0.85rem;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--relay-danger) 30%, var(--relay-border));
  background: #fff1f2;
  text-align: left;
  cursor: pointer;
  margin-bottom: 0.4rem;
}

.p0--on {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--relay-danger) 35%, transparent);
}

.p0__bang {
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 999px;
  background: var(--relay-danger);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 0.75rem;
  font-weight: 700;
  flex-shrink: 0;
}

.p0__line {
  flex: 1;
  min-width: 0;
  font-family: var(--relay-mono);
  font-size: 0.8rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.p0__open {
  font-size: 0.78rem;
  text-decoration: none;
  flex-shrink: 0;
}

.invest {
  padding: 0.9rem 1rem;
  margin-bottom: 0.55rem;
  cursor: pointer;
  display: grid;
  gap: 0.55rem;
}

.invest--on {
  border-color: var(--relay-accent);
  box-shadow: 0 0 0 1px var(--relay-accent-soft);
}

.invest__top {
  display: flex;
  align-items: baseline;
  gap: 0.55rem;
  min-width: 0;
}

.invest__key {
  font-family: var(--relay-mono);
  font-size: 0.82rem;
  text-decoration: none;
  flex-shrink: 0;
}

.invest__title {
  margin: 0;
  font-size: 0.98rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.invest__mid {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.trace {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  font-size: 0.78rem;
}

.trace__k {
  color: var(--relay-text-muted);
}

.trace__v {
  font-family: var(--relay-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 28ch;
}

.trace__copy {
  border: 0;
  background: transparent;
  color: var(--relay-text-muted);
  cursor: pointer;
  padding: 0;
}

.invest__foot {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  font-size: 0.78rem;
}

.handoff {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--relay-text-muted);
}

.avatar {
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 999px;
  background: #334155;
  color: #fff;
  font-size: 0.55rem;
  font-weight: 600;
  display: inline-grid;
  place-items: center;
}

.wait {
  background: var(--relay-surface);
  border: 1px solid var(--relay-border);
  border-radius: 10px;
  overflow: hidden;
}

.wait__row {
  display: grid;
  grid-template-columns: 10px 64px minmax(0, 1.4fr) auto auto auto auto;
  align-items: center;
  gap: 0.55rem;
  padding: 0.65rem 0.8rem;
  border-top: 1px solid var(--relay-border);
  cursor: pointer;
  font-size: 0.82rem;
}

.wait__row:first-child {
  border-top: 0;
}

.wait__row--on {
  background: var(--relay-accent-soft);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #94a3b8;
}

.dot[data-tone="live"] {
  background: #3b82f6;
}

.dot[data-tone="warn"] {
  background: #f59e0b;
}

.wait__key {
  font-family: var(--relay-mono);
  font-size: 0.78rem;
}

.wait__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wait__src,
.wait__who {
  white-space: nowrap;
  font-size: 0.75rem;
}

.wait__go {
  text-decoration: none;
  padding: 0.15rem 0.5rem;
  font-size: 0.78rem;
}

.empty-inline {
  margin: 0;
  padding: 0.75rem 0.25rem;
  color: var(--relay-text-muted);
  font-size: 0.88rem;
}

.empty-inline code {
  font-family: var(--relay-mono);
  font-size: 0.85em;
}

.block--inbox .inbox-row {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  padding: 0.55rem 0.7rem;
  margin-bottom: 0.35rem;
  background: var(--relay-surface);
  border: 1px solid var(--relay-border);
  border-radius: 8px;
  text-decoration: none;
  color: inherit;
  font-size: 0.85rem;
}

@media (max-width: 1100px) {
  .now {
    grid-template-columns: 1fr;
  }

  .now__side {
    position: static;
    height: auto;
    border-left: 0;
    border-top: 1px solid var(--relay-border);
  }

  .wait__row {
    grid-template-columns: 10px 64px minmax(0, 1fr) auto;
  }

  .wait__src,
  .wait__who {
    display: none;
  }
}
</style>
