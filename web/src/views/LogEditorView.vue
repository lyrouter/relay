<script setup lang="ts">
/**
 * LOG-1/2/3/4/5/6/7/9 · writing a log.
 *
 * This is the page S1 exists for, so the notes are about what it promises the
 * author:
 *
 * **Nothing you typed is lost.** Autosave writes a *version* (LOG-4), so the
 * version list is also the recovery list. When the edit lock lapses and somebody
 * takes over, the previous content already *is* version N — which is why the lock
 * banner is informational rather than a block (S-7).
 *
 * **A rollback is an append.** The button says so, because "回滚" reads like undo
 * and it is not: history is never rewritten (§6.2), and `rolled_back_from`
 * distinguishes a rollback from somebody retyping an old draft.
 *
 * **Sharing is a level, and the default is private.** L0 → L3 in one select, with
 * the level's meaning spelled out rather than abbreviated — "L1" means nothing to
 * somebody deciding whether to show their half-finished investigation to the team.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import type { LogFormat, ShareLevel } from "@/api/types";
import { SHARE_LABELS } from "@/api/types";
import LogEditor from "@/components/LogEditor.vue";
import VersionHistory from "@/components/VersionHistory.vue";
import { LOG_TEMPLATES, dailyTitle, type LogTemplate } from "@/markdown/templates";
import { useLogStore } from "@/stores/logs";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const logs = useLogStore();
const meta = useMetaStore();
const session = useSessionStore();

const title = ref("");
const body = ref("");
const format = ref<LogFormat>("markdown");
const showHistory = ref(false);
const uploading = ref(false);

const logId = computed(() => (route.params.id as string | undefined) ?? null);
const isNew = computed(() => logId.value === null);

/** Whose lock it is, when it is not ours. Advisory — see the module note. */
const lockedByOther = computed(() => {
  const held = logs.lock;
  if (!held || !held.holder_id) return null;
  if (held.holder_id === session.session?.user_id) return null;
  return meta.displayName(held.holder_id);
});

const savedLabel = computed(() => {
  if (logs.saving) return "保存中…";
  if (!logs.savedAt) return isNew.value ? "尚未保存" : "已保存";
  return `已保存 ${logs.savedAt.toLocaleTimeString()}`;
});

onMounted(async () => {
  if (logId.value) {
    await logs.open(logId.value);
    if (logs.current) {
      title.value = logs.current.title;
      body.value = logs.current.body;
      format.value = logs.current.format;
      await logs.acquireLock(logId.value);
    }
  } else {
    // A new log starts as markdown: LOG-1 offers both modes and this is the one
    // the templates and the ticket cards are written for.
    format.value = "markdown";
  }
});

onBeforeUnmount(async () => {
  logs.cancelScheduledSave();
  if (logId.value) await logs.releaseLock(logId.value);
});

/**
 * The first save creates; later ones patch.
 *
 * Creating on first *keystroke* rather than on a button press is deliberate: it
 * is what makes "nothing you typed is lost" true from the first sentence rather
 * than from the first click on 保存.
 */
async function persist(): Promise<void> {
  if (!title.value.trim() && !body.value.trim()) return;
  if (isNew.value) {
    const created = await logs.create({
      title: title.value.trim() || "未命名",
      body: body.value,
      format: format.value,
    });
    if (created) {
      // Replace rather than push: the editor is the same page, and a back button
      // that returns to /logs/new would offer to create a duplicate.
      await router.replace({ name: "log-edit", params: { id: created.id } });
      await logs.acquireLock(created.id);
    }
    return;
  }
  await logs.save({ title: title.value, body: body.value });
}

watch([title, body], () => {
  if (isNew.value) {
    // Debounced through the store so the *first* save is also debounced: a create
    // per keystroke would produce a stack of empty logs.
    logs.scheduleSave({});
    window.clearTimeout(newTimer);
    newTimer = window.setTimeout(() => void persist(), 1200);
    return;
  }
  logs.scheduleSave({ title: title.value, body: body.value });
});

let newTimer: number | undefined;
onBeforeUnmount(() => window.clearTimeout(newTimer));

function applyTemplate(template: LogTemplate): void {
  // Only into an empty document. Overwriting somebody's draft with a template
  // would be the single most destructive button on the page.
  if (body.value.trim()) return;
  body.value = template.body;
  if (!title.value.trim()) {
    title.value = template.id === "daily" ? dailyTitle() : template.title;
  }
}

async function onImage(file: File, insert: (markdown: string) => void): Promise<void> {
  if (isNew.value) {
    // The attachment needs an owner, so the log has to exist first. Saving here
    // rather than refusing keeps the paste working — the author does not care why
    // it would not have.
    await persist();
  }
  const id = logId.value;
  if (!id) return;
  uploading.value = true;
  try {
    const attached = await logs.attach(id, file);
    if (!attached) return;
    const url = await logs.linkFor(attached.id);
    if (!url) return;
    // The *link* goes in, not the key: a signed URL lasts five minutes, so what
    // is stored in the body has to be something the renderer can re-sign. This
    // inserts the current link for immediate feedback, and the attachment list
    // below is the durable reference.
    insert(`\n![${attached.filename}](${url})\n`);
  } finally {
    uploading.value = false;
  }
}

