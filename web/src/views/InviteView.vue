<script setup lang="ts">
/**
 * Invitation accept page.
 *
 * The mail points here (`/invite?token=…`). Holding the token *is* proof of
 * address, so accepting creates an active, verified account and then sends the
 * person to login — the accept route deliberately does not open a session.
 */
import { computed, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const route = useRoute();
const router = useRouter();

const token = computed(() => (typeof route.query.token === "string" ? route.query.token : ""));
const password = ref("");
const displayName = ref("");
const busy = ref(false);

async function submit(): Promise<void> {
  if (!token.value) return;
  busy.value = true;
  try {
    const message = await session.acceptInvitation(token.value, password.value, displayName.value);
    if (message) await router.replace({ name: "login", query: { notice: message } });
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="auth">
    <form class="auth__card card" @submit.prevent="submit">
      <h1 class="auth__title">接受邀请</h1>

      <p v-if="!token" class="notice notice--error">邀请链接无效。请联系管理员重新发送。</p>
      <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>

      <template v-if="token">
        <p class="muted auth__hint">设置密码后即可登录。邀请 7 天内有效。</p>
        <label class="auth__field">
          <span>显示名（可选）</span>
          <input v-model="displayName" class="input" type="text" autocomplete="nickname" />
        </label>
        <label class="auth__field">
          <span>密码</span>
          <input
            v-model="password"
            class="input"
            type="password"
            autocomplete="new-password"
            required
          />
        </label>
        <p class="muted auth__hint">
          至少 8 位，并包含大写、小写、数字、符号中的至少三类。
        </p>
        <button class="button button--primary auth__submit" type="submit" :disabled="busy">
          {{ busy ? "请稍候…" : "创建账号" }}
        </button>
      </template>

      <p class="auth__links">
        <RouterLink :to="{ name: 'login' }">已有账号？去登录</RouterLink>
      </p>
    </form>
  </div>
</template>
