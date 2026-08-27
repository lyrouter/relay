<script setup lang="ts">
/**
 * TKT-6 · the board, grouped by status, with drag-and-drop. *Cut candidate #2.*
 *
 * The drag is the only part of this app that can lose somebody's work if it is
 * built naively, so three rules are enforced here:
 *
 * **1 · A drop is a transition, through the same code path as the detail page.**
 * `tickets.transition()` — so the state machine is checked, history is written, and
 * notifications fire. A board that PATCHed `status` directly would be the second
 * write path, and the one that skips all three.
 *
 * **2 · An illegal move snaps back.** The state machine is the server's (§7.2), so
 * the card moves optimistically, the server decides, and a refusal reloads the
 * board rather than leaving the card where the user dropped it. A card sitting in a
 * column it is not in is worse than a card that bounced.
 *
 * **3 · Blocked and Won't Fix ask for a reason before the request** (TKT-3). The
 * server requires it, so a drag without one would be a guaranteed 422 — the prompt
 * is what makes those two columns usable by dragging at all.
 *
 * ⚠️ **Cut candidate #2** (2.5 pd FE). P-5's recommendation is to keep it and
 * schedule it last; if week 7 is tight, deleting this view and its route removes
 * the feature without touching anything else.
 */
import { onMounted, ref } from "vue";
import Draggable from "vuedraggable";

import type { Ticket, TicketStatus } from "@/api/types";
import { STATUS_LABELS, STATUS_ORDER, STATUSES_REQUIRING_REASON } from "@/api/types";
import TicketCard from "@/components/TicketCard.vue";
import TicketFiltersBar from "@/components/TicketFiltersBar.vue";
import { useTicketStore } from "@/stores/tickets";

const tickets = useTicketStore();
const moving = ref(false);

onMounted(() => void tickets.load({ board: true }));

interface DropEvent {
  added?: { element: Ticket };
}

/**
 * A card landed in `status`.
 *
 * `vuedraggable` has already moved the DOM node by the time this fires, which is
 * the optimistic half. The reload on failure is the honest half.
 */
async function onDrop(status: TicketStatus, event: DropEvent): Promise<void> {
  const ticket = event.added?.element;
  if (!ticket || ticket.status === status) return;

  let reason: string | undefined;
  if (STATUSES_REQUIRING_REASON.includes(status)) {
    // Native prompt: a modal component for one field would be the wrong 0.5 pd in
    // a 2.5 pd view, and this asks the question the server is about to ask.
    const answer = window.prompt(
      `移到「${STATUS_LABELS[status]}」需要写明原因（TKT-3）：`,
      "",
    );
    if (!answer || !answer.trim()) {
      await tickets.load({ board: true });
      return;
    }
    reason = answer.trim();
  }

  moving.value = true;
  try {
    const ok = await tickets.transition(ticket, status, reason);
    // Either way the board is re-read: on success other people's changes may have
    // landed too, and on failure the card has to go back where it belongs.
    await tickets.load({ board: true });
    if (!ok && !tickets.conflict && !tickets.error) {
      tickets.error = `无法把 ${ticket.key} 移到「${STATUS_LABELS[status]}」。`;
    }
  } finally {
    moving.value = false;
  }
}
</script>

<template>
  <section>
    <p v-if="moving" class="muted board__saving">保存中…</p>

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
    <p v-if="tickets.conflict" class="notice notice--conflict">{{ tickets.conflict }}</p>

    <TicketFiltersBar :filters="tickets.filters" @change="tickets.load({ board: true })" />

    <div class="board">
      <div v-for="status in STATUS_ORDER" :key="status" class="board__column">
        <header class="board__head">
          <span class="pill" :class="`pill--${status}`">{{ STATUS_LABELS[status] }}</span>
          <span class="muted">{{ tickets.byStatus[status].length }}</span>
        </header>

        <Draggable
          class="board__drop"
          :list="tickets.byStatus[status]"
          group="tickets"
          item-key="id"
          @change="onDrop(status, $event as DropEvent)"
        >
          <template #item="{ element }">
            <TicketCard :ticket="element" compact />
          </template>
        </Draggable>
      </div>
    </div>

    <p class="muted board__note">
      看板一次载入最近更新的 200 条调查。更早的记录请到「工作 · 列表」筛选分页。
    </p>
  </section>
</template>

<style scoped>
.board {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(240px, 1fr);
  gap: 0.75rem;
  overflow-x: auto;
  padding-bottom: 0.5rem;
  align-items: start;
}

.board__column {
  background: var(--relay-surface-alt);
  border: 1px solid var(--relay-border);
  border-radius: 8px;
  padding: 0.6rem;
  min-height: 200px;
}

.board__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.55rem;
  font-size: 0.85rem;
}

/* A tall drop zone even when empty: a 4px-high target is a column you cannot
   drag into. */
.board__drop {
  display: grid;
  gap: 0.45rem;
  min-height: 120px;
}

.board__saving {
  font-size: 0.82rem;
}

.board__note {
  margin-top: 1rem;
  font-size: 0.8rem;
}
</style>
