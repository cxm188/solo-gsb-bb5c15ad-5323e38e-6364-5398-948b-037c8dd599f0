"""WIF 1.1 互操作：机织设计的固定段落纯文本导出与导入解析。

本模块实现服务自身的 **WIF 1.1 子集**（text/plain），段落（INI 风格的
[SECTION]）与键名固定，全部穿综 / 踏综 / 绑综编号采用 **1 基索引**
（与 JSON API 的 0 基内部表示相差 1，导入时减 1、导出时加 1）：

    [WIF]
    Version=1.1
    Name=<设计名，可空；含行分隔符 / 反斜杠 / 首尾空白时为 \\B64: 可逆编码>

    [CONTENTS]
    TEXTPLAN=yes
    TIEUP=yes
    THREADING=yes
    TREADLING=yes
    WEAVING=yes

    [TEXTPLAN]
    WARP THREADS=<经纱数>
    WEFT THREADS=<纬纱数>

    [TIEUP]
    <踏板号 1..treadles>=<每行 shafts 个 0/1，逗号分隔>

    [THREADING]
    <经纱号 1..warp>=<所穿综框号 1..shafts>

    [TREADLING]
    <纬纱号 1..weft>=<所踩踏板号 1..treadles>

    [WEAVING]
    SHAFTS=<综框数>
    TREADLES=<踏板数>
    CYCLIC=<yes|no>
    MAX_FLOAT=<允许的最大浮长>

解析时校验：声明段落与实际段落一致、尺寸（SHAFTS/TREADLES/经纬纱数）
一致、1 基索引落在声明范围内、绑综值严格为 0/1、MAX_FLOAT ≥ 1。
任何不符都抛 WifError（DesignError 的子类），由路由统一转成 400 且不写库。
"""

from __future__ import annotations

import base64
import binascii
import re
import zlib

from .weave import DesignError

WIF_VERSION = "1.1"
WIF_CONTENT_TYPE = "text/plain; charset=utf-8"

_REQUIRED_SECTIONS = ("TEXTPLAN", "TIEUP", "THREADING", "TREADLING", "WEAVING")

# 设计名可逆编码标记。WIF 是逐行文本格式，名称中的 CR/LF（含 Python
# splitlines 认可的全部行分隔符：NEL/ / /VT/FF/RS/GS/US 等）
# 会破坏行结构；反斜杠与首尾空白也需要无损保留。含这些字符的名称导出为
# 单行负载：
#     Name=\B64:<crc32(utf-8 name) 的 8 位十六进制><urlsafe-base64(utf-8 name)>
# 校验和用于识别被截断 / 改写的损坏负载；负载字符（[0-9A-FA-Za-z_-]=）
# 不会与任何行分隔符冲突。旧版无标记的 Name= 文本按老规则（strip）解析，
# 仍可导入。
_NAME_ENC_PREFIX = "\\B64:"
_NAME_CRC_LEN = 8


class WifError(DesignError):
    """WIF 文本解析或校验失败，整份导入应被拒绝（HTTP 400）。"""


# 键的允许字符：字母、数字、空格（如 "WARP THREADS"）
_SECTION_RE = re.compile(r"^\[([A-Z0-9 _-]+)\]$")


def _parse_int(token: str, what: str) -> int:
    """解析严格十进制整数；拒绝空串、正负号、前后空白与非数字。"""
    token = token.strip()
    if not token or not token.isdigit():
        raise WifError(f"{what} must be a non-negative integer, got {token!r}")
    return int(token)


