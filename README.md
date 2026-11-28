# 黄果采集器 · Huangguo Collector

面向 `huangguoai.com`（黄果短剧）、`yeguodj.com`（野果短剧）与欧美影视博主页站点（Pornhub 风格，地址在设置页填写）的本地采集 + 媒体库管理工具。黄果按原创 / 短剧 / 漫剧三个模块采集，野果按探索分类全量采集；两站同名剧自动保留集数更多的版本；欧美影视按「演员订阅」采集博主的全部影片并刮削为 Emby 电影。提供：

- 直观的 Web 媒体库（深色界面，卡片式浏览、搜索、筛选、分页）
- 分区导航：短剧 / 欧美影视 双入口，媒体库、模块与采集入口互相独立，后续可继续加分区
- **演员订阅（欧美影视）**：粘贴博主页地址即可订阅，头像 / 名称自动抓取，扫描遍历全部翻页，新影片自动下载
- 标题翻译开关（免费 Google 翻译），并按 Emby 规范写入译名 / 原文 / 演员头像
- 一键下载整部剧 / 只下载新增分集 / 分批下载
- 批量订阅 / 取消订阅，批量忽略更新、补封面、同步详情、收藏
- 后台下载队列（进度、速度、大小、取消、失败重试）
- 自动生成 Emby / Jellyfin / Kodi 刮削结构（NFO + 海报 + fanart；欧美影视走电影结构）
- 自动打「已下载」标签 + 自定义标签管理
- 更新检测：重新扫描后标记「有更新」，直接补下新增分集
- 多站点：黄果（原创/短剧/漫剧）+ 野果（探索分类）+ 欧美站（博主影片）
- 跨站点同名合并：同名剧只保留集数最多的一个版本，重复条目隐藏但文件不删除
- 重复剧管理页：查看/手动指定主版本，合并标签收藏；同名不同内容可独立保留并分别订阅下载，支持多选批量操作
- 全选当前筛选结果，批量订阅/下载/打标签
- 扫描历史与中断继续，Provider 多站点架构
- 标签排除：把题材/自建标签加入排除列表，相关剧集从媒体库、更新、订阅、统计中隐藏
- 下载队列暂停/继续，当前集完成后不再开始新任务
- 定时任务：每天/每周指定时间自动全量扫库（最新 + 热播 + 精选 / 野果探索 / 演员订阅）
- 订阅追更：订阅的剧在扫库发现新集后自动下载，可单独设置画质；欧美影视按演员订阅自动下载新片
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

## 1.11.2 电影 NFO / 海报修正

- 电影模块 NFO 改为与视频同名（`标题 (年份).nfo`），不再使用 `movie.nfo`，符合 Emby 电影识别惯例。
- 写入新 NFO 时自动清理旧的 `movie.nfo`，避免 Emby 双重识别。
- 电影 `poster.jpg` 保留站点原始横版封面，不再强制转 2:3；剧集模块仍按 2:3 生成。
- 设置页「媒体维护」新增「一键重建 NFO / 海报（已下载）」：按当前规则强制重建 poster、fanart、NFO、演员头像，并清理旧产物。
- 新增重建进度和错误列表，支持按模块组重建。
- 电影卡片改为 16:9 横版网格，详情页使用全宽横幅。
- 72 个单测全部通过。

## 1.11.0 欧美影视：演员订阅 + 电影刮削

- **新适配器 `app/adapters/ph.py`**：接入 Pornhub 风格博主页站点（站点根地址在「欧美影视 → 设置 → 欧美影视站点」填写，支持镜像域名）。
  - 博主页：`#getAvatar` 抓博主头像；`ul/li` 视频卡解析封面（`li/div/div[1]/a`）、标题（`…/div[2]/div[2]/span/a`）、时长、播放量；翻页按钮区解析 `?page=N` 全部页。
  - 视频详情页：标签块（含用户提供的绝对 XPath 兜底）、视频区域 `flashvars` / `mediaDefinitions` 提取 mp4 直链（1080/720/480…）与 hls。
  - 下载画质映射：HEVC / H264 → 最高画质直链，720P → 720 直链（`stream_url` 画质选择接入下载器）。
