<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import {
  clearLogs,
  exportLogs,
  logAudit,
  logError,
  logLogins,
  logOperations,
  logRuntime,
  logSiteFiles,
  logSites,
  logSoftware,
  logSsh,
  logTasks,
} from '../api/logs'

const groups = [
  { id: 'panel', label: '面板日志' },
  { id: 'site', label: '网站日志' },
  { id: 'audit', label: '日志审计' },
  { id: 'ssh', label: 'SSH登录日志' },
  { id: 'software', label: '软件日志' },
]
const panelTabs = [
  { id: 'operation', label: '操作日志' },
  { id: 'login', label: '登录日志' },
  { id: 'runtime', label: '运行日志' },
  { id: 'task', label: '计划任务日志' },
]
const typeOptions = ['全部', '文件管理', '网站管理', '数据库', '日志管理']
const group = ref('panel')
const panelTab = ref('operation')
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const logType = ref('全部')
const rows = ref([])
const lines = ref([])
const total = ref(0)
const notice = ref('')
const message = ref('')
const loading = ref(false)
const siteFile = ref('access.log')
const siteFiles = ref(['access.log', 'error.log'])
const software = ref('nginx')
const follow = ref(false)
const clearOpen = ref(false)
const clearPassword = ref('')
const clearConfirm = ref('')
const auth = useAuthStore()
let socket = null
let searchTimer = 0
let loadSeq = 0
let logAbort = null

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const tableMode = computed(() => group.value === 'audit' || group.value === 'ssh' || (group.value === 'panel' && ['operation', 'login'].includes(panelTab.value)))
const canClear = computed(() => group.value === 'panel' && ['operation', 'login'].includes(panelTab.value))
const canExport = computed(() => ['operation', 'login'].includes(panelTab.value) && group.value === 'panel' || group.value === 'audit')
const streamSource = computed(() => {
  if (group.value === 'panel' && panelTab.value === 'runtime') return 'runtime'
  if (group.value === 'site' && siteFile.value === 'access.log') return 'nginx-access'
  if (group.value === 'site' && siteFile.value === 'error.log') return 'nginx-error'
  return ''
})

function params() {
  return { page: page.value, page_size: pageSize.value, q: keyword.value.trim(), log_type: logType.value }
}

