# 机织纹样浮长审计 API（Weave Float Audit API）

基于 **Python + FastAPI + SQLite** 的机织纹样浮长审计服务。提交经纱穿综映射、
踏板—综框绑综矩阵、逐纬踏综序列与最大浮长后，服务自动生成经纬交织矩阵，
找出所有超过上限的**经向 / 纬向连续浮长**；纹样声明循环时，自动合并矩阵
首尾跨边界的连续段。合法设计与审计结果入库，可按 ID 查询，也可通过
`GET /designs/page` 按设计 ID 升序做 **keyset 分页只读遍历**（`limit`
1～100、`after_id` 游标，多取一条判定后续页；遍历期间新增设计不会造成
已返回项重复）；维度不一致或索引越界的设计将被整份拒绝（HTTP 400，不入库）。此外可对已存设计做
**最小循环单元分析**：基于交织矩阵在经、纬两方向求能无缝平铺整幅的最小
正周期与循环单元；以及**阈值敏感性分析**：在 1～上限内逐档重跑完整浮长
审计，给出每档违规数与达到零违规的最小阈值。对超限设计，还可请求
**单步绑综修复建议**：逐一翻转一个绑综单元重跑审计，给出违规减少的候选；
以及**多步修复方案搜索**：在 1～3 次绑综翻转内搜索最终违规严格减少的方案
（中间状态允许违规数不下降）。还可对两份已存设计做**对齐比对**：枚举第二份
交织矩阵在经、纬方向的所有循环错位，给出不一致单元最少的最佳对齐、错位
坐标与按行/列汇总。还可对已存设计做**局部交织均衡审计**：按给定窗口逐窗
统计交织矩阵中 `1`（经纱在上）的占比，找出占比超出允许区间的超界窗口，
并汇总偏离最近阈值幅度最大的最严重窗口。还可对已存设计做**织机适配
（最少综框/踏板约简）**：基于交织矩阵把完整列向量相同的经纱并入同一约简
综框、完整行向量相同的纬纱并入同一约简踏板，重建穿综/绑综/踏综并逐点
校验与原矩阵一致，给出最少综框/踏板数、约简草案、相对原设计的减少量，
以及在给定设备综框/踏板上限下是否可织（`fits`）。还可对已存设计做
**风险区域聚合**：读取已存交织矩阵与原有浮长违规记录，把每条超限经/纬
浮长展开为矩阵单元，在经纬四邻接（循环设计首尾相接）规则下合并为连通
区域，按严重度降序给出每区的单元坐标、包围盒、面积、最大浮长与涉及方向。
此外服务支持 **WIF 1.1 互操作**：`GET /designs/{id}/wif` 把穿综、绑综、
踏综、循环声明与浮长上限按固定段落（`[WIF]`/`[CONTENTS]`/`[TEXTPLAN]`/
`[TIEUP]`/`[THREADING]`/`[TREADLING]`/`[WEAVING]`）与 1 基索引导出为
`text/plain`；`POST /designs/import-wif` 解析同一格式的文本，校验尺寸、
索引与 0/1 绑综值后复用同一审计流程入库——导出再导入保持交织矩阵与审计
结果完全一致。此外可通过 `POST /designs/{id}/tags` 为已入库设计维护
**去重的简体中文标签**，`GET /designs/{id}/tags` 按首次添加顺序查询
标签。还可通过 `POST /design-archives` 对 1～50 份已入库设计做**归档**：
按 ID 升序读取设计参数与审计结果，生成规范化 JSON 清单并计算 SHA-256
摘要持久化，相同摘要幂等复用；`GET /design-archives/{id}/verify` 重新
读取当前数据重算摘要，报告是否一致、缺失的设计项与首个差异路径。

## 快速启动

单条命令（构建镜像、安装依赖、启动服务、自动建表，一步到位）：

```bash
docker compose up --build
```

首次运行会自动构建镜像（基于 `python:3.12-slim`，仅安装
`requirements.txt` 中的 FastAPI/uvicorn），随后在前台启动服务；
`Ctrl+C` 停止。常用变体：

```bash
# 后台启动（守护态）
docker compose up --build -d

# 查看实时日志
docker compose logs -f

# 停止并移除容器（数据仍保留在 weave-data 卷中）
docker compose down

# 代码或依赖变更后强制重新构建再启动
docker compose up --build --force-recreate
```

启动后访问：

| 地址 | 说明 |
| --- | --- |
| `http://localhost:8000` | API 根地址 |
| `http://localhost:8000/docs` | Swagger 交互式文档 |
| `http://localhost:8000/health` | 健康检查 |

验证容器已就绪：

```bash
curl -s http://localhost:8000/health        # {"status":"ok"}
```

不使用 Compose 时，也可直接构建运行：

```bash
# 8000 端口直出；数据库写入容器内 /data（随容器删除而消失，需持久化请加 -v 挂卷）
docker build -t weave-audit .
docker run --rm -p 8000:8000 --name weave-audit \
  -v "$PWD/data:/data" \
  weave-audit

# 在已构建镜像内跑测试（详见文末「运行测试」）
docker run --rm -v "$PWD":/app -w /app weave-audit \
  sh -c 'python3 tests/test_weave.py && python3 tests/test_api_wif.py'
```

本地开发（无 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 服务监听端口（Compose 中映射为主机 `8000:8000`） |
| `DATABASE_PATH` | 容器内 `/data/weave.db`，本地 `data/weave.db` | SQLite 数据库文件路径；Compose 已挂载卷 `weave-data` 持久化 |

数据库表（`designs`、`audits`、`design_tags`、`design_archives`）在服务启动时
自动创建，无需手工迁移。

## 数据模型与约定

**请求体**（`POST /designs`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `threading` | `int[]` | 经纱穿综映射：`threading[w]` = 第 `w` 根经纱所穿综框的索引 |
| `tie_up` | `int[][]` | 绑综矩阵：`tie_up[t][s] ∈ {0,1}`，踏板 `t` 是否提升综框 `s`；行数 = 踏板数，列数 = 综框数 |
| `treadling` | `int[]` | 逐纬踏综序列：`treadling[h]` = 第 `h` 纬踩下的踏板索引 |
| `max_float` | `int ≥ 1` | 允许的最大浮长，**超过**即判违规 |
| `cyclic` | `bool`，默认 `false` | 纹样是否循环；为 `true` 时合并矩阵首尾跨边界的连续段 |
| `name` | `string`，可选 | 设计名称 |

**交织矩阵**：`interlacing[h][w] = tie_up[treadling[h]][threading[w]]`，
`1` 表示经纱在上，`0` 表示纬纱在上。

**浮长定义**：

- `warp`（经向浮长）：同一根经纱连续浮于纬纱之上，即矩阵某**列**中连续的 `1`，
  长度按纬纱数计；
- `weft`（纬向浮长）：同一根纬纱连续浮于经纱之上，即矩阵某**行**中连续的 `0`，
  长度按经纱数计。

**循环合并**（`cyclic: true`）：矩阵在经纬两个方向上首尾相接。若某列（行）
末尾的连续段与开头的连续段同向相连，则合并为一段：起点取**尾部段的起点**，
长度为两段之和（跨边界延伸）。整列（行）皆为同一浮向时只计一段，长度即
列高（行宽）。