def parse_wif(text: str) -> dict:
    """把 WIF 1.1 文本解析为与 DesignIn 同构的 dict。

    返回字段：name(None 或非空字符串)、cyclic(bool)、max_float(int)、
    threading(list[int], 0 基)、tie_up(list[list[int]])、treadling(list[int],
    0 基)。任何格式 / 尺寸 / 索引 / 取值错误都抛 WifError。
    """
    if not isinstance(text, str) or not text.strip():
        raise WifError("WIF document is empty")

    # 段落名 -> (键 -> 值)；普通 dict 即可记录重复段落
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("["):
            match = _SECTION_RE.match(line.upper())
            if not match or not line.endswith("]"):
                raise WifError(f"line {lineno}: malformed section header {raw!r}")
            current = match.group(1)
            if current in sections:
                raise WifError(f"line {lineno}: duplicate section [{current}]")
            sections[current] = {}
            continue
        if "=" not in line:
            raise WifError(f"line {lineno}: expected key=value, got {raw!r}")
        if current is None:
            raise WifError(f"line {lineno}: key=value outside any section: {raw!r}")
        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if not key:
            raise WifError(f"line {lineno}: empty key in section [{current}]")
        if key in sections[current]:
            raise WifError(
                f"line {lineno}: duplicate key {key!r} in section [{current}]"
            )
        sections[current][key] = value

    # ---- [WIF]：版本必须为 1.1；Name 可缺省（无标记按旧规则 strip，
    #      \B64: 标记按 Base64 无损解码；标记损坏抛 WifError → 400）----
    header = sections.get("WIF")
    if header is None:
        raise WifError("missing required section [WIF]")
    version = header.get("VERSION")
    if version is None:
        raise WifError("[WIF] missing Version")
    if version != WIF_VERSION:
        raise WifError(f"unsupported WIF version {version!r}: only {WIF_VERSION} is supported")
    name = _decode_name(header.get("NAME"))

    # ---- [CONTENTS]：必须逐一声明 TEXTPLAN/TIEUP/THREADING/TREADLING/WEAVING ----
    contents = sections.get("CONTENTS")
    if contents is None:
        raise WifError("missing required section [CONTENTS]")
    for name_ in _REQUIRED_SECTIONS:
        if contents.get(name_, "").strip().lower() != "yes":
            raise WifError(f"[CONTENTS] must declare {name_}=yes")

    for name_ in _REQUIRED_SECTIONS:
        if name_ not in sections:
            raise WifError(f"[CONTENTS] declares {name_}=yes but section [{name_}] is missing")

    # ---- [TEXTPLAN]：经纬纱数（尺寸，正整数）----
    textplan = sections["TEXTPLAN"]
    warp_threads = _parse_int(textplan.get("WARP THREADS", ""), "WARP THREADS")
    weft_threads = _parse_int(textplan.get("WEFT THREADS", ""), "WEFT THREADS")
    if warp_threads < 1:
        raise WifError("WARP THREADS must be >= 1")
    if weft_threads < 1:
        raise WifError("WEFT THREADS must be >= 1")

    # ---- [WEAVING]：综框 / 踏板数、循环声明与浮长上限 ----
    weaving = sections["WEAVING"]
    shafts = _parse_int(weaving.get("SHAFTS", ""), "SHAFTS")
    treadles = _parse_int(weaving.get("TREADLES", ""), "TREADLES")
    if shafts < 1:
        raise WifError("SHAFTS must be >= 1")
    if treadles < 1:
        raise WifError("TREADLES must be >= 1")
    cyclic_raw = weaving.get("CYCLIC", "").strip().lower()
    if cyclic_raw not in ("yes", "no"):
        raise WifError(f"CYCLIC must be 'yes' or 'no', got {cyclic_raw!r}")
    cyclic = cyclic_raw == "yes"
    max_float = _parse_int(weaving.get("MAX_FLOAT", ""), "MAX_FLOAT")
    if max_float < 1:
        raise WifError("MAX_FLOAT must be >= 1")

    # ---- [TIEUP]：键为 1..treadles 的踏板号，值为每行 shafts 个严格 0/1 ----
    tie_up = _parse_tieup(sections["TIEUP"], shafts, treadles)

    # ---- [THREADING] / [TREADLING]：键必须恰好覆盖 1..n，值为范围内 1 基索引 ----
    threading = _parse_index_map(
        sections["THREADING"],
        count=warp_threads,
        limit=shafts,
        index_label="warp end",
        value_label="shaft",
    )
    treadling = _parse_index_map(
        sections["TREADLING"],
        count=weft_threads,
        limit=treadles,
        index_label="weft pick",
        value_label="treadle",
    )

    return {
        "name": name,
        "cyclic": cyclic,
        "max_float": max_float,
        "threading": threading,
        "tie_up": tie_up,
        "treadling": treadling,
    }


