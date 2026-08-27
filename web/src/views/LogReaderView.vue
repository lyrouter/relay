<script setup lang="ts">
/**
 * Read-only log. The knowledge rail's default landing for an article: render,
 * do not acquire the edit lock. Writers switch to 编辑 (the parent tab, or the
 * button here) which is a different route and a different component.
 */
import { computed, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { SHARE_LABELS } from "@/api/types";
import MarkdownView from "@/components/MarkdownView.vue";
import { useLogStore } from "@/stores/logs";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const route = useRoute();
const logs = useLogStore();
const meta = useMetaStore();
const session = useSessionStore();

const logId = computed(() => route.params.id as string);
const canWrite = computed(() => session.can("log_write"));

watch(
  logId,
  (id) => {
    void logs.open(id);
  },
  { immediate: true },
);
</script>

<template>
  <article v-if="logs.current" class="reader">
    <header class="reader__head">
      <h2 class="reader__title">{{ logs.current.title }}</h2>
      <RouterLink
        v-if="canWrite"
        class="button"
        :to="{ name: 'log-edit', params: { id: logs.current.id } }"
      >
        编辑
      </RouterLink>
    </header>

    <p class="muted reader__meta">
      {{ meta.displayName(logs.current.author_id) }} ·
      {{ logs.current.updated_at ? new Date(logs.current.updated_at).toLocaleString() : "—" }} ·
      v{{ logs.current.current_version }}
      <span class="pill">{{ SHARE_LABELS[logs.current.share_level] }}</span>
      <span v-if="logs.current.knowledge_candidate" class="pill reader__knowledge">知识库</span>
    </p>

    <div class="reader__body card">
      <MarkdownView
        :source="logs.current.body || '这篇还没有正文。'"
        :format="logs.current.format"
      />
    </div>

    <section v-if="logs.attachments.length" class="reader__attachments card">
      <h3 class="reader__attachments-title">附件</h3>
      <ul>
        <li v-for="one in logs.attachments" :key="one.id">
          {{ one.filename }}
          <span class="muted">（{{ Math.round(one.size / 1024) }} KB）</span>
        </li>
      </ul>
    </section>
  </article>

  <p v-else-if="logs.error" class="notice notice--error">{{ logs.error }}</p>
  <p v-else-if="logs.loading" class="muted">载入中…</p>
</template>

<style scoped>
.reader {
  display: grid;
  gap: 0.85rem;
}

.reader__head {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.reader__head a.button {
  text-decoration: none;
}

.reader__title {
  flex: 1;
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  line-height: 1.3;
}

.reader__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem;
  margin: 0;
  font-size: 0.85rem;
}

.reader__knowledge {
  border-color: var(--relay-success);
  color: var(--relay-success);
}

.reader__body {
  padding: 1.1rem 1.25rem;
  min-height: 12rem;
}

.reader__attachments {
  padding: 0.9rem 1.1rem;
}

.reader__attachments-title {
  margin: 0 0 0.5rem;
  font-size: 0.95rem;
}

.reader__attachments ul {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.9rem;
}
</style>
