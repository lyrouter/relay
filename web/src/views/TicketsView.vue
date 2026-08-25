<script setup lang="ts">
/**
 * TKT-5 · browse / manage tickets.
 *
 * Used as **上下文** (chain browse + keyword) and under **工作** (list lens).
 * Route meta `surface` picks the framing; the data path is the same.
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";

import TicketCard from "@/components/TicketCard.vue";
import TicketFiltersBar from "@/components/TicketFiltersBar.vue";
import { emptyFilters, useTicketStore } from "@/stores/tickets";
import { useSessionStore } from "@/stores/session";

const tickets = useTicketStore();
const session = useSessionStore();
const route = useRoute();
const creating = ref(false);
const draftTitle = ref("");

const isContext = computed(() => route.meta.surface === "context");
const showTitle = computed(() => isContext.value);

onMounted(() => {
  tickets.filters = emptyFilters();
  void tickets.load();
});

async function createTicket(): Promise<void> {
  const title = draftTitle.value.trim();
  if (!title) return;
  const created = await tickets.create({ type: "task", title });
  if (created) {
    draftTitle.value = "";
    creating.value = false;
    await tickets.load();
  }
}
</script>

<template>
  <section>
    <h1 v-if="showTitle" class="page-title">
      上下文
      <button
        v-if="session.can('ticket_write')"
        type="button"
        class="button button--primary"
        @click="creating = !creating"
      >
        新建调查
      </button>
    </h1>
    <p v-if="isContext" class="muted surface__sub">
      按关键字与筛选浏览接力链。日常注意力请回
      <RouterLink :to="{ name: 'now' }">此刻</RouterLink>。
    </p>

    <div v-if="!showTitle" class="toolbar surface__toolbar">
      <button
        v-if="session.can('ticket_write')"
        type="button"
        class="button button--primary"
        @click="creating = !creating"
      >
        新建
      </button>
    </div>

    <form v-if="creating" class="new-ticket card" @submit.prevent="createTicket">
      <input v-model="draftTitle" class="input" placeholder="一句话说清问题" autofocus />
      <button class="button button--primary" type="submit">创建</button>
      <p class="muted new-ticket__hint">
        创建后是「待办 / P2 / 任务」，在详情里补 AI 上下文与负责人。
      </p>
    </form>

    <TicketFiltersBar :filters="tickets.filters" @change="tickets.load()" />

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
    <p v-if="tickets.conflict" class="notice notice--conflict">{{ tickets.conflict }}</p>

    <div v-if="tickets.items.length" class="list">
      <TicketCard v-for="ticket in tickets.items" :key="ticket.id" :ticket="ticket" />
    </div>
    <p v-else-if="!tickets.loading" class="empty">
      没有符合条件的调查。从企微 <code>@Relay 建单</code>，或点上方新建。
    </p>

    <div v-if="tickets.hasMore" class="list__more">
      <button type="button" class="button" :disabled="tickets.loading" @click="tickets.loadMore()">
        {{ tickets.loading ? "载入中…" : "载入更多" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.surface__sub {
  margin: -0.5rem 0 1rem;
  font-size: 0.9rem;
}

.surface__toolbar {
  margin-bottom: 0.75rem;
}

.list {
  display: grid;
  gap: 0.5rem;
}

.list__more {
  margin-top: 1rem;
  text-align: center;
}

.new-ticket {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.8rem;
  margin-bottom: 1rem;
}

.new-ticket .input {
  flex: 1;
  min-width: 240px;
}

.new-ticket__hint {
  flex-basis: 100%;
  margin: 0;
  font-size: 0.8rem;
}

.empty code {
  font-family: var(--relay-mono);
  font-size: 0.85em;
}
</style>
