<script setup lang="ts">
/**
 * 工作 · list / board / assigned-to-me as lenses on the same tickets.
 * Secondary to 此刻: this is where you manage status flow, not where you land.
 */
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <section class="work">
    <header class="work__head">
      <h1 class="page-title">工作</h1>
      <nav class="work__tabs">
        <RouterLink :to="{ name: 'work-list' }">列表</RouterLink>
        <RouterLink :to="{ name: 'board' }">看板</RouterLink>
        <RouterLink :to="{ name: 'my-tickets' }">指派给我</RouterLink>
      </nav>
    </header>
    <RouterView />
  </section>
</template>

<style scoped>
.work {
  padding: 1.25rem 1.5rem;
}

.work__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.75rem 1.25rem;
  margin-bottom: 0.5rem;
}

.work__head .page-title {
  margin-bottom: 0;
}

.work__tabs {
  display: flex;
  gap: 0.85rem;
  font-size: 0.9rem;
}

.work__tabs a {
  text-decoration: none;
  color: var(--relay-text-muted);
  padding: 0.15rem 0;
  border-bottom: 2px solid transparent;
}

.work__tabs a.router-link-active {
  color: var(--relay-text);
  border-bottom-color: var(--relay-accent);
}
</style>
