# 轨道交通信号检修管理平台

面向轨道交通信号设备日常巡检、故障处置、天窗修作业、器材检修与联锁试验的一体化信号检修管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 联锁管理 | `interlock` | 联锁道岔 | 道岔编号、所属车站、道岔类型 |
| 轨道电路 | `trackcircuit` | 轨道电路 | 区段编号、所属区间、载频类型 |
| 信号机 | `signal` | 信号机 | 信号机编号、所属车站、信号机类型 |
| 转辙机 | `pointmachine` | 转辙机 | 转辙机编号、所属道岔、转辙机型号 |
| 信号电缆 | `cable` | 信号电缆 | 电缆编号、起止站点、电缆芯数 |
| 信号电源 | `powersupply` | 电源屏 | 电源屏编号、所属车站、输入电压 |
| 车载设备 | `atp` | 车载ATP | 设备编号、所属列车、设备型号 |
| 应答器 | `balise` | 应答器 | 应答器编号、所在位置、报文版本 |
| 计轴设备 | `axlecounter` | 计轴器 | 计轴器编号、所属区间、检测磁头 |
| 调度中心 | `dispatchcenter` | 调度台 | 调度台编号、管辖范围、显示设备 |
| 天窗修作业 | `maintenancewindow` | 天窗计划 | 计划编号、作业日期、作业区间 |
| 继电器检修 | `relay` | 继电器 | 继电器编号、继电器型号、所属设备 |
| 熔断器管理 | `fuse` | 熔断器 | 熔断器编号、额定电流、安装位置 |
| 防雷元件 | `lightning` | 防雷元件 | 元件编号、安装位置、防护等级 |
| 应急备品 | `emergencyresp` | 应急备品 | 备品编号、备品名称、规格型号 |
| 联锁试验 | `testrecord` | 试验记录 | 试验编号、试验日期、试验车站 |
| 信号故障 | `fault` | 故障记录 | 故障编号、发生时间、故障设备 |
| 检修工具 | `tool` | 检修工具 | 工具编号、工具名称、规格型号 |
| 技术规章 | `regulation` | 技术规章 | 规章编号、规章名称、适用专业 |
| 技能培训 | `training` | 培训记录 | 培训编号、培训主题、培训对象 |
| 竣工图纸档案 | `drawingarchive` | 图纸档案、移交记录 | 档案编号、工程名称、版本、工程分段、移交状态 |

## 竣工图纸档案

围绕「整套上传 → 按编号覆盖 → 分段下载 → 整体移交」的资料移交流程：

- **上传与编号**：`POST /api/drawing-archives` 上传整套 ZIP。新档案生成
  `JGTZ-年份-序号` 编号；填写已有档案编号即按编号覆盖，返回新增/覆盖/删除的逐张清单。
- **整包退回**：压缩包必须是「唯一顶层文件夹 / 工程分段目录 / 图纸文件」结构，
  根部可带 `archive-manifest.json`。路径穿越、双顶层、散放文件、非法后缀、重名、
  CRC 损坏等问题会累积后一次性以 422 退回并写明全部原因。
- **不留半份数据**：上传字节先落临时文件，全量解析校验通过后才在锁内提交；
  中断、中止、校验失败都会清理临时文件，档案仓库不变。
- **回传对账**：下载包（支持 `?segment=` 按工程分段打包）根部带档案编号、版本与
  每张图纸 sha256 的清单；同一压缩包再传一次，编号与版本原样对上且不升版，
  内容被改过则按实际内容升版并给出清单版本提示。
- **移交锁定**：`POST /api/drawing-archives/{编号}/handover` 整体移交给接收人，
  成功后档案只能查看与下载；`GET /handover-records` 是移交清单。
- **变更留痕**：每一版记录新增、覆盖（含新旧 sha256）、删除图纸、操作人与说明，
  可通过 `GET /api/drawing-archives/{编号}/versions` 查询。
- **一致性核对**：`GET /api/drawing-archives/consistency` 核对档案总数、已移交数、
  移交清单条数、版本与图纸数是否处处一致。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
