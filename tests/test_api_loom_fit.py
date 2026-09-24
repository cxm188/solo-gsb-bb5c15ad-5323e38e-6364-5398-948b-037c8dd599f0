"""POST /designs/{id}/loom-fit 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_loom_fit.py
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
from app import weave  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


# 4 综 4 踏但本质为平纹：绑综列 0/2、1/3 相同，行 0/2、1/3 相同
REDUNDANT = {
    "name": "plain weave in 4x4 frame",
    "max_float": 2,
    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
    "tie_up": [
        [1, 0, 1, 0],
        [0, 1, 0, 1],
        [1, 0, 1, 0],
        [0, 1, 0, 1],
    ],
    "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
}

# 标准 2/2 斜纹：4 种列向量、4 种行向量，约简数量就是 4x4
TWILL = {
    "name": "2/2 twill",
    "cyclic": True,
    "max_float": 2,
    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
    "tie_up": [[1, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 1], [1, 0, 0, 1]],
    "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
}


def test_loom_fit_endpoint():
    with TestClient(app) as client:
        # 设计不存在 -> 404
        resp = client.post(
            "/designs/99999/loom-fit",
            json={"max_shafts": 4, "max_treadles": 4},
        )
        check(resp.status_code == 404, "missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found", "404 detail message")

        rid = client.post("/designs", json=REDUNDANT).json()["id"]
        original_matrix = client.get(f"/designs/{rid}").json()["audit"]["interlacing"]

        # 参数校验：max_shafts / max_treadles 越界或缺字段 -> 422
        bad_bodies = [
            {"max_shafts": 0, "max_treadles": 4},
            {"max_shafts": 65, "max_treadles": 4},
            {"max_shafts": 4, "max_treadles": 0},
            {"max_shafts": 4, "max_treadles": 65},
            {"max_shafts": 4},
            {"max_treadles": 4},
            {},
        ]
        for body in bad_bodies:
            resp = client.post(f"/designs/{rid}/loom-fit", json=body)
            check(resp.status_code == 422, f"invalid body {body} rejected with 422")

        # 约简结果：4x4 框架本质平纹 -> 最少 2 综 2 踏
        resp = client.post(
            f"/designs/{rid}/loom-fit",
            json={"max_shafts": 4, "max_treadles": 4},
        )
        check(resp.status_code == 200, "loom-fit 200")
        data = resp.json()
        check(data["design_id"] == rid and data["warp_ends"] == 8
              and data["weft_picks"] == 8,
              "echoes design id and matrix dimensions")
        check(data["max_shafts"] == 4 and data["max_treadles"] == 4,
              "echoes device limits")
        check(data["fits"] is True, "2x2 minimum fits a 4x4 device")
        check(data["min_shafts"] == 2 and data["min_treadles"] == 2,
              "minimum counts are the distinct column/row vector counts")
        check(data["original_shafts"] == 4 and data["original_treadles"] == 4,
              "original counts come from the stored tie-up")
        check(data["shafts_reduction"] == 2
              and data["treadles_reduction"] == 2,
              "reductions are original minus minimum")

        draft = data["reduced_draft"]
        check(draft["threading"] == [0, 1, 0, 1, 0, 1, 0, 1],
              "reduced threading merges identical column vectors")
        check(draft["treadling"] == [0, 1, 0, 1, 0, 1, 0, 1],
              "reduced treadling merges identical row vectors")
        check(draft["tie_up"] == [[1, 0], [0, 1]],
              "reduced tie-up rebuilt from equivalence classes")
        check(draft["interlacing"] == original_matrix,
              "rebuilt matrix pointwise equals the original interlacing")

        # 上限边界：恰好容纳仍 fits
        exact = client.post(
            f"/designs/{rid}/loom-fit",
            json={"max_shafts": 2, "max_treadles": 2},
        ).json()
        check(exact["fits"] is True, "fits when limits equal the minimum counts")

        # 任一维度超设备上限即 fits=false（另一项不变）
        only_shaft = client.post(
            f"/designs/{rid}/loom-fit",
            json={"max_shafts": 1, "max_treadles": 4},
        ).json()
        check(only_shaft["fits"] is False
              and only_shaft["min_shafts"] == 2
              and only_shaft["min_treadles"] == 2,
              "does not fit when shafts alone exceed the limit")
        only_treadle = client.post(
            f"/designs/{rid}/loom-fit",
            json={"max_shafts": 4, "max_treadles": 1},
        ).json()
        check(only_treadle["fits"] is False,
              "does not fit when treadles alone exceed the limit")
        neither = client.post(
            f"/designs/{rid}/loom-fit",
            json={"max_shafts": 1, "max_treadles": 1},
        ).json()
        check(neither["fits"] is False,
              "does not fit when both exceed the limit")
        # fits=false 时仍返回完整约简草案
        check(neither["reduced_draft"]["tie_up"] == [[1, 0], [0, 1]]
              and neither["reduced_draft"]["interlacing"] == original_matrix,
              "draft and rebuilt matrix are returned even when it does not fit")

        # 2/2 斜纹：列/行向量互异，约简数量与原数量相同，减少量为 0
        tid = client.post("/designs", json=TWILL).json()["id"]
        twill_data = client.post(
            f"/designs/{tid}/loom-fit",
            json={"max_shafts": 4, "max_treadles": 4},
        ).json()
        check(twill_data["min_shafts"] == 4 and twill_data["min_treadles"] == 4,
              "twill minimum counts stay 4x4")
        check(twill_data["shafts_reduction"] == 0
              and twill_data["treadles_reduction"] == 0
              and twill_data["fits"] is True,
              "twill: zero reduction, still fits")
        check(twill_data["reduced_draft"]["tie_up"] == TWILL["tie_up"]
              and twill_data["reduced_draft"]["threading"] == TWILL["threading"]
              and twill_data["reduced_draft"]["treadling"] == TWILL["treadling"],
              "twill: reduced draft equals the original draft")
        twill_small = client.post(
            f"/designs/{tid}/loom-fit",
            json={"max_shafts": 3, "max_treadles": 4},
        ).json()
        check(twill_small["fits"] is False, "twill fails a 3-shaft device")

        # 约简草案本身是合法设计，且再次约简为幂等（数量不再下降）
        revalidated = weave.validate_design(
            draft["threading"], draft["tie_up"], draft["treadling"], 2
        )
        check(revalidated == (2, 2), "reduced draft is a valid 2x2 design")
        rebuilt_once_more = weave.reduce_to_loom(
            draft["threading"], draft["tie_up"], draft["treadling"], 2
        )
        check(rebuilt_once_more.min_shafts == 2
              and rebuilt_once_more.min_treadles == 2
              and rebuilt_once_more.tie_up == draft["tie_up"],
              "reduction is idempotent")

        # 接口只读：不改库，设计数量与原设计内容均不变
        ids_before = {d["id"] for d in client.get("/designs").json()}
        client.post(f"/designs/{rid}/loom-fit",
                    json={"max_shafts": 1, "max_treadles": 1})
        client.post(f"/designs/{tid}/loom-fit",
                    json={"max_shafts": 8, "max_treadles": 8})
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_before == ids_after, "loom-fit creates no new designs")
        again = client.get(f"/designs/{rid}").json()
        check(again["threading"] == REDUNDANT["threading"]
              and again["tie_up"] == REDUNDANT["tie_up"]
              and again["treadling"] == REDUNDANT["treadling"],
              "loom-fit does not modify the stored design")
        check(again["audit"]["interlacing"] == original_matrix,
              "loom-fit does not modify the stored audit")


if __name__ == "__main__":
    test_loom_fit_endpoint()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
