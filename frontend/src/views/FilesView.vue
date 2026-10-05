<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import CodeEditor from '../components/CodeEditor.vue'
import FileTree from '../components/FileTree.vue'
import {
  changeMode,
  completeUpload,
  compressPaths,
  createFile,
  createFolder,
  deletePaths,
  downloadFile,
  extractArchive,
  fileError,
  initUpload,
  listFiles,
  listTree,
  readFile,
  renamePath,
  saveFile,
  uploadChunk,
} from '../api/files'
import { formatBytes, formatDateTime } from '../utils/format'

const CHUNK_SIZE = 1024 * 1024
const PAGE_SIZE = 100
const ROW_HEIGHT = 40

const route = useRoute()
const current = ref('')
const rootLabel = ref('')
const entries = ref([])
const page = ref(1)
const total = ref(0)
const keyword = ref('')
const loading = ref(false)
const editorLoading = ref(false)
const alarm = ref('')
const notice = ref('')
const toast = ref('')
const uploadText = ref('')
const selected = ref([])
const jumpPath = ref('')
const address = ref('')
const locked = ref(false)
const dialog = ref(null)
const dialogValue = ref('')
const dialogBusy = ref(false)
const dialogError = ref('')
const editor = ref(null)
const editorRef = ref(null)
const saving = ref(false)
const fileInput = ref(null)
const scroller = ref(null)
const scrollTop = ref(0)
const viewportHeight = ref(480)

let requestSeq = 0
let listAbort = null
let searchTimer = null
let toastTimer = null
let scrollFrame = 0

const rootNode = reactive({
  name: '根目录',
  path: '',
  open: true,
  loaded: false,
  children: [],
})

const crumbs = computed(() => {
  const items = [{ name: '根目录', path: '' }]
  let acc = ''
  for (const part of current.value.split('/').filter(Boolean)) {
    acc = acc ? `${acc}/${part}` : part
    items.push({ name: part, path: acc })
  }
  return items
})

const rootName = computed(() => rootLabel.value.split('/').filter(Boolean).pop() || 'wwwroot')
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const allSelected = computed(() => entries.value.length > 0 && selected.value.length === entries.value.length)
const startIndex = computed(() => Math.max(0, Math.floor(scrollTop.value / ROW_HEIGHT) - 6))
const visibleCount = computed(() => Math.ceil(viewportHeight.value / ROW_HEIGHT) + 12)
const visibleEntries = computed(() => entries.value.slice(startIndex.value, startIndex.value + visibleCount.value))
const padTop = computed(() => startIndex.value * ROW_HEIGHT)
const padBottom = computed(() => Math.max(0, (entries.value.length - startIndex.value - visibleEntries.value.length) * ROW_HEIGHT))

function joinPath(directory, name) {
  return directory ? `${directory}/${name}` : name
}

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 4000)
}

function fail(error) {
  const parsed = fileError(error)
  if (parsed.canceled) return
  if (parsed.timeout) {
    showToast(parsed.message)
    return
  }
  if (parsed.jail) alarm.value = parsed.message
  else notice.value = parsed.message
}

function joinAbsolute(root, path) {
  const base = String(root || '').replace(/\/$/, '')
  if (!path) return base
  return `${base}/${String(path).replace(/^\//, '')}`
}

function applyListing(data) {
  current.value = data.path || ''
  address.value = data.absolute || joinAbsolute(data.root, data.path)
  jumpPath.value = address.value
  rootLabel.value = data.root || ''
  entries.value = data.entries || []
  total.value = Number(data.total ?? entries.value.length)
  page.value = Number(data.page || 1)
  selected.value = []
  alarm.value = ''
  scrollTop.value = 0
  if (scroller.value) scroller.value.scrollTop = 0
}

async function reload() {
  listAbort?.abort()
  const controller = new AbortController()
  listAbort = controller
  const seq = ++requestSeq
  loading.value = true
  notice.value = ''
  try {
    const { data } = await listFiles(current.value, {
      page: page.value,
      pageSize: PAGE_SIZE,
      q: keyword.value.trim(),
      signal: controller.signal,
    })
    if (controller.signal.aborted || seq !== requestSeq) return false
    applyListing(data)
    return true
  } catch (error) {
    if (controller.signal.aborted || seq !== requestSeq || fileError(error).canceled || error?.name === 'AbortError') return false
    fail(error)
    return false
  } finally {
    if (listAbort === controller) loading.value = false
  }
}

