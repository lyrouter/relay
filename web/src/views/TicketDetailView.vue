<script setup lang="ts">
/**
 * TKT-9 · one ticket: fields, transitions, comments, history.
 *
 * **The URL is the contract here** (S-12). This view is mounted at
 * `/{tenant_slug}/t/{number}` and that shape is frozen on release, because it is
 * what people paste into Jira, chat and the gateway's feedback records. The
 * tenant-less `/t/331` redirects into it rather than rendering — so a link that
 * gets shared is always the canonical one.
 *
 * **Every field edit sends `If-Match`.** Two people triaging the same ticket is the
 * ordinary case, and the loser of that race must be *told*, not silently
 * overwritten. That is what the conflict banner is: the store re-read the ticket,
 * and the user needs to look before re-submitting.
 *
 * **Transitions are their own control, not a status dropdown.** A dropdown would
 * imply status is a field like any other; it is a state machine with edges and two
 * states that require a reason (TKT-3). Only the legal next states are offered, and
 * an illegal one cannot be attempted from the UI at all.
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import type { Priority, TicketStatus, TicketType } from "@/api/types";
import {
  PRIORITY_LABELS,
  STATUSES_REQUIRING_REASON,
  STATUS_LABELS,
  TYPE_LABELS,
} from "@/api/types";
import MarkdownView from "@/components/MarkdownView.vue";
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

const canWrite = computed(() => session.can("ticket_write"));
const canComment = computed(() => session.can("comment_write"));

/** See TicketCard: `submitter` is open JSON, narrowed here rather than inline. */
const submitterName = computed(() => {
  const submitter = tickets.current?.submitter as { name?: string } | null | undefined;
  return submitter?.name ?? "外部用户";
});

/**
 * TKT-3's edges, as the UI knows them.
 *
 * A copy of the server's machine — and a *deliberate* one: the server is the
 * authority and refuses anything illegal, so the worst this table can do is offer
 * a move that then fails. What it buys is a UI that does not offer nonsense. It is
 * small enough to keep in sync by reading §7.2, and the two states S-23 added
 * (`done → todo`, `in_review → in_progress`) are in it.
 */
const EDGES: Record<TicketStatus, TicketStatus[]> = {
  todo: ["in_progress", "blocked", "wont_fix"],
  in_progress: ["in_review", "blocked", "done", "wont_fix"],
  in_review: ["in_progress", "done", "blocked"],
  // S-23: no terminal state in S1. Both of these can come back.
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

async function load(): Promise<void> {
  const number = route.params.number as string;
  await tickets.open(number);
  draftDescription.value = tickets.current?.description ?? "";
}

onMounted(load);
watch(() => route.params.number, load);

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
  } finally {
    busy.value = false;
  }
}

