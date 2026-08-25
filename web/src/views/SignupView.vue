<script setup lang="ts">
/**
 * AC-1 · self-service signup.
 *
 * Three outcomes, and the page must not invent a fourth: join (check your mail),
 * wait for Admin approval, or be refused with "ask for an invite". The backend
 * answers 202 for all of them, including "already registered", so the copy here
 * is the server's message — not a local "you are in".
 */
import { ref } from "vue";
import { RouterLink } from "vue-router";

import { useSessionStore } from "@/stores/session";

const session = useSessionStore();

const email = ref("");
const password = ref("");
const displayName = ref("");
const busy = ref(false);
const done = ref(false);
const refused = ref(false);
const message = ref("");

async function submit(): Promise<void> {
  busy.value = true;
  done.value = false;
  refused.value = false;
  try {
    const result = await session.signup(email.value, password.value, displayName.value);
    if (!result) return;
    done.value = true;
    refused.value = result.outcome === "refused";
    message.value = result.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="auth">
    <form class="auth__card card" @submit.prevent="submit">
      <h1 class="auth__title">注册 Relay</h1>

      <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>
      <p v-else-if="done && refused" class="notice notice--error">{{ message }}</p>
      <p v-else-if="done" class="notice notice--ok">{{ message }}</p>

      <template v-if="!done || refused">
        <label class="auth__field">
          <span>邮箱</span>
          <input
            v-model="email"
            class="input"
            type="email"
            autocomplete="username"
            required
          />
        </label>
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
          至少 12 位，并包含大写、小写、数字、符号中的至少三类。只用公司邮箱域名才能自助注册。
        </p>
        <button class="button button--primary auth__submit" type="submit" :disabled="busy">
          {{ busy ? "请稍候…" : "注册" }}
        </button>
      </template>

      <p class="auth__links">
        <RouterLink :to="{ name: 'login' }">已有账号？去登录</RouterLink>
        <RouterLink :to="{ name: 'verify' }">重发验证邮件</RouterLink>
      </p>
    </form>
  </div>
</template>
