"""HTTP 层冒烟：multipart 上传、错误码与整包下载走真实 ASGI 链路。"""
from __future__ import annotations

import io
import sys

import zipfile
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    return buffer.getvalue()


# 健康检查
check("0.1 健康检查", client.get("/api/health").json()["ok"] is True)

# 坏包：非 zip
resp = client.post(
    "/api/drawing-archives/upload",
    files={"package": ("bad.zip", b"plain text", "application/zip")},
    data={"project_name": "测试工程"},
)
check("1.1 非 ZIP 返回 422", resp.status_code == 422, str(resp.status_code))
check("1.2 退回原因可读", "合法的 ZIP" in resp.json()["detail"]["reasons"][0])

# 结构坏包：多根目录 + 非法文件类型
bad = make_zip({"根A/段/a.txt": b"x", "根B/段/b.pdf": b"y"})
resp = client.post(
    "/api/drawing-archives/upload",
    files={"package": ("bad.zip", bad, "application/zip")},
)
body = resp.json()["detail"]
check("1.3 结构坏包 422 且整包退回说明", resp.status_code == 422 and "整包退回" in body["message"])
check("1.4 同时列出多个原因", len(body["reasons"]) >= 2, str(body["reasons"]))

# 合法建档
good = make_zip({
    "测试工程A/正线段/图1.pdf": b"pdf-1",
    "测试工程A/正线段/图2.dwg": b"dwg-2",
    "测试工程A/车辆段/图3.png": b"\x89PNG-data",
})
resp = client.post(
    "/api/drawing-archives/upload",
    files={"package": ("drawings.zip", good, "application/zip")},
    data={"project_name": "测试工程A", "remark": "第一次"},
)
check("2.1 建档 200", resp.status_code == 200, resp.text)
created = resp.json()
check("2.2 result=created 且消息含编号", created["result"] == "created" and "档案编号" in created["message"])
archive_no = created["archive"]["archive_no"]

# 列表 + 检索
resp = client.get("/api/drawing-archives?keyword=测试工程A")
check("2.3 列表可按名称检索", resp.json()["total"] == 1)

# 详情 / 变更记录
resp = client.get(f"/api/drawing-archives/{archive_no}")
check("2.4 详情返回三段图纸数据", resp.status_code == 200 and len(resp.json()["sections"]) == 2)
resp = client.get(f"/api/drawing-archives/{archive_no}/revisions")
check("2.5 变更记录首版建档", resp.json()["items"][0]["change_type"] == "建档")

# 整套下载再回传
resp = client.get(f"/api/drawing-archives/{archive_no}/download")
check("3.1 整套下载 200 且是 zip", resp.status_code == 200 and zipfile.is_zipfile(io.BytesIO(resp.content)))
check("3.2 下载文件名 UTF-8 编码", "filename*=UTF-8''" in resp.headers["content-disposition"])
resp = client.post(
    "/api/drawing-archives/upload",
    files={"package": ("roundtrip.zip", resp.content, "application/zip")},
)
rt = resp.json()
check("3.3 回传编号版本对上", rt["archive"]["archive_no"] == archive_no and rt["archive"]["version"] == 1)
check("3.4 回传幂等 unchanged", rt["result"] == "unchanged")

# 覆盖一张
with zipfile.ZipFile(io.BytesIO(client.get(f"/api/drawing-archives/{archive_no}/download").content)) as zf:
    entries = {n: zf.read(n) for n in zf.namelist()}
entries["测试工程A/正线段/图1.pdf"] = b"pdf-1-changed"
resp = client.post(
    "/api/drawing-archives/upload",
    files={"package": ("v2.zip", make_zip(entries), "application/zip")},
    data={"remark": "换版"},
)
upd = resp.json()
check("4.1 覆盖升版 V2", upd["result"] == "updated" and upd["archive"]["version"] == 2)
check("4.2 消息说明覆盖图纸", "覆盖图纸" in upd["message"] and "图1" in upd["message"], upd["message"])
check("4.3 overwrites 含新旧校验值", upd["overwrites"][0]["old_sha256"] != upd["overwrites"][0]["new_sha256"])

# 分段下载 / 单图下载
resp = client.get(f"/api/drawing-archives/{archive_no}/sections/车辆段/download")
check("5.1 分段包下载", resp.status_code == 200 and zipfile.is_zipfile(io.BytesIO(resp.content)))
resp = client.get(f"/api/drawing-archives/{archive_no}/sections/不存在的段/download")
check("5.2 未知工程段 404", resp.status_code == 404)
resp = client.get(f"/api/drawing-archives/{archive_no}/sections/正线段/drawings/图2.dwg")
check("5.3 单图下载内容正确", resp.status_code == 200 and resp.content == b"dwg-2")

# 分段包回传拒绝
seg = client.get(f"/api/drawing-archives/{archive_no}/sections/车辆段/download").content
resp = client.post("/api/drawing-archives/upload", files={"package": ("seg.zip", seg, "application/zip")})
check("5.4 分段包回传 422", resp.status_code == 422 and "分段包" in str(resp.json()["detail"]["reasons"]))

# 移交
resp = client.post("/api/drawing-archives/handovers", json={"archive_no": archive_no, "receiver": "运营分公司-周工", "remark": "竣工移交"})
check("6.1 移交成功", resp.status_code == 200 and resp.json()["handover"]["receiver"] == "运营分公司-周工")
resp = client.post("/api/drawing-archives/handovers", json={"archive_no": archive_no, "receiver": "再来一次"})
check("6.2 重复移交 409", resp.status_code == 409)
resp = client.post("/api/drawing-archives/handovers", json={"archive_no": "JG-99999999-9999", "receiver": "x"})
check("6.3 不存在档案移交 404", resp.status_code == 404)

# 已移交只读：改图回传 409；一致回传仍可核对
with zipfile.ZipFile(io.BytesIO(client.get(f"/api/drawing-archives/{archive_no}/download").content)) as zf:
    entries = {n: zf.read(n) for n in zf.namelist()}
entries["测试工程A/正线段/图2.dwg"] = b"dwg-2-tamper"
resp = client.post("/api/drawing-archives/upload", files={"package": ("tamper.zip", make_zip(entries), "application/zip")})
check("7.1 已移交改图 409", resp.status_code == 409)
same = client.get(f"/api/drawing-archives/{archive_no}/download").content
resp = client.post("/api/drawing-archives/upload", files={"package": ("verify.zip", same, "application/zip")})
check("7.2 已移交一致回传可核对", resp.status_code == 200 and resp.json()["result"] == "unchanged")

# 一致性核对
report = client.get("/api/drawing-archives/consistency").json()
check("8.1 一致性核对通过", report["consistent"] and report["archive_total"] == 1 and report["handover_list_total"] == 1, str(report))
items = client.get("/api/drawing-archives/handovers").json()
check("8.2 移交清单含一条 V2 快照", items["total"] == 1 and items["items"][0]["version"] == 2)

print()
if failures:
    print(f"{len(failures)} 项未通过：{failures}")
    sys.exit(1)
print("HTTP 链路全部通过")