async function loadNode(node) {
  const { data } = await listTree(node.path)
  node.children = (data.directories || []).map((item) => ({
    name: item.name,
    path: item.path,
    open: false,
    loaded: false,
    children: [],
  }))
  node.loaded = true
}

async function enter(path) {
  const previous = current.value
  const previousAddress = address.value
  const started = requestSeq
  current.value = path
  page.value = 1
  keyword.value = ''
  const ok = await reload()
  if (!ok && requestSeq === started + 1) {
    current.value = previous
    address.value = previousAddress
  }
}

function goParent() {
  const parts = current.value.split('/').filter(Boolean)
  if (!parts.length) return
  parts.pop()
  enter(parts.join('/'))
}

async function goAddress() {
  const target = address.value.trim()
  const previous = current.value
  const previousAddress = address.value
  const started = requestSeq
  current.value = target
  page.value = 1
  keyword.value = ''
  const ok = await reload()
  if (!ok && requestSeq === started + 1) {
    current.value = previous
    address.value = previousAddress
  }
}

function onSearch(event) {
  keyword.value = event.target.value
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    reload()
  }, 300)
}

function changePage(next) {
  if (loading.value || locked.value) return
  const target = Math.min(pageCount.value, Math.max(1, next))
  if (target === page.value) return
  page.value = target
  reload()
}

async function onTreeOpen(node) {
  enter(node.path)
}

async function onTreeToggle(node) {
  node.open = !node.open
  if (node.open && !node.loaded) {
    try {
      await loadNode(node)
    } catch (error) {
      fail(error)
    }
  }
}

function toggleAll(event) {
  selected.value = event.target.checked ? entries.value.map((item) => item.name) : []
}

function toggleOne(name, event) {
  if (event.target.checked) selected.value = [...selected.value, name]
  else selected.value = selected.value.filter((item) => item !== name)
}

function selectedPaths() {
  return selected.value.map((name) => joinPath(current.value, name))
}

function openDialog(kind) {
  dialogError.value = ''
  const one = selected.value.length === 1 ? selected.value[0] : ''
  const defaults = {
    mkdir: '',
    file: '',
    rename: one,
    chmod: '755',
    compress: 'archive.zip',
    extract: '',
    delete: '',
  }
  dialogValue.value = defaults[kind] || ''
  dialog.value = kind
}

function closeDialog() {
  dialog.value = null
  dialogError.value = ''
}

async function submitDialog() {
  if (dialogBusy.value) return
  dialogError.value = ''
  dialogBusy.value = true
  try {
    if (dialog.value === 'mkdir') await createFolder(current.value, dialogValue.value.trim())
    if (dialog.value === 'file') await createFile(current.value, dialogValue.value.trim())
    if (dialog.value === 'rename') {
      if (selected.value.length !== 1) throw new Error('请选择一个文件')
      await renamePath(joinPath(current.value, selected.value[0]), dialogValue.value.trim())
    }
    if (dialog.value === 'chmod') await changeMode(selectedPaths(), dialogValue.value.trim())
    if (dialog.value === 'compress') {
      await compressPaths(selectedPaths(), joinPath(current.value, dialogValue.value.trim()))
    }
    if (dialog.value === 'extract') {
      if (selected.value.length !== 1) throw new Error('请选择一个压缩包')
      await extractArchive(joinPath(current.value, selected.value[0]), current.value)
    }
    if (dialog.value === 'delete') {
      if (dialogValue.value.trim() !== '确认') throw new Error('请输入确认')
      await deletePaths(selectedPaths())
    }
    closeDialog()
    await reload()
    rootNode.loaded = false
    await loadNode(rootNode)
  } catch (error) {
    if (error?.response) {
      const parsed = fileError(error)
      if (parsed.timeout) {
        showToast(parsed.message)
        closeDialog()
      } else if (parsed.jail) {
        alarm.value = parsed.message
        closeDialog()
      } else dialogError.value = parsed.message
    } else {
      dialogError.value = error.message || '操作失败'
    }
  } finally {
    dialogBusy.value = false
  }
}

function openEntry(entry) {
  if (entry.is_link) {
    notice.value = '符号链接不能直接打开'
    return
  }
  const path = joinPath(current.value, entry.name)
  if (entry.is_dir) {
    enter(path)
    return
  }
  if (locked.value || loading.value) return
  if (!entry.editable) {
    notice.value = '这个文件不能当文本编辑，可以下载'
    return
  }
  locked.value = true
  openEditor(path, entry.name).finally(() => {
    locked.value = false
  })
}

