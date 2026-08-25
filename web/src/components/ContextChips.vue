<script setup lang="ts">
/**
 * Compact AI-context / chain density chips for list rows and 此刻.
 * Empty slots stay visible when `showEmpty` so MVP blank fields remain honest.
 */
import { computed } from "vue";

import type { Ticket } from "@/api/types";
import { buildChain, formatContextValue } from "@/lib/context";

const props = withDefaults(
  defineProps<{
    ticket: Ticket;
    showEmpty?: boolean;
    /** When true, show alert/im/trace/log/pr presence rather than field values. */
    chain?: boolean;
  }>(),
  { showEmpty: false, chain: false },
);

const CHIP_KEYS = ["trace_id", "model", "provider", "error_class"] as const;

const fieldChips = computed(() =>
  CHIP_KEYS.map((key) => ({
    key,
    value: formatContextValue(props.ticket.ai_context?.[key]),
  })).filter((one) => props.showEmpty || one.value),
);

const chainChips = computed(() =>
  buildChain(props.ticket).filter((node) => node.id !== "ticket"),
);
</script>

<template>
  <div class="chips" aria-label="上下文">
    <template v-if="props.chain">
      <span
        v-for="node in chainChips"
        :key="node.id"
        class="chip"
        :class="{ 'chip--empty': !node.present }"
        :title="node.detail || node.label"
      >
        {{ node.label
        }}{{ node.present && node.detail ? ` · ${node.detail}` : "" }}
      </span>
    </template>
    <template v-else>
      <span
        v-for="chip in fieldChips"
        :key="chip.key"
        class="chip"
        :class="{ 'chip--empty': !chip.value }"
      >
        <template v-if="chip.value">{{ chip.key }} · {{ chip.value }}</template>
        <template v-else>{{ chip.key }}</template>
      </span>
      <span v-if="props.ticket.source" class="chip chip--source">
        {{ props.ticket.source }}
      </span>
    </template>
  </div>
</template>

<style scoped>
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  min-width: 0;
}

.chip {
  font-family: var(--relay-mono);
  font-size: 0.72rem;
  line-height: 1.3;
  padding: 0.1rem 0.4rem;
  border: 1px dashed var(--relay-border);
  border-radius: 4px;
  color: var(--relay-text);
  background: var(--relay-surface-alt);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip:not(.chip--empty) {
  border-style: solid;
  border-color: color-mix(in srgb, var(--relay-accent) 45%, var(--relay-border));
  color: var(--relay-accent);
  background: var(--relay-accent-soft);
}

.chip--empty {
  color: var(--relay-text-muted);
  opacity: 0.75;
}

.chip--source {
  font-family: inherit;
  color: var(--relay-text-muted);
  border-style: solid;
}
</style>
