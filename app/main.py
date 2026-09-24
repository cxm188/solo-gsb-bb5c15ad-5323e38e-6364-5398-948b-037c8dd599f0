"""机织纹样浮长审计 HTTP API。"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from . import archives, db, weave, wif
from .models import (
    AuditOut,
    BalanceHotspotsIn,
    BalanceHotspotsOut,
    DesignArchiveIn,
    DesignArchiveOut,
    DesignArchiveVerifyOut,
    DesignCompareIn,
    DesignCompareOut,
    DesignIn,
    DesignOut,
    DesignPageOut,
    DesignSummary,
    DesignTagIn,
    DesignTagsOut,
    ErrorOut,
    FloatRiskZonesOut,
    LoomFitIn,
    LoomFitOut,
    RepeatPeriodOut,
    RepairPlanOut,
    ThresholdProfileOut,
    TieUpSuggestionOut,
    Violation,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时建表（幂等）
    db.init_db()
    yield


app = FastAPI(
    title="Weave Float Audit API",
    summary="机织纹样浮长审计：生成经纬交织矩阵并审计超限浮长",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


def _audit_and_store(
    *,
    name: str | None,
    cyclic: bool,
    max_float: int,
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
) -> dict:
    """共用的「校验 → 生成交织矩阵 → 浮长审计 → 同事务入库」流程，
    返回刚入库设计的完整结果。校验失败抛 weave.DesignError。"""
    shafts, treadles = weave.validate_design(
        threading, tie_up, treadling, max_float
    )
    interlacing, violations = weave.audit_floats(
        threading,
        tie_up,
        treadling,
        max_float,
        cyclic,
    )
    violation_dicts = [v.to_dict() for v in violations]
    design_id = db.insert_design_with_audit(
        name=name,
        cyclic=cyclic,
        max_float=max_float,
        shafts=shafts,
        treadles=treadles,
        threading_map=threading,
        tie_up=tie_up,
        treadling=treadling,
        interlacing=interlacing,
        violations=violation_dicts,
    )
    stored = db.get_design(design_id)
    assert stored is not None  # 刚写入，必然存在
    return stored


@app.post(
    "/designs",
    status_code=201,
    response_model=DesignOut,
    responses={400: {"model": ErrorOut, "description": "维度不一致或索引越界，整份设计被拒绝"}},
    tags=["designs"],
)
def submit_design(design: DesignIn) -> dict:
    """提交设计：校验 → 生成交织矩阵 → 浮长审计 → 入库，返回完整结果。"""
    try:
        return _audit_and_store(
            name=design.name,
            cyclic=design.cyclic,
            max_float=design.max_float,
            threading=design.threading,
            tie_up=design.tie_up,
            treadling=design.treadling,
        )
    except weave.DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/designs",
    response_model=list[DesignSummary],
    tags=["designs"],
)
def list_designs() -> list[dict]:
    """列出全部已入库的合法设计（摘要）。"""
    return db.list_designs()


_NONNEG_INT_RE = re.compile(r"^[0-9]+$")


def _parse_page_param(name: str, raw: str | None) -> int:
    """解析分页查询参数：只接受无符号十进制整数字面量（拒绝 +/−、空白、
    小数、八进制写法等），失败抛 400。"""
    if raw is None or not _NONNEG_INT_RE.fullmatch(raw):
        raise HTTPException(
            status_code=400,
            detail=f"query param {name} must be an integer, got {raw!r}",
        )
    return int(raw)


# 路由声明在 /designs/{design_id} 之前，避免 "page" 落入整型路径参数
@app.get(
    "/designs/page",
    response_model=DesignPageOut,
    responses={
        400: {"model": ErrorOut, "description": "分页参数非法：limit 缺失或不在"
              " 1～100，after_id 非正整数"}
    },
    tags=["designs"],
)
def list_designs_page(
    limit: str | None = Query(
        default=None,
        description="每页返回条数（必填，1～100）；SQL 实际多取 1 条用于"
        "判断 has_more",
    ),
    after_id: str | None = Query(
        default=None,
        description="游标：仅返回设计 ID 严格大于该值的记录；首页省略，"
        "下一页传上一页返回的 next_after_id",
    ),
) -> dict:
    """只读分页列出设计摘要（按设计 ID 升序的 keyset 分页）。

    游标为严格大于比较且设计 ID 单调递增：下一页从上一页最后返回的 ID
    之后继续，**遍历期间新增的设计只会出现在游标之后，已返回项不会重复**。
    limit 缺失或不在 1～100、after_id 非正整数均返回 400。
    """
    limit_value = _parse_page_param("limit", limit)
    if not 1 <= limit_value <= 100:
        raise HTTPException(
            status_code=400,
            detail=f"query param limit must be within 1..100, got {limit_value}",
        )
    after_id_value: int | None = None
    if after_id is not None:
        after_id_value = _parse_page_param("after_id", after_id)
        if after_id_value < 1:
            raise HTTPException(
                status_code=400,
                detail=f"query param after_id must be a positive integer, "
                f"got {after_id_value}",
            )
    return db.list_designs_page(limit=limit_value, after_id=after_id_value)


@app.get(
    "/designs/{design_id}",
    response_model=DesignOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def get_design(design_id: int) -> dict:
    """按 ID 查询已入库的设计及其审计结果。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    return stored


