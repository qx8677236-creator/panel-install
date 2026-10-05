import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AdminLayout from '../layouts/AdminLayout.vue'
import LoginView from '../views/LoginView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true, title: '登录' },
    },
    {
      path: '/',
      component: AdminLayout,
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('../views/DashboardView.vue'),
          meta: { title: '首页' },
        },
        {
          path: 'files',
          name: 'files',
          component: () => import('../views/FilesView.vue'),
          meta: { title: '文件' },
        },
        {
          path: 'sites',
          name: 'sites',
          component: () => import('../views/SitesView.vue'),
          meta: { title: '网站' },
        },
        {
          path: 'databases',
          name: 'databases',
          component: () => import('../views/DatabasesView.vue'),
          meta: { title: '数据库' },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('../views/LogsView.vue'),
          meta: { title: '日志' },
        },
        {
          path: 'crontab',
          name: 'crontab',
          component: () => import('../views/CrontabView.vue'),
          meta: { title: '计划任务' },
        },
        {
          path: 'security',
          name: 'security',
          component: () => import('../views/SecurityView.vue'),
          meta: { title: '安全' },
        },
        {
          path: 'terminal',
          name: 'terminal',
          component: () => import('../views/TerminalView.vue'),
          meta: { title: '终端' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('../views/SettingsView.vue'),
          meta: { title: '面板设置' },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// 未登录去登录页；已登录再打开登录页则回首页。
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    if (to.name === 'login') {
      const token = await auth.ensureAccessToken()
      if (token) return { name: 'dashboard' }
    }
    return true
  }
  const token = await auth.ensureAccessToken()
  if (!token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

router.afterEach((to) => {
  const base = '删库跑路快捷助手'
  document.title = to.meta.title ? `${to.meta.title} · ${base}` : base
})

export default router
