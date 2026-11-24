# 黄果采集器 · Huangguo Collector

面向 `huangguoai.com`（黄果短剧）和 `yeguodj.com`（野果短剧）的本地采集 + 媒体库管理工具。黄果按原创 / 短剧 / 漫剧三个模块采集，野果按探索分类全量采集；两站同名剧自动保留集数更多的版本。提供：

- 直观的 Web 媒体库（深色界面，卡片式浏览、搜索、筛选、分页）
- 一键下载整部剧 / 只下载新增分集 / 分批下载
- 后台下载队列（进度、速度、大小、取消、失败重试）
- 自动生成 Emby / Jellyfin / Kodi 电视剧刮削结构（NFO + 海报 + fanart）
- 自动打「已下载」标签 + 自定义标签管理
- 更新检测：重新扫描后标记「有更新」，直接补下新增分集
- 双站点：黄果（原创/短剧/漫剧）+ 野果（探索分类）
- 跨站点同名合并：同名剧只保留集数最多的一个版本，重复条目隐藏但文件不删除
- 定时任务：每天/每周指定时间自动全量扫库（最新 + 热播 + 精选 / 野果探索）
- 订阅追更：订阅的剧在扫库发现新集后自动下载，可单独设置画质
- 下载目录、目录模板可通过设置页或环境变量指定
- 集封面：下载完成自动从视频抽帧生成 Jellyfin `-thumb.jpg`，支持单剧/批量/全部补全
- 内置 HLS 观看代理，不下载也可以先在网页里看

## 界面

信息密集型媒体库，参考本地资源库的布局：

- **顶栏**：全局搜索、剧集/已下载/更新统计、筛选入口、开始采集、打开下载目录。
- **左侧栏**：状态分面（未下载 / 下载中 / 部分 / 已完成 / 有更新 / 收藏 / 已订阅）、模块计数、题材与自建标签计数，全部可点击筛选；另有更新提醒、下载队列、订阅追更、定时任务、标签、设置入口。
- **主区工具栏**：当前条数、排序、只看未下载、全选当前、总下载进度。
- **卡片**：封面 + 站点 ID + 评分 + 题材/集数角标；标题、上线日期、播放量、下载状态、题材标签、分集进度；底部固定「下载整部 / 详情 / 订阅 / 观看 / 收藏」操作栏。
- **筛选弹窗**：模块、状态、评分、年份、题材、自建标签，全部带数量。
- **批量条**：勾选后底部出现，支持批量下载整部、只下更新、加标签、同步详情、收藏。
- **详情页**：封面、评分、题材、简介、下载进度、体积估算、分集网格、NFO 操作；桌面 3 栏，窄屏自动折叠。

> 仅用于个人媒体库整理。内容为成人向 AI 短剧（18+），请遵守站点条款与当地法律法规，不要公开传播。

## 1.5.0 多站点

- 新增 **野果短剧**：列表 75 页、每页 20 部；用 curl_cffi 过 Cloudflare TLS 指纹。
- 野果播放接口是 AES-CBC 加密 JSON，解密后一次拿到整部剧的全部分集、m3u8、每集封面，不用逐集开网页。
- 野果封面复用现有图片解密协议；下载后仍自动抽帧生成 `-thumb.jpg`。
- **同名跨站点合并**：标题做全角/空白/标点归一化，集数最多的为主版本；其他条目 `is_duplicate=1` 并从媒体库、更新、订阅和统计中隐藏，原文件保留。
- 同名合并数会显示在左侧模块区，例如「同名合并 13 部」。
- HLS 代理新增野果 CDN 域名白名单。
- 新增 17 个单测。

## 1.4.3 易用性

- HLS 播放代理按分片 token 隔离 CDN 鉴权头，避免同时播放/下载多集时串用 `x-cdn-auth`。
- `/api/activity` 使用 2 秒统计缓存；补充 SQLite 热路径索引，降低 1.6 秒轮询开销。
- 下载 HEVC 失败时第 2 次起自动回退 H.264；ffmpeg 增加 120 秒读超时，失败原因写入容器日志。
- 全量扫描跨模块按 `drama_id` 去重详情抓取，减少原创/短剧/漫剧交叉收录的重复请求。
- 容器/Linux 下点“打开目录”不再 500，改为提示宿主机路径。
- 下载失败全局提示 + 一键重试全部失败 + 清理失败记录 + 失败原因弹窗。
- 首次使用空库改为三步引导：扫描、设置下载目录、创建定时任务。
- 设置页新增“测试站点连接”，直接显示延迟或具体错误。
- 前端静态资源按版本号刷新，升级后不再需要手动 `Ctrl+F5`。
- 新增 13 个单测，并接入 GitHub Actions：测试通过才构建镜像。

