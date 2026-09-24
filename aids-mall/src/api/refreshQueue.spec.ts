import { describe, expect, it, vi } from 'vitest'

import { createRefreshCoordinator } from './refreshQueue'

/** 可手动控制何时完成的刷新桩。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('createRefreshCoordinator', () => {
  it('并发调用只触发一次刷新，且所有调用方共享同一结果', async () => {
    const gate = deferred<string>()
    const refresh = vi.fn(() => gate.promise)
    const coordinator = createRefreshCoordinator({ refresh })

    const all = Promise.all([coordinator.run(), coordinator.run(), coordinator.run()])
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(coordinator.pending).toBe(true)

    gate.resolve('token-1')
    await expect(all).resolves.toEqual(['token-1', 'token-1', 'token-1'])
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('刷新结束后 pending 归位，下一次调用发起新的刷新', async () => {
    const first = deferred<string>()
    const refresh = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue('token-2')
    const coordinator = createRefreshCoordinator({ refresh })

    const running = coordinator.run()
    first.resolve('token-1')
    await expect(running).resolves.toBe('token-1')
    expect(coordinator.pending).toBe(false)

    await expect(coordinator.run()).resolves.toBe('token-2')
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('刷新失败：所有等待者都被拒绝，且失败后仍可重新刷新', async () => {
    const failing = deferred<string>()
    const refresh = vi.fn().mockReturnValueOnce(failing.promise).mockResolvedValue('token-ok')
    const coordinator = createRefreshCoordinator({ refresh })

    const all = Promise.allSettled([coordinator.run(), coordinator.run()])
    failing.reject(new Error('refresh failed'))
    const results = await all
    expect(results.map((r) => r.status)).toEqual(['rejected', 'rejected'])
    expect(coordinator.pending).toBe(false)

    // 关键：失败不能把队列锁死
    await expect(coordinator.run()).resolves.toBe('token-ok')
  })

  it('刷新失败会回调 onFailure（清 token / 跳登录的挂点）', async () => {
    const onFailure = vi.fn()
    const boom = new Error('boom')
    const coordinator = createRefreshCoordinator({
      refresh: () => Promise.reject(boom),
      onFailure,
    })

    await expect(coordinator.run()).rejects.toThrow('boom')
    expect(onFailure).toHaveBeenCalledTimes(1)
    expect(onFailure).toHaveBeenCalledWith(boom)
  })

  it('onFailure 只被调用一次（并发等待者不重复触发登出）', async () => {
    const onFailure = vi.fn()
    const gate = deferred<string>()
    const coordinator = createRefreshCoordinator({ refresh: () => gate.promise, onFailure })

    const all = Promise.allSettled([coordinator.run(), coordinator.run()])
    gate.reject(new Error('nope'))
    await all
    expect(onFailure).toHaveBeenCalledTimes(1)
  })
})
