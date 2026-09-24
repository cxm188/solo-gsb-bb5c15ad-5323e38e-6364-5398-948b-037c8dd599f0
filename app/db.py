"""SQLite 持久层：建表在服务启动时执行，合法设计与审计结果按 ID 存取。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join("data", "weave.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    cyclic      INTEGER NOT NULL,
    max_float   INTEGER NOT NULL,
    shafts      INTEGER NOT NULL,
    treadles    INTEGER NOT NULL,
    warp_ends   INTEGER NOT NULL,
    weft_picks  INTEGER NOT NULL,
    threading   TEXT NOT NULL,   -- JSON
    tie_up      TEXT NOT NULL,   -- JSON
    treadling   TEXT NOT NULL,   -- JSON
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id    INTEGER NOT NULL UNIQUE REFERENCES designs (id) ON DELETE CASCADE,
    exceeds      INTEGER NOT NULL,
    interlacing  TEXT NOT NULL,  -- JSON
    violations   TEXT NOT NULL,  -- JSON
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS design_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id   INTEGER NOT NULL REFERENCES designs (id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (design_id, tag)
);

CREATE TABLE IF NOT EXISTS design_archives (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    digest       TEXT NOT NULL UNIQUE,          -- 清单规范化字节的 SHA-256 十六进制
    design_ids   TEXT NOT NULL,                 -- JSON：升序去重的设计 ID
    design_count INTEGER NOT NULL,
    manifest     TEXT NOT NULL,                 -- 规范化 JSON 清单（canonical 文本）
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

MAX_TAG_LENGTH = 32


class DesignNotFoundError(LookupError):
    """写入 / 查询标签时设计不存在。"""


class TagError(ValueError):
    """标签非空或长度等校验失败。"""


class DuplicateTagError(ValueError):
    """同一设计下标签已存在。"""


class ArchiveNotFoundError(LookupError):
    """查询 / 校验归档时归档 ID 不存在。"""

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """启动时建表（幂等）。"""
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def insert_design_with_audit(
    *,
    name: str | None,
    cyclic: bool,
    max_float: int,
    shafts: int,
    treadles: int,
    threading_map: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    interlacing: list[list[int]],
    violations: list[dict],
) -> int:
    """同事务写入设计与审计结果，返回设计 ID。"""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """INSERT INTO designs
               (name, cyclic, max_float, shafts, treadles, warp_ends, weft_picks,
                threading, tie_up, treadling)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                int(cyclic),
                max_float,
                shafts,
                treadles,
                len(threading_map),
                len(treadling),
                json.dumps(threading_map),
                json.dumps(tie_up),
                json.dumps(treadling),
            ),
        )
        design_id = cur.lastrowid
        conn.execute(
            """INSERT INTO audits (design_id, exceeds, interlacing, violations)
               VALUES (?, ?, ?, ?)""",
            (
                design_id,
                int(bool(violations)),
                json.dumps(interlacing),
                json.dumps(violations),
            ),
        )
        return design_id


def _row_to_detail(design: sqlite3.Row, audit: sqlite3.Row) -> dict:
    return {
        "id": design["id"],
        "name": design["name"],
        "cyclic": bool(design["cyclic"]),
        "max_float": design["max_float"],
        "shafts": design["shafts"],
        "treadles": design["treadles"],
        "warp_ends": design["warp_ends"],
        "weft_picks": design["weft_picks"],
        "threading": json.loads(design["threading"]),
        "tie_up": json.loads(design["tie_up"]),
        "treadling": json.loads(design["treadling"]),
        "created_at": design["created_at"],
        "audit": {
            "exceeds": bool(audit["exceeds"]),
            "interlacing": json.loads(audit["interlacing"]),
            "violations": json.loads(audit["violations"]),
        },
    }


def get_design(design_id: int) -> dict | None:
    with _lock, _connect() as conn:
        design = conn.execute(
            "SELECT * FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if design is None:
            return None
        audit = conn.execute(
            "SELECT * FROM audits WHERE design_id = ?", (design_id,)
        ).fetchone()
        return _row_to_detail(design, audit)


_SUMMARY_SELECT = """SELECT d.id, d.name, d.cyclic, d.max_float, d.shafts,
                            d.treadles, d.warp_ends, d.weft_picks, d.created_at,
                            a.exceeds
                     FROM designs d JOIN audits a ON a.design_id = d.id"""


def _row_to_summary(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "cyclic": bool(r["cyclic"]),
        "max_float": r["max_float"],
        "shafts": r["shafts"],
        "treadles": r["treadles"],
        "warp_ends": r["warp_ends"],
        "weft_picks": r["weft_picks"],
        "exceeds": bool(r["exceeds"]),
        "created_at": r["created_at"],
    }


def list_designs() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"{_SUMMARY_SELECT} ORDER BY d.id"
        ).fetchall()
        return [_row_to_summary(r) for r in rows]


def list_designs_page(*, limit: int, after_id: int | None = None) -> dict:
    """按设计 ID 升序的 keyset 分页（只读）：取 id > after_id 的前 limit 条
    摘要，并多取一条用于判断是否还有后续页。

    - after_id 为 None 时从头（id > 0）开始；下一页传上一页最后返回的 id；
    - 多取的第 limit+1 条只用于判定 has_more，不放进结果；
    - has_more 为 true 时 next_after_id 为本页最后一条的 id（即下一页的
      after_id），否则为 None；
    - 游标为严格大于比较：遍历期间新增的设计只会出现在最后一次见过的游标
      之后，绝不会让已返回的项重复出现（设计只新增、ID 单调递增）。
    """
    cursor = after_id if after_id is not None else 0
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"""{_SUMMARY_SELECT}
                WHERE d.id > ?
                ORDER BY d.id
                LIMIT ?""",
            (cursor, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_row_to_summary(r) for r in page_rows]
        next_after_id = items[-1]["id"] if has_more and items else None
        return {
            "items": items,
            "next_after_id": next_after_id,
            "has_more": has_more,
        }