---

## 1. 环境要求

| 依赖 | 说明 |
|---|---|
| Windows 10/11 | 已在 Windows 验证（`os.startfile` 打开目录） |
| Python 3.10+ | 推荐 3.11–3.14 |
| ffmpeg | 必须，用于 HLS 解密合并 / 转码。`winget install Gyan.FFmpeg` |
| 磁盘 | HEVC 约 **90–160 MB/集**，全站下载需要 **TB 级**，请按需选择 |

安装依赖：

```bat
install.bat
```

或：

```bat
python -m pip install -r requirements.txt
```

## 2. 启动

```bat
start.bat
```

浏览器打开 <http://127.0.0.1:8765>。也可以手动：

```bat
python -m app
```

默认只监听 `127.0.0.1`，不对外网开放。端口 `8765` 被占用时，修改 `app/__main__.py` 或：

```bat
python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

## 3. 第一次使用

1. 点右上角 **开始采集**。
2. 勾选模块（原创 / 短剧 / 漫剧 / 野果短剧），页数留空 = 全部。
3. 保持 **同步详情与分集** 勾选（下载必需）。
4. 开始。列表扫描很快；详情按 6 并发同步，全量约 5–15 分钟。
5. 回到媒体库，点卡片进入详情，选画质后「下载全集」。
6. 下载完成后，在 **设置 → 下载根目录** 找到文件；把模块子目录加入 Jellyfin。

首次建议先用「页数 = 1」试跑，确认流程正常再全量。

## 4. 采集实现说明

站点是服务端渲染，列表与详情都不需要登录：

- 列表：`/original/`、`/ai-duanju/`、`/ai-manju/`，分页 `/ai-duanju/2/`…
  短剧/漫剧会同时扫 **最近更新 / 当前热播 / 精选推荐** 三个列表（`/hot/`、`/random/`），
  按 `site_id` 去重；同一部剧可能同时属于多个模块，模块计数会重叠，侧栏显示「跨模块 N 部」。
  解析 `.hg-drama-card`：标题、封面、评分、集数、标签、播放量、角标。
- 详情：`/video/{id}/`，内嵌 `#videoInitialData` JSON + 分集 `<a class="hg-web-play__ep">`。
  分集总数取「链接数 / 选集区间 data-end / trackingEpisodes / 字段 ep」的最大值。
- 播放地址：`POST /api/videos/{id}/episodes/{n}/playback`，请求体 `{}`，必须带
  `Content-Type: application/json`。返回 `streams.main`（H264）与 `streams.hevc`（H265），
  以及 CDN 鉴权头 `x-cdn-auth` / `x-cdn-app`。
- 封面：图床返回的是 **AES-128-CBC 加密图**，密钥由
  `/static/web/js/plugins/crypto-image-protocol.js` 下发（运行时抓取，内置回退值）。
  采集时立即解密并保存到 `data/covers/`，避免签名 URL 过期。
- 下载：ffmpeg 直接读 AES-128 HLS，`-c copy` 合并为 mp4（HEVC 走 `-tag:v hvc1`）。
  下载前会重新取一次播放地址，避免签名过期。

> 站点改版时优先检查 `app/scraper.py` 里的选择器与接口路径。

## 5. 画质与空间

| 画质 | 典型码率 | 体积（每集 8–10 分钟） | 说明 |
|---|---|---|---|
| HEVC 原画 | ~2.4 Mbps | 90–160 MB | **默认**，体积小，Emby/Jellyfin 直接播 |
| H264 原画 | ~3–8 Mbps | 100–600 MB | 兼容性最好，浏览器直接播；片源分辨率/码率随剧集不同 |
| 720P 压缩 | 转码 | 100–180 MB | 从 H264 转码，CPU 占用高、速度慢 |

设置页会实时显示磁盘剩余；下载确认框会按所选画质估算总大小。当前去重后约 1800+ 部、9000+ 集，
**不要无脑全选**，建议按模块/标签/收藏分批下载。

## 6. Jellyfin / Emby 刮削

每次下载完成自动写入：

```text
下载根目录/
  原创/
    剧名 (2026)/
      tvshow.nfo
      poster.jpg
      fanart.jpg
      Season 01/
        剧名 - S01E01.mp4
        剧名 - S01E01.nfo
        ...
  短剧/
  漫剧/
```

