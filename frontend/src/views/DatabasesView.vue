<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  backupDatabase,
  backupJob,
  changeDatabasePassword,
  changeRootPassword,
  createDatabase,
  databaseError,
  databaseStatus,
  deleteDatabase,
  downloadBackup,
  installEngine,
  installProgress,
  rootPasswordOk,
  showRootPassword,
  listBackups,
  listDatabases,
  mongoCreate,
  mongoDelete,
  mongoList,
  postgresCreate,
  postgresDelete,
  postgresDeleteRole,
  postgresList,
  postgresPassword,
  redisFlush,
  redisInfo,
  redisPassword,
  restoreDatabase,
  saveRemote,
  sqlserverBackup,
  sqlserverBackups,
  sqlserverCreate,
  sqlserverDelete,
  sqlserverList,
  sqlserverRestore,
  sqliteFiles,
  sqliteRows,
  sqliteTables,
  startEngine,
  stopEngine,
} from '../api/databases'
import DatabaseAccounts from '../components/DatabaseAccounts.vue'

const tabs = [
  { id: 'mysql', label: 'MySQL' },
  { id: 'sqlserver', label: 'SQLServer' },
  { id: 'mongodb', label: 'MongoDB' },
  { id: 'redis', label: 'Redis' },
  { id: 'pgsql', label: 'PgSQL' },
  { id: 'sqlite', label: 'SQLite' },
]
const engine = ref('mysql')
const current = ref(null)
const notice = ref('')
const toast = ref('')
const secret = ref(null)
const installing = ref(false)
const customPassword = ref('')
const savedRoot = ref('')
const dialog = ref('')
const rootRule = '至少 8 个字符，包含大写、小写、数字和特殊字符，不能包含引号或分号。'
const form = ref(emptyForm())
const panel = ref({})
let timer = 0

const label = computed(() => tabs.find((item) => item.id === engine.value)?.label || '')
const actionBusy = ref(false)
const ready = computed(() => current.value?.state === 'running' || current.value?.state === 'local')
const busy = computed(() => installing.value || actionBusy.value || ['pulling', 'starting'].includes(current.value?.state))
const danger = ref(null)
const dangerPhrase = ref('')
const deleteName = ref('')
const deletePhrase = ref('')
const restoreName = ref('')
const restoreFiles = ref([])
let panelSeq = 0

function emptyForm() {
  return { name: '', user: '', password: '', access: 'local', ip: '', host: '', port: '3306', file: '', table: '', note: '' }
}

function showToast(message) {
  toast.value = message
  setTimeout(() => {
    if (toast.value === message) toast.value = ''
  }, 4000)
}

function fail(error) {
  const text = databaseError(error)
  if (text) notice.value = text
}

function askDanger(title, action) {
  dangerPhrase.value = ''
  notice.value = ''
  danger.value = { title, action }
}

async function submitDanger() {
  if (dangerPhrase.value.trim() !== '确认') {
    notice.value = '请输入确认'
    return
  }
  if (actionBusy.value) return
  actionBusy.value = true
  const action = danger.value?.action
  try {
    await action()
    danger.value = null
  } catch (error) {
    fail(error)
  } finally {
    actionBusy.value = false
  }
}

function stopPoll() {
  if (timer) window.clearInterval(timer)
  timer = 0
}

async function loadPanel(mine = panelSeq) {
  notice.value = ''
  try {
    let data = null
    if (engine.value === 'mysql') data = (await listDatabases(1)).data
    else if (engine.value === 'sqlserver') data = (await sqlserverList()).data
    else if (engine.value === 'mongodb') data = (await mongoList()).data
    else if (engine.value === 'redis') data = (await redisInfo()).data
    else if (engine.value === 'pgsql') data = (await postgresList()).data
    else if (engine.value === 'sqlite') data = (await sqliteFiles()).data
    if (mine !== panelSeq || !data) return
    panel.value = data
  } catch (error) {
    if (mine !== panelSeq) return
    fail(error)
  }
}

