"""请求 / 响应模型。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, Strict, model_validator

# 归档请求中的设计 ID：严格整数（拒绝 "1"、1.0 等宽松转换）且 ≥ 1。
StrictDesignId = Annotated[int, Strict(), Field(ge=1)]


class DesignIn(BaseModel):
    """一份待审计的机织纹样设计。"""

    name: str | None = Field(default=None, description="设计名称（可选）")
    cyclic: bool = Field(
        default=False,
        description="纹样是否循环；为 true 时合并矩阵首尾跨边界的连续浮长段",
    )
    max_float: int = Field(..., ge=1, description="允许的最大浮长，超过即判违规")
    threading: list[int] = Field(
        ..., description="经纱穿综映射：threading[w] 为第 w 根经纱所穿综框的索引"
    )
    tie_up: list[list[int]] = Field(
        ...,
        description="绑综矩阵：tie_up[t][s] ∈ {0,1}，踏板 t 是否提升综框 s；"
        "行数 = 踏板数，列数 = 综框数",
    )
    treadling: list[int] = Field(
        ..., description="逐纬踏综序列：treadling[h] 为第 h 纬踩下的踏板索引"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "2/2 twill, 8x8 repeat",
                    "cyclic": True,
                    "max_float": 2,
                    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
                    "tie_up": [
                        [1, 1, 0, 0],
                        [0, 1, 1, 0],
                        [0, 0, 1, 1],
                        [1, 0, 0, 1],
                    ],
                    "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
                }
            ]
        }
    }


class Violation(BaseModel):
    direction: Literal["warp", "weft"] = Field(
        ..., description="warp=经向浮长（经纱连续浮于纬上），weft=纬向浮长"
    )
    yarn_index: int = Field(..., description="纱线索引：经纱列号或纬纱行号")
    start: int = Field(
        ..., description="连续段起点；循环合并段取尾部段的起点（跨边界延伸）"
    )
    length: int = Field(..., description="连续浮长")


class AuditOut(BaseModel):
    exceeds: bool = Field(..., description="是否存在超过 max_float 的浮长")
    interlacing: list[list[int]] = Field(
        ..., description="经纬交织矩阵，1=经纱在上，0=纬纱在上"
    )
    violations: list[Violation]


class DesignOut(BaseModel):
    id: int
    name: str | None
    cyclic: bool
    max_float: int
    shafts: int
    treadles: int
    warp_ends: int
    weft_picks: int
    threading: list[int]
    tie_up: list[list[int]]
    treadling: list[int]
    created_at: str
    audit: AuditOut


class DesignSummary(BaseModel):
    id: int
    name: str | None
    cyclic: bool
    max_float: int
    shafts: int
    treadles: int
    warp_ends: int
    weft_picks: int
    exceeds: bool
    created_at: str


class DesignPageOut(BaseModel):
    """GET /designs/page 的 keyset 分页结果。"""

    items: list[DesignSummary] = Field(
        ..., description="本页设计摘要，按设计 ID 升序，至多 limit 条"
    )
    next_after_id: int | None = Field(
        ...,
        description="下一页游标：has_more 时为本页最后一条设计的 ID（作为下次"
        "请求的 after_id）；无后续页时为 null",
    )
    has_more: bool = Field(
        ..., description="after_id（或本页游标）之后是否还有未返回的设计"
    )


class TieUpSuggestionOut(BaseModel):
    """单步绑综修复建议：翻转一个 tie_up 单元后违规总数严格减少的候选。"""

    treadle: int = Field(..., description="踏板索引（tie_up 行号）")
    shaft: int = Field(..., description="综框索引（tie_up 列号）")
    new_value: Literal[0, 1] = Field(..., description="该单元翻转后的新值")
    remaining_violations: int = Field(
        ..., description="翻转后按原 cyclic、max_float 重跑审计的剩余违规总数"
    )
    affected_points: int = Field(
        ..., description="受影响交织点数：翻转前后交织矩阵中取值变化的单元数"
    )


class RepairPlanStepOut(BaseModel):
    """修复方案中的一步绑综翻转。"""

    step: int = Field(..., description="第几步翻转（从 1 开始）")
    treadle: int = Field(..., description="踏板索引（tie_up 行号）")
    shaft: int = Field(..., description="综框索引（tie_up 列号）")
    old_value: Literal[0, 1] = Field(..., description="翻转前该单元的值")
    new_value: Literal[0, 1] = Field(..., description="翻转后该单元的值")
    violations_after: int = Field(
        ..., description="本次翻转后完整审计的违规总数（中间状态允许不下降）"
    )


class RepairPlanOut(BaseModel):
    """多步绑综修复方案：终态违规总数严格少于原设计。"""

    changes: int = Field(..., description="方案翻转次数（1～max_changes）")
    final_violations: int = Field(..., description="最终违规总数")
    changed_points: int = Field(
        ..., description="最终交织矩阵相对原矩阵取值变化的点数"
    )
    steps: list[RepairPlanStepOut] = Field(..., description="逐步翻转序列")
    violations: list[Violation] = Field(..., description="最终违规列表")
    interlacing: list[list[int]] = Field(
        ..., description="最终交织矩阵，1=经纱在上，0=纬纱在上"
    )


class RepeatPeriodOut(BaseModel):
    """交织矩阵的最小循环单元分析。"""

    warp_period: int = Field(
        ..., description="经向最小正周期（一个循环单元的列数/经纱数），整除经纱数"
    )
    weft_period: int = Field(
        ..., description="纬向最小正周期（一个循环单元的行数/纬纱数），整除纬纱数"
    )
    unit: list[list[int]] = Field(
        ...,
        description="最小循环单元矩阵（weft_period 行 × warp_period 列），"
        "1=经纱在上，0=纬纱在上；在经纬两方向重复平铺可无缝还原整幅交织矩阵",
    )
    strict_2d_repeat: bool = Field(
        ...,
        description="是否为严格二维重复：仅当 warp_period、weft_period 均严格小于"
        "整幅对应尺寸（单元在两个方向都至少平铺 2 次）时为 true",
    )


class ErrorOut(BaseModel):
    detail: str


class RiskCellOut(BaseModel):
    """风险区域内的一个交织矩阵单元。"""

    row: int = Field(..., description="单元所在纬纱行号")
    col: int = Field(..., description="单元所在经纱列号")


class RiskBBoxOut(BaseModel):
    """风险区域的轴对齐包围盒（取区域内单元的最小/最大行列号）。"""

    min_row: int = Field(..., description="包围盒顶行（最小行号）")
    min_col: int = Field(..., description="包围盒左列（最小列号）")
    max_row: int = Field(..., description="包围盒底行（最大行号）")
    max_col: int = Field(..., description="包围盒右列（最大列号）")
    rows: int = Field(..., description="包围盒高度：max_row - min_row + 1")
    cols: int = Field(..., description="包围盒宽度：max_col - min_col + 1")


class RiskZoneOut(BaseModel):
    """一片由超限浮长单元四邻接合并而成的连通风险区域。"""

    cells: list[RiskCellOut] = Field(
        ..., description="区域内矩阵单元坐标列表，按行、列升序"
    )
    bbox: RiskBBoxOut = Field(
        ...,
        description="轴对齐包围盒（原矩阵坐标）；循环设计中跨首尾边界的区域"
        "其包围盒可能横跨整条边（rows/cols 等于矩阵对应尺寸），单元的"
        "精确集合以 cells 为准",
    )
    area: int = Field(..., description="区域面积 = 区域内单元数")
    max_float: int = Field(..., description="区域内单元涉及的最大浮长")
    directions: list[Literal["warp", "weft"]] = Field(
        ...,
        description="区域涉及的浮长方向（去重，warp 在 weft 前）：仅经向、"
        "仅纬向或两者兼有",
    )


class FloatRiskZonesOut(BaseModel):
    """超限浮长单元的连通区域聚合结果。"""

    design_id: int = Field(..., description="被聚合的设计 ID")
    cyclic: bool = Field(
        ..., description="聚合沿用的设计循环声明（true 时经纬两轴首尾相接）"
    )
    warp_ends: int = Field(..., description="经纱数（交织矩阵列数）")
    weft_picks: int = Field(..., description="纬纱数（交织矩阵行数）")
    zone_count: int = Field(..., description="风险区域总数（无违规时为 0）")
    zones: list[RiskZoneOut] = Field(
        ...,
        description="风险区域列表，按严重度降序：max_float 大者优先，"
        "其次面积大者，再次取区域内最小单元坐标（行后列）升序",
    )


class BalanceHotspotsIn(BaseModel):
    """局部交织均衡审计请求。"""

    window_rows: int = Field(
        ..., ge=1, le=64, description="窗口行数（纬向高度），1～64"
    )
    window_cols: int = Field(
        ..., ge=1, le=64, description="窗口列数（经向宽度），1～64"
    )
    warp_ratio_min: float = Field(
        ..., ge=0.0, le=1.0,
        description="窗口内 1（经纱在上）占比的允许下限（含），0～1",
    )
    warp_ratio_max: float = Field(
        ..., ge=0.0, le=1.0,
        description="窗口内 1（经纱在上）占比的允许上限（含），0～1",
    )

    @model_validator(mode="after")
    def _thresholds_ordered(self):
        if self.warp_ratio_min > self.warp_ratio_max:
            raise ValueError("warp_ratio_min must not exceed warp_ratio_max")
        return self


class BalanceHotspotOut(BaseModel):
    """一个超界窗口：左上角坐标、占比与偏离最近阈值的幅度。"""

    row: int = Field(..., description="窗口左上角纬纱行号")
    col: int = Field(..., description="窗口左上角经纱列号")
    warp_ratio: float = Field(..., description="窗口内 1（经纱在上）的占比")
    deviation: float = Field(..., description="占比偏离最近阈值的幅度")


class BalanceHotspotsOut(BaseModel):
    """局部交织均衡审计结果。"""

    design_id: int = Field(..., description="被审计的设计 ID")
    cyclic: bool = Field(..., description="审计沿用的设计循环声明")
    window_rows: int = Field(..., description="窗口行数（纬向高度）")
    window_cols: int = Field(..., description="窗口列数（经向宽度）")
    warp_ratio_min: float = Field(..., description="占比允许下限（含）")
    warp_ratio_max: float = Field(..., description="占比允许上限（含）")
    windows_scanned: int = Field(..., description="实际扫描的窗口总数")
    hotspots: list[BalanceHotspotOut] = Field(
        ..., description="超界窗口列表，按左上角行、列升序稳定排序"
    )
    worst_hotspot: BalanceHotspotOut | None = Field(
        ..., description="偏离最近阈值幅度最大的超界窗口；无超界时为 null"
    )


class ThresholdProfilePointOut(BaseModel):
    """单档阈值下完整经纬浮长审计的违规汇总。"""

    max_float: int = Field(..., description="本档允许的最大浮长阈值，超过即判违规")
    warp_violations: int = Field(..., description="该阈值下的经向浮长违规数")
    weft_violations: int = Field(..., description="该阈值下的纬向浮长违规数")
    total_violations: int = Field(
        ..., description="该阈值下的违规总数（经向 + 纬向）"
    )


class ThresholdProfileOut(BaseModel):
    """阈值敏感性分析：1..max_float_limit 逐档重跑完整浮长审计的结果。"""

    design_id: int = Field(..., description="被分析的设计 ID")
    cyclic: bool = Field(..., description="逐档审计沿用的设计循环声明")
    max_float: int = Field(..., description="设计入库时声明的最大浮长阈值")
    max_float_limit: int = Field(..., description="本次逐档审计的阈值上限")
    min_zero_violation_threshold: int | None = Field(
        ...,
        description="达到零违规的最小阈值；上限内无法达到零违规时为 null",
    )
    profile: list[ThresholdProfilePointOut] = Field(
        ..., description="逐档审计结果，按阈值 1..max_float_limit 升序"
    )


class DesignCompareIn(BaseModel):
    """设计对齐比对请求。"""

    other_design_id: int = Field(
        ..., ge=1, description="参与比对的另一份已入库设计 ID"
    )


class DesignTagIn(BaseModel):
    """为已入库设计添加一个简体中文标签。

    非空与长度（≤32）的业务校验在 SQLite 写入事务内完成，空串因此返回
    400 而非框架层 422。
    """

    tag: str = Field(..., description="标签文本；非空，长度不超过 32 个字符")


class DesignTagOut(BaseModel):
    """设计的一个标签。"""

    id: int = Field(..., description="标签记录 ID（按首次添加顺序递增）")
    tag: str = Field(..., description="标签文本")
    created_at: str = Field(..., description="首次添加时间（UTC，ISO 8601）")


class DesignTagsOut(BaseModel):
    """设计的标签集合：按首次添加顺序返回。"""

    design_id: int = Field(..., description="所属设计 ID")
    tags: list[DesignTagOut] = Field(
        ..., description="标签列表，按首次添加（id）升序，同一设计内去重"
    )
    count: int = Field(..., description="标签总数")


class LoomFitIn(BaseModel):
    """织机适配（最少综框/踏板约简）请求。"""

    max_shafts: int = Field(
        ..., ge=1, le=64, description="设备可用综框数上限，1～64"
    )
    max_treadles: int = Field(
        ..., ge=1, le=64, description="设备可用踏板数上限，1～64"
    )


class ReducedDraftOut(BaseModel):
    """基于交织矩阵等价类约简出的最少综框/踏板穿综草案。"""

    threading: list[int] = Field(
        ...,
        description="约简穿综：threading[w] 为第 w 根经纱归入的约简综框号"
        "（等价类按列向量首次出现顺序从 0 编号）",
    )
    tie_up: list[list[int]] = Field(
        ...,
        description="约简绑综矩阵：min_treadles 行 × min_shafts 列，"
        "1=踏板提升该约简综框，0=不提升",
    )
    treadling: list[int] = Field(
        ...,
        description="约简踏综：treadling[h] 为第 h 纬归入的约简踏板号"
        "（等价类按行向量首次出现顺序从 0 编号）",
    )
    interlacing: list[list[int]] = Field(
        ...,
        description="由约简草案逐点重建的交织矩阵（与原设计交织矩阵完全一致）",
    )


class LoomFitOut(BaseModel):
    """织机适配结果：交织矩阵驱动的最少综框/踏板约简。"""

    design_id: int = Field(..., description="被分析的设计 ID")
    warp_ends: int = Field(..., description="经纱数（约简前后不变）")
    weft_picks: int = Field(..., description="纬纱数（约简前后不变）")
    max_shafts: int = Field(..., description="本次设备综框数上限（1～64）")
    max_treadles: int = Field(..., description="本次设备踏板数上限（1～64）")
    fits: bool = Field(
        ...,
        description="是否适配：仅当 min_shafts、min_treadles 均不超过对应上限时为 true",
    )
    min_shafts: int = Field(
        ...,
        description="织出同一交织矩阵所需的最少综框数（不同完整列向量数）",
    )
    min_treadles: int = Field(
        ...,
        description="织出同一交织矩阵所需的最少踏板数（不同完整行向量数）",
    )
    original_shafts: int = Field(..., description="原设计综框数（tie_up 列数）")
    original_treadles: int = Field(..., description="原设计踏板数（tie_up 行数）")
    shafts_reduction: int = Field(
        ..., description="相对原设计减少的综框数：original_shafts - min_shafts"
    )
    treadles_reduction: int = Field(
        ..., description="相对原设计减少的踏板数：original_treadles - min_treadles"
    )
    reduced_draft: ReducedDraftOut = Field(
        ..., description="约简穿综草案（threading / tie_up / treadling 及重建矩阵）"
    )



class MismatchPointOut(BaseModel):
    """最佳对齐下一个不一致单元的坐标与双方取值。"""

    row: int = Field(..., description="错位单元所在纬纱行号")
    col: int = Field(..., description="错位单元所在经纱列号")
    design_value: int = Field(..., description="第一份设计交织矩阵在该单元的值")
    other_value: int = Field(..., description="第二份设计矩阵对齐后在该单元的值")


class DesignCompareOut(BaseModel):
    """两份设计交织矩阵的最佳循环对齐比对结果。"""

    design_id: int = Field(..., description="第一份（路径指定的）设计 ID")
    other_design_id: int = Field(..., description="第二份（被循环错位比对的）设计 ID")
    warp_ends: int = Field(..., description="两份设计共同的经纱数（矩阵列数）")
    weft_picks: int = Field(..., description="两份设计共同的纬纱数（矩阵行数）")
    weft_offset: int = Field(
        ..., description="第二份矩阵沿纬向（行）的最佳循环错位量"
    )
    warp_offset: int = Field(
        ..., description="第二份矩阵沿经向（列）的最佳循环错位量"
    )
    mismatch_count: int = Field(..., description="最佳对齐下不一致单元数")
    mismatch_ratio: float = Field(
        ..., description="不一致单元占比：mismatch_count / (weft_picks × warp_ends)"
    )
    mismatches: list[MismatchPointOut] = Field(
        ..., description="错位坐标列表，按行、列升序"
    )
    row_mismatches: list[int] = Field(
        ..., description="按行汇总：row_mismatches[h] 为第 h 纬的错位数"
    )
    col_mismatches: list[int] = Field(
        ..., description="按列汇总：col_mismatches[w] 为第 w 根经纱的错位数"
    )
    aligned: list[list[int]] = Field(
        ...,
        description="最佳对齐后的第二份交织矩阵（1=经纱在上，0=纬纱在上）："
        "aligned[h][w] = 原矩阵[(h + weft_offset) % 行数][(w + warp_offset) % 列数]",
    )


class DesignArchiveIn(BaseModel):
    """设计归档请求：1～50 个已入库设计 ID（框架层校验数量与类型）。"""

    design_ids: list[StrictDesignId] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="待归档的已入库设计 ID，1～50 个（严格正整数）；服务端按 ID "
        "升序去重读取，重复 ID 会被拒绝（400），缺失 ID 返回 404",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"design_ids": [3, 1, 2]}]
        }
    }


class ArchiveParametersOut(BaseModel):
    """归档清单中一份设计的设计参数（不含 id / created_at 等入库元数据）。"""

    name: str | None = Field(..., description="设计名称（可选）")
    cyclic: bool = Field(..., description="纹样是否循环")
    max_float: int = Field(..., description="入库时声明的最大浮长阈值")
    shafts: int = Field(..., description="综框数（tie_up 列数）")
    treadles: int = Field(..., description="踏板数（tie_up 行数）")
    warp_ends: int = Field(..., description="经纱数（交织矩阵列数）")
    weft_picks: int = Field(..., description="纬纱数（交织矩阵行数）")
    threading: list[int]
    tie_up: list[list[int]]
    treadling: list[int]


class ArchiveItemOut(BaseModel):
    """归档清单中的一个设计项：设计 ID + 设计参数 + 审计结果。"""

    design_id: int = Field(..., description="被归档的设计 ID")
    parameters: ArchiveParametersOut
    audit: AuditOut


class ArchiveManifestOut(BaseModel):
    """规范化设计归档清单（v1）：摘要即对此对象规范化字节流的 SHA-256。"""

    format: Literal["design-archive-v1"] = Field(
        ..., description="清单格式标识，当前固定为 design-archive-v1"
    )
    design_ids: list[int] = Field(
        ..., description="归档的设计 ID，升序去重"
    )
    design_count: int = Field(..., description="归档设计数（design_ids 长度）")
    items: list[ArchiveItemOut] = Field(
        ..., description="逐项设计参数与审计结果，顺序与 design_ids 一致"
    )


class DesignArchiveOut(BaseModel):
    """POST /design-archives 的归档结果。"""

    id: int = Field(..., description="归档记录 ID")
    digest: str = Field(
        ..., description="清单规范化字节流的 SHA-256 十六进制摘要（64 位小写）"
    )
    design_ids: list[int] = Field(..., description="实际归档的设计 ID，升序去重")
    design_count: int = Field(..., description="归档设计数")
    manifest: ArchiveManifestOut = Field(..., description="规范化 JSON 清单全文")
    reused: bool = Field(
        ...,
        description="是否幂等复用：true=命中相同 digest 的既有归档（HTTP 200，"
        "未新增数据）；false=本次新建（HTTP 201）",
    )
    created_at: str = Field(..., description="首次归档时间（UTC，ISO 8601）")


class DesignArchiveVerifyOut(BaseModel):
    """GET /design-archives/{id}/verify 的完整性校验报告。"""

    archive_id: int = Field(..., description="被校验的归档 ID")
    digest: str = Field(..., description="归档时持久化的 SHA-256 摘要")
    recomputed_digest: str = Field(
        ...,
        description="按归档 ID 列表重新读取当前设计参数与审计结果后重算的摘要",
    )
    consistent: bool = Field(
        ...,
        description="重算摘要与归档摘要是否一致（且无缺失设计项）"
    )
    missing_design_ids: list[int] = Field(
        ...,
        description="归档 ID 列表中当前已缺失（删除或不存在）的设计 ID，升序",
    )
    first_diff_path: str | None = Field(
        ...,
        description="归档清单与当前内容的首个差异路径（JSON Pointer 风格，根为"
        " 空串）；一致（或仅摘要列被改动而清单仍一致）时为 null",
    )
