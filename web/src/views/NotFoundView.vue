<script setup lang="ts">
/**
 * 404 for a route that does not exist.
 *
 * Worth one deliberate note: a ticket the reader may not see also lands here,
 * because the API answers 404 rather than 403 (S-21 / MT-6). So the wording must
 * not say "you do not have permission" — that sentence would confirm the thing the
 * 404 exists to hide.
 */
import { RouterLink } from "vue-router";
</script>

<template>
  <div class="empty">
    <p>找不到这个页面，或者它不存在。</p>
    <RouterLink :to="{ name: 'now' }">回到此刻</RouterLink>
  </div>
</template>
