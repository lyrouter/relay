<script setup lang="ts">
import { modelTone } from "@/lib/context";

const props = defineProps<{ model?: string }>();
</script>

<template>
  <span v-if="props.model" class="model" :data-tone="modelTone(props.model)">
    {{ props.model }}
  </span>
</template>

<style scoped>
.model {
  display: inline-flex;
  align-items: center;
  font-family: var(--relay-mono);
  font-size: 0.72rem;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  border: 1px solid transparent;
  white-space: nowrap;
}

.model[data-tone="green"] {
  color: #047857;
  background: #ecfdf5;
  border-color: #a7f3d0;
}

.model[data-tone="blue"] {
  color: #1d4ed8;
  background: #eff6ff;
  border-color: #bfdbfe;
}

.model[data-tone="violet"] {
  color: #6d28d9;
  background: #f5f3ff;
  border-color: #ddd6fe;
}

.model[data-tone="gray"] {
  color: var(--relay-text-muted);
  background: var(--relay-surface-alt);
  border-color: var(--relay-border);
}
</style>
