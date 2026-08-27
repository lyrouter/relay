<script setup lang="ts">
/**
 * The signed-in person's own account: display name and password.
 *
 * Email and role are shown but not editable. Email is the residency credential
 * (AC-9); role is an Admin decision (AC-4). A second copy of either rule in
 * this form would be a hole, not a convenience.
 */
import { computed, onMounted, ref, watch } from "vue";

import { ROLE_LABELS } from "@/api/types";
import { initials } from "@/lib/context";
import { useSessionStore } from "@/stores/session";

const session = useSessionStore();

const displayName = ref("");
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const nameBusy = ref(false);
const passwordBusy = ref(false);
const passwordMismatch = ref(false);

const me = computed(() => session.session);
const avatar = computed(() => initials(me.value?.display_name));
const roleLabel = computed(() => (me.value ? ROLE_LABELS[me.value.role] : ""));

function syncName(): void {
  displayName.value = me.value?.display_name ?? "";
}

onMounted(() => {
  session.error = null;
  session.notice = null;
  syncName();
});

watch(() => me.value?.display_name, syncName);

async function saveName(): Promise<void> {
  nameBusy.value = true;
  try {
    await session.updateDisplayName(displayName.value);
  } finally {
    nameBusy.value = false;
  }
}

async function savePassword(): Promise<void> {
  passwordMismatch.value = false;
  session.error = null;
  session.notice = null;
  if (newPassword.value !== confirmPassword.value) {
    passwordMismatch.value = true;
    return;
  }
  passwordBusy.value = true;
  try {
    if (await session.changePassword(currentPassword.value, newPassword.value)) {
      currentPassword.value = "";
      newPassword.value = "";
      confirmPassword.value = "";
    }
  } finally {
    passwordBusy.value = false;
  }
}
</script>

<template>
  <section v-if="me" class="profile">
    <h1 class="page-title">个人资料</h1>

    <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>
    <p v-else-if="session.notice" class="notice notice--ok">{{ session.notice }}</p>

    <div class="profile__grid">
      <form class="card profile__card" @submit.prevent="saveName">
        <header class="profile__head">
          <span class="profile__avatar" aria-hidden="true">{{ avatar }}</span>
          <div>
            <h2 class="profile__title">账号</h2>
            <p class="muted">邮箱与角色由管理员管理，不能在这里改。</p>
          </div>
        </header>

        <dl class="profile__facts">
          <div>
            <dt>邮箱</dt>
            <dd>{{ me.email }}</dd>
          </div>
          <div>
            <dt>角色</dt>
            <dd>{{ roleLabel }}</dd>
          </div>
          <div>
            <dt>租户</dt>
            <dd>{{ me.tenant.name }}</dd>
          </div>
          <div>
            <dt>两步验证</dt>
            <dd>{{ me.mfa_enrolled ? "已开启" : "未开启" }}</dd>
          </div>
        </dl>

        <label class="profile__field">
          <span>显示名</span>
          <input
            v-model="displayName"
            class="input"
            type="text"
            autocomplete="nickname"
            maxlength="200"
            required
          />
        </label>
        <button class="button button--primary" type="submit" :disabled="nameBusy">
          {{ nameBusy ? "保存中…" : "保存显示名" }}
        </button>
      </form>

      <form class="card profile__card" @submit.prevent="savePassword">
        <h2 class="profile__title">修改密码</h2>
        <p class="muted">改密后其他设备上的登录会立刻结束，当前这次登录保留。</p>

        <label class="profile__field">
          <span>当前密码</span>
          <input
            v-model="currentPassword"
            class="input"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>
        <label class="profile__field">
          <span>新密码</span>
          <input
            v-model="newPassword"
            class="input"
            type="password"
            autocomplete="new-password"
            required
          />
        </label>
        <label class="profile__field">
          <span>确认新密码</span>
          <input
            v-model="confirmPassword"
            class="input"
            type="password"
            autocomplete="new-password"
            required
          />
        </label>
        <p v-if="passwordMismatch" class="notice notice--error">两次输入的新密码不一致。</p>
        <p class="muted profile__hint">
          至少 8 位，并包含大写、小写、数字、符号中的至少三类。
        </p>
        <button class="button button--primary" type="submit" :disabled="passwordBusy">
          {{ passwordBusy ? "更新中…" : "更新密码" }}
        </button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.profile {
  max-width: 880px;
}

.profile__grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  align-items: start;
}

.profile__card {
  display: grid;
  gap: 0.85rem;
  padding: 1.15rem 1.25rem;
}

.profile__head {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}

.profile__avatar {
  width: 2.75rem;
  height: 2.75rem;
  border-radius: 999px;
  background: #334155;
  color: #fff;
  font-size: 0.85rem;
  font-weight: 600;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.profile__title {
  margin: 0;
  font-size: 1.05rem;
}

.profile__head .muted,
.profile__card > .muted {
  margin: 0.2rem 0 0;
  font-size: 0.85rem;
}

.profile__facts {
  display: grid;
  gap: 0.65rem;
  margin: 0;
}

.profile__facts div {
  display: grid;
  gap: 0.15rem;
}

.profile__facts dt {
  font-size: 0.78rem;
  color: var(--relay-text-muted);
}

.profile__facts dd {
  margin: 0;
}

.profile__field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.profile__hint {
  margin: 0;
  font-size: 0.85rem;
}

.profile__card .notice {
  margin: 0;
}
</style>