- NFO 使用 Emby/Jellyfin 兼容的 `<tvshow>` / `<episodedetails>`。
- 评分、简介、题材标签、上线日期、出品、站点 ID 全部来自采集数据。
- 演员信息站点本身没有（AI 短剧），因此不写 `<actor>`，用 `<tag>AI短剧</tag>` 标注。
- **推荐做法**：在 Jellyfin 里分别添加三个「电视剧」媒体库，路径指向
  `下载根目录/原创`、`/短剧`、`/漫剧`。这样每个模块目录下就是标准的「剧集/Season」结构。
- 已下载分集立刻生成单集 NFO；`tvshow.nfo` 会随集数更新。
- 想手动刷新：详情页「生成 NFO」；想批量：直接重新下载/重新扫描后自动写。

设置项 `write_nfo` / `write_fanart` 可关闭。`auto_tag_done` 控制全集完成后是否自动加「已下载」标签。

## 7. 更新检测与补下

1. 重新扫描模块（可以只扫 1 页，也可以全量）。
2. 列表卡片集数比库里大 → 自动置 `has_update=1`，并同步详情补分集。
3. 侧边栏「更新提醒」出现红点；更新页显示「新增 N 集（旧 → 新）」。
4. 点「下载更新」只下载 `state != done` 的分集；详情页「下载新增」同理。
5. 全部补齐后自动清除更新标记；也可以手动「忽略」。

扫描默认只同步新增/有变化的剧，已同步过的不会重复抓详情；需要强制刷新时勾选「强制刷新已有详情」。

## 8. 标签

- 系统标签：全集下载完成后自动加 **已下载**（绿色）。
- 自定义标签：标签页创建，详情页「＋ 标签」或批量「加标签」。
- 标签可筛选；搜索框也支持搜自定义标签名。
- 删除标签不会删除剧集或文件。

## 9. 下载队列

- 每集一个任务，SQLite 持久化；程序重启后未完成任务会自动重新排队。
- 并发数默认 2，可在设置里改（**重启程序生效**）。
- 下载中可以直接取消；失败任务会在队列页显示原因，可重试。
- 临时文件位于 `data/tmp/`，成功/取消/失败都会清理。
- 下载完成后：落盘 → 更新数据库 → 写 NFO/海报 → 刷新完成度 → 自动打标签。

## 订阅追更

- 在卡片右下角点 **◉**，或在详情页点「订阅追更」。
- 每部订阅可单独设置画质（HEVC / H264 / 720P）和是否「自动下载新集」。
- 扫库同步到该剧详情后，订阅管理器会检查 `episodes.state != done`：
  - 自动下载开启：直接加入下载队列；
  - 只提醒：仅在订阅页/更新页显示「有更新」，手动点「下载更新」。
- 下载完成后自动打「已下载」，全集补齐后清除更新标记。
- 订阅页支持逐部改画质、开关自动下载、取消订阅、一键下载全部更新。
- 首次订阅不会立刻抓全剧；下一次扫库（手动或定时）才会自动补下。想立刻下载，订阅页点「下载更新」。

## 定时任务

- 侧栏「定时任务」→ 新建：设置名称、`HH:MM`、执行日（周一–周日）、采集模块、是否同步详情、是否启用。
- 到点自动执行一次 **全量扫库**（最新 + 热播 + 精选），并触发订阅自动下载。
- 调度器每 15 秒检查一次；同一时间只允许一个扫描任务，正在扫描时会顺延到下一次。
- 时区跟随服务器本地时间（当前 Asia/Shanghai）；程序离线期间错过的任务不会补跑，恢复后按下一个时间点执行。
- 支持「立即执行」按钮手动触发。

## 下载目录

- 设置页「下载根目录」可直接填写，也可点「浏览」用目录选择器逐级选择磁盘/文件夹，不用手打路径。
- 目录模板可改，支持 `{module}`、`{title}`、`{year}`、`{site_id}`，例如：
  `{module}/{title} ({year})`。
- 环境变量 `HUANGGUO_DOWNLOAD_ROOT` 可作为首次启动的默认下载目录（适合 Docker 挂载）。
- 修改只影响**新任务**：已下载文件不会自动搬迁。

## 集封面

- 每集下载完成后，从视频抽一帧（约 10% 处）生成 640×360 封面，写入：
  - 媒体目录：`剧名 - S01E01-thumb.jpg`（Jellyfin/Emby 自动识别）
  - 数据目录：`data/covers/episodes/`
  - 分集 NFO：`<thumb>` 字段
- 详情页分集网格会直接显示集封面缩略图。
- 历史已下载但没有封面的，分三种补全：
  - 详情页「补集封面」：单个剧
  - 媒体库勾选后「补集封面」：批量
  - 设置页「补全全部已下载集封面」：全库
