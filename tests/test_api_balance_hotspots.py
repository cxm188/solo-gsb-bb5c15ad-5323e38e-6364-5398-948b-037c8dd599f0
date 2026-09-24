"""POST /designs/{id}/balance-hotspots 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_balance_hotspots.py
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


# 4x4 分块矩阵：
# 1 1 0 0
# 1 1 0 0
# 0 0 1 1
# 0 0 1 1
BLOCKS = {
    "name": "4x4 blocks",
    "max_float": 4,
    "threading": [0, 0, 1, 1],
    "tie_up": [[1, 0], [0, 1]],
    "treadling": [0, 0, 1, 1],
}

# 3x3 上三角矩阵：
# 1 1 1
# 0 1 0
# 0 0 1
TRIANGLE = {
    "name": "3x3 triangle",
    "max_float": 3,
    "threading": [0, 1, 2],
    "tie_up": [[1, 1, 1], [0, 1, 0], [0, 0, 1]],
    "treadling": [0, 1, 2],
}


def test_balance_hotspots_endpoint():
    with TestClient(app) as client:
        # 设计不存在 -> 404
        resp = client.post(
            "/designs/99999/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        )
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found", "404 detail message")

        bid = client.post("/designs", json=BLOCKS).json()["id"]
        matrix = client.get(f"/designs/{bid}").json()["audit"]["interlacing"]
        check(matrix == [[1, 1, 0, 0], [1, 1, 0, 0],
                         [0, 0, 1, 1], [0, 0, 1, 1]],
              "block interlacing as expected")

        # 参数校验：窗口尺寸与占比区间越界、下限大于上限 -> 422
        bad_bodies = [
            {"window_rows": 0, "window_cols": 2,
             "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
            {"window_rows": 65, "window_cols": 2,
             "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
            {"window_rows": 2, "window_cols": 0,
             "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
            {"window_rows": 2, "window_cols": 65,
             "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
            {"window_rows": 2, "window_cols": 2,
             "warp_ratio_min": -0.1, "warp_ratio_max": 0.75},
            {"window_rows": 2, "window_cols": 2,
             "warp_ratio_min": 0.25, "warp_ratio_max": 1.1},
            {"window_rows": 2, "window_cols": 2,
             "warp_ratio_min": 0.75, "warp_ratio_max": 0.25},
            {"window_rows": 2, "window_cols": 2, "warp_ratio_min": 0.25},
        ]
        for body in bad_bodies:
            resp = client.post(f"/designs/{bid}/balance-hotspots", json=body)
            check(resp.status_code == 422, f"invalid body {body} rejected with 422")

        # 非循环 2x2 窗口：只扫描完整落入矩阵的 3x3=9 个窗口
        resp = client.post(
            f"/designs/{bid}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        )
        check(resp.status_code == 200, "non-cyclic scan 200")
        data = resp.json()
        check(data["design_id"] == bid and data["cyclic"] is False
              and data["window_rows"] == 2 and data["window_cols"] == 2
              and data["warp_ratio_min"] == 0.25
              and data["warp_ratio_max"] == 0.75,
              "echoes design id, cyclic, window and thresholds")
        check(data["windows_scanned"] == 9,
              "non-cyclic scans only fully-contained windows")
        expected = [
            {"row": 0, "col": 0, "warp_ratio": 1.0, "deviation": 0.25},
            {"row": 0, "col": 2, "warp_ratio": 0.0, "deviation": 0.25},
            {"row": 2, "col": 0, "warp_ratio": 0.0, "deviation": 0.25},
            {"row": 2, "col": 2, "warp_ratio": 1.0, "deviation": 0.25},
        ]
        check(data["hotspots"] == expected,
              "non-cyclic hotspots sorted by top-left coordinate")
        check(data["worst_hotspot"] == expected[0],
              "worst hotspot: tie broken by earliest top-left coordinate")

        # 阈值含边界：占比恰为 0 / 1 的窗口在 [0, 1] 内不超界
        data = client.post(
            f"/designs/{bid}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.0, "warp_ratio_max": 1.0},
        ).json()
        check(data["windows_scanned"] == 9 and data["hotspots"] == []
              and data["worst_hotspot"] is None,
              "inclusive thresholds: no hotspots, worst is null")

        # 非循环窗口大于矩阵：无窗可扫
        data = client.post(
            f"/designs/{bid}/balance-hotspots",
            json={"window_rows": 5, "window_cols": 5,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        ).json()
        check(data["windows_scanned"] == 0 and data["hotspots"] == []
              and data["worst_hotspot"] is None,
              "non-cyclic window larger than matrix scans nothing")

        # 1x1 窗口：每个单元都是窗口，占比 0 或 1 全部超界
        data = client.post(
            f"/designs/{bid}/balance-hotspots",
            json={"window_rows": 1, "window_cols": 1,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        ).json()
        check(data["windows_scanned"] == 16 and len(data["hotspots"]) == 16,
              "1x1 window scans every cell, all out of [0.25, 0.75]")
        check([(h["row"], h["col"]) for h in data["hotspots"]]
              == [(r, c) for r in range(4) for c in range(4)],
              "1x1 hotspots cover all cells in row-major order")
        check(all(h["deviation"] == 0.25 for h in data["hotspots"]),
              "1x1 hotspots all deviate 0.25 from the nearest threshold")

        # 循环设计：每个原矩阵单元都是左上角，两轴取模回绕
        cyclic_blocks = dict(BLOCKS, name="4x4 blocks cyclic", cyclic=True)
        cid = client.post("/designs", json=cyclic_blocks).json()["id"]
        data = client.post(
            f"/designs/{cid}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        ).json()
        check(data["cyclic"] is True and data["windows_scanned"] == 16,
              "cyclic scans one window per matrix cell")
        check(data["hotspots"] == expected,
              "cyclic hotspots: wrap-around windows stay balanced, "
              "same four corners exceed")
        check(data["worst_hotspot"] == expected[0],
              "cyclic worst hotspot matches")

        # 循环设计窗口超过矩阵尺寸 -> 400
        resp = client.post(
            f"/designs/{cid}/balance-hotspots",
            json={"window_rows": 5, "window_cols": 2,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        )
        check(resp.status_code == 400,
              "cyclic window taller than the matrix returns 400")
        check("5x2" in resp.json()["detail"] and "4x4" in resp.json()["detail"],
              "400 detail mentions window and matrix sizes")
        resp = client.post(
            f"/designs/{cid}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 5,
                  "warp_ratio_min": 0.25, "warp_ratio_max": 0.75},
        )
        check(resp.status_code == 400,
              "cyclic window wider than the matrix returns 400")

        # 循环回绕窗口取值验证：3x3 上三角矩阵的 2x2 回绕窗口，
        # 逐窗占比为 0.75/0.75/0.5、0.25/0.5/0.25、0.5/0.75/0.75
        cyclic_tri = dict(TRIANGLE, name="3x3 triangle cyclic", cyclic=True)
        tid = client.post("/designs", json=cyclic_tri).json()["id"]
        data = client.post(
            f"/designs/{tid}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.3, "warp_ratio_max": 0.7},
        ).json()
        check(data["windows_scanned"] == 9, "cyclic triangle scans 9 windows")
        check([(h["row"], h["col"]) for h in data["hotspots"]]
              == [(0, 0), (0, 1), (1, 0), (1, 2), (2, 1), (2, 2)],
              "cyclic triangle hotspots include wrap-around windows")
        check([h["warp_ratio"] for h in data["hotspots"]]
              == [0.75, 0.75, 0.25, 0.25, 0.75, 0.75],
              "cyclic triangle wrap-around window ratios")
        check(all(abs(h["deviation"] - 0.05) < 1e-12
                  for h in data["hotspots"]),
              "cyclic triangle deviations from the nearest threshold")
        check((data["worst_hotspot"]["row"], data["worst_hotspot"]["col"])
              == (0, 0),
              "cyclic triangle worst: first top-left among equal deviations")

        # 非循环 3x3 上三角：2x2 窗口占比为 0.75/0.75/0.25/0.5，
        # 阈值 [0.6, 0.7] 下全部超界且偏离各异，最严重窗口唯一
        tid2 = client.post("/designs", json=TRIANGLE).json()["id"]
        data = client.post(
            f"/designs/{tid2}/balance-hotspots",
            json={"window_rows": 2, "window_cols": 2,
                  "warp_ratio_min": 0.6, "warp_ratio_max": 0.7},
        ).json()
        check(data["windows_scanned"] == 4, "triangle scans 2x2 windows")
        check([(h["row"], h["col"]) for h in data["hotspots"]]
              == [(0, 0), (0, 1), (1, 0), (1, 1)],
              "triangle hotspots in row-major order")
        ratios = [h["warp_ratio"] for h in data["hotspots"]]
        check(ratios == [0.75, 0.75, 0.25, 0.5],
              "triangle window ratios")
        devs = [h["deviation"] for h in data["hotspots"]]
        check(abs(devs[0] - 0.05) < 1e-12 and abs(devs[1] - 0.05) < 1e-12
              and abs(devs[2] - 0.35) < 1e-12 and abs(devs[3] - 0.1) < 1e-12,
              "deviations measure distance to the nearest threshold")
        worst = data["worst_hotspot"]
        check(worst["row"] == 1 and worst["col"] == 0
              and worst["warp_ratio"] == 0.25
              and abs(worst["deviation"] - 0.35) < 1e-12,
              "worst hotspot is the unique max-deviation window")

        # 接口只读：不改库，设计数量与内容均不变
        ids_before = {d["id"] for d in client.get("/designs").json()}
        client.post(f"/designs/{bid}/balance-hotspots",
                    json={"window_rows": 2, "window_cols": 2,
                          "warp_ratio_min": 0.25, "warp_ratio_max": 0.75})
        client.post(f"/designs/{cid}/balance-hotspots",
                    json={"window_rows": 4, "window_cols": 4,
                          "warp_ratio_min": 0.0, "warp_ratio_max": 1.0})
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_before == ids_after, "balance-hotspots creates no new designs")
        again = client.get(f"/designs/{bid}").json()
        check(again["threading"] == BLOCKS["threading"]
              and again["tie_up"] == BLOCKS["tie_up"]
              and again["treadling"] == BLOCKS["treadling"]
              and again["cyclic"] is False,
              "balance-hotspots does not modify the stored design")
        check(again["audit"]["interlacing"] == matrix,
              "balance-hotspots does not modify the stored audit")


if __name__ == "__main__":
    test_balance_hotspots_endpoint()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