**违规记录**字段：`direction`（`warp`/`weft`）、`yarn_index`（经纱列号或
纬纱行号）、`start`（连续段起点）、`length`（浮长）。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/designs` | 提交设计：校验 → 生成交织矩阵 → 浮长审计 → 入库，返回完整结果（201） |
| `GET` | `/designs` | 列出全部合法设计（摘要，含是否超限） |
| `GET` | `/designs/page` | 按设计 ID 升序 keyset 分页列出摘要（`limit` 1～100、`after_id` 游标，返回 `next_after_id`/`has_more`） |
| `GET` | `/designs/{id}` | 按 ID 查询设计及其审计结果 |
| `POST` | `/designs/{id}/tags` | 为设计追加去重标签（非空、≤32 字符，事务内校验）；成功 201，重复 409 |
| `GET` | `/designs/{id}/tags` | 按首次添加顺序返回设计的标签列表与数量 |
| `GET` | `/designs/{id}/audit` | 按设计 ID 单独查询审计结果 |
| `GET` | `/designs/{id}/repeat-period` | 最小循环单元分析：基于交织矩阵求经纬最小正周期与循环单元 |
| `GET` | `/designs/{id}/threshold-profile` | 阈值敏感性分析：1～上限逐档重跑完整浮长审计，给出每档违规数与最小零违规阈值 |
| `POST` | `/designs/{id}/tie-up-suggestions` | 单步绑综修复建议：逐一翻转一个绑综单元重跑审计，返回违规减少的候选 |
| `POST` | `/designs/{id}/repair-plans` | 多步绑综修复方案：1～3 次翻转内搜索最终违规严格减少的方案，支持 `max_changes`、`limit` |
| `POST` | `/designs/{id}/compare` | 设计对齐比对：枚举第二份矩阵的经纬循环错位，返回最佳偏移、错位坐标与汇总 |
| `POST` | `/designs/{id}/balance-hotspots` | 局部交织均衡审计：逐窗统计 `1` 的占比，返回超界窗口、偏离幅度与最严重窗口 |
| `POST` | `/designs/{id}/loom-fit` | 织机适配：按交织矩阵列/行向量等价类约简出最少综框/踏板草案，判断在给定设备上限下是否可织 |
| `GET` | `/designs/{id}/float-risk-zones` | 风险区域聚合：把已存违规展开为矩阵单元并按经纬相邻合并，按严重度降序返回区域列表与总数 |
| `GET` | `/designs/{id}/wif` | WIF 1.1 导出：固定段落、1 基索引的 `text/plain` 文本（含 threading/tie_up/treadling/cyclic/max_float） |
| `POST` | `/designs/import-wif` | WIF 1.1 导入：解析同格式 `[WIF]`/`[CONTENTS]`/`[WEAVING]` 等段落，校验后复用同一审计流程入库（201）；解析失败 400 且不写库 |
| `POST` | `/design-archives` | 设计归档：1～50 个已入库 ID 按升序生成规范化 JSON 清单 + SHA-256 摘要并持久化；相同摘要幂等复用（200/201） |
| `GET` | `/design-archives/{id}/verify` | 归档完整性校验：重算摘要并报告是否一致、缺失设计项与首个差异路径 |
| `GET` | `/health` | 健康检查 |

### 示例：提交设计

```bash
curl -s -X POST http://localhost:8000/designs \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "2/2 twill",
    "cyclic": true,
    "max_float": 2,
    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
    "tie_up": [[1,1,0,0],[0,1,1,0],[0,0,1,1],[1,0,0,1]],
    "treadling": [0, 1, 2, 3, 0, 1, 2, 3]
  }'
```

响应（201）：

```json
{
  "id": 1,
  "name": "2/2 twill",
  "cyclic": true,
  "max_float": 2,
  "shafts": 4,
  "treadles": 4,
  "warp_ends": 8,
  "weft_picks": 8,
  "threading": [0, 1, 2, 3, 0, 1, 2, 3],
  "tie_up": [[1,1,0,0],[0,1,1,0],[0,0,1,1],[1,0,0,1]],
  "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
  "created_at": "2026-09-21T00:00:00Z",
  "audit": {
    "exceeds": false,
    "interlacing": [[1,1,0,0,1,1,0,0], "..."],
    "violations": []
  }
}
```

超示例（跨边界合并后超限）：`threading=[0]`、`tie_up=[[1],[0]]`、
`treadling=[0,0,1,0,0]`、`max_float=2`、`cyclic=true` →

```json
{"direction": "warp", "yarn_index": 0, "start": 3, "length": 4}
```

### 查询

```bash
curl -s http://localhost:8000/designs/1        # 设计 + 审计结果
curl -s http://localhost:8000/designs/1/audit  # 仅审计结果
curl -s http://localhost:8000/designs          # 全部设计摘要
```

### 分页查询设计（designs/page）

`GET http://localhost:8000/designs/page`：以**设计 ID 升序 keyset（游标）
分页**的方式只读列出设计摘要，适合设计数量较多时逐页遍历。SQLite 查询为
`WHERE id > :after_id ORDER BY id LIMIT :limit+1`——**多取的 1 条只用于判定
`has_more`，不会放进本页结果**：

| 参数 | 范围 | 必填 | 说明 |
| --- | --- | --- | --- |
| `limit` | 1～100 的整数 | 是 | 本页返回条数上限 |
| `after_id` | 正整数 | 否 | 游标：仅返回 ID **严格大于**该值的设计；首页省略（从头开始），后续页传上一页响应里的 `next_after_id` |

响应字段：

| 字段 | 说明 |
| --- | --- |
| `items[]` | 本页设计摘要（字段同 `GET /designs`），按 ID 升序，至多 `limit` 条 |
| `next_after_id` | 下一页游标：`has_more=true` 时为**本页最后一条设计的 ID**；无后续页时为 `null` |
| `has_more` | 游标之后是否还有未返回的设计 |

游标使用**严格大于**比较且设计 ID 单调递增，因此**下一页从上一页最后返回
的 ID 之后继续；遍历期间新提交的设计只会出现在游标之后，已返回项不会重复**
（已删除的设计只会被跳过，同样不影响游标推进）。`limit` 缺失或不在
1～100、`after_id` 非正整数（如 `0`、负数、小数、带空白等）一律返回
**400**；`after_id` 超过当前最大 ID 不算错误，返回空页与 `has_more:false`。
`GET /designs` 的一次性全量列表行为保持不变。

```bash
# 首页（省略 after_id）：每页 2 条
curl -s 'http://localhost:8000/designs/page?limit=2'

# 下一页：把上一页的 next_after_id 作为 after_id 传回
curl -s 'http://localhost:8000/designs/page?limit=2&after_id=2'
```

响应（首页，库中已有 5 份设计）：

