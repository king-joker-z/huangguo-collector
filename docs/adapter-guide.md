# 站点适配器开发指南

项目把「站点」和「模块」分开：

```
分区(group)          模块(module)                 适配器(source)
├─ duanju 短剧   ── original / duanju / manju / yeguo ── huangguo / yeguo
└─ film 欧美影视 ── ph_creator（演员订阅驱动）    ── ph（博主页适配器）
```

- **分区**：导航首页上的入口，数据、设置、队列、标签全部按分区隔离。
- **模块**：分区里的栏目，决定侧栏条目、采集弹窗和下载目录名。
- **适配器**：真正抓站点的类，扫描 / 下载 / 播放 / 刮削都只通过它。

一个适配器类只需要实现 5 个方法。复制 [app/adapters/template.py](../app/adapters/template.py) 就能开始。

---

## 1. 必须实现的接口

```python
class MyScraper:
    site_base = "https://video.example.com"   # 站点根
    check_url = site_base + "/"                # 「测试站点连接」用
    cdn_hosts = ("cdn.example.com",)           # 可选：播放代理白名单

    def __init__(self, db, settings): ...      # 签名固定
    def page_count(self, module: str, tab: str = "latest") -> int: ...
    def fetch_listing_page(self, module: str, page: int, tab: str = "latest") -> tuple[list[dict], int]: ...
    def fetch_detail(self, site_id: str) -> dict: ...
    def get_playback(self, site_id: str, ep: int) -> dict: ...
    def save_cover(self, site_id: str, cover_url: str) -> str: ...
```

可选：

| 成员 | 用途 |
|---|---|
| `cdn_headers(payload) -> dict` | ffmpeg / HLS 代理请求 CDN 时带的鉴权头（Referer、`x-cdn-auth` 等） |
| `stream_url(payload, quality) -> str` | 自定义画质选择；不实现时上层直接读 `payload["streams"]` |
| `tab` 参数 | 一个模块下多种列表（最近更新 / 热播 / 精选），默认 `latest` |

## 2. 数据契约

### 列表卡片（`fetch_listing_page` 返回）

| 字段 | 必填 | 说明 |
|---|---|---|
| `site_id` | ✅ | **全局唯一**，见下方「site_id 必须加前缀」 |
| `module` | ✅ | 传入的 module，原样带回 |
| `title` | ✅ | 展示标题；英文标题会按开关自动翻译 |
| `url` | ✅ | 详情页地址 |
| `cover_url` | | 封面原图 URL（可懒加载 `data-src`） |
| `score` | | 浮点，没有就 `None` |
| `total_ep` | | 集数；单片站点填 1 |
| `tags` / `badges` / `views` | | 题材标签、角标、播放量文本 |
| `source` | | 适配器 key，必须与 `config.SOURCES` 一致，否则下载会走错 provider |

### 详情（`fetch_detail` 返回）

```python
{
  "site_id": "mysite_123",
  "title": "...",
  "description": "",            # 空字符串也可以：NFO 会用标题兜底
  "cover_url": "https://...",
  "score": 8.5, "year": 2026, "premiered": "2026-01-02",
  "author": "", "status_text": "", "views": "",
  "duration_text": "", "category_text": "",
  "tags": ["剧情"],
  "episodes": [                 # ep 必须从 1 连续递增
      {"ep": 1, "site_video_id": "abc", "title": "第1集", "synopsis": "", "duration": 0}
  ],
  "actors": [                   # 可选：演员头像
      {"name": "Jane Doe", "role": "演员", "thumb_url": "https://.../avatar.jpg"}
  ],
}
```

`actors[].thumb_url` 会被自动下载到 `data/avatars/`，NFO 里写成
`<actor><name>…</name><role>…</role><thumb>actors/…jpg</thumb></actor>`。

### 播放（`get_playback` 返回）

```python
{
  "id": "mysite_123",
  "streams": {"main": "https://.../index.m3u8", "hevc": "..."},
  "cdn": {"authKey": "x-cdn-auth", "authValue": "..."},
}
```

- 下载读 `streams.main`；有 `streams.hevc` 时「HEVC 原画」才可选。
- `cdn` 里的键值会作为请求头传给 ffmpeg 和播放代理。
- HLS 里的相对路径由代理自动补全，不用处理。

### 封面（`save_cover`）

下载并转成 JPEG 存到 `data/covers/<site_id>.jpg`，然后 **必须** 回写数据库：

```python
self.db.execute("UPDATE dramas SET cover_path=? WHERE site_id=?", (str(target), str(site_id)))
```

## 3. `site_id` 必须加前缀

`dramas.site_id` 是全局唯一键。不同站点数字 ID 很容易撞车，所以适配器内部要
自己加命名空间，例如：

```python
SITE_PREFIX = "mysite_"
def _sid(raw: str) -> str:
    return raw if raw.startswith(SITE_PREFIX) else SITE_PREFIX + raw
```

`fetch_detail` / `get_playback` 收到的 `site_id` 已经带前缀，去前缀后拼真实 URL。

