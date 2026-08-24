<script setup lang="ts">
/**
 * The log list, and the entry point to writing one.
 *
 * Deliberately plain: LOG-1's value is in the editor, and a list that tries to be
 * a dashboard is a list nobody scans. Two affordances earn their place — the share
 * level (so an author can see at a glance what is still private) and the knowledge
 * marker (LOG-9, which is what the acceptance count is drawn from).
 */
import { onMounted } from "vue";
import { RouterLink } from "vue-router";

import { SHARE_LABELS } from "@/api/types";
import { useLogStore } from "@/stores/logs";
import { useMetaStore } from "@/stores/meta";

const logs = useLogStore();
const meta = useMetaStore();

onMounted(() => void logs.load());
</script>

<template>
  <section>
    <h1 class="page-title">
      日志
      <RouterLink class="button button--primary" :to="{ name: 'log-new' }">写一篇</RouterLink>
    </h1>

    <p v-if="logs.error" class="notice notice--error">{{ logs.error }}</p>

    <div v-if="logs.items.length" class="logs">
      <RouterLink
        v-for="log in logs.items"
        :key="log.id"
        class="logs__row card"
        :to="{ name: 'log', params: { id: log.id } }"
      >
        <span class="logs__title">{{ log.title }}</span>
        <span class="pill">{{ SHARE_LABELS[log.share_level] }}</span>
        <span v-if="log.knowledge_candidate" class="pill logs__knowledge">知识库</span>
        <span class="muted logs__meta">
          {{ meta.displayName(log.author_id) }} ·
          {{ log.updated_at ? new Date(log.updated_at).toLocaleString() : "—" }} ·
          v{{ log.current_version }}
        </span>
      </RouterLink>
    </div>

    <p v-else-if="!logs.loading" class="empty">
      还没有日志。<RouterLink :to="{ name: 'log-new' }">写第一篇</RouterLink>。
    </p>
  </section>
</template>

<style scoped>
.logs {
  display: grid;
  gap: 0.5rem;
}

.logs__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.7rem 0.9rem;
  text-decoration: none;
  color: inherit;
}

.logs__row:hover {
  border-color: var(--relay-accent);
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
</style>
