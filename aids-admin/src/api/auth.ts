/** 认证接口（FE-02 的无感刷新依赖这里写入的 Refresh Token）。 */

import { apiPost } from './http'
import { clearTokens, setTokens } from './tokenStore'

export interface LoginResult {
  accessToken: string
  refreshToken: string
  expiresIn: number
}

export async function login(mobile: string, password: string): Promise<LoginResult> {
  const result = await apiPost<LoginResult>('/auth/login', { mobile, password })
  setTokens(result.accessToken, result.refreshToken)
  return result
}

export async function logout(): Promise<void> {
  try {
    await apiPost<void>('/auth/logout')
  } finally {
    // 服务端吊销失败也必须清本地 —— 否则用户点了"退出"却仍是登录态
    clearTokens()
  }
}
