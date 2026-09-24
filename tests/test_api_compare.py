"""POST /designs/{id}/compare 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_compare.py
（也可被 pytest 收集）。
"""

import os
import sys
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(prefix="weave-test-", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def shifted(matrix, weft_offset, warp_offset):
    """与接口对齐约定一致：out[h][w] = matrix[(h+ro)%H][(w+co)%W]。"""
    picks, ends = len(matrix), len(matrix[0])
    return [
        [matrix[(h + weft_offset) % picks][(w + warp_offset) % ends]
         for w in range(ends)]
        for h in range(picks)
    ]


def test_compare_endpoint():
    with TestClient(app) as client:
        # 路径设计不存在 -> 404
        resp = client.post("/designs/99999/compare", json={"other_design_id": 1})
        check(resp.status_code == 404, "missing path design returns 404")
        check(resp.json()["detail"] == "design 99999 not found", "404 detail message")

        # 基准设计：3x3 矩阵（无任何非零循环移位对称，零错位偏移唯一）
        base = {
            "name": "base 3x3",
            "max_float": 3,
            "threading": [0, 1, 2],
            "tie_up": [[1, 0, 1], [0, 1, 1], [1, 0, 0]],
            "treadling": [0, 1, 2],
        }
        aid = client.post("/designs", json=base).json()["id"]

        # 请求体中的设计不存在 -> 404
        resp = client.post(f"/designs/{aid}/compare",
                           json={"other_design_id": 99999})
        check(resp.status_code == 404, "missing other design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "404 detail for other design")

        # 缺少必填字段 -> 422
        resp = client.post(f"/designs/{aid}/compare", json={})
        check(resp.status_code == 422, "missing other_design_id rejected")

        # 尺寸不一致 -> 400
        plain = {
            "name": "plain 4x4",
            "max_float": 2,
            "threading": [0, 1, 0, 1],
            "tie_up": [[1, 0], [0, 1]],
            "treadling": [0, 1, 0, 1],
        }
        pid = client.post("/designs", json=plain).json()["id"]
        resp = client.post(f"/designs/{aid}/compare",
                           json={"other_design_id": pid})
        check(resp.status_code == 400, "dimension mismatch returns 400")
        check("3x3 vs 4x4" in resp.json()["detail"], "400 detail mentions sizes")

        matrix_a = client.get(f"/designs/{aid}").json()["audit"]["interlacing"]
        check(matrix_a == [[1, 0, 1], [0, 1, 1], [1, 0, 0]],
              "base interlacing as expected")

        # 自我比对：零偏移、零错位
        resp = client.post(f"/designs/{aid}/compare",
                           json={"other_design_id": aid})
        check(resp.status_code == 200, "self compare 200")
        data = resp.json()
        check(data["design_id"] == aid and data["other_design_id"] == aid,
              "self compare echoes both ids")
        check(data["warp_ends"] == 3 and data["weft_picks"] == 3,
              "self compare dimensions")
        check(data["weft_offset"] == 0 and data["warp_offset"] == 0,
              "self compare: zero offset")
        check(data["mismatch_count"] == 0 and data["mismatch_ratio"] == 0.0,
              "self compare: zero mismatches")
        check(data["mismatches"] == [], "self compare: empty mismatch list")
        check(data["row_mismatches"] == [0, 0, 0]
              and data["col_mismatches"] == [0, 0, 0],
              "self compare: zero row/col summaries")
        check(data["aligned"] == matrix_a, "self compare: aligned equals own matrix")

        # 循环错位副本：穿综左移 1、踏综左移 2 -> 矩阵 b[h][w] = a[(h+2)%3][(w+1)%3]
        # 最佳偏移应为 weft_offset=1、warp_offset=2，零错位
        copy = {
            "name": "shifted copy",
            "max_float": 3,
            "threading": [1, 2, 0],
            "tie_up": [[1, 0, 1], [0, 1, 1], [1, 0, 0]],
            "treadling": [2, 0, 1],
        }
        cid = client.post("/designs", json=copy).json()["id"]
        data = client.post(f"/designs/{aid}/compare",
                           json={"other_design_id": cid}).json()
        check(data["weft_offset"] == 1 and data["warp_offset"] == 2,
              "shifted copy: best offset (1, 2)")
        check(data["mismatch_count"] == 0 and data["mismatches"] == [],
              "shifted copy: zero mismatches at best offset")
        check(data["aligned"] == matrix_a,
              "shifted copy: aligned restores the base matrix")
        matrix_c = client.get(f"/designs/{cid}").json()["audit"]["interlacing"]
        check(data["aligned"] == shifted(matrix_c, 1, 2),
              "shifted copy: aligned follows the documented shift convention")

        # 单单元差异：tie_up[2][2] 翻为 1 -> 矩阵仅 (2,2) 由 0 变 1
        noisy = {
            "name": "one cell off",
            "max_float": 3,
            "threading": [0, 1, 2],
            "tie_up": [[1, 0, 1], [0, 1, 1], [1, 0, 1]],
            "treadling": [0, 1, 2],
        }
        nid = client.post("/designs", json=noisy).json()["id"]
        data = client.post(f"/designs/{aid}/compare",
                           json={"other_design_id": nid}).json()
        check(data["weft_offset"] == 0 and data["warp_offset"] == 0,
              "one cell off: zero offset wins")
        check(data["mismatch_count"] == 1, "one cell off: one mismatch")
        check(abs(data["mismatch_ratio"] - 1 / 9) < 1e-12,
              "one cell off: ratio 1/9")
        check(data["mismatches"] == [
            {"row": 2, "col": 2, "design_value": 0, "other_value": 1}
        ], "one cell off: mismatch coordinate and values")
        check(data["row_mismatches"] == [0, 0, 1]
              and data["col_mismatches"] == [0, 0, 1],
              "one cell off: row/col summaries")
        check(data["aligned"] == [[1, 0, 1], [0, 1, 1], [1, 0, 1]],
              "one cell off: aligned is the unshifted second matrix")

        # 平局按行偏移再列偏移升序：行全同的 2x2，(0,1) 与 (1,1) 同为零错位
        rows_same_a = {
            "name": "rows same a",
            "max_float": 2,
            "threading": [0, 1],
            "tie_up": [[1, 0], [1, 0]],
            "treadling": [0, 1],
        }
        rows_same_b = {
            "name": "rows same b",
            "max_float": 2,
            "threading": [0, 1],
            "tie_up": [[0, 1], [0, 1]],
            "treadling": [0, 1],
        }
        ra = client.post("/designs", json=rows_same_a).json()["id"]
        rb = client.post("/designs", json=rows_same_b).json()["id"]
        data = client.post(f"/designs/{ra}/compare",
                           json={"other_design_id": rb}).json()
        check((data["weft_offset"], data["warp_offset"]) == (0, 1),
              "row tie broken by smaller weft offset")
        check(data["mismatch_count"] == 0, "row tie: zero mismatches")
        check(data["aligned"] == [[1, 0], [1, 0]],
              "row tie: aligned matrix content")

        # 反向比对（交换两份设计的角色）同样成立
        data = client.post(f"/designs/{cid}/compare",
                           json={"other_design_id": aid}).json()
        check(data["weft_offset"] == 2 and data["warp_offset"] == 1,
              "reversed compare: inverse offset (2, 1)")
        check(data["mismatch_count"] == 0, "reversed compare: zero mismatches")
        matrix_c = client.get(f"/designs/{cid}").json()["audit"]["interlacing"]
        check(data["aligned"] == matrix_c,
              "reversed compare: aligned restores the shifted copy matrix")

        # 接口只读：比对不改库，设计数量与内容均不变
        ids_before = {d["id"] for d in client.get("/designs").json()}
        client.post(f"/designs/{aid}/compare", json={"other_design_id": nid})
        client.post(f"/designs/{nid}/compare", json={"other_design_id": aid})
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_before == ids_after, "compare creates no new designs")
        again = client.get(f"/designs/{aid}").json()
        check(again["threading"] == [0, 1, 2]
              and again["tie_up"] == [[1, 0, 1], [0, 1, 1], [1, 0, 0]]
              and again["treadling"] == [0, 1, 2],
              "compare does not modify the stored design")
        check(again["audit"]["interlacing"] == matrix_a,
              "compare does not modify the stored audit")


def test_compare_large_matrix_timing():
    """64x64 交织矩阵的端到端比对耗时回归：优化前接口单次约 19 秒（朴素逐偏移
    构造完整矩阵+逐点比对），优化后应远低于 1 秒。两个设计取相同穿综/踏综、
    随机绑综（矩阵为穿综的重排），尺寸一致且非退化。"""
    import time

    with TestClient(app) as client:
        def random_design(seed):
            import random
            rng = random.Random(seed)
            return {
                "name": f"random 64x64 {seed}",
                "max_float": 64,
                "threading": list(range(64)),
                "tie_up": [[rng.randrange(2) for _ in range(64)] for _ in range(64)],
                "treadling": list(range(64)),
            }

        first_id = client.post("/designs", json=random_design(101)).json()["id"]
        second_id = client.post("/designs", json=random_design(202)).json()["id"]
        ids_before = {d["id"] for d in client.get("/designs").json()}
        start = time.perf_counter()
        resp = client.post(f"/designs/{first_id}/compare",
                           json={"other_design_id": second_id})
        elapsed = time.perf_counter() - start
        check(resp.status_code == 200, "64x64 compare succeeds")
        data = resp.json()
        check(data["warp_ends"] == 64 and data["weft_picks"] == 64,
              "64x64 compare reports dimensions")
        check(elapsed < 1.0, f"64x64 compare fast: {elapsed:.3f}s < 1.0s")
        # 明细规模与结构保持原契约
        check(0 <= data["weft_offset"] < 64 and 0 <= data["warp_offset"] < 64,
              "64x64 compare: offsets within range")
        check(data["mismatch_count"] == len(data["mismatches"]),
              "64x64 compare: mismatch list length equals count")
        check(len(data["row_mismatches"]) == 64
              and len(data["col_mismatches"]) == 64
              and len(data["aligned"]) == 64
              and all(len(row) == 64 for row in data["aligned"]),
              "64x64 compare: summaries and aligned matrix have full dimensions")
        check(sum(data["row_mismatches"]) == data["mismatch_count"]
              and sum(data["col_mismatches"]) == data["mismatch_count"],
              "64x64 compare: row/col summaries sum to mismatch count")
        # 接口只读：大矩阵比对同样不新增设计
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_before == ids_after,
              "64x64 compare creates no extra designs")


if __name__ == "__main__":
    test_compare_endpoint()
    test_compare_large_matrix_timing()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