def _parse_tieup(
    raw: dict[str, str], shafts: int, treadles: int
) -> list[list[int]]:
    """解析 [TIEUP]：踏板行号 1..treadles 必须各出现一次且不许多余，
    每行恰好 shafts 个逗号分隔的 0/1（1 基行序）。"""
    rows: dict[int, list[int]] = {}
    for key, value in raw.items():
        treadle = _parse_int(key, "[TIEUP] treadle index")
        if not 1 <= treadle <= treadles:
            raise WifError(
                f"[TIEUP] treadle {treadle} out of range: valid index is [1, {treadles}]"
            )
        if treadle in rows:
            raise WifError(f"[TIEUP] duplicate row for treadle {treadle}")
        parts = value.split(",")
        if len(parts) != shafts:
            raise WifError(
                f"[TIEUP] treadle {treadle} has {len(parts)} cells, expected {shafts} "
                "(one 0/1 per shaft)"
            )
        row: list[int] = []
        for c, part in enumerate(parts, start=1):
            cell = part.strip()
            if cell not in ("0", "1"):
                raise WifError(
                    f"[TIEUP] treadle {treadle} shaft {c} must be 0 or 1, got {part!r}"
                )
            row.append(int(cell))
        rows[treadle] = row

    if sorted(rows) != list(range(1, treadles + 1)):
        missing = sorted(set(range(1, treadles + 1)) - set(rows))
        extra = sorted(set(rows) - set(range(1, treadles + 1)))
        raise WifError(
            f"[TIEUP] must contain exactly treadle rows 1..{treadles}; "
            f"missing={missing}, extra={extra}"
        )
    return [rows[t] for t in range(1, treadles + 1)]


def _parse_index_map(
    raw: dict[str, str],
    *,
    count: int,
    limit: int,
    index_label: str,
    value_label: str,
) -> list[int]:
    """解析 [THREADING]/[TREADLING]：键必须恰好是 1..count，值为 1..limit
    的索引；返回 0 基列表。键集合多一少一或值越界均报错。"""
    values: dict[int, int] = {}
    for key, value in raw.items():
        index = _parse_int(key, f"[{index_label}] index")
        if not 1 <= index <= count:
            raise WifError(
                f"{index_label} index {index} out of range: valid index is [1, {count}]"
            )
        if index in values:
            raise WifError(f"duplicate {index_label} index {index}")
        target = _parse_int(value, f"{value_label} reference at {index_label} {index}")
        if not 1 <= target <= limit:
            raise WifError(
                f"{index_label} {index} references {value_label} {target} out of range: "
                f"valid index is [1, {limit}]"
            )
        values[index] = target - 1  # 1 基 -> 0 基

    if sorted(values) != list(range(1, count + 1)):
        missing = sorted(set(range(1, count + 1)) - set(values))
        raise WifError(
            f"{index_label} entries must cover exactly 1..{count}; missing={missing}"
        )
    return [values[i] for i in range(1, count + 1)]


def _name_needs_encoding(name: str) -> bool:
    """名称是否含无法在无标记 Name= 行中无损表示的字符：Python splitlines
    认可的任何行分隔符（CR/LF/NEL/U+2028/U+2029/VT/FF/…）、反斜杠，
    或首尾空白（旧解析规则会 strip）。"""
    if not name:
        return False
    if name.splitlines() != [name]:
        return True
    if "\\" in name or name != name.strip():
        return True
    return False