@app.post(
    "/designs/{design_id}/tags",
    status_code=201,
    response_model=DesignTagsOut,
    responses={
        400: {"model": ErrorOut, "description": "标签为空或长度超过 32，整次写入被拒绝"},
        404: {"model": ErrorOut, "description": "设计不存在"},
        409: {"model": ErrorOut, "description": "同一设计下该标签已存在"},
    },
    tags=["designs"],
)
def add_design_tag(design_id: int, request: DesignTagIn) -> dict:
    """为已入库设计追加一个去重的简体中文标签。校验（设计存在、标签非空且
    长度不超过 32、同一设计不重复）与写入在同一 SQLite 事务内完成：设计不
    存在返回 404，标签非法返回 400，重复标签返回 409，失败均不写库。成功
    返回该设计的全部标签（按首次添加顺序）与数量，不改变原设计与审计结果。"""
    try:
        return db.add_design_tag(design_id, request.tag)
    except db.DesignNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"design {exc.args[0]} not found"
        ) from exc
    except db.TagError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except db.DuplicateTagError as exc:
        design_id, tag = exc.args
        raise HTTPException(
            status_code=409,
            detail=f"tag {tag!r} already exists for design {design_id}",
        ) from exc


@app.get(
    "/designs/{design_id}/tags",
    response_model=DesignTagsOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def list_design_tags(design_id: int) -> dict:
    """查询设计的全部标签，按首次添加顺序返回标签列表与数量。只读，不改变
    原设计与审计结果；设计不存在返回 404。"""
    try:
        return db.list_design_tags(design_id)
    except db.DesignNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"design {exc.args[0]} not found"
        ) from exc


@app.get(
    "/designs/{design_id}/wif",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {}}, "description": "WIF 1.1 纯文本导出"},
        404: {"model": ErrorOut, "description": "设计不存在"},
    },
    tags=["designs"],
)
def export_wif(design_id: int) -> PlainTextResponse:
    """WIF 1.1 导出：把已入库设计的 threading、tie_up、treadling、cyclic 与
    max_float 按固定段落（[WIF]/[CONTENTS]/[TEXTPLAN]/[TIEUP]/[THREADING]/
    [TREADLING]/[WEAVING]）与 1 基索引序列化为 text/plain。设计不存在返回 404。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    text = wif.serialize_wif(
        name=stored["name"],
        cyclic=stored["cyclic"],
        max_float=stored["max_float"],
        threading=stored["threading"],
        tie_up=stored["tie_up"],
        treadling=stored["treadling"],
    )
    return PlainTextResponse(text, media_type=wif.WIF_CONTENT_TYPE)


@app.post(
    "/designs/import-wif",
    status_code=201,
    response_model=DesignOut,
    responses={
        400: {
            "model": ErrorOut,
            "description": "WIF 解析 / 校验失败（段落缺失、尺寸不一致、索引越界、"
            "绑综值非 0/1 等），整份设计被拒绝且不写库",
        }
    },
    tags=["designs"],
)
async def import_wif(request: Request) -> dict:
    """WIF 1.1 导入：解析 text/plain 请求体中的 [WIF]、[CONTENTS]、[WEAVING]
    等固定段落（1 基索引转 0 基），校验尺寸、索引范围与 0/1 绑综值后，复用
    与 POST /designs 完全相同的校验、交织矩阵生成、浮长审计与入库流程，
    返回完整结果。任何解析失败返回 400 且不写库。"""
    raw = await request.body()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="WIF body must be valid UTF-8 text"
        ) from exc
    try:
        parsed = wif.parse_wif(text)
        return _audit_and_store(**parsed)
    except weave.DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/designs/{design_id}/audit",
    response_model=AuditOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def get_audit(design_id: int) -> dict:
    """按设计 ID 单独查询审计结果。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    return stored["audit"]