- **演员订阅**（新表 `creator_subs` + `app/creators.py`）：侧栏「演员订阅」页粘贴博主页地址即可订阅；自动抓名称 / 头像，支持启用 / 停用、自动下载开关、单独画质、每博主页数上限、立即采集（只扫该博主）、刷新资料、批量下载未完成影片。扫描时每个启用的订阅是 `ph_creator` 模块的一个列表（`list_tabs` 动态列表，扫描器自动发现）。
- **影片归属**：`dramas.creator_key` 新列，列表阶段即绑定博主（详情解析失败也能归属）；订阅开启自动下载时，扫描发现的新影片自动入队（`last_auto_count` 等统计回写）。
- **Emby 电影刮削**：欧美影视每部影片独立目录 `博主影片/标题 (年份)/标题 (年份).mp4` + `movie.nfo`；简介用（翻译后的）标题兜底；`<actor>` 写博主，头像下载缓存到 `data/avatars/` 并复制进影片目录 `actors/`；横版封面自动生成 2:3 标准海报（模糊背景 + 原图居中）。
- **翻译**：英文标题走免费 Google 翻译（设置开关），译名写入 `<title>`、原文 `<originaltitle>`。
- **播放**：`get_playback` 返回 hls + mp4 直链；HLS 播放代理白名单自动吸收运行期发现的媒体 CDN 域名（`allowed_hosts()`）。
- 新端点：`GET/POST /api/creator-subs`、`PUT/DELETE /api/creator-subs/{id}`、`POST /api/creator-subs/{id}/scan|refresh`、`POST /api/creator-subs/download`；`/avatars` 静态目录。
- 新增 16 个单测（解析 / 订阅 / 扫描联动 / 电影刮削），共 55 个全部通过。

## 1.10.0 分区导航 / 标题翻译 / 演员

- 新增「导航首页」：打开项目先选分区（短剧 / 欧美影视），进入后各自独立的媒体库、模块计数与采集入口；左侧「切换分区」随时返回。
- **分区完全隔离**：侧边栏状态 / 题材 / 存储统计、设置（下载目录、目录模板、画质、翻译、刮削开关）、下载队列、更新提醒、订阅追更、重复剧、标签、定时任务、扫描历史、封面补全全部按分区计算；未单独设置的分区沿用全局默认值。
- 支持 `?group=film` 深链直接进入某个分区，并记住上次选择。
- 分区与模块由 `MODULE_GROUPS` 与 `MODULES[*].group` 描述：新增站点只需注册一个模块并指定 group，导航、侧栏、采集弹窗自动出现，不用改查询 / 下载 / 刮削代码。
- 「欧美影视」分区已内置博主影片模块（`ph` 源 + `ph_creator` 模块，电影结构），站点地址与博主在页面里配置。
- 媒体库、全选与统计按分区隔离：`GET /api/dramas?group=film`；统计新增 `by_group`。
- 标题翻译（开关控制）：设置页「翻译英文标题」使用免费 Google 翻译，结果缓存在 `translations` 表；译名只影响展示与 NFO，`title` 原文与同名去重逻辑不变。
- NFO 规范化：`<title>` 用译名、`<originaltitle>` 保留站点原文；新增 `<actor><name>/<role>/<thumb>`，演员头像复制到剧集目录的 `actors/` 下。
- 简介为空时 `plot` 用标题兜底（影视类站点常见）。
- 模块支持 `media_type: "tv" | "movie"`：电影模式输出 `<title> (<year>)/<title> (<year>).mp4` + `movie.nfo`（`<movie>` 根节点），不再生成 `Season 01` 和分集 NFO；卡片与详情页显示「电影 / 正片」。
- 适配器扩展点打通：`app/adapters/template.py` 模板 + [docs/adapter-guide.md](docs/adapter-guide.md) 开发指南（接口契约、注册三步、调试与常见坑）；演员头像根据 `actors[].thumb_url` 自动下载并写入 NFO；播放代理白名单从适配器的 `cdn_hosts` 自动扩展。
- 新增 16 个单测（分区隔离、适配器契约端到端、电影模式、演员头像、翻译缓存、NFO），共 39 个全部通过。

