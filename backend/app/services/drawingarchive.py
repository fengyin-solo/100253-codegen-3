"""竣工图纸档案业务规则。

围绕需求里的十件事收口：
1. 整套上传成功后才生成档案编号（先解析暂存、全部通过再落库，失败不留半份数据）；
2. 按工程段组织并支持整段/整套打包下载；
3. 档案整体移交，移交后只读；
4. 重复上传按档案编号覆盖，并明确列出覆盖了哪几张图；
5. 压缩包结构不合规整包退回，退回原因逐条列明；
6. 下载的档案包再上传，编号与版本能对上（编号、版本、校验指纹写入清单文件）；
7. 已移交档案只可查看不可改动；
8. 每一版都落一条变更记录（新增/覆盖/图纸指纹）；
9. 档案总数与移交清单做一致性核对。

当前是内存实现（与项目其他模块一致，克隆即可运行）：图纸字节存在内存里，
服务重启后数据清空；换成数据库/对象存储时只需替换 DrawingArchiveStore 的存储部分。
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from collections import OrderedDict
from datetime import datetime
from typing import Any

# 档案清单的格式版本，将来清单结构演进时用它判断兼容性。
MANIFEST_SCHEMA = "drawing-archive-manifest/1"
MANIFEST_NAME = "档案清单.json"
# 分段包只供按段移交查看，不能整包回传（回传必须用整套包）。
PACKAGE_SCOPE_FULL = "full"
PACKAGE_SCOPE_SECTION = "section"

DRAWING_EXTS = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg"}
# macOS 压缩时常见的目录杂项，扫描时忽略，不计入结构错误。
IGNORE_NAMES = {".ds_store", "thumbs.db"}
IGNORE_PREFIXES = ("__macosx/",)

STATUS_IN_HAND = "在库"
STATUS_HANDED_OVER = "已移交"


class PackageRejected(Exception):
    """压缩包整包退回：reasons 里是要原样展示给移交人的逐条原因。"""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("；".join(reasons))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(drawings: "OrderedDict[str, dict[str, Any]]") -> str:
    """整套图纸的内容指纹：按 工程段/图名 排序后对单图指纹摘要。

    同一份图纸无论压几次 zip，字节可能不同（压缩时间等元数据），
    但只要 工程段/图名/文件内容 不变，指纹就一致，用于回传比对版本。
    """
    digest = hashlib.sha256()
    for key in sorted(drawings):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(drawings[key]["sha256"].encode("ascii"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _normalize_segment(name: str) -> str:
    return name.strip().strip("/").strip()


class ParsedPackage:
    """解析通过、暂存在内存里的待提交包。"""

    def __init__(
        self,
        *,
        root_name: str,
        sections: dict[str, "OrderedDict[str, dict[str, Any]]"],
        manifest: dict[str, Any] | None,
    ) -> None:
        self.root_name = root_name
        # section -> {图名: {filename, size, sha256, data}}
        self.sections = sections
        self.manifest = manifest

    def iter_drawings(self) -> list[tuple[str, str, dict[str, Any]]]:
        flat: list[tuple[str, str, dict[str, Any]]] = []
        for section, drawings in self.sections.items():
            for name, item in drawings.items():
                flat.append((section, name, item))
        return flat


def parse_package(content: bytes) -> ParsedPackage:
    """把上传的压缩包解析成暂存结构；任何不合规都整包退回。

    这里只读 content、不碰档案库数据，因此解析失败天然不会留下半份数据。
    """
    reasons: list[str] = []
    if not content:
        raise PackageRejected(["上传内容为空，未收到任何压缩包数据"])
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise PackageRejected(["文件不是合法的 ZIP 压缩包，请重新打包为 .zip 后上传"])

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise PackageRejected([f"压缩包已损坏，无法打开：{exc}"]) from exc

    bad_entry = zf.testzip()
    if bad_entry is not None:
        raise PackageRejected([f"压缩包内文件「{bad_entry}」CRC 校验失败，包可能未传完整"])

    infos = [info for info in zf.infolist() if not info.is_dir()]
    roots: set[str] = set()
    # (segment, drawing_name) -> zip 内路径，用来查重复图纸
    located: dict[tuple[str, str], str] = {}
    sections: dict[str, "OrderedDict[str, dict[str, Any]]"] = {}
    manifest: dict[str, Any] | None = None

    for info in infos:
        raw = info.filename.replace("\\", "/")
        lower = raw.lower()
        if any(lower.startswith(prefix) for prefix in IGNORE_PREFIXES):
            continue
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if not parts:
            continue
        if parts[-1].lower() in IGNORE_NAMES:
            continue

        # 防 zip slip：拒绝任何 .. 或绝对路径成分。
        if any(part == ".." for part in parts) or raw.startswith("/"):
            reasons.append(f"存在非法路径条目「{info.filename}」，不允许出现绝对路径或上级目录跳转")
            continue

        if len(parts) < 2:
            reasons.append(
                f"文件「{info.filename}」直接放在压缩包根目录下，"
                "要求结构为：唯一根目录/工程段目录/图纸文件"
            )
            continue

        root = parts[0]
        roots.add(root)
        rel_parts = parts[1:]

        # 档案清单固定放在根目录下。
        if len(rel_parts) == 1 and rel_parts[0] == MANIFEST_NAME:
            try:
                manifest = json.loads(_read_entry(zf, info).decode("utf-8-sig"))
            except UnicodeDecodeError:
                reasons.append(f"「{MANIFEST_NAME}」不是 UTF-8 编码的文本文件")
            except json.JSONDecodeError as exc:
                reasons.append(f"「{MANIFEST_NAME}」不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列附近解析失败")
            continue

        if len(rel_parts) < 2:
            reasons.append(
                f"文件「{info.filename}」挂在根目录「{root}」下，"
                "必须放入某个工程段目录（根目录/工程段/图纸）"
            )
            continue

        if len(rel_parts) > 2:
            reasons.append(
                f"文件「{info.filename}」目录层级过深，只允许 根目录/工程段/图纸 两级"
            )
            continue

        segment = _normalize_segment(rel_parts[0])
        if not segment:
            reasons.append(f"文件「{info.filename}」所在工程段目录名为空")
            continue
        drawing_name = rel_parts[1]
        dot_at = drawing_name.rfind(".")
        stem, ext = (
            (drawing_name[:dot_at], drawing_name[dot_at:].lower())
            if dot_at >= 0
            else (drawing_name, "")
        )
        if ext not in DRAWING_EXTS:
            reasons.append(
                f"文件「{info.filename}」类型 {ext or '无扩展名'} 不支持，"
                f"仅支持 {sorted(DRAWING_EXTS)} 图纸格式"
            )
            continue
        if not stem.strip():
            reasons.append(f"文件「{info.filename}」只有扩展名没有图纸名称")
            continue

        dedup_key = (segment, drawing_name)
        if dedup_key in located:
            reasons.append(
                f"工程段「{segment}」下图纸「{drawing_name}」重复出现："
                f"{located[dedup_key]} 与 {info.filename}"
            )
            continue
        located[dedup_key] = info.filename
        try:
            data = _read_entry(zf, info)
        except zipfile.BadZipFile as exc:
            reasons.append(f"文件「{info.filename}」读取失败：{exc}")
            continue
        sections.setdefault(segment, OrderedDict())[drawing_name] = {
            "filename": drawing_name,
            "size": len(data),
            "sha256": sha256_hex(data),
            "data": data,
        }

    if not roots:
        reasons.append("压缩包内没有任何根目录，要求整包图纸放在同一个根目录下")
    elif len(roots) > 1:
        reasons.append(f"压缩包顶层出现 {len(roots)} 个目录（{'、'.join(sorted(roots))}），要求只保留一个根目录")

    if manifest is not None:
        reasons.extend(_validate_manifest(manifest))
    elif roots:
        # 没有清单说明是本地首次打包上传，允许；仅当存在多个根目录等更严重错误时上面已记。
        pass

    total_drawings = sum(len(items) for items in sections.values())
    if total_drawings == 0:
        reasons.append("压缩包内没有找到任何图纸文件（根目录/工程段/图纸），整包退回")

    if reasons:
        raise PackageRejected(reasons)

    return ParsedPackage(root_name=next(iter(roots)), sections=sections, manifest=manifest)


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    with zf.open(info) as handle:
        return handle.read()


def _validate_manifest(manifest: Any) -> list[str]:
    reasons: list[str] = []
    if not isinstance(manifest, dict):
        return [f"「{MANIFEST_NAME}」顶层必须是对象结构，实际是 {type(manifest).__name__}"]
    required = ["schema", "archive_no", "version", "fingerprint"]
    for key in required:
        if key not in manifest:
            reasons.append(f"「{MANIFEST_NAME}」缺少必填字段 {key}")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        reasons.append(
            f"「{MANIFEST_NAME}」schema 为 {manifest.get('schema')!r}，"
            f"本系统只识别 {MANIFEST_SCHEMA}"
        )
    scope = manifest.get("scope")
    if scope == PACKAGE_SCOPE_SECTION:
        reasons.append(
            "这是按工程段导出的分段包（scope=section），只供查看，"
            "不能整包回传；请使用整套档案包（在档案详情里“下载整套包”）"
        )
    if "version" in manifest and not isinstance(manifest["version"], int):
        reasons.append(f"「{MANIFEST_NAME}」version 必须是整数版本号")
    return reasons


def build_zip(
    *,
    root_name: str,
    sections: dict[str, "OrderedDict[str, dict[str, Any]]"],
    manifest: dict[str, Any],
) -> bytes:
    """生成结构合规的档案包：根目录/工程段/图纸 + 根目录下的清单。"""
    buffer = io.BytesIO()
    # ZIP_DEFLATED 压缩；常量时间戳由 ZIP 自身处理，内容指纹不依赖 zip 元数据。
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{root_name}/{MANIFEST_NAME}",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for section in sorted(sections):
            for drawing_name in sorted(sections[section]):
                item = sections[section][drawing_name]
                zf.writestr(
                    f"{root_name}/{section}/{drawing_name}",
                    item["data"],
                )
    return buffer.getvalue()


class DrawingArchiveStore:
    """档案库：图纸字节、版本与移交记录都在这里，写操作统一加锁。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._archives: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._handovers: list[dict[str, Any]] = []
        self._daily_serial: dict[str, int] = {}

    def _next_archive_no(self, now: datetime) -> str:
        day = now.strftime("%Y%m%d")
        serial = self._daily_serial.get(day, 0) + 1
        self._daily_serial[day] = serial
        return f"JG-{day}-{serial:04d}"

    def list_archives(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            items = [self._summary(archive) for archive in self._archives.values()]
        if keyword:
            items = [
                item
                for item in items
                if keyword in item["archive_no"] or keyword in item["project_name"]
            ]
        if status:
            items = [item for item in items if item["status"] == status]
        total = len(items)
        start = max(page - 1, 0) * size
        return items[start : start + size], total

    def get(self, archive_no: str) -> dict[str, Any] | None:
        with self._lock:
            archive = self._archives.get(archive_no)
            return None if archive is None else self._detail(archive)

    def require(self, archive_no: str) -> dict[str, Any]:
        detail = self.get(archive_no)
        if detail is None:
            raise KeyError(archive_no)
        return detail

    def revisions(self, archive_no: str) -> list[dict[str, Any]]:
        archive = self._archives.get(archive_no)
        if archive is None:
            raise KeyError(archive_no)
        with self._lock:
            return [dict(item) for item in archive["revisions"]]

    def ingest(
        self,
        content: bytes,
        *,
        project_name: str | None,
        operator: str,
        remark: str | None,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """接收一个压缩包：解析暂存 → 比对 → 一次性提交。

        返回 (档案详情, 结果类型, 附加信息)，结果类型：
        created 新建 / updated 覆盖升版 / unchanged 内容一致幂等返回。
        """
        parsed = parse_package(content)  # 失败抛 PackageRejected，不触碰库内数据
        manifest_no = parsed.manifest.get("archive_no") if parsed.manifest else None
        with self._lock:
            existing = self._archives.get(manifest_no) if manifest_no else None
            now = datetime.now()
            current_fingerprint = fingerprint(self._flatten(parsed.sections))

            if existing is not None:
                if (
                    existing["status"] == STATUS_HANDED_OVER
                    and existing["fingerprint"] != current_fingerprint
                ):
                    raise PermissionError(existing["archive_no"])
                return self._commit_update(
                    existing, parsed, current_fingerprint, operator, remark, now
                )

            archive_no = self._next_archive_no(now)
            name = (project_name or "").strip() or parsed.root_name
            archive: dict[str, Any] = {
                "archive_no": archive_no,
                "project_name": name,
                "root_name": parsed.root_name,
                "version": 1,
                "status": STATUS_IN_HAND,
                "fingerprint": current_fingerprint,
                "created_at": _fmt(now),
                "created_by": operator,
                "updated_at": _fmt(now),
                "sections": parsed.sections,
                "revisions": [],
                "handover": None,
            }
            drawing_count = sum(len(items) for items in parsed.sections.values())
            archive["revisions"].append(
                self._revision_record(
                    version=1,
                    operator=operator,
                    remark=remark,
                    change_type="建档",
                    added=[f"{section}/{name}" for section, name, _ in parsed.iter_drawings()],
                    overwrites=[],
                    removed=[],
                    before_fingerprint=None,
                    after_fingerprint=current_fingerprint,
                    drawing_count=drawing_count,
                    now=now,
                )
            )
            self._archives[archive_no] = archive
            return self._detail(archive), "created", {"manifest_no": manifest_no}

    def _commit_update(
        self,
        archive: dict[str, Any],
        parsed: ParsedPackage,
        current_fingerprint: str,
        operator: str,
        remark: str | None,
        now: datetime,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        old_sections = archive["sections"]
        added: list[str] = []
        removed: list[str] = []
        overwrites: list[dict[str, str]] = []
        unchanged: list[str] = []
        new_sections: dict[str, "OrderedDict[str, dict[str, Any]]"] = {}

        for section, drawings in parsed.sections.items():
            bucket = new_sections.setdefault(section, OrderedDict())
            for name, item in drawings.items():
                key = f"{section}/{name}"
                old_item = old_sections.get(section, OrderedDict()).get(name)
                if old_item is None:
                    added.append(key)
                elif old_item["sha256"] == item["sha256"]:
                    unchanged.append(key)
                else:
                    overwrites.append(
                        {
                            "drawing": key,
                            "old_sha256": old_item["sha256"],
                            "new_sha256": item["sha256"],
                            "old_size": old_item["size"],
                            "new_size": item["size"],
                        }
                    )
                bucket[name] = item

        for section, drawings in old_sections.items():
            for name in drawings:
                key = f"{section}/{name}"
                if name not in parsed.sections.get(section, OrderedDict()):
                    removed.append(key)

        if current_fingerprint == archive["fingerprint"] and not added and not removed:
            # 图纸内容一张没变：不升版、不留新记录，按幂等原样返回。
            return self._detail(archive), "unchanged", {
                "version": archive["version"],
                "overwrites": [],
                "added": [],
                "removed": [],
            }

        if parsed.manifest is not None:
            declared = parsed.manifest.get("version")
            if isinstance(declared, int) and declared != archive["version"]:
                raise PackageRejected(
                    [
                        f"清单版本号 V{declared} 与档案库当前版本 V{archive['version']} 不一致，"
                        "请先下载档案库当前整套包，基于最新版本重新上传"
                    ]
                )

        archive["version"] += 1
        archive["sections"] = new_sections
        archive["fingerprint"] = current_fingerprint
        archive["root_name"] = parsed.root_name
        archive["updated_at"] = _fmt(now)
        archive["revisions"].append(
            self._revision_record(
                version=archive["version"],
                operator=operator,
                remark=remark,
                change_type="覆盖升版",
                added=added,
                overwrites=overwrites,
                removed=removed,
                before_fingerprint=archive["revisions"][-1]["after_fingerprint"],
                after_fingerprint=current_fingerprint,
                drawing_count=sum(len(items) for items in new_sections.values()),
                now=now,
            )
        )
        return self._detail(archive), "updated", {
            "version": archive["version"],
            "added": added,
            "removed": removed,
            "unchanged": unchanged,
            "overwrites": overwrites,
        }

    def hand_over(
        self, archive_no: str, *, receiver: str, operator: str, remark: str | None
    ) -> dict[str, Any]:
        receiver = receiver.strip()
        if not receiver:
            raise ValueError("接收人不能为空")
        with self._lock:
            archive = self._archives.get(archive_no)
            if archive is None:
                raise KeyError(archive_no)
            if archive["status"] == STATUS_HANDED_OVER:
                raise PermissionError(archive_no)
            now = datetime.now()
            snapshot = {
                section: OrderedDict(
                    (name, {"sha256": item["sha256"], "size": item["size"]})
                    for name, item in drawings.items()
                )
                for section, drawings in archive["sections"].items()
            }
            record = {
                "id": len(self._handovers) + 1,
                "archive_no": archive_no,
                "project_name": archive["project_name"],
                "version": archive["version"],
                "fingerprint": archive["fingerprint"],
                "drawing_count": sum(len(items) for items in archive["sections"].values()),
                "section_count": len(archive["sections"]),
                "receiver": receiver,
                "operator": operator,
                "remark": (remark or "").strip() or None,
                "handed_at": _fmt(now),
                "snapshot": snapshot,
            }
            self._handovers.append(record)
            archive["status"] = STATUS_HANDED_OVER
            archive["handover"] = {
                "receiver": receiver,
                "operator": operator,
                "handed_at": record["handed_at"],
                "version": record["version"],
                "remark": record["remark"],
            }
            return dict(record)

    def list_handovers(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._handover_summary(item) for item in self._handovers]

    def consistency_report(self) -> dict[str, Any]:
        """档案总数与移交清单一致性核对。"""
        with self._lock:
            details: list[dict[str, Any]] = []
            consistent = True
            for record in self._handovers:
                archive = self._archives.get(record["archive_no"])
                problems: list[str] = []
                if archive is None:
                    problems.append("移交清单上的档案在档案库中已不存在")
                else:
                    if archive["status"] != STATUS_HANDED_OVER:
                        problems.append(f"档案状态为{archive['status']}，但移交清单标记已移交")
                    if archive["fingerprint"] != record["fingerprint"]:
                        problems.append(
                            f"档案当前内容指纹 V{archive['version']} 与移交时快照 "
                            f"V{record['version']} 不一致（移交后内容发生过变化）"
                        )
                    elif archive["version"] != record["version"]:
                        problems.append(
                            f"版本号对不上：档案库 V{archive['version']}，移交清单 V{record['version']}"
                        )
                if problems:
                    consistent = False
                details.append(
                    {
                        "archive_no": record["archive_no"],
                        "project_name": record["project_name"],
                        "receiver": record["receiver"],
                        "handed_at": record["handed_at"],
                        "version": record["version"],
                        "ok": not problems,
                        "problems": problems,
                    }
                )
            handed_archives = [a for a in self._archives.values() if a["status"] == STATUS_HANDED_OVER]
            if len(handed_archives) != len(self._handovers):
                consistent = False
            return {
                "consistent": consistent,
                "archive_total": len(self._archives),
                "in_hand_total": len(self._archives) - len(handed_archives),
                "handed_archive_total": len(handed_archives),
                "handover_list_total": len(self._handovers),
                "details": details,
            }

    def get_drawing(
        self, archive_no: str, section: str, drawing_name: str
    ) -> dict[str, Any] | None:
        with self._lock:
            archive = self._archives.get(archive_no)
            if archive is None:
                return None
            item = archive["sections"].get(_normalize_segment(section), OrderedDict()).get(
                drawing_name
            )
            return None if item is None else dict(item)

    def package_full(self, archive_no: str) -> tuple[str, bytes]:
        with self._lock:
            archive = self._archives.get(archive_no)
            if archive is None:
                raise KeyError(archive_no)
            manifest = self._manifest(archive, PACKAGE_SCOPE_FULL)
            data = build_zip(
                root_name=archive["root_name"],
                sections=archive["sections"],
                manifest=manifest,
            )
        return f"{archive_no}_V{archive['version']}_整套.zip", data

    def package_section(self, archive_no: str, section: str) -> tuple[str, bytes]:
        with self._lock:
            archive = self._archives.get(archive_no)
            if archive is None:
                raise KeyError(archive_no)
            segment = _normalize_segment(section)
            if segment not in archive["sections"]:
                raise ValueError(segment)
            sections = {segment: archive["sections"][segment]}
            manifest = self._manifest(
                archive,
                PACKAGE_SCOPE_SECTION,
                section=segment,
                sections=sections,
            )
            data = build_zip(root_name=archive["root_name"], sections=sections, manifest=manifest)
        safe_segment = segment.replace("/", "_")
        return f"{archive_no}_V{archive['version']}_{safe_segment}.zip", data

    def _manifest(
        self,
        archive: dict[str, Any],
        scope: str,
        *,
        section: str | None = None,
        sections: dict[str, "OrderedDict[str, dict[str, Any]]"] | None = None,
    ) -> dict[str, Any]:
        chosen = sections if sections is not None else archive["sections"]
        return {
            "schema": MANIFEST_SCHEMA,
            "scope": scope,
            "archive_no": archive["archive_no"],
            "project_name": archive["project_name"],
            "version": archive["version"],
            "status": archive["status"],
            "fingerprint": archive["fingerprint"],
            "section": section,
            "drawing_count": sum(len(items) for items in chosen.values()),
            "sections": [
                {
                    "name": name,
                    "drawings": [
                        {"name": drawing, "sha256": item["sha256"], "size": item["size"]}
                        for drawing, item in sorted(drawings.items())
                    ],
                }
                for name, drawings in sorted(chosen.items())
            ],
            "generated_at": _fmt(datetime.now()),
        }

    def _revision_record(
        self,
        *,
        version: int,
        operator: str,
        remark: str | None,
        change_type: str,
        added: list[str],
        overwrites: list[dict[str, str]],
        removed: list[str],
        before_fingerprint: str | None,
        after_fingerprint: str,
        drawing_count: int,
        now: datetime,
    ) -> dict[str, Any]:
        return {
            "version": version,
            "change_type": change_type,
            "operator": operator,
            "remark": (remark or "").strip() or None,
            "added": sorted(added),
            "removed": sorted(removed),
            "overwrites": overwrites,
            "added_count": len(added),
            "overwrite_count": len(overwrites),
            "removed_count": len(removed),
            "drawing_count": drawing_count,
            "before_fingerprint": before_fingerprint,
            "after_fingerprint": after_fingerprint,
            "changed_at": _fmt(now),
        }

    @staticmethod
    def _flatten(
        sections: dict[str, "OrderedDict[str, dict[str, Any]]"],
    ) -> "OrderedDict[str, dict[str, Any]]":
        flat: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        for section, drawings in sections.items():
            for name, item in drawings.items():
                flat[f"{section}/{name}"] = item
        return flat

    def _summary(self, archive: dict[str, Any]) -> dict[str, Any]:
        return {
            "archive_no": archive["archive_no"],
            "project_name": archive["project_name"],
            "version": archive["version"],
            "status": archive["status"],
            "section_count": len(archive["sections"]),
            "drawing_count": sum(len(items) for items in archive["sections"].values()),
            "fingerprint": archive["fingerprint"][:12],
            "created_by": archive["created_by"],
            "created_at": archive["created_at"],
            "updated_at": archive["updated_at"],
            "receiver": archive["handover"]["receiver"] if archive["handover"] else None,
            "handed_at": archive["handover"]["handed_at"] if archive["handover"] else None,
        }

    def _detail(self, archive: dict[str, Any]) -> dict[str, Any]:
        detail = self._summary(archive)
        detail["fingerprint"] = archive["fingerprint"]
        detail["root_name"] = archive["root_name"]
        detail["read_only"] = archive["status"] == STATUS_HANDED_OVER
        detail["sections"] = [
            {
                "name": section,
                "drawing_count": len(drawings),
                "drawings": [
                    {
                        "name": name,
                        "size": item["size"],
                        "sha256": item["sha256"],
                    }
                    for name, item in sorted(drawings.items())
                ],
            }
            for section, drawings in sorted(archive["sections"].items())
        ]
        detail["latest_revision"] = dict(archive["revisions"][-1])
        detail["handover"] = dict(archive["handover"]) if archive["handover"] else None
        return detail

    @staticmethod
    def _handover_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "archive_no": record["archive_no"],
            "project_name": record["project_name"],
            "version": record["version"],
            "fingerprint": record["fingerprint"],
            "drawing_count": record["drawing_count"],
            "section_count": record["section_count"],
            "receiver": record["receiver"],
            "operator": record["operator"],
            "remark": record["remark"],
            "handed_at": record["handed_at"],
        }


def _fmt(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M:%S")


# 模块级单例，和 app.store.store 的用法保持一致。
drawing_store = DrawingArchiveStore()