async function load() {
  const mine = ++panelSeq
  notice.value = ''
  try {
    const { data } = await databaseStatus(engine.value)
    if (mine !== panelSeq) return
    current.value = data
    if (data.state === 'pulling' || data.state === 'starting') {
      installing.value = true
      poll()
      return
    }
    installing.value = false
    stopPoll()
    if (data.state === 'running' || data.state === 'local') await loadPanel(mine)
  } catch (error) {
    if (mine !== panelSeq) return
    fail(error)
  }
}

function poll() {
  if (timer) return
  timer = window.setInterval(async () => {
    try {
      const { data } = await installProgress(engine.value)
      if (current.value) current.value = { ...current.value, state: data.state === 'ready' ? current.value.state : data.state, message: data.message }
      if (data.has_password) {
        showToast('数据库密码已保存在服务器，面板不再回显')
      }
      if (data.state === 'ready' || data.state === 'failed' || data.state === 'idle') {
        stopPoll()
        installing.value = false
        await load()
      }
    } catch (error) {
      fail(error)
    }
  }, 2000)
}

function openInstall() {
  notice.value = ''
  customPassword.value = ''
  dialog.value = 'install'
}

async function install() {
  const password = customPassword.value
  if (!rootPasswordOk(password)) {
    notice.value = '密码不符合要求'
    return
  }
  installing.value = true
  notice.value = ''
  dialog.value = ''
  current.value = { ...(current.value || {}), state: 'pulling', message: '正在拉取镜像...' }
  try {
    await installEngine(engine.value, password)
    poll()
  } catch (error) {
    installing.value = false
    fail(error)
    await load()
  }
}

async function start() {
  if (actionBusy.value) return
  actionBusy.value = true
  try {
    await startEngine(engine.value)
    showToast('已启动')
    await load()
  } catch (error) {
    fail(error)
  } finally {
    actionBusy.value = false
  }
}

async function stop() {
  if (!window.confirm(`停止 ${label.value} 容器？数据仍保留在磁盘上。`)) return
  try {
    await stopEngine(engine.value)
    showToast('已停止')
    await load()
  } catch (error) {
    fail(error)
  }
}

function choose(id) {
  if (id === engine.value) return
  stopPoll()
  engine.value = id
  current.value = null
  panel.value = {}
  dialog.value = ''
  customPassword.value = ''
  load()
}

function openDialog(kind, row) {
  notice.value = ''
  form.value = emptyForm()
  const dbName = row?.db_name || row?.name || ''
  if (dbName) form.value.name = dbName
  if (row?.file) form.value.file = row.file
  dialog.value = kind
  if (kind === 'mysql-root') loadSavedRoot()
}

async function loadSavedRoot() {
  savedRoot.value = ''
  try {
    const { data } = await showRootPassword()
    savedRoot.value = data?.has_password ? data.password || '' : ''
  } catch (error) {
    notice.value = databaseError(error)
  }
}

async function changeSavedRoot() {
  if (!rootPasswordOk(form.value.password)) return
  notice.value = ''
  try {
    await changeRootPassword(form.value.password)
    dialog.value = ''
    showToast('已保存')
  } catch (error) {
    notice.value = databaseError(error)
  }
}

async function submit() {
  notice.value = ''
  if (dialog.value === 'mysql-password' && !rootPasswordOk(form.value.password)) {
    notice.value = '密码不符合要求'
    return
  }
  try {
    if (dialog.value === 'mysql-create') await createDatabase(form.value)
    if (dialog.value === 'mysql-password') await changeDatabasePassword(form.value.name, form.value.password)
    if (dialog.value === 'remote') {
      await saveRemote({ host: form.value.host, port: String(form.value.port ?? ''), user: form.value.user, password: form.value.password })
    }
    if (dialog.value === 'sql-create') await sqlserverCreate(form.value.name)
    if (dialog.value === 'mongo-create') await mongoCreate(form.value)
    if (dialog.value === 'pg-create') await postgresCreate(form.value)
    if (dialog.value === 'pg-password') await postgresPassword(form.value.user || 'panel', form.value.password)
    if (dialog.value === 'redis-password') await redisPassword(form.value.password)
    dialog.value = ''
    showToast('已保存')
    await loadPanel()
  } catch (error) {
    notice.value = databaseError(error)
  }
}

