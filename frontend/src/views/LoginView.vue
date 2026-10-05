<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const form = reactive({ username: 'admin', password: '' })
const showPassword = ref(false)
const loading = ref(false)
const error = ref('')

// 只接受站内路径，避免登录后被带到外部地址。
function safeRedirect(value) {
  if (typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')) {
    return value
  }
  return '/'
}

function errorText(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return '登录失败，请检查网络后重试'
}

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    await router.replace(safeRedirect(route.query.redirect))
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-panel-sidebar px-4">
    <div class="w-full max-w-[400px] overflow-hidden rounded-md bg-white shadow-2xl">
      <div class="h-1 bg-panel-green"></div>
      <form class="px-8 py-8" @submit.prevent="onSubmit">
        <h1 class="text-xl font-semibold text-panel-text">删库跑路快捷助手</h1>
        <p class="mt-1 text-sm text-panel-muted">Linux 服务器运维管理面板</p>

        <label class="mt-7 block text-sm text-panel-text">
          用户名
          <input
            v-model="form.username"
            autocomplete="username"
            maxlength="64"
            class="mt-1.5 w-full rounded border border-panel-line px-3 py-2 outline-none focus:border-panel-green"
          />
        </label>

        <label class="mt-4 block text-sm text-panel-text">
          密码
          <span class="relative mt-1.5 block">
            <input
              v-model="form.password"
              :type="showPassword ? 'text' : 'password'"
              autocomplete="current-password"
              maxlength="72"
              class="w-full rounded border border-panel-line px-3 py-2 pr-14 outline-none focus:border-panel-green"
            />
            <button
              type="button"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-panel-muted"
              @click="showPassword = !showPassword"
            >
              {{ showPassword ? '隐藏' : '显示' }}
            </button>
          </span>
        </label>

        <p v-if="error" class="mt-3 text-sm text-[#e55353]">{{ error }}</p>

        <button
          type="submit"
          class="mt-6 w-full rounded bg-panel-green py-2.5 text-sm text-white hover:bg-[#1b8f32] disabled:opacity-60"
          :disabled="loading || !form.username || !form.password"
        >
          {{ loading ? '正在登录…' : '登录' }}
        </button>
        <p class="mt-4 text-xs leading-5 text-panel-muted">
          首次启动时，初始管理员密码会打印在后端控制台，且只显示一次。
        </p>
      </form>
    </div>
  </div>
</template>
