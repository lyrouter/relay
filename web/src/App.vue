<script setup lang="ts">
/**
 * The shell: navigation, the notification badge, and the one place a signed-out
 * user is sent to the login screen.
 *
 * The badge polls. F-1 made in-app the only notification channel in S1, which makes
 * this number the whole reach surface — and P-3 asks the team to watch whether that
 * is enough during the dual-track trial. If the week-6 answer is "I never saw a
 * notification", the fix is NT-3 (email, ~0.5 pd), not a shorter poll.
 */
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { INBOX_POLL_MS, useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const meta = useMetaStore();
const route = useRoute();

let poll: number | undefined;

const showChrome = computed(() => session.signedIn && route.name !== "login");

async function boot(): Promise<void> {
  if (!session.signedIn) return;
  if (!meta.loaded) await meta.load();
  await meta.loadInbox();
}

onMounted(() => {
  void boot();
  poll = window.setInterval(() => {
    if (session.signedIn) void meta.loadInbox();
  }, INBOX_POLL_MS);
});

onBeforeUnmount(() => {
  if (poll !== undefined) window.clearInterval(poll);
});

watch(() => session.signedIn, () => void boot());
</script>

<template>
  <div class="app">
    <header v-if="showChrome" class="app__bar">
      <RouterLink class="app__brand" :to="{ name: 'logs' }">Relay</RouterLink>

      <nav class="app__nav">
        <RouterLink :to="{ name: 'logs' }">日志</RouterLink>
        <RouterLink :to="{ name: 'tickets' }">工单</RouterLink>
        <RouterLink :to="{ name: 'board' }">看板</RouterLink>
        <RouterLink :to="{ name: 'my-tickets' }">我的</RouterLink>
      </nav>

      <div class="app__right">
        <span class="app__badge" :class="{ 'app__badge--zero': meta.unread === 0 }">
          未读 {{ meta.unread }}
        </span>
        <!-- The tenant name, not the slug: the slug belongs in URLs. Shown even
             with one tenant so that the second one is not a surprise. -->
        <span class="app__tenant">{{ session.session?.tenant.name }}</span>
        <span class="app__who">{{ session.session?.display_name }}</span>
        <button type="button" class="app__logout" @click="session.logout()">退出</button>
      </div>
    </header>

    <main class="app__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app__bar {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.6rem 1.25rem;
  border-bottom: 1px solid var(--relay-border);
  background: var(--relay-surface);
  position: sticky;
  top: 0;
  z-index: 10;
}

.app__brand {
  font-weight: 700;
  font-size: 1.05rem;
  text-decoration: none;
  color: var(--relay-text);
}

.app__nav {
  display: flex;
  gap: 1rem;
}

.app__nav a {
  text-decoration: none;
  color: var(--relay-text-muted);
  padding: 0.2rem 0;
  border-bottom: 2px solid transparent;
}

.app__nav a.router-link-active {
  color: var(--relay-text);
  border-bottom-color: var(--relay-accent);
}

.app__right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.85rem;
  font-size: 0.85rem;
  color: var(--relay-text-muted);
}

.app__badge {
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: var(--relay-accent-soft);
  color: var(--relay-accent);
}

.app__badge--zero {
  background: var(--relay-surface-alt);
  color: var(--relay-text-muted);
}

.app__logout {
  background: none;
  border: 1px solid var(--relay-border);
  border-radius: 6px;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
  color: inherit;
  font: inherit;
}

.app__main {
  flex: 1;
  padding: 1.5rem;
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
}
</style>