function openMysqlDelete(row) {
  notice.value = ''
  deleteName.value = row?.db_name || ''
  deletePhrase.value = ''
  dialog.value = 'mysql-delete'
}

async function submitMysqlDelete() {
  if (deletePhrase.value !== deleteName.value || actionBusy.value) return
  actionBusy.value = true
  try {
    await deleteDatabase(deleteName.value, deletePhrase.value)
    dialog.value = ''
    showToast('已删除')
    await loadPanel()
  } catch (error) {
    fail(error)
  } finally {
    actionBusy.value = false
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function waitJob(jobId) {
  for (let i = 0; i < 150; i += 1) {
    const { data } = await backupJob(jobId)
    if (data?.ready && data.file) return data.file
    if (data?.status === 'failed') {
      notice.value = '备份或恢复失败'
      throw new Error('failed')
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  notice.value = '操作超时，请重试'
  throw new Error('timeout')
}

async function backupMysql(row) {
  const name = row?.db_name || ''
  if (!name || actionBusy.value) return
  actionBusy.value = true
  notice.value = ''
  try {
    const { data } = await backupDatabase(name)
    const file = await waitJob(data.job_id)
    const response = await downloadBackup(name, file)
    saveBlob(response.data, file)
    showToast('备份已完成')
    await loadPanel()
  } catch (error) {
    if (!notice.value) fail(error)
  } finally {
    actionBusy.value = false
  }
}

async function openRestore(row) {
  notice.value = ''
  restoreName.value = row?.db_name || ''
  restoreFiles.value = []
  dialog.value = 'mysql-restore'
  try {
    const { data } = await listBackups(restoreName.value)
    restoreFiles.value = data.items || []
  } catch (error) {
    fail(error)
  }
}

async function runRestore(file) {
  if (!restoreName.value || actionBusy.value) return
  actionBusy.value = true
  const name = restoreName.value
  try {
    const { data } = await restoreDatabase(name, file)
    dialog.value = ''
    await waitJob(data.job_id)
    showToast('已还原')
    await loadPanel()
  } catch (error) {
    if (!notice.value) fail(error)
  } finally {
    actionBusy.value = false
  }
}

async function backupSql(name) {
  try {
    await sqlserverBackup(name)
    showToast('备份已完成')
  } catch (error) {
    fail(error)
  }
}

async function restoreSql(name) {
  const { data } = await sqlserverBackups(name)
  const file = (data.items || [])[0]?.file
  if (!file) {
    showToast('还没有备份')
    return
  }
  if (!window.confirm(`用最新备份 ${file} 还原 ${name}？`)) return
  try {
    await sqlserverRestore(name, file)
    showToast('已还原')
  } catch (error) {
    fail(error)
  }
}

function removeSql(name) {
  askDanger(`永久删除 SQL Server 数据库 ${name}`, async () => {
    await sqlserverDelete(name, '确认')
    await loadPanel()
  })
}

function removeMongo(name) {
  askDanger(`永久删除 MongoDB 数据库 ${name}`, async () => {
    await mongoDelete(name, '确认')
    await loadPanel()
  })
}

function flushRedis() {
  askDanger('清空当前 Redis 的全部数据', async () => {
    await redisFlush('确认')
    showToast('已清空')
    await loadPanel()
  })
}

function removePg(name) {
  askDanger(`永久删除 PostgreSQL 数据库 ${name}`, async () => {
    await postgresDelete(name, '确认')
    await loadPanel()
  })
}

function removeRole(name) {
  askDanger(`永久删除角色 ${name}`, async () => {
    await postgresDeleteRole(name, '确认')
    await loadPanel()
  })
}

async function openSqlite(file) {
  form.value.file = file
  try {
    const { data } = await sqliteTables(file)
    panel.value = { ...panel.value, file: data.file, tables: data.tables, columns: [], rows: [] }
  } catch (error) {
    fail(error)
  }
}

async function openTable(table) {
  try {
    const { data } = await sqliteRows(panel.value.file, table, 1)
    panel.value = { ...panel.value, table: data.table, columns: data.columns, rows: data.rows }
  } catch (error) {
    fail(error)
  }
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(secret.value.password)
    showToast('密码已复制')
  } catch {
    showToast('请手动复制密码')
  }
}

onMounted(load)
onUnmounted(stopPoll)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col rounded border border-panel-line bg-white">
    <div class="flex gap-6 border-b border-panel-line px-4 pt-3 text-sm">
      <button
        v-for="item in tabs"
        :key="item.id"
        type="button"
        class="border-b-2 pb-2"
        :class="engine === item.id ? 'border-panel-green text-panel-green' : 'border-transparent text-panel-muted'"
        @click="choose(item.id)"
      >
        {{ item.label }}
      </button>
    </div>

    <div v-if="!current" class="flex flex-1 items-center justify-center text-sm text-panel-muted">正在检测 {{ label }}</div>

    <div v-else-if="!ready" class="flex flex-1 items-center justify-center">
      <div class="w-full max-w-md rounded border border-[#f3ddb0] bg-white px-10 py-8 text-center shadow-card">
        <div class="text-sm text-[#e6a23c]">
          {{ current.state === 'stopped' ? `${label} 容器已停止` : current.state === 'unknown' ? `暂时读不到 ${label} 的真实状态` : `当前未安装 ${label} 环境` }}
        </div>
        <p v-if="current.message" class="mt-3 text-sm text-panel-muted">{{ current.message }}</p>
        <div class="mt-4">
          <button v-if="current.state === 'stopped'" type="button" class="text-sm text-panel-green" :disabled="busy" @click="start">启动</button>
          <button v-else-if="current.state === 'unknown' || engine === 'sqlite'" type="button" class="text-sm text-panel-green" :disabled="busy" @click="load">重新检测</button>
          <button v-else type="button" class="text-sm text-panel-green" :disabled="busy" @click="openInstall">
            {{ busy ? current.message || '正在安装...' : '一键安装' }}
          </button>
        </div>
        <p v-if="engine !== 'sqlite' && current.state !== 'unknown'" class="mt-3 text-xs text-panel-muted">
          只在 127.0.0.1:{{ current.port }} 启动，数据目录 {{ current.data_dir }}。不会改其他服务的端口。
        </p>
        <p v-if="engine === 'sqlserver'" class="mt-2 text-xs text-panel-muted">安装即表示同意 Microsoft SQL Server 的许可条款。</p>
        <button v-if="engine === 'mysql' && !busy" type="button" class="mt-3 text-xs text-panel-green" @click="openDialog('remote')">
          添加远程数据库
        </button>
      </div>
    </div>

    <template v-else>
      <div class="flex flex-wrap items-center gap-2 border-b border-panel-line px-4 py-3">
        <button v-if="engine === 'mysql'" type="button" class="primary" @click="openDialog('mysql-create')">添加数据库</button>
        <button v-if="engine === 'mysql'" type="button" class="tool" @click="openDialog('mysql-root')">root密码</button>
        <button v-if="engine === 'sqlserver'" type="button" class="primary" @click="openDialog('sql-create')">添加数据库</button>
        <button v-if="engine === 'mongodb'" type="button" class="primary" @click="openDialog('mongo-create')">添加数据库</button>
        <button v-if="engine === 'pgsql'" type="button" class="primary" @click="openDialog('pg-create')">添加数据库</button>
        <button v-if="engine === 'pgsql'" type="button" class="tool" @click="openDialog('pg-password')">修改角色密码</button>
        <button v-if="engine === 'redis'" type="button" class="tool" @click="openDialog('redis-password')">修改密码</button>
        <button v-if="engine === 'redis'" type="button" class="tool" @click="flushRedis">清空缓存</button>
        <button v-if="engine !== 'sqlite'" type="button" class="tool" @click="stop">停止</button>
        <span class="text-xs text-panel-muted">{{ current.data_dir }}</span>
      </div>

      <div class="min-h-0 flex-1 overflow-auto p-4 text-sm">
        <DatabaseAccounts v-if="engine === 'mysql' || engine === 'redis' || engine === 'mongodb'" :engine="engine" />
        <table v-if="engine === 'mysql'" class="w-full text-left">
          <thead class="text-xs text-panel-muted"><tr><th>数据库名</th><th>用户名</th><th>密码</th><th>权限</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in panel.items || []" :key="row.db_name" class="border-t border-panel-line">
              <td class="py-2">{{ row.db_name }}</td>
              <td>{{ row.username }}</td>
              <td>{{ row.has_password ? '已设置' : '未设置' }}</td>
              <td>{{ row.privileges }}</td>
              <td class="space-x-2 text-xs">
                <button type="button" class="text-panel-green" @click="openDialog('mysql-password', row)">改密</button>
                <button type="button" class="text-panel-green" :disabled="actionBusy" @click="backupMysql(row)">备份</button>
                <button type="button" class="text-panel-green" @click="openRestore(row)">恢复</button>
                <button type="button" class="text-[#c24141]" @click="openMysqlDelete(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <table v-else-if="engine === 'sqlserver'" class="w-full text-left">
          <thead class="text-xs text-panel-muted"><tr><th>数据库名</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in panel.items || []" :key="row.name" class="border-t border-panel-line">
              <td class="py-2">{{ row.name }}</td>
              <td class="space-x-2 text-xs">
                <button type="button" class="text-panel-green" @click="backupSql(row.name)">备份</button>
                <button type="button" class="text-panel-green" @click="restoreSql(row.name)">恢复</button>
                <button type="button" class="text-[#c24141]" @click="removeSql(row.name)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-else-if="engine === 'mongodb'" class="space-y-3">
          <p class="text-xs text-panel-muted">管理账号 panel。每个业务库会创建同库的读写账号。</p>
          <div v-for="row in panel.items || []" :key="row.name" class="rounded border border-panel-line p-3">
            <div class="flex items-center justify-between">
              <strong>{{ row.name }}</strong>
              <button type="button" class="text-xs text-[#c24141]" @click="removeMongo(row.name)">删除</button>
            </div>
            <p class="mt-1 text-xs text-panel-muted">集合：{{ (row.collections || []).join('、') || '空' }}</p>
          </div>
        </div>

        <div v-else-if="engine === 'redis'" class="grid max-w-3xl grid-cols-3 gap-3">
          <div class="rounded border border-panel-line p-4"><div class="text-xs text-panel-muted">内存</div><div class="mt-1 text-lg">{{ panel.used_memory_human || '—' }}</div></div>
          <div class="rounded border border-panel-line p-4"><div class="text-xs text-panel-muted">客户端</div><div class="mt-1 text-lg">{{ panel.connected_clients ?? '—' }}</div></div>
          <div class="rounded border border-panel-line p-4"><div class="text-xs text-panel-muted">Key 总数</div><div class="mt-1 text-lg">{{ panel.keys ?? '—' }}</div></div>
        </div>

        <div v-else-if="engine === 'pgsql'" class="grid gap-6 md:grid-cols-2">
          <div>
            <h3 class="mb-2 text-xs text-panel-muted">数据库</h3>
            <div v-for="name in panel.databases || []" :key="name" class="flex justify-between border-t border-panel-line py-2">
              <span>{{ name }}</span>
              <button type="button" class="text-xs text-[#c24141]" @click="removePg(name)">删除</button>
            </div>
          </div>
          <div>
            <h3 class="mb-2 text-xs text-panel-muted">角色</h3>
            <div v-for="name in panel.roles || []" :key="name" class="flex justify-between border-t border-panel-line py-2">
              <span>{{ name }}</span>
              <button type="button" class="text-xs text-[#c24141]" @click="removeRole(name)">删除</button>
            </div>
          </div>
        </div>

        <div v-else class="grid gap-4 md:grid-cols-[240px_1fr]">
          <div>
            <form class="mb-3 flex gap-2" @submit.prevent="openSqlite(form.file)">
              <input v-model="form.file" class="field" placeholder="/www/server/data/sqlite/app.db" />
              <button type="submit" class="tool shrink-0 whitespace-nowrap">打开</button>
            </form>
            <button v-for="file in panel.items || []" :key="file" type="button" class="block w-full truncate py-1 text-left text-panel-green" @click="openSqlite(file)">
              {{ file }}
            </button>
          </div>
          <div>
            <div class="mb-2 flex flex-wrap gap-2">
              <button v-for="table in panel.tables || []" :key="table" type="button" class="tool" @click="openTable(table)">{{ table }}</button>
            </div>
            <table v-if="panel.columns" class="w-full text-left text-xs">
              <thead><tr><th v-for="column in panel.columns" :key="column" class="px-2 py-1">{{ column }}</th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in panel.rows || []" :key="index" class="border-t border-panel-line">
                  <td v-for="(cell, cellIndex) in row" :key="cellIndex" class="px-2 py-1">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </template>

    <p v-if="notice && !dialog" class="px-4 py-2 text-sm text-[#c24141]">{{ notice }}</p>
    <div v-if="toast" class="fixed bottom-6 right-6 z-40 rounded bg-[#2b2f36] px-4 py-2 text-sm text-white">{{ toast }}</div>

    <div v-if="secret" class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div class="w-full max-w-md rounded bg-white p-5 shadow-xl">
        <h2 class="text-base font-medium">请保存 {{ secret.engine }} 密码</h2>
        <p class="mt-2 text-sm text-panel-muted">这个密码只显示一次。{{ secret.user ? `账号是 ${secret.user}。` : '' }}</p>
        <div class="mt-3 break-all rounded bg-[#f7f8fa] px-3 py-3 font-mono text-sm">{{ secret.password }}</div>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="copySecret">复制</button>
          <button type="button" class="primary" @click="secret = null">我已保存</button>
        </div>
      </div>
    </div>

    <div v-if="danger" class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="submitDanger">
        <h2 class="text-base font-medium">{{ danger.title }}</h2>
        <p class="mt-2 text-sm text-panel-muted">此操作不能从面板里恢复。请输入“确认”后继续。</p>
        <input v-model="dangerPhrase" class="field mt-3" placeholder="请输入确认" />
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" :disabled="actionBusy" @click="danger = null">取消</button>
          <button type="submit" class="rounded bg-[#c24141] px-3 py-1 text-sm text-white disabled:opacity-50" :disabled="actionBusy">
            {{ actionBusy ? '正在执行…' : '确认执行' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="dialog === 'install'" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4" @click.self.prevent>
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="install">
        <h2 class="text-base font-medium">安装 {{ label }}</h2>
        <input v-model="customPassword" class="field mt-3" type="password" autocomplete="new-password" placeholder="root 密码" />
        <p class="mt-2 text-xs text-panel-muted">{{ rootRule }}</p>
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="dialog = ''">取消</button>
          <button type="submit" class="primary" :disabled="!rootPasswordOk(customPassword)">安装</button>
        </div>
      </form>
    </div>

    <div v-if="dialog === 'mysql-root'" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4" @click.self.prevent>
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent>
        <h2 class="text-base font-medium">root 密码</h2>
        <input class="field mt-3 font-mono" readonly :value="savedRoot" placeholder="还没有保存 root 密码" />
        <input v-model="form.password" class="field mt-3" type="password" autocomplete="new-password" placeholder="新密码，留空则不修改" />
        <p class="mt-2 text-xs text-panel-muted">{{ rootRule }}</p>
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="dialog = ''">关闭</button>
          <button type="button" class="primary" :disabled="!rootPasswordOk(form.password)" @click="changeSavedRoot">修改</button>
        </div>
      </form>
    </div>

    <div v-if="dialog === 'mysql-delete'" class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="submitMysqlDelete">
        <h2 class="text-base font-medium">删除数据库</h2>
        <p class="mt-2 text-sm text-panel-muted">此操作会删除数据库 {{ deleteName }}。请输入数据库名后继续。</p>
        <input v-model="deletePhrase" class="field mt-3" :placeholder="deleteName" />
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" :disabled="actionBusy" @click="dialog = ''">取消</button>
          <button
            type="submit"
            class="rounded bg-[#c24141] px-3 py-1 text-sm text-white disabled:opacity-50"
            :disabled="deletePhrase !== deleteName || actionBusy"
          >
            {{ actionBusy ? '正在执行…' : '删除' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="dialog === 'mysql-restore'" class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div class="w-full max-w-md rounded bg-white p-5 shadow-xl">
        <h2 class="text-base font-medium">恢复 {{ restoreName }}</h2>
        <p v-if="!restoreFiles.length" class="mt-3 text-sm text-panel-muted">还没有这个数据库的备份</p>
        <div v-else class="mt-3 max-h-64 space-y-2 overflow-auto">
          <button
            v-for="item in restoreFiles"
            :key="item.file"
            type="button"
            class="tool block w-full text-left"
            :disabled="actionBusy"
            @click="runRestore(item.file)"
          >
            {{ item.file }}
          </button>
        </div>
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end">
          <button type="button" class="tool" :disabled="actionBusy" @click="dialog = ''">取消</button>
        </div>
      </div>
    </div>

    <div v-if="dialog && dialog !== 'install' && dialog !== 'mysql-root' && dialog !== 'mysql-delete' && dialog !== 'mysql-restore'" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4" @click.self.prevent>
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="submit">
        <h2 class="text-base font-medium">{{ dialog.includes('password') || dialog.includes('root') ? '修改密码' : dialog === 'remote' ? '添加远程数据库' : '添加数据库' }}</h2>
        <div class="mt-3 space-y-2">
          <input v-if="dialog === 'remote'" v-model="form.host" class="field" placeholder="主机" />
          <input v-if="dialog === 'remote'" v-model="form.port" class="field" type="text" inputmode="numeric" placeholder="端口" />
          <input v-if="['mysql-create', 'mongo-create', 'pg-create', 'pg-password', 'remote'].includes(dialog)" v-model="form.user" class="field" placeholder="用户名" />
          <input v-if="['mysql-create', 'sql-create', 'mongo-create', 'pg-create'].includes(dialog)" v-model="form.name" class="field" placeholder="数据库名，字母开头" />
          <input v-if="dialog !== 'sql-create'" v-model="form.password" class="field" type="password" autocomplete="new-password" :placeholder="dialog === 'mysql-password' ? '新密码，需含大小写、数字和特殊字符' : ['mongo-create', 'pg-create', 'pg-password', 'redis-password'].includes(dialog) ? '密码，需含大小写和数字' : '密码，8 位以上字母数字下划线'" />
          <p v-if="dialog === 'mysql-password'" class="text-xs text-panel-muted">{{ rootRule }}</p>
          <div v-if="dialog === 'mysql-create'" class="text-sm">
            <label class="mr-3"><input v-model="form.access" type="radio" value="local" /> 本地</label>
            <label class="mr-3"><input v-model="form.access" type="radio" value="all" /> 所有人</label>
            <label><input v-model="form.access" type="radio" value="ip" /> 指定 IP</label>
            <input v-if="form.access === 'ip'" v-model="form.ip" class="field mt-2" placeholder="203.0.113.10" />
          </div>
        </div>
        <p v-if="notice" class="mt-2 text-sm text-[#c24141]">{{ notice }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="dialog = ''">取消</button>
          <button type="submit" class="primary" :disabled="dialog === 'mysql-password' && !rootPasswordOk(form.password)">确定</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.field { width: 100%; border: 1px solid #e7e9ed; border-radius: 4px; padding: 8px 10px; font-size: 14px; outline: none; }
.field:focus { border-color: #20a53a; }
.tool { border: 1px solid #e7e9ed; border-radius: 4px; padding: 4px 10px; font-size: 13px; }
.tool:disabled, .primary:disabled { opacity: 0.45; }
.primary { border-radius: 4px; background: #20a53a; padding: 4px 12px; font-size: 13px; color: white; }
</style>