```json
{
  "items": [
    {"id": 1, "name": "2/2 twill", "cyclic": true, "max_float": 2,
     "shafts": 4, "treadles": 4, "warp_ends": 8, "weft_picks": 8,
     "exceeds": false, "created_at": "2026-09-23T00:00:00Z"},
    {"id": 2, "name": "plain", "cyclic": false, "max_float": 1,
     "shafts": 2, "treadles": 2, "warp_ends": 2, "weft_picks": 4,
     "exceeds": false, "created_at": "2026-09-23T00:00:01Z"}
  ],
  "next_after_id": 2,
  "has_more": true
}
```

翻到末页（只剩 1 条）：

```json
{
  "items": [
    {"id": 5, "name": "2/2 twill", "cyclic": true, "max_float": 2,
     "shafts": 4, "treadles": 4, "warp_ends": 8, "weft_picks": 8,
     "exceeds": false, "created_at": "2026-09-23T00:00:04Z"}
  ],
  "next_after_id": null,
  "has_more": false
}
```

非法参数：

```bash
curl -s 'http://localhost:8000/designs/page'                 # 缺少 limit → 400
curl -s 'http://localhost:8000/designs/page?limit=0'         # 越界 → 400
curl -s 'http://localhost:8000/designs/page?limit=101'       # 越界 → 400
curl -s 'http://localhost:8000/designs/page?limit=2&after_id=0'  # 非正整数 → 400
```

```json
{"detail": "query param limit must be within 1..100, got 0"}
```

### 设计标签

`POST http://localhost:8000/designs/{id}/tags`：为已入库设计追加一个去重的
简体中文标签。**设计存在性检查、标签校验、查重与写入在同一个 SQLite 事务内
完成**（`design_tags` 表上另有 `UNIQUE (design_id, tag)` 约束兜底）：

- 标签必须是非空字符串，长度不超过 **32 个字符**（中文字符按字符计），
  否则返回 **400** 且不写库（请求体缺字段或类型不符由框架返回 422）；
- 设计不存在返回 **404**；
- 同一设计下标签文本重复返回 **409**，已存标签与顺序均不变；
- 成功返回 **201** 与该设计的全部标签（按首次添加顺序）及数量。

`GET http://localhost:8000/designs/{id}/tags`：按**首次添加顺序**返回该设计
的标签列表与数量；设计不存在返回 404，尚无标签时返回 `tags: []`、
`count: 0`。标签为独立表，写入与查询均**不改变原设计与审计结果**；同名标签
可分别挂在不同设计上。

请求体：`{"tag": "斜纹"}`

```bash
curl -s -X POST http://localhost:8000/designs/1/tags \
  -H 'Content-Type: application/json' -d '{"tag": "斜纹"}'

curl -s -X POST http://localhost:8000/designs/1/tags \
  -H 'Content-Type: application/json' -d '{"tag": "秋冬"}'

curl -s http://localhost:8000/designs/1/tags
```

响应（POST 与 GET 同构，201 / 200）：

```json
{
  "design_id": 1,
  "tags": [
    {"id": 1, "tag": "斜纹", "created_at": "2026-09-22T00:00:00Z"},
    {"id": 2, "tag": "秋冬", "created_at": "2026-09-22T00:00:01Z"}
  ],
  "count": 2
}
```

重复与非法标签：

```json
{"detail": "tag '斜纹' already exists for design 1"}
```

```json
{"detail": "tag too long: 33 chars, max is 32"}
```

### 最小循环单元分析

`GET http://localhost:8000/designs/{id}/repeat-period`：读取已入库设计并重新
生成**交织矩阵**，在经、纬两个方向分别寻找能无缝平铺整幅矩阵的**最小正周期**，
返回循环单元。设计不存在返回 404。接口只读计算，不修改原设计。

周期定义与规则：

- `warp_period`：经向（列方向）最小正周期，即一个循环单元的**列数/经纱数**，
  必须整除整幅经纱数；
- `weft_period`：纬向（行方向）最小正周期，即一个循环单元的**行数/纬纱数**，
  必须整除整幅纬纱数；
- 周期判断**完全基于交织矩阵逐点比较**（取模回绕：`matrix[h][w]` 必须等于
  `unit[h % weft_period][w % warp_period]`），**不分别检查** threading /
  treadling。因此穿综、踏综序列本身没有小周期、但交织结果恰好平铺的设计也能
  被识别；
- 取模回绕比较天然覆盖矩阵首尾边界，**声明循环（cyclic）与非循环设计**的周期
  分析结果一致；
- 某方向不存在小于整幅的周期（例如该尺寸为素数）时，该方向返回**完整尺寸**，
  `unit` 即整幅矩阵；
- `strict_2d_repeat` 仅在两个方向周期**都严格小于**整幅对应尺寸（单元在经、纬
  两方向都至少平铺 2 次）时为 `true`。

返回字段：

| 字段 | 说明 |
| --- | --- |
| `warp_period` | 经向最小正周期（循环单元列数），整除经纱数 |
| `weft_period` | 纬向最小正周期（循环单元行数），整除纬纱数 |
| `unit` | 最小循环单元矩阵（`weft_period` 行 × `warp_period` 列），重复平铺可无缝还原整幅交织矩阵 |
| `strict_2d_repeat` | 是否为严格二维重复（两方向周期都小于整幅） |

```bash
curl -s http://localhost:8000/designs/1/repeat-period
```

响应（200）：

```json
{
  "warp_period": 4,
  "weft_period": 4,
  "unit": [
    [1, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 1],
    [1, 0, 0, 1]
  ],
  "strict_2d_repeat": true
}
```

无更小周期时（3×3 素数尺寸示例）：

```json
{
  "warp_period": 3,
  "weft_period": 3,
  "unit": [[1, 0, 1], [0, 1, 1], [1, 1, 0]],
  "strict_2d_repeat": false
}
```

### 阈值敏感性分析

`GET http://localhost:8000/designs/{id}/threshold-profile`：读取已入库设计，
在 `1..max_float_limit` 内**逐档按原 `cyclic` 语义重跑完整经纬浮长审计**
（与提交时相同的判定规则，交织矩阵只生成一次），返回每档阈值下的经向、
纬向与总违规数，以及该设计**达到零违规的最小阈值**。接口只读计算，
**不修改数据库**；设计不存在返回 404。

查询参数：

| 参数 | 范围 | 默认 | 说明 |
| --- | --- | --- | --- |
| `max_float_limit` | 1～64 | 原设计的 `max_float`（若超过 64 则按 64 封顶） | 逐档审计的阈值上限 |

返回字段：

| 字段 | 说明 |
| --- | --- |
| `design_id` | 被分析的设计 ID |
| `cyclic` | 逐档审计沿用的设计循环声明 |
| `max_float` | 设计入库时声明的最大浮长阈值 |
| `max_float_limit` | 本次逐档审计的阈值上限 |
| `min_zero_violation_threshold` | 达到零违规的最小阈值；上限内始终有违规时为 `null` |
| `profile[]` | 逐档审计结果，按阈值 `1..max_float_limit` 升序 |

`profile[]` 每档包含：`max_float`（本档阈值）、`warp_violations`（经向违规数）、
`weft_violations`（纬向违规数）、`total_violations`（违规总数）。违规数随阈值
单调不增，因此首个零违规档即最小零违规阈值。

