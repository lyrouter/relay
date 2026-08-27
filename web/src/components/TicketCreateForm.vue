<script setup lang="ts">
/**
 * Create a ticket with the fields people actually fill on day one: description,
 * priority, type, assignee, labels, and pending attachments.
 *
 * Attachments cannot exist before the ticket does (the upload is keyed by
 * owner_id), so files stay local until create succeeds, then go up in the same
 * click. A half-created ticket with no files is still a ticket — the caller
 * navigates to it either way.
 */
import { ref } from "vue";

import type { Priority, SupportCategory, Ticket, TicketType } from "@/api/types";
import { CATEGORY_LABELS, PRIORITY_LABELS, TYPE_LABELS } from "@/api/types";
import { useMetaStore } from "@/stores/meta";
import { useTicketStore } from "@/stores/tickets";

const emit = defineEmits<{
  created: [ticket: Ticket];
  cancel: [];
}>();

const tickets = useTicketStore();
const meta = useMetaStore();

const title = ref("");
const description = ref("");
const type = ref<TicketType>("task");
const priority = ref<Priority>("p2");
const category = ref<SupportCategory | "">("");
const assigneeId = ref("");
const iterationId = ref("");
const labelIds = ref<string[]>([]);
const pendingFiles = ref<File[]>([]);
const fileInput = ref<HTMLInputElement | null>(null);
const busy = ref(false);

const priorities = Object.keys(PRIORITY_LABELS) as Priority[];

function toggleLabel(id: string): void {
  const index = labelIds.value.indexOf(id);
  if (index === -1) labelIds.value = [...labelIds.value, id];
  else labelIds.value = labelIds.value.filter((one) => one !== id);
}

function onPickFiles(event: Event): void {
  const input = event.target as HTMLInputElement;
  const picked = input.files ? Array.from(input.files) : [];
  input.value = "";
  if (!picked.length) return;
  pendingFiles.value = [...pendingFiles.value, ...picked];
}

function removeFile(index: number): void {
  pendingFiles.value = pendingFiles.value.filter((_, i) => i !== index);
}

function fileSize(file: File): string {
  if (file.size < 1024) return `${file.size} B`;
  return `${Math.round(file.size / 1024)} KB`;
}

