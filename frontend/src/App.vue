<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">轨道交通信号检修管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向轨道交通信号设备日常巡检、故障处置、天窗修作业、器材检修与联锁试验的一体化信号检修管理后台。</span>
        <span class="head-user">当前值班：{{ store.operator }} · {{ store.shiftLabel }}</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const navItems = [{ label: "运营概览", path: "/" }, { label: "联锁管理", path: "/interlock" }, { label: "轨道电路", path: "/trackcircuit" }, { label: "信号机", path: "/signal" }, { label: "转辙机", path: "/pointmachine" }, { label: "信号电缆", path: "/cable" }, { label: "信号电源", path: "/powersupply" }, { label: "车载设备", path: "/atp" }, { label: "应答器", path: "/balise" }, { label: "计轴设备", path: "/axlecounter" }, { label: "调度中心", path: "/dispatchcenter" }, { label: "天窗修作业", path: "/maintenancewindow" }, { label: "继电器检修", path: "/relay" }, { label: "熔断器管理", path: "/fuse" }, { label: "防雷元件", path: "/lightning" }, { label: "应急备品", path: "/emergencyresp" }, { label: "联锁试验", path: "/testrecord" }, { label: "信号故障", path: "/fault" }, { label: "检修工具", path: "/tool" }, { label: "技术规章", path: "/regulation" }, { label: "技能培训", path: "/training" }, { label: "竣工图纸档案", path: "/drawing-archive" }]
</script>
