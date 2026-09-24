/**
 * JWT 无感刷新 —— 单飞（single-flight）+ 并发排队（FE-02）。
 *
 * 要解决的问题：
 *   一个页面同时发出 N 个请求，Access Token 恰好过期 → N 个请求同时 401。
 *   若每个 401 都各自去刷新：
 *     ① 后端「刷新即轮换」（BE-03）会把先到的刷新作废成旧 Refresh Token，
 *        后到的刷新必然失败并触发登出 —— 用户被"误登出"；
 *     ② 刷出来的 N 个新 token 互相覆盖，最后落库的可能不是最后发出的那个。
 *
 * 解法：把刷新收敛成**单飞** —— 同一时刻只有一个刷新在飞，其余调用方共享其
 * 结果；刷新结束（无论成败）才允许发起下一次。
 *
 * 本模块刻意**不依赖 axios / DOM**：并发语义可以在纯 node 环境下用 vitest 直接
 * 验证（见 `refreshQueue.spec.ts`），不需要 jsdom，也不需要 mock 网络。
 */

export interface RefreshCoordinatorOptions {
  /** 真正执行刷新的函数：成功 resolve 新 accessToken，失败应 reject。 */
  refresh: () => Promise<string>
  /** 刷新最终失败时的回调（清 token / 跳登录由调用方决定）。 */
  onFailure?: (error: unknown) => void
}

export interface RefreshCoordinator {
  /** 取一个新 accessToken；并发调用只会触发**一次** `refresh()`。 */
  run: () => Promise<string>
  /** 当前是否有刷新在飞（测试与调试用）。 */
  readonly pending: boolean
}

export function createRefreshCoordinator(options: RefreshCoordinatorOptions): RefreshCoordinator {
  let inFlight: Promise<string> | null = null

  const run = (): Promise<string> => {
    if (inFlight) {
      // 并发排队：共享同一个 Promise，不重复发起刷新
      return inFlight
    }

    inFlight = options
      .refresh()
      .catch((error: unknown) => {
        options.onFailure?.(error)
        throw error
      })
      .finally(() => {
        // 无论成败都必须释放：否则一次失败会让后续刷新永久挂起
        inFlight = null
      })

    return inFlight
  }

  return {
    run,
    get pending() {
      return inFlight !== null
    },
  }
}
