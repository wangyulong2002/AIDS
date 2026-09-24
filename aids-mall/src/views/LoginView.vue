<script setup lang="ts">
import { ref } from 'vue'

/** 登录页占位：真实表单由 FE-03 落地。401 跳转需要一个存在的落点。 */
const account = ref('')
const password = ref('')
</script>

<template>
  <section>
    <h1>AIDS 商城 · 登录</h1>
    <form class="login-form">
      <label>账号<input v-model="account" type="text" autocomplete="username" /></label>
      <label
        >密码<input v-model="password" type="password" autocomplete="current-password"
      /></label>
    </form>
    <p>表单逻辑由 FE-03 落地，本页当前仅为路由占位。</p>
  </section>
</template>

<style scoped>
.login-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 320px;
}
</style>
