<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { panelSettings, panelTitle } from '../api/panel'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const pageTitle = computed(() => route.meta.title || '首页')

onMounted(async () => {
  try {
    const { data } = await panelSettings()
    if (data.title) panelTitle.value = data.title
  } catch (error) {
    panelTitle.value = '删库跑路快捷助手'
  }
})

async function onLogout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <div class="flex h-full overflow-hidden bg-panel-bg text-panel-text">
    <aside class="flex w-56 shrink-0 flex-col bg-panel-sidebar text-white">
      <div class="flex h-16 items-center gap-2.5 border-b border-white/10 px-4">
        <svg viewBox="0 0 24 24" class="h-8 w-8 shrink-0 text-panel-green" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="3" y="3" width="18" height="18" rx="4" />
          <path d="M7 15.5 11 11l3 2.5L18 8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <div class="min-w-0">
          <div class="truncate text-[13px] font-semibold leading-4">{{ panelTitle }}</div>
          <div class="mt-0.5 text-[11px] text-white/45">服务器运维面板</div>
        </div>
      </div>
      <nav class="flex-1 px-3 py-3">
        <router-link
          :to="{ name: 'dashboard' }"
          class="flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-panel-green"></span>
          首页
        </router-link>
        <router-link
          :to="{ name: 'sites' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          网站
        </router-link>
        <router-link
          :to="{ name: 'files' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          文件
        </router-link>
        <router-link
          :to="{ name: 'databases' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          数据库
        </router-link>
        <router-link
          :to="{ name: 'logs' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          日志
        </router-link>
        <router-link
          :to="{ name: 'crontab' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          计划任务
        </router-link>
        <router-link
          :to="{ name: 'security' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          安全
        </router-link>
        <router-link
          :to="{ name: 'terminal' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          终端
        </router-link>
        <router-link
          :to="{ name: 'settings' }"
          class="mt-1 flex items-center gap-2 rounded px-3 py-2.5 text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
          exact-active-class="!bg-white/10 !text-white shadow-[inset_3px_0_0_#20a53a]"
        >
          <span class="inline-block h-1.5 w-1.5 rounded-full bg-white/40"></span>
          面板设置
        </router-link>
      </nav>
      <div class="border-t border-white/10 px-4 py-3 text-[11px] text-white/35">v0.4 · 定时备份</div>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex h-16 items-center justify-between border-b border-panel-line bg-white px-5">
        <h1 class="text-[15px] font-medium">{{ pageTitle }}</h1>
        <div class="flex items-center gap-4 text-sm">
          <span class="text-panel-muted">{{ auth.username || 'admin' }}</span>
          <button
            type="button"
            class="rounded border border-panel-line px-3 py-1 text-[13px] text-panel-text hover:border-panel-green hover:text-panel-green"
            @click="onLogout"
          >
            退出
          </button>
        </div>
      </header>
      <main class="min-h-0 flex-1 overflow-hidden p-4">
        <router-view class="block h-full min-h-0" />
      </main>
    </div>
  </div>
</template>