async function openEditor(path, name) {
  editorLoading.value = true
  try {
    const { data } = await readFile(path)
    editor.value = {
      path: data.path,
      name,
      content: data.content,
      truncated: Boolean(data.truncated),
      message: data.message || '',
    }
    if (data.truncated) showToast(data.message || '文件过大，已截断显示')
  } catch (error) {
    fail(error)
  } finally {
    editorLoading.value = false
  }
}

async function saveEditor() {
  if (!editor.value || editor.value.truncated) return
  saving.value = true
  try {
    await saveFile(editor.value.path, editorRef.value.getText())
    notice.value = '已保存'
    editor.value = null
    await reload()
  } catch (error) {
    fail(error)
  } finally {
    saving.value = false
  }
}

async function downloadOne(entry) {
  try {
    const path = joinPath(current.value, entry.name)
    const { data } = await downloadFile(path)
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = entry.name
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    fail(error)
  }
}

async function uploadFiles(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  for (const file of files) {
    try {
      await uploadOne(file)
    } catch (error) {
      fail(error)
      break
    }
  }
  uploadText.value = ''
  await reload()
}

async function uploadOne(file) {
  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE))
  const { data } = await initUpload({
    directory: current.value,
    filename: file.name,
    size: file.size,
    total_chunks: totalChunks,
  })
  for (let index = 0; index < totalChunks; index += 1) {
    const start = index * CHUNK_SIZE
    const blob = file.slice(start, Math.min(file.size, start + CHUNK_SIZE))
    await uploadChunk(data.upload_id, index, blob, file.name)
    uploadText.value = `正在上传 ${file.name} ${Math.round(((index + 1) / totalChunks) * 100)}%`
  }
  await completeUpload(data.upload_id)
}

function onListScroll(event) {
  const top = event.target.scrollTop
  if (scrollFrame) return
  scrollFrame = requestAnimationFrame(() => {
    scrollTop.value = top
    scrollFrame = 0
  })
}

function measureViewport() {
  if (scroller.value) viewportHeight.value = scroller.value.clientHeight || 480
}

const dialogTitle = computed(() => {
  const titles = {
    mkdir: '新建文件夹',
    file: '新建文件',
    rename: '重命名',
    chmod: '修改权限',
    compress: '压缩为',
    extract: '解压到当前目录',
    delete: '删除所选',
  }
  return titles[dialog.value] || ''
})

function routePath() {
  return typeof route.query.path === 'string' ? route.query.path : ''
}

onUnmounted(() => {
  listAbort?.abort()
})

onMounted(async () => {
  const initial = routePath()
  if (initial) current.value = initial
  await reload()
  measureViewport()
  try {
    await loadNode(rootNode)
  } catch (error) {
    fail(error)
  }
})

