"""GET /designs/{id}/float-risk-zones 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_float_risk_zones.py
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


# 4 纬 3 经，max_float=2：列 0 是长度 4 的经向浮长（唯一违规），
# 列 2 上的 1 段长度 2 不超限；行内 0 段最长 2，不产生纬向违规。交织矩阵：
# 1 0 1
# 1 0 1
# 1 0 0
# 1 0 0
WARP_RUN = {
    "name": "single warp run 4x3",
    "max_float": 2,
    "threading": [0, 1, 2],
    "tie_up": [[1, 0, 1], [1, 0, 0]],
    "treadling": [0, 0, 1, 1],
}

# 3x3，max_float=1：经/纬浮长在边缘相邻，7 个单元合并为一个混合方向区域：
# 1 1 0
# 1 1 0
# 0 0 0
MIXED = {
    "name": "mixed warp/weft 3x3",
    "max_float": 1,
    "threading": [0, 0, 1],
    "tie_up": [[1, 0], [0, 1], [0, 0]],
    "treadling": [0, 0, 2],
}

# 5 纬 1 经：列 0 取值 1,1,0,0,1。cyclic 下首尾的 1 段跨边界合并为
# start=4、length=3 的经向浮长；非循环下没有任何违规。
SEAM_VERTICAL = {
    "name": "vertical seam 5x1",
    "max_float": 2,
    "threading": [0],
    "tie_up": [[1], [0]],
    "treadling": [0, 0, 1, 1, 0],
}

# 1 纬 5 经：行取值 0,0,1,0,0。cyclic 下两端的 0 段跨边界合并为
# start=3、length=4 的纬向浮长。
SEAM_HORIZONTAL = {
    "name": "horizontal seam 1x5",
    "max_float": 2,
    "threading": [0, 0, 1, 0, 0],
    "tie_up": [[0, 1]],
    "treadling": [0],
}

# 5 纬 5 经，max_float=2，非循环：列 0、2、4 各有长度 3 的经向浮长
# （都在 0..2 行），列间至少隔一列未标记单元，互不相邻；无纬向违规。
# 三个区域严重度相同，按最小单元坐标（列）升序。交织矩阵：
# 1 0 1 0 1
# 1 0 1 0 1
# 1 0 1 0 1
# 0 1 0 1 0
# 0 1 0 1 1
THREE_ZONES = {
    "name": "three equal zones 5x5",
    "max_float": 2,
    "threading": [0, 1, 0, 1, 2],
    "tie_up": [
        [1, 0, 1],
        [1, 0, 1],
        [1, 0, 1],
        [0, 1, 0],
        [0, 1, 1],
    ],
    "treadling": [0, 1, 2, 3, 4],
}


def test_float_risk_zones_endpoint():
    with TestClient(app) as client:
        # 设计不存在 -> 404
        resp = client.get("/designs/99999/float-risk-zones")
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "404 detail message")

        # 无违规设计 -> 空列表、总数 0
        twill = {
            "name": "2/2 twill 4x4",
            "max_float": 2,
            "threading": [0, 1, 2, 3],
            "tie_up": [[1, 1, 0, 0], [0, 1, 1, 0],
                       [0, 0, 1, 1], [1, 0, 0, 1]],
            "treadling": [0, 1, 2, 3],
        }
        tid = client.post("/designs", json=twill).json()["id"]
        resp = client.get(f"/designs/{tid}/float-risk-zones")
        check(resp.status_code == 200, "clean design returns 200")
        data = resp.json()
        check(data["design_id"] == tid and data["cyclic"] is False
              and data["warp_ends"] == 4 and data["weft_picks"] == 4
              and data["zone_count"] == 0 and data["zones"] == [],
              "clean design: dimensions echoed, empty zones and zero count")

        # 读取的是入库时的违规记录：同一份 WARP_RUN 以宽松阈值 max_float=4
        # 入库（无违规），即使矩阵含长度 4 的连续 1 也返回空列表
        lenient = dict(WARP_RUN, name="lenient threshold", max_float=4)
        lid = client.post("/designs", json=lenient).json()["id"]
        data = client.get(f"/designs/{lid}/float-risk-zones").json()
        check(data["zone_count"] == 0 and data["zones"] == [],
              "aggregation consumes the stored violations, not a re-audit")

        # 单条经向浮长 -> 一个区域
        wid = client.post("/designs", json=WARP_RUN).json()["id"]
        data = client.get(f"/designs/{wid}/float-risk-zones").json()
        check(data["warp_ends"] == 3 and data["weft_picks"] == 4
              and data["zone_count"] == 1,
              "warp run: dimensions and zone count")
        zone = data["zones"][0]
        check([(c["row"], c["col"]) for c in zone["cells"]]
              == [(0, 0), (1, 0), (2, 0), (3, 0)],
              "warp run: cells in row-major order")
        check(zone["area"] == 4 and zone["max_float"] == 4
              and zone["directions"] == ["warp"],
              "warp run: area, max float and direction")
        check(zone["bbox"] == {"min_row": 0, "min_col": 0,
                               "max_row": 3, "max_col": 0,
                               "rows": 4, "cols": 1},
              "warp run: bounding box")

        # 经/纬相邻 -> 一个混合方向区域（仅对角接触不合并的规则在单元测试覆盖）
        mid = client.post("/designs", json=MIXED).json()["id"]
        data = client.get(f"/designs/{mid}/float-risk-zones").json()
        check(data["zone_count"] == 1, "mixed runs merge into one zone")
        zone = data["zones"][0]
        expected_cells = [
            {"row": r, "col": c}
            for r in range(3) for c in range(3)
            if (r, c) != (0, 2) and (r, c) != (1, 2)
        ]
        check(zone["cells"] == expected_cells,
              "mixed zone: seven flagged cells in row-major order "
              "(the two isolated 0-column cells are not flagged)")
        check(zone["area"] == 7
              and zone["max_float"] == 3
              and zone["directions"] == ["warp", "weft"],
              "mixed zone: area, max float from the weft run, both directions")
        check(zone["bbox"] == {"min_row": 0, "min_col": 0,
                               "max_row": 2, "max_col": 2,
                               "rows": 3, "cols": 3},
              "mixed zone bbox")

        # 循环设计：垂直方向首尾相接，跨边界的 3 个单元合并为一区
        vid = client.post(
            "/designs", json=dict(SEAM_VERTICAL, cyclic=True)
        ).json()["id"]
        data = client.get(f"/designs/{vid}/float-risk-zones").json()
        check(data["cyclic"] is True and data["zone_count"] == 1,
              "cyclic vertical seam: one zone")
        zone = data["zones"][0]
        check([(c["row"], c["col"]) for c in zone["cells"]]
              == [(0, 0), (1, 0), (4, 0)],
              "cyclic vertical seam: cells span both ends, sorted")
        check(zone["area"] == 3 and zone["max_float"] == 3
              and zone["directions"] == ["warp"],
              "cyclic vertical seam: summary")
        check(zone["bbox"] == {"min_row": 0, "min_col": 0,
                               "max_row": 4, "max_col": 0,
                               "rows": 5, "cols": 1},
              "wrap-around zone bbox spans the full row range; cells are exact")

        # 同一矩阵非循环：入库违规为空 -> 空列表
        vid_nc = client.post("/designs", json=SEAM_VERTICAL).json()["id"]
        data = client.get(f"/designs/{vid_nc}/float-risk-zones").json()
        check(data["cyclic"] is False and data["zone_count"] == 0
              and data["zones"] == [],
              "non-cyclic audit stored no seam violation -> empty zones")

        # 循环设计：水平方向首尾相接
        hid = client.post(
            "/designs", json=dict(SEAM_HORIZONTAL, cyclic=True)
        ).json()["id"]
        data = client.get(f"/designs/{hid}/float-risk-zones").json()
        check(data["zone_count"] == 1, "cyclic horizontal seam: one zone")
        zone = data["zones"][0]
        check([(c["row"], c["col"]) for c in zone["cells"]]
              == [(0, 0), (0, 1), (0, 3), (0, 4)],
              "cyclic horizontal seam: cells span both ends, sorted")
        check(zone["area"] == 4 and zone["max_float"] == 4
              and zone["directions"] == ["weft"],
              "cyclic horizontal seam: summary")
        check(zone["bbox"] == {"min_row": 0, "min_col": 0,
                               "max_row": 0, "max_col": 4,
                               "rows": 1, "cols": 5},
              "wrap-around zone bbox spans the full column range")

        # 三个互不相邻、严重度相同的区域：按最小单元坐标（行后列）升序
        zid = client.post("/designs", json=THREE_ZONES).json()["id"]
        data = client.get(f"/designs/{zid}/float-risk-zones").json()
        check(data["zone_count"] == 3, "three disconnected warp runs")
        for col, zone in zip((0, 2, 4), data["zones"]):
            check([(c["row"], c["col"]) for c in zone["cells"]]
                  == [(0, col), (1, col), (2, col)],
                  f"zone at column {col} cells")
            check(zone["area"] == 3 and zone["max_float"] == 3
                  and zone["directions"] == ["warp"],
                  f"zone at column {col} summary")
        check([z["cells"][0] for z in data["zones"]]
              == [{"row": 0, "col": 0}, {"row": 0, "col": 2},
                  {"row": 0, "col": 4}],
              "equal-severity zones tie-broken by smallest cell (row, col)")

        # 接口只读：重复调用结果一致，设计数量与入库审计不变
        ids_before = {d["id"] for d in client.get("/designs").json()}
        audit_before = client.get(f"/designs/{mid}/audit").json()
        first = client.get(f"/designs/{mid}/float-risk-zones").json()
        second = client.get(f"/designs/{mid}/float-risk-zones").json()
        check(first == second, "repeated calls return identical results")
        ids_after = {d["id"] for d in client.get("/designs").json()}
        audit_after = client.get(f"/designs/{mid}/audit").json()
        check(ids_before == ids_after, "float-risk-zones creates no new designs")
        check(audit_before == audit_after,
              "float-risk-zones does not modify the stored audit")


if __name__ == "__main__":
    test_float_risk_zones_endpoint()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
