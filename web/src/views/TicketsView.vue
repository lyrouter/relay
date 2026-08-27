<script setup lang="ts">
/**
 * TKT-5 · browse / manage tickets.
 *
 * Used as **上下文** (chain browse + keyword) and under **工作** (list lens).
 * Route meta `surface` picks the framing; the data path is the same.
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import type { Ticket } from "@/api/types";
import TicketCard from "@/components/TicketCard.vue";
import TicketCreateForm from "@/components/TicketCreateForm.vue";
import TicketFiltersBar from "@/components/TicketFiltersBar.vue";
import { emptyFilters, useTicketStore } from "@/stores/tickets";
import { useSessionStore } from "@/stores/session";

const tickets = useTicketStore();
const session = useSessionStore();
const route = useRoute();
const router = useRouter();
const creating = ref(false);

const isContext = computed(() => route.meta.surface === "context");
const showTitle = computed(() => isContext.value);

function applyQueryAndLoad(): void {
  tickets.filters = emptyFilters();
  const q = route.query.q;
  if (typeof q === "string" && q.trim()) tickets.filters.keyword = q.trim();
  void tickets.load();
}

onMounted(applyQueryAndLoad);
watch(() => route.query.q, applyQueryAndLoad);

function onCreated(ticket: Ticket): void {
  creating.value = false;
  void router.push({
    name: "ticket",
    params: { tenantSlug: session.tenantSlug || "-", number: String(ticket.number) },
  });
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
        @click="creating = true"
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
        @click="creating = true"
      >
        新建
      </button>
    </div>

    <TicketCreateForm v-if="creating" @created="onCreated" @cancel="creating = false" />

    <TicketFiltersBar :filters="tickets.filters" @change="tickets.load()" />

    <p v-if="tickets.error && !creating" class="notice notice--error">{{ tickets.error }}</p>
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

.empty code {
  font-family: var(--relay-mono);
  font-size: 0.85em;
}
</style>