async function setShare(level: ShareLevel): Promise<void> {
  if (logId.value) await logs.setShare(logId.value, level);
}

async function toggleKnowledge(): Promise<void> {
  if (!logId.value || !logs.current) return;
  await logs.setKnowledge(logId.value, !logs.current.knowledge_candidate);
}
</script>

<template>
  <div class="editor">
    <div class="editor__head">
      <input
        v-model="title"
        class="input editor__title"
        placeholder="标题"
        aria-label="标题"
      />
      <div class="editor__state muted">{{ savedLabel }}</div>
    </div>

    <p v-if="logs.error" class="notice notice--error">{{ logs.error }}</p>
    <p v-if="logs.conflict" class="notice notice--conflict">{{ logs.conflict }}</p>
    <p v-if="lockedByOther" class="notice notice--conflict">
      {{ lockedByOther }} 也在编辑这篇日志。你仍然可以写 —— 每次保存都会存成一个版本，
      不会覆盖掉对方的内容。
    </p>

    <div class="toolbar">
      <!-- LOG-1's two modes. A select rather than a toggle because "plain" is a
           real choice for a paste of logs, not a lesser mode. -->
      <label class="editor__mode">
        <span class="muted">格式</span>
        <select v-model="format" class="select">
          <option value="markdown">Markdown</option>
          <option value="plain">纯文本</option>
        </select>
      </label>

      <label v-if="!isNew && logs.current" class="editor__mode">
        <span class="muted">可见范围</span>
        <select
          class="select"
          :value="logs.current.share_level"
          @change="setShare(($event.target as HTMLSelectElement).value as ShareLevel)"
        >
          <option v-for="(label, level) in SHARE_LABELS" :key="level" :value="level">
            {{ label }}
          </option>
        </select>
      </label>

      <!-- LOG-7. Hidden once there is content: see applyTemplate(). -->
      <div v-if="!body.trim()" class="editor__templates">
        <span class="muted">模板</span>
        <button
          v-for="template in LOG_TEMPLATES"
          :key="template.id"
          type="button"
          class="button"
          :title="template.hint"
          @click="applyTemplate(template)"
        >
          {{ template.name }}
        </button>
      </div>

      <div class="editor__spacer" />

      <button
        v-if="!isNew && logs.current"
        type="button"
        class="button"
        :class="{ 'button--primary': logs.current.knowledge_candidate }"
        @click="toggleKnowledge"
      >
        {{ logs.current.knowledge_candidate ? "✓ 已加入知识库" : "加入知识库" }}
      </button>
      <button v-if="!isNew" type="button" class="button" @click="showHistory = !showHistory">
        版本历史（{{ logs.versions.length }}）
      </button>
      <RouterLink
        v-if="!isNew && logId"
        class="button"
        :to="{ name: 'log', params: { id: logId } }"
      >
        浏览
      </RouterLink>
      <button type="button" class="button button--primary" @click="persist">保存</button>
    </div>

    <LogEditor
      v-model="body"
      :format="format"
      :hint="'开始写。Markdown 可用，#331 会解析成工单卡片。'"
      @image="onImage"
    />

    <p v-if="uploading" class="muted editor__uploading">图片上传中…</p>

    <VersionHistory
      v-if="showHistory && logId"
      :log-id="logId"
      @restored="body = logs.current?.body ?? body"
    />

    <section v-if="logs.attachments.length" class="editor__attachments card">
      <h2 class="editor__attachments-title">附件</h2>
      <ul>
        <li v-for="one in logs.attachments" :key="one.id">
          {{ one.filename }}
          <span class="muted">（{{ Math.round(one.size / 1024) }} KB · {{ one.scan_state }}）</span>
        </li>
      </ul>
      <!-- ``skipped`` is the honest scan verdict in S1 and the UI repeats it
           rather than translating it to "clean", which would be a lie. -->
      <p class="muted editup__note">病毒扫描：S1 未接扫描器，状态为 skipped 表示"未扫描"。</p>
    </section>
  </div>
</template>

<style scoped>
.editor {
  display: grid;
  gap: 0.9rem;
}

.editor__head {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.editor__title {
  flex: 1;
  font-size: 1.15rem;
  padding: 0.5rem 0.7rem;
}

.editor__state {
  font-size: 0.82rem;
  white-space: nowrap;
}

.editor__mode {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}

.editor__templates {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.85rem;
}

.editor__spacer {
  flex: 1;
}

.editor__uploading {
  font-size: 0.85rem;
  margin: 0;
}

.toolbar a.button {
  text-decoration: none;
}

.editor__attachments {
  padding: 0.9rem 1.1rem;
}

.editor__attachments-title {
  margin: 0 0 0.5rem;
  font-size: 0.95rem;
}

.editor__attachments ul {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.9rem;
}
</style>
