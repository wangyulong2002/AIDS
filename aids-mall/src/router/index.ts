import { createRouter, createWebHistory } from 'vue-router'

/**
 * 两个前端工程各自持有一份路由表（FE-01：商城 / 管理后台是**独立工程**）。
 * 页面实现按 TASKS 逐步补齐，这里先钉住"路由骨架 + 懒加载"这一层。
 */
export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
    },
  ],
})
