<script setup>
import { onMounted, ref } from 'vue'
import { firewallStatus, sshConfig, sshScan } from '../api/security'
import { isCanceled } from '../api/http'

const config = ref({})
const items = ref([])
const message = ref('')
const firewall = ref(null)
const firewallMessage = ref('')
const loading = ref(false)
const firewallLoading = ref(false)

function detailOf(error, fallback) {
  if (isCanceled(error)) return ''
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

async function load(scan = false) {
  message.value = ''
  loading.value = true
  try {
    const { data } = scan ? await sshScan() : await sshConfig()
    config.value = data.config || {}
    items.value = data.items || []
  } catch (error) {
    const text = detailOf(error, '读取 SSH 配置失败')
    if (text) message.value = text
  } finally {
    loading.value = false
  }
}

async function loadFirewall() {
  firewallMessage.value = ''
  firewallLoading.value = true
  try {
    const { data } = await firewallStatus()
    firewall.value = data
  } catch (error) {
    firewall.value = null
    const text = detailOf(error, '读取防火墙失败')
    if (text) firewallMessage.value = text
  } finally {
    firewallLoading.value = false
  }
}

onMounted(() => {
  load(false)
  loadFirewall()
})
</script>

<template>
  <div class="h-full space-y-4 overflow-auto p-4">
    <div class="rounded border border-panel-line bg-white p-4">
      <div class="flex items-center justify-between">
        <h2 class="text-base font-medium">SSH 安全</h2>
        <button type="button" class="rounded border border-panel-line px-3 py-1 text-sm disabled:opacity-50" :disabled="loading" @click="load(true)">
          {{ loading ? '正在读取…' : '重新扫描' }}
        </button>
      </div>
      <p class="mt-2 text-xs text-panel-muted">这里只读取 sshd 配置。不会修改配置，也不会重启 SSH。</p>
      <p v-if="message" class="mt-2 text-sm text-[#c24141]">{{ message }}</p>
      <table class="mt-4 w-full text-left text-sm">
        <thead class="text-xs text-panel-muted"><tr><th class="py-2">配置项</th><th>当前值</th></tr></thead>
        <tbody>
          <tr v-for="(value, key) in config" :key="key" class="border-t border-panel-line">
            <td class="py-2">{{ key }}</td>
            <td>{{ value }}</td>
          </tr>
        </tbody>
      </table>
      <ul class="mt-4 space-y-2 text-sm">
        <li v-for="(item, index) in items" :key="index" class="border-t border-panel-line py-2">
          <span :class="item.level === 'high' ? 'text-[#c24141]' : 'text-panel-muted'">{{ item.level }}</span>
          {{ item.item }}：{{ item.message }}
        </li>
      </ul>
    </div>

    <div class="rounded border border-panel-line bg-white p-4">
      <div class="flex items-center justify-between">
        <h2 class="text-base font-medium">系统防火墙</h2>
        <button type="button" class="rounded border border-panel-line px-3 py-1 text-sm disabled:opacity-50" :disabled="firewallLoading" @click="loadFirewall">
          {{ firewallLoading ? '正在读取…' : '重新读取' }}
        </button>
      </div>
      <p class="mt-2 text-xs text-panel-muted">只执行 ufw status。不会添加、删除或修改任何规则。</p>
      <p v-if="firewallMessage" class="mt-2 text-sm text-[#c24141]">{{ firewallMessage }}</p>
      <p v-else-if="firewall" class="mt-3 text-sm">状态：{{ firewall.status }}</p>
      <table v-if="firewall?.rules?.length" class="mt-3 w-full text-left text-sm">
        <thead class="text-xs text-panel-muted"><tr><th class="py-2">到</th><th>动作</th><th>方向</th><th>来自</th></tr></thead>
        <tbody>
          <tr v-for="(rule, index) in firewall.rules" :key="index" class="border-t border-panel-line">
            <td class="py-2">{{ rule.to }}</td>
            <td>{{ rule.action }}</td>
            <td>{{ rule.direction }}</td>
            <td>{{ rule.from }}</td>
          </tr>
        </tbody>
      </table>
      <pre v-if="firewall?.text" class="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[#f7f8fa] p-3 text-xs">{{ firewall.text }}</pre>
    </div>
  </div>
</template>
