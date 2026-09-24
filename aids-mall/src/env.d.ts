/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 接口基地址。生产默认 `/api`（走 Nginx）；构建期由 VITE_API_BASE_URL 注入。 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
