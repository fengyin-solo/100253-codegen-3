import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '@/views/Dashboard.vue'
const Interlock = () => import('@/views/interlock/index.vue')
const Trackcircuit = () => import('@/views/trackcircuit/index.vue')
const Signal = () => import('@/views/signal/index.vue')
const Pointmachine = () => import('@/views/pointmachine/index.vue')
const Cable = () => import('@/views/cable/index.vue')
const Powersupply = () => import('@/views/powersupply/index.vue')
const Atp = () => import('@/views/atp/index.vue')
const Balise = () => import('@/views/balise/index.vue')
const Axlecounter = () => import('@/views/axlecounter/index.vue')
const Dispatchcenter = () => import('@/views/dispatchcenter/index.vue')
const Maintenancewindow = () => import('@/views/maintenancewindow/index.vue')
const Relay = () => import('@/views/relay/index.vue')
const Fuse = () => import('@/views/fuse/index.vue')
const Lightning = () => import('@/views/lightning/index.vue')
const Emergencyresp = () => import('@/views/emergencyresp/index.vue')
const Testrecord = () => import('@/views/testrecord/index.vue')
const Fault = () => import('@/views/fault/index.vue')
const Tool = () => import('@/views/tool/index.vue')
const Regulation = () => import('@/views/regulation/index.vue')
const Training = () => import('@/views/training/index.vue')
const DrawingArchive = () => import('@/views/drawingarchive/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/interlock', name: 'interlock', component: Interlock },
    { path: '/trackcircuit', name: 'trackcircuit', component: Trackcircuit },
    { path: '/signal', name: 'signal', component: Signal },
    { path: '/pointmachine', name: 'pointmachine', component: Pointmachine },
    { path: '/cable', name: 'cable', component: Cable },
    { path: '/powersupply', name: 'powersupply', component: Powersupply },
    { path: '/atp', name: 'atp', component: Atp },
    { path: '/balise', name: 'balise', component: Balise },
    { path: '/axlecounter', name: 'axlecounter', component: Axlecounter },
    { path: '/dispatchcenter', name: 'dispatchcenter', component: Dispatchcenter },
    { path: '/maintenancewindow', name: 'maintenancewindow', component: Maintenancewindow },
    { path: '/relay', name: 'relay', component: Relay },
    { path: '/fuse', name: 'fuse', component: Fuse },
    { path: '/lightning', name: 'lightning', component: Lightning },
    { path: '/emergencyresp', name: 'emergencyresp', component: Emergencyresp },
    { path: '/testrecord', name: 'testrecord', component: Testrecord },
    { path: '/fault', name: 'fault', component: Fault },
    { path: '/tool', name: 'tool', component: Tool },
    { path: '/regulation', name: 'regulation', component: Regulation },
    { path: '/training', name: 'training', component: Training },
    { path: '/drawing-archive', name: 'drawing-archive', component: DrawingArchive },
  ],
})

export default router
