<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SiteSettings from '../components/SiteSettings.vue'
import { createSite, deleteSite, diagnoseSite, listSites, siteError, toggleSite } from '../api/sites'
import { isCanceled } from '../api/http'
import { showError } from '../utils/message'
import { warningScan } from '../api/security'

const router = useRouter()

function openRoot(site) {
  if (!site?.root) return
  router.push({ name: 'files', query: { path: site.root } })
}

const projectTabs = [
  ['php', 'PHP项目'],
  ['java', 'Java项目'],
  ['node', 'Node项目'],
  ['go', 'Go项目'],
  ['python', 'Python项目'],
  ['net', '.Net项目'],
  ['proxy', '反向代理'],
  ['html', 'HTML项目'],
  ['other', '其他项目'],
]

const advancedTabs = [
  ['domain', '域名管理'],
  ['webroot', '网站目录'],
  ['rewrite', '伪静态'],
  ['ssl', 'SSL'],
  ['limit', '流量限制'],
  ['hotlink', '防盗链'],
  ['redirect', '重定向'],
  ['config', '配置文件'],
]

const sites = ref([])
const configDir = ref('')
const nginxFound = ref(true)
const nginxVersion = ref('nginx')
const templates = ref({})
const loading = ref(false)
const busy = ref(false)
const notice = ref('')
const errorLog = ref('')
const creating = ref(false)
const deleting = ref(null)
const deleteFiles = ref(false)
const deleteDatabase = ref(false)
const deleteConfirm = ref('')
const deleteError = ref('')
const scanOpen = ref(false)
const findings = ref([])
const scanError = ref('')
watch(scanOpen, async (open) => {
  if (!open) return
  scanError.value = ''
  try {
    findings.value = (await warningScan()).data.items || []
  } catch (error) {
    findings.value = []
    scanError.value = siteError(error).message
  }
})
const form = ref({ domain: '', root: '', kind: 'html' })
const currentDomain = ref('')
const diagnosing = ref('')
const diagnoseResult = ref(null)
const diagnoseError = ref('')
const settingsTab = ref('domain')
const project = ref('html')
const statusFilter = ref('all')
const keyword = ref('')
const picked = ref([])
const advanced = ref('')

const deleteReady = computed(() => {
  const key = deleting.value?.domain || ''
  return Boolean(key) && deleteConfirm.value.trim() === key
})

const visibleSites = computed(() => {
  const word = keyword.value.trim().toLowerCase()
  return sites.value.filter((site) => {
    if ((site.kind || 'html') !== project.value) return false
    if (statusFilter.value === 'running' && !site.enabled) return false
    if (statusFilter.value === 'stopped' && site.enabled) return false
    if (!word) return true
    const blob = `${site.domain} ${site.root || ''} ${site.note || ''}`.toLowerCase()
    return blob.includes(word)
  })
})

function fail(error) {
  const parsed = siteError(error)
  if (parsed.canceled || isCanceled(error)) return
  notice.value = parsed.message
  errorLog.value = ''
  showError(parsed.message)
}

function siteLabel(site) {
  const text = String(site.domain || "")
  if (text.includes(":")) return text
  const port = String(site.port ?? "80")
  return port === "80" ? text : `${text}:${port}`
}

async function reload() {
  loading.value = true
  try {
    const { data } = await listSites()
    sites.value = data.sites || []
    configDir.value = data.config_dir || ''
    nginxFound.value = Boolean(data.nginx_found)
    nginxVersion.value = data.nginx_version || 'nginx'
    templates.value = data.templates || {}
    picked.value = picked.value.filter((domain) => sites.value.some((site) => site.domain === domain))
  } catch (error) {
    fail(error)
  } finally {
    loading.value = false
  }
}

