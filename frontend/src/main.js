import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'

import App from './App.vue'
import router from './router'
import './style.css'

// 按需注册，仪表盘只使用折线趋势图。
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