async function submit(): Promise<void> {
  const trimmed = title.value.trim();
  if (!trimmed || busy.value) return;
  busy.value = true;
  try {
    const created = await tickets.create({
      type: type.value,
      title: trimmed,
      description: description.value,
      priority: priority.value,
      assignee_id: assigneeId.value || null,
      iteration_id: iterationId.value || null,
      label_ids: labelIds.value,
      category: category.value || null,
    });
    if (!created) return;
    for (const file of pendingFiles.value) {
      await tickets.attachTo(created.id, file);
    }
    emit("created", created);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <form class="create card" @submit.prevent="submit">
    <header class="create__head">
      <h2>新建调查</h2>
      <p class="muted">标题必填。描述、级别、附件会一并写入，创建后进入详情。</p>
    </header>

    <label class="field">
      <span>标题</span>
      <input v-model="title" class="input" placeholder="一句话说清问题" required autofocus />
    </label>

    <fieldset class="create__details">
      <legend>细节</legend>

      <label class="field">
        <span>问题描述</span>
        <textarea
          v-model="description"
          class="textarea"
          rows="6"
          placeholder="现象、影响范围、已知线索…"
        />
      </label>

      <div class="field">
        <span>级别</span>
        <div class="create__pills" role="radiogroup" aria-label="级别">
          <button
            v-for="value in priorities"
            :key="value"
            type="button"
            class="pill create__choice"
            :class="[`pill--${value}`, { 'create__choice--on': priority === value }]"
            :aria-pressed="priority === value"
            @click="priority = value"
          >
            {{ PRIORITY_LABELS[value] }}
          </button>
        </div>
      </div>

      <div class="create__row">
        <label class="field">
          <span>类型</span>
          <select v-model="type" class="select">
            <option v-for="(label, value) in TYPE_LABELS" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>分类</span>
          <select v-model="category" class="select">
            <option value="">无</option>
            <option v-for="(label, value) in CATEGORY_LABELS" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
      </div>

      <div class="create__row">
        <label class="field">
          <span>负责人</span>
          <select v-model="assigneeId" class="select">
            <option value="">未指派</option>
            <option v-for="one in meta.members" :key="one.user_id" :value="one.user_id">
              {{ one.display_name }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>迭代</span>
          <select v-model="iterationId" class="select">
            <option value="">无</option>
            <option v-for="one in meta.iterations" :key="one.id" :value="one.id">
              {{ one.name }}{{ one.closed ? "（已关闭）" : "" }}
            </option>
          </select>
        </label>
      </div>

      <div v-if="meta.labels.length" class="field">
        <span>标签</span>
        <div class="create__pills">
          <button
            v-for="one in meta.labels"
            :key="one.id"
            type="button"
            class="pill create__choice"
            :class="{ 'create__choice--on': labelIds.includes(one.id) }"
            @click="toggleLabel(one.id)"
          >
            {{ one.name }}
          </button>
        </div>
      </div>

      <div class="field">
        <span>附件</span>
        <div class="create__files">
          <button type="button" class="button" @click="fileInput?.click()">选择文件</button>
          <input
            ref="fileInput"
            type="file"
            class="sr-only"
            multiple
            accept="image/*,.pdf,.txt,.json,.zip,.gz,.tar"
            @change="onPickFiles"
          />
          <ul v-if="pendingFiles.length" class="create__file-list">
            <li v-for="(file, index) in pendingFiles" :key="`${file.name}-${index}`">
              <span>{{ file.name }}</span>
              <span class="muted">{{ fileSize(file) }}</span>
              <button type="button" class="linkish" @click="removeFile(index)">移除</button>
            </li>
          </ul>
          <p v-else class="muted create__hint">截图、日志、压缩包都可以。创建时一并上传。</p>
        </div>
      </div>
    </fieldset>

    <footer class="create__actions">
      <button class="button button--primary" type="submit" :disabled="busy || !title.trim()">
        {{ busy ? "创建中…" : "创建" }}
      </button>
      <button type="button" class="button" :disabled="busy" @click="emit('cancel')">取消</button>
    </footer>
    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
  </form>
</template>

<style scoped>
.create {
  display: grid;
  gap: 0.75rem;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
}

.create__head h2 {
  margin: 0 0 0.2rem;
  font-size: 1rem;
}

.create__head p {
  margin: 0;
  font-size: 0.82rem;
}

.create__details {
  display: grid;
  gap: 0.75rem;
  margin: 0;
  padding: 0.75rem 0.85rem 0.85rem;
  border: 1px solid var(--relay-border);
  border-radius: 8px;
  background: var(--relay-surface-alt);
}

.create__details legend {
  padding: 0 0.35rem;
  font-size: 0.82rem;
  font-weight: 600;
}

.create__row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.6rem 0.8rem;
}

.field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.field > span {
  color: var(--relay-text-muted);
}

.field .input,
.field .select,
.field .textarea {
  width: 100%;
  box-sizing: border-box;
}

.create__pills {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.create__choice {
  cursor: pointer;
  opacity: 0.55;
}

.create__choice--on {
  opacity: 1;
  font-weight: 600;
}

.create__files {
  display: grid;
  gap: 0.4rem;
  justify-items: start;
}

.create__file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.25rem;
  width: 100%;
  font-size: 0.85rem;
}

.create__file-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.create__hint {
  margin: 0;
  font-size: 0.8rem;
}

.create__actions {
  display: flex;
  gap: 0.5rem;
}

.linkish {
  border: 0;
  background: none;
  color: var(--relay-accent);
  cursor: pointer;
  padding: 0;
  font: inherit;
}

.sr-only {
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
</style>
