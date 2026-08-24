<script setup lang="ts">
/**
 * LOG-4 · the version list, the line diff, and the rollback.
 *
 * Two things this panel has to say out loud, because the mechanics are unusual and
 * the design chose them deliberately:
 *
 * **Every save is a version, and that is the recovery story.** Autosave mints them
 * (§6.2), so the list is long by design — a long list here means nothing was lost,
 * not that something went wrong.
 *
 * **Rollback appends.** It writes a *new* version equal to the old one. The button
 * says "回到这一版（会新增一个版本）" rather than "恢复", because "恢复" implies the
 * versions after it disappear, and no code path deletes or edits a version row.
 */
import { computed, ref, watch } from "vue";

import { useLogStore } from "@/stores/logs";
import { useMetaStore } from "@/stores/meta";

const props = defineProps<{ logId: string }>();
const emit = defineEmits<{ restored: [] }>();

const logs = useLogStore();
const meta = useMetaStore();

const from = ref<number | null>(null);
const to = ref<number | null>(null);
const busy = ref(false);

const canDiff = computed(() => from.value !== null && to.value !== null && from.value !== to.value);

async function showDiff(): Promise<void> {
  if (!canDiff.value) return;
  await logs.loadDiff(props.logId, from.value as number, to.value as number);
}

async function rollback(version: number): Promise<void> {
  busy.value = true;
  try {
    if (await logs.rollback(props.logId, version)) emit("restored");
  } finally {
    busy.value = false;
  }
}

// Default the comparison to "the last change": the question people arrive with is
// almost always "what changed just now?", not "what changed since version 3?".
watch(
  () => logs.versions,
  (list) => {
    if (list.length >= 2 && from.value === null) {
      from.value = list[list.length - 2].version_no;
      to.value = list[list.length - 1].version_no;
    }
  },
  { immediate: true },
);
</script>

<template>
  <section class="history card">
    <header class="history__head">
      <h2 class="history__title">版本历史</h2>
      <p class="muted history__note">
        每次自动保存都会存成一个版本，所以列表很长是正常的 —— 那说明没有东西丢过。
        保留 90 天，最新版本永久保留。
      </p>
    </header>

    <div class="history__body">
      <ol class="history__list">
        <li v-for="version in [...logs.versions].reverse()" :key="version.version_no">
          <div class="history__row">
            <span class="history__no">v{{ version.version_no }}</span>
            <span class="muted history__when">
              {{ version.created_at ? new Date(version.created_at).toLocaleString() : "—" }}
            </span>
            <span class="muted history__who">{{ meta.displayName(version.author_id) }}</span>
            <span v-if="version.rolled_back_from" class="pill">
              由 v{{ version.rolled_back_from }} 回退而来
            </span>
            <button
              type="button"
              class="button history__restore"
              :disabled="busy"
              @click="rollback(version.version_no)"
            >
              回到这一版（会新增一个版本）
            </button>
          </div>
        </li>
      </ol>

      <div class="history__diff">
        <div class="toolbar">
          <label>
            <span class="muted">从</span>
            <select v-model.number="from" class="select">
              <option v-for="one in logs.versions" :key="one.version_no" :value="one.version_no">
                v{{ one.version_no }}
              </option>
            </select>
          </label>
          <label>
            <span class="muted">到</span>
            <select v-model.number="to" class="select">
              <option v-for="one in logs.versions" :key="one.version_no" :value="one.version_no">
                v{{ one.version_no }}
              </option>
            </select>
          </label>
          <button type="button" class="button" :disabled="!canDiff" @click="showDiff">
            对比
          </button>
        </div>

        <pre v-if="logs.diff.length" class="history__lines"><code><span
          v-for="(line, index) in logs.diff"
          :key="index"
          :class="`history__line history__line--${line.op}`"
        >{{ line.op === "add" ? "+" : line.op === "remove" ? "-" : " " }} {{ line.text }}
</span></code></pre>
        <p v-else class="muted">选择两个版本后点"对比"。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.history {
  padding: 1rem 1.1rem;
}

.history__head {
  margin-bottom: 0.8rem;
}

.history__title {
  margin: 0 0 0.25rem;
  font-size: 1rem;
}

.history__note {
  margin: 0;
  font-size: 0.82rem;
}

.history__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 1.25rem;
}

@media (max-width: 900px) {
  .history__body {
    grid-template-columns: 1fr;
  }
}

.history__list {
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 320px;
  overflow: auto;
  font-size: 0.85rem;
}

.history__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--relay-border);
}

.history__no {
  font-family: var(--relay-mono);
}

.history__when,
.history__who {
  font-size: 0.8rem;
}

.history__restore {
  margin-left: auto;
  font-size: 0.78rem;
  padding: 0.15rem 0.5rem;
}

.history__lines {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  background: var(--relay-code-bg);
  border: 1px solid var(--relay-border);
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  font-size: 0.8rem;
}

.history__line {
  display: block;
  white-space: pre-wrap;
}

.history__line--add {
  color: var(--relay-success);
}

.history__line--remove {
  color: var(--relay-danger);
}
</style>
