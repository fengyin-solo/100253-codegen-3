"""竣工图纸档案接口：整包上传、覆盖升版、分段下载、整体移交与一致性核对。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.schemas import PageResult
from app.services.drawingarchive import (
    MANIFEST_NAME,
    PackageRejected,
    drawing_store,
)

router = APIRouter(prefix="/api/drawing-archives", tags=["竣工图纸档案"])

ZIP_MEDIA_TYPE = "application/zip"


class HandoverPayload(BaseModel):
    """整体移交：把档案（当前版本快照）整体转给接收人。"""

    archive_no: str
    receiver: str = Field(min_length=1)
    remark: str | None = None


@router.get("", response_model=PageResult[dict])
def list_archives(
    keyword: str | None = Query(default=None, description="按档案编号或工程名称检索"),
    status: str | None = Query(default=None, description="在库、已移交"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """档案台账：编号、版本、状态、工程段数、图纸张数一目了然。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = drawing_store.list_archives(
        keyword=keyword, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/consistency")
def consistency() -> dict:
    """档案总数与移交清单一致性核对（含移交后内容是否被改动）。"""
    return drawing_store.consistency_report()


@router.get("/handovers")
def list_handovers() -> dict:
    """移交清单：每条对应一份已整体移交的档案及其版本快照。"""
    items = drawing_store.list_handovers()
    return {"total": len(items), "items": items}


@router.post("/handovers")
def create_handover(payload: HandoverPayload) -> dict:
    """办理整体移交；已移交的档案不能再次移交，移交后档案转为只读。"""
    try:
        record = drawing_store.hand_over(
            payload.archive_no.strip(),
            receiver=payload.receiver,
            operator="值班管理员",
            remark=payload.remark,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"档案 {payload.archive_no} 不存在") from None
    except PermissionError:
        raise HTTPException(
            status_code=409,
            detail=f"档案 {payload.archive_no} 已办理移交，只能查看，不能重复移交或改动",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "message": f"档案已整体移交接收人：{record['receiver']}", "handover": record}


@router.post("/upload")
async def upload_package(
    package: UploadFile = File(..., description="整套图纸压缩包（根目录/工程段/图纸）"),
    project_name: str | None = Form(default=None, description="首次建档时的工程名称；覆盖时以档案编号为准"),
    operator: str = Form(default="值班管理员"),
    remark: str | None = Form(default=None),
) -> dict:
    """整包上传：先解析暂存，全部校验通过后一次性提交。

    - 结构不合规：整包退回（422），逐条写明原因，库内不留任何半份数据；
    - 根目录清单里带档案编号：按编号覆盖升版，并说明覆盖/新增/未变的图纸；
    - 图纸内容一张没变：不升版，原样返回当前编号与版本（幂等）。
    """
    # 先把上传内容完整读进内存；读取中断（连接断开/包不完整）在这里就抛错，
    # 此时还没有触碰档案库，不会留下半份数据。
    content = await package.read()
    try:
        detail, result, extra = drawing_store.ingest(
            content, project_name=project_name, operator=operator, remark=remark
        )
    except PackageRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "压缩包结构不符合要求，整包退回", "reasons": exc.reasons},
        ) from None
    except PermissionError:
        raise HTTPException(
            status_code=409,
            detail="该档案已移交并锁定为只读，只能查看，不能覆盖改动；内容一致的回传仅用于核对",
        ) from None

    if result == "created":
        message = f"整套图纸已建档，档案编号 {detail['archive_no']}，版本 V1"
    elif result == "unchanged":
        message = (
            f"回传内容与档案库完全一致，未做改动：编号 {detail['archive_no']}，"
            f"版本 V{detail['version']}"
        )
    else:
        over = [item["drawing"] for item in extra["overwrites"]]
        parts = [
            f"档案 {detail['archive_no']} 已覆盖升版到 V{detail['version']}",
            (
                "覆盖图纸 "
                + ("、".join(over) if over else "无")
                + f"（共 {len(over)} 张）"
            ),
            f"新增 {len(extra['added'])} 张，删除 {len(extra['removed'])} 张，"
            f"内容未变 {len(extra['unchanged'])} 张",
        ]
        message = "；".join(parts)
    return {
        "ok": True,
        "result": result,
        "message": message,
        "overwrites": extra.get("overwrites", []),
        "added": extra.get("added", []),
        "removed": extra.get("removed", []),
        "unchanged": extra.get("unchanged", []),
        "archive": detail,
    }


@router.get("/{archive_no}")
def get_archive(archive_no: str) -> dict:
    """档案详情：工程段分组、每张图纸的大小与校验值、当前版本与状态。"""
    try:
        return drawing_store.require(archive_no)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"档案 {archive_no} 不存在") from None


@router.get("/{archive_no}/revisions")
def get_revisions(archive_no: str) -> dict:
    """变更记录：每一版新增/覆盖/删除了哪些图纸，以及前后内容指纹。"""
    try:
        items = drawing_store.revisions(archive_no)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"档案 {archive_no} 不存在") from None
    return {"archive_no": archive_no, "total": len(items), "items": items}


@router.get("/{archive_no}/download")
def download_full(archive_no: str) -> Response:
    """下载整套档案包：根目录/工程段/图纸 + 根目录下的档案清单（含编号、版本、指纹）。"""
    try:
        filename, data = drawing_store.package_full(archive_no)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"档案 {archive_no} 不存在") from None
    return _zip_response(filename, data)


@router.get("/{archive_no}/sections/{section}/download")
def download_section(archive_no: str, section: str) -> Response:
    """按工程段分段打包下载（分段包仅供查看，不能整包回传覆盖）。"""
    try:
        filename, data = drawing_store.package_section(archive_no, section)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"档案 {archive_no} 不存在") from None
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"档案 {archive_no} 下不存在工程段：{section}"
        ) from None
    return _zip_response(filename, data)


@router.get("/{archive_no}/sections/{section}/drawings/{drawing_name}")
def download_drawing(archive_no: str, section: str, drawing_name: str) -> Response:
    """查看/下载单张图纸；已移交档案同样可调，只读查看不受限。"""
    detail = drawing_store.get(archive_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"档案 {archive_no} 不存在")
    item = drawing_store.get_drawing(archive_no, section, drawing_name)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"工程段「{section}」下不存在图纸「{drawing_name}」",
        )
    filename = item["filename"]
    return Response(
        content=item["data"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename=download{_ext_of(filename)}; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


def _zip_response(filename: str, data: bytes) -> Response:
    return Response(
        content=data,
        media_type=ZIP_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f"attachment; filename=drawing-archive.zip; filename*=UTF-8''{quote(filename)}"
            ),
            "X-Manifest-Name": quote(MANIFEST_NAME),
        },
    )


def _ext_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:] if dot >= 0 else ""
