import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

/**
 * 单测配置（FE-02）与构建配置分开：`vite build` 不需要知道测试怎么跑，
 * 测试也不该把 devServer/代理带进来。
 *
 * environment 用 `node`（不是 jsdom）：FE-02 里最需要被验证的是**并发刷新语义**，
 * 它不碰 DOM —— 拆到独立的 `refreshQueue.test.ts` 正是为了让这条不变量能在
 * 最少的依赖下被验证（jsdom 会掩盖"实现对 DOM 有隐式依赖"这类问题）。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.spec.ts'],
  },
})
