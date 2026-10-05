<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import {
  backupLogs,
  backupOptions,
  deleteBackup,
  listBackups,
  runBackup,
  saveBackup,
  toggleBackup,
} from '../api/backup'
import { crontabLogs, deleteCrontab, listCrontab, runCrontab, saveCrontab, toggleCrontab } from '../api/crontab'
import { isCanceled } from '../api/http'

const items = ref([])
const paths = ref({ site: '', database: '' })
const sites = ref([])
const databases = ref([])
const message = ref('')
const busy = ref(false)
const adding = ref(false)
const pending = ref(null)
const phrase = ref('')
const logTask = ref(null)
const logLines = ref([])
const logFiles = ref([])
let logTimer = 0

const legacy = ref([])
const legacyLogs = ref([])
const legacyPending = ref(null)
const legacyPhrase = ref('')
const legacyForm = ref({
  name: '',
  type: 'day',
  where1: '1',
  hour: '3',
  minute: '30',
  sType: 'toUrl',
  sBody: '',
  sName: '',
  enabled: true,
})

const form = ref({
  name: '',
  kind: 'site',
  engine: '',
  target: '',
  type: 'day',
  where1: '1',
  hour: '3',
  minute: '30',
  cron: '30 3 * * *',
  custom: false,
  keep: 7,
  enabled: true,
})

const weeks = [
  ['0', '周日'],
  ['1', '周一'],
  ['2', '周二'],
  ['3', '周三'],
  ['4', '周四'],
  ['5', '周五'],
  ['6', '周六'],
]

function clamp(value, low, high, fallback) {
  const number = Number.parseInt(value, 10)
  if (Number.isNaN(number)) return fallback
  return Math.min(high, Math.max(low, number))
}

function buildCron() {
  const minute = clamp(form.value.minute, 0, 59, 0)
  const hour = clamp(form.value.hour, 0, 23, 3)
  if (form.value.type === 'minute') return `*/${clamp(form.value.where1, 1, 59, 1)} * * * *`
  if (form.value.type === 'hour') return `${minute} * * * *`
  if (form.value.type === 'week') return `${minute} ${hour} * * ${clamp(form.value.where1, 0, 6, 1)}`
  if (form.value.type === 'month') return `${minute} ${hour} ${clamp(form.value.where1, 1, 28, 1)} * *`
  return `${minute} ${hour} * * *`
}

watch(
  () => [form.value.type, form.value.where1, form.value.hour, form.value.minute, form.value.custom],
  () => {
    if (!form.value.custom) form.value.cron = buildCron()
  },
)

function fail(error) {
  if (isCanceled(error)) return
  const detail = error?.response?.data?.detail
  message.value = typeof detail === 'string' ? detail : '操作失败，请稍后重试'
}

