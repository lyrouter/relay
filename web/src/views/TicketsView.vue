<script setup lang="ts">
/**
 * TKT-5 · the list view with filters and keyset paging.
 *
 * "载入更多" rather than numbered pages, because the ordering key is `updated_at`:
 * page 3 of a list that reorders while you read it is not a stable thing to ask
 * for, and the cursor is what makes "the next 50 after what I have seen" exact.
 */
import { onMounted, ref } from "vue";

import TicketCard from "@/components/TicketCard.vue";
import TicketFiltersBar from "@/components/TicketFiltersBar.vue";
import { useTicketStore } from "@/stores/tickets";
import { useSessionStore } from "@/stores/session";

const tickets = useTicketStore();
const session = useSessionStore();
const creating = ref(false);
const draftTitle = ref("");

onMounted(() => void tickets.load());

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
    <h1 class="page-title">
      工单
      <button
        v-if="session.can('ticket_write')"
        type="button"
        class="button button--primary"
        @click="creating = !creating"
      >
        新建
      </button>
    </h1>

    <form v-if="creating" class="new-ticket card" @submit.prevent="createTicket">
      <input v-model="draftTitle" class="input" placeholder="一句话说清问题" autofocus />
      <button class="button button--primary" type="submit">创建</button>
      <p class="muted new-ticket__hint">
        创建后是"待办 / P2 / 任务"，在详情页里再改类型、优先级和负责人。
      </p>
    </form>

    <TicketFiltersBar :filters="tickets.filters" @change="tickets.load()" />

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
    <p v-if="tickets.conflict" class="notice notice--conflict">{{ tickets.conflict }}</p>

    <div v-if="tickets.items.length" class="list">
      <TicketCard v-for="ticket in tickets.items" :key="ticket.id" :ticket="ticket" />
    </div>
    <p v-else-if="!tickets.loading" class="empty">没有符合条件的工单。</p>

    <div v-if="tickets.hasMore" class="list__more">
      <button type="button" class="button" :disabled="tickets.loading" @click="tickets.loadMore()">
        {{ tickets.loading ? "载入中…" : "载入更多" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
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
</style>