```bash
# 缺省上限 = 原设计的 max_float
curl -s http://localhost:8000/designs/1/threshold-profile

# 显式指定上限 4
curl -s 'http://localhost:8000/designs/1/threshold-profile?max_float_limit=4'
```

响应（200，循环设计存在跨边界合并后长度 4 的经向浮长时）：

```json
{
  "design_id": 1,
  "cyclic": true,
  "max_float": 2,
  "max_float_limit": 4,
  "min_zero_violation_threshold": 4,
  "profile": [
    {"max_float": 1, "warp_violations": 1, "weft_violations": 0, "total_violations": 1},
    {"max_float": 2, "warp_violations": 1, "weft_violations": 0, "total_violations": 1},
    {"max_float": 3, "warp_violations": 1, "weft_violations": 0, "total_violations": 1},
    {"max_float": 4, "warp_violations": 0, "weft_violations": 0, "total_violations": 0}
  ]
}
```

上限内达不到零违规时 `min_zero_violation_threshold` 为 `null`：

```json
{"design_id": 1, "cyclic": true, "max_float": 2, "max_float_limit": 2,
 "min_zero_violation_threshold": null,
 "profile": [
   {"max_float": 1, "warp_violations": 1, "weft_violations": 0, "total_violations": 1},
   {"max_float": 2, "warp_violations": 1, "weft_violations": 0, "total_violations": 1}
 ]}
```

### 单步绑综修复建议

`POST http://localhost:8000/designs/{id}/tie-up-suggestions`：读取已入库设计，
逐一翻转 `tie_up` 的一个 0/1 单元，并按原 `cyclic`、`max_float` 重跑完整审计；
仅返回**违规总数严格减少**的候选。接口只读计算，**不修改原设计**；设计不存在
返回 404，无改善时返回空列表 `[]`。

查询参数：`limit`（可选，1～50，默认 50）——返回候选数上限。

每个候选包含：

| 字段 | 说明 |
| --- | --- |
| `treadle` | 踏板索引（`tie_up` 行号） |
| `shaft` | 综框索引（`tie_up` 列号） |
| `new_value` | 该单元翻转后的新值（0 或 1） |
| `remaining_violations` | 翻转后完整审计的剩余违规总数 |
| `affected_points` | 受影响交织点数（翻转前后交织矩阵中取值变化的单元数） |

结果按 `remaining_violations` → `affected_points` → `treadle` → `shaft`
升序稳定排序。

```bash
curl -s -X POST 'http://localhost:8000/designs/1/tie-up-suggestions?limit=5'
```

响应（200）：

```json
[
  {"treadle": 0, "shaft": 1, "new_value": 0, "remaining_violations": 2, "affected_points": 6},
  {"treadle": 1, "shaft": 0, "new_value": 1, "remaining_violations": 3, "affected_points": 1}
]
```

### 多步绑综修复方案

`POST http://localhost:8000/designs/{id}/repair-plans`：读取已入库设计，
在最多 `max_changes`（1～3，默认 **2**）次 `tie_up` 0/1 单元翻转内广度优先
搜索修复方案。**中间状态允许违规数不下降**（容许先变差或持平），但方案终态
的违规总数必须**严格少于原设计**。搜索按绑综状态（整个 `tie_up` 的 0/1 位图）
去重，同一单元不会在一条翻转序列内重复翻转；等价终态（相同绑综状态）只保留
排序最优的翻转序列。接口只读计算，**不修改原设计**；设计不存在返回 404，
没有改善方案时返回空列表 `[]`。

实现上按「每次翻转只更新受影响经列与纬行的浮长计数」做增量审计（行/列
位图 + 位运算段计数），逐层枚举、状态去重、收录、排序与平局规则不变；
仅对最终入选的前 `limit` 条方案重建绑综并完整重算交织矩阵与违规列表，
返回结果与逐状态完整重算**逐项一致**——8×8 绑综、16×16 交织、
`max_changes=3&limit=5` 在 8 秒内响应。

查询参数：

| 参数 | 范围 | 默认 | 说明 |
| --- | --- | --- | --- |
| `max_changes` | 1～3 | `2` | 最多翻转绑综单元的次数 |
| `limit` | 1～50 | `50` | 返回方案数上限 |

方案排序键（依次升序，稳定排序）：最终违规数 → 改动数（翻转次数）→
最终交织矩阵相对原矩阵的**变化点数** → 翻转序列字典序（按
`(treadle, shaft)` 依次比较）。

每个方案包含：

| 字段 | 说明 |
| --- | --- |
| `changes` | 翻转次数（1～`max_changes`） |
| `final_violations` | 最终违规总数（严格小于原设计） |
| `changed_points` | 最终交织矩阵相对原矩阵取值变化的点数 |
| `steps[]` | 逐步翻转：`step`（从 1 起）、`treadle`、`shaft`、`old_value`、`new_value`、`violations_after`（该步翻转后的违规总数，允许不下降） |
| `violations[]` | 最终违规列表（字段同审计结果） |
| `interlacing[][]` | 最终交织矩阵（1=经纱在上，0=纬纱在上） |

```bash
# 默认最多翻转 2 次，最多返回 50 个方案
curl -s -X POST http://localhost:8000/designs/2/repair-plans

# 最多 3 次翻转，只取前 5 个方案
curl -s -X POST 'http://localhost:8000/designs/2/repair-plans?max_changes=3&limit=5'
```

响应（200）：

```json
[
  {
    "changes": 2,
    "final_violations": 0,
    "changed_points": 2,
    "steps": [
      {"step": 1, "treadle": 0, "shaft": 0, "old_value": 1, "new_value": 0, "violations_after": 1},
      {"step": 2, "treadle": 3, "shaft": 0, "old_value": 1, "new_value": 0, "violations_after": 0}
    ],
    "violations": [],
    "interlacing": [[0], [1], [1], [0], [1], [1]]
  }
]
```

### 设计对齐比对

`POST http://localhost:8000/designs/{id}/compare`：读取两份已入库设计并重新
生成**交织矩阵**（尺寸必须一致：同经同纬，否则 400），枚举第二份矩阵在
经、纬两个方向的**所有循环错位**，返回与第一份矩阵**不一致单元最少**的
对齐结果；平局按行偏移（`weft_offset`）再列偏移（`warp_offset`）升序取先者。
接口只读计算，**不改库**；任一设计不存在返回 404。

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `other_design_id` | `int ≥ 1` | 参与比对的另一份已入库设计 ID（允许与路径 ID 相同，即自我比对） |

对齐约定：`aligned[h][w] = 第二份矩阵[(h + weft_offset) % 行数][(w + warp_offset) % 列数]`，
即第二份矩阵沿纬向循环上移 `weft_offset` 行、沿经向循环左移 `warp_offset`
列后，与第一份矩阵逐点比较。

返回字段：