@app.get(
    "/designs/{design_id}/repeat-period",
    response_model=RepeatPeriodOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def get_repeat_period(design_id: int) -> dict:
    """最小循环单元分析：读取已入库设计并重新生成交织矩阵，在经、纬两个方向
    寻找能无缝平铺整幅矩阵的最小正周期（周期整除对应尺寸），返回循环单元。
    比较基于交织矩阵本身，而非分别检查穿综/踏综；取模回绕比较对声明循环
    （cyclic）设计的首尾边界同样成立。任一方向不存在更小周期时取整幅尺寸。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    period = weave.analyze_repeat_period(
        stored["threading"],
        stored["tie_up"],
        stored["treadling"],
        stored["max_float"],
    )
    return period.to_dict()


@app.get(
    "/designs/{design_id}/threshold-profile",
    response_model=ThresholdProfileOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def get_threshold_profile(
    design_id: int,
    max_float_limit: int | None = Query(
        default=None,
        ge=1,
        le=64,
        description="逐档审计的阈值上限（1～64）；缺省取原设计的 max_float"
        "（若超过 64 则按 64 封顶）",
    ),
) -> dict:
    """阈值敏感性分析：读取已入库设计，在 1..max_float_limit 内逐档按原
    cyclic 语义重跑完整经纬浮长审计，返回每档阈值、经向/纬向违规数、总数，
    以及该设计达到零违规的最小阈值（上限内达不到则为 null）。接口只读
    计算，不修改数据库；设计不存在返回 404。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    limit = (
        max_float_limit
        if max_float_limit is not None
        else min(stored["max_float"], 64)
    )
    profile = weave.threshold_profile(
        stored["threading"],
        stored["tie_up"],
        stored["treadling"],
        stored["cyclic"],
        limit,
    )
    return {
        "design_id": design_id,
        "cyclic": stored["cyclic"],
        "max_float": stored["max_float"],
        **profile.to_dict(),
    }


@app.post(
    "/designs/{design_id}/tie-up-suggestions",
    response_model=list[TieUpSuggestionOut],
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def suggest_tie_up(
    design_id: int,
    limit: int = Query(default=50, ge=1, le=50, description="返回候选数上限"),
) -> list[dict]:
    """单步绑综修复建议：逐一翻转 tie_up 的一个 0/1 单元并按原 cyclic、
    max_float 重跑完整审计，仅返回违规总数严格减少的候选（不修改原设计）。
    结果按剩余违规数、受影响交织点数、踏板索引、综框索引升序排列。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    suggestions = weave.suggest_tie_up_flips(
        stored["threading"],
        stored["tie_up"],
        stored["treadling"],
        stored["max_float"],
        stored["cyclic"],
    )
    return [s.to_dict() for s in suggestions[:limit]]


@app.post(
    "/designs/{design_id}/repair-plans",
    response_model=list[RepairPlanOut],
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def repair_plans(    design_id: int,
    max_changes: int = Query(
        default=2, ge=1, le=3, description="最多翻转绑综单元的次数（1～3）"
    ),
    limit: int = Query(default=50, ge=1, le=50, description="返回方案数上限"),
) -> list[dict]:
    """多步绑综修复方案：在最多 max_changes 次绑综单元翻转内搜索终态违规总数
    严格少于原设计的方案。中间状态允许违规数不下降；按绑综状态去重，同一单元
    不在一条序列内重复翻转；等价终态只保留排序最优的序列。接口只读计算，
    不修改原设计；设计不存在返回 404，无改善时返回空列表 []。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    plans = weave.search_repair_plans(
        stored["threading"],
        stored["tie_up"],
        stored["treadling"],
        stored["max_float"],
        stored["cyclic"],
        max_changes=max_changes,
        limit=limit,
    )
    return [p.to_dict() for p in plans]


@app.post(
    "/designs/{design_id}/compare",
    response_model=DesignCompareOut,
    responses={
        400: {"model": ErrorOut, "description": "两份设计的交织矩阵尺寸不一致"},
        404: {"model": ErrorOut, "description": "任一设计不存在"},
    },
    tags=["designs"],
)
def compare_designs(design_id: int, request: DesignCompareIn) -> dict:
    """设计对齐比对：读取两份已入库设计并重新生成交织矩阵（尺寸必须一致，
    否则 400），枚举第二份矩阵在经、纬方向的所有循环错位，取不一致单元最少
    的对齐结果；平局按行偏移再列偏移升序。返回最佳偏移、错位数量与比例、
    错位坐标、按行/列汇总及对齐后的矩阵。接口只读计算，不改库。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    other = db.get_design(request.other_design_id)
    if other is None:
        raise HTTPException(
            status_code=404, detail=f"design {request.other_design_id} not found"
        )
    matrix_a = weave.build_interlacing(
        stored["threading"], stored["tie_up"], stored["treadling"]
    )
    matrix_b = weave.build_interlacing(
        other["threading"], other["tie_up"], other["treadling"]
    )
    try:
        result = weave.best_alignment(matrix_a, matrix_b)
    except weave.DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "design_id": design_id,
        "other_design_id": request.other_design_id,
        "warp_ends": stored["warp_ends"],
        "weft_picks": stored["weft_picks"],
        **result.to_dict(),
    }


@app.post(
    "/designs/{design_id}/balance-hotspots",
    response_model=BalanceHotspotsOut,
    responses={
        400: {"model": ErrorOut, "description": "循环设计的窗口尺寸超过交织矩阵尺寸"},
        404: {"model": ErrorOut, "description": "设计不存在"},
    },
    tags=["designs"],
)
def balance_hotspots(design_id: int, request: BalanceHotspotsIn) -> dict:
    """局部交织均衡审计：读取已入库设计并重新生成交织矩阵，按给定窗口逐窗
    统计 1（经纱在上）的占比。非循环设计只扫描完整落入矩阵的窗口（窗口大于
    矩阵时无窗可扫）；循环设计以每个原矩阵单元为左上角、两轴取模回绕，窗口
    不得超过矩阵尺寸（否则 400）。返回按左上角坐标稳定排序的超界窗口、占比
    及偏离最近阈值的幅度，并汇总最严重窗口（无超界时为 null）。接口只读
    计算，不改库。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    try:
        result = weave.scan_balance_hotspots(
            stored["threading"],
            stored["tie_up"],
            stored["treadling"],
            stored["cyclic"],
            request.window_rows,
            request.window_cols,
            request.warp_ratio_min,
            request.warp_ratio_max,
        )
    except weave.DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "design_id": design_id,
        "cyclic": stored["cyclic"],
        **result.to_dict(),
    }


@app.get(
    "/designs/{design_id}/float-risk-zones",
    response_model=FloatRiskZonesOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def get_float_risk_zones(design_id: int) -> dict:
    """风险区域聚合：直接读取**已入库**的交织矩阵与原有浮长违规记录
    （不重新审计），把每条超限经/纬浮长展开为其覆盖的矩阵单元，再在
    经纬四邻接规则下合并为连通区域——设计声明循环时矩阵经纬两轴首尾
    相接（环面拓扑），仅对角接触的单元不合并。返回按严重度降序的区域
    列表（每区给出单元坐标、包围盒、面积、最大浮长与涉及方向）与区域
    总数；设计不存在返回 404，无违规时返回空列表。接口只读，不改库。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    audit = stored["audit"]
    result = weave.aggregate_float_risk_zones(
        audit["interlacing"], audit["violations"], stored["cyclic"]
    )
    return {
        "design_id": design_id,
        "cyclic": stored["cyclic"],
        **result.to_dict(),
    }


@app.post(
    "/designs/{design_id}/loom-fit",
    response_model=LoomFitOut,
    responses={404: {"model": ErrorOut, "description": "设计不存在"}},
    tags=["designs"],
)
def loom_fit(design_id: int, request: LoomFitIn) -> dict:
    """织机适配：读取已入库设计并重新生成交织矩阵，按**完整列向量相同的经纱
    归入同一约简综框、完整行向量相同的纬纱归入同一约简踏板**（等价类按首次
    出现顺序从 0 编号）重建 threading / tie_up / treadling，并逐点校验重建
    矩阵与原矩阵一致。返回不同列/行向量数——即织出同一矩阵所需的**最少
    综框 / 踏板数**、约简草案及相对原设计的减少量；仅当两项最少数量均不超过
    设备上限（max_shafts、max_treadles，1～64）时 `fits=true`。接口只读
    计算，不改库；设计不存在返回 404。"""
    stored = db.get_design(design_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"design {design_id} not found")
    reduction = weave.reduce_to_loom(
        stored["threading"],
        stored["tie_up"],
        stored["treadling"],
        stored["max_float"],
    )
    return {
        "design_id": design_id,
        "warp_ends": stored["warp_ends"],
        "weft_picks": stored["weft_picks"],
        "max_shafts": request.max_shafts,
        "max_treadles": request.max_treadles,
        "fits": (
            reduction.min_shafts <= request.max_shafts
            and reduction.min_treadles <= request.max_treadles
        ),
        "min_shafts": reduction.min_shafts,
        "min_treadles": reduction.min_treadles,
        "original_shafts": reduction.original_shafts,
        "original_treadles": reduction.original_treadles,
        "shafts_reduction": reduction.original_shafts - reduction.min_shafts,
        "treadles_reduction": reduction.original_treadles - reduction.min_treadles,
        "reduced_draft": {
            "threading": reduction.threading,
            "tie_up": reduction.tie_up,
            "treadling": reduction.treadling,
            "interlacing": reduction.interlacing,
        },
    }


def _archive_payload(record: dict, reused: bool) -> dict:
    return {
        "id": record["id"],
        "digest": record["digest"],
        "design_ids": record["design_ids"],
        "design_count": record["design_count"],
        "manifest": record["manifest"],
        "reused": reused,
        "created_at": record["created_at"],
    }


@app.post(
    "/design-archives",
    response_model=DesignArchiveOut,
    responses={
        200: {"model": DesignArchiveOut, "description": "命中相同 digest 的既有归档，幂等复用（未新增数据）"},
        201: {"model": DesignArchiveOut, "description": "新建归档"},
        400: {"model": ErrorOut, "description": "请求 ID 列表含重复项"},
        404: {"model": ErrorOut, "description": "存在尚未入库的设计 ID，整次归档不写库"},
    },
    tags=["design-archives"],
)
def create_design_archive(request: DesignArchiveIn, response: Response) -> dict:
    """设计归档：接收 1～50 个已入库设计 ID，**按 ID 升序去重**后在同一读取
    流程中取出每份设计的参数与审计结果，生成规范化（键字典序、无空白、UTF-8）
    JSON 清单（`design-archive-v1`），并计算其 SHA-256 摘要持久化。

    - 列表含重复 ID 返回 **400**，不写库；存在未入库 ID 返回 **404**
      （detail 列出全部缺失 ID），不写库——校验、读取全部成功后才执行唯一
      一次写入，**归档失败不产生半成品**；
    - 摘要相同（同一批设计、与提交顺序无关）**幂等复用**同一条归档记录，
      返回 **200** 与 `reused=true`；首次归档返回 **201**、`reused=false`。
    """
    design_ids = sorted(set(request.design_ids))
    if len(design_ids) != len(request.design_ids):
        raise HTTPException(
            status_code=400,
            detail="design_ids must not contain duplicates",
        )
    stored = db.get_designs_many(design_ids)
    missing = [
        design_id for design_id, item in zip(design_ids, stored) if item is None
    ]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"designs not found: {', '.join(str(i) for i in missing)}",
        )
    manifest = archives.build_manifest(stored)  # type: ignore[arg-type]
    manifest_text = archives.canonical_text(manifest)
    digest = archives.digest_of(manifest)
    record, reused = db.insert_archive_or_get(
        digest=digest, design_ids=design_ids, manifest_text=manifest_text
    )
    # 复用既有归档返回 200，首次创建返回 201（状态码无法用单一 status_code
    # 声明，这里显式设置）。
    response.status_code = 200 if reused else 201
    return _archive_payload(record, reused)


@app.get(
    "/design-archives/{archive_id}/verify",
    response_model=DesignArchiveVerifyOut,
    responses={404: {"model": ErrorOut, "description": "归档不存在"}},
    tags=["design-archives"],
)
def verify_design_archive(archive_id: int) -> dict:
    """归档完整性校验：按归档时记录的设计 ID 列表重新读取**当前**的设计参数
    与审计结果，重建规范化清单并重算 SHA-256 摘要，与归档摘要比对。

    - `consistent`：重算摘要与归档摘要一致且无缺失设计项时为 `true`；
    - `missing_design_ids`：归档后被删除（或当前不存在）的设计 ID（升序）；
    - `first_diff_path`：归档清单与当前内容的首个差异路径（JSON Pointer
      风格，如 `/items/0/audit/exceeds`、`/design_count`），一致时为 `null`。
    接口只读计算，不修改归档与设计。
    """
    record = db.get_archive(archive_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"design archive {archive_id} not found"
        )
    design_ids = record["design_ids"]
    current_rows = db.get_designs_many(design_ids)
    missing = [
        design_id
        for design_id, item in zip(design_ids, current_rows)
        if item is None
    ]
    present = [item for item in current_rows if item is not None]
    current_manifest = archives.build_manifest(present)
    recomputed = archives.digest_of(current_manifest)
    stored_manifest = record["manifest"]
    first_diff = archives.first_diff_path(stored_manifest, current_manifest)
    consistent = recomputed == record["digest"] and not missing
    return {
        "archive_id": archive_id,
        "digest": record["digest"],
        "recomputed_digest": recomputed,
        "consistent": consistent,
        "missing_design_ids": missing,
        "first_diff_path": first_diff,
    }
