<script setup lang="ts">
/**
 * One ticket, as a card. Used by the board (TKT-6), list (TKT-5), and 此刻.
 *
 * The key is rendered as `RL-331` and links to the **tenant-qualified** permalink
 * (S-12). Context chips surface before type/iteration chrome so the row reads as
 * a chain node, not a generic tracker card.
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import type { Ticket } from "@/api/types";
import { PRIORITY_LABELS, STATUS_LABELS, TYPE_LABELS, CATEGORY_LABELS } from "@/api/types";
import ContextChips from "@/components/ContextChips.vue";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const props = defineProps<{ ticket: Ticket; compact?: boolean }>();

const meta = useMetaStore();
const session = useSessionStore();

/**
 * API-6's `submitter` is an open JSON object on the wire, so it is narrowed here
 * rather than in the template — a template cannot assert a type, and the shape is
 * the consumer's to send (`name` plus optional `email` / `external_id`).
 */
const submitterName = computed(() => {
  const submitter = props.ticket.submitter as { name?: string } | null | undefined;
  return submitter?.name ?? "外部用户";
});
</script>

<template>
  <article class="ticket-card card" :class="{ 'ticket-card--compact': props.compact }">
    <header class="ticket-card__head">
      <RouterLink
        class="ticket-card__key"
        :to="{
          name: 'ticket',
          params: { tenantSlug: session.tenantSlug || '-', number: String(props.ticket.number) },
        }"
      >
        {{ props.ticket.key }}
      </RouterLink>
      <span class="pill" :class="`pill--${props.ticket.priority}`">
        {{ PRIORITY_LABELS[props.ticket.priority] }}
      </span>
      <span v-if="!props.compact" class="pill" :class="`pill--${props.ticket.status}`">
        {{ STATUS_LABELS[props.ticket.status] }}
      </span>
    </header>

    <p class="ticket-card__title">{{ props.ticket.title }}</p>

    <ContextChips :ticket="props.ticket" :chain="Boolean(props.compact)" />

    <footer class="ticket-card__foot muted">
      <span>{{ TYPE_LABELS[props.ticket.type] }}</span>
      <span v-if="props.ticket.category">
        · {{ CATEGORY_LABELS[props.ticket.category] }}
      </span>
      <span>· {{ meta.displayName(props.ticket.assignee_id) }}</span>
      <span v-for="id in props.ticket.label_ids" :key="id" class="pill ticket-card__label">
        {{ meta.labelName(id) }}
      </span>
      <!-- API-6: a ticket filed through the gateway shows who submitted it. Not
           the reporter (S-10) — the reporter is the machine principal. -->
      <span v-if="props.ticket.submitter" class="ticket-card__submitter">
        · 由 {{ submitterName }} 通过 {{ props.ticket.source ?? "外部系统" }} 提交
      </span>
    </footer>
  </article>
</template>

<style scoped>
.ticket-card {
  padding: 0.65rem 0.8rem;
  display: grid;
  gap: 0.35rem;
}

.ticket-card--compact {
  padding: 0.5rem 0.6rem;
}

.ticket-card__head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.ticket-card__key {
  font-family: var(--relay-mono);
  font-size: 0.82rem;
  text-decoration: none;
}

.ticket-card__title {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.4;
}

.ticket-card__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.78rem;
}

.ticket-card__label {
  font-size: 0.72rem;
}

.ticket-card__submitter {
  font-size: 0.75rem;
}
</style>
