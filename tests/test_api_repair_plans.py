"""POST /designs/{id}/repair-plans 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_repair_plans.py
（也可被 pytest 收集）。
"""

import os
import sys
import tempfile
import time

_TMP_DB = tempfile.NamedTemporaryFile(prefix="weave-test-", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.weave import audit_floats  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


# 8x8 绑综、16x16 交织、max_float=2 下有大量超限浮长的设计
BIG_DESIGN = {
    "name": "8x8 block",
    "cyclic": True,
    "max_float": 2,
    "threading": [s % 8 for s in range(16)],
    "treadling": [t % 8 for t in range(16)],
    "tie_up": [
        [1, 1, 1, 0, 0, 0, 1, 1],
        [1, 1, 1, 1, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 0, 0],
        [0, 0, 1, 1, 1, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 1, 1, 1],
        [1, 1, 0, 0, 0, 1, 1, 1],
    ],
}


def test_repair_plans_endpoint_known_sequence():
    """已知结果的小设计：6 踏单列全 1，两次翻转把循环 1-段切成三段，
    恰 3 个方案且按翻转序列字典序返回。"""
    with TestClient(app) as client:
        resp = client.post("/designs/99999/repair-plans")
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "404 detail message")

        design = {
            "name": "single column",
            "cyclic": True,
            "max_float": 2,
            "threading": [0],
            "tie_up": [[1], [1], [1], [1], [1], [1]],
            "treadling": [0, 1, 2, 3, 4, 5],
        }
        did = client.post("/designs", json=design).json()["id"]

        plans = client.post(
            f"/designs/{did}/repair-plans",
            params={"max_changes": 2, "limit": 50},
        ).json()
        check(len(plans) == 3, "exactly 3 two-flip plans")
        seqs = [[(s["treadle"], s["shaft"]) for s in p["steps"]] for p in plans]
        check(seqs == [[(0, 0), (3, 0)], [(1, 0), (4, 0)], [(2, 0), (5, 0)]],
              "plans in lexicographic sequence order")
        for p in plans:
            check(p["changes"] == 2 and p["final_violations"] == 0
                  and p["changed_points"] == 2 and p["violations"] == [],
                  "plan clears violations with 2 flips / 2 changed points")
            check([s["violations_after"] for s in p["steps"]] == [1, 0],
                  "intermediate violations may stay, final must improve")

        # 默认参数（max_changes=2, limit=50）与无改善设计
        plans = client.post(f"/designs/{did}/repair-plans").json()
        check(len(plans) == 3, "default query params work")
        plain = {
            "cyclic": False,
            "max_float": 2,
            "threading": [0, 1, 0, 1],
            "tie_up": [[1, 0], [0, 1]],
            "treadling": [0, 1, 0, 1],
        }
        pid = client.post("/designs", json=plain).json()["id"]
        resp = client.post(f"/designs/{pid}/repair-plans")
        check(resp.status_code == 200 and resp.json() == [],
              "empty list when no flip improves")

        # 查询参数越界由框架校验返回 422
        for params in ({"max_changes": 0}, {"max_changes": 4},
                       {"limit": 0}, {"limit": 51}):
            resp = client.post(f"/designs/{did}/repair-plans", params=params)
            check(resp.status_code == 422,
                  f"out-of-range {params} rejected with 422")


def test_repair_plans_three_flips_within_8s_and_read_only():
    """8x8 绑综、16x16 交织、max_changes=3、limit=5 必须在 8 秒内响应；
    每个入选方案按翻转序列重建绑综并完整重算审计，逐项与响应一致；
    接口只读，设计与审计结果在调用前后完全相同。"""
    with TestClient(app) as client:
        did = client.post("/designs", json=BIG_DESIGN).json()["id"]
        before = client.get(f"/designs/{did}").json()
        base_count = len(before["audit"]["violations"])
        check(base_count > 0, "baseline design has violations")

        start = time.perf_counter()
        resp = client.post(
            f"/designs/{did}/repair-plans",
            params={"max_changes": 3, "limit": 5},
        )
        elapsed = time.perf_counter() - start
        check(resp.status_code == 200, "repair-plans returns 200")
        check(elapsed < 8.0,
              f"8x8 tie-up / 16x16 interlacing max_changes=3 responds "
              f"within 8s ({elapsed:.2f}s)")
        plans = resp.json()
        check(0 < len(plans) <= 5, "limit=5 caps the returned plans")

        base_matrix = before["audit"]["interlacing"]
        for p in plans:
            check(p["final_violations"] < base_count,
                  "final state strictly improves over baseline")
            check(p["changes"] == len(p["steps"]),
                  "changes matches step count")
            # 按翻转序列逐步重建绑综并完整重算：每步与终态逐项一致
            final_tie_up = [row[:] for row in BIG_DESIGN["tie_up"]]
            for k, s in enumerate(p["steps"]):
                check(s["step"] == k + 1, "steps numbered from 1")
                check(final_tie_up[s["treadle"]][s["shaft"]] == s["old_value"]
                      and s["new_value"] == 1 - s["old_value"],
                      "each step flips the current cell value")
                final_tie_up[s["treadle"]][s["shaft"]] = s["new_value"]
                _, step_violations = audit_floats(
                    BIG_DESIGN["threading"], final_tie_up,
                    BIG_DESIGN["treadling"], 2, True,
                )
                check(len(step_violations) == s["violations_after"],
                      "violations_after matches full recompute at every step")
            matrix, violations = audit_floats(
                BIG_DESIGN["threading"], final_tie_up,
                BIG_DESIGN["treadling"], 2, True,
            )
            check(len(violations) == p["final_violations"],
                  "final_violations matches full recompute")
            check([v.to_dict() for v in violations] == p["violations"],
                  "final violation list matches full recompute item by item")
            check(matrix == p["interlacing"],
                  "final interlacing matches full recompute pointwise")
            changed = sum(
                a != b
                for row_a, row_b in zip(base_matrix, matrix)
                for a, b in zip(row_a, row_b)
            )
            check(changed == p["changed_points"],
                  "changed_points matches full recompute")

        # 排序键：最终违规数、改动数、变化点数、翻转序列字典序
        keys = [
            (p["final_violations"], p["changes"], p["changed_points"],
             [(s["treadle"], s["shaft"]) for s in p["steps"]])
            for p in plans
        ]
        check(keys == sorted(keys), "plans sorted by the declared key")

        # 接口只读：设计与审计结果不变
        after = client.get(f"/designs/{did}").json()
        check(after == before,
              "design and stored audit unchanged (endpoint is read-only)")


if __name__ == "__main__":
    test_repair_plans_endpoint_known_sequence()
    test_repair_plans_three_flips_within_8s_and_read_only()
    print("all tests passed")
