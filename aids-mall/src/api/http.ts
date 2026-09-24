/**
 * axios 封装（FE-02）：统一响应解包、统一错误提示、JWT 无感刷新、401 跳登录。
 *
 * 后端契约（docs/API.md §1.1）：HTTP 200 既表示业务成功也表示业务失败（看 body 的
 * `code`，0 为成功）；只有 401/403/429/500 用非 200 状态码。因此这里**同时**处理两类
 * 失败：HTTP 层异常，与「HTTP 200 + code !== 0」。
 *
 * 为什么刷新请求不走本实例：
 *   本实例的响应拦截器在 401 时会触发刷新；若刷新请求也走它，刷新自身返回 401 时
 *   会**递归触发刷新**。故刷新统一走不带拦截器的 `rawClient`（BE-03：刷新即轮换）。
 */

import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

import { notifyError } from './notify'
import { createRefreshCoordinator } from './refreshQueue'
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from './tokenStore'

/** 接口基地址：生产构建期由 VITE_API_BASE_URL 注入，缺省走 Nginx 的 `/api`。 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** 登录跳转路径 */
const LOGIN_PATH = '/login'

/** 后端统一响应体（API.md §1.1） */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

/** 统一业务错误：HTTP 层错误与 `code !== 0` 都被规整成它。 */
export class ApiError extends Error {
  readonly code: number
  readonly httpStatus: number

  constructor(code: number, message: string, httpStatus: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.httpStatus = httpStatus
  }
}

/** 未登录 / Token 失效（API.md §1.3） */
const UNAUTHORIZED = 10002

/** 不带拦截器的裸客户端：专供刷新使用，避免递归。 */
const rawClient = axios.create({ baseURL: API_BASE_URL, timeout: 15_000 })

interface TokensPayload {
  accessToken: string
  refreshToken: string
}

async function doRefresh(): Promise<string> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    throw new ApiError(UNAUTHORIZED, '登录已过期，请重新登录', 401)
  }
  const response = await rawClient.post<ApiEnvelope<TokensPayload>>('/auth/refresh', {
    refreshToken,
  })
  const body = response.data
  if (body.code !== 0) {
    throw new ApiError(body.code, body.message, response.status)
  }
  setTokens(body.data.accessToken, body.data.refreshToken)
  return body.data.accessToken
}

function redirectToLogin(): void {
  clearTokens()
  if (typeof window !== 'undefined' && window.location.pathname !== LOGIN_PATH) {
    window.location.assign(LOGIN_PATH)
  }
}

const refreshCoordinator = createRefreshCoordinator({
  refresh: doRefresh,
  onFailure: redirectToLogin,
})

export const http: AxiosInstance = axios.create({ baseURL: API_BASE_URL, timeout: 15_000 })

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

interface RetriableConfig extends InternalAxiosRequestConfig {
  /** 标记已重放，避免"刷新成功但接口仍 401"时的无限重试。 */
  _aidsRetried?: boolean
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiEnvelope<unknown>>) => {
    const config = error.config as RetriableConfig | undefined
    if (error.response?.status === 401 && config && !config._aidsRetried) {
      config._aidsRetried = true
      try {
        const token = await refreshCoordinator.run()
        config.headers.set('Authorization', `Bearer ${token}`)
        return await http.request(config)
      } catch {
        return Promise.reject(new ApiError(UNAUTHORIZED, '登录已过期，请重新登录', 401))
      }
    }
    // 兜底：带 `Bearer` 的接口仍返回 10002（未登录），说明会话已不可恢复
    const body = error.response?.data
    if (body && typeof body === 'object' && body.code === UNAUTHORIZED) {
      redirectToLogin()
    }
    return Promise.reject(toApiError(error))
  },
)

function toApiError(error: AxiosError<ApiEnvelope<unknown>>): ApiError {
  const httpStatus = error.response?.status ?? 0
  const body = error.response?.data
  if (body && typeof body === 'object' && typeof body.code === 'number') {
    return new ApiError(body.code, body.message ?? error.message, httpStatus)
  }
  return new ApiError(-1, error.message || '网络异常，请稍后重试', httpStatus)
}

/**
 * 统一请求入口：解包 `{code, message, data}` 返回 `data`。
 *
 * 业务失败（HTTP 200 + code !== 0）在这里被转成 `ApiError` —— 调用方只需要
 * `try/catch`，不必每次都判断 `code`（漏判就是"接口失败当成功用"）。
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<ApiEnvelope<T>>(config)
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        const failure = new ApiError(body.code, body.message, response.status)
        notifyError(failure.message)
        throw failure
      }
      return body.data
    }
    return body as unknown as T
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    const failure = toApiError(error as AxiosError<ApiEnvelope<unknown>>)
    notifyError(failure.message)
    throw failure
  }
}

export function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return request<T>({ ...config, method: 'GET', url })
}

export function apiPost<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request<T>({ ...config, method: 'POST', url, data })
}