def _encode_name(name: str | None) -> str:
    """设计名编码为 WIF 单行 Name= 的值。普通名称原样输出；含行分隔符、
    反斜杠或首尾空白的名称用 \\B64:<crc32><urlsafe-base64(utf-8)> 可逆
    编码，保证导出再导入逐字符一致。None / 空串导出为空。"""
    if not name:
        return ""
    if not _name_needs_encoding(name):
        return name
    data = name.encode("utf-8")
    crc = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    payload = base64.urlsafe_b64encode(data).decode("ascii")
    return _NAME_ENC_PREFIX + crc + payload


def _decode_name(raw: str | None) -> str | None:
    """解析 Name= 的值，与 _encode_name 配对：

    * 缺省 / 空 / 仅空白 → None；
    * \\B64: 标记 → 校验 CRC32 后严格 Base64 解码 UTF-8，任何损坏（负载过短、
      校验和不符、CRC 非十六进制、非 Base64 字符、错误填充、非法 UTF-8、
      标记被用于无需编码的名称）都抛 WifError（导入整体 400 且不写库）；
    * 其余为旧版无标记文本：按旧规则 strip，空则 None（向后兼容）。
    """
    if raw is None:
        return None
    if not raw.startswith(_NAME_ENC_PREFIX):
        return raw.strip() or None
    body = raw[len(_NAME_ENC_PREFIX):]
    if len(body) <= _NAME_CRC_LEN:
        raise WifError(f"[WIF] Name has corrupt {_NAME_ENC_PREFIX!r} encoding")
    crc_hex, payload = body[:_NAME_CRC_LEN], body[_NAME_CRC_LEN:]
    try:
        expect_crc = int(crc_hex, 16)
        # validate=True：拒绝非字母表字符；altchars=_- 对应 urlsafe 变体
        data = base64.b64decode(payload.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WifError(f"[WIF] Name has corrupt {_NAME_ENC_PREFIX!r} encoding") from exc
    if (zlib.crc32(data) & 0xFFFFFFFF) != expect_crc:
        raise WifError(f"[WIF] Name has corrupt {_NAME_ENC_PREFIX!r} checksum")
    try:
        name = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WifError(f"[WIF] Name has corrupt {_NAME_ENC_PREFIX!r} encoding") from exc
    # 只接受本服务导出时本应加标记的名称，防止标记 / 数据被篡改后静默入库。
    if not _name_needs_encoding(name):
        raise WifError(
            f"[WIF] Name {_NAME_ENC_PREFIX!r} marker used on a name that needs no encoding"
        )
    return name or None


def serialize_wif(
    *,
    name: str | None,
    cyclic: bool,
    max_float: int,
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
) -> str:
    """把内部 0 基设计序列化为固定段落、1 基索引的 WIF 1.1 文本。"""
    shafts = len(tie_up[0])
    treadles = len(tie_up)
    lines: list[str] = [
        "[WIF]",
        f"Version={WIF_VERSION}",
        f"Name={_encode_name(name)}",
        "",
        "[CONTENTS]",
        "TEXTPLAN=yes",
        "TIEUP=yes",
        "THREADING=yes",
        "TREADLING=yes",
        "WEAVING=yes",
        "",
        "[TEXTPLAN]",
        f"WARP THREADS={len(threading)}",
        f"WEFT THREADS={len(treadling)}",
        "",
        "[TIEUP]",
    ]
    lines += [
        f"{t}=" + ",".join(str(cell) for cell in tie_up[t - 1])
        for t in range(1, treadles + 1)
    ]
    lines += ["", "[THREADING]"]
    lines += [
        f"{w}={threading[w - 1] + 1}" for w in range(1, len(threading) + 1)
    ]
    lines += ["", "[TREADLING]"]
    lines += [
        f"{h}={treadling[h - 1] + 1}" for h in range(1, len(treadling) + 1)
    ]
    lines += [
        "",
        "[WEAVING]",
        f"SHAFTS={shafts}",
        f"TREADLES={treadles}",
        f"CYCLIC={'yes' if cyclic else 'no'}",
        f"MAX_FLOAT={max_float}",
        "",
    ]
    return "\n".join(lines)
