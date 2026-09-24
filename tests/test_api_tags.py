"""POST /designs/{id}/tags 与 GET /designs/{id}/tags 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_tags.py
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


def test_get_tags_empty():
    with TestClient(app) as client:
        design_id = client.post("/designs", json=TWILL).json()["id"]
        resp = client.get(f"/designs/{design_id}/tags")
        check(resp.status_code == 200, "fresh design GET tags returns 200")
        data = resp.json()
        check(data["design_id"] == design_id, "GET echoes design_id")
        check(data["tags"] == [], "fresh design has no tags")
        check(data["count"] == 0, "fresh design tag count is 0")


def test_add_and_list_tags_ordered():
    with TestClient(app) as client:
        design_id = client.post("/designs", json=TWILL).json()["id"]

        first = ["斜纹", "经密高", "正装面料"]
        for i, tag in enumerate(first, start=1):
            resp = client.post(f"/designs/{design_id}/tags", json={"tag": tag})
            check(resp.status_code == 201, f"POST new tag {tag!r} returns 201")
            data = resp.json()
            check(data["count"] == i, f"POST response count grows to {i}")
            check([t["tag"] for t in data["tags"]] == first[:i],
                  "POST response lists all tags in first-added order")

        # GET 按首次添加顺序返回标签及数量
        resp = client.get(f"/designs/{design_id}/tags")
        check(resp.status_code == 200, "GET tags returns 200")
        data = resp.json()
        check(data["count"] == 3, "GET count is 3")
        tags = data["tags"]
        check([t["tag"] for t in tags] == first, "GET preserves first-added order")
        check(all("created_at" in t and isinstance(t["id"], int) for t in tags),
              "each tag carries id and created_at")
        check([t["id"] for t in tags] == sorted(t["id"] for t in tags),
              "tag ids ascending match first-added order")

        # 再追加一个，GET 与 POST 响应一致，旧标签顺序不动
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": "秋冬"})
        check(resp.status_code == 201, "POST another tag returns 201")
        check([t["tag"] for t in resp.json()["tags"]] == first + ["秋冬"],
              "new tag appended after existing ones")
        resp = client.get(f"/designs/{design_id}/tags")
        check([t["tag"] for t in resp.json()["tags"]] == first + ["秋冬"],
              "GET after append still first-added order")


def test_duplicate_tag_conflicts():
    with TestClient(app) as client:
        design_id = client.post("/designs", json=TWILL).json()["id"]
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": "平纹"})
        check(resp.status_code == 201, "first occurrence accepted")

        resp = client.post(f"/designs/{design_id}/tags", json={"tag": "平纹"})
        check(resp.status_code == 409, "duplicate tag on same design returns 409")
        check("平纹" in resp.json()["detail"], "409 detail names the tag")

        # 冲突不写库：数量与顺序不变
        data = client.get(f"/designs/{design_id}/tags").json()
        check(data["count"] == 1, "409 writes no extra row")
        check([t["tag"] for t in data["tags"]] == ["平纹"],
              "409 leaves tag list untouched")


def test_tags_scoped_per_design():
    with TestClient(app) as client:
        id_a = client.post("/designs", json=TWILL).json()["id"]
        id_b = client.post("/designs", json=TWILL).json()["id"]

        resp = client.post(f"/designs/{id_a}/tags", json={"tag": "素色"})
        check(resp.status_code == 201, "tag added to design A")
        # 同名标签用于另一设计：不冲突
        resp = client.post(f"/designs/{id_b}/tags", json={"tag": "素色"})
        check(resp.status_code == 201, "same tag text allowed on another design")

        tags_a = client.get(f"/designs/{id_a}/tags").json()
        tags_b = client.get(f"/designs/{id_b}/tags").json()
        check([t["tag"] for t in tags_a["tags"]] == ["素色"],
              "design A keeps its own tag")
        check([t["tag"] for t in tags_b["tags"]] == ["素色"],
              "design B keeps its own tag")
        check(tags_a["tags"][0]["id"] != tags_b["tags"][0]["id"],
              "tags are distinct rows per design")


def test_missing_design_returns_404():
    with TestClient(app) as client:
        resp = client.get("/designs/99999/tags")
        check(resp.status_code == 404, "GET tags of missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "GET 404 detail message")

        resp = client.post("/designs/99999/tags", json={"tag": "斜纹"})
        check(resp.status_code == 404, "POST tag to missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "POST 404 detail message")


def test_tag_validation():
    with TestClient(app) as client:
        design_id = client.post("/designs", json=TWILL).json()["id"]

        # 空标签 -> 400（业务校验在写入事务内）
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": ""})
        check(resp.status_code == 400, "empty tag returns 400")
        check("detail" in resp.json(), "400 carries detail")

        # 32 个字符的标签合法（中文字符按字符计数）
        tag32 = "标" * 32
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": tag32})
        check(resp.status_code == 201, f"32-char tag accepted: {resp.status_code}")

        # 33 个字符 -> 400
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": "标" * 33})
        check(resp.status_code == 400, "33-char tag returns 400")

        # 非法请求体：缺字段 / 类型不符 -> 422（框架层）
        resp = client.post(f"/designs/{design_id}/tags", json={})
        check(resp.status_code == 422, "missing tag field returns 422")
        resp = client.post(f"/designs/{design_id}/tags", json={"tag": 123})
        check(resp.status_code == 422, "non-string tag returns 422")

        # 所有失败都不写库：仅 32 字符那一条存在
        data = client.get(f"/designs/{design_id}/tags").json()
        check(data["count"] == 1, "rejected writes leave no tags behind")
        check(data["tags"][0]["tag"] == tag32, "only the valid tag persists")


def test_tags_do_not_change_design_or_audit():
    with TestClient(app) as client:
        created = client.post("/designs", json=TWILL).json()
        design_id = created["id"]

        client.post(f"/designs/{design_id}/tags", json={"tag": "斜纹"})
        client.post(f"/designs/{design_id}/tags", json={"tag": "经典组织"})
        # 故意制造一次 409
        client.post(f"/designs/{design_id}/tags", json={"tag": "斜纹"})

        stored = client.get(f"/designs/{design_id}").json()
        for field in ("name", "cyclic", "max_float", "shafts", "treadles",
                      "warp_ends", "weft_picks", "threading", "tie_up",
                      "treadling"):
            check(stored[field] == created[field],
                  f"tagging leaves design field {field} unchanged")
        check(stored["audit"] == created["audit"],
              "tagging leaves audit result unchanged")

        audit = client.get(f"/designs/{design_id}/audit").json()
        check(audit == created["audit"], "audit endpoint still returns original audit")


if __name__ == "__main__":
    test_get_tags_empty()
    test_add_and_list_tags_ordered()
    test_duplicate_tag_conflicts()
    test_tags_scoped_per_design()
    test_missing_design_returns_404()
    test_tag_validation()
    test_tags_do_not_change_design_or_audit()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all tags API tests passed")