| 字段 | 说明 |
| --- | --- |
| `design_id` / `other_design_id` | 两份设计的 ID |
| `warp_ends` / `weft_picks` | 两份设计共同的经纱数 / 纬纱数 |
| `weft_offset` / `warp_offset` | 最佳循环错位量（行偏移 / 列偏移） |
| `mismatch_count` | 最佳对齐下不一致单元数 |
| `mismatch_ratio` | 不一致单元占比：`mismatch_count / (weft_picks × warp_ends)` |
| `mismatches[]` | 错位坐标列表（按行、列升序）：`row`、`col`、`design_value`、`other_value` |
| `row_mismatches[]` | 按行汇总：`row_mismatches[h]` 为第 `h` 纬的错位数 |
| `col_mismatches[]` | 按列汇总：`col_mismatches[w]` 为第 `w` 根经纱的错位数 |
| `aligned[][]` | 最佳对齐后的第二份交织矩阵（1=经纱在上，0=纬纱在上） |

```bash
curl -s -X POST http://localhost:8000/designs/1/compare \
  -H 'Content-Type: application/json' \
  -d '{"other_design_id": 2}'
```

响应（200）：

```json
{
  "design_id": 1,
  "other_design_id": 2,
  "warp_ends": 8,
  "weft_picks": 8,
  "weft_offset": 0,
  "warp_offset": 1,
  "mismatch_count": 0,
  "mismatch_ratio": 0.0,
  "mismatches": [],
  "row_mismatches": [0, 0, 0, 0, 0, 0, 0, 0],
  "col_mismatches": [0, 0, 0, 0, 0, 0, 0, 0],
  "aligned": [[1, 1, 0, 0, 1, 1, 0, 0], "..."]
}
```

尺寸不一致时返回 400：

```json
{"detail": "interlacing size mismatch: 8x8 vs 4x4 (weft_picks x warp_ends)"}
```

### 局部交织均衡审计

`POST http://localhost:8000/designs/{id}/balance-hotspots`：读取已入库设计并
重新生成**交织矩阵**，按给定窗口逐窗统计矩阵中 `1`（经纱在上）的占比，
找出占比超出允许区间 `[warp_ratio_min, warp_ratio_max]`（闭区间，含边界）
的**超界窗口**。接口只读计算，**不改库**；设计不存在返回 404。

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `window_rows` | `int`，1～64 | 窗口行数（纬向高度） |
| `window_cols` | `int`，1～64 | 窗口列数（经向宽度） |
| `warp_ratio_min` | `float`，0～1 | 窗口内 `1` 占比的允许下限（含） |
| `warp_ratio_max` | `float`，0～1 | 窗口内 `1` 占比的允许上限（含），不得小于 `warp_ratio_min` |

窗口扫描规则：

- **非循环设计**：只扫描完整落入矩阵的窗口（左上角限制在矩阵内）；窗口
  大于矩阵对应尺寸时没有可扫描窗口（`windows_scanned` 为 0）；
- **循环设计**：以每个原矩阵单元为左上角，窗口在经、纬两轴**取模回绕**；
  窗口尺寸不得超过矩阵对应尺寸，否则返回 400。

返回字段：

| 字段 | 说明 |
| --- | --- |
| `design_id` / `cyclic` | 被审计的设计 ID 与沿用的循环声明 |
| `window_rows` / `window_cols` / `warp_ratio_min` / `warp_ratio_max` | 回显本次审计的窗口与阈值 |
| `windows_scanned` | 实际扫描的窗口总数 |
| `hotspots[]` | 超界窗口列表，按左上角坐标（行、列）升序稳定排序：`row`、`col`、`warp_ratio`（窗口内 `1` 的占比）、`deviation`（占比偏离最近阈值的幅度） |
| `worst_hotspot` | 偏离最近阈值幅度最大的超界窗口（平局取扫描序最先者）；无超界时为 `null` |

```bash
curl -s -X POST http://localhost:8000/designs/1/balance-hotspots \
  -H 'Content-Type: application/json' \
  -d '{"window_rows": 2, "window_cols": 2,
       "warp_ratio_min": 0.25, "warp_ratio_max": 0.75}'
```

响应（200，4×4 分块矩阵的非循环扫描示例）：

```json
{
  "design_id": 1,
  "cyclic": false,
  "window_rows": 2,
  "window_cols": 2,
  "warp_ratio_min": 0.25,
  "warp_ratio_max": 0.75,
  "windows_scanned": 9,
  "hotspots": [
    {"row": 0, "col": 0, "warp_ratio": 1.0, "deviation": 0.25},
    {"row": 0, "col": 2, "warp_ratio": 0.0, "deviation": 0.25},
    {"row": 2, "col": 0, "warp_ratio": 0.0, "deviation": 0.25},
    {"row": 2, "col": 2, "warp_ratio": 1.0, "deviation": 0.25}
  ],
  "worst_hotspot": {"row": 0, "col": 0, "warp_ratio": 1.0, "deviation": 0.25}
}
```

循环设计的窗口超过矩阵尺寸时返回 400：

```json
{"detail": "window 5x2 exceeds interlacing size 4x4 (window_rows x window_cols vs weft_picks x warp_ends) for cyclic design"}
```

### 织机适配（最少综框 / 踏板约简）

`POST http://localhost:8000/designs/{id}/loom-fit`：读取已入库设计并重新
生成**交织矩阵**，完全基于矩阵本身（而非原综框/踏板编号）做约简：

- **完整列向量相同的经纱**（在所有纬上的浮沉逐点一致）归入同一个**约简
  综框**；
- **完整行向量相同的纬纱**归入同一个**约简踏板**；
- 等价类分别按经纱、纬纱**首次出现**的顺序从 `0` 编号；
- 据等价类代表点重建 `tie_up`（约简踏板数 × 约简综框数）、`threading`、
  `treadling`，并**逐点校验**由约简草案重建的交织矩阵与原矩阵完全一致；
- 不同完整列（行）向量的数量即织出同一矩阵所需综框（踏板）数的下界——
  穿同一综框（踩同一踏板）的经（纬）纱列（行）向量必然逐点相同，因此
  本约简同时达到下界，数量**最少**（未被穿经使用的原综框、未被踩踏的
  原踏板也自然消失）。

仅当 `min_shafts ≤ max_shafts` **且** `min_treadles ≤ max_treadles` 时
`fits` 为 `true`；任一项超限均为 `false`（此时仍返回完整约简草案）。
接口只读计算，**不改库**；设计不存在返回 404。

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_shafts` | `int`，1～64 | 设备可用综框数上限 |
| `max_treadles` | `int`，1～64 | 设备可用踏板数上限 |

返回字段：

| 字段 | 说明 |
| --- | --- |
| `design_id` / `warp_ends` / `weft_picks` | 设计 ID 与交织矩阵尺寸（经/纬纱数约简前后不变） |
| `max_shafts` / `max_treadles` | 回显本次设备上限 |
| `fits` | 是否适配：两项最少数量均不超过对应上限时为 `true` |
| `min_shafts` / `min_treadles` | 最少综框 / 踏板数（不同完整列 / 行向量数） |
| `original_shafts` / `original_treadles` | 原设计综框 / 踏板数（原 `tie_up` 的列 / 行数） |
| `shafts_reduction` / `treadles_reduction` | 相对原设计减少的综框 / 踏板数（原数量 − 最少数量） |
| `reduced_draft.threading` / `treadling` | 约简穿综 / 踏综序列（长度同原经 / 纬纱数，值为等价类编号） |
| `reduced_draft.tie_up` | 约简绑综矩阵（`min_treadles` 行 × `min_shafts` 列） |
| `reduced_draft.interlacing` | 由约简草案逐点重建的交织矩阵（与原矩阵完全一致） |

```bash
curl -s -X POST http://localhost:8000/designs/1/loom-fit \
  -H 'Content-Type: application/json' \
  -d '{"max_shafts": 2, "max_treadles": 2}'