def _tags_of(conn: sqlite3.Connection, design_id: int) -> list[dict]:
    """读取设计的全部标签，按首次添加（design_tags.id）升序。"""
    rows = conn.execute(
        """SELECT id, tag, created_at FROM design_tags
           WHERE design_id = ? ORDER BY id""",
        (design_id,),
    ).fetchall()
    return [
        {"id": r["id"], "tag": r["tag"], "created_at": r["created_at"]}
        for r in rows
    ]


def add_design_tag(design_id: int, tag: str) -> dict:
    """在同一 SQLite 事务内：确认设计存在 → 校验标签非空且长度不超过 32 →
    确认该设计下尚未有同名标签 → 写入并返回该设计的完整标签列表（按首次
    添加顺序）。设计不存在抛 DesignNotFoundError；标签非法抛 TagError；
    重复抛 DuplicateTagError，失败时事务整体回滚不写库。"""
    with _lock, _connect() as conn:
        design = conn.execute(
            "SELECT 1 FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if design is None:
            raise DesignNotFoundError(design_id)
        if not isinstance(tag, str) or len(tag) == 0:
            raise TagError("tag must be a non-empty string")
        if len(tag) > MAX_TAG_LENGTH:
            raise TagError(
                f"tag too long: {len(tag)} chars, max is {MAX_TAG_LENGTH}"
            )
        existing = conn.execute(
            "SELECT 1 FROM design_tags WHERE design_id = ? AND tag = ?",
            (design_id, tag),
        ).fetchone()
        if existing is not None:
            raise DuplicateTagError(design_id, tag)
        conn.execute(
            "INSERT INTO design_tags (design_id, tag) VALUES (?, ?)",
            (design_id, tag),
        )
        tags = _tags_of(conn, design_id)
        return {
            "design_id": design_id,
            "tags": tags,
            "count": len(tags),
        }


def list_design_tags(design_id: int) -> dict:
    """按首次添加顺序返回设计的标签与数量；设计不存在抛
    DesignNotFoundError。只读，不改动原设计与审计结果。"""
    with _lock, _connect() as conn:
        design = conn.execute(
            "SELECT 1 FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if design is None:
            raise DesignNotFoundError(design_id)
        tags = _tags_of(conn, design_id)
        return {
            "design_id": design_id,
            "tags": tags,
            "count": len(tags),
        }


def get_designs_many(design_ids: list[int]) -> list[dict | None]:
    """在同一连接 / 同一读取快照内按给定顺序返回设计详情（结构与
    get_design 一致）；缺失的 ID 对应位置为 None。用于归档前一次性读取
    全部设计参数与审计结果，避免半读状态。"""
    if not design_ids:
        return []
    with _lock, _connect() as conn:
        results: list[dict | None] = []
        for design_id in design_ids:
            design = conn.execute(
                "SELECT * FROM designs WHERE id = ?", (design_id,)
            ).fetchone()
            if design is None:
                results.append(None)
                continue
            audit = conn.execute(
                "SELECT * FROM audits WHERE design_id = ?", (design_id,)
            ).fetchone()
            results.append(_row_to_detail(design, audit))
        return results


def _archive_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "digest": row["digest"],
        "design_ids": json.loads(row["design_ids"]),
        "design_count": row["design_count"],
        "manifest": json.loads(row["manifest"]),
        "manifest_text": row["manifest"],
        "created_at": row["created_at"],
    }


def insert_archive_or_get(
    *,
    digest: str,
    design_ids: list[int],
    manifest_text: str,
) -> tuple[dict, bool]:
    """原子「按摘要查重 → 不存在则写入」。

    清单与摘要在事务外已完整构建（所有设计均确认存在），本函数只做一次
    写入，失败整体回滚，绝不产生半成品；digest 上有 UNIQUE 约束兜底：
    并发请求命中同一摘要时，落败的 INSERT 抛 IntegrityError 后回读已存在
    的归档。返回 (归档记录, reused)，reused=True 表示命中既有归档（幂等
    复用，未新增数据）。
    """
    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM design_archives WHERE digest = ?", (digest,)
        ).fetchone()
        if existing is not None:
            return _archive_row_to_dict(existing), True
        try:
            cur = conn.execute(
                """INSERT INTO design_archives
                       (digest, design_ids, design_count, manifest)
                   VALUES (?, ?, ?, ?)""",
                (
                    digest,
                    json.dumps(design_ids),
                    len(design_ids),
                    manifest_text,
                ),
            )
        except sqlite3.IntegrityError:
            # 并发下另一请求已写入同一 digest：回读复用，本事务回滚。
            conn.rollback()
            raced = conn.execute(
                "SELECT * FROM design_archives WHERE digest = ?", (digest,)
            ).fetchone()
            return _archive_row_to_dict(raced), True
        row = conn.execute(
            "SELECT * FROM design_archives WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _archive_row_to_dict(row), False


def get_archive(archive_id: int) -> dict | None:
    """按归档 ID 读取完整归档（含解析后的 manifest 与原始规范化文本）。"""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM design_archives WHERE id = ?", (archive_id,)
        ).fetchone()
        if row is None:
            return None
        return _archive_row_to_dict(row)
