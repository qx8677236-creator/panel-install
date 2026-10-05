<script setup>
import { computed } from 'vue'

const props = defineProps({
  percent: { type: Number, default: 0 },
  color: { type: String, default: '#20a53a' },
})

const radius = 42
const circumference = 2 * Math.PI * radius
const clamped = computed(() => Math.min(100, Math.max(0, Number(props.percent) || 0)))
const offset = computed(() => circumference * (1 - clamped.value / 100))
</script>

<template>
  <svg viewBox="0 0 120 120" class="h-[108px] w-[108px]">
    <circle cx="60" cy="60" r="42" fill="none" stroke="#eef0f3" stroke-width="8" />
    <circle
      cx="60"
      cy="60"
      r="42"
      fill="none"
      :stroke="color"
      stroke-width="8"
      stroke-linecap="round"
      :stroke-dasharray="circumference"
      :stroke-dashoffset="offset"
      transform="rotate(-90 60 60)"
      class="transition-[stroke-dashoffset] duration-500"
    />
    <text
      x="60"
      y="66"
      text-anchor="middle"
      font-size="22"
      font-weight="600"
      fill="#2b2f36"
      style="font-variant-numeric: tabular-nums"
    >
      {{ Math.round(clamped) }}%
    </text>
  </svg>
</template>
