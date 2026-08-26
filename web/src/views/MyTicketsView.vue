<script setup lang="ts">
/**
 * TKT-7 · "my tickets".
 *
 * Assigned to me, split into "还在我手上" and "已经完成". Filtered **server-side**
 * (the store sets `assignee_id`), not by filtering a page of 50 in the browser —
 * client-side filtering of a paged list silently hides everything on page two.
 */
import { computed, onMounted } from "vue";

import TicketCard from "@/components/TicketCard.vue";
import { useSessionStore } from "@/stores/session";
import { useTicketStore } from "@/stores/tickets";

const tickets = useTicketStore();
const session = useSessionStore();

const open = computed(() =>
  tickets.items.filter((one) => one.status !== "resolved" && one.status !== "closed"),
);
const closed = computed(() =>
  tickets.items.filter((one) => one.status === "resolved" || one.status === "closed"),
);

onMounted(async () => {
  const me = session.session?.user_id;
  if (me) await tickets.loadMine(me);
});
</script>

<template>
  <section>
    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>

    <h2 class="my__group">还在我手上（{{ open.length }}）</h2>
    <div v-if="open.length" class="my__list">
      <TicketCard v-for="ticket in open" :key="ticket.id" :ticket="ticket" />
    </div>
    <p v-else class="empty">没有待处理的调查。打开的会同时出现在「此刻 · 等你接力」。</p>

    <template v-if="closed.length">
      <h2 class="my__group">已经完成（{{ closed.length }}）</h2>
      <div class="my__list">
        <TicketCard v-for="ticket in closed" :key="ticket.id" :ticket="ticket" />
      </div>
      <!-- S-23 removed the terminal states, and people need to know: a finished
           ticket can come back. -->
      <p class="muted my__note">
        已完成和不修的调查都可以被重新打开（S-23），所以这一组不是「归档」。
      </p>
    </template>

    <div v-if="tickets.hasMore" class="my__more">
      <button type="button" class="button" :disabled="tickets.loading" @click="tickets.loadMore()">
        载入更多
      </button>
    </div>
  </section>
</template>

<style scoped>
.my__group {
  font-size: 1rem;
  margin: 1.2rem 0 0.6rem;
}

.my__list {
  display: grid;
  gap: 0.5rem;
}

.my__note {
  font-size: 0.8rem;
  margin-top: 0.6rem;
}

.my__more {
  margin-top: 1rem;
  text-align: center;
}
</style>