```

响应（200，原设计用 4 综 4 踏织平纹：绑综列 0/2、1/3 相同，行 0/2、1/3
相同，约简后只需 2 综 2 踏）：

```json
{
  "design_id": 1,
  "warp_ends": 8,
  "weft_picks": 8,
  "max_shafts": 2,
  "max_treadles": 2,
  "fits": true,
  "min_shafts": 2,
  "min_treadles": 2,
  "original_shafts": 4,
  "original_treadles": 4,
  "shafts_reduction": 2,
  "treadles_reduction": 2,
  "reduced_draft": {
    "threading": [0, 1, 0, 1, 0, 1, 0, 1],
    "tie_up": [[1, 0], [0, 1]],
    "treadling": [0, 1, 0, 1, 0, 1, 0, 1],
    "interlacing": [[1, 0, 1, 0, 1, 0, 1, 0], "..."]
  }
}
```

任一上限不足时 `fits` 为 `false`，例如 4×4 的 2/2 斜纹（4 种列/行向量，
无法约简）面对 3 综设备：`{"max_shafts": 3, "max_treadles": 4}` →
`"fits": false, "min_shafts": 4, "min_treadles": 4, "shafts_reduction": 0,
"treadles_reduction": 0`。

### 风险区域聚合（float-risk-zones）

`GET http://localhost:8000/designs/{id}/float-risk-zones`：直接读取**已入库**
的交织矩阵与**原有浮长违规记录**（不重新审计、不改阈值），把每条超限
浮长展开为其覆盖的矩阵单元，再按经纬相邻合并为连通风险区域。接口只读，
**不改库**；设计不存在返回 404，无违规时返回空列表 `zones: []`、
`zone_count: 0`。

展开与合并规则：

- **展开**：`warp` 违规（列 `yarn_index` 上起点 `start`、长度 `length` 的
  连续 `1`）展开为单元 `((start + k) % 行数, yarn_index)`；`weft` 违规展开为
  `(yarn_index, (start + k) % 列数)`，`k = 0..length-1`。循环合并段的
  `start` 是尾部段起点、长度为跨边界两段之和，统一取模即可覆盖首尾两侧
  的单元；同一单元被多条违规覆盖时去重，涉及方向取并集、浮长取最大值。
- **相邻**：四邻接（上、下、左、右）；仅对角接触（共享顶点）不合并。
  **非循环设计**只在矩形矩阵范围内相邻；**循环设计**（`cyclic: true`）在
  经纬两轴首尾相接（环面拓扑），末行与首行、末列与首列的单元也互为邻居。
- **严重度排序**（降序）：区域内最大浮长 `max_float` 大者优先；其次面积
  （单元数）大者；再次取区域内最小单元坐标（先行后列）升序作为确定性平局。

返回字段：

| 字段 | 说明 |
| --- | --- |
| `design_id` / `cyclic` | 设计 ID 与聚合沿用的循环声明 |
| `warp_ends` / `weft_picks` | 交织矩阵列数（经纱数）/ 行数（纬纱数） |
| `zone_count` | 风险区域总数 |
| `zones[]` | 风险区域列表，按严重度降序 |
| `zones[].cells[]` | 区域内单元坐标（`row`、`col`），按行、列升序 |
| `zones[].bbox` | 轴对齐包围盒：`min_row`、`min_col`、`max_row`、`max_col` 及 `rows`、`cols`（原矩阵坐标；跨首尾边界的循环区域其包围盒可能横跨整条边，精确单元以 `cells` 为准） |
| `zones[].area` | 面积 = 区域单元数 |
| `zones[].max_float` | 区域内单元涉及的最大浮长 |
| `zones[].directions` | 涉及方向（去重，`warp` 在 `weft` 前）：仅经向、仅纬向或两者兼有 |

```bash
curl -s http://localhost:8000/designs/1/float-risk-zones
```

响应（200，3×3、`max_float=1` 的矩阵 `110/110/000`：两条长度 2 的经向
浮长与一条长度 3 的纬向浮长在边缘相邻，合并为一个混合方向区域）：

```json
{
  "design_id": 1,
  "cyclic": false,
  "warp_ends": 3,
  "weft_picks": 3,
  "zone_count": 1,
  "zones": [
    {
      "cells": [
        {"row": 0, "col": 0}, {"row": 0, "col": 1},
        {"row": 1, "col": 0}, {"row": 1, "col": 1},
        {"row": 2, "col": 0}, {"row": 2, "col": 1}, {"row": 2, "col": 2}
      ],
      "bbox": {"min_row": 0, "min_col": 0, "max_row": 2, "max_col": 2,
               "rows": 3, "cols": 3},
      "area": 7,
      "max_float": 3,
      "directions": ["warp", "weft"]
    }
  ]
}
```

循环设计跨首尾边界合并（5×1 矩阵列值 `1,1,0,0,1`、`max_float=2`）：
违规为 `{"direction": "warp", "yarn_index": 0, "start": 4, "length": 3}`，
展开为 `(4,0)(0,0)(1,0)`，环面相邻合并为一个区域：

```json
{
  "design_id": 2,
  "cyclic": true,
  "warp_ends": 1,
  "weft_picks": 5,
  "zone_count": 1,
  "zones": [{
    "cells": [{"row": 0, "col": 0}, {"row": 1, "col": 0}, {"row": 4, "col": 0}],
    "bbox": {"min_row": 0, "min_col": 0, "max_row": 4, "max_col": 0,
             "rows": 5, "cols": 1},
    "area": 3,
    "max_float": 3,
    "directions": ["warp"]
  }]
}
```

无违规或设计不存在：

```json
{"design_id": 3, "cyclic": false, "warp_ends": 4, "weft_picks": 4,
 "zone_count": 0, "zones": []}
```

```json
{"detail": "design 99999 not found"}
```

### WIF 1.1 互操作（导出 / 导入）

服务实现一个固定段落、**1 基索引**的 WIF 1.1 纯文本子集（`text/plain;
charset=utf-8`）。导出与导入使用同一格式，因此任意已入库设计导出后再导入，
都会得到**完全相同的设计名、交织矩阵与审计结果**（`name` 逐字符一致；
`exceeds`、`interlacing`、`violations` 逐项一致），仅 `id`/`created_at`
等入库元数据不同。

段落与键固定如下（顺序、空行与拼写均为固定形式；编号全部 1 基，
对应 JSON API 内部的 0 基表示 ±1）：

