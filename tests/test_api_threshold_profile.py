"""GET /designs/{id}/threshold-profile 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_threshold_profile.py
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


def test_threshold_profile_endpoint():
    with TestClient(app) as client:
        # 不存在的设计 -> 404
        resp = client.get("/designs/99999/threshold-profile")
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found", "404 detail message")

        # 循环跨边界合并后长度 4 的经向浮长（入库阈值 max_float=2）
        wrap = {
            "name": "cyclic wrap",
            "cyclic": True,
            "max_float": 2,
            "threading": [0],
            "tie_up": [[1], [0]],
            "treadling": [0, 0, 1, 0, 0],
        }
        wid = client.post("/designs", json=wrap).json()["id"]

        # 缺省上限 = 原设计 max_float：只覆盖阈值 1..2，达不到零违规
        resp = client.get(f"/designs/{wid}/threshold-profile")
        check(resp.status_code == 200, "default limit endpoint 200")
        data = resp.json()
        check(data["design_id"] == wid and data["cyclic"] is True
              and data["max_float"] == 2,
              "echoes design id, cyclic and stored max_float")
        check(data["max_float_limit"] == 2, "default limit is the stored max_float")
        check([p["max_float"] for p in data["profile"]] == [1, 2],
              "default profile covers thresholds 1..max_float")
        check([(p["warp_violations"], p["weft_violations"], p["total_violations"])
               for p in data["profile"]] == [(1, 0, 1), (1, 0, 1)],
              "default profile: one warp violation per threshold")
        check(data["min_zero_violation_threshold"] is None,
              "zero violations unreachable within default limit -> null")

        # 显式上限 4：阈值 4 清零，最小零违规阈值为 4
        data = client.get(f"/designs/{wid}/threshold-profile",
                          params={"max_float_limit": 4}).json()
        check(data["max_float_limit"] == 4 and len(data["profile"]) == 4,
              "explicit limit extends the profile to 4 points")
        check([p["total_violations"] for p in data["profile"]]
              == [1, 1, 1, 0],
              "wrap float of length 4 cleared at threshold 4")
        check(data["profile"][3] == {"max_float": 4, "warp_violations": 0,
                                     "weft_violations": 0, "total_violations": 0},
              "threshold 4 point is fully clean")
        check(data["min_zero_violation_threshold"] == 4,
              "min zero-violation threshold is 4")

        # 与入库审计一致：阈值等于原 max_float 的档位，违规数与库存审计相同
        stored = client.get(f"/designs/{wid}").json()
        stored_violations = stored["audit"]["violations"]
        at_stored = data["profile"][stored["max_float"] - 1]
        check(at_stored["total_violations"] == len(stored_violations),
              "point at stored max_float matches the stored audit count")
        check(at_stored["warp_violations"]
              == sum(1 for v in stored_violations if v["direction"] == "warp"),
              "warp split matches the stored audit")

        # 纬向浮长设计：第 0 纬连续浮过 3 根经纱，阈值 3 清零
        weft = {
            "name": "weft float",
            "max_float": 1,
            "threading": [0, 0, 0],
            "tie_up": [[0], [1]],
            "treadling": [0, 1],
        }
        fid = client.post("/designs", json=weft).json()["id"]
        data = client.get(f"/designs/{fid}/threshold-profile",
                          params={"max_float_limit": 3}).json()
        check([(p["warp_violations"], p["weft_violations"])
               for p in data["profile"]] == [(0, 1), (0, 1), (0, 0)],
              "weft-only float counted per threshold")
        check(data["min_zero_violation_threshold"] == 3,
              "weft float cleared at threshold 3")

        # 无违规设计：阈值 1 即零违规
        plain = {
            "name": "plain",
            "max_float": 2,
            "threading": [0, 1, 0, 1],
            "tie_up": [[1, 0], [0, 1]],
            "treadling": [0, 1, 0, 1],
        }
        pid = client.post("/designs", json=plain).json()["id"]
        data = client.get(f"/designs/{pid}/threshold-profile").json()
        check(data["min_zero_violation_threshold"] == 1
              and all(p["total_violations"] == 0 for p in data["profile"]),
              "clean design: zero violations from threshold 1")

        # 入库 max_float 超过 64 时，缺省上限按 64 封顶
        huge = {
            "name": "huge max_float",
            "max_float": 100,
            "threading": [0],
            "tie_up": [[1], [0]],
            "treadling": [0, 1],
        }
        hid = client.post("/designs", json=huge).json()["id"]
        data = client.get(f"/designs/{hid}/threshold-profile").json()
        check(data["max_float"] == 100 and data["max_float_limit"] == 64
              and len(data["profile"]) == 64,
              "default limit capped at 64 for oversized stored max_float")

        # max_float_limit 越界或非整数 -> 422
        for bad in ("0", "65", "-1", "2.5", "abc"):
            resp = client.get(f"/designs/{wid}/threshold-profile",
                              params={"max_float_limit": bad})
            check(resp.status_code == 422,
                  f"max_float_limit={bad} rejected with 422")

        # 接口只读：不改库，设计数量与内容均不变
        ids_before = {d["id"] for d in client.get("/designs").json()}
        client.get(f"/designs/{wid}/threshold-profile",
                   params={"max_float_limit": 64})
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_before == ids_after, "threshold-profile creates no new designs")
        again = client.get(f"/designs/{wid}").json()
        check(again["threading"] == [0] and again["tie_up"] == [[1], [0]]
              and again["treadling"] == [0, 0, 1, 0, 0]
              and again["max_float"] == 2 and again["cyclic"] is True,
              "threshold-profile does not modify the stored design")
        check(again["audit"]["violations"] == stored_violations,
              "threshold-profile does not modify the stored audit")


if __name__ == "__main__":
    test_threshold_profile_endpoint()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