## 扩展：接入新站点

1. 复制 `app/adapters/template.py` 为 `app/adapters/mysite.py`，实现 `page_count / fetch_listing_page / fetch_detail / get_playback / save_cover`。
2. 在 `app/config.py` 的 `SOURCES` 加站点、`MODULES` 加模块（`"group": "film"` 即挂到欧美影视分区）。
3. 在 `app/providers.py` 的 `build_providers` 注册实例。

重启后导航首页、侧栏、采集弹窗会自动出现新模块，前端无需改动。完整字段说明和示例见 [docs/adapter-guide.md](docs/adapter-guide.md)。

## 1.9.1 重复剧批量操作

- 重复剧页支持多选：单个勾选、全选、全选隐藏、清空。
- 支持批量「独立保留 / 取消独立」。
- 支持批量「订阅」和「下载」；隐藏版本会自动独立保留。
- 批量订阅/下载使用当前默认画质。
- 批量操作后统一刷新媒体库、统计和重复剧分组。
- 新增 `POST /api/duplicates/batch`。
- 23 个单测全部通过。

## 1.9.0 同名不同内容多版本

- 重复剧管理页新增「独立保留」：同名但内容不同的版本可以同时展示、订阅、下载。
- 每个版本卡片直接提供「订阅 / 下载」按钮；隐藏版本点订阅或下载会自动独立保留。
- 独立保留的版本目录和 NFO 自动带 `[站点ID]` 后缀，避免同名文件互相覆盖。
- 「取消独立」会回到自动去重；已下载文件保留，不删除。
- 默认行为不变：仍然自动按集数最多保留一个版本，只有手动独立保留才显示多个。
- 新增 `POST /api/duplicates/{id}/keep`。
- 23 个单测全部通过。

## 1.8.0 全选 / 重复剧 / 扫描历史

- 媒体库工具栏新增「全选筛选结果」：按当前搜索/模块/状态/标签/题材/评分/年份/排序，一次选中全部结果，批量订阅、下载、打标签直接作用于全部。
- 新增「重复剧」页：查看所有同名分组、来源、集数、下载情况。
- 重复剧可「设为主版本」：会合并重复版本的标签和收藏；已下载文件不删除。
- 支持「恢复自动」：回到按集数最多自动选择，手动指定会被持久化。
- 新增「扫描历史」：记录每次扫描的模块、发现/新增/更新、状态和错误。
- 扫描停止/异常/重启中断后，可在历史里「继续扫描」；已同步详情会自动跳过。
- 新增 `SiteProvider` 注册表，黄果/野果采集器统一由 `app/providers.py` 构建，后续加站点只改注册表与 MODULES。
- 新增 `/api/providers` 查看站点 Provider 状态。
- 22 个单测全部通过。

## 1.7.1 视频头/宽高兜底

- 修复 `[mp4] dimensions not set / Could not write header`：部分 HLS 流在 `-c:v copy` 时拿不到宽高/SPS。
- 输入阶段增加 `-analyzeduration 100M`、`-probesize 100M`，让 ffmpeg 先探测完整流参数。
- 下载重试升级为 4 层：
  1. 原画 HEVC + AAC
  2. 回退 H264 + AAC
  3. 视频转码 H264 + AAC
  4. 视频转码、无音轨
- 转码兜底会补齐宽高和编码参数，输出标准 MP4。
- 音频 ADTS 问题继续走 AAC 转码兜底。
- 19 个单测全部通过。

