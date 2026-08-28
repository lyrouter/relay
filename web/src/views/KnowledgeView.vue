<script setup lang="ts">
/**
 * 知识 · browse / edit as lenses on the same logs.
 *
 * Default is 浏览: the rail item is a knowledge base, not an editor. Clicking a
 * row should render the article. 编辑 is the writer lens (list → editor, plus
 * 「写一篇」). The tabs follow 工作's 列表 / 看板 pattern so the two surfaces
 * share one piece of IA.
 *
 * 「导入」creates the same kind of log the lenses already know how to open —
 * HTML is converted to Markdown on the server, so there is no second reader.
 */
import { computed, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { ProblemError } from "@/api/client";
import { useLogStore } from "@/stores/logs";
import { useSessionStore } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const logs = useLogStore();

const fileInput = ref<HTMLInputElement | null>(null);
const importing = ref(false);
const importNotice = ref<string | null>(null);

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

function pickFiles(): void {
  importNotice.value = null;
  fileInput.value?.click();
}

async function onFiles(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  if (!files.length) return;

  importing.value = true;
  importNotice.value = null;
  const imported: string[] = [];
  const failed: string[] = [];
  try {
    for (const file of files) {
      try {
        const created = await logs.importNote(file);
        imported.push(created.id);
      } catch (caught) {
        failed.push(
          `${file.name}：${caught instanceof ProblemError ? caught.message : "导入失败"}`,
        );
      }
    }
    if (imported.length === 1 && failed.length === 0) {
      const id = imported[0];
      await router.push(
        editOn.value ? { name: "log-edit", params: { id } } : { name: "log", params: { id } },
      );
      return;
    }
    if (imported.length) await logs.load();
    const parts: string[] = [];
    if (imported.length) parts.push(`已导入 ${imported.length} 篇`);
    if (failed.length) parts.push(failed.join("；"));
    importNotice.value = parts.join("。") || null;
  } finally {
    importing.value = false;
  }
}
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
      <div v-if="canWrite" class="knowledge__actions">
        <input
          ref="fileInput"
          class="knowledge__file"
          type="file"
          accept=".md,.markdown,.mdown,.html,.htm,text/markdown,text/html"
          multiple
          @change="onFiles"
        />
        <button
          type="button"
          class="button"
          :disabled="importing"
          @click="pickFiles"
        >
          {{ importing ? "导入中…" : "导入" }}
        </button>
        <RouterLink class="button button--primary knowledge__write" :to="{ name: 'log-new' }">
          写一篇
        </RouterLink>
      </div>
    </header>
    <p
      v-if="importNotice"
      class="notice knowledge__notice"
      :class="importNotice.includes('：') ? 'notice--error' : 'notice--ok'"
    >
      {{ importNotice }}
    </p>
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

.knowledge__actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-left: auto;
}

.knowledge__file {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.knowledge__write {
  text-decoration: none;
}

.knowledge__notice {
  margin: 0 0 0.75rem;
}
</style>