- 抽帧失败时用剧集海报兜底，不会留空。
- 站点 `<video poster>` 实际是同剧海报且签名会过期，所以采用“从视频抽帧”而不是抓站点 poster。
- 野果播放接口里每集有 `episode_cover`，会存入分集 `cover_url`；最终 Jellyfin 缩略图仍优先用视频抽帧，避免海报过期。

## 10. 目录结构

```text
huangguo-collector/
  app/
    config.py       路径、模块、默认设置
    db.py           SQLite 表结构与数据访问
    scraper.py      黄果列表/详情/播放接口/封面解密
    yeguo.py        野果列表/播放 AES 解密/封面
    scan.py         全量与增量扫描调度
    scheduler.py    定时任务调度器
    covers.py       已下载分集的封面抽帧补全
    downloader.py   ffmpeg 下载队列与进度
    nfo.py          NFO/海报/目录结构
    hlsproxy.py     站内 HLS 观看代理（m3u8 重写 + HMAC 签名）
    api.py          REST API
    main.py         FastAPI 入口
  web/
    index.html      单页界面
    app.js          前端逻辑（无构建）
    style.css       深色主题
    hls.min.js      hls.js 1.6.13（Apache-2.0）
  data/             运行时生成：DB、封面、临时文件
  huangguo-downloads/ 默认下载根目录
  docker/entrypoint.sh 容器入口
  Dockerfile
  docker-compose.yml / docker-compose.zspace.yml
  requirements.txt
  start.bat / install.bat
```

运行时数据全部在 `data/` 与下载目录，删掉 `data/` 即可重置媒体库（不会删除已下载视频）。

## 11. 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/activity` | 扫描进度 + 活动任务 + 统计（前端 1.6s 轮询） |
| POST | `/api/scan` | 开始扫描 `{modules, pages, details, refresh}`，modules 支持 `yeguo` |
| GET | `/api/dramas` | 分页/搜索/筛选/排序；支持 `source`、`include_duplicates` |
| GET | `/api/dramas/{id}` | 详情 + 分集 + 标签 + 空间估算；重复条目返回 `duplicate_preferred` |
| POST | `/api/dramas/{id}/download` | `{scope: all/new/eps, eps, quality}` |
| POST | `/api/dramas/batch` | 批量下载/同步/打标签/收藏 |
| GET | `/api/updates` | 有更新的剧 |
| GET | `/api/subscriptions` | 订阅列表 + 更新/自动下载统计 |
| POST | `/api/dramas/{id}/subscribe` | `{enabled, quality, auto_download, download_now}` |
| DELETE | `/api/subscriptions/{id}` | 取消订阅 |
| POST | `/api/subscriptions/download` | 下载全部订阅更新 |
| GET/POST/PUT/DELETE | `/api/schedules` | 定时任务 CRUD |
| POST | `/api/schedules/{id}/run` | 立即执行定时任务 |
| POST | `/api/settings/check-path` | 校验/创建下载目录并返回剩余空间 |
| GET | `/api/fs/list` | 目录选择器：列出磁盘与子目录 |
| POST | `/api/fs/mkdir` | 在指定目录下新建文件夹 |
| GET | `/api/covers` | 集封面补全进度 |
| POST | `/api/covers/backfill` | `{drama_ids}` 或 `{all:true}` 补全集封面 |
| GET | `/api/site-check` | 测试站点连通性；`?source=huangguo|yeguo` |
| GET | `/api/tasks` | 队列与历史任务 |
| POST | `/api/tasks/retry-failed` | 重试全部失败任务 |
| POST | `/api/tasks/clear` | `{states}` 清理完成/取消/失败记录 |
| GET | `/api/watch/{id}/{ep}` | 重写后的 m3u8（站内观看） |
| GET | `/api/stream?u=&t=` | 分片/密钥代理（HMAC + 域名白名单） |
| GET/PUT | `/api/settings` | 设置 |

## 12. 常见问题

**下载报 “ffmpeg 退出码 1 / 403”**
播放签名过期或站点限流。任务会自动重试 3 次（每次重新取播放地址）。仍失败时降低并发、增大请求间隔。

**封面显示占位图**
图床解密密钥或域名变了。检查 `app/scraper.py` 的 `FALLBACK_IMAGE_KEY/IV`，或删除 `data/covers` 后重新扫描详情。

**Jellyfin 识别不了**
确认媒体库路径指向 `下载根目录/原创` 这类模块目录，而不是整个下载根目录；确认文件夹里有 `tvshow.nfo` 和 `Season 01`。

**全量扫描很慢**
详情同步是 6 并发、每次请求间隔 0.15s。可在设置里把 `scan_workers` 调到 8–10、`request_delay` 调到 0.05；不建议更高，容易被限流。