async function load() {
  logAbort?.abort()
  const controller = new AbortController()
  logAbort = controller
  const signal = controller.signal
  const seq = ++loadSeq
  loading.value = true
  notice.value = ''
  try {
    if (group.value === 'panel' && panelTab.value === 'operation') {
      const { data } = await logOperations(params(), signal)
      if (signal.aborted || seq !== loadSeq) return
      setTable(data)
    } else if (group.value === 'panel' && panelTab.value === 'login') {
      const { data } = await logLogins(params(), signal)
      if (signal.aborted || seq !== loadSeq) return
      setTable(data)
    } else if (group.value === 'panel' && panelTab.value === 'runtime') {
      const { data } = await logRuntime({ limit: 200, q: keyword.value.trim() }, signal)
      if (signal.aborted || seq !== loadSeq) return
      setLines(data)
    } else if (group.value === 'panel' && panelTab.value === 'task') {
      const { data } = await logTasks({ limit: 200, q: keyword.value.trim() }, signal)
      if (signal.aborted || seq !== loadSeq) return
      setLines(data)
    } else if (group.value === 'site') {
      const files = await logSiteFiles(signal)
      if (signal.aborted || seq !== loadSeq) return
      siteFiles.value = files.data.items?.length ? files.data.items : ['access.log', 'error.log']
      if (!siteFiles.value.includes(siteFile.value)) siteFile.value = siteFiles.value[0]
      const { data } = await logSites({ file: siteFile.value, limit: 200, q: keyword.value.trim() }, signal)
      if (signal.aborted || seq !== loadSeq) return
      setLines(data)
    } else if (group.value === 'audit') {
      const { data } = await logAudit(params(), signal)
      if (signal.aborted || seq !== loadSeq) return
      setTable(data)
    } else if (group.value === 'ssh') {
      const { data } = await logSsh(params(), signal)
      if (signal.aborted || seq !== loadSeq) return
      setTable(data)
      message.value = data.message || ''
    } else {
      const { data } = await logSoftware({ name: software.value, limit: 200, q: keyword.value.trim() }, signal)
      if (signal.aborted || seq !== loadSeq) return
      setLines(data)
    }
  } catch (error) {
    if (signal.aborted || seq !== loadSeq || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED') return
    const text = logError(error)
    if (text) notice.value = text
  } finally {
    if (logAbort === controller) loading.value = false
  }
}

function setTable(data) {
  rows.value = data.items || []
  lines.value = []
  total.value = data.total || 0
  message.value = data.message || ''
}

function setLines(data) {
  rows.value = []
  lines.value = data.items || []
  total.value = data.total || lines.value.length
  message.value = data.message || (lines.value.length ? '只显示文件末尾的最近内容' : '这段日志是空的')
}

function chooseGroup(id) {
  group.value = id
  page.value = 1
  follow.value = false
  stopFollow()
  load()
}

function choosePanel(id) {
  panelTab.value = id
  page.value = 1
  follow.value = false
  stopFollow()
  load()
}

async function download() {
  const section = group.value === 'audit' ? 'audit' : panelTab.value === 'login' ? 'login' : 'operation'
  try {
    const response = await exportLogs({ section, form: 'csv', q: keyword.value.trim(), log_type: logType.value })
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = 'panel-logs.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    notice.value = logError(error)
  }
}

async function submitClear() {
  notice.value = ''
  try {
    await clearLogs({
      section: panelTab.value === 'login' ? 'login' : 'operation',
      password: clearPassword.value,
      confirm: clearConfirm.value.trim(),
    })
    clearOpen.value = false
    clearPassword.value = ''
    clearConfirm.value = ''
    page.value = 1
    await load()
  } catch (error) {
    notice.value = logError(error)
  }
}

function stopFollow() {
  if (socket) {
    socket.close()
    socket = null
  }
}

function toggleFollow() {
  follow.value = !follow.value
  if (!follow.value) {
    stopFollow()
    return
  }
  const token = auth.accessToken
  const source = streamSource.value
  if (!token || !source) {
    follow.value = false
    notice.value = '这个日志不支持实时滚动'
    return
  }
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}/ws/logs`)
  socket.onopen = () => socket?.send(JSON.stringify({ token, source }))
  socket.onmessage = (event) => {
    const data = JSON.parse(event.data)
    if (data.type === 'lines') lines.value = data.lines || []
    if (data.type === 'append') lines.value = [...lines.value, ...(data.lines || [])].slice(-500)
  }
  socket.onerror = () => {
    notice.value = '实时日志连接失败'
    follow.value = false
  }
}

watch(keyword, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    load()
  }, 400)
})

onMounted(load)
onUnmounted(() => {
  logAbort?.abort()
  stopFollow()
  window.clearTimeout(searchTimer)
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col rounded border border-panel-line bg-white">
    <div class="flex gap-6 border-b border-panel-line px-4 pt-3 text-sm">
      <button v-for="item in groups" :key="item.id" type="button" class="border-b-2 pb-2" :class="group === item.id ? 'border-panel-green text-panel-green' : 'border-transparent text-panel-muted'" @click="chooseGroup(item.id)">
        {{ item.label }}
      </button>
    </div>
    <div v-if="group === 'panel'" class="flex gap-5 border-b border-panel-line px-4 pt-3 text-sm">
      <button v-for="item in panelTabs" :key="item.id" type="button" class="border-b-2 pb-2" :class="panelTab === item.id ? 'border-panel-green text-panel-green' : 'border-transparent text-panel-muted'" @click="choosePanel(item.id)">
        {{ item.label }}
      </button>
    </div>

    <div class="flex flex-wrap items-center gap-2 border-b border-panel-line px-4 py-3">
      <button type="button" class="tool" :disabled="loading" @click="load">刷新日志</button>
      <select v-if="group === 'panel' && panelTab === 'operation'" v-model="logType" class="tool" :disabled="loading" @change="page = 1; load()">
        <option v-for="item in typeOptions" :key="item">{{ item }}</option>
      </select>
      <select v-if="group === 'site'" v-model="siteFile" class="tool" :disabled="loading" @change="follow = false; stopFollow(); load()">
        <option v-for="item in siteFiles" :key="item">{{ item }}</option>
      </select>
      <select v-if="group === 'software'" v-model="software" class="tool" :disabled="loading" @change="load()">
        <option value="nginx">Nginx 错误日志</option>
        <option value="panel">面板运行日志</option>
      </select>
      <input v-model="keyword" class="field ml-auto max-w-xs" placeholder="搜索关键字" />
      <button v-if="canExport" type="button" class="tool" :disabled="loading" @click="download">导出日志</button>
      <button v-if="canClear" type="button" class="tool" :disabled="loading" @click="clearOpen = true">清空日志</button>
      <button v-if="streamSource" type="button" class="tool" :disabled="loading" @click="toggleFollow">{{ follow ? '停止滚动' : '实时滚动' }}</button>
      <span v-if="loading" class="text-xs text-panel-muted">正在读取</span>
    </div>

    <div class="min-h-0 flex-1 overflow-auto">
      <table v-if="tableMode" class="w-full text-left text-sm">
        <thead class="bg-[#f7f8fa] text-xs text-panel-muted">
          <tr>
            <th class="px-3 py-2 font-medium">用户</th>
            <th class="px-3 py-2 font-medium">操作类型</th>
            <th class="px-3 py-2 font-medium">详情</th>
            <th class="px-3 py-2 font-medium">IP</th>
            <th class="px-3 py-2 font-medium">操作时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td colspan="5" class="px-4 py-10 text-center text-panel-muted">还没有日志</td>
          </tr>
          <tr v-for="row in rows" :key="row.id || row.created_at + row.details" class="border-t border-panel-line">
            <td class="px-3 py-3">{{ row.operator }}</td>
            <td class="px-3 py-3">{{ row.type }}</td>
            <td class="px-3 py-3">{{ row.details }}</td>
            <td class="px-3 py-3">{{ row.ip }}</td>
            <td class="px-3 py-3 text-panel-muted">{{ row.created_at }}</td>
          </tr>
        </tbody>
      </table>
      <pre v-else class="min-h-full whitespace-pre-wrap break-all px-4 py-3 font-mono text-xs leading-5 text-[#333]">{{ lines.join('\n') || '还没有日志' }}</pre>
    </div>

    <div class="flex items-center justify-end gap-3 border-t border-panel-line px-4 py-2 text-xs text-panel-muted">
      <span v-if="message">{{ message }}</span>
      <span>共 {{ total }} 条</span>
      <button type="button" class="tool" :disabled="loading || page <= 1 || !tableMode" @click="page -= 1; load()">上一页</button>
      <span>{{ page }} / {{ pageCount }}</span>
      <button type="button" class="tool" :disabled="loading || page >= pageCount || !tableMode" @click="page += 1; load()">下一页</button>
      <select v-model.number="pageSize" class="tool" :disabled="loading" @change="page = 1; load()">
        <option :value="10">10条/页</option>
        <option :value="20">20条/页</option>
      </select>
    </div>
    <p v-if="notice && !clearOpen" class="px-4 py-2 text-sm text-[#c24141]">{{ notice }}</p>

    <div v-if="clearOpen" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="submitClear">
        <h2 class="text-base font-medium">清空日志</h2>
        <p class="mt-2 text-sm text-panel-muted">只有当前管理员可以清空面板里的操作或登录记录。系统上的 SSH 和网站日志文件不会被删除。</p>
        <input v-model="clearPassword" type="password" class="field mt-3" placeholder="管理员密码" />
        <input v-model="clearConfirm" class="field mt-2" placeholder="请输入：清空日志" />
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="clearOpen = false">取消</button>
          <button type="submit" class="rounded bg-[#c24141] px-3 py-1 text-sm text-white">确认清空</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.field { width: 100%; border: 1px solid #e7e9ed; border-radius: 4px; padding: 6px 10px; font-size: 13px; outline: none; }
.field:focus { border-color: #20a53a; }
.tool { border: 1px solid #e7e9ed; border-radius: 4px; background: white; padding: 4px 10px; font-size: 13px; }
.tool:disabled { opacity: 0.45; }
</style>
