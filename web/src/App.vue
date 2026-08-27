<script setup lang="ts">
/**
 * Shell matching mockups/now.png: top search bar + left icon rail.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { initials } from "@/lib/context";
import { INBOX_POLL_MS, useMetaStore } from "@/stores/meta";
import { useSessionStore } from "@/stores/session";

const session = useSessionStore();
const meta = useMetaStore();
const route = useRoute();
const router = useRouter();

const search = ref("");
let poll: number | undefined;

const showChrome = computed(() => session.signedIn && route.meta.public !== true);
const workActive = computed(() => route.path.startsWith("/work"));
const avatar = computed(() => initials(session.session?.display_name));

type NavItem = {
  name: string;
  to: { name: string };
  label: string;
  icon: string;
  match?: "work";
};

const nav = computed((): NavItem[] => {
  const items: NavItem[] = [
    { name: "now", to: { name: "now" }, label: "此刻", icon: "bolt" },
    // Temporarily hidden: 上下文 (chain browse). Restore this item + the /context
    // route in router/index.ts when the surface ships again.
    { name: "work", to: { name: "work-list" }, label: "工作", icon: "check", match: "work" },
    { name: "logs", to: { name: "logs" }, label: "知识", icon: "book" },
  ];
  if (session.can("user_manage")) {
    items.push({ name: "users", to: { name: "users" }, label: "成员", icon: "people" });
  }
  return items;
});

function isActive(item: NavItem): boolean {
  if (item.match === "work") return workActive.value;
  if (item.name === "logs") return route.path.startsWith("/logs");
  return route.name === item.name;
}

async function boot(): Promise<void> {
  if (!session.signedIn) return;
  if (!meta.loaded) await meta.load();
  await meta.loadInbox();
}

function onSearch(): void {
  const q = search.value.trim();
  void router.push({ name: "work-list", query: q ? { q } : {} });
}

function onKeydown(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    document.getElementById("relay-global-search")?.focus();
  }
}

onMounted(() => {
  void boot();
  poll = window.setInterval(() => {
    if (session.signedIn) void meta.loadInbox();
  }, INBOX_POLL_MS);
  window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  if (poll !== undefined) window.clearInterval(poll);
  window.removeEventListener("keydown", onKeydown);
});

watch(() => session.signedIn, () => void boot());
</script>

<template>
  <div class="app" :class="{ 'app--chrome': showChrome }">
    <template v-if="showChrome">
      <header class="top">
        <RouterLink class="top__brand" :to="{ name: 'now' }">
          <span class="top__bolt" aria-hidden="true">⚡</span>
          Relay
        </RouterLink>

        <form class="top__search" @submit.prevent="onSearch">
          <span class="top__search-icon" aria-hidden="true">⌕</span>
          <input
            id="relay-global-search"
            v-model="search"
            class="top__search-input"
            type="search"
            placeholder="搜索 trace / 工单 / 日志"
            autocomplete="off"
          />
          <kbd class="top__kbd">⌘ K</kbd>
        </form>

        <div class="top__right">
          <RouterLink
            class="top__bell"
            :class="{ 'top__bell--on': meta.unread > 0 }"
            :to="{ name: 'now', hash: '#inbox' }"
            :title="`未读 ${meta.unread}`"
          >
            <span aria-hidden="true">🔔</span>
            <span v-if="meta.unread > 0" class="top__count">{{ meta.unread > 99 ? "99+" : meta.unread }}</span>
          </RouterLink>
          <span class="top__tenant">{{ session.session?.tenant.name }}</span>
          <button
            type="button"
            class="top__avatar"
            :title="session.session?.display_name"
            @click="session.logout()"
          >
            {{ avatar }}
          </button>
        </div>
      </header>

      <div class="shell">
        <nav class="rail" aria-label="主导航">
          <RouterLink
            v-for="item in nav"
            :key="item.name"
            class="rail__item"
            :class="{ 'rail__item--on': isActive(item) }"
            :to="item.to"
          >
            <span class="rail__icon" :data-icon="item.icon" aria-hidden="true" />
            <span class="rail__label">{{ item.label }}</span>
          </RouterLink>
        </nav>

        <main class="main" :class="{ 'main--flush': route.name === 'ticket' || route.name === 'now' }">
          <RouterView />
        </main>
      </div>
    </template>

    <main v-else class="main main--auth">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app--chrome {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--relay-bg);
}

.top {
  display: grid;
  grid-template-columns: auto minmax(240px, 560px) auto;
  align-items: center;
  gap: 1rem;
  padding: 0.55rem 1rem 0.55rem 0.85rem;
  background: var(--relay-surface);
  border-bottom: 1px solid var(--relay-border);
  position: sticky;
  top: 0;
  z-index: 20;
}

.top__brand {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-weight: 700;
  font-size: 1.05rem;
  text-decoration: none;
  color: var(--relay-text);
  letter-spacing: -0.02em;
  min-width: 5.5rem;
}

.top__bolt {
  font-size: 0.95rem;
}

.top__search {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  width: 100%;
  margin: 0 auto;
  padding: 0.35rem 0.7rem;
  border: 1px solid var(--relay-border);
  border-radius: 10px;
  background: var(--relay-surface-alt);
}

.top__search-icon {
  color: var(--relay-text-muted);
  font-size: 0.95rem;
}

.top__search-input {
  flex: 1;
  border: 0;
  background: transparent;
  font: inherit;
  color: var(--relay-text);
  outline: none;
  min-width: 0;
}

.top__kbd {
  font-family: var(--relay-mono);
  font-size: 0.7rem;
  color: var(--relay-text-muted);
  border: 1px solid var(--relay-border);
  border-radius: 4px;
  padding: 0.05rem 0.3rem;
  background: var(--relay-surface);
}

.top__right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  justify-content: flex-end;
}

.top__bell {
  position: relative;
  text-decoration: none;
  color: var(--relay-text-muted);
  font-size: 1rem;
  line-height: 1;
  padding: 0.25rem;
}

.top__count {
  position: absolute;
  top: -2px;
  right: -6px;
  min-width: 1rem;
  height: 1rem;
  padding: 0 0.25rem;
  border-radius: 999px;
  background: var(--relay-danger);
  color: #fff;
  font-size: 0.65rem;
  font-weight: 600;
  display: grid;
  place-items: center;
}

.top__tenant {
  font-size: 0.85rem;
  color: var(--relay-text-muted);
}

.top__avatar {
  width: 2rem;
  height: 2rem;
  border-radius: 999px;
  border: 0;
  background: #334155;
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
}

.shell {
  flex: 1;
  display: flex;
  min-height: 0;
}

.rail {
  width: 72px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem 0.4rem;
  background: var(--relay-rail);
  border-right: 1px solid var(--relay-border);
}

.rail__item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.2rem;
  padding: 0.55rem 0.2rem;
  border-radius: 10px;
  text-decoration: none;
  color: var(--relay-text-muted);
  position: relative;
}

.rail__item--on {
  background: var(--relay-surface);
  color: var(--relay-text);
  box-shadow: 0 0 0 1px var(--relay-border);
}

.rail__item--on::before {
  content: "";
  position: absolute;
  left: -0.4rem;
  top: 18%;
  bottom: 18%;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--relay-accent);
}

.rail__icon {
  width: 1.25rem;
  height: 1.25rem;
  display: block;
  background: currentColor;
  mask-position: center;
  mask-repeat: no-repeat;
  mask-size: contain;
  -webkit-mask-position: center;
  -webkit-mask-repeat: no-repeat;
  -webkit-mask-size: contain;
}

.rail__icon[data-icon="bolt"] {
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M13 2 4 14h7l-1 8 10-13h-7l1-7z'/%3E%3C/svg%3E");
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M13 2 4 14h7l-1 8 10-13h-7l1-7z'/%3E%3C/svg%3E");
}

.rail__icon[data-icon="chain"] {
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'/%3E%3Cpath d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'/%3E%3C/svg%3E");
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'/%3E%3Cpath d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'/%3E%3C/svg%3E");
}

.rail__icon[data-icon="check"] {
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Crect x='3' y='3' width='18' height='18' rx='2'/%3E%3Cpath d='m9 12 2 2 4-4'/%3E%3C/svg%3E");
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Crect x='3' y='3' width='18' height='18' rx='2'/%3E%3Cpath d='m9 12 2 2 4-4'/%3E%3C/svg%3E");
}

.rail__icon[data-icon="book"] {
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20'/%3E%3Cpath d='M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'/%3E%3C/svg%3E");
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20'/%3E%3Cpath d='M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'/%3E%3C/svg%3E");
}

.rail__icon[data-icon="people"] {
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2'/%3E%3Ccircle cx='9' cy='7' r='4'/%3E%3Cpath d='M22 21v-2a4 4 0 0 0-3-3.87'/%3E%3Cpath d='M16 3.13a4 4 0 0 1 0 7.75'/%3E%3C/svg%3E");
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Cpath d='M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2'/%3E%3Ccircle cx='9' cy='7' r='4'/%3E%3Cpath d='M22 21v-2a4 4 0 0 0-3-3.87'/%3E%3Cpath d='M16 3.13a4 4 0 0 1 0 7.75'/%3E%3C/svg%3E");
}

.rail__label {
  font-size: 0.68rem;
  line-height: 1.2;
}

.main {
  flex: 1;
  min-width: 0;
  padding: 1.25rem 1.5rem;
  overflow: auto;
}

.main--flush {
  padding: 0;
}

.main--auth {
  min-height: 100vh;
  padding: 1.5rem;
}
</style>