```ini
[WIF]
Version=1.1
Name=2/2 twill

[CONTENTS]
TEXTPLAN=yes
TIEUP=yes
THREADING=yes
TREADLING=yes
WEAVING=yes

[TEXTPLAN]
WARP THREADS=8
WEFT THREADS=8

[TIEUP]
1=1,1,0,0
2=0,1,1,0
3=0,0,1,1
4=1,0,0,1

[THREADING]
1=1
2=2
3=3
4=4
5=1
6=2
7=3
8=4

[TREADLING]
1=1
2=2
3=3
4=4
5=1
6=2
7=3
8=4

[WEAVING]
SHAFTS=4
TREADLES=4
CYCLIC=yes
MAX_FLOAT=2
```

格式约定：

| 段落 / 键 | 说明 |
| --- | --- |
| `[WIF] Version` | 必须为 `1.1`；`Name` 可缺省或为空（导入为空名称 `null`） |
| `[WIF] Name` | 普通名称按无标记单行文本导出 / 导入（旧规则：导入时去掉首尾空白，空值为 `null`）。名称若含 CR/LF（含 NEL、U+2028/U+2029 等全部行分隔符）、反斜杠或首尾空白，则导出为可逆的单行编码 `Name=\B64:<CRC32><urlsafe-base64(UTF-8 名称)>`（`<CRC32>` 为名称 UTF-8 字节的 8 位小写十六进制 CRC32，其余为 URL 安全 Base64），导入时校验并原样还原，含此类字符的名称可无损往返；校验和不符、负载损坏或标记滥用一律 400 且不写库 |
| `[CONTENTS]` | 必须逐一声明 `TEXTPLAN/TIEUP/THREADING/TREADLING/WEAVING=yes`，且对应段落都存在；额外声明本子集不使用的段落（如 `WARP`/`WEFT`）会被忽略 |
| `[TEXTPLAN] WARP/WEFT THREADS` | 经纱数 / 纬纱数（正整数），必须分别与 `[THREADING]`/`[TREADLING]` 条目数一致 |
| `[TIEUP]` | 键为踏板号 `1..TREADLES`（每行恰好出现一次）；值为逗号分隔、每行 `SHAFTS` 个严格的 `0`/`1` |
| `[THREADING]` | 键为经纱号 `1..WARP THREADS`（连续不重不漏）；值为所穿综框号 `1..SHAFTS` |
| `[TREADLING]` | 键为纬纱号 `1..WEFT THREADS`；值为所踩踏板号 `1..TREADLES` |
| `[WEAVING] SHAFTS/TREADLES` | 综框数 / 踏板数（正整数），必须与绑综矩阵列数 / 行数一致 |
| `[WEAVING] CYCLIC` | 仅接受 `yes`/`no`（大小写不敏感），分别对应 JSON 的 `true`/`false` |
| `[WEAVING] MAX_FLOAT` | 允许的最大浮长，正整数（≥ 1），语义同 JSON API |

`GET /designs/{id}/wif`：导出已入库设计，`200 text/plain`；设计不存在
返回 404。`POST /designs/import-wif`：请求体直接放 WIF 文本
（`Content-Type: text/plain`，未设置该头也按纯文本解析；非合法 UTF-8
同样 400），导入成功走与 `POST /designs` 完全相同的校验、交织矩阵生成、
浮长审计与同事务入库流程，返回 `DesignOut`（201）。

```bash
# 导出
curl -s http://localhost:8000/designs/1/wif

# 导出到文件后原样导回
curl -s http://localhost:8000/designs/1/wif > /tmp/design.wif
curl -s -X POST http://localhost:8000/designs/import-wif \
  -H 'Content-Type: text/plain' \
  --data-binary @/tmp/design.wif
```

解析失败（空文档、版本不为 1.1、必需段落 / 声明缺失或不一致、重复段落或
键、尺寸不符、索引越界、误用 0 基编号、绑综值非 0/1、`CYCLIC`/`MAX_FLOAT`
非法等）一律返回 **400 且不写库**：

```json
{"detail": "warp end 3 references shaft 5 out of range: valid index is [1, 4]"}
```

### 设计归档（design-archives）

`POST http://localhost:8000/design-archives`：接收 1～50 个**已入库**设计 ID，
**按 ID 升序去重**后在同一读取流程中取出每份设计的参数与审计结果，生成
**规范化 JSON 清单**（`design-archive-v1`），计算其 **SHA-256** 摘要并持久化
到独立的 `design_archives` 表。

- 请求体：`{"design_ids": [3, 1, 2]}`，元素为严格正整数（`"1"`、`1.0`、
  `0`、负数等由框架返回 **422**），数量必须为 1～50（空列表或超过 50 同样
  **422**）；
- 列表含**重复 ID** 返回 **400**；存在**未入库 ID** 返回 **404**（`detail`
  按升序列出全部缺失 ID）。校验与全部读取成功后才执行唯一一次写入，
  **归档失败不产生半成品**（不写任何归档记录）；
- 摘要只依赖清单内容（不含归档/设计自身的 `id`、`created_at` 等入库元数据），
  因此**同一批设计无论提交顺序、何时归档都得到同一摘要**：命中相同 digest 时
  **幂等复用**同一条归档记录，返回 **200** 与 `reused: true`（不新增行、
  保留首次 `created_at`）；首次归档返回 **201**、`reused: false`；
  `digest` 列另有 `UNIQUE` 约束兜底并发请求。

规范化（canonical）序列化规则：UTF-8、对象键按字典序
（`sort_keys`）、紧凑分隔符（`separators=(",", ":")`）、不转义非 ASCII
（`ensure_ascii=False`）；摘要即规范化字节流的 SHA-256 十六进制（64 位）。

清单结构（即响应中的 `manifest`，入库的是它的规范化文本）：

```json
{
  "format": "design-archive-v1",
  "design_ids": [1, 2, 3],
  "design_count": 3,
  "items": [
    {
      "design_id": 1,
      "parameters": {
        "name": "2/2 twill", "cyclic": true, "max_float": 2,
        "shafts": 4, "treadles": 4, "warp_ends": 8, "weft_picks": 8,
        "threading": [0, 1, 2, 3, 0, 1, 2, 3],
        "tie_up": [[1, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 1], [1, 0, 0, 1]],
        "treadling": [0, 1, 2, 3, 0, 1, 2, 3]
      },
      "audit": {
        "exceeds": false,
        "interlacing": [[1, 1, 0, 0, 1, 1, 0, 0], "..."],
        "violations": []
      }
    }
  ]
}
```

```bash
# 乱序提交无妨：清单按 ID 升序；首次 201
curl -s -X POST http://localhost:8000/design-archives \
  -H 'Content-Type: application/json' \
  -d '{"design_ids": [3, 1, 2]}'
```

响应（首次 201，再次提交同集合为 200 且 `reused: true`）：

```json
{
  "id": 1,
  "digest": "5737989b7c548f35…（64 位十六进制）",
  "design_ids": [1, 2, 3],
  "design_count": 3,
  "manifest": { "format": "design-archive-v1", "...": "..." },
  "reused": false,
  "created_at": "2026-09-23T00:00:00Z"
}
```

拒绝情形（均不写库）：

```json
{"detail": "design_ids must not contain duplicates"}
```

```json
{"detail": "designs not found: 99999"}
```