## 1.7.0 批量订阅

- 媒体库批量条新增「批量订阅」：选多部剧后一次订阅。
- 已全选的剧都订阅时，按钮自动变为「取消订阅」。
- 订阅弹窗可设画质（HEVC/H264/720P）、是否自动下载新集、是否订阅后立即补下已有分集。
- 批量「忽略更新」：一次清掉多部剧的更新标记。
- 批量收藏改为切换：已全收藏时显示「取消收藏」。
- 修复批量「同步详情」只调用黄果 scraper 的问题，野果也可批量同步。
- 移动端批量条支持横向滚动，不再挤出屏幕。
- 19 个单测全部通过。

## 1.6.1 下载修复

- 原画下载改为「视频复制 + 音频转 AAC」，避开 `aac_adtstoasc / Error applying bitstream filters` 这类音轨不兼容报错。
- 第 3 次重试自动降级为无音轨视频，至少保证画面落盘。
- 野果 `episodeAll` 只返回当前分页时，播放接口会带 `episode_sort` 单独取第 N 集，修复「第 28 集没有播放地址」。
- 分集状态明确区分「排队中 / 下载中 / 待下载 / 已下载 / 失败」。
- 详情页分集标题显示：待下载、排队、下载中、失败数量；下载开始/完成会自动刷新状态。
- 19 个单测全部通过。

## 1.6.0 标签排除与队列暂停

- 标签管理页新增「排除标签」：题材标签、自建标签都可排除/恢复。
- 排除后立即生效：媒体库、搜索、筛选、模块计数、更新提醒、订阅追更、统计、同名合并区都不再出现含该标签的剧。
- 排除只隐藏，不删除视频/NFO；恢复标签后剧集重新出现。
- 标签页现在同时展示 49 个题材标签和自建标签，可逐个「筛选 / 排除」。
- 下载队列新增「暂停队列 / 继续队列」；暂停后当前正在下载的集完成，不再启动新任务。
- 排除标签会跳过订阅自动下载，避免后台偷偷下载已屏蔽内容。
- 新增 19 个单测（标签排除 + 队列暂停）。

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

短剧分区：

1. 点右上角 **开始采集**。
2. 勾选模块（原创 / 短剧 / 漫剧 / 野果短剧），页数留空 = 全部。
3. 保持 **同步详情与分集** 勾选（下载必需）。
4. 开始。列表扫描很快；详情按 6 并发同步，全量约 5–15 分钟。
5. 回到媒体库，点卡片进入详情，选画质后「下载全集」。
6. 下载完成后，在 **设置 → 下载根目录** 找到文件；把模块子目录加入 Jellyfin。

欧美影视分区：

1. 首页切到「欧美影视」→ **设置 → 欧美影视站点** 填站点根地址（如 `https://www.pornhub.com`），点「测试欧美站」确认连通。
2. 侧栏 **演员订阅 → ＋添加博主**：粘贴博主页地址（`/model/…`、`/channels/…`、`/users/…` 均可），保存时自动抓名称与头像。
3. 列表里点 **立即采集**（只扫该博主）或「开始采集」（扫全部启用的博主）；新影片自动入库，开启自动下载的直接入队。
4. 标题翻译：设置页打开「翻译英文标题」，详情同步时自动译成中文并写入 NFO。
5. Emby 把 `下载根目录/博主影片` 添加为**电影**库即可（`movie.nfo` + `poster.jpg` + 演员 `actors/`）。

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

欧美影视（电影结构）：

```text
下载根目录/
  博主影片/
    影片标题 (2025)/
      movie.nfo
      poster.jpg          2:3 标准海报（横版封面自动转换）
      fanart.jpg
      影片标题 (2025).mp4
      actors/Jane Doe.jpg 博主头像
```

