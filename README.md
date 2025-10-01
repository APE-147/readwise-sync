# reader-sync

本项目是一个将本地保存的 HTML 页面批量同步到 Readwise Reader 的命令行工具（CLI）。
它会扫描你指定的文件夹，解析页面来源 URL 与标题，按需创建或更新 Reader 中的文档，并在本地保存同步状态与失败记录，支持增量同步与失败重试。

## 主要功能
- 扫描本地目录中的 HTML（支持多模式匹配）。
- 自动识别来源 URL：优先从 HTML `<link rel="canonical">`、`<!-- saved from url=... -->`、`og:url` 中提取；若无则可从文件名中 `[URL]` 片段推断；完全缺失时生成本地 `synthetic` URL 以保证可追踪。
- 统一 URL：保留指定参数（如 `id`、`page` 等）、丢弃追踪参数（如 `utm_`、`gclid` 等）。
- 标题来源优先级：在线抓取 > Reader 返回 > HTML 本地 `<title>`。
- 调用 Readwise Reader API：
  - 创建：`POST /api/v3/save/`
  - 更新：`PATCH /api/v3/update/{id}/`
  - 鉴权：`GET /api/v2/auth/`（204 表示成功）
- 并发与限速：可配置并发数、每分钟保存/更新速率，自动处理 429 与 `Retry-After`。
- 本地状态管理（SQLite）：去重（按规范化 URL）、最近状态码/错误、Reader 文档 id、增量水位线等。
- 失败记录（CSV，按天分文件），支持按天重放失败项。
- CLI 体验：一次性导入（--all）、增量同步（--new）、失败重放（replay）、干跑（--dry-run）、详细日志（-v）。

## 适用场景
- 你在本地或浏览器扩展（如 SingleFile）保存了大量网页，需要批量导入 Reader。
- 之后只想同步新增文件，避免重复处理。
- 希望本地留存可审计的同步状态与失败明细，必要时可重试。

## 快速开始
1) 安装（Python 3.11+）：
- 开发安装：
  - 在项目根执行：`pip install -e .`
  - 将可安装脚本 `rw-sync`（入口：`reader_sync.cli:app`）

2) 配置环境变量：
- 复制并编辑 `.env.example` 为 `.env`，至少设置：
  - `READWISE_TOKEN=你的Readwise令牌`
- 可选：在 `.env` 中设置路径覆盖，避免把本机路径写进 YAML：
  - `RW_SYNC_WATCH_DIR=/path/to/inbox`
  - `RW_SYNC_DB_PATH=./data/state/rw_sync.db`

3) 准备运行配置：
- 复制 `.rw-sync.example.yaml` 为 `.rw-sync.yaml`，按需调整：
  - `watch.dir`、`patterns`
  - `network`（并发、超时、RPM）
  - `readwise.should_clean_html`、`default_category`
  - `url_norm.keep_params` / `drop_params`

4) 验证鉴权：
- `rw-sync auth check`（返回 `Auth OK (204)` 表示成功）

5) 首次导入与日常增量：
- 首次全量：`rw-sync push --all`
- 之后增量：`rw-sync push --new`
- 预览而不真正调用 API：加 `--dry-run`

可选：使用提供的包装脚本 `rw_sync_push_new.sh`（支持 Conda 环境），也可设置 `RW_CONDA_ENV`、`RW_DEFAULT_ENV_DIR`。

## CLI 命令速览
- 全局选项：`--config PATH` 指定 YAML；`--dry-run` 干跑；`-v/--verbose` 调试日志。
- 鉴权：`rw-sync auth check`
- 推送：
  - `rw-sync push --all [--max N] [--since YYYY-MM-DD|YYYY-MM-DDTHH:MM:SS]`
  - `rw-sync push --new [--max N] [--since ...]`
  - `--all` 与 `--new` 二选一；`--since` 可设时间窗口；`--max` 控制本次上限。
- 失败重放：`rw-sync replay [--date YYYY-MM-DD]`

