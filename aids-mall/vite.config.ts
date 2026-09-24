import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * 开发代理：前端一律以 `/api` 开头调用，与生产走 Nginx 的口径一致。
 *
 * 与 Nginx 的对应关系（deploy/nginx/conf.d/default.conf）：
 *   - `/api/**` -> 主业务 FastAPI，**剥离** `/api` 前缀（Nginx 用 rewrite，这里用 rewritE）；
 *   - `/api/ai/**` -> AI 服务，**不剥离**（AI 侧路由自带 `/api/ai` 前缀），走更长前缀以先匹配。
 */
const API_TARGET = process.env.VITE_PROXY_API ?? 'http://127.0.0.1:8080'
const AI_TARGET = process.env.VITE_PROXY_AI ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api/ai': {
        target: AI_TARGET,
        changeOrigin: true,
      },
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
