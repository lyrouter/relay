<script setup lang="ts">
/**
 * TKT-5's filter set: status, assignee, priority, label, iteration, keyword.
 *
 * Shared by the list and the board so the two cannot drift into different filter
 * behaviour — a board that silently ignores the label filter is the kind of bug
 * nobody reports, they just stop trusting the board.
 *
 * The keyword field is visibly separate, because it *is* separate: it goes to
 * LOG-8's search endpoint (pgroonga) rather than to the list query. Mixing it in
 * would give the product two search behaviours that disagree about Chinese
 * tokenisation, and the cheap one would be the one people hit first.
 */
import { STATUS_LABELS, STATUS_ORDER, PRIORITY_LABELS } from "@/api/types";
import type { Priority, TicketStatus } from "@/api/types";
import { useMetaStore } from "@/stores/meta";
import type { TicketFilters } from "@/stores/tickets";

const props = defineProps<{ filters: TicketFilters }>();
const emit = defineEmits<{ change: [] }>();

const meta = useMetaStore();
const priorities = Object.keys(PRIORITY_LABELS) as Priority[];

function toggleStatus(status: TicketStatus): void {
  const list = props.filters.status;
  const index = list.indexOf(status);
  if (index === -1) list.push(status);
  else list.splice(index, 1);
  emit("change");
}

function togglePriority(priority: string): void {
  const list = props.filters.priority;
  const index = list.indexOf(priority);
  if (index === -1) list.push(priority);
  else list.splice(index, 1);
  emit("change");
}

function reset(): void {
  props.filters.status.splice(0);
  props.filters.priority.splice(0);
  props.filters.assignee_id = undefined;
  props.filters.label_id = undefined;
  props.filters.iteration_id = undefined;
  props.filters.keyword = undefined;
  emit("change");
}
</script>

<template>
  <div class="filters">
    <div class="filters__row">
      <button
        v-for="status in STATUS_ORDER"
        :key="status"
        type="button"
        class="pill filters__toggle"
        :class="[`pill--${status}`, { 'filters__toggle--on': props.filters.status.includes(status) }]"
        @click="toggleStatus(status)"
      >
        {{ STATUS_LABELS[status] }}
      </button>

      <span class="filters__divider" />

      <button
        v-for="priority in priorities"
        :key="priority"
        type="button"
        class="pill filters__toggle"
        :class="[
          `pill--${priority}`,
          { 'filters__toggle--on': props.filters.priority.includes(priority) },
        ]"
        @click="togglePriority(priority)"
      >
        {{ PRIORITY_LABELS[priority] }}
      </button>
    </div>

    <div class="filters__row">
      <select
        class="select"
        :value="props.filters.assignee_id ?? ''"
        @change="
          props.filters.assignee_id =
            ($event.target as HTMLSelectElement).value || undefined;
          emit('change');
        "
      >
        <option value="">全部负责人</option>
        <option v-for="one in meta.members" :key="one.user_id" :value="one.user_id">
          {{ one.display_name }}
        </option>
      </select>

      <select
        class="select"
        :value="props.filters.label_id ?? ''"
        @change="
          props.filters.label_id = ($event.target as HTMLSelectElement).value || undefined;
          emit('change');
        "
      >
        <option value="">全部标签</option>
        <option v-for="one in meta.labels" :key="one.id" :value="one.id">{{ one.name }}</option>
      </select>

      <select
        class="select"
        :value="props.filters.iteration_id ?? ''"
        @change="
          props.filters.iteration_id =
            ($event.target as HTMLSelectElement).value || undefined;
          emit('change');
        "
      >
        <option value="">全部迭代</option>
        <option v-for="one in meta.iterations" :key="one.id" :value="one.id">
          {{ one.name }}{{ one.closed ? "（已关闭）" : "" }}
        </option>
      </select>

      <!-- Enter rather than input: search is a request per submit, not per
           keystroke. -->
      <input
        class="input filters__keyword"
        type="search"
        placeholder="关键词（全文检索）"
        :value="props.filters.keyword ?? ''"
        @change="
          props.filters.keyword = ($event.target as HTMLInputElement).value || undefined;
          emit('change');
        "
      />

      <button type="button" class="button" @click="reset">清空</button>
    </div>
  </div>
</template>

<style scoped>
.filters {
  display: grid;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.filters__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}

.filters__divider {
  width: 1px;
  height: 1.1rem;
  background: var(--relay-border);
  margin: 0 0.35rem;
}

.filters__toggle {
  cursor: pointer;
  opacity: 0.55;
}

.filters__toggle--on {
  opacity: 1;
  font-weight: 600;
}

.filters__keyword {
  min-width: 220px;
}
</style>
