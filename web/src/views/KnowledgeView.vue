<script setup lang="ts">
/**
 * 知识 · browse / edit as lenses on the same logs.
 *
 * Default is 浏览: the rail item is a knowledge base, not an editor. Clicking a
 * row should render the article. 编辑 is the writer lens (list → editor, plus
 * 「写一篇」). The tabs follow 工作's 列表 / 看板 pattern so the two surfaces
 * share one piece of IA.
 */
import { computed } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { useSessionStore } from "@/stores/session";

const route = useRoute();
const session = useSessionStore();

const logId = computed(() => {
  const id = route.params.id;
  return typeof id === "string" ? id : null;
});

const canWrite = computed(() => session.can("log_write"));

/** On an article, the tab switches mode for *this* log; otherwise it switches the list. */
const browseTo = computed(() => {
  if (logId.value && route.name !== "log-new") {
    return { name: "log" as const, params: { id: logId.value } };
  }
  return { name: "logs" as const };
});

const editTo = computed(() => {
  if (logId.value && route.name !== "log-new") {
    return { name: "log-edit" as const, params: { id: logId.value } };
  }
  return { name: "logs-edit" as const };
});

const browseOn = computed(() => route.name === "logs" || route.name === "log");
const editOn = computed(
  () => route.name === "logs-edit" || route.name === "log-edit" || route.name === "log-new",
);
</script>

<template>
  <section class="knowledge">
    <header class="knowledge__head">
      <h1 class="page-title">知识</h1>
      <nav class="knowledge__tabs" aria-label="知识模式">
        <RouterLink :to="browseTo" :class="{ 'knowledge__tab--on': browseOn }">浏览</RouterLink>
        <RouterLink
          v-if="canWrite"
          :to="editTo"
          :class="{ 'knowledge__tab--on': editOn }"
        >
          编辑
        </RouterLink>
      </nav>
      <RouterLink
        v-if="canWrite"
        class="button button--primary knowledge__write"
        :to="{ name: 'log-new' }"
      >
        写一篇
      </RouterLink>
    </header>
    <RouterView />
  </section>
</template>

<style scoped>
.knowledge__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.75rem 1.25rem;
  margin-bottom: 0.5rem;
}

.knowledge__head .page-title {
  margin-bottom: 0;
}

.knowledge__tabs {
  display: flex;
  gap: 0.85rem;
  font-size: 0.9rem;
}

.knowledge__tabs a {
  text-decoration: none;
  color: var(--relay-text-muted);
  padding: 0.15rem 0;
  border-bottom: 2px solid transparent;
}

.knowledge__tabs a.knowledge__tab--on {
  color: var(--relay-text);
  border-bottom-color: var(--relay-accent);
}

.knowledge__write {
  margin-left: auto;
  text-decoration: none;
}
</style>