`GET http://localhost:8000/design-archives/{id}/verify`：**归档完整性校验**。
按归档时记录的设计 ID 列表重新读取**当前**的设计参数与审计结果（只重算清单，
不改库），重建规范化清单并重算 SHA-256，返回：

| 字段 | 说明 |
| --- | --- |
| `archive_id` | 被校验的归档 ID |
| `digest` | 归档时持久化的 SHA-256 摘要 |
| `recomputed_digest` | 按当前数据重算的 SHA-256 摘要 |
| `consistent` | 重算摘要与归档摘要一致**且无缺失设计项**时为 `true` |
| `missing_design_ids` | 归档后已缺失（被删除或不存在）的设计 ID，升序；无缺失为 `[]` |
| `first_diff_path` | 归档清单与当前内容的**首个**差异路径（[JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) 风格，根为空串）；完全一致时为 `null` |

差异定位顺序与规范化一致：对象按键名字典序、数组按下标升序；数组长度不同且
公共前缀全等时指向第一个缺失下标（如设计被删除时为 `/design_count`），叶子
值不同时一路深入（如 `/items/0/parameters/max_float`、
`/items/0/audit/exceeds`）。归档不存在返回 404。

```bash
curl -s http://localhost:8000/design-archives/1/verify
```

响应（200，归档完好 / 设计漂移两例）：

```json
{
  "archive_id": 1,
  "digest": "5737989b…",
  "recomputed_digest": "5737989b…",
  "consistent": true,
  "missing_design_ids": [],
  "first_diff_path": null
}
```

```json
{
  "archive_id": 1,
  "digest": "5737989b…",
  "recomputed_digest": "9a10c4f2…",
  "consistent": false,
  "missing_design_ids": [],
  "first_diff_path": "/items/0/parameters/max_float"
}
```

### 拒绝情形（HTTP 400，整份设计不入库）

- 绑综矩阵非矩形（各行长度不一致）或含非 0/1 值；
- 穿综索引越界：`threading[i] ∉ [0, 综框数)`；
- 踏综索引越界：`treadling[i] ∉ [0, 踏板数)`；
- 空穿综 / 空踏综 / 空绑综矩阵、`max_float < 1`；
- WIF 导入时：版本不为 1.1、必需段落缺失 / 声明不一致、尺寸不符、
  1 基索引越界（含误用 0 基 `0`）、绑综单元非 0/1、`CYCLIC` 非
  `yes`/`no`、请求体非合法 UTF-8、`Name` 的 `\B64:` 编码标记损坏
  （校验和不符 / Base64 或 UTF-8 非法 / 标记被用于无需编码的名称）等。

```json
{"detail": "threading[3]=9 out of range: valid shaft index is [0, 4)"}
```

## 运行测试

本地（需先 `pip install -r requirements.txt`）：

```bash
python3 tests/test_weave.py                  # 核心逻辑单元测试（无外部依赖）
python3 tests/test_api_designs_page.py       # designs/page keyset 分页接口集成测试
python3 tests/test_api_repeat_period.py      # repeat-period 接口集成测试
python3 tests/test_api_threshold_profile.py  # threshold-profile 接口集成测试
python3 tests/test_api_repair_plans.py       # repair-plans 接口集成测试（含 8 秒耗时回归）
python3 tests/test_api_compare.py            # compare 接口集成测试
python3 tests/test_api_balance_hotspots.py   # balance-hotspots 接口集成测试
python3 tests/test_api_loom_fit.py           # loom-fit 接口集成测试
python3 tests/test_api_float_risk_zones.py   # float-risk-zones 接口集成测试
python3 tests/test_api_tags.py               # tags 新增/查询接口集成测试
python3 tests/test_api_design_archives.py     # design-archives 归档/校验接口集成测试
python3 tests/test_api_wif.py                # WIF 导出/导入接口集成测试
```

接口测试使用临时 SQLite 文件与 FastAPI TestClient，不需要启动服务，也不会
触碰默认的 `data/weave.db`。也可通过 `pytest tests/` 一次性收集执行。

容器内执行（复用已构建的镜像，挂载源码后运行）：

```bash
docker build -t weave-audit .
docker run --rm -v "$PWD":/app -w /app weave-audit \
  sh -c 'python3 tests/test_weave.py && python3 tests/test_api_design_archives.py'
```

### Docker 启动下的接口冒烟测试

`tests/live_smoke.py` 是针对**真实运行中服务**（经 uvicorn / Docker 暴露 HTTP
端口）的端到端冒烟脚本，仅使用 Python 标准库（镜像内可直接运行）。先启动
服务，再执行：

```bash
# 终端 1：构建并启动（后台）
docker compose up --build -d

# 终端 2：对 http://localhost:8000 跑归档全流程冒烟（健康检查 → 提交两份设计 →
# 归档 201 → 同 digest 幂等复用 200 → verify 一致 → 重复 ID 400 / 缺失 404 /
# 422 校验 → verify 404）
BASE_URL=http://localhost:8000 python3 tests/live_smoke.py

# 或在容器镜像内运行（host.docker.internal 指向宿主机映射端口）
docker run --rm -e BASE_URL=http://host.docker.internal:8000 \
  -v "$PWD/tests:/tests:ro" weave-audit python3 /tests/live_smoke.py
```

脚本退出码 0 表示全部断言通过；它创建的设计与归档落在运行服务的数据库卷中。

## 项目结构

```
app/
  main.py      # FastAPI 路由与启动钩子（启动时建表）
  weave.py     # 交织矩阵生成与浮长审计核心逻辑
  wif.py       # WIF 1.1 固定段落纯文本的导出序列化与导入解析
  archives.py  # 归档清单规范化序列化、SHA-256 摘要与首个差异路径定位
  db.py        # SQLite 持久层
  models.py    # Pydantic 请求/响应模型
tests/
  test_weave.py                   # 核心逻辑单元测试：python3 tests/test_weave.py
  test_api_designs_page.py        # designs/page keyset 分页接口集成测试（TestClient，临时库）
  test_api_repeat_period.py       # repeat-period 接口集成测试（TestClient，临时库）
  test_api_threshold_profile.py   # threshold-profile 接口集成测试（TestClient，临时库）
  test_api_repair_plans.py        # repair-plans 接口集成测试（TestClient，临时库，含 8 秒耗时回归）
  test_api_compare.py             # compare 接口集成测试（TestClient，临时库）
  test_api_balance_hotspots.py    # balance-hotspots 接口集成测试（TestClient，临时库）
  test_api_loom_fit.py            # loom-fit 接口集成测试（TestClient，临时库）
  test_api_float_risk_zones.py    # float-risk-zones 接口集成测试（TestClient，临时库）
  test_api_tags.py                # tags 新增/查询接口集成测试（TestClient，临时库）
  test_api_design_archives.py     # design-archives 归档/校验接口集成测试（TestClient，临时库）
  test_api_wif.py                 # WIF 导出/导入接口集成测试（TestClient，临时库）
  live_smoke.py                   # Docker/uvicorn 真实启动下的 HTTP 端到端冒烟（标准库）
Dockerfile
docker-compose.yml
requirements.txt
```
