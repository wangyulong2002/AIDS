<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { isLoggedIn } = storeToRefs(auth)
</script>

<template>
  <section>
    <h1>AIDS 管理后台</h1>
    <p>工程骨架已就绪（T1 · FE-01 / FE-02）。</p>
    <p>登录状态：{{ isLoggedIn ? '已登录' : '未登录' }}</p>
  </section>
</template>