async function saveDescription(): Promise<void> {
  await patchField({ description: draftDescription.value });
  editingDescription.value = false;
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
</script>

<template>
  <section v-if="tickets.current" class="detail">
    <header class="detail__head">
      <span class="detail__key">{{ tickets.current.key }}</span>
      <span class="pill" :class="`pill--${tickets.current.status}`">
        {{ STATUS_LABELS[tickets.current.status] }}
      </span>
      <span class="muted detail__rev">rev {{ tickets.current.rev }}</span>
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
          <!-- Rendered through the same renderer as a log, so `#331` and Mermaid
               work in a ticket description too (LOG-2 / LOG-3). -->
          <MarkdownView v-else :source="tickets.current.description || '（没有描述）'" />
        </section>

        <section class="card detail__panel">
          <h2>评论（{{ tickets.comments.length }}）</h2>
          <ol class="detail__comments">
            <li v-for="one in tickets.comments" :key="one.id" class="detail__comment">
              <div class="muted detail__comment-head">
                {{ meta.displayName(one.author_id) }} ·
                {{ one.created_at ? new Date(one.created_at).toLocaleString() : "" }}
                <span v-if="one.mentioned.length" class="pill">
                  已通知 {{ one.mentioned.length }} 人
                </span>
              </div>
              <MarkdownView :source="one.body" />
            </li>
          </ol>

          <div v-if="canComment" class="detail__compose">
            <textarea
              v-model="comment"
              class="textarea"
              rows="3"
              placeholder="留言。@某人 会给对方发站内通知。"
            />
            <button class="button button--primary" :disabled="busy" @click="addComment">
              发表
            </button>
          </div>
        </section>

        <section class="card detail__panel">
          <h2>历史</h2>
          <ol class="detail__history">
            <li v-for="(row, index) in tickets.history" :key="index">
              <span class="muted">
                {{ row.created_at ? new Date(row.created_at).toLocaleString() : "" }}
              </span>
              <span>
                {{ row.from_status ? STATUS_LABELS[row.from_status] : "创建" }} →
                {{ STATUS_LABELS[row.to_status] }}
              </span>
              <span class="muted">{{ meta.displayName(row.actor_id) }}</span>
              <!-- §8.4: a person in the UI, or an integration over the API. Shown
                   because "who changed this" is the first question in an
                   investigation, and a machine answer is a different answer. -->
              <span v-if="row.actor_type !== 'user'" class="pill">
                {{ row.actor_type }} · {{ row.origin }}
              </span>
              <span v-if="row.reason" class="muted">— {{ row.reason }}</span>
            </li>
          </ol>
        </section>
      </main>

      <aside class="detail__side">
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
            已完成的工单可以重新打开（S-23），编号与历史都保留。
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

          <p class="detail__field">
            <span class="muted">报告人</span>
            <span>{{ meta.displayName(tickets.current.reporter_id) }}</span>
          </p>

          <!-- API-6 / §8.8. Shown separately from the reporter, and labelled as
               what it is: the real person behind a machine-filed ticket. -->
          <p v-if="tickets.current.submitter" class="detail__field">
            <span class="muted">提交人</span>
            <span>
              {{ submitterName }} 通过 {{ tickets.current.source ?? "外部系统" }} 提交
            </span>
          </p>
        </section>

        <section
          v-if="Object.keys(tickets.current.ai_context ?? {}).length"
          class="card detail__panel"
        >
          <h2>AI 上下文</h2>
          <dl class="detail__context">
            <template v-for="(value, key) in tickets.current.ai_context" :key="key">
              <dt class="muted">
                {{ meta.ticketFields.find((one) => one.key === key)?.label ?? key }}
              </dt>
              <dd>{{ Array.isArray(value) ? value.join("、") : value }}</dd>
            </template>
          </dl>
        </section>
      </aside>
    </div>
  </section>

  <p v-else-if="!tickets.loading" class="empty">找不到这张工单，或者它不存在。</p>
</template>

<style scoped>
.detail {
  display: grid;
  gap: 0.8rem;
}

.detail__head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.detail__key {
  font-family: var(--relay-mono);
  font-size: 1.05rem;
}

.detail__rev {
  font-size: 0.8rem;
}

.detail__title {
  font-size: 1.2rem;
  padding: 0.5rem 0.7rem;
}

.detail__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 1rem;
  align-items: start;
}

@media (max-width: 1000px) {
  .detail__body {
    grid-template-columns: 1fr;
  }
}

.detail__main,
.detail__side {
  display: grid;
  gap: 0.8rem;
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

.detail__comments,
.detail__history {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.7rem;
  font-size: 0.9rem;
}

.detail__history li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  font-size: 0.85rem;
}

.detail__comment {
  border-top: 1px solid var(--relay-border);
  padding-top: 0.6rem;
}

.detail__comment:first-child {
  border-top: none;
  padding-top: 0;
}

.detail__comment-head {
  font-size: 0.8rem;
  margin-bottom: 0.2rem;
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

.detail__context {
  margin: 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 0.6rem;
  font-size: 0.85rem;
}

.detail__context dd {
  margin: 0;
}
</style>