function sizeText(bytes) {
  const size = Number(bytes) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function lastText(item) {
  if (!item.last_run) return '尚未执行'
  const word = item.last_status === 'ok' ? '成功' : item.last_status === 'error' ? '失败' : ''
  return word ? `${item.last_run} · ${word}` : item.last_run
}

async function load() {
  const { data } = await listBackups()
  items.value = data.items || []
  paths.value = data.paths || paths.value
}

async function loadLegacy() {
  try {
    const { data } = await listCrontab()
    legacy.value = data.items || []
  } catch (error) {
    if (!isCanceled(error)) legacy.value = []
  }
}

async function run(action) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    await action()
    await load()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function openAdd() {
  message.value = ''
  adding.value = true
  form.value.target = ''
  form.value.engine = ''
  try {
    const { data } = await backupOptions()
    sites.value = data.sites || []
    databases.value = data.databases || []
    if (data.paths) paths.value = data.paths
  } catch (error) {
    fail(error)
    adding.value = false
  }
}

async function submit() {
  const payload = {
    name: form.value.name.trim(),
    kind: form.value.kind,
    engine: form.value.kind === 'database' ? form.value.engine : '',
    target: form.value.target,
    cron: form.value.custom ? form.value.cron.trim() : buildCron(),
    keep: Number(form.value.keep),
    enabled: form.value.enabled,
  }
  await run(() => saveBackup(payload))
  if (!message.value) {
    message.value = '备份任务已保存'
    adding.value = false
    form.value.name = ''
  }
}

function askDelete(item) {
  phrase.value = ''
  message.value = ''
  pending.value = item
}

async function confirmDelete() {
  if (phrase.value.trim() !== '确认') {
    message.value = '请输入确认'
    return
  }
  const id = pending.value?.id
  if (!id) return
  await run(() => deleteBackup(id, '确认'))
  if (!message.value) pending.value = null
}

async function pullLogs() {
  if (!logTask.value) return
  try {
    const { data } = await backupLogs(logTask.value.id)
    logLines.value = data.lines || []
    logFiles.value = data.files || []
    await load()
  } catch (error) {
    if (!isCanceled(error)) fail(error)
  }
}

async function showLogs(item) {
  logTask.value = item
  logLines.value = []
  logFiles.value = []
  await pullLogs()
  window.clearInterval(logTimer)
  logTimer = window.setInterval(pullLogs, 2000)
}

function closeLogs() {
  window.clearInterval(logTimer)
  logTimer = 0
  logTask.value = null
}

async function runOnce(item) {
  await run(() => runBackup(item.id))
  if (!message.value) {
    message.value = '已提交执行，备份在独立进程里进行'
    await showLogs(item)
  }
}

function onDatabase(event) {
  const value = event.target.value
  const index = value.indexOf('\t')
  form.value.engine = index >= 0 ? value.slice(0, index) : ''
  form.value.target = index >= 0 ? value.slice(index + 1) : ''
}

async function submitLegacy() {
  busy.value = true
  message.value = ''
  try {
    await saveCrontab({ ...legacyForm.value })
    legacyForm.value.sBody = ''
    message.value = '原有计划任务已写入 crontab'
    await loadLegacy()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function runLegacy(id) {
  busy.value = true
  message.value = ''
  try {
    await runCrontab(id)
    const { data } = await crontabLogs(id)
    legacyLogs.value = data.lines || []
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function confirmLegacy() {
  if (legacyPhrase.value.trim() !== '确认') {
    message.value = '请输入确认'
    return
  }
  const id = legacyPending.value?.id
  if (!id || busy.value) return
  busy.value = true
  try {
    await deleteCrontab(id, '确认')
    legacyPending.value = null
    await loadLegacy()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    await load()
  } catch (error) {
    fail(error)
  }
  await loadLegacy()
})

onUnmounted(() => window.clearInterval(logTimer))
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-4 p-4">
    <div class="flex items-center justify-between gap-3">
      <p class="text-sm text-panel-muted">
        网站备份到 {{ paths.site || '/www/backup/site' }}，数据库备份到 {{ paths.database || '/www/backup/database' }}。时间按北京时间。
      </p>
      <button type="button" class="btn shrink-0" :disabled="busy" @click="openAdd">添加任务</button>
    </div>
    <p v-if="message" class="text-sm" :class="message.includes('失败') || message.includes('确认') || message.includes('没有') || message.includes('无效') || message.includes('请') ? 'text-[#c24141]' : 'text-panel-green'">{{ message }}</p>

    <div class="min-h-0 flex-1 overflow-auto rounded border border-panel-line bg-white">
      <table class="w-full min-w-[860px] text-left text-sm">
        <thead class="text-xs text-panel-muted">
          <tr>
            <th class="px-3 py-2">任务名称</th>
            <th>任务类型</th>
            <th>具体目标</th>
            <th>执行周期</th>
            <th>状态</th>
            <th>最后执行时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id" class="border-t border-panel-line">
            <td class="px-3 py-2">{{ item.name }}</td>
            <td>{{ item.kind === 'site' ? '网站备份' : '数据库备份' }}</td>
            <td class="max-w-[220px] truncate" :title="item.target_label">{{ item.target_label }}</td>
            <td :title="item.cron">{{ item.cycle }}<span class="ml-1 text-xs text-panel-muted">{{ item.cron }}</span></td>
            <td>{{ item.enabled ? '已启用' : '已禁用' }}</td>
            <td>{{ lastText(item) }}</td>
            <td class="space-x-2 whitespace-nowrap px-3 py-2 text-xs">
              <button type="button" class="text-panel-green disabled:opacity-40" :disabled="busy" @click="runOnce(item)">立即执行</button>
              <button type="button" class="text-panel-green disabled:opacity-40" :disabled="busy" @click="showLogs(item)">日志</button>
              <button type="button" class="text-panel-green disabled:opacity-40" :disabled="busy" @click="run(() => toggleBackup(item.id, !item.enabled))">{{ item.enabled ? '禁用' : '启用' }}</button>
              <button type="button" class="text-[#c24141] disabled:opacity-40" :disabled="busy" @click="askDelete(item)">删除</button>
            </td>
          </tr>
          <tr v-if="!items.length"><td class="px-3 py-6 text-panel-muted" colspan="7">还没有备份任务</td></tr>
        </tbody>
      </table>
    </div>

    <details class="rounded border border-panel-line bg-white p-3 text-sm">
      <summary class="cursor-pointer text-panel-muted">访问 URL 与脚本任务（仍写入系统 crontab）</summary>
      <form class="mt-3 grid gap-2 md:grid-cols-4" @submit.prevent="submitLegacy">
        <input v-model="legacyForm.name" class="box" placeholder="任务名称" required />
        <select v-model="legacyForm.sType" class="box">
          <option value="toUrl">访问 URL</option>
          <option value="toShell">脚本</option>
        </select>
        <input v-model="legacyForm.sBody" class="box" placeholder="URL 或脚本" />
        <button class="btn" type="submit" :disabled="busy">添加</button>
      </form>
      <table class="mt-3 w-full text-left text-sm">
        <tbody>
          <tr v-for="item in legacy" :key="item.id" class="border-t border-panel-line">
            <td class="py-2">{{ item.name }}</td>
            <td>{{ item.cron }}</td>
            <td>{{ item.sType }}</td>
            <td class="space-x-2 text-xs">
              <button type="button" class="text-panel-green disabled:opacity-40" :disabled="busy" @click="runLegacy(item.id)">执行</button>
              <button type="button" class="text-panel-green disabled:opacity-40" :disabled="busy" @click="toggleCrontab(item.id, !item.enabled).then(loadLegacy)">{{ item.enabled ? '暂停' : '启用' }}</button>
              <button type="button" class="text-[#c24141] disabled:opacity-40" :disabled="busy" @click="legacyPhrase = ''; legacyPending = item">删除</button>
            </td>
          </tr>
          <tr v-if="!legacy.length"><td class="py-3 text-panel-muted">没有这类任务</td></tr>
        </tbody>
      </table>
      <pre v-if="legacyLogs.length" class="mt-2 max-h-32 overflow-auto text-xs">{{ legacyLogs.join('\n') }}</pre>
    </details>

    <div v-if="adding" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <form class="max-h-[90vh] w-full max-w-lg overflow-auto rounded bg-white p-5 shadow-xl" @submit.prevent="submit">
        <h2 class="text-base font-medium">添加备份任务</h2>
        <div class="mt-3 grid gap-3">
          <input v-model="form.name" class="box" maxlength="40" placeholder="任务名称" required />
          <select v-model="form.kind" class="box" @change="form.target = ''; form.engine = ''">
            <option value="site">网站备份</option>
            <option value="database">数据库备份</option>
          </select>
          <select v-if="form.kind === 'site'" v-model="form.target" class="box" required>
            <option value="" disabled>选择网站</option>
            <option v-for="site in sites" :key="site.domain" :value="site.domain">{{ site.domain }} · {{ site.root }}</option>
          </select>
          <select v-else class="box" required @change="onDatabase">
            <option value="" selected disabled>选择数据库</option>
            <option v-for="item in databases" :key="item.engine + item.name" :value="item.engine + '\t' + item.name">{{ item.label }}</option>
          </select>
          <p v-if="form.kind === 'database' && !databases.length" class="text-xs text-panel-muted">当前没有可备份的数据库。MySQL、MongoDB、Redis 未安装时不能选。</p>
          <div class="grid grid-cols-2 gap-2">
            <select v-model="form.type" class="box" :disabled="form.custom">
              <option value="minute">每 N 分钟</option>
              <option value="hour">每小时</option>
              <option value="day">每天</option>
              <option value="week">每周</option>
              <option value="month">每月</option>
            </select>
            <input v-if="form.type === 'minute'" v-model="form.where1" class="box" :disabled="form.custom" placeholder="间隔分钟" />
            <select v-else-if="form.type === 'week'" v-model="form.where1" class="box" :disabled="form.custom">
              <option v-for="[value, label] in weeks" :key="value" :value="value">{{ label }}</option>
            </select>
            <input v-else-if="form.type === 'month'" v-model="form.where1" class="box" :disabled="form.custom" placeholder="日期 1-28" />
            <input v-else v-model="form.minute" class="box" :disabled="form.custom" placeholder="分钟" />
          </div>
          <div v-if="form.type !== 'minute' && form.type !== 'hour'" class="grid grid-cols-2 gap-2">
            <input v-model="form.hour" class="box" :disabled="form.custom" placeholder="时" />
            <input v-model="form.minute" class="box" :disabled="form.custom" placeholder="分" />
          </div>
          <label class="flex items-center gap-2 text-sm text-panel-muted">
            <input v-model="form.custom" type="checkbox" />
            直接填写 Cron（5 段：分 时 日 月 周）
          </label>
          <input v-model="form.cron" class="box" :disabled="!form.custom" placeholder="30 3 * * *" />
          <label class="text-sm text-panel-muted">
            保留最新
            <input v-model.number="form.keep" class="box mt-1" type="number" min="1" max="30" required />
            份，更早的自动删除
          </label>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="btn ghost" :disabled="busy" @click="adding = false">取消</button>
          <button type="submit" class="btn" :disabled="busy || (form.kind === 'database' && !databases.length)">{{ busy ? '正在保存…' : '保存' }}</button>
        </div>
      </form>
    </div>

    <div v-if="logTask" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <div class="flex max-h-[90vh] w-full max-w-2xl flex-col rounded bg-white p-5 shadow-xl">
        <h2 class="text-base font-medium">{{ logTask.name }} 的执行日志</h2>
        <pre class="mt-3 min-h-40 flex-1 overflow-auto whitespace-pre-wrap break-all rounded bg-[#f7f8fa] p-3 text-xs">{{ logLines.length ? logLines.join('\n') : '还没有执行记录' }}</pre>
        <ul v-if="logFiles.length" class="mt-3 max-h-28 overflow-auto text-xs text-panel-muted">
          <li v-for="file in logFiles" :key="file.name">{{ file.mtime }} · {{ file.name }} · {{ sizeText(file.size) }}</li>
        </ul>
        <div class="mt-4 flex justify-end">
          <button type="button" class="btn ghost" @click="closeLogs">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="pending" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="confirmDelete">
        <h2 class="text-base font-medium">删除备份任务 {{ pending.name }}</h2>
        <p class="mt-2 text-sm text-panel-muted">任务会停止调度。已经生成的备份文件会保留。请输入“确认”后继续。</p>
        <input v-model="phrase" class="box mt-3" placeholder="请输入确认" />
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="btn ghost" :disabled="busy" @click="pending = null">取消</button>
          <button type="submit" class="btn danger" :disabled="busy">{{ busy ? '正在删除…' : '确认删除' }}</button>
        </div>
      </form>
    </div>

    <div v-if="legacyPending" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="confirmLegacy">
        <h2 class="text-base font-medium">删除计划任务 {{ legacyPending.name }}</h2>
        <input v-model="legacyPhrase" class="box mt-3" placeholder="请输入确认" />
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="btn ghost" @click="legacyPending = null">取消</button>
          <button type="submit" class="btn danger">确认删除</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.box { width: 100%; border: 1px solid #e7e9ed; border-radius: 4px; padding: 6px 8px; }
.btn { border-radius: 4px; background: #20a53a; color: white; padding: 6px 12px; }
.ghost { background: #fff; color: #2b2f36; border: 1px solid #e7e9ed; }
.danger { background: #c24141; }
</style>
