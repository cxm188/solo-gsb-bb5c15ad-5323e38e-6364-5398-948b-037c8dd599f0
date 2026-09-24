"""机织纹样核心计算：由穿综、绑综、踏综生成经纬交织矩阵，并审计浮长。

约定：
- 交织矩阵 interlacing[h][w] == 1 表示第 h 纬处第 w 根经纱在纬纱之上（经纱浮起），
  == 0 表示纬纱在上。
- 经向浮长（warp float）：同一根经纱连续浮于纬纱之上的纬纱数，即矩阵某一列中
  连续的 1。
- 纬向浮长（weft float）：同一根纬纱连续浮于经纱之上的经纱数，即矩阵某一行中
  连续的 0。
- 纹样声明循环（cyclic）时，矩阵在经纬两个方向上都首尾相接：跨越末行/首行
  （或末列/首列）边界的同向连续段合并为一段，其起点取尾部段的起点，长度为
  两段之和。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


class DesignError(ValueError):
    """设计校验失败（维度不一致或索引越界等），整份设计应被拒绝。"""


@dataclass
class FloatViolation:
    direction: str   # "warp" | "weft"
    yarn_index: int  # 经纱索引（列）或纬纱索引（行）
    start: int       # 连续段起点（对循环合并段，为尾部段的起点）
    length: int      # 连续浮长

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TieUpSuggestion:
    treadle: int               # 踏板索引（tie_up 行号）
    shaft: int                 # 综框索引（tie_up 列号）
    new_value: int             # 该单元翻转后的新值（0 或 1）
    remaining_violations: int  # 翻转后完整审计的剩余违规总数
    affected_points: int       # 交织矩阵中取值发生变化的点数

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepeatPeriod:
    warp_period: int                 # 经向最小正周期（一个循环单元的列数/经纱数）
    weft_period: int                 # 纬向最小正周期（一个循环单元的行数/纬纱数）
    unit: list[list[int]]            # 最小循环单元，weft_period 行 × warp_period 列
    strict_2d_repeat: bool           # 两方向周期是否都严格小于整幅尺寸

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MismatchPoint:
    row: int           # 错位单元所在纬纱行号
    col: int           # 错位单元所在经纱列号
    design_value: int  # 第一份设计交织矩阵在该单元的值
    other_value: int   # 第二份设计矩阵对齐后在该单元的值

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alignment:
    weft_offset: int                  # 第二份矩阵沿纬向（行）的最佳循环错位量
    warp_offset: int                  # 第二份矩阵沿经向（列）的最佳循环错位量
    mismatch_count: int               # 最佳对齐下不一致单元数
    mismatch_ratio: float             # 不一致单元占比
    mismatches: list[MismatchPoint]   # 错位坐标（按行、列升序）
    row_mismatches: list[int]         # 按行汇总：每纬的错位数
    col_mismatches: list[int]         # 按列汇总：每经的错位数
    aligned: list[list[int]]          # 最佳对齐后的第二份矩阵

    def to_dict(self) -> dict:
        return {
            "weft_offset": self.weft_offset,
            "warp_offset": self.warp_offset,
            "mismatch_count": self.mismatch_count,
            "mismatch_ratio": self.mismatch_ratio,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "row_mismatches": self.row_mismatches,
            "col_mismatches": self.col_mismatches,
            "aligned": self.aligned,
        }


@dataclass
class BalanceHotspot:
    row: int           # 窗口左上角纬纱行号
    col: int           # 窗口左上角经纱列号
    warp_ratio: float  # 窗口内 1（经纱在上）的占比
    deviation: float   # 占比偏离最近阈值的幅度

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BalanceHotspotScan:
    window_rows: int                      # 窗口行数（纬向高度）
    window_cols: int                      # 窗口列数（经向宽度）
    warp_ratio_min: float                 # 占比允许下限（含）
    warp_ratio_max: float                 # 占比允许上限（含）
    windows_scanned: int                  # 实际扫描的窗口总数
    hotspots: list[BalanceHotspot]        # 超界窗口（按左上角行、列升序）
    worst_hotspot: BalanceHotspot | None  # 偏离最甚者；无超界时为 None

    def to_dict(self) -> dict:
        return {
            "window_rows": self.window_rows,
            "window_cols": self.window_cols,
            "warp_ratio_min": self.warp_ratio_min,
            "warp_ratio_max": self.warp_ratio_max,
            "windows_scanned": self.windows_scanned,
            "hotspots": [h.to_dict() for h in self.hotspots],
            "worst_hotspot": (
                self.worst_hotspot.to_dict() if self.worst_hotspot else None
            ),
        }


@dataclass
class ThresholdProfilePoint:
    max_float: int        # 本档允许的最大浮长阈值
    warp_violations: int  # 该阈值下经向浮长违规数
    weft_violations: int  # 该阈值下纬向浮长违规数
    total_violations: int # 该阈值下违规总数（经向 + 纬向）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThresholdProfile:
    max_float_limit: int                      # 逐档审计的阈值上限
    min_zero_violation_threshold: int | None  # 达到零违规的最小阈值（上限内无则 None）
    points: list[ThresholdProfilePoint]       # 逐档结果，阈值 1..max_float_limit 升序

    def to_dict(self) -> dict:
        return {
            "max_float_limit": self.max_float_limit,
            "min_zero_violation_threshold": self.min_zero_violation_threshold,
            # 接口响应中逐档列表的字段名为 profile
            "profile": [p.to_dict() for p in self.points],
        }


@dataclass
class RepairPlanStep:
    step: int               # 第几步（从 1 开始）
    treadle: int            # 踏板索引（tie_up 行号）
    shaft: int              # 综框索引（tie_up 列号）
    old_value: int          # 翻转前该单元的值（0 或 1）
    new_value: int          # 翻转后该单元的值（0 或 1）
    violations_after: int   # 本次翻转后完整审计的违规总数（允许不少于上一步）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskZone:
    """一片由超限浮长单元四邻接合并而成的连通风险区域。"""

    cells: list[tuple[int, int]]  # 区域内矩阵单元 (行, 列)，按行、列升序
    min_row: int                  # 包围盒顶行（最小行号）
    min_col: int                  # 包围盒左列（最小列号）
    max_row: int                  # 包围盒底行（最大行号）
    max_col: int                  # 包围盒右列（最大列号）
    area: int                     # 面积 = 区域单元数
    max_float: int                # 区域内单元涉及的最大浮长
    directions: list[str]         # 涉及方向："warp"、"weft" 去重，warp 在前

    def to_dict(self) -> dict:
        return {
            "cells": [{"row": r, "col": c} for r, c in self.cells],
            "bbox": {
                "min_row": self.min_row,
                "min_col": self.min_col,
                "max_row": self.max_row,
                "max_col": self.max_col,
                "rows": self.max_row - self.min_row + 1,
                "cols": self.max_col - self.min_col + 1,
            },
            "area": self.area,
            "max_float": self.max_float,
            "directions": self.directions,
        }


@dataclass
class FloatRiskZones:
    """超限浮长单元的连通区域聚合结果。"""

    warp_ends: int              # 经纱数（矩阵列数）
    weft_picks: int             # 纬纱数（矩阵行数）
    zones: list[RiskZone]       # 风险区域，按严重度降序

    def to_dict(self) -> dict:
        return {
            "warp_ends": self.warp_ends,
            "weft_picks": self.weft_picks,
            "zone_count": len(self.zones),
            "zones": [z.to_dict() for z in self.zones],
        }


@dataclass
class LoomFitReduction:
    """基于交织矩阵等价类约简出的最少综框/踏板穿综草案。"""

    original_shafts: int            # 原设计综框数（tie_up 列数）
    original_treadles: int          # 原设计踏板数（tie_up 行数）
    min_shafts: int                 # 不同完整列向量数 = 织出同一矩阵所需最少综框数
    min_treadles: int               # 不同完整行向量数 = 织出同一矩阵所需最少踏板数
    threading: list[int]            # 约简穿综：长度 = 经纱数，取值为约简综框号
    tie_up: list[list[int]]         # 约简绑综：min_treadles 行 × min_shafts 列
    treadling: list[int]            # 约简踏综：长度 = 纬纱数，取值为约简踏板号
    interlacing: list[list[int]]    # 由约简草案重建的交织矩阵（与原矩阵逐点一致）


@dataclass
class RepairPlan:
    changes: int                          # 翻转次数（方案长度）
    final_violations: int                 # 最终违规总数（必须严格少于原设计）
    changed_points: int                   # 最终矩阵相对原矩阵的变化点数
    steps: list[RepairPlanStep]           # 逐步翻转序列
    violations: list[FloatViolation]      # 最终违规列表
    interlacing: list[list[int]]          # 最终交织矩阵

    def to_dict(self) -> dict:
        return {
            "changes": self.changes,
            "final_violations": self.final_violations,
            "changed_points": self.changed_points,
            "steps": [s.to_dict() for s in self.steps],
            "violations": [v.to_dict() for v in self.violations],
            "interlacing": self.interlacing,
        }


def _check_int(value, what: str) -> None:
    # bool 是 int 的子类，单独排除，避免 True/False 被当成合法索引
    if not isinstance(value, int) or isinstance(value, bool):
        raise DesignError(f"{what} must be an integer, got {value!r}")


def validate_design(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
) -> tuple[int, int]:
    """校验设计，返回 (综框数, 踏板数)。任何不一致都抛 DesignError。"""

    _check_int(max_float, "max_float")
    if max_float < 1:
        raise DesignError("max_float must be >= 1")

    if not threading:
        raise DesignError("threading must be a non-empty list")
    if not treadling:
        raise DesignError("treadling must be a non-empty list")
    if not tie_up:
        raise DesignError("tie_up must be a non-empty matrix")

    shafts = len(tie_up[0])
    if shafts == 0:
        raise DesignError("tie_up rows must be non-empty")
    for r, row in enumerate(tie_up):
        if not isinstance(row, list) or len(row) != shafts:
            got = len(row) if isinstance(row, list) else type(row).__name__
            raise DesignError(
                f"tie_up row {r} has length {got}, expected {shafts} "
                "(tie_up must be a rectangular matrix)"
            )
        for c, v in enumerate(row):
            _check_int(v, f"tie_up[{r}][{c}]")
            if v not in (0, 1):
                raise DesignError(f"tie_up[{r}][{c}] must be 0 or 1, got {v}")

    treadles = len(tie_up)

    for i, s in enumerate(threading):
        _check_int(s, f"threading[{i}]")
        if s < 0 or s >= shafts:
            raise DesignError(
                f"threading[{i}]={s} out of range: valid shaft index is [0, {shafts})"
            )
    for i, t in enumerate(treadling):
        _check_int(t, f"treadling[{i}]")
        if t < 0 or t >= treadles:
            raise DesignError(
                f"treadling[{i}]={t} out of range: valid treadle index is [0, {treadles})"
            )

    return shafts, treadles


def build_interlacing(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
) -> list[list[int]]:
    """生成交织矩阵：interlacing[h][w] = tie_up[treadling[h]][threading[w]]。"""
    return [
        [tie_up[pick][shaft] for shaft in threading]
        for pick in treadling
    ]


def _proper_divisors(n: int) -> list[int]:
    """按升序返回 n 的所有真因子（能整除 n 且小于 n 的正整数）。"""
    small: list[int] = []
    large: list[int] = []
    i = 1
    while i * i <= n:
        if n % i == 0:
            small.append(i)
            if i != 1 and i * i != n:
                large.append(n // i)
        i += 1
    return small + large[::-1]


def _min_warp_period(matrix: list[list[int]]) -> int:
    """交织矩阵在经向（列方向）的最小正周期：最小 p（p 整除列数）使
    matrix[h][w] == matrix[h][w % p] 对所有单元成立；找不到则返回整幅列数。

    比较基于完整交织矩阵，而非分别检查穿综/踏综。统一按取模回绕比较，
    天然覆盖跨首尾边界的对齐，因此对声明循环（cyclic）的设计同样正确。
    """
    picks = len(matrix)
    ends = len(matrix[0])
    for period in _proper_divisors(ends):
        if all(
            matrix[h][w] == matrix[h][w % period]
            for h in range(picks)
            for w in range(ends)
        ):
            return period
    return ends


def _min_weft_period(matrix: list[list[int]]) -> int:
    """交织矩阵在纬向（行方向）的最小正周期：最小 p（p 整除行数）使
    matrix[h][w] == matrix[h % p][w] 对所有单元成立；找不到则返回整幅行数。"""
    picks = len(matrix)
    ends = len(matrix[0])
    for period in _proper_divisors(picks):
        if all(
            matrix[h][w] == matrix[h % period][w]
            for h in range(picks)
            for w in range(ends)
        ):
            return period
    return picks


def repeat_period_of_matrix(
    matrix: list[list[int]],
) -> RepeatPeriod:
    """对给定交织矩阵求能无缝平铺整幅的最小循环单元。

    经、纬两个方向独立寻找最小正周期（周期必须整除对应尺寸），取矩阵
    左上角 weft_period × warp_period 子矩阵为循环单元 unit。
    strict_2d_repeat 仅在两个方向的周期都严格小于整幅尺寸（单元在经纬
    两个方向都至少平铺 2 次）时为 True；任一方向不存在更小周期则为 False。
    """
    picks = len(matrix)
    ends = len(matrix[0])
    warp_period = _min_warp_period(matrix)
    weft_period = _min_weft_period(matrix)
    unit = [row[:warp_period] for row in matrix[:weft_period]]
    return RepeatPeriod(
        warp_period=warp_period,
        weft_period=weft_period,
        unit=unit,
        strict_2d_repeat=warp_period < ends and weft_period < picks,
    )


def analyze_repeat_period(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
) -> RepeatPeriod:
    """由穿综、绑综、踏综生成交织矩阵后求最小循环单元。

    周期分析完全基于生成出的交织矩阵，不分别检查 threading / treadling；
    因此穿综与踏综单独看没有小周期、但交织结果恰好平铺的设计也能被识别。
    （交织矩阵的平铺等价性与设计是否声明 cyclic 无关：取模回绕比较对
    循环设计的首尾边界天然成立。）
    """
    validate_design(threading, tie_up, treadling, max_float)
    matrix = build_interlacing(threading, tie_up, treadling)
    return repeat_period_of_matrix(matrix)


def _runs(line: list[int], target: int, cyclic: bool) -> list[tuple[int, int]]:
    """返回一维序列中所有等于 target 的连续段 [(start, length), ...]。

    cyclic=True 时，若首段从 0 开始且尾段到达序列末尾，则两段在循环意义上
    相连，合并为 (尾段起点, 两段长度之和)。整行皆为 target 时只有一段，
    不参与合并，长度即序列长度。
    """
    n = len(line)
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if line[i] == target:
            j = i + 1
            while j < n and line[j] == target:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1

    if cyclic and len(runs) > 1:
        first, last = runs[0], runs[-1]
        if first[0] == 0 and last[0] + last[1] == n:
            merged = (last[0], last[1] + first[1])
            runs = [merged] + runs[1:-1]
    return runs


def violations_of_matrix(
    matrix: list[list[int]],
    max_float: int,
    cyclic: bool,
) -> list[FloatViolation]:
    """对给定交织矩阵找出所有长度超过 max_float 的浮长段。"""
    picks = len(matrix)          # 纬纱数（行数）
    ends = len(matrix[0])        # 经纱数（列数）

    violations: list[FloatViolation] = []

    # 经向浮长：每根经纱（列）上连续的 1
    for w in range(ends):
        column = [matrix[h][w] for h in range(picks)]
        for start, length in _runs(column, 1, cyclic):
            if length > max_float:
                violations.append(FloatViolation("warp", w, start, length))

    # 纬向浮长：每根纬纱（行）上连续的 0
    for h in range(picks):
        for start, length in _runs(matrix[h], 0, cyclic):
            if length > max_float:
                violations.append(FloatViolation("weft", h, start, length))

    return violations


def audit_floats(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
) -> tuple[list[list[int]], list[FloatViolation]]:
    """生成交织矩阵并找出所有长度超过 max_float 的浮长段。"""
    validate_design(threading, tie_up, treadling, max_float)
    matrix = build_interlacing(threading, tie_up, treadling)
    return matrix, violations_of_matrix(matrix, max_float, cyclic)


def _violation_as_tuple(violation: FloatViolation | dict) -> tuple[str, int, int, int]:
    """规范化一条违规记录（dataclass 或已入库 dict）为
    (direction, yarn_index, start, length)。"""
    if isinstance(violation, FloatViolation):
        return (
            violation.direction,
            violation.yarn_index,
            violation.start,
            violation.length,
        )
    if not isinstance(violation, dict):
        raise DesignError(f"invalid violation record: {violation!r}")
    try:
        direction = violation["direction"]
        yarn_index = violation["yarn_index"]
        start = violation["start"]
        length = violation["length"]
    except (KeyError, TypeError) as exc:
        raise DesignError(f"invalid violation record: {violation!r}") from exc
    if direction not in ("warp", "weft"):
        raise DesignError(f"invalid violation direction: {direction!r}")
    for name, value in (
        ("yarn_index", yarn_index),
        ("start", start),
        ("length", length),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise DesignError(f"violation {name} must be an integer, got {value!r}")
    return direction, yarn_index, start, length


def aggregate_float_risk_zones(
    matrix: list[list[int]],
    violations: list[FloatViolation] | list[dict],
    cyclic: bool,
) -> FloatRiskZones:
    """把每条超限浮长违规展开为矩阵单元，并按经纬相邻合并为连通风险区域。

    展开规则：
    - warp 违规（列 yarn_index 上连续的 1，起点 start、长度 length）展开为
      单元 ((start + k) % 行数, yarn_index)，k = 0..length-1；
    - weft 违规（行 yarn_index 上连续的 0）展开为
      (yarn_index, (start + k) % 列数)。
    对 cyclic 合并段，start 取尾部段起点、长度为跨边界两段之和，因此统一
    取模即可正确覆盖首尾两侧的单元（同一单元被多条违规覆盖时去重，涉及
    方向取并集、浮长取最大值）。

    合并规则：在展开得到的单元集合上做四邻接（上、下、左、右）连通分量。
    非循环设计只在矩阵矩形范围内相邻；cyclic 设计在经纬两轴首尾相接
    （环面拓扑）：末行单元与首行单元、末列单元与首列单元也互为邻居。
    对角线接触（仅共享顶点）不合并。

    每个区域给出单元坐标（按行、列升序）、轴对齐包围盒、面积（单元数）、
    区域内最大浮长以及涉及方向（去重，warp 在 weft 前）。区域按严重度
    降序：max_float 大者优先，其次面积大者，再次取区域内最小单元坐标
    （行后列）升序作为确定性平局裁决。不修改传入数据。
    """
    if not matrix or not matrix[0]:
        raise DesignError("interlacing matrix must be non-empty")
    picks = len(matrix)          # 纬纱数（行数）
    ends = len(matrix[0])        # 经纱数（列数）

    # cell -> (最大浮长, 涉及方向集合)
    flagged: dict[tuple[int, int], tuple[int, set[str]]] = {}
    for raw in violations:
        direction, yarn_index, start, length = _violation_as_tuple(raw)
        if length <= 0 or start < 0:
            raise DesignError(
                f"invalid violation (start={start}, length={length})"
            )
        if direction == "warp":
            if not 0 <= yarn_index < ends:
                raise DesignError(
                    f"warp violation yarn_index {yarn_index} out of range "
                    f"[0, {ends})"
                )
            if not cyclic and start + length > picks:
                raise DesignError(
                    f"non-cyclic warp violation exceeds column: "
                    f"start={start}, length={length}, column height={picks}"
                )
            cells = (
                ((start + k) % picks, yarn_index) for k in range(length)
            )
        else:
            if not 0 <= yarn_index < picks:
                raise DesignError(
                    f"weft violation yarn_index {yarn_index} out of range "
                    f"[0, {picks})"
                )
            if not cyclic and start + length > ends:
                raise DesignError(
                    f"non-cyclic weft violation exceeds row: "
                    f"start={start}, length={length}, row width={ends}"
                )
            cells = (
                (yarn_index, (start + k) % ends) for k in range(length)
            )
        for cell in cells:
            existing = flagged.get(cell)
            if existing is None:
                flagged[cell] = (length, {direction})
            else:
                existing[1].add(direction)
                if length > existing[0]:
                    flagged[cell] = (length, existing[1])

    remaining = set(flagged)
    zones: list[RiskZone] = []
    # 按行主序取起点，保证连通分量及单元列表的产出顺序确定
    for seed in sorted(flagged):
        if seed not in remaining:
            continue
        component: list[tuple[int, int]] = []
        max_float = 0
        directions: set[str] = set()
        stack = [seed]
        remaining.remove(seed)
        while stack:
            r, c = stack.pop()
            component.append((r, c))
            cell_float, cell_dirs = flagged[(r, c)]
            if cell_float > max_float:
                max_float = cell_float
            directions |= cell_dirs
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if cyclic:
                    neighbor = ((r + dr) % picks, (c + dc) % ends)
                else:
                    neighbor = (r + dr, c + dc)
                    if not (0 <= neighbor[0] < picks and 0 <= neighbor[1] < ends):
                        continue
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        component.sort()
        rows = [r for r, _ in component]
        cols = [c for _, c in component]
        ordered_dirs = [d for d in ("warp", "weft") if d in directions]
        zones.append(
            RiskZone(
                cells=component,
                min_row=min(rows),
                min_col=min(cols),
                max_row=max(rows),
                max_col=max(cols),
                area=len(component),
                max_float=max_float,
                directions=ordered_dirs,
            )
        )

    zones.sort(
        key=lambda z: (-z.max_float, -z.area, z.cells[0][0], z.cells[0][1])
    )
    return FloatRiskZones(warp_ends=ends, weft_picks=picks, zones=zones)


def threshold_profile(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    cyclic: bool,
    max_float_limit: int,
) -> ThresholdProfile:
    """阈值敏感性分析：在 1..max_float_limit 内逐档重跑完整经纬浮长审计。

    交织矩阵与阈值无关，只生成一次；每一档阈值都按原 cyclic 语义对矩阵做
    完整的经向 + 纬向浮长审计（与 audit_floats 相同的判定规则），统计该档
    的经向、纬向与总违规数。违规数随阈值单调不增，因此首个零违规档即
    达到零违规的最小阈值；上限内始终有违规时 min_zero_violation_threshold
    为 None。不修改传入的设计数据。
    """
    if not isinstance(max_float_limit, int) or isinstance(max_float_limit, bool):
        raise DesignError("max_float_limit must be an integer")
    if not 1 <= max_float_limit <= 64:
        raise DesignError("max_float_limit must be within [1, 64]")

    validate_design(threading, tie_up, treadling, max_float_limit)
    matrix = build_interlacing(threading, tie_up, treadling)

    points: list[ThresholdProfilePoint] = []
    min_zero: int | None = None
    for threshold in range(1, max_float_limit + 1):
        violations = violations_of_matrix(matrix, threshold, cyclic)
        warp = sum(1 for v in violations if v.direction == "warp")
        weft = len(violations) - warp
        if min_zero is None and not violations:
            min_zero = threshold
        points.append(
            ThresholdProfilePoint(threshold, warp, weft, len(violations))
        )
    return ThresholdProfile(max_float_limit, min_zero, points)


def suggest_tie_up_flips(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
) -> list[TieUpSuggestion]:
    """单步绑综修复建议：逐一翻转 tie_up 的一个 0/1 单元，按原 cyclic、max_float
    重跑完整审计，仅保留违规总数严格减少的候选。

    受影响交织点数 = 翻转前后交织矩阵中取值发生变化的单元数（即所有
    踩该踏板的纬 × 穿该综框的经 的交叉点）。结果按 (剩余违规数, 受影响点数,
    踏板索引, 综框索引) 升序稳定排序。不修改传入的 tie_up。
    """
    base_matrix, base_violations = audit_floats(
        threading, tie_up, treadling, max_float, cyclic
    )
    base_count = len(base_violations)

    suggestions: list[TieUpSuggestion] = []
    for t, row in enumerate(tie_up):
        for s, value in enumerate(row):
            flipped = [r[:] for r in tie_up]
            flipped[t][s] = 1 - value
            matrix, violations = audit_floats(
                threading, flipped, treadling, max_float, cyclic
            )
            if len(violations) >= base_count:
                continue
            affected = sum(
                a != b
                for row_a, row_b in zip(base_matrix, matrix)
                for a, b in zip(row_a, row_b)
            )
            suggestions.append(
                TieUpSuggestion(t, s, 1 - value, len(violations), affected)
            )

    suggestions.sort(
        key=lambda x: (x.remaining_violations, x.affected_points, x.treadle, x.shaft)
    )
    return suggestions


def _matrix_diff(a: list[list[int]], b: list[list[int]]) -> int:
    return sum(
        x != y
        for row_a, row_b in zip(a, b)
        for x, y in zip(row_a, row_b)
    )


def _line_violation_count(x: int, n: int, cyclic: bool, max_float: int) -> int:
    """统计 n 位序列中长度超过 max_float 的目标连续段数。

    x 的第 i 位为 1 表示位置 i 取目标值（经向列为 1 的位、纬向行为 0 的位
    取反后传入）。连续段枚举与循环合并规则和 _runs 逐条一致：cyclic 时
    首段自 0 开始且尾段抵达末尾则合并为一段。用「x + (x & -x) 进位穿过
    最低一段 1」的位技巧逐段弹出，每段只有常数次大整数运算。
    """
    runs: list[tuple[int, int]] = []
    while x:
        low = x & -x                 # 最低一段 1 的起点（最低置位）
        start = low.bit_length() - 1
        y = x + low                  # 进位穿过整段：段内清零，段后第一个 0 变 1
        length = (x ^ y).bit_length() - 1 - start
        x &= y                       # 清除该段，继续下一段
        runs.append((start, length))
    if cyclic and len(runs) > 1:
        first, last = runs[0], runs[-1]
        if first[0] == 0 and last[0] + last[1] == n:
            merged = (last[0], last[1] + first[1])
            runs = [merged] + runs[1:-1]
    return sum(1 for _, length in runs if length > max_float)


def search_repair_plans(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
    max_changes: int = 2,
    limit: int = 50,
) -> list[RepairPlan]:
    """在最多 max_changes（1～3）次绑综单元翻转内搜索修复方案。

    规则：
    - 广度优先逐层展开 1..max_changes 步翻转；中间状态允许违规数不下降，
      但方案终态的违规总数必须严格少于原设计，否则不收录；
    - 按绑综状态（tie_up 0/1 位图）去重：同一状态只保留首次到达的翻转序列，
      且一条序列内同一单元不会被重复翻转（翻转两次等于未翻转）；
    - 单元按 (treadle, shaft) 字典序展开，因此首次到达某状态的序列即
      翻转序列字典序最小者；
    - 收录方案按 (最终违规数, 改动数, 最终矩阵相对原矩阵的变化点数,
      翻转序列字典序) 升序稳定排序，等价终态只保留排序最优的序列；
    - 返回前 limit 条。不修改传入的 tie_up。

    性能说明：逐层枚举、状态去重、收录与排序规则与「每个状态完整重算
    审计」完全一致，但搜索过程中不做完整重算——
    - 交织矩阵用行位图 + 列位图（每行/列一个大整数，第 i 位对应该行/列
      第 i 个单元）表示，翻转一个绑综单元只是对受影响行/列做按位异或；
    - 每次翻转只重算**受影响的经列与纬行**的超限段数（单条线的段枚举用
      位运算完成，重复出现的行/列模式按位图缓存），其余行/列的计数不变，
      由增量得到该状态的违规总数；变化点数同样按翻转位的 popcount 增量
      维护；
    - 候选方案只携带轻量元组参与排序，仅对最终入选的前 limit 条方案，
      从绑综位图重建 tie_up 并**完整重算**交织矩阵与违规列表（并断言
      与增量计数逐项一致），保证返回结果与完整重算完全相同。
    """
    if not isinstance(max_changes, int) or isinstance(max_changes, bool):
        raise DesignError("max_changes must be an integer")
    if not 1 <= max_changes <= 3:
        raise DesignError("max_changes must be within [1, 3]")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise DesignError("limit must be an integer")
    if not 1 <= limit <= 50:
        raise DesignError("limit must be within [1, 50]")

    validate_design(threading, tie_up, treadling, max_float)

    treadles = len(tie_up)
    shafts = len(tie_up[0])
    picks = len(treadling)         # 纬纱数（行数）
    ends = len(threading)          # 经纱数（列数）
    cells = [(t, s) for t in range(treadles) for s in range(shafts)]
    base_mask = sum(
        tie_up[t][s] << (t * shafts + s) for t, s in cells
    )

    base_matrix, base_violations = audit_floats(
        threading, tie_up, treadling, max_float, cyclic
    )
    base_count = len(base_violations)

    # 踩同一踏板的纬、穿同一综框的经交叉处的交织点，翻转该单元时一起变化
    affected_points: list[tuple[list[int], list[int]]] = []
    for t, s in cells:
        picks_h = [h for h, pick in enumerate(treadling) if pick == t]
        ends_w = [w for w, shaft in enumerate(threading) if shaft == s]
        affected_points.append((picks_h, ends_w))
    # 翻转单元 idx 时作用在每条受影响行上的列位掩码 / 每条受影响列上的行位掩码
    flip_row_masks = [
        sum(1 << w for w in ends_w) for _, ends_w in affected_points
    ]
    flip_col_masks = [
        sum(1 << h for h in picks_h) for picks_h, _ in affected_points
    ]

    # 基态行/列位图：row_bits[h] 第 w 位 = matrix[h][w]，col_bits[w] 第 h 位同
    row_full = (1 << ends) - 1
    base_rows = [
        sum(v << w for w, v in enumerate(row)) for row in base_matrix
    ]
    base_cols = [
        sum(base_matrix[h][w] << h for h in range(picks)) for w in range(ends)
    ]

    # 单条线超限段数缓存：键为「目标位置位图」（纬向行取 0 的位、经向列取
    # 1 的位），搜索全程 cyclic / max_float / 线长固定，位图相同则计数相同
    row_count_cache: dict[int, int] = {}
    col_count_cache: dict[int, int] = {}

    def weft_count(row_bits: int) -> int:
        # 纬向浮长是行内连续的 0：取反后统计 1 的连续段
        target = ~row_bits & row_full
        cached = row_count_cache.get(target)
        if cached is None:
            cached = _line_violation_count(target, ends, cyclic, max_float)
            row_count_cache[target] = cached
        return cached

    def warp_count(col_bits: int) -> int:
        cached = col_count_cache.get(col_bits)
        if cached is None:
            cached = _line_violation_count(col_bits, picks, cyclic, max_float)
            col_count_cache[col_bits] = cached
        return cached

    # BFS 节点：(tie_up 位图, 行位图, 列位图, 违规总数, 变化点数,
    #            翻转单元索引序列, 各步翻转后的违规总数)
    # visited 保证每个绑综状态只展开一次；按单元 (treadle, shaft) 字典序展开，
    # 首次到达某状态的序列即翻转序列字典序最小者。
    visited: set[int] = {base_mask}
    frontier: list[
        tuple[int, list[int], list[int], int, int, tuple[int, ...], tuple[int, ...]]
    ] = [(base_mask, base_rows, base_cols, base_count, 0, (), ())]
    # 候选方案：(最终违规数, 改动数, 变化点数, 翻转单元索引序列,
    #            各步违规总数, 终态 tie_up 位图)
    candidates: list[
        tuple[int, int, int, tuple[int, ...], tuple[int, ...], int]
    ] = []

    for depth in range(1, max_changes + 1):
        next_frontier: list[
            tuple[
                int, list[int], list[int], int, int,
                tuple[int, ...], tuple[int, ...],
            ]
        ] = []
        for mask, rows, cols, total, changed, seq, totals in frontier:
            used = set(seq)
            for idx, (t, s) in enumerate(cells):
                if idx in used:
                    continue
                bit = t * shafts + s
                child_mask = mask ^ (1 << bit)
                if child_mask in visited:
                    continue
                visited.add(child_mask)

                picks_h, ends_w = affected_points[idx]
                row_flip = flip_row_masks[idx]
                col_flip = flip_col_masks[idx]

                # 仅受影响的纬行 / 经列取值变化：只更新这些行/列的位图，
                # 违规总数按这些线的新旧计数差增量维护，其余线不变。
                child_rows = rows[:]
                child_cols = cols[:]
                delta = 0
                for h in picks_h:
                    new_bits = rows[h] ^ row_flip
                    child_rows[h] = new_bits
                    delta += weft_count(new_bits) - weft_count(rows[h])
                for w in ends_w:
                    new_bits = cols[w] ^ col_flip
                    child_cols[w] = new_bits
                    delta += warp_count(new_bits) - warp_count(cols[w])
                child_total = total + delta

                # 变化点数增量：受影响行内，翻转位中与原矩阵相同的位变为
                # 不同（+1），不同的位变为相同（-1）
                delta_changed = 0
                for h in picks_h:
                    same_as_base = (
                        ~(rows[h] ^ base_rows[h]) & row_flip
                    ).bit_count()
                    delta_changed += 2 * same_as_base - len(ends_w)
                child_changed = changed + delta_changed

                child_seq = seq + (idx,)
                child_totals = totals + (child_total,)

                # 中间状态允许违规数不下降：仍入队以便继续展开；
                # 仅当终态严格优于原设计时收录为候选方案。
                if depth < max_changes:
                    next_frontier.append(
                        (
                            child_mask,
                            child_rows,
                            child_cols,
                            child_total,
                            child_changed,
                            child_seq,
                            child_totals,
                        )
                    )
                if child_total < base_count:
                    candidates.append(
                        (
                            child_total,
                            depth,
                            child_changed,
                            child_seq,
                            child_totals,
                            child_mask,
                        )
                    )
        frontier = next_frontier

    candidates.sort(
        key=lambda c: (
            c[0],
            c[1],
            c[2],
            tuple(cells[idx] for idx in c[3]),
        )
    )

    # 仅为最终入选的前 limit 条方案构造完整结果：从终态绑综位图重建
    # tie_up 后完整重算交织矩阵与违规列表，保证与完整重算逐项一致。
    plans: list[RepairPlan] = []
    for total, changes, changed, seq, totals, mask in candidates[:limit]:
        final_tie_up = [
            [(mask >> (t * shafts + s)) & 1 for s in range(shafts)]
            for t in range(treadles)
        ]
        matrix, violations = audit_floats(
            threading, final_tie_up, treadling, max_float, cyclic
        )
        # 增量维护的计数与完整重算互为校验，二者必须一致
        assert len(violations) == total
        assert _matrix_diff(base_matrix, matrix) == changed
        steps = []
        for k, idx in enumerate(seq):
            t, s = cells[idx]
            old_value = tie_up[t][s]  # 同一单元一条序列内只翻一次，必为基态值
            steps.append(
                RepairPlanStep(
                    step=k + 1,
                    treadle=t,
                    shaft=s,
                    old_value=old_value,
                    new_value=1 - old_value,
                    violations_after=totals[k],
                )
            )
        plans.append(
            RepairPlan(
                changes=changes,
                final_violations=total,
                changed_points=changed,
                steps=steps,
                violations=violations,
                interlacing=matrix,
            )
        )
    return plans


def _cyclic_shift(
    matrix: list[list[int]], weft_offset: int, warp_offset: int
) -> list[list[int]]:
    """将矩阵沿纬向循环上移 weft_offset 行、沿经向循环左移 warp_offset 列：
    shifted[h][w] = matrix[(h + weft_offset) % H][(w + warp_offset) % W]。"""
    picks = len(matrix)
    ends = len(matrix[0])
    return [
        [matrix[(h + weft_offset) % picks][(w + warp_offset) % ends]
         for w in range(ends)]
        for h in range(picks)
    ]


def _pack_matrix(matrix: list[list[int]]) -> int:
    """把 0/1 矩阵按行主序打包为单个大整数：bit (h*W + w) 为 matrix[h][w]。

    每个 W 位宽的「位块」对应矩阵的一行，块内左低右高（低位为第 0 列），
    块间低位到高位按行号升序排列。"""
    packed = 0
    for row in reversed(matrix):
        block = 0
        for w, value in enumerate(row):
            block |= value << w  # 列 0 落在块内最低位
        packed = (packed << len(row)) | block
    return packed


def best_alignment(
    matrix_a: list[list[int]],
    matrix_b: list[list[int]],
) -> Alignment:
    """枚举 matrix_b 在经、纬方向的所有循环错位，返回与 matrix_a 不一致
    单元最少的对齐结果。

    对齐约定：aligned[h][w] = matrix_b[(h + weft_offset) % H][(w + warp_offset) % W]，
    即第二份矩阵沿纬向循环上移 weft_offset 行、沿经向循环左移 warp_offset 列后
    与第一份矩阵逐点比较。两份矩阵尺寸必须一致（同纬同经），否则抛 DesignError。
    平局按行偏移（weft_offset）再列偏移（warp_offset）升序取先者——按偏移升序
    枚举且仅在错位严格更少时更新最优即可自然实现。不修改传入矩阵。

    朴素实现对每个 (weft_offset, warp_offset) 都构造完整对齐矩阵并逐点比对，
    复杂度 O((H·W)²)，64×64 需约 1600 万次偏移×单元的 Python 级比较（实测
    单次约 20 秒）。这里把两份矩阵各打包成行主序大整数（见 _pack_matrix）：
    - 纬向移位对应把整个大整数按 W 位为一块的整块循环旋转，经向移位对应各
      W 位块内部的循环移位；
    - 枚举按两偏移升序推进，每步只做一次增量旋转（纬向整块右移 1 块，经向
      各块内右移 1 位，溢出的块/位用掩码补回另一端），不对每个偏移重新做
      整幅移位；
    - 错位数即两个大整数异或后 1 的位数（int.bit_count，C 层执行，结果精确
      而非近似）。
    因此计数阶段每个偏移只有常数次大整数运算（共 O(H·W) 次）；错位坐标、
    行列汇总与对齐矩阵只对最终选定的最佳偏移构造一次。
    """
    if not matrix_a or not matrix_a[0] or not matrix_b or not matrix_b[0]:
        raise DesignError("interlacing matrices must be non-empty")
    picks = len(matrix_a)
    ends = len(matrix_a[0])
    if len(matrix_b) != picks or len(matrix_b[0]) != ends:
        raise DesignError(
            f"interlacing size mismatch: {picks}x{ends} vs "
            f"{len(matrix_b)}x{len(matrix_b[0])} (weft_picks x warp_ends)"
        )

    bits = picks * ends
    packed_a = _pack_matrix(matrix_a)
    packed_b = _pack_matrix(matrix_b)

    # 全部 bits 位置 1 的掩码（亦为每个 W 位块都填满的掩码）。
    all_mask = (1 << bits) - 1
    # 每个位块（行）的最低位组成的掩码：…0001 | 0001<<W | 0001<<2W…
    low_bit_pattern = sum(1 << (h * ends) for h in range(picks))
    # 最低一个位块（最后一行）全部位组成的掩码，用于纬向增量旋转时把整行
    # 块补入最高块。
    low_block_pattern = (1 << ends) - 1

    best_weft = best_warp = 0
    best_count: int | None = None
    # 按 weft_offset 外层、warp_offset 内层升序枚举，平局自然保留最先遇到者。
    # 两个方向都用「增量旋转 1 步」推进，每步只有常数次大整数移位/按位运算，
    # 不对每个偏移重新做整幅移位。
    row_shifted = packed_b  # weft_offset = 0
    for weft_offset in range(picks):
        shifted = row_shifted  # warp_offset = 0
        for warp_offset in range(ends):
            count = (packed_a ^ shifted).bit_count()
            if best_count is None or count < best_count:
                best_count = count
                best_weft = weft_offset
                best_warp = warp_offset
            if warp_offset + 1 < ends:
                # 块内右移 1 位即经向左移 1 列；先屏蔽各块最低位，避免 >> 1
                # 把本块列 0 的位移入上一块的最高位；各块移出的最低位补回本块
                # 最高位。
                shifted = (
                    ((shifted & ~low_bit_pattern) >> 1)
                    | ((shifted & low_bit_pattern) << (ends - 1))
                ) & all_mask
        if weft_offset + 1 < picks:
            # 整体按 ends 位块右移 1 块即纬向上移 1 行：最低块（最后一行）
            # 整行补入最高块（第一行）。
            row_shifted = (
                (row_shifted >> ends)
                | ((row_shifted & low_block_pattern) << (bits - ends))
            ) & all_mask
    assert best_count is not None  # 尺寸非空，至少枚举到 (0, 0)

    # 仅为最佳偏移构造错位坐标、行列汇总与对齐矩阵（各一次 O(H·W)）。
    aligned = _cyclic_shift(matrix_b, best_weft, best_warp)
    mismatches: list[MismatchPoint] = []
    row_mismatches = [0] * picks
    col_mismatches = [0] * ends
    for h in range(picks):
        for w in range(ends):
            if matrix_a[h][w] != aligned[h][w]:
                mismatches.append(
                    MismatchPoint(h, w, matrix_a[h][w], aligned[h][w])
                )
                row_mismatches[h] += 1
                col_mismatches[w] += 1
    count = len(mismatches)
    assert count == best_count  # 与大整数计数互为校验，二者必须一致
    return Alignment(
        weft_offset=best_weft,
        warp_offset=best_warp,
        mismatch_count=count,
        mismatch_ratio=count / (picks * ends),
        mismatches=mismatches,
        row_mismatches=row_mismatches,
        col_mismatches=col_mismatches,
        aligned=aligned,
    )


def _prefix_sums(matrix: list[list[int]]) -> list[list[int]]:
    """二维前缀和：ps[h][w] 为矩阵前 h 行、前 w 列的元素之和。"""
    picks = len(matrix)
    ends = len(matrix[0])
    ps = [[0] * (ends + 1) for _ in range(picks + 1)]
    for h in range(picks):
        row_sum = 0
        for w in range(ends):
            row_sum += matrix[h][w]
            ps[h + 1][w + 1] = ps[h][w + 1] + row_sum
    return ps


def scan_balance_hotspots(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    cyclic: bool,
    window_rows: int,
    window_cols: int,
    warp_ratio_min: float,
    warp_ratio_max: float,
) -> BalanceHotspotScan:
    """局部交织均衡审计：逐窗统计交织矩阵中 1（经纱在上）的占比。

    扫描规则：
    - 非循环设计只扫描完整落入矩阵的窗口；窗口大于矩阵对应尺寸时没有
      可扫描窗口（windows_scanned 为 0）；
    - 循环设计以每个原矩阵单元为左上角，窗口在两轴取模回绕；窗口尺寸
      不得超过矩阵对应尺寸，否则抛 DesignError；
    - 占比落在 [warp_ratio_min, warp_ratio_max] 闭区间之外的窗口记为超界
      热点，deviation 为占比偏离最近阈值的幅度（低于下限取下限之差，
      高于上限取上限之差）。

    热点按左上角 (row, col) 升序稳定排序（行主序扫描天然满足）；最严重
    窗口取 deviation 最大者，平局取扫描序最先者；无超界时 worst_hotspot
    为 None。窗口内 1 的计数用二维前缀和实现（循环设计先把矩阵平铺 2×2，
    使任意回绕窗口完整落在平铺矩阵内）。不修改传入的设计数据。
    """
    _check_int(window_rows, "window_rows")
    _check_int(window_cols, "window_cols")
    if not 1 <= window_rows <= 64:
        raise DesignError("window_rows must be within [1, 64]")
    if not 1 <= window_cols <= 64:
        raise DesignError("window_cols must be within [1, 64]")
    for name, value in (("warp_ratio_min", warp_ratio_min),
                        ("warp_ratio_max", warp_ratio_max)):
        # bool 是 int 的子类，单独排除
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DesignError(f"{name} must be a number, got {value!r}")
        if not 0.0 <= value <= 1.0:
            raise DesignError(f"{name} must be within [0, 1], got {value!r}")
    if warp_ratio_min > warp_ratio_max:
        raise DesignError(
            f"warp_ratio_min ({warp_ratio_min}) must not exceed "
            f"warp_ratio_max ({warp_ratio_max})"
        )

    validate_design(threading, tie_up, treadling, 1)
    matrix = build_interlacing(threading, tie_up, treadling)
    picks = len(matrix)          # 纬纱数（行数）
    ends = len(matrix[0])        # 经纱数（列数）

    if cyclic:
        if window_rows > picks or window_cols > ends:
            raise DesignError(
                f"window {window_rows}x{window_cols} exceeds interlacing size "
                f"{picks}x{ends} (window_rows x window_cols vs "
                f"weft_picks x warp_ends) for cyclic design"
            )
        # 两轴取模回绕等价于在 2×2 平铺矩阵上取普通窗口：窗口尺寸不超过
        # 原矩阵尺寸，任意左上角 (r, c) 的窗口都完整落入平铺矩阵
        ps = _prefix_sums([row * 2 for row in matrix] * 2)
        origins = ((r, c) for r in range(picks) for c in range(ends))
    else:
        ps = _prefix_sums(matrix)
        origins = (
            (r, c)
            for r in range(max(picks - window_rows + 1, 0))
            for c in range(max(ends - window_cols + 1, 0))
        )

    area = window_rows * window_cols
    scanned = 0
    hotspots: list[BalanceHotspot] = []
    for r, c in origins:
        scanned += 1
        ones = (
            ps[r + window_rows][c + window_cols]
            - ps[r][c + window_cols]
            - ps[r + window_rows][c]
            + ps[r][c]
        )
        ratio = ones / area
        if ratio < warp_ratio_min:
            deviation = warp_ratio_min - ratio
        elif ratio > warp_ratio_max:
            deviation = ratio - warp_ratio_max
        else:
            continue
        hotspots.append(BalanceHotspot(r, c, ratio, deviation))

    # deviation 最大者；严格大于才更新，平局保留扫描序最先者
    worst: BalanceHotspot | None = None
    for hotspot in hotspots:
        if worst is None or hotspot.deviation > worst.deviation:
            worst = hotspot

    return BalanceHotspotScan(
        window_rows=window_rows,
        window_cols=window_cols,
        warp_ratio_min=float(warp_ratio_min),
        warp_ratio_max=float(warp_ratio_max),
        windows_scanned=scanned,
        hotspots=hotspots,
        worst_hotspot=worst,
    )


def reduce_to_loom(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
) -> LoomFitReduction:
    """基于交织矩阵把设计约简到织出同一矩阵所需的最少综框与踏板。

    约简依据是交织矩阵本身（而非原综框/踏板编号）：
    - 完整列向量相同的经纱（矩阵中上下浮沉完全一致）归入同一个**约简综框**；
    - 完整行向量相同的纬纱归入同一个**约简踏板**；
    - 等价类按各自方向上**首次出现**的顺序从 0 编号；
    - 约简绑综 tie_up'[t'][s'] 取该踏板等价类任一代表行在该综框等价类
      任一代表列上的交织值（同类行/列上该值必然一致）；
    - 据此重建 threading、tie_up、treadling，并逐点校验重建矩阵与原矩阵一致，
      不一致说明约简逻辑有误，直接抛内部错误。

    不同完整列（行）向量的数量即为织出同一矩阵所需综框（踏板）数的下界：
    穿同一综框（踩同一踏板）的经（纬）纱在矩阵中的列（行）必然逐点相同，
    因此该约简同时达到下界、数量最少。不修改传入的设计数据。
    """
    validate_design(threading, tie_up, treadling, max_float)
    matrix = build_interlacing(threading, tie_up, treadling)
    picks = len(matrix)          # 纬纱数（行数）
    ends = len(matrix[0])        # 经纱数（列数）

    # 列向量（经纱）等价类：按经纱首次出现顺序编号
    shaft_of_col: dict[tuple[int, ...], int] = {}
    col_rep: list[int] = []
    reduced_threading: list[int] = []
    for w in range(ends):
        col = tuple(matrix[h][w] for h in range(picks))
        label = shaft_of_col.get(col)
        if label is None:
            label = len(shaft_of_col)
            shaft_of_col[col] = label
            col_rep.append(w)
        reduced_threading.append(label)

    # 行向量（纬纱）等价类：按纬纱首次出现顺序编号
    treadle_of_row: dict[tuple[int, ...], int] = {}
    row_rep: list[int] = []
    reduced_treadling: list[int] = []
    for h in range(picks):
        row = tuple(matrix[h][w] for w in range(ends))
        label = treadle_of_row.get(row)
        if label is None:
            label = len(treadle_of_row)
            treadle_of_row[row] = label
            row_rep.append(h)
        reduced_treadling.append(label)

    # 用每个踏板类代表行 × 每个综框类代表列的交织值重建绑综矩阵
    reduced_tie_up = [
        [matrix[h_rep][w_rep] for w_rep in col_rep]
        for h_rep in row_rep
    ]

    rebuilt = build_interlacing(
        reduced_threading, reduced_tie_up, reduced_treadling
    )
    if any(
        rebuilt[h][w] != matrix[h][w]
        for h in range(picks)
        for w in range(ends)
    ):
        raise RuntimeError("loom reduction rebuilt an inconsistent interlacing matrix")

    return LoomFitReduction(
        original_shafts=len(tie_up[0]),
        original_treadles=len(tie_up),
        min_shafts=len(col_rep),
        min_treadles=len(row_rep),
        threading=reduced_threading,
        tie_up=reduced_tie_up,
        treadling=reduced_treadling,
        interlacing=rebuilt,
    )
