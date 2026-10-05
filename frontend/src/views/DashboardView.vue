<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import MetricRing from '../components/MetricRing.vue'
import TrendChart from '../components/TrendChart.vue'
import { useMetricsStore } from '../stores/metrics'
import {
  formatBytes,
  formatClock,
  formatDateTime,
  formatPercent,
  formatSpeed,
  formatUptime,
  usageColor,
} from '../utils/format'

const metrics = useMetricsStore()

onMounted(() => {
  metrics.start()
})

onUnmounted(() => {
  metrics.stop()
})

const point = computed(() => metrics.current)
const rootDisk = computed(() => {
  const disks = point.value?.disks || []
  return disks.find((item) => item.mount === '/') || disks[0] || null
})

const statusText = computed(() => {
  if (metrics.status === 'live') return '实时已连接'
  if (metrics.status === 'reconnecting') return '正在重连'
  if (metrics.status === 'connecting') return '正在连接'
  return '未连接'
})

const categories = computed(() => metrics.history.map((item) => formatClock(item.ts)))
const cpuSeries = computed(() => metrics.history.map((item) => Number(item.cpu_percent) || 0))
const memorySeries = computed(() => metrics.history.map((item) => Number(item.memory?.percent) || 0))
const uploadSeries = computed(() => metrics.history.map((item) => Number(item.network?.upload_bps) || 0))
const downloadSeries = computed(() => metrics.history.map((item) => Number(item.network?.download_bps) || 0))

const infoRows = computed(() => {
  const current = point.value
  if (!current) return []
  const addresses = (current.addresses || []).map((item) => `${item.name} · ${item.address}`)
  return [
    ['主机名', current.hostname || '--'],
    ['系统', current.platform || '--'],
    ['运行时间', formatUptime(current.uptime_seconds)],
    ['启动时间', formatDateTime(current.boot_time)],
    ['CPU 核心', current.cpu_count ? `${current.cpu_count} 核` : '--'],
    ['负载', (current.load || []).join(' / ') || '--'],
    ['进程数', current.process_count ?? '--'],
    ['IPv4', addresses.length ? addresses.join('\n') : '未检测到'],
  ]
})

function formatAxisPercent(value) {
  return `${Math.round(value)}%`
}
</script>

