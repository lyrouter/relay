<script setup lang="ts">
/**
 * Right-rail AI context panel from mockups/now.png and detail.png.
 */
import { computed } from "vue";

import type { Ticket } from "@/api/types";
import { contextValue, copyText } from "@/lib/context";
import { useMetaStore } from "@/stores/meta";

const props = defineProps<{
  ticket: Ticket | null;
  refreshing?: boolean;
}>();

const emit = defineEmits<{ refresh: [] }>();

const meta = useMetaStore();

const fields = computed(() => {
  const configured = meta.ticketFields.filter((one) => one.visible);
  const keys = configured.length
    ? configured.map((one) => ({ key: one.key, label: one.label }))
    : [
        { key: "trace_id", label: "trace_id" },
        { key: "provider", label: "provider" },
        { key: "model", label: "model" },
        { key: "prompt_version", label: "prompt_version" },
        { key: "token_cost", label: "token_cost" },
        { key: "blast_radius", label: "blast_radius" },
      ];
  return keys.map((one) => {
    const value = props.ticket ? contextValue(props.ticket, one.key) : "";
    const emptyLabel =
      one.key === "blast_radius" ? "未评估" : one.key === "prompt_version" ? "未设置" : "未设置";
    return { ...one, value, emptyLabel };
  });
});
</script>

<template>
  <aside class="panel">
    <h2 class="panel__title">AI 上下文</h2>

    <div v-if="!props.ticket" class="panel__empty muted">
      选中一条调查后，这里显示结构化上下文。
    </div>

    <template v-else>
      <div v-for="field in fields" :key="field.key" class="field">
        <div class="field__label">{{ field.label }}</div>
        <div
          class="field__box"
          :class="{
            'field__box--empty': !field.value,
            'field__box--model': field.key === 'model' && field.value,
          }"
        >
          <span class="field__value">{{ field.value || field.emptyLabel }}</span>
          <button
            v-if="field.value"
            type="button"
            class="field__copy"
            title="复制"
            @click="copyText(field.value)"
          >
            ⎘
          </button>
        </div>
      </div>

      <p class="panel__note muted">
        <span aria-hidden="true">ⓘ</span>
        以上信息由系统与 AI 自动提取，可能不完整
      </p>

      <button
        type="button"
        class="button panel__refresh"
        :disabled="props.refreshing"
        @click="emit('refresh')"
      >
        ↻ 刷新上下文
      </button>
    </template>
  </aside>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1.1rem 1rem;
  background: var(--relay-surface);
  border-left: 1px solid var(--relay-border);
  height: 100%;
  min-height: 0;
}

.panel__title {
  margin: 0;
  font-size: 0.95rem;
}

.panel__empty {
  font-size: 0.85rem;
}

.field__label {
  font-size: 0.75rem;
  color: var(--relay-text-muted);
  margin-bottom: 0.25rem;
}

.field__box {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.55rem;
  border-radius: 8px;
  border: 1px solid var(--relay-border);
  background: var(--relay-surface-alt);
  font-family: var(--relay-mono);
  font-size: 0.78rem;
}

.field__box--empty {
  border-style: dashed;
  color: var(--relay-text-muted);
  background: transparent;
}

.field__box--model {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #047857;
}

.field__value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.field__copy {
  border: 0;
  background: transparent;
  color: var(--relay-text-muted);
  cursor: pointer;
  padding: 0;
  font-size: 0.85rem;
}

.panel__note {
  display: flex;
  gap: 0.35rem;
  font-size: 0.75rem;
  margin: 0.25rem 0 0;
  line-height: 1.4;
}

.panel__refresh {
  margin-top: auto;
  width: 100%;
}
</style>
