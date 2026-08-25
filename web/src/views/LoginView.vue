<script setup lang="ts">
/**
 * Sign in, and the second factor when there is one.
 *
 * The TOTP step is a *step*, not an error state: AC-3 opens a session before the
 * second factor is verified, so the form swaps rather than the page failing. A red
 * "login failed" here would be wrong — nothing failed.
 *
 * AC-8: an unverified login is refused with a resend, not a bare "cannot log in".
 */
import { onMounted, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const router = useRouter();
const route = useRoute();

const email = ref("");
const password = ref("");
const code = ref("");
const busy = ref(false);

onMounted(() => {
  const incoming = route.query.notice;
  if (typeof incoming === "string" && incoming) session.notice = incoming;
});

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

async function resend(): Promise<void> {
  if (!email.value) return;
  busy.value = true;
  try {
    await session.resendVerification(email.value);
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
  <div class="auth">
    <form class="auth__card card" @submit.prevent="session.mfaRequired ? submitTotp() : submit()">
      <h1 class="auth__title">登录 Relay</h1>

      <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>
      <p v-else-if="session.notice" class="notice notice--ok">{{ session.notice }}</p>

      <template v-if="!session.mfaRequired">
        <label class="auth__field">
          <span>邮箱</span>
          <input v-model="email" class="input" type="email" autocomplete="username" required />
        </label>
        <label class="auth__field">
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
        <p class="muted auth__hint">请输入两步验证动态码。</p>
        <label class="auth__field">
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

      <button class="button button--primary auth__submit" type="submit" :disabled="busy">
        {{ busy ? "请稍候…" : session.mfaRequired ? "验证" : "登录" }}
      </button>

      <button
        v-if="session.needsVerification"
        type="button"
        class="button"
        :disabled="busy || !email"
        @click="resend"
      >
        重新发送验证邮件
      </button>

      <p v-if="!session.mfaRequired" class="auth__links">
        <RouterLink :to="{ name: 'signup' }">没有账号？去注册</RouterLink>
        <RouterLink :to="{ name: 'verify' }">重发验证邮件</RouterLink>
      </p>
    </form>
  </div>
</template>
