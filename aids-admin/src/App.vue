<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <header class="app-header">
    <strong>AIDS 管理后台</strong>
    <nav class="app-nav">
      <RouterLink to="/">首页</RouterLink>
    </nav>
  </header>
  <main class="app-main">
    <RouterView />
  </main>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 12px 20px;
  border-bottom: 1px solid #eee;
}

.app-nav a {
  color: #1677ff;
  text-decoration: none;
}

.app-main {
  padding: 20px;
  font-family: system-ui, sans-serif;
}
</style>
