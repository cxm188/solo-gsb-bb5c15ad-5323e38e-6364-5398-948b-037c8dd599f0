"""POST /design-archives 与 GET /design-archives/{id}/verify 接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_design_archives.py
（也可被 pytest 收集）。
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(prefix="weave-test-", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.archives import MANIFEST_FORMAT, canonical_bytes  # noqa: E402
from app.main import app  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


TWILL = {
    "name": "2/2 twill",
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

# 与 TWILL 交织矩阵尺寸不同（2 经 4 纬）的平纹
PLAIN = {
    "name": "plain",
    "cyclic": False,
    "max_float": 1,
    "threading": [0, 1],
    "tie_up": [[1, 0], [0, 1]],
    "treadling": [0, 1, 0, 1],
}


def _create(client, payload):
    return client.post("/designs", json=payload).json()


def _archive_rows():
    """直接数 design_archives 表行数（用于断言失败不留半成品）。"""
    with sqlite3.connect(_TMP_DB.name) as conn:
        return conn.execute("SELECT COUNT(*) FROM design_archives").fetchone()[0]


def test_create_archive_manifest_and_digest():
    with TestClient(app) as client:
        a = _create(client, TWILL)
        b = _create(client, PLAIN)

        # 乱序提交：清单仍按 ID 升序
        resp = client.post("/design-archives", json={"design_ids": [b["id"], a["id"]]})
        check(resp.status_code == 201, "first archive returns 201")
        data = resp.json()
        check(data["reused"] is False, "new archive reports reused=false")
        check(data["design_ids"] == [a["id"], b["id"]],
              "manifest design_ids sorted ascending")
        check(data["design_count"] == 2, "design_count is 2")
        check(isinstance(data["id"], int) and data["id"] >= 1, "archive has an id")
        check(len(data["digest"]) == 64, "digest is 64 hex chars")

        manifest = data["manifest"]
        check(manifest["format"] == MANIFEST_FORMAT, "manifest format marker")
        check(manifest["design_ids"] == [a["id"], b["id"]],
              "manifest payload design_ids sorted")
        check(manifest["design_count"] == 2, "manifest payload design_count")
        check([it["design_id"] for it in manifest["items"]] == [a["id"], b["id"]],
              "items ordered by ascending design id")

        item = manifest["items"][0]
        params = item["parameters"]
        for field in ("name", "cyclic", "max_float", "shafts", "treadles",
                      "warp_ends", "weft_picks", "threading", "tie_up",
                      "treadling"):
            check(params[field] == a[field],
                  f"archived parameter {field} matches stored design")
        check("id" not in params and "created_at" not in params,
              "item parameters exclude storage metadata (id/created_at)")
        check(item["audit"] == a["audit"], "archived audit matches stored audit")

        # digest 可独立复算：对同样的 manifest 规范化字节做 SHA-256
        expected = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
        check(data["digest"] == expected,
              "digest equals sha256 of canonical manifest bytes")

        # 规范化：键排序、无多余结构空白（数据值内空格，如设计名，保留）
        with sqlite3.connect(_TMP_DB.name) as conn:
            stored_text = conn.execute(
                "SELECT manifest FROM design_archives WHERE id = ?",
                (data["id"],),
            ).fetchone()[0]
        check('": ' not in stored_text and ', ' not in stored_text and "\n" not in stored_text,
              "canonical text uses compact separators (no structural whitespace)")
        parsed = json.loads(stored_text)
        check(parsed == manifest, "persisted canonical text parses back to manifest")
        # 键按字典序：顶层键 design_count 排在 format 之前
        check(stored_text.startswith('{"design_count":2,'),
              f"canonical text keys sorted lexicographically: {stored_text[:40]!r}")
        # 清单内条目按 ID 升序：交换两个条目会改变摘要（顺序有意义）
        shuffled = json.loads(json.dumps(manifest))
        shuffled["items"][0], shuffled["items"][1] = (
            shuffled["items"][1], shuffled["items"][0]
        )
        check(hashlib.sha256(canonical_bytes(shuffled)).hexdigest() != expected,
              "sanity: swapped item order changes digest (order is significant)")


def test_digest_idempotent_and_order_independent():
    with TestClient(app) as client:
        a = _create(client, TWILL)
        b = _create(client, PLAIN)

        first = client.post(
            "/design-archives", json={"design_ids": [a["id"], b["id"]]}
        ).json()
        rows_after_create = _archive_rows()

        # 同一集合、不同顺序 -> 同 digest，复用同一记录，HTTP 200
        resp = client.post(
            "/design-archives", json={"design_ids": [b["id"], a["id"]]}
        )
        check(resp.status_code == 200, "identical content re-archived returns 200")
        second = resp.json()
        check(second["reused"] is True, "reuse reports reused=true")
        check(second["id"] == first["id"], "reuse returns same archive id")
        check(second["digest"] == first["digest"], "reuse returns same digest")
        check(second["created_at"] == first["created_at"],
              "reused archive keeps original created_at")
        check(second["manifest"] == first["manifest"],
              "reused archive keeps identical manifest")
        check(_archive_rows() == rows_after_create,
              "idempotent reuse inserts no new row")

        # 不同集合（单子集）-> 不同 digest，独立新记录
        third = client.post(
            "/design-archives", json={"design_ids": [a["id"]]}
        ).json()
        check(third["digest"] != first["digest"],
              "different id set produces a different digest")
        check(third["id"] != first["id"], "different content gets a new archive")

        # 重复提交单个 ID 仍然复用
        again = client.post(
            "/design-archives", json={"design_ids": [a["id"]]}
        )
        check(again.status_code == 200 and again.json()["id"] == third["id"],
              "repeated single-id archive is idempotent too")


def test_duplicate_ids_rejected():
    with TestClient(app) as client:
        a = _create(client, TWILL)["id"]
        rows_before = _archive_rows()

        resp = client.post(
            "/design-archives", json={"design_ids": [a, a]}
        )
        check(resp.status_code == 400, "duplicate ids return 400")
        check("duplicate" in resp.json()["detail"], "400 detail mentions duplicates")

        # 多次重复（如 [x, y, x]）同样拒绝，不写库
        b = _create(client, PLAIN)["id"]
        resp = client.post(
            "/design-archives", json={"design_ids": [a, b, a]}
        )
        check(resp.status_code == 400, "triple list with duplicate returns 400")
        check(_archive_rows() == rows_before,
              "rejected duplicate request writes no archive row")


def test_missing_designs_rejected():
    with TestClient(app) as client:
        a = _create(client, TWILL)["id"]
        rows_before = _archive_rows()

        resp = client.post(
            "/design-archives", json={"design_ids": [a, 99999]}
        )
        check(resp.status_code == 404, "unknown design id returns 404")
        check("99999" in resp.json()["detail"], "404 detail lists missing id")

        resp = client.post(
            "/design-archives", json={"design_ids": [88888, 99999]}
        )
        check(resp.status_code == 404, "all-unknown design ids return 404")
        detail = resp.json()["detail"]
        check("88888" in detail and "99999" in detail,
              "404 detail lists every missing id in ascending order")

        check(_archive_rows() == rows_before,
              "failed archive (missing designs) leaves no half-finished row")


def test_pydantic_validation_422():
    with TestClient(app) as client:
        a = _create(client, TWILL)["id"]
        rows_before = _archive_rows()

        for label, body in (
            ("empty list", {"design_ids": []}),
            ("51 ids", {"design_ids": [a] + list(range(1000, 1050))}),
            ("missing field", {}),
            ("string id", {"design_ids": ["1"]}),
            ("float id", {"design_ids": [1.0]}),
            ("zero id", {"design_ids": [0]}),
            ("negative id", {"design_ids": [-3]}),
            ("boolean id", {"design_ids": [True]}),
            ("null element", {"design_ids": [None]}),
        ):
            resp = client.post("/design-archives", json=body)
            check(resp.status_code == 422, f"{label} -> 422 (got {resp.status_code})")

        check(_archive_rows() == rows_before,
              "422 rejections never persist an archive")

    # 边界：恰好 50 个**不同**的已入库 ID 合法
    with TestClient(app) as client:
        ids = [_create(client, TWILL)["id"] for _ in range(50)]
        resp = client.post("/design-archives", json={"design_ids": ids})
        check(resp.status_code == 201, "exactly 50 distinct ids accepted (201)")
        check(resp.json()["design_count"] == 50, "50-item archive count")


def test_verify_fresh_archive_consistent():
    with TestClient(app) as client:
        a = _create(client, TWILL)
        b = _create(client, PLAIN)
        created = client.post(
            "/design-archives", json={"design_ids": [a["id"], b["id"]]}
        ).json()

        resp = client.get(f"/design-archives/{created['id']}/verify")
        check(resp.status_code == 200, "verify returns 200")
        report = resp.json()
        check(report["archive_id"] == created["id"], "verify echoes archive id")
        check(report["digest"] == created["digest"], "verify reports stored digest")
        check(report["recomputed_digest"] == created["digest"],
              "fresh archive recomputes identical digest")
        check(report["consistent"] is True, "fresh archive is consistent")
        check(report["missing_design_ids"] == [], "no missing designs")
        check(report["first_diff_path"] is None, "no diff path when identical")

        check(client.get("/design-archives/99999/verify").status_code == 404,
              "verify of unknown archive returns 404")


def test_verify_detects_deleted_design():
    with TestClient(app) as client:
        a = _create(client, TWILL)
        b = _create(client, PLAIN)
        created = client.post(
            "/design-archives", json={"design_ids": [a["id"], b["id"]]}
        ).json()

        # API 不开放删除设计：直接在 SQLite 中删除 a（audits 外键级联，
        # 归档记录独立保留），模拟归档后设计项缺失。
        with sqlite3.connect(_TMP_DB.name) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM designs WHERE id = ?", (a["id"],))

        report = client.get(f"/design-archives/{created['id']}/verify").json()
        check(report["consistent"] is False, "archive with deleted design inconsistent")
        check(report["missing_design_ids"] == [a["id"]],
              "verify reports the deleted design id")
        check(report["recomputed_digest"] != report["digest"],
              "recomputed digest differs after design removed")
        check(report["first_diff_path"] is not None,
              "a structural diff path is reported")
        # 规范键序下 design_count（2->1）先于 items 出现
        check(report["first_diff_path"] == "/design_count",
              f"first diff points at design_count, got {report['first_diff_path']!r}")

        # 归档记录本身仍然存在且清单原文未被改动
        stored = db.get_archive(created["id"])
        check(stored is not None, "archive row survives deletion of a member design")
        check(stored["digest"] == created["digest"],
              "verify is read-only: stored digest untouched")


def test_verify_detects_drift_first_diff_path():
    with TestClient(app) as client:
        a = _create(client, TWILL)
        created = client.post(
            "/design-archives", json={"design_ids": [a["id"]]}
        ).json()

        # 直接改库模拟归档后设计参数漂移（max_float 2 -> 5）。
        with sqlite3.connect(_TMP_DB.name) as conn:
            conn.execute(
                "UPDATE designs SET max_float = 5 WHERE id = ?", (a["id"],)
            )

        report = client.get(f"/design-archives/{created['id']}/verify").json()
        check(report["consistent"] is False, "drifted design breaks consistency")
        check(report["missing_design_ids"] == [], "no designs missing")
        check(report["first_diff_path"] == "/items/0/parameters/max_float",
              f"first diff drills to the changed parameter, "
              f"got {report['first_diff_path']!r}")

        # 再改审计字段：差异仍定位到首个不同的叶子（参数已先不同）。
        # 恢复参数后差异应移动到 audit。
        with sqlite3.connect(_TMP_DB.name) as conn:
            conn.execute(
                "UPDATE designs SET max_float = 2 WHERE id = ?", (a["id"],)
            )
            conn.execute(
                "UPDATE audits SET exceeds = 1 WHERE design_id = ?", (a["id"],)
            )
        report = client.get(f"/design-archives/{created['id']}/verify").json()
        check(report["first_diff_path"] == "/items/0/audit/exceeds",
              f"first diff moves into audit leaf, got {report['first_diff_path']!r}")

        # 校验本身只读：再跑一次结果稳定
        again = client.get(f"/design-archives/{created['id']}/verify").json()
        check(again["first_diff_path"] == report["first_diff_path"]
              and again["consistent"] == report["consistent"],
              "verify is a pure read: repeated reports identical")


def _design_audit_selects(info):
    """从 spy 记录中筛出批量读取 designs / audits 的查询。"""
    return [
        e for e in info
        if "FROM designs" in e[1] or "FROM audits" in e[1]
    ]


def test_batch_read_pinned_to_single_snapshot():
    """并发快照保证（白盒）：get_designs_many 的所有 designs / audits
    查询（含缺失 ID 判定）必须在同一个显式读取事务内执行——每条 SELECT
    发生时该连接都处于事务中，且整批只使用同一个连接。

    SQLite 的读取快照在事务首次读取时确定并保持到事务结束；若查询像旧
    实现那样逐条自动提交（in_transaction 为 False），另一实例 / 维护进程
    就能在两次查询之间提交修改，把新旧版本拼进同一批结果。
    """
    import threading as _threading

    with TestClient(app) as client:
        a = _create(client, TWILL)["id"]

        seen = []  # (id(conn), sql, in_transaction)
        lock = _threading.Lock()

        class _SpiedConnection:
            """代理真实连接：记录 designs/audits 查询发生时的事务状态。"""

            def __init__(self, real):
                self._real = real

            def execute(self, sql, parameters=()):
                if (
                    isinstance(sql, str)
                    and ("FROM designs" in sql or "FROM audits" in sql)
                ):
                    with lock:
                        seen.append(
                            (id(self._real), sql, self._real.in_transaction)
                        )
                return self._real.execute(sql, parameters)

            def __getattr__(self, name):
                return getattr(self._real, name)

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *exc):
                return self._real.__exit__(*exc)

        orig_connect = db._connect

        def spy_connect():
            return _SpiedConnection(orig_connect())

        db._connect = spy_connect
        try:
            rows = db.get_designs_many([a, 99999])
        finally:
            db._connect = orig_connect

        check(rows[0] is not None and rows[1] is None,
              "get_designs_many returns detail and None slot for missing id")
        events = _design_audit_selects(seen)
        check(len(events) >= 3,
              f"batch read issues design+audit+missing selects, got {len(events)}")
        check(all(in_tx for _, _, in_tx in events),
              "every designs/audits SELECT in the batch runs inside a transaction"
              " (single pinned read snapshot; no per-statement autocommit)")
        check(len({conn_id for conn_id, _, _ in events}) == 1,
              "all selects of the batch run on one connection / one snapshot")

        # verify 端点共用同一快照语义：重算读取的全部 SELECT 也在一个事务内
        created = client.post(
            "/design-archives", json={"design_ids": [a]}
        ).json()
        seen.clear()
        db._connect = spy_connect
        try:
            resp = client.get(f"/design-archives/{created['id']}/verify")
        finally:
            db._connect = orig_connect
        check(resp.status_code == 200 and resp.json()["consistent"],
              "verify still consistent under snapshot spy")
        events = _design_audit_selects(seen)
        check(events and all(in_tx for _, _, in_tx in events),
              "verify batch re-read is also pinned to one read transaction")
        check(len({conn_id for conn_id, _, _ in events}) == 1,
              "verify batch re-read uses one connection / one snapshot")


def test_concurrent_batch_read_never_mixes_versions():
    """并发快照保证（黑盒压力）：一个**独立进程**（不共享本进程的任何锁，
    与「另一服务实例 / 维护进程」等价）在批量读取期间持续用独立事务提交
    「设计 + 审计」成对修改，get_designs_many 每次返回的每一项必须自洽——
    审计 exceeds 与设计 max_float 始终来自同一版本，且整批结果按请求顺序、
    无缺失（ID 从不删除）。

    若读取逐条自动提交，设计与其审计可能分别落在更新前后两个版本，
    不变量即被打破。
    """
    import subprocess
    import sys
    import time

    with TestClient(app) as client:
        ids = [_create(client, TWILL)["id"] for _ in range(3)]
        ordered = sorted(ids)

        # 独立子进程维护写者：BEGIN IMMEDIATE 整事务成对更新 designs/audits。
        writer_code = (
            "import sqlite3, sys, time\n"
            "path, ids = sys.argv[1], [int(x) for x in sys.argv[2:]]\n"
            "conn = sqlite3.connect(path, timeout=10)\n"
            "version = 0\n"
            "while True:\n"
            "    version += 1\n"
            "    new_float = 5 if version % 2 else 1\n"
            "    new_exceeds = 0 if new_float == 5 else 1\n"
            "    conn.execute('BEGIN IMMEDIATE')\n"
            "    for design_id in ids:\n"
            "        conn.execute('UPDATE designs SET max_float = ? WHERE id = ?',\n"
            "                     (new_float, design_id))\n"
            "        conn.execute('UPDATE audits SET exceeds = ? WHERE design_id = ?',\n"
            "                     (new_exceeds, design_id))\n"
            "    conn.commit()\n"
            "    time.sleep(0)\n"
        )
        writer = subprocess.Popen(
            [sys.executable, "-c", writer_code, _TMP_DB.name, *map(str, ids)]
        )
        try:
            # 等写者先跑若干轮，保证读取期间确实有并发提交。
            time.sleep(0.3)
            check(writer.poll() is None, "maintenance writer process stays alive")
            for _ in range(200):
                rows = db.get_designs_many(ordered)
                check([r["id"] for r in rows] == ordered,
                      "snapshot batch preserves requested id order")
                for row in rows:
                    expected_exceeds = row["max_float"] == 1
                    check(
                        row["audit"]["exceeds"] == expected_exceeds,
                        "design parameters and audit come from the same snapshot "
                        "version (no new/old mixing under concurrent writes)",
                    )
                # 归档创建共用同一批量读取：只允许 200/201（读到的必然是
                # 某个自洽版本），绝不允许因快照拼版导致的 5xx 半成品。
                resp = client.post(
                    "/design-archives", json={"design_ids": ordered}
                )
                check(resp.status_code in (200, 201),
                      f"archive creation succeeds amid concurrent writes, "
                      f"got {resp.status_code}")
        finally:
            writer.terminate()
            writer.wait(10)
        check(writer.returncode != 0, "maintenance writer terminated with request")


def test_openapi_documents_archive_routes():
    schema = app.openapi()
    paths = schema["paths"]
    check("/design-archives" in paths, "OpenAPI documents POST /design-archives")
    check("post" in paths["/design-archives"], "archive POST method documented")
    verify_path = "/design-archives/{archive_id}/verify"
    check(verify_path in paths and "get" in paths[verify_path],
          "OpenAPI documents GET .../verify")
    post_responses = paths["/design-archives"]["post"]["responses"]
    for code in ("200", "201", "400", "404"):
        check(code in post_responses, f"POST documents {code} response")
    check("DesignArchiveIn" in schema["components"]["schemas"],
          "DesignArchiveIn schema published")
    check("DesignArchiveVerifyOut" in schema["components"]["schemas"],
          "DesignArchiveVerifyOut schema published")


if __name__ == "__main__":
    test_create_archive_manifest_and_digest()
    test_digest_idempotent_and_order_independent()
    test_duplicate_ids_rejected()
    test_missing_designs_rejected()
    test_pydantic_validation_422()
    test_verify_fresh_archive_consistent()
    test_verify_detects_deleted_design()
    test_verify_detects_drift_first_diff_path()
    test_batch_read_pinned_to_single_snapshot()
    test_concurrent_batch_read_never_mixes_versions()
    test_openapi_documents_archive_routes()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all design-archives API tests passed")
