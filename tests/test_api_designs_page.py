"""GET /designs/page 只读分页接口的集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_designs_page.py
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

# 与 TWILL 交织矩阵不同的平纹（2 经 4 纬，无超限）
PLAIN = {
    "name": "plain",
    "cyclic": False,
    "max_float": 1,
    "threading": [0, 1],
    "tie_up": [[1, 0], [0, 1]],
    "treadling": [0, 1, 0, 1],
}

SUMMARY_FIELDS = {
    "id", "name", "cyclic", "max_float", "shafts", "treadles",
    "warp_ends", "weft_picks", "exceeds", "created_at",
}


def _create_n(client, payload, n):
    """连续创建 n 份设计（背靠背提交，ID 连续），返回其 ID 升序列表。"""
    ids = [client.post("/designs", json=payload).json()["id"] for _ in range(n)]
    return sorted(ids)


def test_empty_page_beyond_last_id():
    with TestClient(app) as client:
        last_id = client.post("/designs", json=TWILL).json()["id"]
        resp = client.get("/designs/page", params={"limit": 10,
                                                   "after_id": last_id})
        check(resp.status_code == 200, "page after last id returns 200")
        data = resp.json()
        check(data["items"] == [], "no items after the last id")
        check(data["has_more"] is False, "has_more false on empty tail page")
        check(data["next_after_id"] is None, "next_after_id null when no more")


def test_keyset_pagination_walks_all_pages():
    with TestClient(app) as client:
        ids = _create_n(client, TWILL, 5)  # 背靠背创建，ID 连续
        start_after = ids[0] - 1

        # 第一页：limit=2
        resp = client.get("/designs/page",
                          params={"limit": 2, "after_id": start_after})
        check(resp.status_code == 200, "first page returns 200")
        page1 = resp.json()
        check([d["id"] for d in page1["items"]] == ids[0:2],
              "first page returns first two ids ascending")
        check(page1["has_more"] is True, "first page has_more true")
        check(page1["next_after_id"] == ids[1],
              "next_after_id is last returned id")
        check(set(page1["items"][0].keys()) == SUMMARY_FIELDS,
              "items carry lightweight summary fields only")
        check(page1["items"][0]["exceeds"] is False,
              "summary exceeds is a bool matching /designs")

        # 第二页：用上一页 next_after_id 继续
        resp = client.get("/designs/page",
                          params={"limit": 2, "after_id": page1["next_after_id"]})
        page2 = resp.json()
        check(resp.status_code == 200, "second page returns 200")
        check([d["id"] for d in page2["items"]] == ids[2:4],
              "second page continues strictly after previous cursor")
        check(page2["has_more"] is True, "second page has_more true")
        check(page2["next_after_id"] == ids[3],
              "second page cursor advances")

        # 末页：只剩 1 条
        resp = client.get("/designs/page",
                          params={"limit": 2, "after_id": page2["next_after_id"]})
        page3 = resp.json()
        check([d["id"] for d in page3["items"]] == ids[4:],
              "last page returns the remaining item")
        check(page3["has_more"] is False, "last page has_more false")
        check(page3["next_after_id"] is None,
              "next_after_id null on the last page")

        seen = [d["id"] for p in (page1, page2, page3) for d in p["items"]]
        check(seen == ids, "full walk returns exactly the created ids in order")
        check(len(seen) == len(set(seen)), "full walk has no duplicate ids")


def test_first_page_without_after_id():
    with TestClient(app) as client:
        # after_id 省略时从头开始；只断言本批创建项能完整翻到
        ids = _create_n(client, PLAIN, 3)
        seen = []
        cursor = None
        while True:
            params = {"limit": 1}
            if cursor is not None:
                params["after_id"] = cursor
            data = client.get("/designs/page", params=params).json()
            seen.extend(d["id"] for d in data["items"])
            if not data["has_more"]:
                check(data["next_after_id"] is None,
                      "terminal page without after_id has null cursor")
                break
            cursor = data["next_after_id"]
            check(cursor is not None, "has_more page always gives a cursor")
        # 全库从头遍历应包含全部已入库设计且无重复
        all_ids = [d["id"] for d in client.get("/designs").json()]
        check(seen == all_ids, "walk from start equals GET /designs order")
        check(all(d in seen for d in ids), "newly created ids all reachable")


def test_designs_inserted_during_walk_never_duplicate():
    with TestClient(app) as client:
        ids = _create_n(client, PLAIN, 3)
        start_after = ids[0] - 1

        # 先取第一页（游标停在 ids[1]）
        page1 = client.get("/designs/page",
                           params={"limit": 2, "after_id": start_after}).json()
        check([d["id"] for d in page1["items"]] == ids[0:2],
              "first page before concurrent insert")

        # 遍历期间新增一份设计（ID 单调递增，落在当前游标之后）
        new_id = client.post("/designs", json=TWILL).json()["id"]
        check(new_id > page1["next_after_id"],
              "newly inserted id is beyond the current cursor")

        # 用上一页游标继续：已返回的 ids[0]/ids[1] 不重复，新设计出现在后续页
        page2 = client.get(
            "/designs/page",
            params={"limit": 2, "after_id": page1["next_after_id"]},
        ).json()
        check([d["id"] for d in page2["items"]] == [ids[2], new_id],
              "continuation starts after cursor and picks up the new design")
        check(page2["has_more"] is False, "exactly one full second page")
        check(page2["next_after_id"] is None, "no further page after the insert")

        walked = [d["id"] for d in page1["items"]] + [
            d["id"] for d in page2["items"]
        ]
        check(len(walked) == len(set(walked)),
              "inserts during a walk never cause returned items to repeat")


def test_limit_boundaries():
    with TestClient(app) as client:
        ids = _create_n(client, TWILL, 3)
        start_after = ids[0] - 1

        data = client.get("/designs/page",
                          params={"limit": 1, "after_id": start_after}).json()
        check([d["id"] for d in data["items"]] == ids[:1],
              "limit=1 returns a single item")
        check(data["has_more"] is True, "limit=1 page has more")

        # limit=100 为允许上限：3 条一次取完（只多取 1 条判 has_more）
        data = client.get("/designs/page",
                          params={"limit": 100, "after_id": start_after}).json()
        check([d["id"] for d in data["items"]] == ids,
              "limit=100 returns up to 100 items")
        check(data["has_more"] is False, "limit=100 swallows the small batch")


def test_invalid_params_return_400():
    with TestClient(app) as client:
        def expect_400(params, msg):
            resp = client.get("/designs/page", params=params)
            check(resp.status_code == 400, f"{msg}: status {resp.status_code}")
            check("detail" in resp.json(), f"{msg}: carries detail")

        # limit 缺失
        expect_400({}, "missing limit")
        # limit 越界
        expect_400({"limit": 0}, "limit=0")
        expect_400({"limit": 101}, "limit=101")
        expect_400({"limit": -1}, "negative limit")
        # limit 非整数（字符串形态由本接口严格解析，不走框架 422）
        expect_400({"limit": "abc"}, "non-numeric limit")
        expect_400({"limit": "1.5"}, "float limit")
        expect_400({"limit": " 2"}, "leading-space limit")
        expect_400({"limit": "+2"}, "signed limit")
        expect_400({"limit": ""}, "empty limit")
        # after_id 必须是正整数
        expect_400({"limit": 2, "after_id": 0}, "after_id=0")
        expect_400({"limit": 2, "after_id": -1}, "negative after_id")
        expect_400({"limit": 2, "after_id": "abc"}, "non-numeric after_id")
        expect_400({"limit": 2, "after_id": "1.0"}, "float after_id")
        expect_400({"limit": 2, "after_id": " 3"}, "space-padded after_id")


def test_original_list_designs_unchanged():
    with TestClient(app) as client:
        # /designs 仍返回全部摘要的扁平列表（无分页信封）
        resp = client.get("/designs")
        check(resp.status_code == 200, "GET /designs still 200")
        rows = resp.json()
        check(isinstance(rows, list), "/designs still returns a bare list")
        check([d["id"] for d in rows] == sorted(d["id"] for d in rows),
              "/designs still ordered by id ascending")
        check(all(set(d.keys()) == SUMMARY_FIELDS for d in rows),
              "/designs items keep the original summary shape")


if __name__ == "__main__":
    test_empty_page_beyond_last_id()
    test_keyset_pagination_walks_all_pages()
    test_first_page_without_after_id()
    test_designs_inserted_during_walk_never_duplicate()
    test_limit_boundaries()
    test_invalid_params_return_400()
    test_original_list_designs_unchanged()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all designs page API tests passed")
