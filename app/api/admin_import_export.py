"""规则与项目的 yaml 导入导出。

导入是**全文件单事务**:任何一条校验不过就整体回滚返 422。不允许出现
「导入了一半」的中间状态——那种状态既不知道成功了哪几条,也不敢重试。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.rules_io import BundleParseError
from app.core.rules_sync import BundleValidationError, export_bundle, import_bundle
from app.db import repo_projects
from app.db.session import get_db

router = APIRouter(tags=["admin-import-export"])
log = logging.getLogger("aicheck.admin.io")

UNASSIGNED = "__none__"
_ALLOWED_SUFFIX = (".yaml", ".yml")


@router.get("/export", summary="导出规则为 yaml 文件")
def export_rules(
    project: str | None = Query(
        None, description=f"按项目 key 导出；传 {UNASSIGNED} 只导未分组；不传导全部"
    ),
    db: Session = Depends(get_db),
) -> Response:
    unassigned = project == UNASSIGNED
    project_key = None if unassigned else project

    if project_key and repo_projects.get_by_key(db, project_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目不存在: {project_key}",
        )

    text = export_bundle(db, project_key=project_key, unassigned=unassigned)

    stamp = datetime.now().strftime("%Y%m%d")
    if unassigned:
        filename = f"rules-unassigned-{stamp}.yaml"
    elif project_key:
        filename = f"rules-{project_key}-{stamp}.yaml"
    else:
        filename = f"rules-{stamp}.yaml"

    log.info("导出规则 project=%s file=%s", project or "全部", filename)
    return Response(
        content=text,
        media_type="application/x-yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", summary="从 yaml 文件导入规则(合并 upsert)")
async def import_rules(
    file: UploadFile, db: Session = Depends(get_db)
) -> dict[str, Any]:
    name = (file.filename or "").lower()
    if not name.endswith(_ALLOWED_SUFFIX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"只接受 .yaml / .yml 文件,收到: {file.filename}",
        )

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件不是 UTF-8 编码",
        ) from e

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件为空",
        )

    try:
        result = import_bundle(db, text)
        db.commit()
    except (BundleParseError, BundleValidationError) as e:
        db.rollback()
        log.warning("导入失败已回滚: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except Exception:
        db.rollback()
        log.exception("导入未捕获异常,已回滚")
        raise

    return result.to_api_dict()
