<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'

const props = defineProps({
  filename: { type: String, default: '' },
  initial: { type: String, default: '' },
})

const host = ref(null)
let view = null

function languageOf(filename) {
  const lower = filename.toLowerCase()
  const ext = lower.includes('.') ? lower.slice(lower.lastIndexOf('.')) : ''
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs' || ext === '.jsx') return javascript()
  if (ext === '.ts' || ext === '.tsx') return javascript({ typescript: true })
  if (ext === '.json') return json()
  if (ext === '.html' || ext === '.htm' || ext === '.vue') return html()
  if (ext === '.css' || ext === '.scss' || ext === '.less') return css()
  if (ext === '.py') return python()
  if (ext === '.md') return markdown()
  return null
}

function mount(text) {
  view?.destroy()
  const language = languageOf(props.filename)
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: text,
      extensions: [
        basicSetup,
        EditorView.theme({
          '&': { height: '100%', fontSize: '13px' },
          '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' },
        }),
        language,
      ].filter(Boolean),
    }),
  })
}

function getText() {
  return view ? view.state.doc.toString() : props.initial
}

defineExpose({ getText })

onMounted(() => mount(props.initial))
watch(() => props.initial, (text) => mount(text))
onBeforeUnmount(() => view?.destroy())
</script>

<template>
  <div ref="host" class="h-full overflow-hidden bg-white"></div>
</template>
