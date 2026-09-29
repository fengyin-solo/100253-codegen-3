"""竣工图纸档案端到端校验：直接驱动 DrawingArchiveStore 的全部业务规则。

运行：.venv-linux/bin/python scripts/verify_drawing_archive.py
"""
from __future__ import annotations

import io
import sys
import zipfile

from app.services.drawingarchive import (
    PackageRejected,
    drawing_store,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    return buffer.getvalue()


# 1. 合法整套包建档 -------------------------------------------------------
pkg_v1 = make_zip({
    "XX号线信号工程/正线段/SIG-001 进站信号机布置图.pdf": b"%PDF-1.4 drawing-A-v1",
    "XX号线信号工程/正线段/SIG-002 出站信号机.dwg": b"DWG-BINARY-v1",
    "XX号线信号工程/车辆段/CD-001 车辆段电缆径路.dxf": b"DXF-C-v1",
})
detail, result, _ = drawing_store.ingest(
    pkg_v1, project_name="XX号线信号工程", operator="张三", remark="首次移交前归档"
)
check("1.1 合法包首次建档", result == "created", detail)
check("1.2 自动生成档案编号", detail["archive_no"].startswith("JG-"), detail["archive_no"])
check("1.3 首版为 V1", detail["version"] == 1)
check("1.4 识别出两个工程段", detail["section_count"] == 2, str(detail["section_count"]))
check("1.5 图纸张数为 3", detail["drawing_count"] == 3)
check("1.6 状态在库", detail["status"] == "在库")
archive_no = detail["archive_no"]

# 2. 结构不合规整包退回（多种错误一次列全，且不留半份数据） ---------------
bad_pkg = make_zip({
    "散落根目录的图.pdf": b"x",
    "根A/正线段/嵌套太深/再嵌一层/x.pdf": b"x",
    "根A/正线段/note.txt": b"not a drawing",
    "根B/车辆段/x.pdf": b"x",
})
rejected = None
try:
    drawing_store.ingest(bad_pkg, project_name=None, operator="张三", remark=None)
except PackageRejected as exc:
    rejected = exc.reasons
check("2.1 坏包被整包退回", rejected is not None)
check("2.2 退回原因逐条列明", rejected is not None and len(rejected) >= 4, str(rejected))
rows, total = drawing_store.list_archives(page=1, size=50)
check("2.3 退回后不留半份数据（仍只有 1 份档案）", total == 1, str(total))

# 非 zip 字节
try:
    drawing_store.ingest(b"not a zip at all", project_name=None, operator="x", remark=None)
    check("2.4 非 ZIP 内容退回", False)
except PackageRejected as exc:
    check("2.4 非 ZIP 内容退回", "ZIP" in exc.reasons[0], exc.reasons[0])

# 3. 下载整套包再回传：编号与版本对上、内容一致不升版 ----------------------
_, full_zip = drawing_store.package_full(archive_no)
with zipfile.ZipFile(io.BytesIO(full_zip)) as zf:
    names = zf.namelist()
check("3.1 下载包含档案清单", any(n.endswith("档案清单.json") for n in names), str(names))
detail_rt, result_rt, extra_rt = drawing_store.ingest(
    full_zip, project_name=None, operator="李四", remark="接收方回传核对"
)
check("3.2 回传识别为同一编号", detail_rt["archive_no"] == archive_no)
check("3.3 内容一致幂等不升版", result_rt == "unchanged" and detail_rt["version"] == 1)
check("3.4 指纹一致", detail_rt["fingerprint"] == detail["fingerprint"])

# 4. 同编号覆盖：覆盖一张 + 新增一张 + 保留未变，说明覆盖了哪几张 ----------
with zipfile.ZipFile(io.BytesIO(full_zip)) as zf:
    entries = {n: zf.read(n) for n in zf.namelist()}
root = "XX号线信号工程"
entries[f"{root}/正线段/SIG-002 出站信号机.dwg"] = b"DWG-BINARY-v2-changed"
entries[f"{root}/正线段/SIG-003 新增调车信号机.pdf"] = b"%PDF new drawing D"
pkg_v2 = make_zip(entries)
detail_v2, result_v2, extra_v2 = drawing_store.ingest(
    pkg_v2, project_name=None, operator="王五", remark="图纸换版"
)
check("4.1 覆盖升版到 V2", result_v2 == "updated" and detail_v2["version"] == 2)
check(
    "4.2 明确覆盖的是哪张图",
    [o["drawing"] for o in extra_v2["overwrites"]] == ["正线段/SIG-002 出站信号机.dwg"],
    str(extra_v2["overwrites"]),
)
check("4.3 新增一张被识别", extra_v2["added"] == ["正线段/SIG-003 新增调车信号机.pdf"], str(extra_v2["added"]))
check("4.4 未变图纸计数", len(extra_v2["unchanged"]) == 2, str(extra_v2["unchanged"]))
check("4.5 图纸总数变为 4", detail_v2["drawing_count"] == 4)

# 5. 版本号对不上要拒绝 ----------------------------------------------------
import json
with zipfile.ZipFile(io.BytesIO(full_zip)) as zf:
    old_entries = {n: zf.read(n) for n in zf.namelist()}
manifest_path = f"{root}/档案清单.json"
manifest = json.loads(old_entries[manifest_path].decode("utf-8"))
manifest["version"] = 99
old_entries[manifest_path] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
stale_pkg = make_zip(old_entries)
try:
    drawing_store.ingest(stale_pkg, project_name=None, operator="x", remark=None)
    check("5.1 清单版本落后被拒绝", False)
except PackageRejected as exc:
    check("5.1 清单版本落后被拒绝", any("版本" in r for r in exc.reasons), str(exc.reasons))

# 6. 每一版都留变更记录 ----------------------------------------------------
revs = drawing_store.revisions(archive_no)
check("6.1 留下两版变更记录（建档+覆盖）", len(revs) == 2, str(len(revs)))
check("6.2 V1 记录建档新增 3 张", revs[0]["added_count"] == 3 and revs[0]["change_type"] == "建档")
check("6.3 V2 记录覆盖 1/新增 1", revs[1]["overwrite_count"] == 1 and revs[1]["added_count"] == 1)
check("6.4 前后指纹不同且都有留痕", revs[0]["after_fingerprint"] != revs[1]["after_fingerprint"])

# 7. 分段下载与分段包回传拒绝 ---------------------------------------------
_, section_zip = drawing_store.package_section(archive_no, "正线段")
try:
    drawing_store.ingest(section_zip, project_name=None, operator="x", remark=None)
    check("7.1 分段包不能整包回传", False)
except PackageRejected as exc:
    check("7.1 分段包不能整包回传", any("分段包" in r for r in exc.reasons), str(exc.reasons))

single = drawing_store.get_drawing(archive_no, "正线段", "SIG-001 进站信号机布置图.pdf")
check("7.2 单张图纸可查看下载", single is not None and single["data"].startswith(b"%PDF"))

# 8. 整体移交 + 已移交只读 -------------------------------------------------
record = drawing_store.hand_over(archive_no, receiver="接收单位-李工", operator="张三", remark="竣工资料整体移交")
check("8.1 移交生成清单记录", record["receiver"] == "接收单位-李工" and record["version"] == 2)
detail_locked = drawing_store.require(archive_no)
check("8.2 移交后状态已移交且只读", detail_locked["status"] == "已移交" and detail_locked["read_only"] is True)
try:
    drawing_store.hand_over(archive_no, receiver="重复", operator="x", remark=None)
    check("8.3 不能重复移交", False)
except PermissionError:
    check("8.3 不能重复移交", True)
# 内容一致的回传仍允许（只做核对）
_, result_locked_same, _ = drawing_store.ingest(
    drawing_store.package_full(archive_no)[1], project_name=None, operator="李工", remark="接收核对"
)
check("8.4 已移交后内容一致的回传仍可核对", result_locked_same == "unchanged")
# 内容改动的回传必须拒绝
full_locked = drawing_store.package_full(archive_no)[1]
with zipfile.ZipFile(io.BytesIO(full_locked)) as zf:
    locked_entries = {n: zf.read(n) for n in zf.namelist()}
target_name = f"{root}/正线段/SIG-001 进站信号机布置图.pdf"
locked_entries[target_name] = b"%PDF TAMPERED CONTENT"
try:
    drawing_store.ingest(make_zip(locked_entries), project_name=None, operator="李工", remark=None)
    check("8.5 已移交后改动内容被拒绝", False)
except PermissionError:
    check("8.5 已移交后改动内容被拒绝", True)

# 9. 档案总数与移交清单一致 ------------------------------------------------
report = drawing_store.consistency_report()
check(
    "9.1 档案总数与移交清单一致",
    report["consistent"]
    and report["archive_total"] == 1
    and report["handover_list_total"] == 1
    and report["handed_archive_total"] == 1,
    str(report),
)

# 10. 第二份档案建档 + 空接收人校验 ---------------------------------------
pkg2 = make_zip({"二期工程/区间/QJ-1.pdf": b"pdf-x"})
d2, r2, _ = drawing_store.ingest(pkg2, project_name="二期工程", operator="赵六", remark=None)
try:
    drawing_store.hand_over(d2["archive_no"], receiver="   ", operator="x", remark=None)
    check("10.1 空接收人被拒绝", False)
except ValueError:
    check("10.1 空接收人被拒绝", True)
report2 = drawing_store.consistency_report()
check("10.2 未移交档案不计入清单，核对仍一致", report2["archive_total"] == 2 and report2["handover_list_total"] == 1 and report2["consistent"])

print()
if failures:
    print(f"{len(failures)} 项未通过：{failures}")
    sys.exit(1)
print("全部业务规则校验通过")
