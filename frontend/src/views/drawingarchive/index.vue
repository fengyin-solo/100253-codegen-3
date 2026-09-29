<template>
  <section class="page" data-module="drawing-archive">
    <header class="page-head">
      <div>
        <h2>竣工图纸档案</h2>
        <p class="page-desc">
          整套图纸一次上传生成档案编号，按工程分段打包下载，资料移交时整体转给接收人；
          已移交档案锁定只读，每一版保留变更记录，档案总数与移交清单随时核对。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openUpload()">上传整套图纸</button>
        <button class="btn" type="button" @click="runConsistency">核对档案与移交清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tabs">
      <button :class="['tab', { active: tab === 'archives' }]" type="button" @click="tab = 'archives'">图纸档案</button>
      <button :class="['tab', { active: tab === 'handover' }]" type="button" @click="reloadHandovers">移交清单</button>
    </div>

    <!-- 档案列表 -->
    <div v-if="tab === 'archives'">
      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>档案编号 / 工程名称</span>
          <input v-model="keyword" placeholder="按档案编号或工程名称检索" />
        </label>
        <label class="filter-item">
          <span>移交状态</span>
          <select v-model="statusFilter">
            <option value="">全部</option>
            <option value="待移交">待移交</option>
            <option value="已移交">已移交</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>档案编号</th><th>工程名称</th><th>当前版本</th><th>图纸数</th>
            <th>工程分段</th><th>状态</th><th>更新时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in archives" :key="row.archive_no">
            <td>{{ row.archive_no }}</td>
            <td>{{ row.project_name }}</td>
            <td>v{{ row.current_version }}</td>
            <td>{{ row.file_count }}</td>
            <td>{{ row.segment_count }}</td>
            <td>
              <span :class="['tag', row.status === '已移交' ? 'tag-lock' : 'tag-open']">{{ row.status }}</span>
            </td>
            <td>{{ row.updated_at }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openDetail(row.archive_no)">查看明细</button>
              <button class="link" type="button" @click="download(row.archive_no)">下载整套</button>
              <button v-if="row.status === '待移交'" class="link" type="button" @click="openUpload(row.archive_no)">
                覆盖上传
              </button>
              <button v-if="row.status === '待移交'" class="link" type="button" @click="openHandover(row)">
                办理移交
              </button>
            </td>
          </tr>
          <tr v-if="!archives.length">
            <td colspan="8" class="empty-state">暂无图纸档案，先把整套图纸打成 ZIP 上传</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ total }} 个图纸档案</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </div>

    <!-- 移交清单 -->
    <div v-else>
      <form class="filter-bar" @submit.prevent="reloadHandovers">
        <label class="filter-item">
          <span>档案编号 / 工程名称 / 接收人</span>
          <input v-model="handoverKeyword" placeholder="按接收人或档案检索" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>档案编号</th><th>工程名称</th><th>移交版本</th><th>图纸数</th>
            <th>工程分段</th><th>接收人</th><th>移交人</th><th>移交时间</th><th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in handovers" :key="row.id">
            <td>{{ row.archive_no }}</td>
            <td>{{ row.project_name }}</td>
            <td>v{{ row.version }}</td>
            <td>{{ row.file_count }}</td>
            <td>{{ row.segments.join('、') }}</td>
            <td>{{ row.receiver }}</td>
            <td>{{ row.operator }}</td>
            <td>{{ row.handed_at }}</td>
            <td>{{ row.remark || '—' }}</td>
          </tr>
          <tr v-if="!handovers.length">
            <td colspan="9" class="empty-state">暂无移交记录</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>移交清单共 {{ handovers.length }} 条，与已移交档案数 {{ stats[2]?.value ?? 0 }} 对应</span>
      </footer>
    </div>

    <!-- 上传弹窗 -->
    <div v-if="uploadOpen" class="modal-mask" @click.self="cancelUpload">
      <div class="modal">
        <h3>{{ uploadForm.archive_no ? `按编号覆盖上传：${uploadForm.archive_no}` : '上传整套图纸（新建档案）' }}</h3>
        <p class="modal-tip">
          压缩包要求：仅一个顶层文件夹，根部放图纸分段目录（如「正线区间/」「车辆段/」），
          图纸直接放在分段目录下；允许 PDF/DWG/DXF/图片。结构不符会整包退回并写明原因。
        </p>
        <div class="form-grid">
          <label v-if="!uploadForm.archive_no">
            <span>工程名称 *</span>
            <input v-model="uploadForm.projectName" placeholder="如：3号线信号系统竣工图" />
          </label>
          <label>
            <span>操作人</span>
            <input v-model="uploadForm.operator" />
          </label>
          <label>
            <span>本次说明</span>
            <input v-model="uploadForm.remark" placeholder="覆盖了什么，便于写进版本记录" />
          </label>
          <label class="full">
            <span>图纸压缩包（ZIP）*</span>
            <input type="file" accept=".zip" @change="onFilePicked" />
          </label>
        </div>

        <div v-if="uploading" class="progress-box">
          <div class="progress-bar"><div :style="{ width: progressPercent + '%' }"></div></div>
          <span>{{ progressPercent }}%</span>
          <button class="btn ghost" type="button" @click="abortUpload">中止上传</button>
        </div>

        <div v-if="uploadErrors.length" class="reject-box">
          <strong>整包退回，原因如下（档案数据未做任何改动）：</strong>
          <ul>
            <li v-for="(reason, idx) in uploadErrors" :key="idx">{{ reason }}</li>
          </ul>
        </div>

        <div v-if="uploadResult" class="result-box">
          <p class="ok-line">{{ uploadResult.message }}</p>
          <p v-if="uploadResult.warnings?.length" class="warn-line">
            <span v-for="(w, i) in uploadResult.warnings" :key="i">⚠ {{ w }}<br /></span>
          </p>
          <table v-if="uploadResult.changed || uploadResult.new_archive" class="mini-table">
            <thead><tr><th>类别</th><th>图纸</th></tr></thead>
            <tbody>
              <tr v-for="item in uploadResult.added" :key="'a' + item.path">
                <td class="diff-add">新增</td><td>{{ item.segment }}/{{ item.name }}</td>
              </tr>
              <tr v-for="item in uploadResult.overwritten" :key="'o' + item.path">
                <td class="diff-over">覆盖</td><td>{{ item.segment }}/{{ item.name }}</td>
              </tr>
              <tr v-for="item in uploadResult.deleted" :key="'d' + item.path">
                <td class="diff-del">删除</td><td>{{ item.segment }}/{{ item.name }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="modal-foot">
          <button class="btn" type="button" @click="cancelUpload">{{ uploadResult ? '关闭' : '取消' }}</button>
          <button
            class="btn primary"
            type="button"
            :disabled="!pickedFile || uploading"
            @click="submitUpload"
          >
            {{ uploading ? '上传中…' : '确认上传' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 明细抽屉 -->
    <div v-if="detail" class="drawer-mask" @click.self="detail = null">
      <div class="drawer">
        <div class="drawer-head">
          <div>
            <h3>{{ detail.archive_no }} · {{ detail.project_name }}</h3>
            <p>
              当前版本 v{{ detail.current_version }} ·
              <span :class="['tag', detail.locked ? 'tag-lock' : 'tag-open']">{{ detail.status }}</span>
              <span v-if="detail.locked">（已移交给 {{ detail.handover?.receiver }}，只能查看与下载）</span>
            </p>
          </div>
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </div>

        <div class="drawer-actions">
          <button class="btn" type="button" @click="download(detail.archive_no)">下载整套</button>
          <button
            v-for="seg in detail.segments"
            :key="seg.name"
            class="btn"
            type="button"
            @click="downloadSegment(detail.archive_no, seg.name)"
          >
            分段下载：{{ seg.name }}（{{ seg.file_count }} 张）
          </button>
        </div>

        <h4>图纸清单（{{ detail.files.length }} 张）</h4>
        <table class="data-table mini-table">
          <thead><tr><th>工程分段</th><th>图纸文件</th><th>大小</th><th>SHA256</th></tr></thead>
          <tbody>
            <tr v-for="f in detail.files" :key="f.path">
              <td>{{ f.segment }}</td>
              <td>{{ f.name }}</td>
              <td>{{ formatSize(f.size) }}</td>
              <td class="hash-cell" :title="f.sha256">{{ f.sha256.slice(0, 12) }}…</td>
            </tr>
          </tbody>
        </table>

        <h4>版本变更记录（{{ versions.length }} 版）</h4>
        <div v-for="ver in versions" :key="ver.version" class="version-card">
          <div class="version-head">
            <strong>v{{ ver.version }}</strong>
            <span>{{ ver.created_at }} · {{ ver.operator }}</span>
          </div>
          <p class="version-remark">{{ ver.remark }}（在档 {{ ver.file_count }} 张）</p>
          <div class="version-diff">
            <span v-for="item in ver.added" :key="'a' + item.path" class="chip diff-add">+ {{ item.name }}</span>
            <span v-for="item in ver.overwritten" :key="'o' + item.path" class="chip diff-over">⟳ {{ item.name }}</span>
            <span v-for="item in ver.deleted" :key="'d' + item.path" class="chip diff-del">− {{ item.name }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 移交弹窗 -->
    <div v-if="handoverOpen" class="modal-mask" @click.self="handoverOpen = false">
      <div class="modal narrow">
        <h3>整体移交图纸档案</h3>
        <p class="modal-tip">
          将把档案 {{ handoverTarget?.archive_no }} v{{ handoverTarget?.current_version }}
          （{{ handoverTarget?.file_count }} 张图纸）整体转给接收人。移交后档案锁定，只能查看与下载，不能再改动。
        </p>
        <div class="form-grid">
          <label>
            <span>接收人 / 接收单位 *</span>
            <input v-model="handoverForm.receiver" placeholder="如：城建档案馆 张工" />
          </label>
          <label>
            <span>移交人</span>
            <input v-model="handoverForm.operator" />
          </label>
          <label class="full">
            <span>移交备注</span>
            <input v-model="handoverForm.remark" placeholder="如：竣工验收后资料移交" />
          </label>
        </div>
        <p v-if="handoverMessage" :class="handoverOk ? 'ok-line' : 'error-text'">{{ handoverMessage }}</p>
        <div class="modal-foot">
          <button class="btn" type="button" @click="handoverOpen = false">取消</button>
          <button class="btn primary" type="button" :disabled="handoverBusy" @click="submitHandover">确认移交</button>
        </div>
      </div>
    </div>

    <!-- 一致性核对结果 -->
    <div v-if="consistencyResult" class="modal-mask" @click.self="consistencyResult = null">
      <div class="modal">
        <h3>档案总数与移交清单核对</h3>
        <p :class="consistencyResult.ok ? 'ok-line' : 'error-text'">
          {{ consistencyResult.ok ? '✓ 全部一致' : '✗ 发现不一致项' }}
        </p>
        <table class="data-table mini-table">
          <thead><tr><th>核对项</th><th>应为</th><th>实际</th><th>结果</th></tr></thead>
          <tbody>
            <tr v-for="(c, i) in consistencyResult.checks" :key="i">
              <td>{{ c.name }}</td><td>{{ c.expected }}</td><td>{{ c.actual }}</td>
              <td>{{ c.ok ? '一致' : '不符' }}</td>
            </tr>
          </tbody>
        </table>
        <ul v-if="consistencyResult.mismatches.length" class="reject-box">
          <li v-for="(m, i) in consistencyResult.mismatches" :key="i">{{ m }}</li>
        </ul>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="consistencyResult = null">知道了</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/drawing-archives'

interface Summary {
  id: number
  archive_no: string
  project_name: string
  current_version: number
  status: string
  file_count: number
  segment_count: number
  updated_at: string
}
interface ArchiveFile { path: string; segment: string; name: string; size: number; sha256: string }
interface ArchiveDetail extends Summary {
  locked: boolean
  files: ArchiveFile[]
  segments: { name: string; file_count: number; total_size: number }[]
  handover: { receiver: string; handed_at: string } | null
}
interface VersionRecord {
  version: number
  created_at: string
  operator: string
  remark: string
  file_count: number
  added: ArchiveFile[]
  overwritten: (ArchiveFile & { old_sha256: string })[]
  deleted: ArchiveFile[]
}
interface UploadResult {
  message: string
  changed: boolean
  new_archive: boolean
  archive_no: string
  version: number
  file_count: number
  warnings: string[]
  added: ArchiveFile[]
  overwritten: (ArchiveFile & { old_sha256?: string })[]
  deleted: ArchiveFile[]
  unchanged: string[]
}
interface HandoverRow {
  id: number
  archive_no: string
  project_name: string
  version: number
  file_count: number
  segments: string[]
  receiver: string
  operator: string
  remark: string
  handed_at: string
}

const tab = ref<'archives' | 'handover'>('archives')
const stats = ref([
  { label: '档案总数', value: 0 },
  { label: '待移交', value: 0 },
  { label: '已移交（锁定）', value: 0 },
  { label: '在档图纸', value: 0 },
])
const archives = ref<Summary[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')

const handovers = ref<HandoverRow[]>([])
const handoverKeyword = ref('')

const detail = ref<ArchiveDetail | null>(null)
const versions = ref<VersionRecord[]>([])

const uploadOpen = ref(false)
const uploadForm = ref({ archive_no: '', projectName: '', operator: '值班管理员', remark: '' })
const pickedFile = ref<File | null>(null)
const uploading = ref(false)
const progressPercent = ref(0)
const uploadErrors = ref<string[]>([])
const uploadResult = ref<UploadResult | null>(null)
let activeXhr: XMLHttpRequest | null = null

const handoverOpen = ref(false)
const handoverTarget = ref<Summary | null>(null)
const handoverForm = ref({ receiver: '', operator: '值班管理员', remark: '' })
const handoverBusy = ref(false)
const handoverMessage = ref('')
const handoverOk = ref(false)

const consistencyResult = ref<{
  ok: boolean
  checks: { name: string; expected: number; actual: number; ok: boolean }[]
  mismatches: string[]
} | null>(null)

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

async function loadStats() {
  const response = await request(`${ENDPOINT}/stats`)
  if (response.ok) {
    const data = await response.json()
    stats.value = [
      { label: '档案总数', value: data.archive_total },
      { label: '待移交', value: data.pending_total },
      { label: '已移交（锁定）', value: data.handed_total },
      { label: '在档图纸', value: data.drawing_total },
    ]
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value) params.set('keyword', keyword.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${params}`)
    if (!response.ok) throw new Error('档案列表读取失败')
    const data = await response.json()
    archives.value = data.items ?? []
    total.value = data.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '档案列表读取失败'
  }
  void loadStats()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

async function reloadHandovers() {
  tab.value = 'handover'
  const params = new URLSearchParams()
  if (handoverKeyword.value) params.set('keyword', handoverKeyword.value)
  const response = await request(`${ENDPOINT}/handover-records?${params}`)
  if (response.ok) {
    const data = await response.json()
    handovers.value = data.items ?? []
  }
}

function openUpload(archiveNo = '') {
  uploadOpen.value = true
  uploadErrors.value = []
  uploadResult.value = null
  pickedFile.value = null
  progressPercent.value = 0
  uploading.value = false
  uploadForm.value = { archive_no: archiveNo, projectName: '', operator: '值班管理员', remark: '' }
}

function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  pickedFile.value = input.files?.[0] ?? null
}

function submitUpload() {
  if (!pickedFile.value || uploading.value) return
  uploading.value = true
  uploadErrors.value = []
  uploadResult.value = null
  progressPercent.value = 0

  const form = new FormData()
  form.append('package', pickedFile.value)
  form.append('archive_no', uploadForm.value.archive_no)
  form.append('project_name', uploadForm.value.projectName)
  form.append('operator', uploadForm.value.operator)
  form.append('remark', uploadForm.value.remark)

  // multipart 不能走统一 JSON 封装：用 XHR 拿进度，并允许随时中止；中止/失败都不会产生半份数据。
  const xhr = new XMLHttpRequest()
  xhr.open('POST', ENDPOINT)
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) progressPercent.value = Math.round((event.loaded / event.total) * 100)
  }
  xhr.onload = () => {
    uploading.value = false
    let payload: unknown = null
    try {
      payload = JSON.parse(xhr.responseText)
    } catch {
      uploadErrors.value = ['服务端返回无法解析，请稍后重试']
      return
    }
    if (xhr.status >= 200 && xhr.status < 300) {
      uploadResult.value = payload as UploadResult
      progressPercent.value = 100
      void reload()
    } else {
      const detail = (payload as { detail?: { reasons?: string[]; message?: string } })?.detail
      if (detail?.reasons) uploadErrors.value = detail.reasons
      else if (detail?.message) uploadErrors.value = [detail.message]
      else uploadErrors.value = [`上传未被接收（HTTP ${xhr.status}），档案数据未改动`]
    }
  }
  xhr.onerror = () => {
    uploading.value = false
    uploadErrors.value = ['网络中断，上传未完成；服务端不会保留半份数据，可重新上传']
  }
  xhr.onabort = () => {
    uploading.value = false
    uploadErrors.value = ['已主动中止上传，未写入任何档案数据']
  }
  activeXhr = xhr
  xhr.send(form)
}

function abortUpload() {
  activeXhr?.abort()
}

function cancelUpload() {
  if (uploading.value) abortUpload()
  uploadOpen.value = false
}

async function openDetail(archiveNo: string) {
  const response = await request(`${ENDPOINT}/${archiveNo}`)
  if (!response.ok) return
  detail.value = await response.json()
  const verResponse = await request(`${ENDPOINT}/${archiveNo}/versions`)
  if (verResponse.ok) {
    const data = await verResponse.json()
    versions.value = (data.items ?? []).slice().reverse()
  }
}

function download(archiveNo: string) {
  window.open(`${ENDPOINT}/${archiveNo}/download`, '_blank')
}

function downloadSegment(archiveNo: string, segment: string) {
  window.open(`${ENDPOINT}/${archiveNo}/download?segment=${encodeURIComponent(segment)}`, '_blank')
}

function openHandover(row: Summary) {
  handoverTarget.value = row
  handoverForm.value = { receiver: '', operator: '值班管理员', remark: '' }
  handoverMessage.value = ''
  handoverOk.value = false
  handoverOpen.value = true
}

async function submitHandover() {
  if (!handoverTarget.value || handoverBusy.value) return
  handoverBusy.value = true
  handoverMessage.value = ''
  try {
    const form = new FormData()
    form.append('receiver', handoverForm.value.receiver)
    form.append('operator', handoverForm.value.operator)
    form.append('remark', handoverForm.value.remark)
    const response = await request(`${ENDPOINT}/${handoverTarget.value.archive_no}/handover`, {
      method: 'POST',
      body: form,
    })
    const data = await response.json()
    handoverOk.value = Boolean(data.ok)
    handoverMessage.value = data.message
    if (data.ok) {
      await reload()
    }
  } catch (error) {
    handoverOk.value = false
    handoverMessage.value = error instanceof Error ? error.message : '移交失败'
  } finally {
    handoverBusy.value = false
  }
}

async function runConsistency() {
  const response = await request(`${ENDPOINT}/consistency`)
  if (response.ok) consistencyResult.value = await response.json()
}

onMounted(reload)
</script>

<style scoped>
.tabs { display: flex; gap: 4px; margin: 8px 0; }
.tab { border: 1px solid var(--border); background: #fff; border-radius: 6px 6px 0 0; padding: 6px 16px; cursor: pointer; }
.tab.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.tag { font-size: 12px; padding: 1px 8px; border-radius: 10px; }
.tag-open { background: #ecfdf3; color: #067647; }
.tag-lock { background: #f2f4f7; color: #475467; }
.page-actions { display: flex; gap: 8px; }

.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { background: #fff; border-radius: 10px; width: 720px; max-width: 92vw; max-height: 88vh; overflow: auto; padding: 20px 24px; }
.modal.narrow { width: 480px; }
.modal h3 { margin: 0 0 8px; }
.modal-tip { color: var(--muted); font-size: 12px; line-height: 1.7; background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; margin: 12px 0; }
.form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.form-grid label.full { grid-column: 1 / -1; }
.form-grid input, .form-grid select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; color: #1f2937; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.progress-box { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.progress-bar { flex: 1; height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden; }
.progress-bar div { height: 100%; background: var(--brand); transition: width 0.2s; }
.reject-box { background: #fef3f2; border: 1px solid #fda29b; border-radius: 6px; padding: 8px 12px; color: #b42318; font-size: 13px; }
.reject-box ul { margin: 6px 0 0; padding-left: 18px; }
.result-box { border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; margin: 8px 0; }
.ok-line { color: #067647; margin: 4px 0; font-size: 13px; }
.warn-line { color: #b54708; font-size: 12px; margin: 4px 0; }
.mini-table { margin: 8px 0; font-size: 12px; }
.diff-add { color: #067647; }
.diff-over { color: #b54708; }
.diff-del { color: #b42318; }

.drawer-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); z-index: 40; display: flex; justify-content: flex-end; }
.drawer { width: 760px; max-width: 94vw; background: #fff; height: 100%; overflow: auto; padding: 20px 24px; }
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.drawer h4 { margin: 18px 0 8px; }
.drawer-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.hash-cell { font-family: monospace; color: var(--muted); }
.version-card { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px; }
.version-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); }
.version-remark { font-size: 13px; margin: 4px 0; }
.version-diff { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; background: #f8fafc; border: 1px solid var(--border); border-radius: 10px; padding: 1px 10px; }
</style>
