"""GET /designs/{id}/repeat-period 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_repeat_period.py
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


def tiles(interlacing, warp_period, weft_period, unit):
    picks, ends = len(interlacing), len(interlacing[0])
    return (
        ends % warp_period == 0
        and picks % weft_period == 0
        and len(unit) == weft_period
        and all(len(r) == warp_period for r in unit)
        and all(
            interlacing[h][w] == unit[h % weft_period][w % warp_period]
            for h in range(picks)
            for w in range(ends)
        )
    )


def test_repeat_period_endpoint():
    with TestClient(app) as client:
        # 不存在的设计 -> 404
        resp = client.get("/designs/99999/repeat-period")
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found", "404 detail message")

        # 平纹 4x4：周期 2/2，严格二维重复
        plain = {
            "name": "plain",
            "max_float": 2,
            "threading": [0, 1, 0, 1],
            "tie_up": [[1, 0], [0, 1]],
            "treadling": [0, 1, 0, 1],
        }
        pid = client.post("/designs", json=plain).json()["id"]
        resp = client.get(f"/designs/{pid}/repeat-period")
        check(resp.status_code == 200, "plain weave endpoint 200")
        data = resp.json()
        check(data["warp_period"] == 2 and data["weft_period"] == 2,
              "plain weave periods 2/2")
        check(data["unit"] == [[1, 0], [0, 1]], "plain weave unit 2x2")
        check(data["strict_2d_repeat"] is True, "plain weave strict 2D")

        # 2/2 斜纹 8x8（cyclic）：经纬周期都是 4，单元为 4x4 斜纹块
        twill = {
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
        tid = client.post("/designs", json=twill).json()["id"]
        data = client.get(f"/designs/{tid}/repeat-period").json()
        expected_unit = [
            [1, 1, 0, 0],
            [0, 1, 1, 0],
            [0, 0, 1, 1],
            [1, 0, 0, 1],
        ]
        check(data["warp_period"] == 4 and data["weft_period"] == 4,
              "8x8 twill periods 4/4")
        check(data["unit"] == expected_unit, "8x8 twill unit is 4x4 block")
        check(data["strict_2d_repeat"] is True, "8x8 twill strict 2D")
        interlacing = client.get(f"/designs/{tid}").json()["audit"]["interlacing"]
        check(tiles(interlacing, data["warp_period"], data["weft_period"],
                    data["unit"]),
              "twill unit tiles stored interlacing across boundary")

        # 素数尺寸无重复：返回完整尺寸，非严格二维重复
        prime = {
            "name": "prime",
            "max_float": 3,
            "threading": [0, 1, 2],
            "tie_up": [[1, 0, 1], [0, 1, 1], [1, 1, 0]],
            "treadling": [0, 1, 2],
        }
        rid = client.post("/designs", json=prime).json()["id"]
        data = client.get(f"/designs/{rid}/repeat-period").json()
        check(data["warp_period"] == 3 and data["weft_period"] == 3,
              "prime 3x3: full-size periods")
        check(len(data["unit"]) == 3 and all(len(r) == 3 for r in data["unit"]),
              "prime 3x3: unit is full 3x3 matrix")
        check(data["strict_2d_repeat"] is False, "prime 3x3: not strict 2D")

        # 基于交织矩阵而非 threading：threading 序列无 2-周期，
        # 但 tie_up 让穿综 0、2 的列表现一致，矩阵经向周期为 2
        matrix_based = {
            "name": "matrix-based",
            "max_float": 4,
            "threading": [0, 1, 2, 1],
            "tie_up": [[1, 0, 1], [0, 1, 0], [0, 0, 1]],
            "treadling": [0, 1, 0, 1],
        }
        mid = client.post("/designs", json=matrix_based).json()["id"]
        data = client.get(f"/designs/{mid}/repeat-period").json()
        check(matrix_based["threading"][0] != matrix_based["threading"][2],
              "threading sequence alone has no 2-period")
        check(data["warp_period"] == 2 and data["weft_period"] == 2,
              "matrix-based: periods derived from interlacing (2/2)")
        check(data["unit"] == [[1, 0], [0, 1]], "matrix-based: 2x2 unit")
        check(data["strict_2d_repeat"] is True, "matrix-based: strict 2D")

        # 循环边界：单列 1 1 0 1 1 0，周期 3 整除 6，不可误判为 2
        cyclic_boundary = {
            "name": "cyclic boundary",
            "cyclic": True,
            "max_float": 2,
            "threading": [0],
            "tie_up": [[1], [0]],
            "treadling": [0, 0, 1, 0, 0, 1],
        }
        cid = client.post("/designs", json=cyclic_boundary).json()["id"]
        data = client.get(f"/designs/{cid}/repeat-period").json()
        check(data["warp_period"] == 1 and data["weft_period"] == 3,
              "cyclic single column: periods 1/3 (boundary handled)")
        check(data["unit"] == [[1], [1], [0]], "cyclic single column: 3x1 unit")
        check(data["strict_2d_repeat"] is False,
              "cyclic single column: not strict 2D")
        interlacing = client.get(f"/designs/{cid}").json()["audit"]["interlacing"]
        check(tiles(interlacing, data["warp_period"], data["weft_period"],
                    data["unit"]),
              "cyclic 3x1 unit tiles the 6x1 column across boundary")

        # 接口只读，不改变设计本身
        again = client.get(f"/designs/{cid}").json()
        check(again["threading"] == [0] and again["treadling"] == [0, 0, 1, 0, 0, 1],
              "repeat-period analysis does not modify the stored design")


if __name__ == "__main__":
    test_repeat_period_endpoint()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
