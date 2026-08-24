<script setup lang="ts">
/**
 * Sign in, and the second factor when there is one.
 *
 * The TOTP step is a *step*, not an error state: AC-3 opens a session before the
 * second factor is verified, so the form swaps rather than the page failing. A red
 * "login failed" here would be wrong — nothing failed.
 */
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const router = useRouter();
const route = useRoute();

const email = ref("");
const password = ref("");
const code = ref("");
const busy = ref(false);

async function submit(): Promise<void> {
  busy.value = true;
  try {
    if (await session.login(email.value, password.value)) await go();
  } finally {
    busy.value = false;
  }
}

async function submitTotp(): Promise<void> {
  busy.value = true;
  try {
    if (await session.submitTotp(code.value)) await go();
  } finally {
    busy.value = false;
  }
}

async function go(): Promise<void> {
  const next = route.query.next;
  await router.replace(typeof next === "string" && next ? next : { name: "logs" });
}
</script>

<template>
  <div class="login">
    <form class="login__card card" @submit.prevent="session.mfaRequired ? submitTotp() : submit()">
      <h1 class="login__title">登录 Relay</h1>

      <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>

      <template v-if="!session.mfaRequired">
        <label class="login__field">
          <span>邮箱</span>
          <input v-model="email" class="input" type="email" autocomplete="username" required />
        </label>
        <label class="login__field">
          <span>密码</span>
          <input
            v-model="password"
            class="input"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>
      </template>

      <template v-else>
        <p class="muted login__hint">请输入两步验证动态码。</p>
        <label class="login__field">
          <span>动态码</span>
          <input
            v-model="code"
            class="input"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            required
          />
        </label>
      </template>

      <button class="button button--primary login__submit" type="submit" :disabled="busy">
        {{ busy ? "请稍候…" : session.mfaRequired ? "验证" : "登录" }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login {
  display: grid;
  place-items: center;
  min-height: 70vh;
}

.login__card {
  width: min(380px, 92vw);
  padding: 1.5rem;
  display: grid;
  gap: 0.9rem;
}

.login__title {
  margin: 0;
  font-size: 1.25rem;
}

.login__field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.login__hint {
  margin: 0;
  font-size: 0.85rem;
}

.login__submit {
  margin-top: 0.4rem;
}
</style>
