"""竣工图纸档案接口：整套上传、分段下载、版本留痕、移交锁定与一致性核对。

上传链路只做「收临时文件 → 交给服务做全量校验与一次性提交」：
临时文件无论成功失败都在 finally 里删掉，中断或校验退回都不会留下半份数据。
"""
from __future__ import annotations

import os
import tempfile
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.schemas import ActionResult, PageResult
from app.services.drawingarchive import ServiceError, service

router = APIRouter(prefix="/api/drawing-archives", tags=["竣工图纸档案"])

# 静态路径（consistency / handover-records）声明在动态路径之前，避免被 /{archive_no} 吞掉。


@router.get("/stats")
def archive_stats() -> dict[str, object]:
    """档案总量看板：档案总数、待移交、已移交、在档图纸数。"""
    return service.stats()


@router.get("/consistency")
def consistency() -> dict[str, object]:
    """核对档案总数与移交清单是否始终一致，逐条给出核对结果。"""
    return service.consistency_check()


@router.get("/handover-records")
def handover_records(keyword: str | None = Query(default=None, description="按档案编号、工程名称或接收人检索")) -> dict[str, object]:
    """移交清单：每次移交的档案、版本、接收人与分段范围都在这里。"""
    rows = service.list_handover_records(keyword=keyword)
    return {"items": rows, "total": len(rows)}


@router.get("", response_model=PageResult[dict])
def list_archives(
    keyword: str | None = Query(default=None, description="按档案编号或工程名称检索"),
    status: str | None = Query(default=None, description="待移交 / 已移交"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """分页读取档案列表；只返回摘要，明细走单档接口。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_archives(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("")
async def upload_package(
    package: UploadFile = File(..., description="整套图纸 ZIP"),
    archive_no: str = Form(default="", description="覆盖上传时填写已有档案编号；新建留空"),
    project_name: str = Form(default="", description="新建档案必填的工程名称"),
    operator: str = Form(default="值班管理员"),
    remark: str = Form(default="", description="本次覆盖说明"),
) -> dict[str, object]:
    """整套上传图纸：结构不符整包退回（422 并写明全部原因）；已移交档案拒绝改动（409）。

    先把上传字节完整落到临时文件，解析校验全部通过后才写档案仓库；
    连接中断、解析失败、校验退回都会在 finally 删除临时文件，不留半份数据。
    """
    suffix = ".zip"
    fd, tmp_path = tempfile.mkstemp(prefix="drawing-archive-", suffix=suffix)
    os.close(fd)
    try:
        with open(tmp_path, "wb") as handle:
            # 分块读取：大文件不一次性吃进内存，且客户端中断时抛出异常，临时文件随后清理。
            while True:
                chunk = await package.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        await package.close()
        return service.receive_package(
            tmp_path,
            form_archive_no=archive_no,
            project_name=project_name,
            operator=operator,
            remark=remark,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


@router.get("/{archive_no}")
def get_archive(archive_no: str) -> dict[str, object]:
    """读取单个档案的完整明细：分段、图纸清单、当前版本、移交状态。"""
    try:
        return service.get_archive(archive_no)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload)


@router.get("/{archive_no}/versions")
def list_versions(archive_no: str) -> dict[str, object]:
    """版本变更记录：每一版的新增、覆盖、删除图纸与操作说明。"""
    try:
        rows = service.list_versions(archive_no)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload)
    return {"archive_no": archive_no, "items": rows, "total": len(rows)}


@router.get("/{archive_no}/download")
def download_archive(
    archive_no: str,
    segment: str | None = Query(default=None, description="只下载某一个工程分段；不传则下载整套"),
) -> Response:
    """按工程分段（或整套）打包下载。

    包内根目录自带 archive-manifest.json（档案编号、版本、每张图纸 sha256），
    接收方把同一压缩包再传一次时，编号与版本可以直接核对。
    """
    try:
        body, filename, info = service.build_archive_zip(archive_no, segment=segment)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.payload)
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(
        content=body,
        media_type="application/zip",
        headers={"Content-Disposition": disposition, "X-Archive-No": str(info["archive_no"]),
                 "X-Archive-Version": str(info["version"])},
    )


@router.post("/{archive_no}/handover", response_model=ActionResult)
def handover_archive(
    archive_no: str,
    receiver: str = Form(..., description="接收人或接收单位"),
    operator: str = Form(default="值班管理员"),
    remark: str = Form(default="", description="移交备注"),
) -> ActionResult:
    """把图纸档案整体移交给接收人；移交成功后档案锁定，只能查看与下载。"""
    try:
        record = service.handover(archive_no, receiver, operator, remark)
    except ServiceError as exc:
        return ActionResult(ok=False, message=exc.payload["message"], entry=exc.payload)
    return ActionResult(
        ok=True,
        message=f"档案 {archive_no} v{record['version']} 已整体移交给 {record['receiver']}，档案已锁定",
        entry=record,
    )
