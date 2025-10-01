# rw-sync — 本地网页 → Readwise Reader 同步器（最终交付版）

> **阶段**：S4 汇总交付｜**[进度 100%｜细化度 100%]**
> **目标**：从本地目录监控/扫描新增 HTML 文件，解析并通过 Readwise Reader API 入库；首次可全量推送、命令式推送新增，支持全局去重、失败重试与滚动 CSV 失败清单；只支持 HTML；标题抓取以**网页在线 `<title>` 为主**，再回退 Reader 解析，再回退本地。
> **优先级权重**（默认）：UX 0.35｜代码整洁 0.30｜部署便捷 0.20｜安全 0.10｜成本 0.05

---

## 目录

- [rw-sync — 本地网页 → Readwise Reader 同步器（最终交付版）](#rw-sync--本地网页--readwise-reader-同步器最终交付版)
  - [目录](#目录)
  - [1. 需求与验收](#1-需求与验收)
  - [2. 架构与数据流](#2-架构与数据流)
  - [3. 关键接口（Readwise Reader）](#3-关键接口readwise-reader)
  - [4. 配置与优先级](#4-配置与优先级)
  - [5. 目录结构（src/ 布局）](#5-目录结构src-布局)
  - [6. 数据模型（SQLite + WAL）](#6-数据模型sqlite--wal)
  - [7. URL 提取与规范化](#7-url-提取与规范化)
  - [8. 标题抓取与回退策略](#8-标题抓取与回退策略)
  - [9. 并发与限流](#9-并发与限流)
  - [10. CLI 设计与用法](#10-cli-设计与用法)
  - [11. 失败重试与观测](#11-失败重试与观测)
  - [12. 重要实现片段（示例代码）](#12-重要实现片段示例代码)
    - [12.1 Typer 入口与配置优先级](#121-typer-入口与配置优先级)
    - [12.2 SQLite \& WAL 初始化](#122-sqlite--wal-初始化)
    - [12.3 URL 解析与规范化（含特例）](#123-url-解析与规范化含特例)
    - [12.4 标题抓取（3s 预算）](#124-标题抓取3s-预算)
    - [12.5 Readwise 客户端与限流](#125-readwise-客户端与限流)
    - [12.6 失败 CSV（细粒度文件锁）](#126-失败-csv细粒度文件锁)
  - [13. 测试计划](#13-测试计划)
  - [14. 部署与运维](#14-部署与运维)
  - [15. 安全与合规](#15-安全与合规)
  - [16. 决策记录（ADR 摘要）](#16-决策记录adr-摘要)
  - [17. 全量问答清单（Q1–Q28 摘要）](#17-全量问答清单q1q28-摘要)
  - [18. Mermaid 流程图](#18-mermaid-流程图)
  - [19. 总结与取舍](#19-总结与取舍)
    - [附：参考文档（核心负载语句的出处）](#附参考文档核心负载语句的出处)

---

## 1. 需求与验收

**功能需求**

1. 监控/扫描项目根配置中指定的目录（仅 HTML / 网页离线文件）。
2. 新增文件：解析 URL（**canonical > SingleFile 注释 > 自造 URL**），抽取 HTML，上传至 Reader：

   * `POST /api/v3/save/`，包含 `url`、可选 `html`，启用 `should_clean_html=true` 让 Reader 清洗正文与元信息。新增 201；已存在 200（幂等）。([Readwise][1])
3. **标题策略**：优先在线抓取 `<title>`（3s 预算），失败回退 Reader 解析，再回退本地。`<title>` 定义见 MDN。([MDN Web Docs][2])
4. **更新元数据**（不改正文）：若文档已存在，支持 `PATCH /api/v3/update/<id>/` 更新 `title/tags/location/...`。接口不支持 `html` 字段更新。([Readwise][1])
5. **首批全量发送**（遵守限流），以及 **命令式“仅新增”发送**。
6. **全局去重**：以“规范化 URL”为唯一键；重复文件跳过或仅做元数据 PATCH。
7. **失败闭环**：本地重试（含 429 `Retry-After` 退避）；将失败 URL + 文件名按**自然日（本地时区）**滚动写入 `data/failures/YYYYMMDD/failed_urls.csv`，并使用文件锁避免并发写竞态。([Readwise][1])

**非功能 & 约束**

* 仅支持 HTML；不处理 PDF/EPUB。
* 速率限制：Reader 默认基线 20 rpm，但 **CREATE/UPDATE 为 50 rpm 上限**；尊重 `Retry-After`。([Readwise][1])
* 采用 **httpx.AsyncClient + aiolimiter** 控并发 + 令牌桶。([HTTPX][3])
* 状态存储 SQLite（WAL），默认 `./data/state/rw_sync.db`，可由配置文件覆盖。([SQLite][4])

**验收标准**

* 首次 `push --all` 能在限流下把目录内所有 HTML 入 Reader，失败项出现在 CSV；再次运行不重复入库。
* `push --new` 仅处理新增文件；默认按“上次成功运行到本次启动之间”的时间窗口增量扫描（优先使用创建时间，回退为修改时间）；幂等。可用 `--since` 覆盖起始时间。
* 任一失败项具备 **URL + 文件名** 且落在当日 CSV；日志可追溯退避与重试信息。
* `auth check` 能对 `GET /api/v2/auth/` 返回 204 做校验。([Readwise][1])

---

## 2. 架构与数据流

1. **文件发现**（一次性 CLI）：扫描配置目录，过滤 `.html`/`.htm`。
2. **URL 解析**：优先按“文件名内嵌 URL 规范”提取（`… [URL] <百分号编码后的网址>.html`，含安全清洗与冲突时间戳）；其次 `link[rel=canonical]` > SingleFile 注释（如 `<!-- saved from url=... -->`）> 可能的 `og:url`。`rel=canonical` 的用法参考 Google 文档。([Google for Developers][5])
3. **URL 规范化**：剔除跟踪参数（如 `utm_*`、`fbclid`、`gclid`），保留内容定位参数（白名单）。`utm_*` 参数见 GA 文档。([Google Help][6])
4. **去重查询**：SQLite `documents(norm_url UNIQUE)`。
5. **标题抓取**：httpx GET（3s），BS4 解析 `<title>`；失败回退。([HTTPX][7])
6. **入库**：`POST /save`（`should_clean_html=true`），并根据返回判断 201/200。([Readwise][1])
7. **可选更新**：如在线标题与 Reader 解析不同，则 `PATCH /update/<id>/` 修正 `title`。([Readwise][1])
8. **失败处理**：网络/429 → 重试并尊重 `Retry-After`；最终失败写 CSV（文件锁）。([Readwise][1])

---

## 3. 关键接口（Readwise Reader）

* **认证**：`Authorization: Token <READWISE_TOKEN>`；校验：`GET /api/v2/auth/` 期望 204。([Readwise][1])
* **创建**：`POST /api/v3/save/`；关键字段：`url`（必需）、`html`（可选）、`should_clean_html`（建议启用）、`title`/`tags`/`category` 等（可选）。已存在返回 200。([Readwise][1])
* **更新**：`PATCH /api/v3/update/<document_id>/` 可更新 `title/author/summary/location/category/tags` 等，不含 `html`。([Readwise][1])
* **限流**：基线 20 rpm；CREATE/UPDATE 各 50 rpm；遵守 429 的 `Retry-After`。([Readwise][1])

---

## 4. 配置与优先级

**配置文件**：YAML（`.rw-sync.yaml`），解析使用 **PyYAML `safe_load`**。`yaml.load` 不安全已被官方弃用，使用 `safe_load`。([PyYAML][8])

**优先级**：`CLI --config` > 环境变量 `RW_SYNC_CONFIG` > CWD 下 `.rw-sync.yaml` > 默认。
**项目根**：若提供 `--config`，以该文件所在目录为根；否则以 `Path.cwd()`。
**敏感信息**：`READWISE_TOKEN` 通过 `.env`（开发）或环境变量（生产）；加载用 **python-dotenv**。([PyPI][9])

**`.rw-sync.example.yaml`（示例）**

```yaml
watch:
  dir: "./inbox"         # 仅 HTML
  patterns: ["*.html", "*.htm"]

state:
  db_path: "./data/state/rw_sync.db"  # 可覆盖；未配置则回落该默认

network:
  title_fetch_timeout: 3.0
  concurrency: 6
  rpm_save: 50
  rpm_update: 50

readwise:
  should_clean_html: true
  default_category: "article"

url_norm:
  keep_params: ["id", "p", "page", "s", "v", "t", "q"]  # 内容定位白名单
  drop_params: ["utm_", "gclid", "fbclid", "mkt_tok", "mc_cid", "mc_eid", "yclid", "spm", "ref"]
```

**`.env.example`**

```
READWISE_TOKEN=xxxxx
```

---

## 5. 目录结构（src/ 布局）

```
rw-sync/
├─ pyproject.toml / requirements.txt
├─ .env.example
├─ .rw-sync.example.yaml
├─ data/
│  ├─ failures/YYYYMMDD/failed_urls.csv
│  └─ state/rw_sync.db
├─ src/rwsync/
│  ├─ __init__.py
│  ├─ cli.py                  # Typer 入口
│  ├─ config.py               # YAML + .env 读取与优先级
│  ├─ db.py                   # SQLite/WAL & DAO
│  ├─ urlnorm.py              # URL 解析/规范化/去重键
│  ├─ extract.py              # HTML 解析、canonical/SingleFile/OG提取
│  ├─ title.py                # 在线 <title> 抓取（httpx + BS4）
│  ├─ readwise.py             # /auth /save /update 客户端
│  ├─ syncer.py               # push --all/--new 核心流程
│  ├─ failures.py             # CSV 滚动 & filelock
│  └─ logging_conf.py
└─ tests/...
```

* **Typer** 用于 CLI（基于类型提示，支持回调与上下文对象）。([Typer][10])
* Typer 基于 **Click**，可通过 `typer.Context` 在全局回调读取配置与依赖注入。([Typer][11])

---

## 6. 数据模型（SQLite + WAL）

* 启用 WAL：`PRAGMA journal_mode=WAL;`（持久化，跨连接保持生效；WAL 文件与 DB 同目录）。([SQLite][4])

**表：documents**（全局去重与同步状态）

| 字段                    | 类型          | 说明                     |
| --------------------- | ----------- | ---------------------- |
| id                    | INTEGER PK  | 本地自增                   |
| norm_url              | TEXT UNIQUE | 规范化 URL（去跟踪参数）         |
| source_url            | TEXT        | 原始 URL（未规范化）           |
| title                 | TEXT        | 最终标题                   |
| title_source          | TEXT        | `web`/`reader`/`local` |
| file_path             | TEXT        | 本地文件绝对/相对路径            |
| file_mtime            | REAL        | 发现时 mtime              |
| readwise_id           | TEXT        | Reader 返回的文档 id        |
| last_status           | INTEGER     | 最近 HTTP 状态             |
| last_error            | TEXT        | 最近错误（可空）               |
| created_at/updated_at | TEXT        | ISO 时间                 |

**唯一约束**：`UNIQUE(norm_url)` —— 全局去重。

---

## 7. URL 提取与规范化

**提取优先级**：

1. `<link rel="canonical" href="...">`（最可靠）；([Google for Developers][5])
2. SingleFile 注释 / 元（`<!-- saved from url=... -->`、`meta[property=og:url]` 等）；
3. **兜底**：从文件名推断或自造：`https://local/doc/<sha1>`。

**规范化策略**（白名单保留）

* 移除跟踪参数（如 `utm_*`、`gclid`、`fbclid` 等）；保留 `id/p/page/s/v/t/q` 等**内容定位参数**。`utm_*` 的定义与用途见 GA 文档。([Google Help][6])
* 统一大小写（scheme/host 小写）、删除片段 `#...`、排序查询参数。
* 特例：对文件名形如 `twitter.com_<user>_status_<id>_...html` 的离线快照，识别并还原为 `https://x.com/<user>/status/<id>`（或 `https://twitter.com/...`）——保证与 canonical 幂等。

---

## 8. 标题抓取与回退策略

* **首选**：在线请求目标 URL，3 秒预算，解析 `<title>` 文本。([HTTPX][7])
* **回退**：Reader `should_clean_html=true` 自动解析的标题（不保证与网页完全一致）；再回退本地 `<title>`。`should_clean_html` 参数官方说明见 CREATE 字段表。([Readwise][1])
* `<title>` 标准语义见 MDN（页面 `<head>` 内定义文档标题）。([MDN Web Docs][2])

---

## 9. 并发与限流

* HTTP 客户端采用 **httpx.AsyncClient**（异步、连接池、可设 timeouts/limits）。([HTTPX][3])
* 令牌桶：**aiolimiter** 控制 `50 req/min`（save 与 update 各一只 limiter）。([Aiolimiter][12])
* 连接池：`httpx.Limits(max_connections=20, max_keepalive_connections=10)`；超时：`httpx.Timeout(connect=5, read=10, write=10, pool=5)`。官方推荐可调资源上限与超时。([HTTPX][13])
* 429 退避：读取 `Retry-After` 头后重排任务；上限重试次数并记录失败。Reader 文档明确支持该头。([Readwise][1])

---

## 10. CLI 设计与用法

**框架**：Typer（回调统一加载配置与依赖，通过 `typer.Context` 传递）。([Typer][14])

**命令一览**

```
rw-sync auth check                 # 检查 token（204 通过）
rw-sync push --all [--max N]       # 首次全量推送（限流）
rw-sync push --new [--since DT]    # 仅新增（默认基于 DB 去重）
rw-sync replay [--date YYYY-MM-DD] # 重放当日 CSV 失败清单
```

**全局选项**

```
--config PATH   # 首选；> RW_SYNC_CONFIG > ./.rw-sync.yaml > 默认
--dry-run       # 仅打印计划（不触发 API）
--verbose / -v
```

---

## 11. 失败重试与观测

* **重试**：网络错误/超时/429；指数退避 + `Retry-After`。([Readwise][1])
* **失败清单**：`data/failures/YYYYMMDD/failed_urls.csv`（本地时区，自然日滚动），字段：`url,file_name`；写入采用 **filelock** 防并发。([py-filelock.readthedocs.io][15])
* **日志**：INFO 概要、DEBUG 细节、ERROR 失败；统计：成功/已存在/更新/失败计数。

---

## 12. 重要实现片段（示例代码）

> **说明**：以下为关键骨架，真实项目在 `src/rwsync/` 下拆分为模块与单元测试。

### 12.1 Typer 入口与配置优先级

```python
# src/rwsync/cli.py
import typer, os, sys
from pathlib import Path
from typer import Option
from .config import load_settings, Settings
from .syncer import push_all, push_new, replay_failures
from .readwise import auth_check

app = typer.Typer(help="Local HTML → Readwise Reader sync CLI")

@app.callback()
def main(
    ctx: typer.Context,
    config: Path = Option(None, "--config", exists=True, help="Path to .rw-sync.yaml"),
    verbose: bool = Option(False, "--verbose", "-v", help="Verbose logs"),
):
    # 优先级：--config > RW_SYNC_CONFIG > ./.rw-sync.yaml > 默认
    cfg_path = config or os.getenv("RW_SYNC_CONFIG")
    if not cfg_path:
        default = Path.cwd() / ".rw-sync.yaml"
        cfg_path = default if default.exists() else None
    settings = load_settings(cfg_path)  # 读取 YAML（PyYAML safe_load）
    if verbose:
        settings.log_level = "DEBUG"
    ctx.obj = settings  # 通过 Typer Context 传递
```

> Typer 回调可声明全局参数并注入 `typer.Context`，用于在子命令共享配置与依赖。([Typer][14])

```python
# src/rwsync/config.py
from dataclasses import dataclass
from pathlib import Path
import os, yaml
from dotenv import load_dotenv

@dataclass
class Settings:
    root: Path
    watch_dir: Path
    patterns: list[str]
    db_path: Path
    title_timeout: float
    concurrency: int
    rpm_save: int
    rpm_update: int
    should_clean_html: bool
    default_category: str
    keep_params: set[str]
    drop_params: set[str]
    log_level: str = "INFO"
    token: str = ""

def load_settings(cfg_path: str | Path | None) -> Settings:
    load_dotenv()  # 便于本地开发，从 .env 注入 READWISE_TOKEN
    data = {}
    if cfg_path:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}  # 使用 safe_load 避免执行任意对象
    root = Path(cfg_path).parent if cfg_path else Path.cwd()

    watch = data.get("watch", {})
    state = data.get("state", {})
    net = data.get("network", {})
    rw = data.get("readwise", {})
    norm = data.get("url_norm", {})

    settings = Settings(
        root=root,
        watch_dir=(root / watch.get("dir", "./inbox")).resolve(),
        patterns=watch.get("patterns", ["*.html", "*.htm"]),
        db_path=(root / state.get("db_path", "./data/state/rw_sync.db")).resolve(),
        title_timeout=float(net.get("title_fetch_timeout", 3.0)),
        concurrency=int(net.get("concurrency", 6)),
        rpm_save=int(net.get("rpm_save", 50)),
        rpm_update=int(net.get("rpm_update", 50)),
        should_clean_html=bool(rw.get("should_clean_html", True)),
        default_category=rw.get("default_category", "article"),
        keep_params=set(norm.get("keep_params", ["id","p","page","s","v","t","q"])),
        drop_params=set(norm.get("drop_params", ["utm_","gclid","fbclid","mkt_tok","mc_cid","mc_eid","yclid","spm","ref"])),
        token=os.getenv("READWISE_TOKEN", ""),
    )
    return settings
```

> `.env` 读取建议用 **python-dotenv**；YAML 使用 **PyYAML `safe_load`**。([PyPI][9])

### 12.2 SQLite & WAL 初始化

```python
# src/rwsync/db.py
import sqlite3, time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  norm_url TEXT NOT NULL UNIQUE,
  source_url TEXT,
  title TEXT,
  title_source TEXT,
  file_path TEXT,
  file_mtime REAL,
  readwise_id TEXT,
  last_status INTEGER,
  last_error TEXT,
  created_at TEXT,
  updated_at TEXT
);
"""

def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.executescript(SCHEMA)
    return conn
```

> WAL 通过 `PRAGMA journal_mode=WAL;` 启用，且是持久化模式；WAL 文件与 DB 同目录。([SQLite][4])

### 12.3 URL 解析与规范化（含特例）

```python
# src/rwsync/urlnorm.py
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import re

def choose_url_from_html(html: str) -> tuple[str, str]:
    """
    返回 (source_url, source)：
    source in {"canonical","singlefile","og:url","inferred","synthetic"}
    """
    # 1) <link rel="canonical">
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html, re.I)
    if m: return (m.group(1), "canonical")
    # 2) SingleFile 注释 <!-- saved from url=(xx) -->
    m = re.search(r'<!--\s*saved from url=\(?\s*([^)>\s]+)\s*\)?\s*-->', html, re.I)
    if m: return (m.group(1), "singlefile")
    # 2b) og:url
    m = re.search(r'<meta[^>]+property=["\']og:url["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if m: return (m.group(1), "og:url")
    return ("", "synthetic")

def infer_from_filename(name: str) -> str|None:
    # twitter.com_<user>_status_<id>_*.html → https://x.com/<user>/status/<id>
    tw = re.match(r'(?:x|twitter)\.com_([A-Za-z0-9_]+)_status_(\d+)', name)
    if tw:
        return f"https://x.com/{tw.group(1)}/status/{tw.group(2)}"
    return None

def normalize_url(u: str, keep_params: set[str], drop_prefixes: set[str]) -> str:
    if not u: return u
    parts = urlsplit(u)
    # 规范 host/scheme
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    # 过滤 query
    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if any(k.startswith(pfx) for pfx in drop_prefixes):
            continue
        if k in keep_params:
            kept.append((k, v))
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, netloc, parts.path, query, ""))  # 丢弃 fragment
```

### 12.4 标题抓取（3s 预算）

```python
# src/rwsync/title.py
import httpx
from bs4 import BeautifulSoup

async def fetch_title(url: str, timeout: float = 3.0) -> str|None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        t = soup.find("title")
        return t.get_text(strip=True) if t else None
```

> httpx 支持异步客户端/超时；BS4 便于解析 `<title>`。([HTTPX][3])

### 12.5 Readwise 客户端与限流

```python
# src/rwsync/readwise.py
import httpx, asyncio, time
from aiolimiter import AsyncLimiter

SAVE_URL = "https://readwise.io/api/v3/save/"
UPDATE_URL = "https://readwise.io/api/v3/update/{id}/"
AUTH_URL = "https://readwise.io/api/v2/auth/"

class ReaderClient:
    def __init__(self, token: str, rpm_save=50, rpm_update=50):
        self.headers = {"Authorization": f"Token {token}"}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=10, write=10, pool=5))
        self.save_limiter = AsyncLimiter(rpm_save, 60)
        self.update_limiter = AsyncLimiter(rpm_update, 60)

    async def auth_check(self) -> bool:
        r = await self.client.get(AUTH_URL, headers=self.headers)
        return r.status_code == 204

    async def save(self, payload: dict) -> tuple[int, dict|None, float]:
        async with self.save_limiter:
            start = time.time()
            r = await self.client.post(SAVE_URL, headers=self.headers, json=payload)
            if r.status_code == 429:
                await asyncio.sleep(int(r.headers.get("Retry-After", "1")))
            return r.status_code, (r.json() if r.content else None), time.time()-start

    async def update(self, doc_id: str, payload: dict) -> int:
        async with self.update_limiter:
            r = await self.client.patch(UPDATE_URL.format(id=doc_id), headers=self.headers, json=payload)
            if r.status_code == 429:
                await asyncio.sleep(int(r.headers.get("Retry-After", "1")))
            return r.status_code

async def auth_check(token: str) -> bool:
    return await ReaderClient(token).auth_check()
```

> CREATE/UPDATE 各 50 rpm，配合 `AsyncLimiter` 令牌桶；429 读取 `Retry-After`。([Readwise][1])

### 12.6 失败 CSV（细粒度文件锁）

```python
# src/rwsync/failures.py
from filelock import FileLock
from pathlib import Path
import csv, datetime, os

def failure_csv_path(root: Path) -> Path:
    local_date = datetime.datetime.now().strftime("%Y%m%d")
    p = root / "data" / "failures" / local_date / "failed_urls.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def append_failure(root: Path, url: str, filename: str):
    path = failure_csv_path(root)
    lock = FileLock(str(path) + ".lock")
    with lock:
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(["url","file_name"])
            w.writerow([url, filename])
```

> `filelock` 是跨平台文件锁，建议使用独立 `.lock` 文件。([py-filelock.readthedocs.io][15])

---

## 13. 测试计划

* **单元**：

  * URL 抽取：canonical / SingleFile / og:url / 文件名特例。
  * 规范化：`utm_*` 清理 + 白名单保留（构造多种 query）。([Google Help][6])
  * 标题抓取：离线 HTML 与在线模拟（超时/4xx）。
  * DB 去重：`UNIQUE(norm_url)` 冲突路径。
  * 失败 CSV：并发写入正确性（确认首行 header 仅一次）。
* **集成**：

  * `auth check`（mock 204）。([Readwise][1])
  * `push --all`/`--new`：500/429/网络错误 → 重试与退避。([Readwise][1])
  * 限流精度：60s 窗口内请求不超 50 rpm。([Aiolimiter][12])

---

## 14. 部署与运维

* **依赖**：`httpx`, `aiolimiter`, `beautifulsoup4`, `lxml`, `PyYAML`, `python-dotenv`, `filelock`.（Typer 作为 CLI）([Typer][10])
* **容器化**：多阶段镜像（builder 安装依赖，runtime 仅复制 `src/` + 入口），挂载工作目录与 `.env`。
* **环境变量**：`READWISE_TOKEN`。`.env` 仅用于开发/本机，生产用系统环境或 CI Secret。([PyPI][9])
* **持久化**：`./data/state/rw_sync.db`（或配置路径）；WAL 模式。([SQLite][4])

---

## 15. 安全与合规

* 不将 Token 写入仓库；`.env.example` 仅占位。([PyPI][9])
* YAML 仅 `safe_load`；拒绝任意对象反序列化。([GitHub][16])
* HTTP 默认跟随重定向；可选启用 `http2`（httpx 支持）。([HTTPX][17])

---

## 16. 决策记录（ADR 摘要）

> 采用 Pugh 加权（默认权重）并结合你的选择，形成最终结论。

* **文件类型**：仅 HTML（不含 PDF/EPUB）——实现最小可用。
* **新增/更新策略**：以创建为主，补充 PATCH 更新**元数据**（正文不更新）。([Readwise][1])
* **URL 提取优先级**：canonical > SingleFile 注释/og:url > 自造。([Google for Developers][5])
* **标题获取**：A 方案（requests/httpx + BS4/lxml），在线 3s 超时，回退 Reader→本地。([MDN Web Docs][2])
* **规范化**：白名单保留，移除 `utm_*` 等跟踪参数。([Google Help][6])
* **速率控制**：httpx.AsyncClient + aiolimiter，CREATE/UPDATE 各 50 rpm，429 读 `Retry-After`。([HTTPX][3])
* **凭据管理**：`.env`（开发）+ 环境变量（生产）。([PyPI][9])
* **失败闭环**：本地重试 + CSV（自然日滚动，本地时区），写入加文件锁。([py-filelock.readthedocs.io][15])
* **CLI**：Typer；一次性命令（`push --all/--new`），不常驻。([Typer][10])
* **状态库**：SQLite 原生 + WAL；默认 `./data/state/rw_sync.db`，可配置覆盖。([SQLite][4])
* **配置格式**：YAML（PyYAML `safe_load`）。([PyYAML][8])

---

## 17. 全量问答清单（Q1–Q28 摘要）

* **Q1**：仅 HTML；用 `/save` 传 `html` + 自造 URL（有真实 URL 则用真实）。
* **Q2**：选择 **B：支持元数据更新**（不更新正文）。([Readwise][1])
* **Q3**：不映射目录为标签；标题用网页标题优先。
* **Q4**：不支持 PDF/EPUB。
* **Q5**：状态存储 SQLite。
* **Q6**：标题优先级：网页 → Reader → 本地。([MDN Web Docs][2])
* **Q7**：**B1** 一次性 CLI。
* **Q8**：URL 提取 **A > B > C**。([Google for Developers][5])
* **Q9/10/11**：标题抓取 A 方案；URL 规范化白名单（含特殊下划线文件名还原）。
* **Q12**：限流 A（并发 4–6 + 令牌桶 50 rpm）。([Readwise][1])
* **Q13**：Token 用环境变量 + `.env`；启动校验 204。([Readwise][1])
* **Q14–Q16**：失败重试 + 自然日 CSV（`url,file_name`），细粒度文件锁。([py-filelock.readthedocs.io][15])
* **Q17**：启用去重 + 文件锁。
* **Q18**：自然日按本地时区。
* **Q19**：全局去重。
* **Q20**：仅 CSV 写锁。
* **Q21–Q24**：httpx+aiolimiter；“基准配置 A”；`should_clean_html=true`。([Readwise][1])
* **Q25**：Typer。([Typer][10])
* **Q26–Q28**：DB 路径可配置；**配置优先级 A**；配置格式**YAML**。([PyYAML][8])

---

## 18. Mermaid 流程图

```mermaid
flowchart TD
  A[扫描 watch_dir\n匹配 *.html] --> B[解析 URL\ncanonical > SingleFile/og:url > 文件名 > 自造]
  B --> C[URL 规范化\n剔除 utm_* 等\n保留白名单参数]
  C --> D{DB 是否存在\nUNIQUE(norm_url)}
  D -- 否 --> E[在线抓取 <title> 3s\n失败回退 Reader→本地]
  E --> F[POST /save\nshould_clean_html=true]
  F -->|201 新建 或 200 已存在| G[记录 readwise_id\n更新 DB]
  D -- 是 --> H[可选 PATCH /update\n更新标题/标签等]
  F --> I{HTTP 429/网络失败?}
  I -- 是 --> J[读 Retry-After\n重试/退避]
  I -- 失败终止 --> K[写 CSV: data/failures/yyyymmdd/failed_urls.csv\n加 filelock]
  G & H --> L[完成统计/日志]
```

---

## 19. 总结与取舍

* 我们以**一次性命令**实现稳定的数据通路，绕开常驻进程与复杂部署；以 **URL 规范化 + 全局去重** 确保幂等。
* **在线 `<title>` 抓取**保证 UX；Reader 的自动清洗（`should_clean_html`）作为兜底，兼顾质量与实现成本。([Readwise][1])
* 并发与限流采用 **httpx + aiolimiter** 轻量组合，精确匹配 Reader 的 50 rpm 上限；429 自动退避。([HTTPX][3])
* 配置与密钥分离（YAML + `.env`），状态用 SQLite（WAL），**src/** 布局保证可维护性与容器化友好。([SQLite][4])

---

### 附：参考文档（核心负载语句的出处）

* Reader API：认证/CREATE/UPDATE/限流/`should_clean_html`。([Readwise][1])
* Typer：回调、上下文对象、基于 Click。([Typer][14])
* httpx：异步支持、超时与资源上限。([HTTPX][3])
* aiolimiter：异步令牌桶。([Aiolimiter][12])
* SQLite WAL：启用方式与特性。([SQLite][4])
* PyYAML `safe_load`（安全性）。([PyYAML][8])
* HTML `<title>` 标准；`rel=canonical` 的规范用法。

---

> 若你希望，我可以基于此文档直接生成：`pyproject.toml`、`requirements.txt`、最小可运行的 `src/` 骨架与 `tests/` 雏形，按上述结构落地。

[1]: https://readwise.io/reader_api "Reader API | Readwise"
[2]: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/title?utm_source=chatgpt.com "<title>: The Document Title element - HTML - MDN - Mozilla"
[3]: https://www.python-httpx.org/async/?utm_source=chatgpt.com "Async Support"
[4]: https://sqlite.org/wal.html?utm_source=chatgpt.com "Write-Ahead Logging"
[5]: https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls?utm_source=chatgpt.com "How to specify a canonical URL with rel=\"canonical\" and ..."
[6]: https://support.google.com/analytics/answer/10917952?hl=en&utm_source=chatgpt.com "[GA4] URL builders: Collect campaign data with custom ..."
[7]: https://www.python-httpx.org/advanced/timeouts/?utm_source=chatgpt.com "Timeouts"
[8]: https://pyyaml.org/wiki/PyYAMLDocumentation?utm_source=chatgpt.com "PyYAML Documentation"
[9]: https://pypi.org/project/python-dotenv/?utm_source=chatgpt.com "python-dotenv"
[10]: https://typer.tiangolo.com/?utm_source=chatgpt.com "Typer"
[11]: https://typer.tiangolo.com/tutorial/using-click/?utm_source=chatgpt.com "Using Click"
[12]: https://aiolimiter.readthedocs.io/?utm_source=chatgpt.com "aiolimiter — aiolimiter 1.2.1 documentation"
[13]: https://www.python-httpx.org/advanced/resource-limits/?utm_source=chatgpt.com "Resource Limits"
[14]: https://typer.tiangolo.com/tutorial/commands/callback/?utm_source=chatgpt.com "Typer Callback"
[15]: https://py-filelock.readthedocs.io/?utm_source=chatgpt.com "filelock - Read the Docs"
[16]: https://github.com/yaml/pyyaml/wiki/PyYAML-yaml.load%28input%29-Deprecation?utm_source=chatgpt.com "PyYAML yaml.load(input) Deprecation"
[17]: https://www.python-httpx.org/api/?utm_source=chatgpt.com "Developer Interface"
