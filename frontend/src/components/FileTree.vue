<script setup>
defineProps({
  node: { type: Object, required: true },
  active: { type: String, default: '' },
})

defineEmits(['open', 'toggle'])
</script>

<template>
  <div>
    <div class="flex items-center">
      <button
        type="button"
        class="flex h-7 w-5 shrink-0 items-center justify-center text-[10px] text-panel-muted"
        @click="$emit('toggle', node)"
      >
        {{ node.open ? '▾' : '▸' }}
      </button>
      <button
        type="button"
        class="min-w-0 flex-1 truncate rounded px-1 py-1 text-left text-sm"
        :class="active === node.path ? 'bg-[#e9f7ec] text-[#17812c]' : 'hover:bg-[#f6f7f9]'"
        @click="$emit('open', node)"
      >
        {{ node.name }}
      </button>
    </div>
    <div v-if="node.open" class="pl-3">
      <FileTree
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :active="active"
        @open="$emit('open', $event)"
        @toggle="$emit('toggle', $event)"
      />
    </div>
  </div>
</template>
