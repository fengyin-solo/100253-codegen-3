"""竣工图纸档案业务规则。

职责边界：
- 整套图纸以 ZIP 上传，先全量解析、全量校验，通过后在锁内一次性提交，
  上传失败或中断时全局仓库不会留下半份数据（临时文件由路由层负责清理）。
- 新档案生成档案编号；重复上传时按档案编号覆盖，并逐张说明新增/覆盖/删除。
- 结构不符合要求的压缩包整包退回，累积全部问题一次性写明原因。
- 下载包内置 archive-manifest.json，回传同一套图纸时编号与版本必须对得上。
- 档案移交后锁定，只能查看与下载；每一版都保留变更记录。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import threading
import zipfile
import zlib
from datetime import datetime
from typing import Any

# 允许进档案的图纸/资料后缀；其他后缀一律视为结构不符。
ALLOWED_EXTS = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
# 整包大小保护，避免演示环境被超大压缩包撑爆。
MAX_MEMBER_SIZE = 200 * 1024 * 1024
MAX_TOTAL_SIZE = 1024 * 1024 * 1024
MANIFEST_NAME = "archive-manifest.json"
STATUS_PENDING = "待移交"
STATUS_HANDED = "已移交"


class ServiceError(Exception):
    """业务失败：携带 HTTP 状态码与可直接展示给前端的载荷。"""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        super().__init__(str(payload))
        self.status_code = status_code
        self.payload = payload


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """还原压缩包内的中文文件名。

    Windows 资源管理器打出的包常用 GBK 但只打了 CP437 标记，Python 会按 CP437
    解出乱码；这里按标记位回退成 GBK 重新解码。
    """
    raw = info.filename
    if info.flag_bits & 0x800:  # UTF-8 标记位
        return raw.replace("\\", "/")
    try:
        return raw.encode("cp437").decode("gbk").replace("\\", "/")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw.replace("\\", "/")


class DrawingArchiveService:
    """图纸档案仓库：档案、版本快照、移交清单都收在同一个锁内维护。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._archives: list[dict[str, Any]] = []
        self._handover_records: list[dict[str, Any]] = []
        # 内容寻址的图纸字节仓：同一字节只存一份，删版本也不影响在档图纸。
        self._blobs: dict[str, bytes] = {}
        self._seq: dict[int, int] = {}
        self._next_id = 1
        self._seed_demo_archives()

    # ------------------------------------------------------------------ 示例数据

    def _seed_demo_archives(self) -> None:
        """预置两份档案：一份待移交、一份已移交锁定，页面打开即可看到完整状态。"""
        demo_packages = [
            {
                "project_name": "3号线信号系统竣工图",
                "status": STATUS_PENDING,
                "files": {
                    "正线区间/联锁表.pdf": b"%PDF-1.4 demo: interlocking table",
                    "正线区间/信号机布置图.dwg": b"DWG-DEMO signal layout",
                    "车辆段/电缆径路图.pdf": b"%PDF-1.4 demo: cable route",
                },
                "handover": None,
            },
            {
                "project_name": "7号线一期信号竣工图",
                "status": STATUS_HANDED,
                "files": {
                    "控制中心/调度台盘面图.pdf": b"%PDF-1.4 demo: dispatch panel",
                },
                "handover": {"receiver": "城建档案馆 张工", "remark": "一期工程资料移交"},
            },
        ]
        for package in demo_packages:
            drawings: dict[str, dict[str, Any]] = {}
            for path, content in package["files"].items():
                segment, name = path.split("/", 1)
                sha = hashlib.sha256(content).hexdigest()
                self._blobs[sha] = content
                drawings[path] = {"segment": segment, "name": name, "sha256": sha, "size": len(content)}
            archive = self._new_archive_locked(package["project_name"], drawings, "系统初始化", "示例档案")
            if package["status"] == STATUS_HANDED:
                handover = package["handover"] or {}
                archive["status"] = STATUS_HANDED
                self._handover_records.append(self._build_handover_record_locked(
                    archive, handover.get("receiver", "接收人"), "系统初始化", handover.get("remark", "")
                ))

    # ------------------------------------------------------------------ 列表/明细

    def list_archives(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [dict(self._summary_locked(a)) for a in self._archives]
        if keyword:
            kw = keyword.strip()
            rows = [r for r in rows if kw in r["archive_no"] or kw in r["project_name"]]
        if status:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: r["id"], reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "archive_total": len(self._archives),
                "pending_total": sum(1 for a in self._archives if a["status"] == STATUS_PENDING),
                "handed_total": sum(1 for a in self._archives if a["status"] == STATUS_HANDED),
                "drawing_total": sum(len(a["files"]) for a in self._archives),
            }

    def _find_locked(self, archive_no: str) -> dict[str, Any] | None:
        archive_no = archive_no.strip()
        for archive in self._archives:
            if archive["archive_no"] == archive_no:
                return archive
        return None

    def get_archive(self, archive_no: str) -> dict[str, Any]:
        with self._lock:
            archive = self._find_locked(archive_no)
            if archive is None:
                raise ServiceError(404, {"code": "not_found", "message": f"档案 {archive_no} 不存在"})
            return self._detail_locked(archive)

    def list_versions(self, archive_no: str) -> list[dict[str, Any]]:
        with self._lock:
            archive = self._find_locked(archive_no)
            if archive is None:
                raise ServiceError(404, {"code": "not_found", "message": f"档案 {archive_no} 不存在"})
            return [dict(v) for v in archive["versions"]]

    def _summary_locked(self, archive: dict[str, Any]) -> dict[str, Any]:
        segments = {item["segment"] for item in archive["files"].values()}
        return {
            "id": archive["id"],
            "archive_no": archive["archive_no"],
            "project_name": archive["project_name"],
            "current_version": archive["current_version"],
            "status": archive["status"],
            "file_count": len(archive["files"]),
            "segment_count": len(segments),
            "created_at": archive["created_at"],
            "updated_at": archive["updated_at"],
        }

    def _detail_locked(self, archive: dict[str, Any]) -> dict[str, Any]:
        detail = self._summary_locked(archive)
        segments: dict[str, dict[str, Any]] = {}
        for path, item in sorted(archive["files"].items()):
            bucket = segments.setdefault(item["segment"], {"name": item["segment"], "file_count": 0, "total_size": 0})
            bucket["file_count"] += 1
            bucket["total_size"] += item["size"]
        detail["segments"] = [segments[name] for name in sorted(segments)]
        detail["files"] = [
            {
                "path": path,
                "segment": item["segment"],
                "name": item["name"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for path, item in sorted(archive["files"].items())
        ]
        detail["handover"] = archive["handover"]
        detail["locked"] = archive["status"] == STATUS_HANDED
        return detail

    # ------------------------------------------------------------------ 上传接收

    def receive_package(
        self,
        zip_path: str,
        *,
        form_archive_no: str,
        project_name: str,
        operator: str,
        remark: str,
    ) -> dict[str, Any]:
        """接收一整套图纸：解析校验 → 锁内提交。任何一步失败都不写档案仓库。"""
        drawings, manifest, errors, warnings = self._inspect_package(zip_path)
        if errors:
            # 结构不符合要求：整包退回，所有原因一次写明。
            raise ServiceError(422, {"code": "invalid_package", "reasons": errors})
        return self._commit_locked(
            drawings,
            manifest,
            warnings,
            form_archive_no=form_archive_no.strip(),
            project_name=project_name.strip(),
            operator=operator.strip() or "值班管理员",
            remark=remark.strip(),
        )

    def _inspect_package(
        self, zip_path: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None, list[str], list[str]]:
        drawings: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        warnings: list[str] = []
        manifest: dict[str, Any] | None = None

        try:
            package = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError):
            return drawings, None, ["文件不是有效的 ZIP 压缩包（可能上传中断导致损坏），整包退回"], warnings

        with package:
            members = [info for info in package.infolist() if not info.is_dir()]
            if not members:
                return drawings, None, ["压缩包内没有任何文件，空包整包退回"], warnings

            top_dirs = {_decode_member_name(info).split("/")[0] for info in members}
            if len(top_dirs) != 1:
                errors.append(
                    f"压缩包必须有且仅有一个顶层文件夹，当前发现 {len(top_dirs)} 个：{'、'.join(sorted(top_dirs))}"
                )

            manifest_infos: list[zipfile.ZipInfo] = []
            total_size = 0
            seen_keys: dict[str, str] = {}  # 小写路径 → 原始路径，查同名大小写冲突

            for info in members:
                name = _decode_member_name(info)
                raw_parts = name.split("/")
                parts = [part for part in raw_parts if part]
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                    errors.append(f"不允许绝对路径：{name}")
                    continue
                if len(parts) != len(raw_parts):
                    errors.append(f"存在空目录名或多余分隔符：{name}")
                    continue
                if any(part in ("..", ".") for part in parts):
                    errors.append(f"不允许出现 . 或 .. 路径：{name}")
                    continue
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode == stat.S_IFLNK:
                    errors.append(f"不允许软链接条目：{name}")
                    continue

                rel = parts[1:]  # 去掉顶层文件夹
                if len(rel) == 1 and rel[0] == MANIFEST_NAME:
                    manifest_infos.append(info)
                    continue
                if len(rel) == 1:
                    errors.append(f"顶层文件夹下只允许放「{MANIFEST_NAME}」和工程分段目录，散放文件：{parts[-1]}")
                    continue
                if len(rel) != 2:
                    errors.append(f"图纸必须放在「顶层文件夹/工程分段/图纸文件」两级目录内：{name}")
                    continue

                segment, file_name = rel
                if not segment.strip() or not file_name.strip():
                    errors.append(f"分段名或图纸名为空：{name}")
                    continue
                ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
                if ext not in ALLOWED_EXTS:
                    errors.append(f"不支持的图纸格式 {ext or '（无后缀）'}：{name}，允许：{sorted(ALLOWED_EXTS)}")
                    continue
                if info.file_size > MAX_MEMBER_SIZE:
                    errors.append(f"单张图纸超过 {MAX_MEMBER_SIZE // (1024 * 1024)}MB 上限：{name}")
                    continue
                total_size += info.file_size
                if total_size > MAX_TOTAL_SIZE:
                    errors.append(f"整包图纸超过 {MAX_TOTAL_SIZE // (1024 * 1024)}MB 上限，整包退回")
                    continue

                key = f"{segment}/{file_name}"
                clash = seen_keys.get(key.casefold())
                if clash is not None:
                    errors.append(f"包内存在重复图纸（路径仅大小写不同也算重复）：{clash} 与 {key}")
                    continue
                seen_keys[key.casefold()] = key

                # 读字节会触发 CRC 校验：上传中断造成的尾部损坏在这里暴露。
                try:
                    content = package.read(info)
                except (zipfile.BadZipFile, zlib.error, OSError) as exc:
                    errors.append(f"图纸 {key} 数据损坏（CRC 校验失败，疑似上传中断）：{exc}，整包退回")
                    continue
                sha = hashlib.sha256(content).hexdigest()
                drawings[key] = {
                    "segment": segment,
                    "name": file_name,
                    "sha256": sha,
                    "size": info.file_size,
                    "content": content,
                }

            if len(manifest_infos) > 1:
                errors.append(f"包内存在多个 {MANIFEST_NAME}，只允许在顶层文件夹根部放一个")
            elif manifest_infos:
                manifest, manifest_errors, manifest_warnings = self._parse_manifest(
                    package, manifest_infos[0], drawings
                )
                errors.extend(manifest_errors)
                warnings.extend(manifest_warnings)

        if not drawings and not any(MANIFEST_NAME in e for e in errors):
            errors.append("压缩包内没有任何符合要求的图纸文件，整包退回")
        return drawings, manifest, errors, warnings

    def _parse_manifest(
        self,
        package: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        drawings: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            manifest = json.loads(package.read(info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, [f"{MANIFEST_NAME} 不是合法的 UTF-8 JSON：{exc}，整包退回"], warnings
        if not isinstance(manifest, dict):
            return None, [f"{MANIFEST_NAME} 顶层必须是 JSON 对象，整包退回"], warnings

        for key in ("archive_no", "version", "files"):
            if key not in manifest:
                errors.append(f"{MANIFEST_NAME} 缺少必填字段「{key}」")
        if errors:
            return None, errors, warnings
        if not isinstance(manifest["archive_no"], str) or not manifest["archive_no"].strip():
            errors.append(f"{MANIFEST_NAME} 的 archive_no 必须是非空字符串")
        if not isinstance(manifest["version"], int) or isinstance(manifest["version"], bool) or manifest["version"] < 1:
            errors.append(f"{MANIFEST_NAME} 的 version 必须是不小于 1 的整数")
        files = manifest["files"]
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            errors.append(f"{MANIFEST_NAME} 的 files 必须是 {{图纸路径: sha256}} 的对象")
            return None, errors, warnings

        listed = set(files)
        actual = set(drawings)
        for missing in sorted(actual - listed):
            errors.append(f"{MANIFEST_NAME} 漏登图纸：{missing}")
        for extra in sorted(listed - actual):
            errors.append(f"{MANIFEST_NAME} 登记了包内不存在的图纸：{extra}")
        for path, sha in files.items():
            if path in drawings and not re.fullmatch(r"[0-9a-f]{64}", sha):
                errors.append(f"{MANIFEST_NAME} 中 {path} 的 sha256 格式不合法")
        # 哈希对不上：包被改过，不算结构问题，比对时按实际内容升级版本并给出提示。
        for path, sha in files.items():
            if path in drawings and re.fullmatch(r"[0-9a-f]{64}", sha) and sha != drawings[path]["sha256"]:
                warnings.append(f"清单中 {path} 的校验值与实际内容不一致，按实际图纸内容比对")
        return manifest, errors, warnings

    def _commit_locked(
        self,
        drawings: dict[str, dict[str, Any]],
        manifest: dict[str, Any] | None,
        warnings: list[str],
        *,
        form_archive_no: str,
        project_name: str,
        operator: str,
        remark: str,
    ) -> dict[str, Any]:
        with self._lock:
            manifest_no = str(manifest["archive_no"]).strip() if manifest else ""
            manifest_version = int(manifest["version"]) if manifest else None

            if form_archive_no and manifest_no and form_archive_no != manifest_no:
                raise ServiceError(400, {
                    "code": "archive_no_conflict",
                    "message": f"表单指定的档案编号 {form_archive_no} 与包内清单编号 {manifest_no} 不一致，未接收",
                })
            target_no = form_archive_no or manifest_no

            target = self._find_locked(target_no) if target_no else None
            if target_no and target is None:
                raise ServiceError(404, {
                    "code": "not_found",
                    "message": f"档案 {target_no} 不存在，无法按编号覆盖；新建档案请留空档案编号",
                })
            if target is not None and target["status"] == STATUS_HANDED:
                raise ServiceError(409, {
                    "code": "archive_locked",
                    "message": f"档案 {target_no} 已移交并锁定，只能查看与下载，不能改动",
                })

            old_files = target["files"] if target else {}
            added, overwritten, deleted, unchanged = self._diff_files(old_files, drawings)

            if manifest_version is not None and target is not None and manifest_version != target["current_version"]:
                warnings.append(
                    f"包内清单版本 v{manifest_version} 与服务端当前版本 v{target['current_version']} 不一致，"
                    f"已按图纸实际内容比对"
                )

            # 回传包内容完全一致：编号与版本必须对得上，不生成新版本。
            if not added and not overwritten and not deleted:
                if manifest_version is not None and manifest_version != target["current_version"]:
                    warnings.append(
                        f"包内清单版本 v{manifest_version} 与服务端版本 v{target['current_version']} 不一致，"
                        f"但图纸内容完全相同，仍按服务端版本返回"
                    )
                return {
                    "ok": True,
                    "changed": False,
                    "new_archive": False,
                    "archive_no": target["archive_no"],
                    "version": target["current_version"],
                    "manifest_version": manifest_version,
                    "project_name": target["project_name"],
                    "file_count": len(drawings),
                    "added": [],
                    "overwritten": [],
                    "deleted": [],
                    "unchanged": unchanged,
                    "warnings": warnings,
                    "message": f"回传图纸与服务端档案完全一致：编号 {target['archive_no']}、"
                    f"版本 v{target['current_version']} 与清单对得上，未生成新版本",
                }

            if target is None:
                if not project_name:
                    raise ServiceError(400, {
                        "code": "missing_project_name",
                        "message": "新建档案必须填写工程名称",
                    })
                target = self._new_archive_locked(project_name, drawings, operator, remark)
                for item in drawings.values():
                    self._blobs.setdefault(item["sha256"], item["content"])
                message = (
                    f"新档案已接收：编号 {target['archive_no']}，版本 v1，"
                    f"共收录 {len(drawings)} 张图纸"
                )
                new_archive = True
            else:
                new_archive = False
                version = target["current_version"] + 1
                for item in drawings.values():
                    self._blobs.setdefault(item["sha256"], item["content"])
                target["files"] = {
                    path: {k: v for k, v in item.items() if k != "content"} for path, item in drawings.items()
                }
                if project_name:
                    target["project_name"] = project_name
                target["current_version"] = version
                target["updated_at"] = _now()
                default_remark = (
                    f"按档案编号覆盖上传（清单版本 v{manifest_version}）" if manifest_version else "按档案编号覆盖上传"
                )
                target["versions"].append(self._version_record(
                    version,
                    drawings,
                    added=added,
                    overwritten=overwritten,
                    deleted=deleted,
                    operator=operator,
                    remark=remark or default_remark,
                ))
                message = (
                    f"已按档案编号 {target['archive_no']} 覆盖并生成版本 v{version}："
                    f"新增 {len(added)} 张、覆盖 {len(overwritten)} 张、删除 {len(deleted)} 张"
                )

            return {
                "ok": True,
                "changed": True,
                "new_archive": new_archive,
                "archive_no": target["archive_no"],
                "version": target["current_version"],
                "manifest_version": manifest_version,
                "project_name": target["project_name"],
                "file_count": len(drawings),
                "added": added,
                "overwritten": overwritten,
                "deleted": deleted,
                "unchanged": unchanged,
                "warnings": warnings,
                "message": message,
            }

    def _diff_files(
        self,
        old_files: dict[str, dict[str, Any]],
        new_drawings: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        added: list[dict[str, Any]] = []
        overwritten: list[dict[str, Any]] = []
        deleted: list[dict[str, Any]] = []
        unchanged: list[str] = []
        for path, item in new_drawings.items():
            payload = {"path": path, "segment": item["segment"], "name": item["name"], "sha256": item["sha256"]}
            if path not in old_files:
                added.append(payload)
            elif old_files[path]["sha256"] != item["sha256"]:
                payload["old_sha256"] = old_files[path]["sha256"]
                overwritten.append(payload)
            else:
                unchanged.append(path)
        for path, item in old_files.items():
            if path not in new_drawings:
                deleted.append({"path": path, "segment": item["segment"], "name": item["name"], "removed_sha256": item["sha256"]})
        return (
            sorted(added, key=lambda x: x["path"]),
            sorted(overwritten, key=lambda x: x["path"]),
            sorted(deleted, key=lambda x: x["path"]),
            sorted(unchanged),
        )

    def _new_archive_locked(
        self,
        project_name: str,
        drawings: dict[str, dict[str, Any]],
        operator: str,
        remark: str,
    ) -> dict[str, Any]:
        year = datetime.now().year
        existing = [
            int(a["archive_no"].rsplit("-", 1)[-1])
            for a in self._archives
            if a["archive_no"].startswith(f"JGTZ-{year}-")
        ]
        seq = max(self._seq.get(year, 0), max(existing, default=0)) + 1
        self._seq[year] = seq
        now = _now()
        files_meta = {path: {k: v for k, v in item.items() if k != "content"} for path, item in drawings.items()}
        archive: dict[str, Any] = {
            "id": self._next_id,
            "archive_no": f"JGTZ-{year}-{seq:04d}",
            "project_name": project_name,
            "status": STATUS_PENDING,
            "current_version": 1,
            "created_at": now,
            "updated_at": now,
            "files": files_meta,
            "versions": [],
            "handover": None,
        }
        archive["versions"].append(self._version_record(
            1, drawings,
            added=[{"path": p, "segment": i["segment"], "name": i["name"], "sha256": i["sha256"]}
                   for p, i in sorted(drawings.items())],
            overwritten=[], deleted=[], operator=operator, remark=remark or "整套图纸首次上传",
        ))
        self._next_id += 1
        self._archives.append(archive)
        return archive

    def _version_record(
        self,
        version: int,
        drawings: dict[str, dict[str, Any]],
        *,
        added: list[dict[str, Any]],
        overwritten: list[dict[str, Any]],
        deleted: list[dict[str, Any]],
        operator: str,
        remark: str,
    ) -> dict[str, Any]:
        return {
            "version": version,
            "created_at": _now(),
            "operator": operator,
            "remark": remark,
            "added": [dict(item) for item in added],
            "overwritten": [dict(item) for item in overwritten],
            "deleted": [dict(item) for item in deleted],
            "file_count": len(drawings),
        }

    # ------------------------------------------------------------------ 下载

    def build_archive_zip(
        self, archive_no: str, segment: str | None = None
    ) -> tuple[bytes, str, dict[str, Any]]:
        with self._lock:
            archive = self._find_locked(archive_no)
            if archive is None:
                raise ServiceError(404, {"code": "not_found", "message": f"档案 {archive_no} 不存在"})
            available = sorted({item["segment"] for item in archive["files"].values()})
            if segment is not None:
                segment = segment.strip()
                if segment not in available:
                    raise ServiceError(400, {
                        "code": "segment_not_found",
                        "message": f"档案 {archive_no} 下没有工程分段「{segment}」",
                        "available_segments": available,
                    })
            chosen = {
                path: item
                for path, item in archive["files"].items()
                if segment is None or item["segment"] == segment
            }
            snapshot = {
                "archive_no": archive["archive_no"],
                "project_name": archive["project_name"],
                "version": archive["current_version"],
                "status": archive["status"],
                "exported_at": _now(),
                "files": {path: item["sha256"] for path, item in sorted(chosen.items())},
            }
            blobs = {item["sha256"]: self._blobs[item["sha256"]] for item in chosen.values()}
            label = segment or "整套"

        top = f"{snapshot['archive_no']}-v{snapshot['version']}" + (f"-{segment}" if segment else "")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(
                f"{top}/{MANIFEST_NAME}",
                json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for path, item in sorted(chosen.items()):
                package.writestr(f"{top}/{path}", blobs[item["sha256"]])
        suffix = f"-{segment}" if segment else ""
        filename = f"{snapshot['archive_no']}-v{snapshot['version']}{suffix}.zip"
        return buffer.getvalue(), filename, {"segment": label, **{k: snapshot[k] for k in ("archive_no", "version", "status")}}

    # ------------------------------------------------------------------ 移交

    def handover(self, archive_no: str, receiver: str, operator: str, remark: str) -> dict[str, Any]:
        receiver = receiver.strip()
        if not receiver:
            raise ServiceError(400, {"code": "missing_receiver", "message": "移交必须填写接收人"})
        with self._lock:
            archive = self._find_locked(archive_no)
            if archive is None:
                raise ServiceError(404, {"code": "not_found", "message": f"档案 {archive_no} 不存在"})
            if archive["status"] == STATUS_HANDED:
                raise ServiceError(409, {
                    "code": "archive_locked",
                    "message": f"档案 {archive_no} 已移交给 {archive['handover']['receiver']}，不能重复移交",
                })
            archive["status"] = STATUS_HANDED
            archive["updated_at"] = _now()
            record = self._build_handover_record_locked(archive, receiver, operator.strip() or "值班管理员", remark.strip())
            archive["handover"] = {k: v for k, v in record.items() if k != "id"}
            self._handover_records.append(record)
            return dict(record)

    def _build_handover_record_locked(
        self, archive: dict[str, Any], receiver: str, operator: str, remark: str
    ) -> dict[str, Any]:
        return {
            "id": len(self._handover_records) + 1,
            "archive_no": archive["archive_no"],
            "project_name": archive["project_name"],
            "version": archive["current_version"],
            "file_count": len(archive["files"]),
            "segments": sorted({item["segment"] for item in archive["files"].values()}),
            "receiver": receiver,
            "operator": operator,
            "remark": remark,
            "handed_at": _now(),
        }

    def list_handover_records(self, keyword: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._handover_records]
        if keyword:
            kw = keyword.strip()
            rows = [r for r in rows if kw in r["archive_no"] or kw in r["receiver"] or kw in r["project_name"]]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return rows

    # ------------------------------------------------------------------ 一致性核对

    def consistency_check(self) -> dict[str, Any]:
        """核对档案总数与移交清单：记录数量、指向、版本、图纸数必须处处对得上。"""
        checks: list[dict[str, Any]] = []
        mismatches: list[str] = []
        with self._lock:
            handed = [a for a in self._archives if a["status"] == STATUS_HANDED]
            records = self._handover_records

            def add_check(name: str, expected: Any, actual: Any) -> None:
                ok = expected == actual
                checks.append({"name": name, "expected": expected, "actual": actual, "ok": ok})
                if not ok:
                    mismatches.append(f"{name}：应为 {expected}，实际 {actual}")

            add_check("已移交档案数与移交清单条数一致", len(handed), len(records))

            record_map = {r["archive_no"]: r for r in records}
            add_check("移交清单中的档案编号均存在且唯一", len(record_map), len(records))
            for record in records:
                archive = self._find_locked(record["archive_no"])
                if archive is None:
                    mismatches.append(f"移交清单引用了不存在的档案 {record['archive_no']}")
                    continue
                if archive["status"] != STATUS_HANDED:
                    mismatches.append(f"清单已登记移交，但档案 {archive['archive_no']} 状态仍是「{archive['status']}」")
                if record["version"] != archive["current_version"]:
                    mismatches.append(
                        f"档案 {archive['archive_no']} 移交清单版本 v{record['version']} "
                        f"与当前版本 v{archive['current_version']} 不一致"
                    )
                if record["file_count"] != len(archive["files"]):
                    mismatches.append(
                        f"档案 {archive['archive_no']} 移交清单图纸数 {record['file_count']} "
                        f"与档案实际图纸数 {len(archive['files'])} 不一致"
                    )
            for archive in handed:
                if archive["archive_no"] not in record_map:
                    mismatches.append(f"档案 {archive['archive_no']} 状态为已移交，但移交清单中没有记录")
                latest = archive["versions"][-1]
                if latest["file_count"] != len(archive["files"]):
                    mismatches.append(f"档案 {archive['archive_no']} 最新版本明细图数与档案图数不一致")
                for path, item in archive["files"].items():
                    if item["sha256"] not in self._blobs:
                        mismatches.append(f"档案 {archive['archive_no']} 的图纸 {path} 缺少归档字节，档案不完整")

            return {
                "ok": not mismatches,
                "checked_at": _now(),
                "archive_total": len(self._archives),
                "handed_total": len(handed),
                "pending_total": len(self._archives) - len(handed),
                "handover_record_total": len(records),
                "checks": checks,
                "mismatches": mismatches,
            }


service = DrawingArchiveService()