<template>
  <div class="h-full space-y-4 overflow-auto">
    <div class="flex items-center justify-between text-xs text-panel-muted">
      <div class="flex items-center gap-2">
        <span
          class="h-1.5 w-1.5 rounded-full"
          :class="metrics.status === 'live' ? 'bg-panel-green' : 'bg-[#e6a23c]'"
        ></span>
        {{ statusText }}
        <span v-if="point">· 采样 {{ formatDateTime(point.ts) }}</span>
      </div>
      <span>每秒刷新 · 趋势保留最近 2 分钟</span>
    </div>

    <section class="grid grid-cols-1 overflow-hidden rounded border border-panel-line bg-white shadow-card sm:grid-cols-2 xl:grid-cols-4">
      <div class="flex flex-col items-center border-b border-panel-line px-4 py-6 sm:border-r xl:border-b-0">
        <MetricRing
          :percent="point?.cpu_percent || 0"
          :color="usageColor(point?.cpu_percent, '#20a53a')"
        />
        <div class="mt-2 text-sm">CPU 使用率</div>
        <div class="mt-1 text-xs text-panel-muted tabular">
          {{ point?.cpu_count || '--' }} 核 · 负载 {{ (point?.load || []).join(' / ') || '--' }}
        </div>
      </div>

      <div class="flex flex-col items-center border-b border-panel-line px-4 py-6 xl:border-r xl:border-b-0">
        <MetricRing
          :percent="point?.memory?.percent || 0"
          :color="usageColor(point?.memory?.percent, '#3a8ee6')"
        />
        <div class="mt-2 text-sm">内存使用率</div>
        <div class="mt-1 text-center text-xs leading-5 text-panel-muted tabular">
          已用 {{ formatBytes(point?.memory?.used) }} / 共 {{ formatBytes(point?.memory?.total) }}
          <br />
          可用 {{ formatBytes(point?.memory?.available) }}
        </div>
      </div>

      <div class="flex flex-col items-center border-b border-panel-line px-4 py-6 sm:border-b-0 sm:border-r">
        <MetricRing
          :percent="rootDisk?.percent || 0"
          :color="usageColor(rootDisk?.percent, '#e6a23c')"
        />
        <div class="mt-2 text-sm">磁盘 {{ rootDisk?.mount || '/' }}</div>
        <div class="mt-1 text-center text-xs leading-5 text-panel-muted tabular">
          已用 {{ formatBytes(rootDisk?.used) }} / 共 {{ formatBytes(rootDisk?.total) }}
          <br />
          可用 {{ formatBytes(rootDisk?.free) }}
        </div>
      </div>

      <div class="flex flex-col items-center justify-center px-4 py-6">
        <div class="text-sm text-panel-muted">实时网速</div>
        <div class="mt-3 w-full max-w-[180px] space-y-2 text-sm tabular">
          <div class="flex items-center justify-between">
            <span class="text-[#e6a23c]">上行</span>
            <span class="font-medium">{{ formatSpeed(point?.network?.upload_bps) }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-panel-green">下行</span>
            <span class="font-medium">{{ formatSpeed(point?.network?.download_bps) }}</span>
          </div>
        </div>
        <div v-if="point?.swap?.total" class="mt-4 text-xs text-panel-muted tabular">
          交换区 {{ formatPercent(point.swap.percent) }}
        </div>
      </div>
    </section>

    <section class="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <div class="rounded border border-panel-line bg-white p-4 shadow-card xl:col-span-2">
        <h2 class="mb-2 flex items-center gap-2 text-sm font-medium">
          <span class="h-4 w-1 rounded-sm bg-panel-green"></span>
          资源趋势
        </h2>
        <div class="h-64">
          <div v-if="!metrics.history.length" class="flex h-full items-center justify-center text-sm text-panel-muted">
            等待采样数据…
          </div>
          <TrendChart
            v-else
            :categories="categories"
            :y-max="100"
            :format-value="formatPercent"
            :format-axis="formatAxisPercent"
            :series="[
              { name: 'CPU', data: cpuSeries, color: '#20a53a', area: true },
              { name: '内存', data: memorySeries, color: '#3a8ee6', area: true },
            ]"
          />
        </div>
      </div>

      <div class="rounded border border-panel-line bg-white p-4 shadow-card">
        <h2 class="mb-3 flex items-center gap-2 text-sm font-medium">
          <span class="h-4 w-1 rounded-sm bg-panel-green"></span>
          系统信息
        </h2>
        <dl v-if="point" class="space-y-2.5 text-sm">
          <div v-for="row in infoRows" :key="row[0]" class="flex gap-3">
            <dt class="w-16 shrink-0 text-panel-muted">{{ row[0] }}</dt>
            <dd class="min-w-0 whitespace-pre-line break-words">{{ row[1] }}</dd>
          </div>
        </dl>
        <p v-else class="text-sm text-panel-muted">正在采集系统信息…</p>
      </div>
    </section>

    <section class="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <div class="rounded border border-panel-line bg-white p-4 shadow-card xl:col-span-2">
        <h2 class="mb-2 flex items-center gap-2 text-sm font-medium">
          <span class="h-4 w-1 rounded-sm bg-panel-green"></span>
          网络吞吐
        </h2>
        <div class="h-64">
          <div v-if="!metrics.history.length" class="flex h-full items-center justify-center text-sm text-panel-muted">
            等待采样数据…
          </div>
          <TrendChart
            v-else
            :categories="categories"
            :format-value="formatSpeed"
            :format-axis="formatSpeed"
            :series="[
              { name: '上行', data: uploadSeries, color: '#e6a23c', area: true },
              { name: '下行', data: downloadSeries, color: '#20a53a', area: true },
            ]"
          />
        </div>
      </div>

      <div class="rounded border border-panel-line bg-white p-4 shadow-card">
        <h2 class="mb-3 flex items-center gap-2 text-sm font-medium">
          <span class="h-4 w-1 rounded-sm bg-panel-green"></span>
          磁盘与网卡
        </h2>
        <div v-if="point" class="space-y-4">
          <div v-for="disk in point.disks" :key="disk.mount">
            <div class="flex justify-between text-xs">
              <span>{{ disk.mount }}</span>
              <span class="tabular">{{ formatPercent(disk.percent) }}</span>
            </div>
            <div class="mt-1 h-1.5 overflow-hidden rounded bg-[#eef0f3]">
              <div
                class="h-full rounded"
                :style="{ width: `${Math.min(100, disk.percent)}%`, background: usageColor(disk.percent, '#20a53a') }"
              ></div>
            </div>
            <div class="mt-1 text-[11px] text-panel-muted tabular">
              已用 {{ formatBytes(disk.used) }} / 共 {{ formatBytes(disk.total) }} · 可用 {{ formatBytes(disk.free) }}
            </div>
          </div>

          <table class="w-full text-left text-xs">
            <thead class="text-panel-muted">
              <tr>
                <th class="py-1 font-normal">网卡</th>
                <th class="py-1 font-normal">上行</th>
                <th class="py-1 font-normal">下行</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="nic in point.network?.interfaces || []" :key="nic.name" class="border-t border-panel-line">
                <td class="py-1.5">{{ nic.name }}</td>
                <td class="py-1.5 tabular">{{ formatSpeed(nic.upload_bps) }}</td>
                <td class="py-1.5 tabular">{{ formatSpeed(nic.download_bps) }}</td>
              </tr>
              <tr v-if="!(point.network?.interfaces || []).length">
                <td colspan="3" class="py-2 text-panel-muted">暂无活动网卡</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="text-sm text-panel-muted">正在采集…</p>
      </div>
    </section>
  </div>
</template>
