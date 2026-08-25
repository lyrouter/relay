<script setup lang="ts">
/**
 * TKT-9 · one ticket as a **context chain** (relay-ui skill).
 *
 * Layout: left chain · center handoff timeline · right AI context + transitions.
 * Permalinks stay `/{tenant}/t/{n}` (S-12). Mutations still carry `If-Match` /
 * legal edges only — chrome changed, contracts did not.
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import type { Priority, TicketStatus, TicketType } from "@/api/types";
import {
  PRIORITY_LABELS,
  STATUSES_REQUIRING_REASON,
  STATUS_LABELS,
  TYPE_LABELS,
} from "@/api/types";
import MarkdownView from "@/components/MarkdownView.vue";
import { buildChain, buildTimeline, formatContextValue } from "@/lib/context";
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
const draftContext = ref<Record<string, string>>({});
const contextDirty = ref(false);

const canWrite = computed(() => session.can("ticket_write"));
const canComment = computed(() => session.can("comment_write"));

const submitterName = computed(() => {
  const submitter = tickets.current?.submitter as { name?: string } | null | undefined;
  return submitter?.name ?? "外部用户";
});

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

function syncDraftContext(): void {
  const ctx = tickets.current?.ai_context ?? {};
  const next: Record<string, string> = {};
  for (const field of visibleFields.value) {
    next[field.key] = formatContextValue(ctx[field.key]);
  }
  draftContext.value = next;
  contextDirty.value = false;
}

async function load(): Promise<void> {
  const number = route.params.number as string;
  await tickets.open(number);
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

function focusCompose(): void {
  const el = document.getElementById("relay-compose");
  el?.focus();
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
}
</script>

<template>
  <section v-if="tickets.current" class="detail">
    <header class="detail__head">
      <nav class="detail__crumb muted">
        <RouterLink :to="{ name: 'now' }">此刻</RouterLink>
        <span>/</span>
        <span class="detail__key">{{ tickets.current.key }}</span>
      </nav>
      <span class="pill" :class="`pill--${tickets.current.status}`">
        {{ STATUS_LABELS[tickets.current.status] }}
      </span>
      <span class="pill" :class="`pill--${tickets.current.priority}`">
        {{ PRIORITY_LABELS[tickets.current.priority] }}
      </span>
      <span class="muted detail__rev">rev {{ tickets.current.rev }}</span>
      <button
        v-if="canComment"
        type="button"
        class="button button--primary detail__cta"
        @click="focusCompose"
      >
        写接力笔记
      </button>
    </header>

    <p v-if="tickets.error" class="notice notice--error">{{ tickets.error }}</p>
    <p v-if="tickets.conflict" class="notice notice--conflict">{{ tickets.conflict }}</p>

    <input
      class="input detail__title"
      :value="tickets.current.title"
      :readonly="!canWrite"
      @change="patchField({ title: ($event.target as HTMLInputElement).value })"
    />

    <div class="detail__body">
      <!-- Left: context chain -->
      <aside class="detail__chain" aria-label="上下文链">
        <h2 class="detail__side-title">上下文链</h2>
        <ol class="chain">
          <li
            v-for="node in chain"
            :key="node.id"
            class="chain__node"
            :class="{ 'chain__node--on': node.present, 'chain__node--off': !node.present }"
          >
            <span class="chain__dot" />
            <div class="chain__body">
              <div class="chain__label">{{ node.label }}</div>
              <div v-if="node.detail" class="chain__detail">
                <a
                  v-if="node.id === 'pr' && tickets.current.pr_url"
                  :href="tickets.current.pr_url"
                  target="_blank"
                  rel="noopener"
                >
                  {{ node.detail }}
                </a>
                <span v-else>{{ node.detail }}</span>
              </div>
              <div v-else-if="!node.present" class="chain__detail muted">未接入</div>
              <div v-if="node.at" class="chain__at muted">
                {{ new Date(node.at).toLocaleString() }}
              </div>
            </div>
          </li>
        </ol>
        <p class="muted chain__hint">
          空节点会保留：S1 往往还没有告警/日志自动挂链。
        </p>
      </aside>

      <!-- Center: description + handoff timeline -->
      <main class="detail__main">
        <section class="card detail__panel">
          <header class="detail__panel-head">
            <h2>描述</h2>
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
            <textarea v-model="draftDescription" class="textarea detail__textarea" rows="10" />
            <button class="button button--primary" :disabled="busy" @click="saveDescription">
              保存
            </button>
          </template>
          <MarkdownView v-else :source="tickets.current.description || '（没有描述）'" />
        </section>

        <section class="card detail__panel">
          <h2>接力时间线（{{ timeline.length }}）</h2>
          <ol class="detail__timeline">
            <li v-for="(item, index) in timeline" :key="index" class="detail__tl-item">
              <template v-if="item.kind === 'transition'">
                <div class="muted detail__tl-head">
                  {{ new Date(item.at).toLocaleString() }}
                  ·
                  {{ meta.displayName(item.entry.actor_id) }}
                  <span v-if="item.entry.actor_type !== 'user'" class="pill">
                    {{ item.entry.actor_type }} · {{ item.entry.origin }}
                  </span>
                </div>
                <p class="detail__tl-system">
                  {{ item.entry.from_status ? STATUS_LABELS[item.entry.from_status] : "创建" }}
                  → {{ STATUS_LABELS[item.entry.to_status] }}
                  <span v-if="item.entry.reason" class="muted">— {{ item.entry.reason }}</span>
                </p>
              </template>
              <template v-else>
                <div class="muted detail__tl-head">
                  {{ meta.displayName(item.comment.author_id) }} ·
                  {{ new Date(item.at).toLocaleString() }}
                  <span v-if="item.comment.mentioned.length" class="pill">
                    已通知 {{ item.comment.mentioned.length }} 人
                  </span>
                </div>
                <MarkdownView :source="item.comment.body" />
              </template>
            </li>
          </ol>
          <p v-if="!timeline.length" class="muted detail__tl-empty">
            还没有接力记录。写下这一棒你知道的。
          </p>

          <div v-if="canComment" class="detail__compose">
            <textarea
              id="relay-compose"
              v-model="comment"
              class="textarea"
              rows="3"
              placeholder="写下这一棒你知道的… @某人 会发站内通知。"
            />
            <button class="button button--primary" :disabled="busy" @click="addComment">
              留下这一棒
            </button>
          </div>
        </section>
      </main>

      <!-- Right: AI context + transitions + fields -->
      <aside class="detail__side">
        <section class="card detail__panel">
          <header class="detail__panel-head">
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
          <label
            v-for="field in visibleFields"
            :key="field.key"
            class="detail__field"
          >
            <span class="muted">{{ field.label }}</span>
            <input
              v-model="draftContext[field.key]"
              class="input detail__mono"
              :readonly="!canWrite"
              :placeholder="field.type === 'string_list' ? '多项用顿号分隔' : '未填写'"
              @input="contextDirty = true"
            />
          </label>
          <p v-if="!visibleFields.length" class="muted detail__note">
            本租户尚未配置 AI 上下文字段。
          </p>
        </section>

        <section class="card detail__panel">
          <h2>流转</h2>
          <div class="detail__moves">
            <button
              v-for="status in nextStates"
              :key="status"
              type="button"
              class="button"
              :class="{ 'button--primary': pendingStatus === status }"
              :disabled="!canWrite || busy"
              @click="pendingStatus = status"
            >
              {{ STATUS_LABELS[status] }}
            </button>
          </div>
          <template v-if="pendingStatus">
            <label v-if="needsReason" class="detail__field">
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
          <p v-if="tickets.current.status === 'done'" class="muted detail__note">
            已完成的调查可以重新打开（S-23），编号与历史都保留。
          </p>
        </section>

        <section class="card detail__panel">
          <h2>字段</h2>

          <label class="detail__field">
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

          <label class="detail__field">
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

          <label class="detail__field">
            <span class="muted">负责人</span>
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
          </label>

          <label class="detail__field">
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

          <label class="detail__field">
            <span class="muted">PR</span>
            <input
              class="input detail__mono"
              :value="tickets.current.pr_url ?? ''"
              :readonly="!canWrite"
              placeholder="https://…"
              @change="
                patchField({
                  pr_url: ($event.target as HTMLInputElement).value.trim() || null,
                })
              "
            />
          </label>

          <p class="detail__field">
            <span class="muted">报告人</span>
            <span>{{ meta.displayName(tickets.current.reporter_id) }}</span>
          </p>

          <p v-if="tickets.current.submitter" class="detail__field">
            <span class="muted">提交人</span>
            <span>
              {{ submitterName }} 通过 {{ tickets.current.source ?? "外部系统" }} 提交
            </span>
          </p>
        </section>

        <p class="muted detail__knowledge">
          复盘沉淀到知识库：
          <RouterLink :to="{ name: 'log-new' }">从这条调查写日志</RouterLink>
        </p>
      </aside>
    </div>
  </section>

  <p v-else-if="!tickets.loading" class="empty">
    找不到这条调查，或者它不存在。
  </p>
</template>

<style scoped>
.detail {
  display: grid;
  gap: 0.8rem;
}

.detail__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}

.detail__crumb {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.85rem;
}

.detail__crumb a {
  text-decoration: none;
}

.detail__key {
  font-family: var(--relay-mono);
  font-size: 1.05rem;
  color: var(--relay-text);
}

.detail__rev {
  font-size: 0.8rem;
}

.detail__cta {
  margin-left: auto;
}

.detail__title {
  font-size: 1.2rem;
  padding: 0.5rem 0.7rem;
}

.detail__body {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr) 280px;
  gap: 1rem;
  align-items: start;
}

@media (max-width: 1100px) {
  .detail__body {
    grid-template-columns: minmax(0, 1fr) 280px;
  }

  .detail__chain {
    grid-column: 1 / -1;
  }

  .chain {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .chain__node {
    flex: 1 1 140px;
    padding-left: 0.9rem;
  }

  .chain__node::before {
    display: none;
  }
}

@media (max-width: 800px) {
  .detail__body {
    grid-template-columns: 1fr;
  }
}

.detail__main,
.detail__side,
.detail__chain {
  display: grid;
  gap: 0.8rem;
  align-content: start;
}

.detail__side-title {
  margin: 0;
  font-size: 0.85rem;
  color: var(--relay-text-muted);
}

.detail__panel {
  padding: 0.9rem 1.05rem;
}

.detail__panel h2 {
  margin: 0 0 0.6rem;
  font-size: 0.95rem;
}

.detail__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}

.detail__panel-head h2 {
  margin: 0;
}

.detail__textarea {
  width: 100%;
  font-family: var(--relay-mono);
  font-size: 0.88rem;
  margin-bottom: 0.5rem;
}

.detail__mono {
  font-family: var(--relay-mono);
  font-size: 0.82rem;
}

.chain {
  list-style: none;
  margin: 0;
  padding: 0;
  position: relative;
}

.chain__node {
  position: relative;
  padding: 0 0 1rem 1.1rem;
}

.chain__node:last-child {
  padding-bottom: 0;
}

.chain__node::before {
  content: "";
  position: absolute;
  left: 5px;
  top: 12px;
  bottom: -4px;
  width: 2px;
  background: var(--relay-border);
}

.chain__node:last-child::before {
  display: none;
}

.chain__dot {
  position: absolute;
  left: 0;
  top: 4px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--relay-border);
  background: var(--relay-surface);
}

.chain__node--on .chain__dot {
  border-color: var(--relay-accent);
  background: var(--relay-accent);
  box-shadow: 0 0 0 3px var(--relay-accent-soft);
}

.chain__node--off {
  opacity: 0.7;
}

.chain__label {
  font-size: 0.85rem;
  font-weight: 600;
}

.chain__detail {
  font-family: var(--relay-mono);
  font-size: 0.75rem;
  margin-top: 0.15rem;
  word-break: break-all;
}

.chain__at {
  font-size: 0.72rem;
  margin-top: 0.15rem;
}

.chain__hint {
  font-size: 0.75rem;
  margin: 0;
}

.detail__timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.85rem;
}

.detail__tl-item {
  border-top: 1px solid var(--relay-border);
  padding-top: 0.65rem;
}

.detail__tl-item:first-child {
  border-top: none;
  padding-top: 0;
}

.detail__tl-head {
  font-size: 0.8rem;
  margin-bottom: 0.25rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
}

.detail__tl-system {
  margin: 0;
  font-size: 0.9rem;
}

.detail__tl-empty {
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
}

.detail__compose {
  margin-top: 0.8rem;
  display: grid;
  gap: 0.4rem;
}

.detail__compose .textarea {
  width: 100%;
}

.detail__moves {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
}

.detail__field {
  display: grid;
  gap: 0.25rem;
  margin: 0 0 0.6rem;
  font-size: 0.85rem;
}

.detail__note {
  font-size: 0.8rem;
  margin: 0.4rem 0 0;
}

.detail__knowledge {
  font-size: 0.8rem;
  margin: 0;
}
</style>
