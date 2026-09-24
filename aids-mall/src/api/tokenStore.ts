/**
 * Token 存储（FE-02）。
 *
 * 为什么要包一层：
 *   1. 测试/SSR 环境没有 `window`，直接访问会抛 ReferenceError；
 *   2. 键名必须**唯一** —— 散落的 `localStorage.getItem('refreshToken')`
 *      在改名时必然漏改，症状是"刷新总是失败"，且只在浏览器里复现。
 */

const ACCESS_KEY = 'aids.accessToken'
const REFRESH_KEY = 'aids.refreshToken'

let memoryAccess: string | null = null
let memoryRefresh: string | null = null

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    // 隐私模式/禁用存储时访问 localStorage 会抛异常，此处降级为内存态
    return null
  }
}

export function getAccessToken(): string | null {
  return storage()?.getItem(ACCESS_KEY) ?? memoryAccess
}

export function getRefreshToken(): string | null {
  return storage()?.getItem(REFRESH_KEY) ?? memoryRefresh
}

export function setTokens(accessToken: string, refreshToken?: string | null): void {
  memoryAccess = accessToken
  storage()?.setItem(ACCESS_KEY, accessToken)
  if (refreshToken !== undefined && refreshToken !== null) {
    memoryRefresh = refreshToken
    storage()?.setItem(REFRESH_KEY, refreshToken)
  }
}

export function clearTokens(): void {
  memoryAccess = null
  memoryRefresh = null
  storage()?.removeItem(ACCESS_KEY)
  storage()?.removeItem(REFRESH_KEY)
}