async function submitCreate() {
  if (busy.value) return
  busy.value = true
  notice.value = ''
  errorLog.value = ''
  try {
    await createSite(form.value.domain.trim(), form.value.root.trim(), form.value.kind)
    project.value = form.value.kind
    creating.value = false
    form.value = { domain: '', root: '', kind: project.value }
    notice.value = '站点已创建，并已加载到系统 Nginx'
    await reload()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function openDiagnose(site) {
  diagnosing.value = site.domain
  diagnoseResult.value = null
  diagnoseError.value = ''
  try {
    const { data } = await diagnoseSite(site.domain)
    diagnoseResult.value = {
      ok: Boolean(data.ok),
      nginx_test: data.nginx_test || '',
      http_status: data.http_status ?? 0,
      error_log: data.error_log || '',
    }
  } catch (error) {
    diagnoseError.value = siteError(error).message
  }
}

async function switchSite(site) {
  busy.value = true
  notice.value = ''
  errorLog.value = ''
  try {
    await toggleSite(site.domain, !site.enabled)
    notice.value = site.enabled ? '站点已停止' : '站点已启动'
    await reload()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

function openDelete(site) {
  deleting.value = site
  deleteFiles.value = false
  deleteDatabase.value = false
  deleteConfirm.value = ''
  deleteError.value = ''
}

function closeDelete() {
  deleting.value = null
  deleteError.value = ''
}

async function submitDelete() {
  const site = deleting.value
  if (!site || deleteConfirm.value.trim() !== site.domain || busy.value) return
  const typed = deleteConfirm.value.trim()
  busy.value = true
  deleteError.value = ''
  try {
    await deleteSite(site.domain, deleteFiles.value, deleteDatabase.value, typed)
    closeDelete()
    notice.value = '站点已删除'
    errorLog.value = ''
    await reload()
  } catch (error) {
    const parsed = siteError(error)
    if (!parsed.canceled && !isCanceled(error)) deleteError.value = parsed.message
  } finally {
    busy.value = false
  }
}

function openSettings(domain, tab = 'domain') {
  settingsTab.value = tab
  currentDomain.value = domain
}

function selectedSite() {
  if (picked.value.length !== 1) {
    notice.value = '请先勾选一个站点'
    errorLog.value = ''
    return null
  }
  return sites.value.find((site) => site.domain === picked.value[0]) || null
}

function useAdvanced() {
  const tab = advanced.value
  advanced.value = ''
  if (!tab) return
  const site = selectedSite()
  if (!site) return
  openSettings(site.domain, tab)
}

function openSecurity() {
  const site = selectedSite()
  if (!site) return
  openSettings(site.domain, 'security')
}

function invert() {
  const names = visibleSites.value.map((site) => site.domain)
  const chosen = new Set(picked.value)
  picked.value = names.filter((name) => !chosen.has(name))
}

onMounted(reload)
</script>

<template>
  <div class="h-full space-y-3 overflow-auto">
    <div v-if="!nginxFound" class="rounded border border-[#f3ddb0] bg-[#fff8eb] px-4 py-2 text-sm text-[#8a5a12]">
      没有找到 nginx。站点不会加载。
    </div>

    <div v-if="notice" class="rounded border border-panel-line bg-white px-4 py-3 text-sm shadow-card">
      <div class="flex items-start justify-between gap-3">
        <span>{{ notice }}</span>
        <button type="button" class="text-xs text-panel-muted" @click="notice = ''; errorLog = ''">关闭</button>
      </div>
      <pre v-if="errorLog" class="log mt-2">{{ errorLog }}</pre>
    </div>

    <div class="overflow-hidden rounded border border-panel-line bg-white shadow-card">
      <div class="flex gap-1 overflow-x-auto border-b border-panel-line px-3 pt-2">
        <button
          v-for="[id, label] in projectTabs"
          :key="id"
          type="button"
          class="tab"
          :class="project === id ? 'tab-on' : ''"
          @click="project = id"
        >
          {{ label }}
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2 border-b border-panel-line px-3 py-3">
        <button type="button" class="primary" @click="creating = true">添加站点</button>
        <select v-model="advanced" class="tool" @change="useAdvanced">
          <option value="">高级设置</option>
          <option v-for="[id, label] in advancedTabs" :key="id" :value="id">{{ label }}</option>
        </select>
        <button type="button" class="tool" @click="scanOpen = true">漏洞扫描</button>
        <button type="button" class="tool" @click="openSecurity">网站安全</button>
        <button type="button" class="tool" @click="reload">Nginx {{ nginxVersion }}</button>
        <button type="button" class="tool" @click="invert">目录求反选</button>
        <div class="ml-auto flex gap-2">
          <select v-model="statusFilter" class="tool">
            <option value="all">全部分类</option>
            <option value="running">运行中</option>
            <option value="stopped">已停止</option>
          </select>
          <input v-model="keyword" class="search" placeholder="请输入域名或备注" />
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full min-w-[1080px] text-left text-sm">
          <thead class="bg-[#f7f8fa] text-xs text-panel-muted">
            <tr>
              <th class="w-10 px-3 py-2"></th>
              <th class="px-3 py-2 font-medium">网站名</th>
              <th class="px-3 py-2 font-medium">状态</th>
              <th class="px-3 py-2 font-medium">备份</th>
              <th class="px-3 py-2 font-medium">根目录</th>
              <th class="px-3 py-2 font-medium">日流量</th>
              <th class="px-3 py-2 font-medium">到期时间</th>
              <th class="px-3 py-2 font-medium">备注</th>
              <th class="px-3 py-2 font-medium">PHP</th>
              <th class="px-3 py-2 font-medium">SSL证书</th>
              <th class="px-3 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!visibleSites.length">
              <td colspan="11" class="px-4 py-10 text-center text-panel-muted">{{ loading ? '正在读取' : '这个分类下还没有站点' }}</td>
            </tr>
            <tr v-for="site in visibleSites" :key="site.domain" class="border-t border-panel-line">
              <td class="px-3 py-3">
                <input v-model="picked" type="checkbox" :value="site.domain" />
              </td>
              <td class="px-3 py-3">
                <button type="button" class="font-medium text-panel-green" @click="openSettings(site.domain)">{{ siteLabel(site) }}</button>
              </td>
              <td class="px-3 py-3">
                <span :class="site.enabled ? 'text-panel-green' : 'text-panel-muted'">{{ site.status }}</span>
              </td>
              <td class="px-3 py-3">{{ site.backup }}</td>
              <td class="px-3 py-3 font-mono text-xs">
                <button v-if="site.root" type="button" class="text-left text-panel-green hover:underline" @click="openRoot(site)">{{ site.root }}</button>
                <span v-else>—</span>
              </td>
              <td class="px-3 py-3">{{ site.traffic || '0 B' }}</td>
              <td class="px-3 py-3">{{ site.expires_at || '—' }}</td>
              <td class="px-3 py-3">{{ site.note || '—' }}</td>
              <td class="px-3 py-3">{{ site.php || '未安装' }}</td>
              <td class="px-3 py-3">{{ site.ssl ? '已部署' : '未部署' }}</td>
              <td class="px-3 py-3">
                <div class="flex gap-2">
                  <button type="button" class="tool" @click="openDiagnose(site)">诊断</button>
                  <button type="button" class="tool" @click="openSettings(site.domain)">设置</button>
                  <button type="button" class="tool" :disabled="busy" @click="switchSite(site)">
                    {{ site.enabled ? '停止' : '启动' }}
                  </button>
                  <button type="button" class="tool danger" :disabled="busy" @click="openDelete(site)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="border-t border-panel-line px-4 py-2 text-xs text-panel-muted">
        {{ loading ? '正在读取' : `当前分类 ${visibleSites.length} 个站点` }} · 配置目录 {{ configDir || '读取中' }}
      </div>
    </div>

    <div v-if="diagnosing" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <div class="w-full max-w-lg rounded bg-white p-5 shadow-card">
        <h2 class="text-base font-medium">诊断 {{ diagnosing }}</h2>
        <p v-if="diagnoseError" class="mt-3 text-sm text-[#c24141]">{{ diagnoseError }}</p>
        <template v-else-if="diagnoseResult">
          <p class="mt-3 text-sm">结果：{{ diagnoseResult.ok ? '可访问' : '不可访问' }}</p>
          <p class="mt-2 text-sm">HTTP 状态：{{ diagnoseResult.http_status }}</p>
          <p class="mt-2 text-xs text-panel-muted">nginx 检查</p>
          <pre class="log mt-1">{{ diagnoseResult.nginx_test || '无输出' }}</pre>
          <p class="mt-2 text-xs text-panel-muted">错误日志</p>
          <pre class="log mt-1">{{ diagnoseResult.error_log || '无' }}</pre>
        </template>
        <p v-else class="mt-3 text-sm text-panel-muted">正在检查</p>
        <div class="mt-4 flex justify-end">
          <button type="button" class="tool" @click="diagnosing = ''">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="creating" class="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-card" @submit.prevent="submitCreate">
        <h2 class="text-base font-medium">添加站点</h2>
        <label class="mt-4 block text-xs text-panel-muted">项目类型</label>
        <select v-model="form.kind" class="field">
          <option v-for="[id, label] in projectTabs" :key="id" :value="id">{{ label }}</option>
        </select>
        <label class="mt-3 block text-xs text-panel-muted">域名或 IP</label>
        <input v-model="form.domain" class="field" placeholder="www.example.com 或 20.187.70.213:9999" required />
        <label class="mt-3 block text-xs text-panel-muted">网站根目录</label>
        <input v-model="form.root" class="field" placeholder="留空则使用域名，在网站目录下创建" />
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="creating = false">取消</button>
          <button type="submit" class="primary" :disabled="busy">创建</button>
        </div>
      </form>
    </div>

    <div v-if="deleting" class="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-card" @submit.prevent="submitDelete">
        <div class="flex items-start justify-between gap-3">
          <h2 class="text-base font-medium text-[#c24141]">删除站点 {{ deleting.domain }}</h2>
          <button type="button" class="text-sm text-panel-muted" @click="closeDelete">×</button>
        </div>
        <p class="mt-3 text-sm text-panel-muted">此操作会从面板和 Nginx 中移除该站点。请输入站点名 {{ deleting.domain }} 后才能继续。</p>
        <label class="mt-4 flex items-start gap-2 text-sm">
          <input v-model="deleteFiles" type="checkbox" class="mt-1" />
          <span>勾选此项，将同步永久删除网站根目录文件（不可逆）</span>
        </label>
        <label class="mt-3 flex items-start gap-2 text-sm">
          <input v-model="deleteDatabase" type="checkbox" class="mt-1" />
          <span>勾选此项，将同步永久删除关联的数据库及用户</span>
        </label>
        <input v-model="deleteConfirm" class="field" :placeholder="deleting.domain" autocomplete="off" />
        <p v-if="deleteError" class="mt-2 text-sm text-[#c24141]">{{ deleteError }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" @click="closeDelete">取消</button>
          <button type="submit" class="danger-btn" :disabled="busy || !deleteReady">确定删除</button>
        </div>
      </form>
    </div>

    <div v-if="scanOpen" class="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4">
      <div class="w-full max-w-lg rounded bg-white p-5 shadow-card">
        <h2 class="text-base font-medium">漏洞扫描</h2>
        <p class="mt-3 text-sm text-panel-muted">这里检查站点配置有没有通过 Nginx 语法检查，以及有没有成功加载。不会改其他服务的端口，也不会连接外部扫描器。</p>
        <p v-if="scanError" class="mt-3 text-sm text-[#c24141]">{{ scanError }}</p>
        <ul class="mt-3 space-y-2 text-sm">
          <li v-if="!findings.length" class="text-panel-muted">正在读取本机检查结果</li>
          <li v-for="(item, index) in findings" :key="index" class="border-t border-panel-line py-2">
            <span class="text-panel-muted">{{ item.site }} · {{ item.level }}</span>
            <div>{{ item.item }}：{{ item.message }}</div>
          </li>
        </ul>
        <div class="mt-4 text-right">
          <button type="button" class="tool" @click="scanOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <SiteSettings
      v-if="currentDomain"
      :domain="currentDomain"
      :templates="templates"
      :initial-tab="settingsTab"
      @close="currentDomain = ''"
      @saved="reload"
    />
  </div>
</template>

<style scoped>
.tab {
  border-bottom: 2px solid transparent;
  padding: 8px 12px;
  font-size: 13px;
  color: #5c6570;
  white-space: nowrap;
}
.tab-on {
  border-color: #20a53a;
  color: #20a53a;
  font-weight: 500;
}
.tool {
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  color: #2b2f36;
  background: white;
}
.tool:disabled { opacity: 0.45; }
.tool:not(:disabled):hover { border-color: #20a53a; color: #20a53a; }
.danger { color: #c24141; }
.danger:not(:disabled):hover { border-color: #c24141; color: #c24141; }
.danger-btn {
  border-radius: 4px;
  background: #c24141;
  padding: 4px 12px;
  font-size: 12px;
  color: white;
}
.danger-btn:disabled { opacity: 0.45; }
.primary {
  border-radius: 4px;
  background: #20a53a;
  padding: 4px 12px;
  font-size: 12px;
  color: white;
}
.primary:disabled { opacity: 0.45; }
.field, .search {
  margin-top: 4px;
  width: 100%;
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  padding: 6px 8px;
  outline: none;
}
.search { margin-top: 0; width: 180px; }
.field:focus, .search:focus { border-color: #20a53a; }
.log {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  border-radius: 4px;
  background: #1f2a38;
  padding: 8px;
  font-size: 12px;
  color: #d7dde6;
}
</style>
