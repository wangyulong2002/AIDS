import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { login as apiLogin } from '@/api/auth'
import { clearTokens, getAccessToken } from '@/api/tokenStore'

/**
 * 登录态（FE-01 脚手架里的 Pinia 示范 store）。
 * FE-03 / FE-14 起承载真实登录流程与权限信息。
 */
export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(getAccessToken())
  const isLoggedIn = computed(() => accessToken.value !== null)

  async function login(mobile: string, password: string): Promise<void> {
    const result = await apiLogin(mobile, password)
    accessToken.value = result.accessToken
  }

  function logout(): void {
    clearTokens()
    accessToken.value = null
  }

  return { accessToken, isLoggedIn, login, logout }
})