- `<movie>` 根节点，`<title>` 用译名、`<originaltitle>` 保留原文，简介为空时用标题兜底。
- `<actor>` 写博主（演员）与头像；Emby 直接把 `博主影片` 目录添加为电影库。

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
    adapters/
      template.py   适配器模板
      ph.py         欧美影视博主页适配器
    creators.py     演员订阅管理（博主 CRUD + 头像）
    providers.py    站点 Provider 注册表
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
  data/             运行时生成：DB、封面、头像、临时文件
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
| GET | `/api/drama-ids` | 当前筛选的全部剧集 ID（全选筛选结果） |
| GET | `/api/duplicates` | 同名重复剧分组 |
| POST | `/api/duplicates/{id}/prefer` | 设为主版本并合并标签/收藏 |
| POST | `/api/duplicates/{id}/keep` | 独立保留/取消独立（同名多版本） |
| POST | `/api/duplicates/batch` | 重复剧批量保留/取消/订阅/下载 |
| POST | `/api/duplicates/{id}/auto` | 恢复自动选择主版本 |
| GET | `/api/scans` | 扫描历史 |
| POST | `/api/scans/{id}/resume` | 继续上次扫描 |
| GET | `/api/providers` | 站点 Provider 状态 |
| GET | `/api/dramas/{id}` | 详情 + 分集 + 标签 + 空间估算；重复条目返回 `duplicate_preferred` |
| POST | `/api/dramas/{id}/download` | `{scope: all/new/eps, eps, quality}` |
| POST | `/api/dramas/batch` | 批量下载/订阅/取消订阅/同步/打标签/收藏/忽略更新 |
| GET | `/api/updates` | 有更新的剧 |
| GET | `/api/subscriptions` | 订阅列表 + 更新/自动下载统计 |
| POST | `/api/dramas/{id}/subscribe` | `{enabled, quality, auto_download, download_now}` |
| DELETE | `/api/subscriptions/{id}` | 取消订阅 |
| POST | `/api/subscriptions/download` | 下载全部订阅更新 |
| GET/POST | `/api/creator-subs` | 演员订阅列表 / 添加博主 `{url, quality, max_pages, auto_download, enabled}` |
| PUT/DELETE | `/api/creator-subs/{id}` | 更新 / 删除演员订阅 |
| POST | `/api/creator-subs/{id}/scan` | 立即采集该博主（只扫他的全部翻页） |
| POST | `/api/creator-subs/{id}/refresh` | 重新抓取博主名称与头像 |
| POST | `/api/creator-subs/download` | 下载全部博主未完成影片 |
| GET/POST/PUT/DELETE | `/api/schedules` | 定时任务 CRUD |
| POST | `/api/schedules/{id}/run` | 立即执行定时任务 |
| POST | `/api/settings/check-path` | 校验/创建下载目录并返回剩余空间 |
| GET | `/api/fs/list` | 目录选择器：列出磁盘与子目录 |
| POST | `/api/fs/mkdir` | 在指定目录下新建文件夹 |
| GET | `/api/covers` | 集封面补全进度 |
| POST | `/api/covers/backfill` | `{drama_ids}` 或 `{all:true}` 补全集封面 |
| GET | `/api/tags` | 标签列表 + 已排除标签 |
| POST | `/api/excluded-tags` | `{name}` 排除标签 |
| DELETE | `/api/excluded-tags?name=` | 恢复标签 |
| POST | `/api/downloads/pause` / `resume` | 暂停 / 继续下载队列 |
| GET | `/api/site-check` | 测试站点连通性；`?source=huangguo|yeguo|ph` |
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

- **镜像**：`jokermelove/huangguo-collector:1.11.1`（另有 `latest`）
- **GitHub**：<https://github.com/king-joker-z/huangguo-collector>
- **Actions**：推送到 `main` 自动构建并推送 Docker Hub，也可在 Actions 页手动 `Run workflow`

```sh
docker pull jokermelove/huangguo-collector:1.11.1
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
docker build --platform linux/amd64 -t jokermelove/huangguo-collector:1.11.1 .
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


