<script setup lang="ts">
/**
 * The log list, and the entry point to writing one.
 *
 * Two lenses (parent tabs): 浏览 is the default and opens the reader; 编辑 opens
 * the writer. A list that tries to be a dashboard is a list nobody scans, so the
 * rows stay dense — share level and the knowledge marker (LOG-9) still earn their
 * place, and each row also names the other lens so a reader can jump to edit
 * without flipping the tab first.
 */
import { computed, onMounted } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { SHARE_LABELS } from "@/api/types";
import { useLogStore } from "@/stores/logs";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const logs = useLogStore();
const meta = useMetaStore();
const session = useSessionStore();
const route = useRoute();

const isEdit = computed(() => route.name === "logs-edit");
const canWrite = computed(() => session.can("log_write"));

function rowTo(id: string) {
  return isEdit.value
    ? { name: "log-edit" as const, params: { id } }
    : { name: "log" as const, params: { id } };
}

onMounted(() => void logs.load());
</script>

<template>
  <div>
    <p class="muted logs__sub">
      复盘与排查记录。优先从调查详情里的「写日志」进入，而不是从空白页开始。
    </p>

    <p v-if="logs.error" class="notice notice--error">{{ logs.error }}</p>

    <div v-if="logs.items.length" class="logs">
      <div v-for="log in logs.items" :key="log.id" class="logs__row card">
        <RouterLink class="logs__main" :to="rowTo(log.id)">
          <span class="logs__title">{{ log.title }}</span>
          <span class="pill">{{ SHARE_LABELS[log.share_level] }}</span>
          <span v-if="log.knowledge_candidate" class="pill logs__knowledge">知识库</span>
          <span class="muted logs__meta">
            {{ meta.displayName(log.author_id) }} ·
            {{ log.updated_at ? new Date(log.updated_at).toLocaleString() : "—" }} ·
            v{{ log.current_version }}
          </span>
        </RouterLink>
        <span class="logs__actions">
          <RouterLink
            v-if="isEdit"
            class="logs__action"
            :to="{ name: 'log', params: { id: log.id } }"
          >
            浏览
          </RouterLink>
          <RouterLink
            v-else-if="canWrite"
            class="logs__action"
            :to="{ name: 'log-edit', params: { id: log.id } }"
          >
            编辑
          </RouterLink>
        </span>
      </div>
    </div>

    <p v-else-if="!logs.loading" class="empty">
      还没有知识条目。打开一条调查后写复盘，或
      <RouterLink v-if="canWrite" :to="{ name: 'log-new' }">从空白开始</RouterLink>
      <template v-else>等有写权限的同事写一篇</template>。
    </p>
  </div>
</template>

<style scoped>
.logs__sub {
  margin: 0 0 1rem;
  font-size: 0.9rem;
}

.logs {
  display: grid;
  gap: 0.5rem;
}

.logs__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.7rem 0.9rem;
}

.logs__row:hover {
  border-color: var(--relay-accent);
}

.logs__main {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
  flex: 1;
  text-decoration: none;
  color: inherit;
}

.logs__title {
  font-weight: 500;
}

.logs__knowledge {
  border-color: var(--relay-success);
  color: var(--relay-success);
}

.logs__meta {
  margin-left: auto;
  font-size: 0.8rem;
  white-space: nowrap;
}

.logs__actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.logs__action {
  font-size: 0.82rem;
  text-decoration: none;
  color: var(--relay-accent);
  white-space: nowrap;
}
</style>