## 4. 注册三步

以「欧美影视」分区为例，内置的 `ph` 源就是这么接的：`app/adapters/ph.py`
（博主页 `#getAvatar` 头像 + `ul/li` 视频卡 + viewkey 详情 + `mediaDefinitions`
下载直链，站点地址在设置页填写，采集入口由「演员订阅」动态提供——适配器实现
`list_tabs(module)` 时，扫描器会用它替代静态 TAB_VARIANTS）。

### ① 写适配器

`app/adapters/mysite.py`，参考模板实现 5 个方法。

### ② 配置站点、模块

`app/config.py`：

```python
SOURCES = {
    ...
    "mysite": {"label": "MySite", "base": "https://video.example.com"},
}

MODULES = {
    ...
    "mysite_film": {
        "label": "影视",
        "icon": "▷",
        "base": "/",
        "desc": "MySite 影视",
        "source": "mysite",
        "group": "film",          # 关键：挂到哪个分区
        "media_type": "movie",    # movie=电影结构；tv=剧集结构（默认）
    },
}
```

`group` 决定它出现在导航首页哪个入口。`film` 分区目前没有模块，加上这条后
导航卡片、侧栏、采集弹窗都会自动出现，**前端不用改**。

`media_type` 决定刮削结构：

| 值 | 目录与文件 | NFO |
|---|---|---|
| `tv`（默认） | `{module}/{title} ({year})/Season 01/{title} - S01E01.mp4` | `tvshow.nfo` + 分集 `.nfo` |
| `movie` | `{module}/{title} ({year})/{title} ({year}).mp4` | `movie.nfo`（`<movie>` 根节点，无分集 NFO） |

电影模式同样自动生成 `poster.jpg`、`fanart.jpg`、`<actor>` 头像，Emby 直接把
「影视」目录添加为电影库即可。

### ③ 注册 provider

`app/providers.py`：

```python
def build_providers(db, settings):
    from .adapters.mysite import MyScraper
    ...
    return {
        "huangguo": Scraper(db, settings),
        "yeguo": YeguoScraper(db, settings),
        "mysite": MyScraper(db, settings),
    }
```

### ④（可选）多列表

`app/scan.py`：

```python
TAB_VARIANTS = {
    ...
    "mysite_film": [("latest", "最近更新")],
}
```

不写也行，默认只扫 `latest` 一个列表。

重启 `python -m app` → 打开 <http://127.0.0.1:8765> → 导航首页出现「欧美影视」→ 进入 → 「开始采集」。

## 5. 上层已经帮你做的事

| 能力 | 说明 |
|---|---|
| 扫描与去重 | 按 `site_id` 增量写入，`total_ep` 变大自动标「有更新」 |
| 同名合并 | 跨站点同名按归一化标题去重，保留集数多的版本 |
| 标题翻译 | 设置页开关打开后，详情同步时自动翻译英文标题，写入 NFO `<title>`，`<originaltitle>` 保留原文 |
| 演员头像 | `actors[].thumb_url` 自动下载缓存，NFO 写 `<actor>` 并复制到剧集目录 `actors/` |
| 下载 | m3u8 → ffmpeg 合并，HEVC/H264/720P，失败自动降级重试 |
| Emby 刮削 | `{module}/{title} ({year})` + `tvshow.nfo` / `movie.nfo` + `poster.jpg` + `fanart.jpg`；`media_type: movie` 时自动切成电影结构 |
| 分区设置 | 下载根目录、画质、目录模板、NFO 开关都按分区独立；`nfo.show_folder()` 自动读剧集所属分区 |
| 播放 | 网页内 HLS 代理，自动改写分片地址；`cdn_hosts` 决定哪些域允许代理 |
| 订阅 | 订阅后扫库发现新集自动下载，画质按订阅保存 |

## 6. 调试建议

1. **先跑页数 1**：采集弹窗里「页数」填 1，只验证列表 + 详情 + 一集下载。
2. **解析函数单独测**：把 HTML 存成 fixture，直接调 `parse_listing` / `parse_detail`，不要联网，写成 `tests/test_mysite.py`。
3. **XPath → CSS**：浏览器复制出来的 XPath 可以用 `lxml` 的 `soup.select_one()` 配合 CSS；实在要用 XPath，`lxml.html.fromstring(html).xpath(...)` 也能拿到节点。
4. **懒加载封面**：优先取 `data-src`，再退回 `src`。
5. **防盗链**：`save_cover`、`get_playback` 记得带 `Referer`；必要时在 `cdn_headers` 里加站点要求的 token。
6. **接口返回非 JSON / 加密**：参考 `app/yeguo.py` 里 AES 解密的写法，解密后再构造上面的契约。
7. **分页上限**：`page_count` 返回总页数；站点没有分页就返回 1。

## 7. 边界

适配器只负责「按公开页面结构取数据」。请只采集你有权访问的内容，遵守站点条款和当地法律；站点改版导致解析失效时，只需改这一个文件。
