<script setup lang="ts">
/**
 * Chain detail · matches mockups/detail.png.
 * Permalink `/{tenant}/t/{n}` and If-Match / transitions unchanged.
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import type { Priority, TicketStatus, TicketType } from "@/api/types";
import {
  CATEGORY_LABELS,
  PRIORITY_LABELS,
  STATUSES_REQUIRING_REASON,
  STATUS_LABELS,
  TYPE_LABELS,
} from "@/api/types";
import MarkdownView from "@/components/MarkdownView.vue";
import {
  buildChain,
  buildTimeline,
  clockTime,
  copyText,
  formatContextValue,
  initials,
} from "@/lib/context";
import { useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";
import { useTicketStore } from "@/stores/tickets";

const route = useRoute();
const tickets = useTicketStore();
const meta = useMetaStore();
const session = useSessionStore();

const comment = ref("");
const reason = ref("");
const pendingStatus = ref<TicketStatus | null>(null);
const editingDescription = ref(false);
const draftDescription = ref("");
const busy = ref(false);
const uploading = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const draftContext = ref<Record<string, string>>({});
const contextDirty = ref(false);
const draftPr = ref("");

const canWrite = computed(() => session.can("ticket_write"));
const canComment = computed(() => session.can("comment_write"));

const EDGES: Record<TicketStatus, TicketStatus[]> = {
  todo: ["in_progress", "blocked", "wont_fix"],
  in_progress: ["in_review", "blocked", "done", "wont_fix"],
  in_review: ["in_progress", "done", "blocked"],
  done: ["todo", "in_progress"],
  blocked: ["todo", "in_progress", "wont_fix"],
  wont_fix: ["todo"],
};

const nextStates = computed<TicketStatus[]>(() =>
  tickets.current ? EDGES[tickets.current.status] : [],
);

const needsReason = computed(
  () => pendingStatus.value !== null && STATUSES_REQUIRING_REASON.includes(pendingStatus.value),
);

const chain = computed(() => (tickets.current ? buildChain(tickets.current) : []));
const timeline = computed(() => buildTimeline(tickets.comments, tickets.history));
const visibleFields = computed(() => meta.ticketFields.filter((one) => one.visible));
const assigneeName = computed(() => meta.displayName(tickets.current?.assignee_id));

function syncDraftContext(): void {
  const ctx = tickets.current?.ai_context ?? {};
  const next: Record<string, string> = {};
  for (const field of visibleFields.value) {
    next[field.key] = formatContextValue(ctx[field.key]);
  }
  draftContext.value = next;
  draftPr.value = tickets.current?.pr_url ?? "";
  contextDirty.value = false;
}

async function load(): Promise<void> {
  await tickets.open(route.params.number as string);
  draftDescription.value = tickets.current?.description ?? "";
  syncDraftContext();
}

onMounted(load);
watch(() => route.params.number, load);
watch(visibleFields, () => {
  if (tickets.current && !contextDirty.value) syncDraftContext();
});

async function move(): Promise<void> {
  const ticket = tickets.current;
  const target = pendingStatus.value;
  if (!ticket || !target) return;
  if (needsReason.value && !reason.value.trim()) return;
  busy.value = true;
  try {
    if (await tickets.transition(ticket, target, reason.value.trim() || undefined)) {
      pendingStatus.value = null;
      reason.value = "";
      await tickets.open(ticket.key);
      syncDraftContext();
    }
  } finally {
    busy.value = false;
  }
}

async function patchField(changes: Record<string, unknown>): Promise<void> {
  const ticket = tickets.current;
  if (!ticket) return;
  busy.value = true;
  try {
    await tickets.patch(ticket, changes);
    syncDraftContext();
  } finally {
    busy.value = false;
  }
}

async function saveDescription(): Promise<void> {
  await patchField({ description: draftDescription.value });
  editingDescription.value = false;
}

function parseContextValue(type: string, raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (type === "number") {
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : trimmed;
  }
  if (type === "boolean") return trimmed === "true" || trimmed === "1" || trimmed === "是";
  if (type === "string_list") {
    return trimmed
      .split(/[,，、]/)
      .map((one) => one.trim())
      .filter(Boolean);
  }
  return trimmed;
}

async function saveContext(): Promise<void> {
  const ticket = tickets.current;
  if (!ticket) return;
  const next: Record<string, unknown> = { ...(ticket.ai_context ?? {}) };
  for (const field of visibleFields.value) {
    const parsed = parseContextValue(field.type, draftContext.value[field.key] ?? "");
    if (parsed === null) delete next[field.key];
    else next[field.key] = parsed;
  }
  busy.value = true;
  try {
    if (await tickets.patch(ticket, { ai_context: next })) {
      contextDirty.value = false;
      syncDraftContext();
    }
  } finally {
    busy.value = false;
  }
}

async function savePr(): Promise<void> {
  await patchField({ pr_url: draftPr.value.trim() || null });
}

async function addComment(): Promise<void> {
  const ticket = tickets.current;
  if (!ticket || !comment.value.trim()) return;
  busy.value = true;
  try {
    if (await tickets.comment(ticket, comment.value)) comment.value = "";
  } finally {
    busy.value = false;
  }
}

async function onAttach(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  uploading.value = true;
  try {
    await tickets.attach(file);
  } finally {
    uploading.value = false;
  }
}

async function openAttachment(id: string): Promise<void> {
  const url = await tickets.linkFor(id);
  if (url) window.open(url, "_blank", "noopener");
}

function focusCompose(): void {
  const el = document.getElementById("relay-compose");
  el?.focus();
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function chainIcon(id: string): string {
  const map: Record<string, string> = {
    alert: "⚠",
    im: "💬",
    ticket: "🎫",
    trace: "📡",
    log: "📄",
    pr: "⎇",
  };
  return map[id] ?? "•";
}
</script>

<template>
  <section v-if="tickets.current" class="detail">
    <header class="detail__head">
      <div class="detail__head-main">
        <nav class="detail__crumb">
          <RouterLink :to="{ name: 'now' }">此刻</RouterLink>
          <span>/</span>
          <span class="mono">{{ tickets.current.key }}</span>
        </nav>
        <div class="detail__title-row">
          <input
            class="input detail__title"
            :value="tickets.current.title"
            :readonly="!canWrite"
            @change="patchField({ title: ($event.target as HTMLInputElement).value })"
          />
          <span class="pill pill--status" :class="`pill--${tickets.current.status}`">
            {{ STATUS_LABELS[tickets.current.status] }}
          </span>
          <span class="prio" :class="`prio--${tickets.current.priority}`">
            {{ tickets.current.priority.toUpperCase() }}
          </span>
          <span v-if="tickets.current.category" class="pill">
            {{ CATEGORY_LABELS[tickets.current.category] }}
          </span>
        </div>
      </div>
      <button
        v-if="canComment"
        type="button"
        class="button button--primary"
        @click="focusCompose"
      >
        ✎ 写接力笔记
      </button>
    </header>

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
    <p v-if="tickets.conflict" class="notice notice--conflict">{{ tickets.conflict }}</p>

    <div class="detail__grid">
      <!-- CONTEXT CHAIN -->
      <aside class="chain-col">
        <h2 class="col-title">CONTEXT CHAIN</h2>
        <ol class="chain">
          <li
            v-for="node in chain"
            :key="node.id"
            class="chain__node"
            :class="{
              'chain__node--on': node.present,
              'chain__node--active': node.active,
              'chain__node--off': !node.present,
            }"
          >
            <div class="chain__time mono">{{ clockTime(node.at) || "—" }}</div>
            <div class="chain__mark" aria-hidden="true">{{ chainIcon(node.id) }}</div>
            <div class="chain__body">
              <div class="chain__title">{{ node.title }}</div>
              <div v-if="node.detail" class="chain__detail mono">
                <a
                  v-if="node.id === 'pr' && tickets.current.pr_url"
                  :href="tickets.current.pr_url"
                  target="_blank"
                  rel="noopener"
                >
                  {{ node.detail }}
                </a>
                <template v-else>{{ node.detail }}</template>
              </div>
              <div v-else-if="!node.present" class="chain__detail muted">未接入</div>
            </div>
          </li>
        </ol>
      </aside>

      <!-- Center -->
      <main class="center">
        <section class="card panel">
          <header class="panel__head">
            <h2>问题描述</h2>
            <button
              v-if="canWrite"
              type="button"
              class="button"
              @click="
                editingDescription = !editingDescription;
                draftDescription = tickets.current.description;
              "
            >
              {{ editingDescription ? "取消" : "编辑" }}
            </button>
          </header>
          <template v-if="editingDescription">
            <textarea v-model="draftDescription" class="textarea mono-area" rows="10" />
            <button class="button button--primary" :disabled="busy" @click="saveDescription">
              保存
            </button>
          </template>
          <MarkdownView v-else :source="tickets.current.description || '（没有描述）'" />
        </section>

        <section class="card panel">
          <header class="panel__head">
            <h2>附件</h2>
            <button
              v-if="canWrite"
              type="button"
              class="button"
              :disabled="uploading"
              @click="fileInput?.click()"
            >
              {{ uploading ? "上传中…" : "添加文件" }}
            </button>
          </header>
          <input
            ref="fileInput"
            type="file"
            class="sr-only"
            accept="image/*,.pdf,.txt,.json,.zip,.gz,.tar"
            @change="onAttach"
          />
          <ul v-if="tickets.attachments.length" class="files">
            <li v-for="one in tickets.attachments" :key="one.id">
              <button type="button" class="linkish" @click="openAttachment(one.id)">
                {{ one.filename }}
              </button>
              <span class="muted">
                （{{ Math.round(one.size / 1024) }} KB）
              </span>
            </li>
          </ul>
          <p v-else class="muted note">网关同步的截图和文件会显示在这里。</p>
        </section>

        <section class="card panel">
          <h2>接力时间线</h2>
          <ol class="tl">
            <li v-for="(item, index) in timeline" :key="index" class="tl__item">
              <template v-if="item.kind === 'transition'">
                <div class="tl__avatar tl__avatar--sys" aria-hidden="true">⚙</div>
                <div class="tl__content">
                  <div class="tl__meta muted">
                    系统 · {{ clockTime(item.at) || new Date(item.at).toLocaleString() }}
                    <span v-if="item.entry.actor_type !== 'user'" class="pill">
                      {{ item.entry.actor_type }}
                    </span>
                  </div>
                  <p class="tl__text">
                    {{ item.entry.from_status ? STATUS_LABELS[item.entry.from_status] : "创建" }}
                    → {{ STATUS_LABELS[item.entry.to_status] }}
                    <span v-if="item.entry.reason" class="muted">— {{ item.entry.reason }}</span>
                  </p>
                </div>
              </template>
              <template v-else>
                <div class="tl__avatar" aria-hidden="true">
                  {{ initials(meta.displayName(item.comment.author_id)) }}
                </div>
                <div class="tl__content">
                  <div class="tl__meta">
                    <strong>{{ meta.displayName(item.comment.author_id) }}</strong>
                    <span
                      v-if="item.comment.author_id === tickets.current.assignee_id"
                      class="baton"
                    >
                      当前接力人
                    </span>
                    <span class="muted">
                      · {{ clockTime(item.at) || new Date(item.at).toLocaleString() }}
                    </span>
                  </div>
                  <MarkdownView :source="item.comment.body" />
                </div>
              </template>
            </li>
          </ol>
          <p v-if="!timeline.length" class="muted empty-tl">还没有接力记录。</p>

          <div v-if="canComment" class="compose">
            <textarea
              id="relay-compose"
              v-model="comment"
              class="textarea"
              rows="3"
              placeholder="写下这一棒你知道的…"
            />
            <div class="compose__bar">
              <span class="muted compose__hint">@某人 会发站内通知</span>
              <button class="button button--primary" :disabled="busy" @click="addComment">
                发送
              </button>
            </div>
          </div>
        </section>
      </main>

      <!-- Right -->
      <aside class="side">
        <section class="card panel">
          <header class="panel__head">
            <h2>结构化上下文</h2>
            <button
              v-if="canWrite && contextDirty"
              type="button"
              class="button button--primary"
              :disabled="busy"
              @click="saveContext"
            >
              保存
            </button>
          </header>

          <div v-for="field in visibleFields" :key="field.key" class="kv">
            <div class="kv__k muted">{{ field.label }}</div>
            <div class="kv__v">
              <template v-if="field.key === 'error_class' && draftContext[field.key]">
                <span class="err-pill">{{ draftContext[field.key] }}</span>
                <button
                  v-if="canWrite"
                  type="button"
                  class="linkish"
                  @click="
                    draftContext[field.key] = '';
                    contextDirty = true;
                  "
                >
                  清除
                </button>
              </template>
              <template v-else>
                <input
                  v-model="draftContext[field.key]"
                  class="input mono-input"
                  :readonly="!canWrite"
                  :placeholder="field.type === 'string_list' ? '多项用顿号分隔' : '未填写'"
                  @input="contextDirty = true"
                />
                <button
                  v-if="draftContext[field.key]"
                  type="button"
                  class="copy"
                  @click="copyText(draftContext[field.key])"
                >
                  ⎘
                </button>
              </template>
            </div>
          </div>
          <p v-if="!visibleFields.length" class="muted note">本租户尚未配置 AI 上下文字段。</p>
        </section>

        <section class="card panel">
          <h2>指派给</h2>
          <div class="assignee">
            <span class="avatar">{{ initials(assigneeName) }}</span>
            <select
              class="select"
              :value="tickets.current.assignee_id ?? ''"
              :disabled="!canWrite"
              @change="
                patchField({
                  assignee_id: ($event.target as HTMLSelectElement).value || null,
                })
              "
            >
              <option value="">未指派</option>
              <option v-for="one in meta.members" :key="one.user_id" :value="one.user_id">
                {{ one.display_name }}
              </option>
            </select>
          </div>
        </section>

        <section class="card panel">
          <h2>状态流转</h2>
          <div class="moves">
            <button
              v-for="status in nextStates"
              :key="status"
              type="button"
              class="button"
              :class="{
                'button--primary': pendingStatus === status || status === 'done',
              }"
              :disabled="!canWrite || busy"
              @click="pendingStatus = status"
            >
              {{ STATUS_LABELS[status] }}
            </button>
          </div>
          <template v-if="pendingStatus">
            <label v-if="needsReason" class="field">
              <span class="muted">原因（必填）</span>
              <textarea v-model="reason" class="textarea" rows="2" />
            </label>
            <button
              class="button button--primary"
              :disabled="busy || (needsReason && !reason.trim())"
              @click="move"
            >
              移到「{{ STATUS_LABELS[pendingStatus] }}」
            </button>
          </template>
        </section>

        <section class="card panel">
          <h2>关联 PR</h2>
          <template v-if="tickets.current.pr_url && !canWrite">
            <a :href="tickets.current.pr_url" target="_blank" rel="noopener">
              {{ tickets.current.pr_url }}
            </a>
          </template>
          <template v-else>
            <p v-if="!draftPr" class="muted note">暂无关联 PR</p>
            <input
              v-model="draftPr"
              class="input mono-input"
              :readonly="!canWrite"
              placeholder="https://…"
              @change="savePr"
            />
            <button
              v-if="canWrite"
              type="button"
              class="button"
              style="margin-top: 0.4rem"
              :disabled="busy"
              @click="savePr"
            >
              关联 PR
            </button>
          </template>
        </section>

        <section class="card panel">
          <h2>字段</h2>
          <label class="field">
            <span class="muted">类型</span>
            <select
              class="select"
              :value="tickets.current.type"
              :disabled="!canWrite"
              @change="patchField({ type: ($event.target as HTMLSelectElement).value as TicketType })"
            >
              <option v-for="(label, value) in TYPE_LABELS" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </label>
          <label class="field">
            <span class="muted">优先级</span>
            <select
              class="select"
              :value="tickets.current.priority"
              :disabled="!canWrite"
              @change="
                patchField({ priority: ($event.target as HTMLSelectElement).value as Priority })
              "
            >
              <option v-for="(label, value) in PRIORITY_LABELS" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </label>
          <label class="field">
            <span class="muted">迭代</span>
            <select
              class="select"
              :value="tickets.current.iteration_id ?? ''"
              :disabled="!canWrite"
              @change="
                patchField({
                  iteration_id: ($event.target as HTMLSelectElement).value || null,
                })
              "
            >
              <option value="">无</option>
              <option v-for="one in meta.iterations" :key="one.id" :value="one.id">
                {{ one.name }}
              </option>
            </select>
          </label>
          <p v-if="tickets.current.source" class="note muted">
            来源标记：{{ tickets.current.source }}
            <template v-if="tickets.current.external_ref">
              · {{ tickets.current.external_ref.system }}
              /
              <a
                v-if="tickets.current.external_ref.external_url"
                :href="tickets.current.external_ref.external_url"
                target="_blank"
                rel="noopener"
              >
                {{ tickets.current.external_ref.external_id }}
              </a>
              <span v-else>{{ tickets.current.external_ref.external_id }}</span>
            </template>
          </p>
        </section>

        <div class="more">
          <RouterLink :to="{ name: 'log-new' }">从这条调查写日志</RouterLink>
          <span class="muted">rev {{ tickets.current.rev }}</span>
        </div>
      </aside>
    </div>
  </section>

  <p v-else-if="!tickets.loading" class="empty">找不到这条调查，或者它不存在。</p>
</template>

<style scoped>
.detail {
  min-height: calc(100vh - 53px);
  display: flex;
  flex-direction: column;
  background: var(--relay-bg);
}

.detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.85rem 1.25rem;
  background: var(--relay-surface);
  border-bottom: 1px solid var(--relay-border);
}

.detail__crumb {
  display: flex;
  gap: 0.35rem;
  align-items: center;
  font-size: 0.8rem;
  color: var(--relay-text-muted);
  margin-bottom: 0.35rem;
}

.detail__crumb a {
  text-decoration: none;
}

.detail__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.55rem;
}

.detail__title {
  font-size: 1.15rem;
  font-weight: 600;
  border: 0;
  background: transparent;
  padding: 0.15rem 0;
  min-width: 12rem;
  flex: 1;
}

.prio {
  font-family: var(--relay-mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  background: var(--relay-surface-alt);
}

.prio--p0 {
  background: #fee2e2;
  color: var(--relay-danger);
}

.prio--p1 {
  background: #ffedd5;
  color: var(--relay-warning);
}

.detail__grid {
  flex: 1;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr) 280px;
  min-height: 0;
}

.chain-col {
  padding: 1rem 0.85rem;
  background: var(--relay-surface);
  border-right: 1px solid var(--relay-border);
  overflow: auto;
}

.center {
  padding: 1rem;
  display: grid;
  gap: 0.85rem;
  align-content: start;
  overflow: auto;
}

.side {
  padding: 1rem 0.85rem;
  display: grid;
  gap: 0.75rem;
  align-content: start;
  border-left: 1px solid var(--relay-border);
  background: var(--relay-surface);
  overflow: auto;
}

.col-title {
  margin: 0 0 0.85rem;
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  color: var(--relay-text-muted);
}

.panel {
  padding: 0.85rem 0.95rem;
}

.panel h2,
.panel__head h2 {
  margin: 0 0 0.55rem;
  font-size: 0.92rem;
}

.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
}

.panel__head h2 {
  margin: 0;
}

.chain {
  list-style: none;
  margin: 0;
  padding: 0;
}

.chain__node {
  display: grid;
  grid-template-columns: 36px 22px minmax(0, 1fr);
  gap: 0.35rem;
  padding: 0.55rem 0.4rem;
  border-radius: 8px;
  position: relative;
  margin-bottom: 0.2rem;
}

.chain__node--active {
  background: var(--relay-accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--relay-accent) 35%, transparent);
}

.chain__node--off {
  opacity: 0.55;
}

.chain__time {
  font-size: 0.68rem;
  color: var(--relay-text-muted);
  padding-top: 0.15rem;
}

.chain__mark {
  font-size: 0.85rem;
  line-height: 1.2;
}

.chain__title {
  font-size: 0.8rem;
  font-weight: 600;
}

.chain__detail {
  font-size: 0.7rem;
  margin-top: 0.15rem;
  word-break: break-all;
}

.tl {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.85rem;
}

.tl__item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 0.55rem;
}

.tl__avatar {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #334155;
  color: #fff;
  font-size: 0.65rem;
  font-weight: 600;
  display: grid;
  place-items: center;
}

.tl__avatar--sys {
  background: var(--relay-surface-alt);
  color: var(--relay-text-muted);
  border: 1px solid var(--relay-border);
}

.tl__meta {
  font-size: 0.8rem;
  margin-bottom: 0.2rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
}

.tl__text {
  margin: 0;
  font-size: 0.9rem;
}

.baton {
  font-size: 0.68rem;
  padding: 0.05rem 0.35rem;
  border-radius: 999px;
  background: var(--relay-accent-soft);
  color: var(--relay-accent);
}

.empty-tl {
  font-size: 0.85rem;
}

.compose {
  margin-top: 0.85rem;
  display: grid;
  gap: 0.45rem;
}

.compose .textarea {
  width: 100%;
}

.compose__bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.compose__hint {
  font-size: 0.75rem;
}

.kv {
  margin-bottom: 0.55rem;
}

.kv__k {
  font-size: 0.72rem;
  margin-bottom: 0.2rem;
}

.kv__v {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.mono-input {
  flex: 1;
  font-family: var(--relay-mono);
  font-size: 0.78rem;
}

.mono-area {
  width: 100%;
  font-family: var(--relay-mono);
  font-size: 0.85rem;
  margin-bottom: 0.45rem;
}

.copy,
.linkish {
  border: 0;
  background: transparent;
  color: var(--relay-text-muted);
  cursor: pointer;
  font-size: 0.8rem;
}

.err-pill {
  display: inline-block;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  background: #fee2e2;
  color: var(--relay-danger);
  font-family: var(--relay-mono);
  font-size: 0.75rem;
}

.assignee {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.assignee .select {
  flex: 1;
}

.avatar {
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 999px;
  background: #334155;
  color: #fff;
  font-size: 0.65rem;
  font-weight: 600;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.moves {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.45rem;
}

.field {
  display: grid;
  gap: 0.25rem;
  margin: 0 0 0.55rem;
  font-size: 0.85rem;
}

.note {
  font-size: 0.8rem;
  margin: 0 0 0.4rem;
}

.files {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
  font-size: 0.85rem;
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

.more {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.8rem;
  padding: 0 0.2rem;
}

.mono {
  font-family: var(--relay-mono);
}

@media (max-width: 1100px) {
  .detail__grid {
    grid-template-columns: 1fr;
  }

  .chain-col,
  .side {
    border: 0;
  }
}
</style>
