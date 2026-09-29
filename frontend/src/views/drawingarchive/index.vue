<template>
  <section class="page" data-module="drawingarchive">
    <header class="page-head">
      <div>
        <h2>竣工图纸档案</h2>
        <p class="page-desc">
          整套图纸一次上传生成档案编号，按工程段打包下载，资料移交整体转给接收人；
          上传失败不留半份数据，重复上传按编号覆盖升版并逐张说明。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openUpload">上传整套图纸</button>
        <button class="btn" type="button" @click="loadConsistency">重新核对台账</button>
      </div>
    </header>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.badge !== undefined" class="tab-badge">{{ tab.badge }}</span>
      </button>
    </div>

    <template v-if="activeTab === 'archives'">
      <div class="stat-row">
        <article v-for="item in stats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>档案编号 / 工程名称</span>
          <input v-model="filters.keyword" placeholder="如 JG- 或工程名" />
        </label>
        <label class="filter-item">
          <span>档案状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option value="在库">在库</option>
            <option value="已移交">已移交</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>档案编号</th>
            <th>工程名称</th>
            <th>版本</th>
            <th>工程段/图纸</th>
            <th>状态</th>
            <th>接收人</th>
            <th>最近更新</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.archive_no">
            <td>{{ row.archive_no }}</td>
            <td>{{ row.project_name }}</td>
            <td>V{{ row.version }}</td>
            <td>{{ row.section_count }} 段 / {{ row.drawing_count }} 张</td>
            <td>
              <span class="status-tag" :class="row.status === '已移交' ? 'locked' : 'inhand'">
                {{ row.status }}
              </span>
            </td>
            <td>{{ row.receiver ?? '—' }}<template v-if="row.handed_at"><br /><small>{{ row.handed_at }}</small></template></td>
            <td>{{ row.updated_at }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openDetail(row.archive_no)">查看档案</button>
              <a class="link" :href="`${ENDPOINT}/${row.archive_no}/download`">下载整套</a>
              <button
                v-if="row.status !== '已移交'"
                class="link"
                type="button"
                @click="openHandover(row)"
              >
                办理移交
              </button>
              <span v-else class="readonly-hint">只读</span>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="8" class="empty-state">暂无图纸档案，点右上角“上传整套图纸”建档</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ total }} 份图纸档案</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>

    <template v-else-if="activeTab === 'handovers'">
      <table class="data-table">
        <thead>
          <tr>
            <th>移交序号</th>
            <th>档案编号</th>
            <th>工程名称</th>
            <th>移交版本</th>
            <th>图纸张数</th>
            <th>接收人</th>
            <th>移交人</th>
            <th>移交时间</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in handovers" :key="item.id">
            <td>{{ item.id }}</td>
            <td><button class="link" type="button" @click="openDetail(item.archive_no)">{{ item.archive_no }}</button></td>
            <td>{{ item.project_name }}</td>
            <td>V{{ item.version }}</td>
            <td>{{ item.drawing_count }} 张 / {{ item.section_count }} 段</td>
            <td>{{ item.receiver }}</td>
            <td>{{ item.operator }}</td>
            <td>{{ item.handed_at }}</td>
            <td>{{ item.remark ?? '—' }}</td>
          </tr>
          <tr v-if="!handovers.length">
            <td colspan="9" class="empty-state">尚未办理过移交</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>移交清单共 {{ handovers.length }} 条，每条对应一份已整体移交档案的版本快照</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>

    <template v-else>
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">档案总数</span>
          <strong class="stat-value">{{ consistency?.archive_total ?? '—' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">在库档案</span>
          <strong class="stat-value">{{ consistency?.in_hand_total ?? '—' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已移交档案</span>
          <strong class="stat-value">{{ consistency?.handed_archive_total ?? '—' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">移交清单条数</span>
          <strong class="stat-value">{{ consistency?.handover_list_total ?? '—' }}</strong>
        </article>
      </div>

      <p class="consistency-verdict" :class="consistency?.consistent ? 'ok' : 'bad'">
        {{ !consistency
          ? '尚未核对'
          : consistency.consistent
            ? '核对通过：档案总数与移交清单一一对应，移交后内容未发生变化'
            : '核对发现不一致：以下档案需要核查' }}
      </p>

      <table v-if="consistency" class="data-table">
        <thead>
          <tr>
            <th>档案编号</th>
            <th>工程名称</th>
            <th>接收人</th>
            <th>移交时间</th>
            <th>移交版本</th>
            <th>核对结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in consistency.details" :key="item.archive_no">
            <td>{{ item.archive_no }}</td>
            <td>{{ item.project_name }}</td>
            <td>{{ item.receiver }}</td>
            <td>{{ item.handed_at }}</td>
            <td>V{{ item.version }}</td>
            <td>
              <span v-if="item.ok" class="status-tag inhand">一致</span>
              <ul v-else class="problem-list">
                <li v-for="problem in item.problems" :key="problem">{{ problem }}</li>
              </ul>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 上传弹窗 -->
    <div v-if="uploadOpen" class="modal-mask" @click.self="closeUpload">
      <div class="modal">
        <h3>上传整套图纸</h3>
        <div class="upload-rules">
          <p>压缩包结构要求（不符合将整包退回）：</p>
          <pre>唯一根目录/（如：XX号线信号工程/）
├── 工程段目录/（如：正线段/、车辆段/）
│   ├── 图纸文件（.pdf/.dwg/.dxf/.png/.jpg）
│   └── …
└── 档案清单.json（回传下载包时自动携带，请勿手改）</pre>
        </div>
        <label class="form-row">
          <span>工程名称（首次建档使用；回传覆盖时以档案编号为准）</span>
          <input v-model="uploadForm.projectName" placeholder="如：XX号线信号系统工程" />
        </label>
        <label class="form-row">
          <span>变更说明</span>
          <input v-model="uploadForm.remark" placeholder="如：按评审意见换版，记录到本版变更记录" />
        </label>
        <label class="form-row file-row">
          <span>选择 ZIP 压缩包</span>
          <input ref="fileInput" type="file" accept=".zip" @change="onFilePicked" />
        </label>
        <p v-if="pickedFile" class="picked-file">
          已选择：{{ pickedFile.name }}（{{ formatSize(pickedFile.size) }}）
        </p>

        <div v-if="uploadRejected" class="reject-box">
          <strong>压缩包结构不符合要求，整包退回，未写入任何数据：</strong>
          <ul>
            <li v-for="reason in uploadRejected" :key="reason">{{ reason }}</li>
          </ul>
        </div>
        <div v-if="uploadDone" class="upload-done">
          <strong>{{ uploadDone.message }}</strong>
          <template v-if="uploadDone.result === 'updated'">
            <p v-if="uploadDone.overwrites.length">
              覆盖图纸（{{ uploadDone.overwrites.length }} 张）：
            </p>
            <ul>
              <li v-for="item in uploadDone.overwrites" :key="item.drawing">
                {{ item.drawing }}
                <small>{{ item.old_sha256.slice(0, 8) }} → {{ item.new_sha256.slice(0, 8) }}</small>
              </li>
            </ul>
            <p v-if="uploadDone.added.length">新增 {{ uploadDone.added.length }} 张：{{ uploadDone.added.join('、') }}</p>
            <p v-if="uploadDone.removed.length">删除 {{ uploadDone.removed.length }} 张：{{ uploadDone.removed.join('、') }}</p>
          </template>
        </div>

        <footer class="modal-foot">
          <span v-if="uploading" class="uploading-hint">正在上传并校验，整包通过后才会提交…</span>
          <button class="btn" type="button" :disabled="uploading" @click="closeUpload">关闭</button>
          <button class="btn primary" type="button" :disabled="uploading || !pickedFile" @click="submitUpload">
            {{ uploading ? '上传中…' : '开始上传' }}
          </button>
        </footer>
      </div>
    </div>

    <!-- 移交弹窗 -->
    <div v-if="handoverOpen" class="modal-mask" @click.self="closeHandover">
      <div class="modal">
        <h3>办理整体移交</h3>
        <p class="handover-meta">
          档案 {{ handoverTarget?.archive_no }} · {{ handoverTarget?.project_name }} ·
          当前 V{{ handoverTarget?.version }}（{{ handoverTarget?.drawing_count }} 张）
        </p>
        <p class="handover-tip">移交后档案整体转给接收人，状态转为“已移交”，只能查看不能改动。</p>
        <label class="form-row">
          <span>接收人 / 接收单位</span>
          <input v-model="handoverForm.receiver" placeholder="如：运营分公司-李工" />
        </label>
        <label class="form-row">
          <span>移交备注</span>
          <input v-model="handoverForm.remark" placeholder="如：竣工资料整体移交" />
        </label>
        <footer class="modal-foot">
          <span v-if="handoverError" class="error-text">{{ handoverError }}</span>
          <button class="btn" type="button" @click="closeHandover">取消</button>
          <button class="btn primary" type="button" :disabled="handoverSubmitting" @click="submitHandover">
            确认移交
          </button>
        </footer>
      </div>
    </div>

    <!-- 档案详情抽屉 -->
    <div v-if="detail" class="drawer-mask" @click.self="detail = null">
      <aside class="drawer">
        <header class="drawer-head">
          <div>
            <h3>{{ detail.archive_no }} · {{ detail.project_name }}</h3>
            <p>
              V{{ detail.version }} ·
              <span class="status-tag" :class="detail.read_only ? 'locked' : 'inhand'">{{ detail.status }}</span>
              · {{ detail.section_count }} 个工程段 / {{ detail.drawing_count }} 张图纸
              · 指纹 {{ detail.fingerprint.slice(0, 12) }}
            </p>
          </div>
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </header>

        <div v-if="detail.handover" class="handover-banner">
          已于 {{ detail.handover.handed_at }} 整体移交接收人 {{ detail.handover.receiver }}
          （移交版本 V{{ detail.handover.version }}），档案只读，任何改动请求都会被拒绝。
        </div>

        <div class="drawer-actions">
          <a class="btn" :href="`${ENDPOINT}/${detail.archive_no}/download`">下载整套包</a>
          <button
            v-if="!detail.read_only"
            class="btn primary"
            type="button"
            @click="openHandover(detail); detail = null"
          >
            办理移交
          </button>
          <button class="btn" type="button" @click="loadRevisions(detail.archive_no)">
            刷新变更记录
          </button>
        </div>

        <h4>工程段与图纸</h4>
        <div v-for="section in detail.sections" :key="section.name" class="section-block">
          <div class="section-head">
            <strong>{{ section.name }}</strong>
            <span>{{ section.drawing_count }} 张</span>
            <a class="link" :href="`${ENDPOINT}/${detail.archive_no}/sections/${encodeURIComponent(section.name)}/download`">
              按段打包下载
            </a>
          </div>
          <ul class="drawing-list">
            <li v-for="drawing in section.drawings" :key="drawing.name">
              <a
                class="link"
                :href="`${ENDPOINT}/${detail.archive_no}/sections/${encodeURIComponent(section.name)}/drawings/${encodeURIComponent(drawing.name)}`"
              >
                {{ drawing.name }}
              </a>
              <small>{{ formatSize(drawing.size) }} · {{ drawing.sha256.slice(0, 10) }}</small>
            </li>
          </ul>
        </div>

        <h4>变更记录（每一版留痕）</h4>
        <ol class="revision-list">
          <li v-for="rev in revisions" :key="rev.version">
            <div class="revision-head">
              <strong>V{{ rev.version }} · {{ rev.change_type }}</strong>
              <span>{{ rev.changed_at }} · {{ rev.operator }}</span>
            </div>
            <p v-if="rev.remark">说明：{{ rev.remark }}</p>
            <p>
              新增 {{ rev.added_count }} 张 · 覆盖 {{ rev.overwrite_count }} 张 ·
              删除 {{ rev.removed_count }} 张 · 归档时共 {{ rev.drawing_count }} 张
            </p>
            <ul v-if="rev.overwrites.length" class="overwrite-list">
              <li v-for="item in rev.overwrites" :key="item.drawing">
                覆盖：{{ item.drawing }}
                <small>{{ item.old_sha256.slice(0, 8) }} → {{ item.new_sha256.slice(0, 8) }}</small>
              </li>
            </ul>
            <p class="fingerprint-line">
              指纹：{{ (rev.before_fingerprint ?? '—').slice(0, 10) }}
              → {{ rev.after_fingerprint.slice(0, 10) }}
            </p>
          </li>
        </ol>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/drawing-archives'

interface ArchiveSummary {
  archive_no: string
  project_name: string
  version: number
  status: string
  section_count: number
  drawing_count: number
  fingerprint: string
  created_by: string
  created_at: string
  updated_at: string
  receiver: string | null
  handed_at: string | null
}

interface DrawingItem {
  name: string
  size: number
  sha256: string
}

interface ArchiveDetail extends ArchiveSummary {
  root_name: string
  read_only: boolean
  sections: { name: string; drawing_count: number; drawings: DrawingItem[] }[]
  latest_revision: Revision
  handover: {
    receiver: string
    operator: string
    handed_at: string
    version: number
    remark: string | null
  } | null
}

interface Revision {
  version: number
  change_type: string
  operator: string
  remark: string | null
  added: string[]
  removed: string[]
  added_count: number
  overwrite_count: number
  removed_count: number
  overwrites: { drawing: string; old_sha256: string; new_sha256: string; old_size: number; new_size: number }[]
  drawing_count: number
  before_fingerprint: string | null
  after_fingerprint: string
  changed_at: string
}

interface Handover {
  id: number
  archive_no: string
  project_name: string
  version: number
  drawing_count: number
  section_count: number
  receiver: string
  operator: string
  remark: string | null
  handed_at: string
}

interface ConsistencyDetail {
  archive_no: string
  project_name: string
  receiver: string
  handed_at: string
  version: number
  ok: boolean
  problems: string[]
}

interface ConsistencyReport {
  consistent: boolean
  archive_total: number
  in_hand_total: number
  handed_archive_total: number
  handover_list_total: number
  details: ConsistencyDetail[]
}

interface UploadOverwrite {
  drawing: string
  old_sha256: string
  new_sha256: string
  old_size: number
  new_size: number
}

interface UploadResult {
  ok: boolean
  result: 'created' | 'updated' | 'unchanged'
  message: string
  overwrites: UploadOverwrite[]
  added: string[]
  removed: string[]
  unchanged: string[]
  archive: ArchiveDetail
}

const activeTab = ref<'archives' | 'handovers' | 'consistency'>('archives')
const rows = ref<ArchiveSummary[]>([])
const total = ref(0)
const handovers = ref<Handover[]>([])
const consistency = ref<ConsistencyReport | null>(null)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '' })

const detail = ref<ArchiveDetail | null>(null)
const revisions = ref<Revision[]>([])

const uploadOpen = ref(false)
const uploading = ref(false)
const pickedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploadForm = ref({ projectName: '', remark: '' })
const uploadRejected = ref<string[]>([])
const uploadDone = ref<UploadResult | null>(null)

const handoverOpen = ref(false)
const handoverSubmitting = ref(false)
const handoverTarget = ref<ArchiveSummary | ArchiveDetail | null>(null)
const handoverForm = ref({ receiver: '', remark: '' })
const handoverError = ref('')

const stats = computed(() => [
  { label: '档案总数', value: total.value },
  { label: '在库档案', value: rows.value.filter((item) => item.status === '在库').length },
  { label: '已移交档案', value: rows.value.filter((item) => item.status === '已移交').length },
  {
    label: '图纸总张数',
    value: rows.value.reduce((sum, item) => sum + item.drawing_count, 0),
  },
])

const tabs = computed(() => [
  { key: 'archives' as const, label: '图纸档案' },
  { key: 'handovers' as const, label: '移交清单', badge: handovers.value.length },
  { key: 'consistency' as const, label: '台账核对' },
])

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('档案列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '档案列表读取失败'
  }
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

async function loadHandovers() {
  try {
    const response = await request(`${ENDPOINT}/handovers`)
    if (!response.ok) throw new Error('移交清单读取失败')
    const payload = await response.json()
    handovers.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '移交清单读取失败'
  }
}

async function loadConsistency() {
  activeTab.value = 'consistency'
  try {
    const response = await request(`${ENDPOINT}/consistency`)
    if (!response.ok) throw new Error('台账核对失败')
    consistency.value = (await response.json()) as ConsistencyReport
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '台账核对失败'
  }
}

function switchTab(key: 'archives' | 'handovers' | 'consistency') {
  activeTab.value = key
  errorMessage.value = ''
  if (key === 'handovers') void loadHandovers()
  if (key === 'consistency') void loadConsistency()
}

function openUpload() {
  uploadOpen.value = true
  uploadRejected.value = []
  uploadDone.value = null
  pickedFile.value = null
  uploadForm.value = { projectName: '', remark: '' }
}

function closeUpload() {
  uploadOpen.value = false
}

function onFilePicked(event: Event) {
  const target = event.target as HTMLInputElement
  pickedFile.value = target.files?.[0] ?? null
  uploadRejected.value = []
  uploadDone.value = null
}

async function submitUpload() {
  if (!pickedFile.value) return
  uploading.value = true
  uploadRejected.value = []
  uploadDone.value = null
  try {
    // 上传是 multipart，不能带统一封装里的 application/json 头。
    const form = new FormData()
    form.append('package', pickedFile.value)
    if (uploadForm.value.projectName) form.append('project_name', uploadForm.value.projectName)
    if (uploadForm.value.remark) form.append('remark', uploadForm.value.remark)
    form.append('operator', '值班管理员')
    const response = await fetch(`${ENDPOINT}/upload`, { method: 'POST', body: form })
    const payload = await response.json()
    if (response.status === 422) {
      uploadRejected.value = payload.detail?.reasons ?? [payload.detail?.message ?? '整包退回']
      return
    }
    if (response.status === 409) {
      uploadRejected.value = [payload.detail ?? '该档案已移交只读，不能改动']
      return
    }
    if (!response.ok) {
      uploadRejected.value = [payload.detail ?? '上传失败，未写入任何数据']
      return
    }
    uploadDone.value = payload
    await reload()
  } catch (error) {
    uploadRejected.value = [
      `上传中断或网络异常，服务端未提交任何数据：${error instanceof Error ? error.message : '未知错误'}`,
    ]
  } finally {
    uploading.value = false
  }
}

async function openDetail(archiveNo: string) {
  detail.value = null
  revisions.value = []
  try {
    const response = await request(`${ENDPOINT}/${archiveNo}`)
    if (!response.ok) throw new Error('档案详情读取失败')
    detail.value = await response.json()
    await loadRevisions(archiveNo)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '档案详情读取失败'
  }
}

async function loadRevisions(archiveNo: string) {
  try {
    const response = await request(`${ENDPOINT}/${archiveNo}/revisions`)
    if (!response.ok) throw new Error('变更记录读取失败')
    const payload = await response.json()
    revisions.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变更记录读取失败'
  }
}

function openHandover(row: ArchiveSummary | ArchiveDetail) {
  handoverTarget.value = row
  handoverForm.value = { receiver: '', remark: '' }
  handoverError.value = ''
  handoverOpen.value = true
}

function closeHandover() {
  handoverOpen.value = false
  handoverTarget.value = null
}

async function submitHandover() {
  if (!handoverTarget.value) return
  handoverSubmitting.value = true
  handoverError.value = ''
  try {
    const response = await request(`${ENDPOINT}/handovers`, {
      method: 'POST',
      body: JSON.stringify({
        archive_no: handoverTarget.value.archive_no,
        receiver: handoverForm.value.receiver,
        remark: handoverForm.value.remark || null,
      }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      handoverError.value = payload.detail ?? '移交未生效'
      return
    }
    closeHandover()
    await reload()
    await loadHandovers()
    activeTab.value = 'handovers'
  } catch (error) {
    handoverError.value = error instanceof Error ? error.message : '移交请求失败'
  } finally {
    handoverSubmitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.tab-bar { display: flex; gap: 8px; margin: 8px 0 12px; }
.tab-btn {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 6px;
  padding: 6px 14px;
  cursor: pointer;
  font-size: 13px;
}
.tab-btn.active { border-color: var(--brand); color: var(--brand); font-weight: 600; }
.tab-badge {
  display: inline-block;
  margin-left: 6px;
  background: #eef2ff;
  color: var(--brand);
  border-radius: 10px;
  padding: 0 7px;
  font-size: 12px;
}
.status-tag { border-radius: 10px; padding: 2px 10px; font-size: 12px; }
.status-tag.inhand { background: #ecfdf3; color: #027a48; }
.status-tag.locked { background: #f2f4f7; color: #475467; }
.readonly-hint { color: var(--muted); font-size: 12px; }

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
}
.modal {
  width: 620px;
  max-height: 86vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal h3 { margin: 0 0 10px; }
.upload-rules {
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--muted);
}
.upload-rules pre { white-space: pre-wrap; margin: 6px 0 0; font-size: 12px; }
.form-row { display: block; margin: 10px 0; font-size: 13px; }
.form-row span { display: block; color: var(--muted); margin-bottom: 4px; }
.form-row input { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.file-row input { border: none; padding: 0; }
.picked-file { font-size: 12px; color: var(--muted); }
.reject-box {
  margin-top: 10px;
  border: 1px solid #fecdca;
  background: #fef3f2;
  border-radius: 8px;
  padding: 10px 12px;
  color: #b42318;
  font-size: 13px;
}
.reject-box ul { margin: 6px 0 0; padding-left: 18px; }
.upload-done {
  margin-top: 10px;
  border: 1px solid #abefc6;
  background: #ecfdf3;
  border-radius: 8px;
  padding: 10px 12px;
  color: #05603a;
  font-size: 13px;
}
.upload-done ul { margin: 6px 0; padding-left: 18px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; align-items: center; margin-top: 14px; }
.uploading-hint, .handover-tip { font-size: 12px; color: var(--muted); margin-right: auto; }
.handover-meta { font-size: 13px; font-weight: 600; margin: 4px 0; }

.drawer-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.4); z-index: 25; }
.drawer {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 640px;
  max-width: 92vw;
  background: #fff;
  padding: 18px 20px;
  overflow: auto;
}
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.drawer-head h3 { margin: 0 0 4px; font-size: 16px; }
.drawer-head p { margin: 0; color: var(--muted); font-size: 12px; }
.drawer-actions { display: flex; gap: 8px; margin: 12px 0; }
.handover-banner {
  background: #f2f4f7;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
  color: #344054;
  margin-bottom: 10px;
}
.section-block { border: 1px solid var(--border); border-radius: 8px; margin: 8px 0; }
.section-head {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  background: #f8fafc;
  font-size: 13px;
}
.section-head span { color: var(--muted); font-size: 12px; }
.section-head a { margin-left: auto; }
.drawing-list { list-style: none; margin: 0; padding: 6px 12px; }
.drawing-list li { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; }
.drawing-list small { color: var(--muted); }
.revision-list { padding-left: 18px; font-size: 13px; }
.revision-list li { margin-bottom: 10px; }
.revision-head { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; }
.revision-list p { margin: 2px 0; }
.overwrite-list { margin: 4px 0; padding-left: 18px; }
.overwrite-list small { color: var(--muted); margin-left: 6px; }
.fingerprint-line { color: var(--muted); font-size: 12px; }
.consistency-verdict { font-weight: 600; }
.consistency-verdict.ok { color: #027a48; }
.consistency-verdict.bad { color: #b42318; }
.problem-list { margin: 0; padding-left: 16px; color: #b42318; }
</style>
