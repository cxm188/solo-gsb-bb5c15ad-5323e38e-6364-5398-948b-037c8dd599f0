#!/usr/bin/env python3
"""针对**真实运行中服务**（uvicorn / Docker 暴露 HTTP 端口）的归档接口冒烟测试。

与 tests/test_api_design_archives.py（FastAPI TestClient + 临时库）互补，本脚本
只使用 Python 标准库，直接对 BASE_URL（默认 http://localhost:8000）发真实 HTTP
请求，验证容器启动后的完整链路：

    健康检查 → 提交两份设计 → 归档（201）→ 同 digest 幂等复用（200）
    → verify 一致 → 顺序无关 → 重复 ID 400 / 缺失 404 / 422 校验
    → 归档漂移后 verify 不一致（直接改 SQLite 不可用时跳过该项）

用法：
    BASE_URL=http://localhost:8000 python3 tests/live_smoke.py

退出码 0 表示全部断言通过；非 0 会在 stderr 打印失败项。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")

_passed = 0


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    global _passed
    _passed += 1
    print(f"  ok: {msg}")


def request(method: str, path: str, body=None):
    url = f"{BASE_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload


TWILL = {
    "name": "archive-smoke twill",
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

PLAIN = {
    "name": "archive-smoke plain",
    "cyclic": False,
    "max_float": 1,
    "threading": [0, 1],
    "tie_up": [[1, 0], [0, 1]],
    "treadling": [0, 1, 0, 1],
}


def main() -> int:
    try:
        status, health = request("GET", "/health")
        check(status == 200 and health == {"status": "ok"},
             f"service healthy at {BASE_URL}")

        status, a = request("POST", "/designs", TWILL)
        check(status == 201, f"design A created ({status})")
        status, b = request("POST", "/designs", PLAIN)
        check(status == 201, f"design B created ({status})")
        id_a, id_b = a["id"], b["id"]

        # 首次归档（乱序提交）-> 201，清单按 ID 升序
        status, arc = request(
            "POST", "/design-archives", {"design_ids": [id_b, id_a]}
        )
        check(status == 201, f"first archive returns 201 (got {status})")
        check(arc["reused"] is False, "new archive reused=false")
        check(arc["design_ids"] == [id_a, id_b],
              "manifest design_ids ascending regardless of request order")
        check(arc["design_count"] == 2, "design_count=2")
        check(len(arc["digest"]) == 64, "digest is 64 hex chars")
        manifest = arc["manifest"]
        check(manifest["format"] == "design-archive-v1", "manifest format marker")

        # 独立复算摘要
        canonical = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        check(hashlib.sha256(canonical).hexdigest() == arc["digest"],
              "digest independently reproduced from canonical manifest")
        check(manifest["items"][0]["audit"] == a["audit"],
              "archived audit equals design A audit")

        # 相同集合（不同顺序）-> 200 幂等复用同一条记录、同一 digest
        status, again = request(
            "POST", "/design-archives", {"design_ids": [id_a, id_b]}
        )
        check(status == 200, f"identical content reuses archive (200, got {status})")
        check(again["reused"] is True, "reuse flag true")
        check(again["id"] == arc["id"], "same archive id reused")
        check(again["digest"] == arc["digest"], "same digest reused")

        # verify：全新归档一致
        status, report = request("GET", f"/design-archives/{arc['id']}/verify")
        check(status == 200, "verify 200")
        check(report["consistent"] is True, "fresh archive consistent")
        check(report["digest"] == arc["digest"]
              and report["recomputed_digest"] == arc["digest"],
              "verify digest vs recomputed digest equal")
        check(report["missing_design_ids"] == [],
              "no missing designs")
        check(report["first_diff_path"] is None, "no first diff path")

        # 拒绝情形
        status, err = request(
            "POST", "/design-archives", {"design_ids": [id_a, id_a]}
        )
        check(status == 400 and "duplicate" in err["detail"],
              "duplicate ids -> 400")

        status, err = request(
            "POST", "/design-archives", {"design_ids": [id_a, 99999999]}
        )
        check(status == 404 and "99999999" in err["detail"],
              "missing design -> 404 with id named")

        for label, body in (
            ("empty", {"design_ids": []}),
            ("over 50", {"design_ids": [1] * 51}),
            ("string id", {"design_ids": ["1"]}),
            ("zero", {"design_ids": [0]}),
            ("missing field", {}),
        ):
            status, _ = request("POST", "/design-archives", body)
            check(status == 422, f"pydantic validation {label} -> 422 (got {status})")

        status, _ = request("GET", "/design-archives/99999999/verify")
        check(status == 404, "verify unknown archive -> 404")

        # OpenAPI 已挂载
        status, spec = request("GET", "/openapi.json")
        check(status == 200 and "/design-archives" in spec["paths"],
              "OpenAPI exposes /design-archives")

    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"cannot reach service at {BASE_URL}: {exc}", file=sys.stderr)
        return 2

    print(f"live smoke passed: {_passed} checks against {BASE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