## 工作原理（简述）
1) 发现文件：在 `watch.dir` 内按 `patterns` 递归查找 HTML，读取 `mtime/size/birthtime`。
2) 准备文档：读取 HTML、计算 `sha1`；解析/推断来源 URL 并规范化；抽取本地 `<title>`。
3) 决策动作：
   - 未见过或无 `readwise_id` → `create`
   - 增量模式（--new）且已存在 → `skip`
   - 其他情况 → `update`（若仅标题变化亦可更新）
4) 发送请求：按限速并发调用 Reader API；处理 429/异常并记录失败。
5) 持久化：将文档状态写入 SQLite（`data/state/rw_sync.db`），保存增量水位线与最近状态。

## 配置项说明（.rw-sync.yaml）
- `watch.dir`：监控目录，默认 `./inbox`（首次运行会自动创建）。
- `watch.patterns`：文件匹配，默认 `['*.html','*.htm']`。
- `state.db_path`：SQLite 路径，默认 `./data/state/rw_sync.db`。
- `network.concurrency`：并发请求数，默认 6。
- `network.title_fetch_timeout`：标题抓取超时（秒），默认 3.0。
- `network.rpm_save` / `network.rpm_update`：每分钟保存/更新上限，默认 50/50。
- `readwise.should_clean_html`：是否让 Reader 清洗 HTML，默认 `true`。
- `readwise.default_category`：默认分类（如 `article`），为空则不附加分类。
- `url_norm.keep_params` / `drop_params`：URL 参数保留/丢弃规则（前者精确匹配，后者按前缀）。

提示：也可用环境变量覆盖关键路径：`RW_SYNC_WATCH_DIR`、`RW_SYNC_DB_PATH`；配置文件路径可用 `RW_SYNC_CONFIG` 指定。

## 环境变量
- 必填：`READWISE_TOKEN`（API 令牌，建议放 `.env`）
- 可选：`RW_SYNC_CONFIG`（YAML 路径）
- 可选：`RW_SYNC_WATCH_DIR`、`RW_SYNC_DB_PATH`（覆盖 YAML 路径）
- 可选（包装脚本）：`RW_CONDA_ENV`、`RW_DEFAULT_ENV_DIR`

## 文件结构（摘）
```
service/readwise/reader-sync/
├─ src/reader_sync/
│  ├─ cli.py               # Typer CLI：auth/push/replay
│  ├─ config.py            # 加载 .env 与 YAML，合并默认值
│  ├─ sync.py              # 扫描、准备文档、决策与调用 API
│  ├─ readwise_client.py   # httpx + aiolimiter + 429 处理
│  ├─ html_utils.py        # URL/标题提取与标准化
│  ├─ filesystem.py        # 文件发现/读取/sha1
│  ├─ database.py          # SQLite 文档状态与元数据
│  ├─ title_fetcher.py     # 在线抓取 <title>
│  ├─ logging_setup.py     # 日志格式配置
│  └─ models.py            # 数据模型与统计
├─ .rw-sync.example.yaml   # 示例运行配置（复制为 .rw-sync.yaml）
├─ .env.example            # 示例环境变量（复制为 .env）
├─ rw_sync_push_new.sh     # 便捷脚本（可与 Conda 集成）
├─ data/                   # 输出/失败/报告/状态（已在 .gitignore 中忽略）
└─ tests/                  # 单元测试（pytest）
```

## 数据与隐私
- `.env`、`data/*`、缓存等均已被 `.gitignore` 忽略，不会入库。
- `.rw-sync.yaml`（含本机路径）也被忽略；请将可分享的示例改在 `*.example.*` 中。

## 开发与测试
- 安装开发依赖：`pip install -e .[dev]`
- 运行测试：`pytest`
- 日志：`-v/--verbose` 输出调试日志；默认 INFO。

## 命名规范补充
- 从文件名推断 URL（可选）：文件名包含标记 `[URL]` 后接原始 URL 的编码版，例如：
  - `Cleaned_Title_20250101_121314_[URL]_https%3A%2F%2Fexample.com%2Fpost.html`
  - 也支持空格形式：`Cleaned Title 20250101 121314 [URL] https%3A...`
- 若无法解析 URL，将使用 `https://local/doc/<sha1>` 作为占位来源（后续仍可更新）。

---
如需更贴合你的使用方式（文件命名/参数规范/分类策略等），可在 `.rw-sync.yaml` 与 `.env` 中调整上述配置。