**磁盘不够**
默认 HEVC 已是体积最小的原画。建议先筛选「未下载」+ 按模块/评分下载；或改用 720P 压缩（CPU 换空间）。

**端口被占用**
`python -m uvicorn app.main:app --port 8766`，或改 `app/__main__.py`。

## Docker / 极空间 Z4 Pro

镜像已发布到 Docker Hub（`linux/amd64`，适配 Z4 Pro）：

- **镜像**：`jokermelove/huangguo-collector:1.5.0`（另有 `latest`）
- **GitHub**：<https://github.com/king-joker-z/huangguo-collector>
- **Actions**：推送到 `main` 自动构建并推送 Docker Hub，也可在 Actions 页手动 `Run workflow`

```sh
docker pull jokermelove/huangguo-collector:1.5.0
```

### 方式 A：极空间直接拉取镜像（推荐）

1. 在 NAS 上建目录，例如 `/volume1/docker/huangguo/`。
2. 把仓库里的 `docker-compose.zspace.yml` 放到该目录，按需修改两个 `volumes`：
   - `/volume1/docker/huangguo/data:/data`：数据库、封面、临时文件
   - `/volume1/Media/Huangguo:/downloads`：下载的视频和 NFO
3. 极空间 Docker → Compose → 新建项目，选择该目录部署；或 SSH：

```sh
cd /volume1/docker/huangguo
docker compose -f docker-compose.zspace.yml pull
docker compose -f docker-compose.zspace.yml up -d
```

4. 浏览器打开 `http://极空间IP:8765`。

镜像已内置 ffmpeg、时区、健康检查，无需上传源码或本地构建。

### 方式 B：自己构建（可选）

本机有 Docker 时：

```sh
docker build --platform linux/amd64 -t jokermelove/huangguo-collector:1.5.0 .
# 或
docker compose up -d --build
```

Windows 可双击 `docker-build.bat` 构建并导出 tar，再导入极空间。

### 更新

```sh
docker compose -f docker-compose.zspace.yml pull
docker compose -f docker-compose.zspace.yml up -d
```

`data` 和 `downloads` 卷会保留；`HUANGGUO_DATA_DIR` / `HUANGGUO_DOWNLOAD_ROOT` 在容器内生效。

然后把 compose 里的 `image:` 改成对应仓库地址、删掉 `build:`，极空间直接 `docker compose up -d` 即可。

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HUANGGUO_DATA_DIR` | `/data` | SQLite、封面、临时文件 |
| `HUANGGUO_DOWNLOAD_ROOT` | `/downloads` | 视频下载目录；容器内会覆盖数据库里的旧路径 |
| `TZ` | `Asia/Shanghai` | 定时任务时区 |
| `PORT` | `8765` | 容器内监听端口 |

### 注意

- 容器默认以 root 运行，方便写入极空间挂载目录；如遇权限问题，给 `data` / `downloads` 目录放权即可。
- `HUANGGUO_DOWNLOAD_ROOT` 在容器内是**强制值**：设置页里改下载路径，重启容器后会被环境变量改回 `/downloads`。要在容器里换目录，请改 compose 的 volume 映射。
- **v1.4.1 起修复了 Docker 下下载全部失败的问题**：旧版把临时文件放在 `/data/tmp`，最终文件在 `/downloads`，两者是两个独立挂载点，`os.replace` 会报 `EXDEV / Invalid cross-device link`。现在临时 `.part.mp4` 直接写在目标 Season 目录，最后同目录重命名。
- 如果升级后仍失败，先看网页任务错误，或执行：

```sh
docker exec huangguo-collector python -c "import sqlite3;c=sqlite3.connect('/data/huangguo.db');[print(r) for r in c.execute(\"select id,quality,size,error from tasks where state='failed' order by id desc limit 10\")]"
docker exec huangguo-collector df -h /data /downloads
```
- 本服务没有登录认证，**只建议局域网使用**，不要把 8765 端口直接暴露到公网；需要外网访问请套反向代理 + 认证。
- 720P 压缩转码使用 CPU，极空间上并发不要开太高（建议 2）。
- 升级：替换项目/镜像后重新 `docker compose up -d --build`，`data` 和 `downloads` 卷会保留。
- 备份：备份 `/data` 即可保留媒体库元数据、标签、订阅和定时任务；视频在 `/downloads`。

## 13. 免责声明

本项目只做本地采集与媒体库整理，不托管、不分发任何内容。请自行确认内容合法性、年龄限制与站点服务条款；因使用产生的任何后果由使用者承担。





