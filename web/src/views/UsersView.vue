<script setup lang="ts">
/**
 * R-2 · the account review screen.
 *
 * This is the UI for WEB-4's admin operations: list (with emails and leavers),
 * invite, approve a pending signup, change role, deactivate / reactivate.
 * Capability comes from `/web/session` — a `role === "admin"` check here would
 * be a second copy of the permission matrix.
 */
import { onMounted, ref } from "vue";

import { ROLE_LABELS, ROLES, USER_STATUS_LABELS } from "@/api/types";
import type { Role, UserStatus } from "@/api/types";
import { useSessionStore } from "@/stores/session";
import { useUserStore } from "@/stores/users";

const session = useSessionStore();
const users = useUserStore();

const inviteEmail = ref("");
const inviteRole = ref<Role>("member");
const inviting = ref(false);

onMounted(() => {
  if (session.can("user_manage")) void users.load();
});

function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const zone = session.session?.tenant.timezone ?? "Asia/Shanghai";
  return new Date(iso).toLocaleString("zh-CN", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function sendInvite(): Promise<void> {
  inviting.value = true;
  try {
    if (await users.invite(inviteEmail.value, inviteRole.value)) {
      inviteEmail.value = "";
      inviteRole.value = "member";
    }
  } finally {
    inviting.value = false;
  }
}

async function onRole(userId: string, event: Event): Promise<void> {
  const role = (event.target as HTMLSelectElement).value as Role;
  await users.changeRole(userId, role);
}

async function onDeactivate(userId: string, name: string): Promise<void> {
  if (!window.confirm(`停用 ${name}？对方的当前会话会立刻结束。`)) return;
  await users.deactivate(userId);
}

const filters: { id: UserStatus | "all"; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "pending", label: "待审批" },
  { id: "active", label: "正常" },
  { id: "deactivated", label: "已停用" },
];
</script>

<template>
  <section>
    <h1 class="page-title">成员</h1>

    <p v-if="!session.can('user_manage')" class="notice notice--error">
      只有管理员可以查看注册信息。请联系管理员。
    </p>

    <template v-else>
      <form class="invite card" @submit.prevent="sendInvite">
        <input
          v-model="inviteEmail"
          class="input"
          type="email"
          placeholder="邀请邮箱"
          required
        />
        <select v-model="inviteRole" class="select">
          <option v-for="role in ROLES" :key="role" :value="role">{{ ROLE_LABELS[role] }}</option>
        </select>
        <button class="button button--primary" type="submit" :disabled="inviting">
          {{ inviting ? "发送中…" : "发送邀请" }}
        </button>
        <p class="muted invite__hint">邀请 7 天内有效，不检查域名白名单（这是自助注册的例外路径）。</p>
      </form>

      <p v-if="users.error" class="notice notice--error">{{ users.error }}</p>
      <p v-if="users.notice" class="notice notice--ok">{{ users.notice }}</p>

      <div class="toolbar">
        <button
          v-for="one in filters"
          :key="one.id"
          type="button"
          class="button"
          :class="{ 'button--primary': users.filter === one.id }"
          @click="users.filter = one.id"
        >
          {{ one.label }}
          <template v-if="one.id === 'pending' && users.pendingCount">
            （{{ users.pendingCount }}）
          </template>
        </button>
      </div>

      <div v-if="users.visible.length" class="table-wrap card">
        <table class="users">
          <thead>
            <tr>
              <th>邮箱</th>
              <th>名称</th>
              <th>角色</th>
              <th>状态</th>
              <th>邮箱验证</th>
              <th>注册时间</th>
              <th>最近登录</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in users.visible" :key="row.user_id">
              <td>
                {{ row.email }}
                <span class="muted">@{{ row.handle }}</span>
              </td>
              <td>{{ row.display_name }}</td>
              <td>
                <select
                  class="select"
                  :value="row.role"
                  :disabled="row.user_id === session.session?.user_id"
                  @change="onRole(row.user_id, $event)"
                >
                  <option v-for="role in ROLES" :key="role" :value="role">
                    {{ ROLE_LABELS[role] }}
                  </option>
                </select>
              </td>
              <td>
                <span class="pill" :class="`pill--${row.status}`">
                  {{ USER_STATUS_LABELS[row.status] }}
                </span>
              </td>
              <td>{{ row.email_verified_at ? when(row.email_verified_at) : "未验证" }}</td>
              <td>{{ when(row.created_at) }}</td>
              <td>{{ when(row.last_login_at) }}</td>
              <td class="users__actions">
                <button
                  v-if="row.status === 'pending'"
                  type="button"
                  class="button button--primary"
                  @click="users.approve(row.user_id)"
                >
                  批准
                </button>
                <button
                  v-else-if="row.status === 'active' && row.user_id !== session.session?.user_id"
                  type="button"
                  class="button"
                  @click="onDeactivate(row.user_id, row.display_name || row.email)"
                >
                  停用
                </button>
                <button
                  v-else-if="row.status === 'deactivated'"
                  type="button"
                  class="button"
                  @click="users.reactivate(row.user_id)"
                >
                  恢复
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="!users.loading" class="empty">没有符合条件的账号。</p>
    </template>
  </section>
</template>

<style scoped>
.invite {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.8rem;
  margin-bottom: 1rem;
}

.invite .input {
  flex: 1;
  min-width: 240px;
}

.invite__hint {
  flex-basis: 100%;
  margin: 0;
  font-size: 0.8rem;
}

.table-wrap {
  overflow-x: auto;
}

.users {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.users th,
.users td {
  text-align: left;
  padding: 0.55rem 0.7rem;
  border-bottom: 1px solid var(--relay-border);
  vertical-align: middle;
}

.users th {
  font-weight: 600;
  color: var(--relay-text-muted);
  font-size: 0.78rem;
}

.users__actions {
  white-space: nowrap;
}

.users .select {
  min-width: 6.5rem;
}
</style>
