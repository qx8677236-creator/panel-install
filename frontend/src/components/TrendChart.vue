<script setup>
import { computed } from 'vue'
import VChart from 'vue-echarts'

const props = defineProps({
  categories: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  yMax: { type: Number, default: null },
  formatValue: { type: Function, default: (value) => String(value ?? '') },
  formatAxis: { type: Function, default: (value) => String(value) },
})

const option = computed(() => ({
  animationDuration: 280,
  grid: { left: 8, right: 16, top: 36, bottom: 4, containLabel: true },
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(31, 42, 56, 0.94)',
    borderWidth: 0,
    textStyle: { color: '#fff', fontSize: 12 },
    valueFormatter: (value) => props.formatValue(value),
  },
  legend: {
    top: 0,
    right: 0,
    icon: 'roundRect',
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: '#667085', fontSize: 12 },
  },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: props.categories,
    axisLine: { lineStyle: { color: '#e6e8eb' } },
    axisTick: { show: false },
    axisLabel: { color: '#98a2b3', fontSize: 11 },
  },
  yAxis: {
    type: 'value',
    min: 0,
    ...(props.yMax == null ? {} : { max: props.yMax }),
    axisLabel: {
      color: '#98a2b3',
      fontSize: 11,
      formatter: (value) => props.formatAxis(value),
    },
    splitLine: { lineStyle: { color: '#f2f4f7' } },
  },
  series: props.series.map((item) => ({
    name: item.name,
    type: 'line',
    smooth: 0.25,
    showSymbol: false,
    data: item.data,
    lineStyle: { width: 2, color: item.color },
    itemStyle: { color: item.color },
    areaStyle: item.area ? { color: item.color, opacity: 0.08 } : undefined,
  })),
}))
</script>

<template>
  <div class="h-full w-full">
    <VChart class="h-full w-full" :option="option" autoresize />
  </div>
</template>
