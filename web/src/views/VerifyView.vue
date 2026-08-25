<script setup lang="ts">
/**
 * Email verification landing page.
 *
 * The mail points here (`/verify?token=…`). Consuming the token is a POST, so
 * this page is the browser half of that round trip — without it the link 404s
 * and an unverified account can never log in. Invalid / expired tokens get the
 * resend form rather than a dead end (AC-8).
 */
import { onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const route = useRoute();

const email = ref("");
const busy = ref(false);
const result = ref<string | null>(null);
const activated = ref(false);

async function consume(): Promise<void> {
  const token = route.query.token;
  if (typeof token !== "string" || !token) return;
  busy.value = true;
  try {
    const verified = await session.verifyEmail(token);
    if (verified) {
      result.value = verified.message;
      activated.value = verified.activated;
    }
  } finally {
    busy.value = false;
  }
}

async function resend(): Promise<void> {
  busy.value = true;
  try {
    await session.resendVerification(email.value);
  } finally {
    busy.value = false;
  }
}

onMounted(() => void consume());
</script>

<template>
  <div class="auth">
    <form class="auth__card card" @submit.prevent="resend">
      <h1 class="auth__title">验证邮箱</h1>

      <p v-if="session.error" class="notice notice--error">{{ session.error }}</p>
      <p v-else-if="result" class="notice" :class="activated ? 'notice--ok' : 'notice--conflict'">
        {{ result }}
      </p>
      <p v-else-if="session.notice" class="notice notice--ok">{{ session.notice }}</p>
      <p v-else-if="busy && route.query.token" class="muted">正在验证…</p>
      <p v-else class="muted auth__hint">没有验证链接？填写注册邮箱，我们会再发一封。</p>

      <template v-if="!result">
        <label class="auth__field">
          <span>邮箱</span>
          <input v-model="email" class="input" type="email" autocomplete="username" required />
        </label>
        <button class="button button--primary auth__submit" type="submit" :disabled="busy">
          {{ busy ? "请稍候…" : "重新发送验证邮件" }}
        </button>
      </template>

      <p class="auth__links">
        <RouterLink :to="{ name: 'login' }">去登录</RouterLink>
        <RouterLink :to="{ name: 'signup' }">去注册</RouterLink>
      </p>
    </form>
  </div>
</template>