watch(
  () => route.query.path,
  (value) => {
    if (typeof value === 'string' && value !== current.value) enter(value)
  },
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div v-if="alarm" class="mb-3 flex items-center justify-between rounded border border-[#f3c1c1] bg-[#fff5f5] px-3 py-2 text-sm text-[#c24141]">
      <span>路径越界报警：{{ alarm }}</span>
      <button type="button" class="text-xs" @click="alarm = ''">关闭</button>
    </div>

    <div class="flex min-h-0 flex-1 overflow-hidden rounded border border-panel-line bg-white shadow-card">
      <aside class="flex w-56 shrink-0 flex-col border-r border-panel-line">
        <div class="border-b border-panel-line px-3 py-2 text-xs text-panel-muted" :title="rootLabel">
          常用目录 · {{ rootName }}
        </div>
        <div class="min-h-0 flex-1 overflow-auto px-2 py-2">
          <FileTree :node="rootNode" :active="current" @open="onTreeOpen" @toggle="onTreeToggle" />
        </div>
      </aside>

      <section class="flex min-w-0 flex-1 flex-col">
        <form class="flex items-center gap-2 border-b border-panel-line bg-[#f7f8fa] px-3 py-2" @submit.prevent="goAddress">
          <button type="button" class="tool h-8 w-8" title="返回上一级" :disabled="loading" @click="goParent">←</button>
          <input
            v-model="address"
            class="min-w-0 flex-1 rounded border border-[#cfd5dc] bg-white px-3 py-1.5 font-mono text-sm outline-none focus:border-panel-green"
            spellcheck="false"
            autocomplete="off"
            @keydown.enter.stop.prevent="goAddress"
          />
          <button type="submit" class="tool h-8 w-8" title="刷新" :disabled="loading">↻</button>
        </form>
        <div class="flex flex-wrap items-center gap-2 border-b border-panel-line px-3 py-2">
          <button type="button" class="tool" @click="openDialog('file')">新建文件</button>
          <button type="button" class="tool" @click="openDialog('mkdir')">新建文件夹</button>
          <button type="button" class="tool" @click="fileInput?.click()">上传</button>
          <button type="button" class="tool" :disabled="!selected.length" @click="openDialog('compress')">压缩</button>
          <button type="button" class="tool" :disabled="selected.length !== 1" @click="openDialog('extract')">解压</button>
          <button type="button" class="tool" :disabled="selected.length !== 1" @click="openDialog('rename')">重命名</button>
          <button type="button" class="tool" :disabled="!selected.length" @click="openDialog('chmod')">权限</button>
          <button type="button" class="tool text-[#c24141]" :disabled="!selected.length" @click="openDialog('delete')">删除</button>
          <button type="button" class="tool" :disabled="loading" @click="reload">{{ loading ? '正在读取' : '刷新' }}</button>
          <input ref="fileInput" type="file" multiple class="hidden" @change="uploadFiles" />
          <span v-if="uploadText" class="text-xs text-panel-muted">{{ uploadText }}</span>
        </div>

        <div class="flex flex-wrap items-center gap-2 border-b border-panel-line px-3 py-2 text-sm">
          <button
            v-for="(crumb, index) in crumbs"
            :key="crumb.path + index"
            type="button"
            class="text-panel-muted hover:text-panel-green"
            @click="enter(crumb.path)"
          >
            {{ crumb.name }}<span v-if="index < crumbs.length - 1" class="px-1 text-panel-line">/</span>
          </button>
          <input
            class="ml-auto w-40 rounded border border-panel-line px-2 py-1 text-xs outline-none focus:border-panel-green"
            :value="keyword"
            placeholder="搜索当前目录"
            @input="onSearch"
          />
        </div>

        <p v-if="notice" class="px-3 pt-2 text-xs text-panel-muted">{{ notice }}</p>

        <div class="relative min-h-0 flex-1">
          <div v-if="loading || locked" class="absolute inset-0 z-10 cursor-wait overflow-hidden bg-white/85">
            <div v-for="row in 8" :key="row" class="flex items-center gap-4 border-b border-panel-line px-3 py-3">
              <div class="h-3 w-3 animate-pulse rounded bg-[#e7ebf0]"></div>
              <div class="h-3 flex-1 animate-pulse rounded bg-[#e7ebf0]"></div>
              <div class="h-3 w-16 animate-pulse rounded bg-[#e7ebf0]"></div>
              <div class="h-3 w-24 animate-pulse rounded bg-[#e7ebf0]"></div>
            </div>
          </div>
          <div v-if="editorLoading" class="absolute right-3 top-3 z-20 rounded bg-white px-3 py-1 text-xs text-panel-muted shadow">
            正在读取文件
          </div>
          <div ref="scroller" class="h-full overflow-auto" @scroll.passive="onListScroll">
            <table class="w-full text-left text-sm">
              <thead class="sticky top-0 z-[1] bg-[#fafbfc] text-xs text-panel-muted">
                <tr>
                  <th class="w-10 px-3 py-2 font-normal">
                    <input type="checkbox" :checked="allSelected" @change="toggleAll" />
                  </th>
                  <th class="px-2 py-2 font-normal">名称</th>
                  <th class="w-28 px-2 py-2 font-normal">大小</th>
                  <th class="w-36 px-2 py-2 font-normal">权限</th>
                  <th class="w-44 px-2 py-2 font-normal">修改时间</th>
                  <th class="w-24 px-3 py-2 font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!entries.length && !loading">
                  <td colspan="6" class="px-3 py-8 text-center text-panel-muted">{{ keyword ? '没有匹配的文件' : '这个目录是空的' }}</td>
                </tr>
                <tr v-if="padTop" :style="{ height: padTop + 'px' }">
                  <td colspan="6"></td>
                </tr>
                <tr
                  v-for="entry in visibleEntries"
                  :key="entry.name"
                  class="cursor-pointer border-t border-panel-line hover:bg-[#f7fbf8]"
                  :style="{ height: ROW_HEIGHT + 'px' }"
                >
                  <td class="px-3 py-2" @click.stop>
                    <input
                      type="checkbox"
                      :checked="selected.includes(entry.name)"
                      @change="toggleOne(entry.name, $event)"
                    />
                  </td>
                  <td class="px-2 py-2" @click="openEntry(entry)">
                    <span class="mr-2 text-xs text-panel-muted">{{ entry.is_dir ? '目录' : entry.is_link ? '链接' : '文件' }}</span>
                    {{ entry.name }}
                  </td>
                  <td class="px-2 py-2 tabular text-panel-muted">{{ entry.is_dir ? '--' : formatBytes(entry.size) }}</td>
                  <td class="px-2 py-2 tabular text-panel-muted" :title="entry.mode_text">{{ entry.mode }} {{ entry.mode_text }}</td>
                  <td class="px-2 py-2 tabular text-panel-muted">{{ formatDateTime(entry.mtime) }}</td>
                  <td class="px-3 py-2" @dblclick.stop>
                    <button
                      v-if="!entry.is_dir && !entry.is_link"
                      type="button"
                      class="text-xs text-panel-green"
                      @click="downloadOne(entry)"
                    >
                      下载
                    </button>
                  </td>
                </tr>
                <tr v-if="padBottom" :style="{ height: padBottom + 'px' }">
                  <td colspan="6"></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="flex items-center justify-between border-t border-panel-line px-3 py-2 text-xs text-panel-muted">
          <span>共 {{ total }} 项 · 第 {{ page }} / {{ pageCount }} 页</span>
          <div class="flex gap-2">
            <button type="button" class="tool" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button>
            <button type="button" class="tool" :disabled="page >= pageCount || loading" @click="changePage(page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </div>

    <div v-if="toast" class="fixed bottom-6 right-6 z-40 rounded bg-[#2b2f36] px-4 py-2 text-sm text-white shadow-lg">
      {{ toast }}
    </div>

    <div v-if="editor" class="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-6">
      <div class="flex h-[80vh] w-full max-w-5xl flex-col overflow-hidden rounded bg-white shadow-2xl">
        <div class="flex items-center justify-between border-b border-panel-line px-4 py-3">
          <div>
            <div class="text-sm font-medium">{{ editor.name }}</div>
            <div class="text-xs text-panel-muted">{{ editor.path || '根目录' }}</div>
          </div>
          <div class="flex gap-2">
            <button type="button" class="tool" @click="editor = null">关闭</button>
            <button
              type="button"
              class="rounded bg-panel-green px-3 py-1 text-sm text-white disabled:opacity-60"
              :disabled="saving || editor.truncated"
              @click="saveEditor"
            >
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
        <p v-if="editor.truncated" class="bg-[#fff8e8] px-4 py-2 text-xs text-[#8a5a12]">{{ editor.message || '文件过大，已截断显示' }}</p>
        <CodeEditor ref="editorRef" class="min-h-0 flex-1" :filename="editor.name" :initial="editor.content" />
      </div>
    </div>

    <div v-if="dialog" class="fixed inset-0 z-20 flex items-center justify-center bg-black/30 p-4">
      <form class="w-full max-w-md rounded bg-white p-5 shadow-xl" @submit.prevent="submitDialog">
        <h2 class="text-base font-medium">{{ dialogTitle }}</h2>
        <p v-if="dialog === 'delete'" class="mt-3 text-sm text-panel-muted">删除后不能从面板里恢复。只会删除当前根目录内的文件。请输入“确认”后继续。</p>
        <p v-else-if="dialog === 'extract'" class="mt-3 text-sm text-panel-muted">解压到当前目录。压缩包里的 ../ 和系统路径会被拦截。</p>
        <input
          v-if="dialog !== 'extract'"
          v-model="dialogValue"
          class="mt-3 w-full rounded border border-panel-line px-3 py-2 text-sm outline-none focus:border-panel-green"
          :placeholder="dialog === 'delete' ? '请输入确认' : dialog === 'chmod' ? '例如 755' : ''"
        />
        <p v-if="dialogError" class="mt-2 text-sm text-[#c24141]">{{ dialogError }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="tool" :disabled="dialogBusy" @click="closeDialog">取消</button>
          <button type="submit" class="rounded bg-panel-green px-3 py-1 text-sm text-white disabled:opacity-60" :disabled="dialogBusy">
            {{ dialogBusy ? '正在执行…' : '确定' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.tool {
  border: 1px solid #e7e9ed;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: #2b2f36;
}
.tool:disabled {
  opacity: 0.45;
}
.tool:not(:disabled):hover {
  border-color: #20a53a;
  color: #20a53a;
}
</style>
