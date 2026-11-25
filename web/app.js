/* 黄果媒体库 · 前端（无构建依赖） */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const API = '/api';
  const QUALITY_MBPS = { hevc: 2.36, h264: 8.3, '720p': 2.2 };
  const QUALITY_LABEL = { hevc: 'HEVC 原画', h264: 'H264 原画', '720p': '720P 压缩' };

  const MODULES = [
    { key: 'original', label: '原创', icon: '◆' },
    { key: 'duanju', label: '短剧', icon: '▶' },
    { key: 'manju', label: '漫剧', icon: '✦' },
    { key: 'yeguo', label: '野果短剧', icon: '🍑' },
  ];
  const STATUSES = [
    { key: 'all', label: '全部', cls: '' },
    { key: 'none', label: '未下载', cls: 'none' },
    { key: 'downloading', label: '下载中', cls: 'down' },
    { key: 'partial', label: '部分下载', cls: 'partial' },
    { key: 'done', label: '已完成', cls: 'done' },
    { key: 'update', label: '有更新', cls: 'update' },
    { key: 'favorite', label: '收藏', cls: 'fav' },
    { key: 'subscribed', label: '已订阅', cls: 'sub' },
  ];

  const state = {
    route: { name: 'library' },
    module: 'all',
    q: '',
    status: 'all',
    tag: '',
    siteTag: '',
    scoreMin: 0,
    year: 0,
    sort: 'updated',
    page: 1,
    per_page: 36,
    items: [],
    total: 0,
    pages: 1,
    selected: new Set(),
    selectMode: false,
    tags: [],
    excludedTags: [],
    tagSearch: '',
    stats: {},
    activity: { scan: { state: 'idle' }, tasks: [], summary: {} },
    health: {},
    settings: {},
    detail: null,
    detailActiveCount: 0,
    epSelected: new Set(),
    tasks: [],
    history: [],
    subscriptions: [],
    schedules: [],
    covers: { state: 'idle' },
    lastCoverState: 'idle',
    lastScanState: 'idle',
    failureDismissed: false,
    quality: localStorage.getItem('hg_quality') || 'hevc',
  };

  // ------------------------------------------------------------------ utils
  function h(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function fmtBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    const value = n / Math.pow(1024, i);
    return `${value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`;
  }

  function fmtDuration(seconds) {
    const n = Number(seconds) || 0;
    if (!n) return '';
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  }

  function pct(done, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  }

  function shortTime(value) {
    if (!value) return '';
    const text = String(value).replace('T', ' ');
    const match = text.match(/\d{4}-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    return match ? `${match[1]}-${match[2]} ${match[3]}:${match[4]}` : text.slice(0, 16);
  }

  function debounce(fn, wait = 280) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
  }

  async function api(path, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(options.headers || {}) };
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(API + path, opts);
    let data = null;
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const message = (data && (data.detail || data.message)) || `请求失败 (${response.status})`;
      throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
    }
    return data;
  }

  function toast(message, type = '') {
    const root = $('#toasts');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(6px)';
      el.style.transition = '.25s';
      setTimeout(() => el.remove(), 260);
    }, 3600);
  }

  function coverHTML(item, extra = '') {
    const src = item.cover || item.cover_url || '';
    const char = h((item.title || '?').trim().charAt(0));
    return `${src ? `<img src="${h(src)}" alt="" loading="lazy" ${extra} onerror="this.style.display='none'">` : ''}
      <div class="ph">${char}</div>`;
  }

  function moduleLabel(key) {
    return (MODULES.find((m) => m.key === key) || {}).label || key;
  }

  function statusLabel(key) {
    return (STATUSES.find((s) => s.key === key) || {}).label || key;
  }

  function downloadStateText(item) {
    if (item.download_state === 'done') return '已下载';
    if (item.download_state === 'partial') return `已下载 ${item.done_ep}/${item.total_ep}`;
    if (item.download_state === 'downloading') return '下载中';
    return '未下载';
  }

  // -------------------------------------------------------------- navigation
  function parseHash() {
    const raw = (location.hash || '#/library').replace(/^#/, '');
    const parts = raw.split('/').filter(Boolean);
    if (!parts.length) return { name: 'library' };
    if (parts[0] === 'module' && parts[1]) return { name: 'module', module: parts[1] };
    if (parts[0] === 'drama' && parts[1]) return { name: 'drama', id: Number(parts[1]) };
    if (['library', 'updates', 'downloads', 'tags', 'settings', 'subscriptions', 'schedules'].includes(parts[0])) return { name: parts[0] };
    return { name: 'library' };
  }

  function navigate(path) {
    if (location.hash === '#' + path) return;
    location.hash = path;
  }

  function updateDocumentTitle() {
    const r = state.route;
    if (r.name === 'drama') document.title = `${state.detail ? state.detail.title : '剧集详情'} · 黄果媒体库`;
    else if (r.name === 'downloads') document.title = '下载队列 · 黄果媒体库';
    else if (r.name === 'updates') document.title = '更新提醒 · 黄果媒体库';
    else if (r.name === 'tags') document.title = '标签管理 · 黄果媒体库';
    else if (r.name === 'settings') document.title = '设置 · 黄果媒体库';
    else if (r.name === 'subscriptions') document.title = '订阅追更 · 黄果媒体库';
    else if (r.name === 'schedules') document.title = '定时任务 · 黄果媒体库';
    else document.title = '黄果媒体库 · Huangguo Library';
  }

  // ------------------------------------------------------------------ chrome
  function filterCount() {
    let n = 0;
    if (state.module !== 'all') n += 1;
    if (state.status !== 'all') n += 1;
    if (state.tag) n += 1;
    if (state.siteTag) n += 1;
    if (state.scoreMin) n += 1;
    if (state.year) n += 1;
    if (state.q) n += 1;
    return n;
  }

  function updateHeader() {
    const s = state.stats || {};
    const status = s.status || {};
    const episodesDone = s.episodes_done || 0;
    $('#hs-total').textContent = s.total || 0;
    $('#hs-done').textContent = episodesDone;
    $('#hs-updates').textContent = status.update || 0;
    $('#hs-subs').textContent = s.subscriptions || 0;
    const updated = s.last_scraped_at ? `更新于 ${shortTime(s.last_scraped_at)}` : '等待首次采集';
    $('#updated-at').textContent = updated;
    const count = filterCount();
    const badge = $('#filter-count');
    badge.textContent = count;
    badge.classList.toggle('hidden', !count);
  }

  function updateSidebarCounts() {
    const s = state.stats || {};
    const status = s.status || {};
    const byModule = s.by_module || {};
    if (!$('#status-list')) return;
    STATUSES.forEach((item) => {
      const row = $(`#status-list [data-status="${item.key}"]`);
      if (!row) return;
      row.classList.toggle('on', state.status === item.key);
      const count = row.querySelector('i');
      if (count) count.textContent = status[item.key] || 0;
    });
    MODULES.forEach((mod) => {
      const row = $(`#module-list [data-module="${mod.key}"]`);
      if (!row) return;
      row.classList.toggle('on', state.module === mod.key);
      const count = row.querySelector('i');
      if (count) count.textContent = byModule[mod.key] || 0;
    });
    $$('#tag-list [data-tag]').forEach((row) => {
      row.classList.toggle('on', !state.siteTag && state.tag === row.dataset.tag);
    });
    $$('#tag-list [data-site-tag]').forEach((row) => {
      row.classList.toggle('on', state.siteTag === row.dataset.siteTag);
    });

    $('#nav-updates').textContent = status.update || 0;
    const activeTasks = ((state.activity && state.activity.summary) || {}).downloading || 0;
    const queuedTasks = ((state.activity && state.activity.summary) || {}).queued || 0;
    $('#nav-tasks').textContent = activeTasks + queuedTasks;
    $('#nav-subs').textContent = s.subscriptions || 0;
    $$('.snav').forEach((link) => link.classList.toggle('on', link.dataset.nav === state.route.name));

    const used = s.storage_bytes || 0;
    const free = (state.health && state.health.disk_free) || 0;
    $('#storage-used').textContent = fmtBytes(used);
    $('#storage-free').textContent = free ? `磁盘剩余 ${fmtBytes(free)}` : '磁盘剩余 --';
    $('#storage-eps').textContent = `${s.episodes_done || 0} 集`;
    const total = used + free;
    $('#storage-fill').style.width = total > 0 ? `${Math.min(100, (used / total) * 100).toFixed(2)}%` : '0%';
    $('#module-total').textContent = s.total || 0;
    $('#tag-total').textContent = ((state.tags || []).length + ((s.site_tags || []).length));
    if (state.health) {
      const ok = state.health.ffmpeg;
      $('#health-dot').style.background = ok ? 'var(--ok)' : 'var(--accent)';
      $('#health-text').textContent = ok ? `v1.6.1 · ${state.health.workers || 0} 路下载` : 'ffmpeg 未就绪';
    }
  }

  function renderSidebar() {
    const status = (state.stats && state.stats.status) || {};
    $('#status-list').innerHTML = STATUSES.map((item) => `
      <button class="sseg ${item.cls} ${state.status === item.key ? 'on' : ''}" data-action="set-status" data-status="${item.key}">
        <span class="dot"></span><span>${item.label}</span><i>${status[item.key] || 0}</i>
      </button>`).join('');

    const byModule = (state.stats && state.stats.by_module) || {};
    const cross = (state.stats && state.stats.cross_listed) || 0;
    const duplicates = (state.stats && state.stats.duplicates) || 0;
    $('#module-list').innerHTML = MODULES.map((mod) => `
      <button class="smod ${state.module === mod.key ? 'on' : ''}" data-action="set-module" data-module="${mod.key}">
        <span class="ic">${mod.icon}</span><span>${mod.label}</span><i>${byModule[mod.key] || 0}</i>
      </button>`).join('') + (cross ? `<div class="side-note">跨模块 ${cross} 部同时收录</div>` : '') +
      (duplicates ? `<div class="side-note">同名合并 ${duplicates} 部</div>` : '');

    renderTagList();
    updateSidebarCounts();
  }

  function renderTagList() {
    const wrap = $('#tag-list');
    if (!wrap) return;
    const keyword = state.tagSearch.trim().toLowerCase();
    const excluded = new Set((state.excludedTags || []).map((t) => t.name));
    const siteTags = ((state.stats && state.stats.site_tags) || [])
      .filter((t) => !keyword || t.name.toLowerCase().includes(keyword));
    const customTags = (state.tags || [])
      .slice().sort((a, b) => (b.count || 0) - (a.count || 0))
      .filter((t) => !excluded.has(t.name))
      .filter((t) => !keyword || t.name.toLowerCase().includes(keyword));
    if (!siteTags.length && !customTags.length) {
      wrap.innerHTML = `<div class="tag-empty">${state.tags.length || (state.stats.site_tags || []).length ? '没有匹配的标签' : '还没有标签'}</div>`;
      return;
    }
    const rows = [];
    if (customTags.length) {
      rows.push('<div class="tag-group">自建标签</div>');
      rows.push(customTags.map((tag) => `
        <button class="tag-row ${state.tag === tag.name ? 'on' : ''}" data-action="set-tag" data-tag="${h(tag.name)}">
          <span class="tag-dot" style="background:${h(tag.color || '#8d94a6')}"></span>
          <span class="tag-name">${h(tag.name)}</span><i>${tag.count || 0}</i>
        </button>`).join(''));
    }
    if (siteTags.length) {
      rows.push('<div class="tag-group">题材</div>');
      rows.push(siteTags.map((tag) => `
        <button class="tag-row ${state.siteTag === tag.name ? 'on' : ''}" data-action="set-site-tag" data-site-tag="${h(tag.name)}">
          <span class="tag-dot" style="background:#7db6d9"></span>
          <span class="tag-name">${h(tag.name)}</span><i>${tag.count || 0}</i>
        </button>`).join(''));
    }
    wrap.innerHTML = rows.join('');
  }

  async function loadStats() {
    try { state.stats = await api('/stats'); } catch (_) {}
    updateHeader();
    if ($('#status-list') && !$('#status-list').children.length) renderSidebar();
    else renderTagList();
    updateSidebarCounts();
  }

  async function loadTags() {
    try {
      const res = await api('/tags');
      state.tags = res.tags || [];
      state.excludedTags = res.excluded || [];
    } catch (_) {}
    renderTagList();
    updateSidebarCounts();
  }

  async function loadHealth() {
    try { state.health = await api('/health'); } catch (_) {}
    updateSidebarCounts();
  }

  async function refreshActivity() {
    try {
      const data = await api('/activity');
      state.activity = data;
      state.stats = data.stats || state.stats;
      updateHeader();
      updateSidebarCounts();
      updateScanBanner(data.scan);
      updateCoverBanner(data.covers);
      updateFailureBanner(data.summary);
      if (state.route.name === 'downloads') updateTaskRows(data.tasks || []);
      if (state.route.name === 'drama' && state.detail) {
        const activeForDetail = (data.tasks || []).filter((t) => Number(t.drama_id) === Number(state.detail.id));
        activeForDetail.forEach((task) => updateEpisodeTile(task.ep, task.state));
        if (state.detailActiveCount > 0 && activeForDetail.length === 0) {
          loadDetail(state.detail.id);
        }
        state.detailActiveCount = activeForDetail.length;
      }
    } catch (_) {}
  }

  function updateCoverBanner(covers) {
    const banner = $('#cover-banner');
    if (!banner) return;
    if (!covers || covers.state !== 'running') {
      banner.classList.add('hidden');
      if (state.lastCoverState === 'running' && covers && covers.state !== 'running') {
        toast(covers.message || '封面补全完成', covers.state === 'error' ? 'err' : 'ok');
        if (state.route.name === 'drama' && state.detail) loadDetail(state.detail.id);
      }
      state.lastCoverState = covers ? covers.state : 'idle';
      return;
    }
    state.lastCoverState = 'running';
    banner.classList.remove('hidden');
    $('#cover-message').textContent = covers.message || '正在处理…';
    const total = Number(covers.total) || 0;
    const done = Number(covers.done) || 0;
    $('#cover-counter').textContent = total ? `${done} / ${total} 集` : '';
    $('#cover-fill').style.width = total ? `${Math.min(100, (done / total) * 100).toFixed(1)}%` : '3%';
  }

  function updateFailureBanner(summary) {
    const banner = $('#failure-banner');
    if (!banner) return;
    const failed = Number(summary && summary.failed) || 0;
    const hide = !failed || state.failureDismissed || state.route.name === 'downloads';
    banner.classList.toggle('hidden', hide);
    if (failed) $('#failure-count').textContent = failed;
  }

  function updateScanBanner(scan) {
    const banner = $('#scan-banner');
    if (!scan || scan.state !== 'running') {
      banner.classList.add('hidden');
      if (state.lastScanState === 'running' && scan && scan.state !== 'running') {
        const msg = scan.state === 'done' ? '采集完成' : scan.state === 'error' ? '采集异常' : '采集已停止';
        toast(`${msg}：${scan.message || ''}`, scan.state === 'error' ? 'err' : 'ok');
        loadStats().then(() => {
          if (['library', 'module', 'updates', 'subscriptions', 'schedules'].includes(state.route.name)) render();
        });
      }
      state.lastScanState = scan ? scan.state : 'idle';
      return;
    }
    state.lastScanState = 'running';
    banner.classList.remove('hidden');
    $('#scan-title').textContent = scan.module_label ? `正在采集「${scan.module_label}」` : '正在采集';
    $('#scan-message').textContent = scan.message || '准备中…';
    const detailTotal = scan.detail_total || 0;
    const detailDone = scan.detail_done || 0;
    if (detailTotal) {
      $('#scan-counter').textContent = `${detailDone} / ${detailTotal} 部`;
      $('#scan-fill').style.width = `${Math.min(100, (detailDone / detailTotal) * 100).toFixed(1)}%`;
    } else {
      const pages = scan.pages || 0;
      const done = scan.done_pages || 0;
      $('#scan-counter').textContent = pages ? `${done} / ${pages} 页` : '';
      $('#scan-fill').style.width = pages ? `${Math.min(100, (done / pages) * 100).toFixed(1)}%` : '3%';
    }
  }

  // ------------------------------------------------------------------ library
  async function loadLibrary() {
    const token = ++renderToken;
    const view = $('#view');
    view.innerHTML = `<div class="toolbar"><div class="skeleton" style="width:90px;height:20px"></div><div class="skeleton" style="width:110px;height:32px"></div><div class="skeleton" style="width:520px;height:32px"></div></div>
      <div class="grid">${Array.from({ length: 12 }).map(() => '<div class="card"><div class="skeleton sk-cover"></div><div style="padding:10px"><div class="skeleton sk-line"></div><div class="skeleton sk-line" style="width:60%"></div></div></div>').join('')}</div>`;
    const params = new URLSearchParams({
      module: state.module,
      q: state.q,
      status: state.status,
      tag: state.tag,
      site_tag: state.siteTag,
      sort: state.sort,
      page: String(state.page),
      per_page: String(state.per_page),
      score_min: String(state.scoreMin || 0),
      year: String(state.year || 0),
    });
    try {
      const data = await api('/dramas?' + params.toString());
      if (token !== renderToken) return;
      state.items = data.items || [];
      state.total = data.total || 0;
      state.pages = data.pages || 1;
      state.page = data.page || 1;
      renderLibraryView();
    } catch (err) {
      if (token === renderToken) view.innerHTML = emptyHTML('加载失败', err.message, '重试', 'reload');
    }
  }

  function renderLibraryView() {
    const view = $('#view');
    const totalInLibrary = Number((state.stats || {}).total) || 0;
    const empty = totalInLibrary > 0
      ? emptyHTML('没有符合条件的剧集', '试试清空搜索或筛选条件。', '清除筛选', 'clear-all')
      : onboardingHTML();
    view.innerHTML = `${totalInLibrary > 0 ? toolbarHTML() + activeFiltersHTML() : ''}
      ${state.items.length ? `<div class="grid ${state.selectMode ? 'selecting' : ''}">${state.items.map(cardHTML).join('')}</div>` : empty}
      ${state.items.length ? pagerHTML() : ''}`;
    syncSelectionDOM();
  }

  function onboardingHTML() {
    return `<div class="onboarding">
      <div class="onb-glyph">▦</div>
      <h3>先把媒体库扫进来</h3>
      <p>第一次使用按这三步走，后面基本不用管。</p>
      <ol>
        <li><b>扫描站点</b>：选择原创 / 短剧 / 漫剧，第一次建议全量。</li>
        <li><b>设置下载目录</b>：填 NAS 路径，或点“浏览”选择文件夹。</li>
        <li><b>订阅 + 定时</b>：定时扫库，发现新集自动下载。</li>
      </ol>
      <div class="onb-actions">
        <button class="btn pri" data-action="open-scan">开始采集</button>
        <a class="btn" href="#/settings">设置下载目录</a>
        <a class="btn" href="#/schedules">创建定时任务</a>
      </div>
    </div>`;
  }

  function pageAllSelected() {
    return state.items.length > 0 && state.items.every((item) => state.selected.has(item.id));
  }

  function toolbarHTML() {
    const s = state.stats || {};
    const done = s.episodes_done || 0;
    const total = s.episodes_total || 0;
    const progress = pct(done, total);
    const sorts = [
      ['updated', '最近更新'], ['added', '最近入库'], ['score', '评分最高'],
      ['episodes', '集数最多'], ['progress', '完成度'], ['title', '标题'],
    ];
    return `<div class="toolbar">
      <div class="count">当前 <b>${state.total}</b> 条</div>
      <button class="btn sm mobile-filter" data-action="open-filter">筛选</button>
      <select class="select" id="sort-filter">
        ${sorts.map(([key, label]) => `<option value="${key}" ${state.sort === key ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
      <button class="toggle ${state.status === 'none' ? 'on' : ''}" data-action="toggle-unwatched"><span class="sw"></span>只看未下载</button>
      <button class="toggle ${pageAllSelected() ? 'on' : ''}" data-action="select-page"><span class="sw"></span>全选当前</button>
      <div class="progress"><span>已下载 <b>${done}</b> / ${total} 集</span><div class="pbar"><i style="width:${progress}%"></i></div></div>
    </div>`;
  }

  function activeFiltersHTML() {
    const chips = [];
    if (state.module !== 'all') chips.push(['module', `模块：${moduleLabel(state.module)}`]);
    if (state.status !== 'all') chips.push(['status', `状态：${statusLabel(state.status)}`]);
    if (state.tag) chips.push(['tag', `标签：${state.tag}`]);
    if (state.siteTag) chips.push(['siteTag', `题材：${state.siteTag}`]);
    if (state.scoreMin) chips.push(['score', `评分 ≥ ${state.scoreMin.toFixed(1)}`]);
    if (state.year) chips.push(['year', `年份：${state.year}`]);
    if (state.q) chips.push(['q', `搜索：${state.q}`]);
    if (!chips.length) return '';
    return `<div class="activefilters">
      ${chips.map(([kind, label]) => `<span class="fchip"><b>${h(label)}</b><span class="x" data-action="clear-filter" data-kind="${kind}" title="移除">×</span></span>`).join('')}
      <button class="fclear" data-action="clear-all">清除全部</button>
    </div>`;
  }

  function cardHTML(item) {
    const selected = state.selected.has(item.id);
    const total = item.total_ep || 0;
    const done = item.done_ep || 0;
    const progress = pct(done, total);
    const siteTags = (item.tags_site || []).slice(0, 2);
    const custom = (item.tags || []).filter((t) => t.kind !== 'system').slice(0, 1);
    const chips = siteTags.map((t) => `<span class="chip tag">${h(t)}</span>`).join('') +
      custom.map((t) => `<span class="chip custom">${h(t.name)}</span>`).join('');
    const stateCls = item.download_state === 'done' ? 'ok' : item.download_state === 'partial' ? 'warn' : item.has_update ? 'accent' : '';
    const date = item.premiered || (item.year ? String(item.year) : '');
    const scope = item.has_update ? 'new' : 'all';
    const complete = total > 0 && done >= total;
    const dlLabel = item.has_update ? '补下更新' : complete ? '已下载' : done > 0 ? '继续下载' : '下载整部';
    const moduleText = ((item.module_labels && item.module_labels.length) ? item.module_labels : [item.module_label || moduleLabel(item.module)]).join(' · ');
    return `<article class="card ${selected ? 'sel' : ''}" data-id="${item.id}">
      <div class="cover">
        ${coverHTML(item)}
        <div class="cover-top">
          <span class="code">#${h(item.raw_site_id || item.site_id)}</span>
          ${item.score ? `<span class="score">${Number(item.score).toFixed(1)}<span>分</span></span>` : ''}
        </div>
        <div class="check" data-action="toggle-select" data-id="${item.id}"></div>
        ${item.has_update ? '<span class="update-ribbon">有更新</span>' : ''}
        <div class="cover-bottom">
          <span class="cbadge module ${item.module}">${h(moduleText)}</span>
          <span class="cbadge ep">${total ? `全 ${total} 集` : '待同步'}</span>
        </div>
      </div>
      <div class="body">
        <h3 class="title" title="${h(item.title)}">${h(item.title)}</h3>
        <div class="meta">
          ${date ? `<span>${h(date)}</span>` : ''}
          ${item.views ? `<span>${h(item.views)} 播放</span>` : ''}
          <span class="${stateCls}">${h(downloadStateText(item))}</span>
        </div>
        <div class="chips">${chips}</div>
        <div class="card-progress ${total && done >= total ? 'done' : ''}">
          <div class="bar"><i style="width:${progress}%"></i></div><span>${done}/${total || '?'} 集</span>
        </div>
        <div class="acts">
          <button class="dl ${item.has_update ? 'update' : ''} ${complete ? 'done' : ''}" data-action="quick-download" data-id="${item.id}" data-scope="${scope}" ${complete ? 'disabled' : ''}>${dlLabel}</button>
          <button class="icon" data-action="open-detail" data-id="${item.id}" title="详情">ⓘ</button>
          <button class="icon sub ${item.subscribed ? 'on' : ''}" data-action="toggle-subscription" data-id="${item.id}" title="${item.subscribed ? '取消订阅' : '订阅追更'}">◉</button>
          <button class="icon" data-action="quick-watch" data-id="${item.id}" title="观看">▶</button>
          <button class="icon fav ${item.favorite ? 'on' : ''}" data-action="toggle-fav" data-id="${item.id}" title="收藏">★</button>
        </div>
      </div>
    </article>`;
  }

  function pagerHTML() {
    const { page, pages, total } = state;
    const nums = [];
    for (let i = Math.max(1, page - 2); i <= Math.min(pages, page + 2); i++) nums.push(i);
    const btn = (p, label, disabled = false, active = false) =>
      `<button data-action="page" data-page="${p}" ${disabled ? 'disabled' : ''} class="${active ? 'active' : ''}">${label}</button>`;
    return `<div class="pager">
      ${btn(1, '«', page <= 1)}${btn(page - 1, '‹', page <= 1)}
      ${nums.map((n) => btn(n, n, false, n === page)).join('')}
      ${btn(page + 1, '›', page >= pages)}${btn(pages, '»', page >= pages)}
      <span class="info">共 ${total} 部 · ${pages} 页</span>
    </div>`;
  }

  function emptyHTML(title, desc, actionLabel, action) {
    return `<div class="empty"><div class="glyph">▦</div><h3>${h(title)}</h3><p>${h(desc || '')}</p>
      ${actionLabel ? `<button class="btn" data-action="${action}">${h(actionLabel)}</button>` : ''}</div>`;
  }

  // ---------------------------------------------------------------- selection
  function updateBatchBar() {
    const bar = $('#batchbar');
    if (!state.selected.size) {
      bar.classList.remove('show');
      bar.innerHTML = '';
      return;
    }
    bar.innerHTML = `<span class="n">已选 <b>${state.selected.size}</b> 项</span><span class="divider"></span>
      <button class="btn sm pri" data-action="batch-download">下载整部</button>
      <button class="btn sm" data-action="batch-download-new">只下更新</button>
      <button class="btn sm" data-action="batch-tag">加标签</button>
      <button class="btn sm" data-action="batch-scan">同步详情</button>
      <button class="btn sm" data-action="batch-covers">补集封面</button>
      <button class="btn sm" data-action="batch-fav">收藏</button>
      <button class="btn sm ghost" data-action="batch-clear">取消选择</button>`;
    bar.classList.add('show');
  }

  function syncSelectionDOM() {
    $$('.card[data-id]').forEach((card) => {
      card.classList.toggle('sel', state.selected.has(Number(card.dataset.id)));
    });
    const toggle = $('[data-action="select-page"]');
    if (toggle) toggle.classList.toggle('on', pageAllSelected());
    updateBatchBar();
  }

  function toggleSelect(id) {
    if (state.selected.has(id)) state.selected.delete(id);
    else state.selected.add(id);
    syncSelectionDOM();
  }

  function selectPage() {
    if (pageAllSelected()) state.items.forEach((item) => state.selected.delete(item.id));
    else state.items.forEach((item) => state.selected.add(item.id));
    syncSelectionDOM();
  }

  // ------------------------------------------------------------------- detail
  async function loadDetail(id) {
    const token = ++renderToken;
    state.detail = null;
    state.detailActiveCount = 0;
    state.epSelected = new Set();
    $('#view').innerHTML = '<div class="skeleton" style="height:340px;border-radius:14px"></div>';
    try {
      const res = await api(`/dramas/${id}`);
      if (token !== renderToken) return;
      state.detail = res.drama;
      renderDetailView();
      updateDocumentTitle();
    } catch (err) {
      if (token === renderToken) $('#view').innerHTML = emptyHTML('打不开剧集', err.message, '返回媒体库', 'go-library');
    }
  }

  function renderDetailView() {
    const d = state.detail;
    if (!d) return;
    const total = d.total_ep || 0;
    const done = d.done_ep || 0;
    const missing = Math.max(0, total - done);
    const customTags = (d.tags || []).filter((t) => t.kind !== 'system');
    const siteTags = d.tags_site || [];
    const year = d.year || (d.premiered ? String(d.premiered).slice(0, 4) : '');
    const episodes = d.episodes || [];
    const queuedCount = episodes.filter((e) => e.state === 'queued').length;
    const downloadingCount = episodes.filter((e) => e.state === 'downloading').length;
    const failedCount = episodes.filter((e) => e.state === 'failed').length;
    const doneBytes = episodes.filter((e) => e.state === 'done').reduce((sum, e) => sum + (Number(e.file_size) || 0), 0);
    const progressPct = pct(done, total);
    const estimate = d.estimate || {};
    const complete = total > 0 && done >= total;
    const moduleText = ((d.module_labels && d.module_labels.length) ? d.module_labels : [d.module_label || moduleLabel(d.module)]).join(' · ');
    $('#view').innerHTML = `
      <a class="detail-back" href="#/${state.module === 'all' ? 'library' : 'module/' + state.module}">← 返回列表</a>
      ${d.is_duplicate && d.duplicate_preferred ? `<div class="merge-banner">同名剧已保留集数更多的版本：
        <a href="#/drama/${d.duplicate_preferred.id}">${h(d.duplicate_preferred.title)}（${d.duplicate_preferred.total_ep} 集）</a>
        ，当前条目已从媒体库隐藏，文件不会删除。</div>` : ''}
      ${d.tag_excluded ? '<div class="merge-banner">该剧含有已排除的标签，已从媒体库/更新/订阅中隐藏；在「标签管理」里恢复标签后会重新显示。</div>' : ''}
      <div class="detail-hero">
        <div class="detail-cover">
          ${coverHTML(d)}
          <span class="cbadge module ${d.module}">${h(moduleText)}</span>
          ${d.score ? `<div class="score">${Number(d.score).toFixed(1)}<span>分</span></div>` : ''}
        </div>
        <div class="detail-info">
          <div class="detail-kicker">
            <span class="pill">${h(d.status_text || (total ? `更新至 ${total} 集` : '待同步'))}</span>
            ${d.premiered ? `<span class="pill">上线 ${h(d.premiered)}</span>` : ''}
            <span class="pill ${done >= total && total ? 'ok' : ''}">已下载 ${done} / ${total || '?'}</span>
            ${d.has_update ? '<span class="pill warn">有更新</span>' : ''}
            ${d.subscribed ? '<span class="pill sub">已订阅</span>' : ''}
          </div>
          <div class="detail-title-row">
            <h1 class="detail-title">${h(d.title)}</h1>
            <button class="detail-fav ${d.favorite ? 'on' : ''}" data-action="detail-fav" title="${d.favorite ? '取消收藏' : '收藏'}">★</button>
          </div>
          <div class="detail-meta-line">
            <span>评分 <b>${d.score ? Number(d.score).toFixed(1) : '—'}</b></span>
            <span>播放 <b>${h(d.views || '—')}</b></span>
            <span>单集 <b>${h(d.duration_text || '—')}</b></span>
            <span>年份 <b>${year || '—'}</b></span>
            <span>出品 <b>${h(d.author || (d.source === 'yeguo' ? '野果短剧' : '黄果短剧'))}</b></span>
          </div>
          <div class="detail-tags">
            ${siteTags.map((t) => `<span class="chip tag">${h(t)}</span>`).join('')}
            ${customTags.map((t) => `<span class="chip custom">${h(t.name)}<span class="x" data-action="detail-tag-remove" data-name="${h(t.name)}">×</span></span>`).join('')}
            <span class="chip add" data-action="detail-tag-add">＋ 标签</span>
          </div>
          <div class="detail-plot clamped" id="detail-plot">${h(d.description || '暂无简介')}</div>
          <button class="detail-plot-toggle" data-action="detail-plot">展开简介</button>
          <div class="detail-actions">
            <button class="btn pri" data-action="detail-download-all" ${complete ? 'disabled' : ''}>${complete ? '已下载完成' : (done > 0 ? `继续下载 · 缺 ${missing} 集` : `下载全集${total ? ` · ${total} 集` : ''}`)}</button>
            <button class="btn" data-action="detail-download-new" ${missing ? '' : 'disabled'}>下载新增${missing ? ` · ${missing} 集` : ''}</button>
            <button class="btn" data-action="detail-watch">▶ 观看</button>
            <button class="btn ${d.subscribed ? 'sub-on' : ''}" data-action="detail-subscribe">${d.subscribed ? '◉ 已订阅' : '◉ 订阅追更'}</button>
            <button class="btn" data-action="detail-scan">同步详情</button>
            <button class="btn ghost" data-action="detail-nfo">生成 NFO</button>
            <button class="btn ghost" data-action="detail-covers">补集封面</button>
            <button class="btn ghost" data-action="detail-folder">打开目录</button>
          </div>
          <div class="detail-kv">
            <div><span>总集数</span><b>${total || '—'}</b></div>
            <div><span>已下载</span><b>${done}</b></div>
            <div><span>年份</span><b>${year || '—'}</b></div>
            <div><span>存储位置</span><b class="path" title="${h(d.folder)}">${h(d.folder)}</b></div>
          </div>
        </div>
        <div class="detail-side">
          <div class="side-card">
            <div class="sc-label">下载进度</div>
            <div class="sc-big">${progressPct}<span>%</span></div>
            <div class="sc-bar"><i style="width:${progressPct}%"></i></div>
            <div class="sc-sub">${done} / ${total || '?'} 集 · 已用 ${fmtBytes(doneBytes)}</div>
          </div>
          <div class="side-card">
            <div class="sc-label">体积估算</div>
            <div class="est-row"><span>HEVC 原画</span><b>${fmtBytes(estimate.hevc || 0)}</b></div>
            <div class="est-row"><span>H264 原画</span><b>${fmtBytes(estimate.h264 || 0)}</b></div>
            <div class="est-row"><span>720P 压缩</span><b>${fmtBytes(estimate['720p'] || 0)}</b></div>
          </div>
          <div class="side-card">
            <div class="sc-label">媒体刮削</div>
            <div class="est-row"><span>NFO</span><b>自动写入</b></div>
            <div class="est-row"><span>结构</span><b>电视剧</b></div>
            <div class="sc-sub">Emby / Jellyfin 可直接识别</div>
            <button class="btn sm" data-action="detail-nfo" style="margin-top:9px">刷新 NFO</button>
          </div>
        </div>
      </div>

      <div class="ep-panel">
        <div class="ep-head">
          <h2>分集 <small>${episodes.length} 集 · 已下载 ${done} 集 · 待下载 ${missing} 集${queuedCount ? ` · 排队 ${queuedCount}` : ''}${downloadingCount ? ` · 下载中 ${downloadingCount}` : ''}${failedCount ? ` · 失败 ${failedCount}` : ''}</small></h2>
          <div class="spacer"></div>
          <div class="ep-actions">
            <select class="select" id="detail-quality">
              <option value="hevc" ${state.quality === 'hevc' ? 'selected' : ''}>HEVC 原画</option>
              <option value="h264" ${state.quality === 'h264' ? 'selected' : ''}>H264 原画</option>
              <option value="720p" ${state.quality === '720p' ? 'selected' : ''}>720P 压缩</option>
            </select>
            <button class="btn sm" data-action="ep-select-missing">全选未下载</button>
            <button class="btn sm" data-action="ep-select-all">全选</button>
            <button class="btn sm" data-action="ep-clear">清空</button>
            <button class="btn sm pri" data-action="detail-download-selected">下载选中 <span id="ep-selected-count">0</span></button>
          </div>
        </div>
        ${episodes.length ? `<div class="ep-grid" id="ep-grid">${episodes.map(epHTML).join('')}</div><div class="ep-foot">单击分集选中，双击直接观看</div>` : emptyHTML('还没有分集数据', '点击「同步详情」拉取分集列表。', '', '')}
      </div>

      <div class="scrape-panel">
        <div class="txt">已完成分集自动生成 <b>tvshow.nfo</b>、<b>poster.jpg</b>、<b>fanart.jpg</b> 与分集 NFO。<br>目录：<code>${h(d.folder)}</code></div>
        <div class="btns"><button class="btn sm" data-action="detail-nfo">刷新 NFO</button><button class="btn sm" data-action="detail-folder">打开文件夹</button></div>
      </div>`;
    updateEpSelection();
  }

  function updateEpisodeTile(number, taskState) {
    const el = $(`.ep[data-ep="${Number(number)}"]`);
    if (!el) return;
    el.classList.remove('done', 'downloading', 'queued', 'failed');
    const mapped = taskState === 'downloading' ? 'downloading' : 'queued';
    el.classList.add(mapped);
    const small = el.querySelector('small');
    if (small) small.textContent = taskState === 'downloading' ? '下载中' : '排队中';
  }

  function epHTML(ep) {
    const selected = state.epSelected.has(ep.ep);
    const stateClass = ep.state === 'done' ? 'done' : ep.state === 'downloading' ? 'downloading' : ep.state === 'queued' ? 'queued' : ep.state === 'failed' ? 'failed' : '';
    const stateText = ep.state === 'done' ? '已下载' : ep.state === 'downloading' ? '下载中' : ep.state === 'queued' ? '排队中' : ep.state === 'failed' ? '失败' : (ep.duration ? fmtDuration(ep.duration) : '待下载');
    return `<div class="ep ${stateClass} ${selected ? 'selected' : ''}" data-ep="${ep.ep}" title="${h(ep.synopsis || ep.title || '')}">
      ${ep.thumb ? `<img src="${h(ep.thumb)}" alt="" loading="lazy" onerror="this.style.display='none'">` : ''}
      <b>${String(ep.ep).padStart(2, '0')}</b>
      <small>${stateText}</small>
      <span class="ep-dot"></span>
    </div>`;
  }

  function updateEpSelection() {
    $$('.ep[data-ep]').forEach((el) => el.classList.toggle('selected', state.epSelected.has(Number(el.dataset.ep))));
    const count = $('#ep-selected-count');
    if (count) count.textContent = state.epSelected.size;
  }

  // ---------------------------------------------------------------- downloads
  async function renderDownloadsView() {
    const token = ++renderToken;
    $('#view').innerHTML = '<div class="skeleton" style="height:120px;border-radius:12px"></div>';
    try {
      const [tasksRes, historyRes] = await Promise.all([api('/tasks?state=all&limit=400'), api('/history')]);
      if (token !== renderToken) return;
      state.tasks = tasksRes.tasks || [];
      state.history = historyRes.items || [];
      renderDownloads();
    } catch (err) {
      if (token === renderToken) $('#view').innerHTML = emptyHTML('下载队列加载失败', err.message, '重试', 'reload');
    }
  }

  function renderDownloads() {
    const tasks = state.tasks;
    const active = tasks.filter((t) => t.state === 'downloading' || t.state === 'queued');
    const failed = tasks.filter((t) => t.state === 'failed');
    const summary = {
      ...(state.activity.summary || {}),
      downloading: tasks.filter((t) => t.state === 'downloading').length,
      queued: tasks.filter((t) => t.state === 'queued').length,
      done: tasks.filter((t) => t.state === 'done').length,
      failed: failed.length,
    };
    $('#view').innerHTML = `
      <div class="page-head"><h1>下载队列</h1><p>实时进度、失败重试与历史</p>
        <div class="right">
          <button class="btn sm" data-action="queue-toggle">${summary.paused ? '▶ 继续队列' : 'Ⅱ 暂停队列'}</button>
          ${failed.length ? '<button class="btn sm pri" data-action="retry-all-failed">重试全部失败</button><button class="btn sm ghost" data-action="clear-failed">清理失败</button>' : ''}
          <button class="btn sm ghost" data-action="tasks-clear">清除已完成</button>
        </div></div>
      <div class="summary-row">
        <span class="${summary.paused ? 'warn' : 'ok'}">${summary.paused ? '已暂停' : '运行中'}</span>
        <span class="warn">下载中 <b>${summary.downloading || 0}</b></span>
        <span>排队 <b>${summary.queued || 0}</b></span>
        <span class="ok">完成 <b>${summary.done || 0}</b></span>
        <span class="${summary.failed ? 'accent' : ''}">失败 <b>${summary.failed || 0}</b></span>
        <span>并发 <b>${summary.workers || 0}</b> 路</span>
      </div>
      <div id="downloads-live">${sectionActive(active)}${sectionFailed(failed)}</div>
      <div class="section-title">最近完成 <span class="count">${state.history.length} 条</span></div>
      ${state.history.length ? state.history.map(historyRow).join('') : '<div class="tag-empty">暂无下载记录</div>'}`;
  }

  function sectionActive(active) {
    return `<div class="section-title">队列 <span class="count">${active.length} 个任务</span></div>
      ${active.length ? `<div class="task-list">${active.map(taskRow).join('')}</div>` : '<div class="tag-empty">队列空闲 · <a href="#/library">去媒体库选择剧集下载</a></div>'}`;
  }

  function sectionFailed(failed) {
    if (!failed.length) return '';
    return `<div class="section-title">失败 <span class="count">${failed.length}</span></div>
      <div class="task-list">${failed.map((t) => taskRow(t, true)).join('')}</div>`;
  }

  function taskRow(task, failed = false) {
    const progress = Number(task.progress) || 0;
    const speed = Number(task.speed) || 0;
    const stateText = failed ? (task.error || '失败') : task.state === 'queued' ? '排队中' : '下载中';
    return `<div class="task" data-task-id="${task.id}">
      <div class="task-icon">${failed ? '!' : '↓'}</div>
      <div class="task-main">
        <div class="task-title">${h(task.drama_title || '')} <span style="color:var(--dim)">· 第 ${task.ep} 集</span></div>
        <div class="task-sub ${failed ? 'task-error-link' : ''}" ${failed ? `data-action="task-error" data-id="${task.id}" title="${h(task.error || '')}"` : ''}>${h(stateText)} · ${h(QUALITY_LABEL[task.quality] || task.quality || '')}</div>
        <div class="task-bar ${progress >= 100 ? 'done' : ''}"><span style="width:${progress}%"></span></div>
      </div>
      <div class="task-state">${failed ? '—' : progress.toFixed(0) + '%'}</div>
      <div class="task-size">${fmtBytes(task.size || 0)}</div>
      <div class="task-size">${failed ? '' : speed ? fmtBytes(speed) + '/s' : ''}</div>
      <button class="icon" data-action="${failed ? 'task-retry' : 'task-cancel'}" data-id="${task.id}" data-drama="${task.drama_id}" data-ep="${task.ep}" title="${failed ? '重试' : '取消'}">${failed ? '↻' : '×'}</button>
    </div>`;
  }

  function historyRow(item) {
    return `<div class="task" style="grid-template-columns:32px minmax(0,1fr) 86px 66px 30px">
      <div class="task-icon" style="color:var(--ok)">✓</div>
      <div class="task-main"><div class="task-title">${h(item.title)} <span style="color:var(--dim)">· 第 ${item.ep} 集</span></div>
        <div class="task-sub">${h(item.downloaded_at || '')}</div></div>
      <div class="task-size">${fmtBytes(item.file_size || 0)}</div>
      <div class="task-size">第 ${item.ep} 集</div>
      <button class="icon" data-action="open-detail" data-id="${item.drama_id}" title="详情">ⓘ</button>
    </div>`;
  }

  function updateTaskRows(tasks) {
    const live = $('#downloads-live');
    if (!live) return;
    const renderedActive = (state.tasks || []).filter((t) => t.state === 'downloading' || t.state === 'queued').length;
    if (tasks.length !== renderedActive) { renderDownloadsView(); return; }
    tasks.forEach((task) => {
      const row = document.querySelector(`.task[data-task-id="${task.id}"]`);
      if (!row) return;
      const bar = row.querySelector('.task-bar span');
      if (bar) bar.style.width = `${Number(task.progress) || 0}%`;
      const stateEl = row.querySelector('.task-state');
      if (stateEl) stateEl.textContent = `${(Number(task.progress) || 0).toFixed(0)}%`;
      const sizes = row.querySelectorAll('.task-size');
      if (sizes[0]) sizes[0].textContent = fmtBytes(task.size || 0);
      if (sizes[1]) sizes[1].textContent = task.speed ? fmtBytes(task.speed) + '/s' : '';
    });
  }

  // ------------------------------------------------------------------ updates
  async function renderUpdatesView() {
    const token = ++renderToken;
    $('#view').innerHTML = '<div class="skeleton" style="height:120px;border-radius:12px"></div>';
    try {
      const res = await api('/updates');
      if (token !== renderToken) return;
      const items = res.items || [];
      $('#view').innerHTML = `
        <div class="page-head"><h1>更新提醒</h1><p>发现新分集，一键补全</p>
          <div class="right">${items.length ? '<button class="btn pri" data-action="update-all">一键下载全部更新</button>' : ''}</div></div>
        ${items.length ? items.map(updateRow).join('') : emptyHTML('暂时没有更新', '重新扫描模块后，新分集会自动出现在这里。', '去采集', 'open-scan')}`;
    } catch (err) {
      if (token === renderToken) $('#view').innerHTML = emptyHTML('加载失败', err.message, '重试', 'reload');
    }
  }

  function updateRow(item) {
    const added = Math.max(0, (item.total_ep || 0) - (item.prev_total_ep || 0));
    return `<div class="update-row">
      <img src="${h(item.cover || '')}" alt="" onerror="this.style.visibility='hidden'">
      <div>
        <div class="update-title">${h(item.title)}</div>
        <div class="update-sub">${h(item.module_label || '')} · 已下载 ${item.done_ep || 0}/${item.total_ep || 0} 集 · ${h(item.status_text || '')}</div>
        <div class="update-delta">↑ 新增 ${added} 集${item.prev_total_ep ? `（${item.prev_total_ep} → ${item.total_ep}）` : ''}</div>
      </div>
      <div class="update-actions">
        <button class="btn sm pri" data-action="update-download" data-id="${item.id}">下载更新</button>
        <button class="btn sm" data-action="open-detail" data-id="${item.id}">详情</button>
        <button class="btn sm ghost" data-action="update-ignore" data-id="${item.id}">忽略</button>
      </div>
    </div>`;
  }

  // -------------------------------------------------------------- subscriptions
  async function renderSubscriptionsView() {
    const token = ++renderToken;
    $('#view').innerHTML = '<div class="skeleton" style="height:120px;border-radius:12px"></div>';
    try {
      const res = await api('/subscriptions');
      if (token !== renderToken) return;
      state.subscriptions = res.items || [];
      const updates = state.subscriptions.filter((i) => i.has_update).length;
      const auto = state.subscriptions.filter((i) => i.sub_auto).length;
      $('#view').innerHTML = `
        <div class="page-head"><h1>订阅追更</h1><p>扫库发现新分集后自动下载；可单独设置画质</p>
          <div class="right">
            <button class="btn sm" data-action="sub-download-all">下载全部更新</button>
            <button class="btn pri" data-action="open-scan">＋ 开始采集</button>
          </div></div>
        <div class="summary-row">
          <span>订阅 <b>${state.subscriptions.length}</b> 部</span>
          <span class="accent">有更新 <b>${updates}</b></span>
          <span class="ok">自动下载 <b>${auto}</b></span>
        </div>
        ${state.subscriptions.length ? state.subscriptions.map(subRow).join('') : emptyHTML('还没有订阅', '在媒体库卡片上点 ◉，或进入详情页点「订阅追更」。', '去媒体库', 'go-library')}`;
    } catch (err) {
      if (token === renderToken) $('#view').innerHTML = emptyHTML('订阅页加载失败', err.message, '重试', 'reload');
    }
  }

  function subRow(item) {
    const total = item.total_ep || 0;
    const done = item.done_ep || 0;
    const progress = pct(done, total);
    const quality = item.sub_quality || state.quality;
    const lastAuto = item.last_auto_at ? `上次自动 ${shortTime(item.last_auto_at)} +${item.last_auto_count || 0} 集` : '尚未自动下载';
    return `<div class="sub-row">
      <img src="${h(item.cover || '')}" alt="" onerror="this.style.visibility='hidden'">
      <div class="sub-main">
        <div class="sub-title">${h(item.title)}
          ${item.has_update ? '<span class="badge red">有更新</span>' : ''}
          <span class="badge ${item.sub_auto ? 'amber' : ''}">${item.sub_auto ? '自动下载' : '仅提醒'}</span>
        </div>
        <div class="sub-meta">
          <span>${h(item.module_label || '')}</span>
          <span>已下载 ${done}/${total || '?'} 集</span>
          <span>${h(QUALITY_LABEL[quality] || quality)}</span>
          <span>${h(lastAuto)}</span>
        </div>
        <div class="sub-progress"><div class="bar"><i style="width:${progress}%"></i></div><span>${progress}%</span></div>
      </div>
      <div class="sub-actions">
        <select class="select" data-sub-quality data-id="${item.id}">
          <option value="hevc" ${quality === 'hevc' ? 'selected' : ''}>HEVC 原画</option>
          <option value="h264" ${quality === 'h264' ? 'selected' : ''}>H264 原画</option>
          <option value="720p" ${quality === '720p' ? 'selected' : ''}>720P 压缩</option>
        </select>
        <label class="sub-auto"><input type="checkbox" data-sub-auto data-id="${item.id}" ${item.sub_auto ? 'checked' : ''}> 自动下载新集</label>
        <div class="line">
          <button class="btn sm pri" data-action="sub-download" data-id="${item.id}">下载更新</button>
          <button class="btn sm" data-action="open-detail" data-id="${item.id}">详情</button>
          <button class="btn sm ghost" data-action="sub-unsubscribe" data-id="${item.id}">取消</button>
        </div>
      </div>
    </div>`;
  }

  // ----------------------------------------------------------------- schedules
  const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日'];

  async function renderSchedulesView() {
    const token = ++renderToken;
    $('#view').innerHTML = '<div class="skeleton" style="height:120px;border-radius:12px"></div>';
    try {
      const res = await api('/schedules');
      if (token !== renderToken) return;
      state.schedules = res.items || [];
      const runningId = res.active_schedule_id;
      $('#view').innerHTML = `
        <div class="page-head"><h1>定时任务</h1><p>到点自动执行全量扫库；订阅的剧会在扫库后自动下载新集</p>
          <div class="right"><button class="btn pri" data-action="sched-new">＋ 新建定时任务</button></div></div>
        <div class="summary-row">
          <span>任务 <b>${state.schedules.length}</b> 个</span>
          <span class="ok">启用 <b>${state.schedules.filter((s) => s.enabled).length}</b></span>
          <span>${runningId ? `正在由任务 #${runningId} 执行` : '调度器运行中'}</span>
        </div>
        ${state.schedules.length ? `<div class="sched-grid">${state.schedules.map((s) => scheduleCard(s, runningId)).join('')}</div>`
          : emptyHTML('还没有定时任务', '例如每天 03:00 自动扫全库，扫完后自动补下订阅剧的新集。', '新建定时任务', 'sched-new')}
        <div class="scan-note" style="margin-top:14px">时区跟随服务器本地时间（当前为 Asia/Shanghai）。程序离线期间错过的任务不会补跑，恢复后会排到下一个时间点。</div>`;
    } catch (err) {
      if (token === renderToken) $('#view').innerHTML = emptyHTML('定时任务加载失败', err.message, '重试', 'reload');
    }
  }

  function scheduleCard(schedule, runningId) {
    const days = new Set(schedule.weekdays || []);
    const modules = (schedule.modules || []).map(moduleLabel).join(' / ') || '全部模块';
    const running = Number(runningId) === Number(schedule.id);
    return `<div class="sched-card ${schedule.enabled ? '' : 'off'}">
      <div class="sched-top">
        <div class="sched-time">${h(schedule.time)}</div>
        <div style="flex:1;min-width:0">
          <div class="sched-name">${h(schedule.name)}</div>
          <div class="sched-days">${WEEKDAY_LABELS.map((label, i) => `<span class="${days.has(i) ? 'on' : ''}">${label}</span>`).join('')}</div>
        </div>
        <span class="badge ${running ? 'amber' : schedule.enabled ? 'green' : ''}">${running ? '执行中' : schedule.enabled ? '启用' : '暂停'}</span>
      </div>
      <div class="sched-meta">
        <span>模块：<b>${h(modules)}</b></span>
        <span>下次：<b>${h(shortTime(schedule.next_run_at) || '—')}</b></span>
      </div>
      <div class="sched-meta">
        <span>上次：<b>${h(shortTime(schedule.last_run_at) || '—')}</b></span>
        <span>${h(schedule.last_status || '等待执行')}${schedule.last_message ? ` · ${h(schedule.last_message)}` : ''}</span>
      </div>
      <div class="sched-actions">
        <button class="btn sm pri" data-action="sched-run" data-id="${schedule.id}">立即执行</button>
        <button class="btn sm" data-action="sched-toggle" data-id="${schedule.id}">${schedule.enabled ? '暂停' : '启用'}</button>
        <button class="btn sm" data-action="sched-edit" data-id="${schedule.id}">编辑</button>
        <button class="btn sm ghost" data-action="sched-delete" data-id="${schedule.id}">删除</button>
      </div>
    </div>`;
  }

  function openScheduleModal(schedule = null) {
    const editing = !!schedule;
    const current = schedule || {
      name: '每日全量扫库', time: '03:00', weekdays: [0, 1, 2, 3, 4, 5, 6],
      modules: ['original', 'duanju', 'manju', 'yeguo'], details: 1, enabled: 1,
    };
    const days = new Set((current.weekdays || []).map(Number));
    const modules = new Set(current.modules || []);
    const modal = openModal(`
      ${modalHead(editing ? '编辑定时任务' : '新建定时任务')}
      <div class="mbody">
        <div class="field"><label>任务名称</label><input id="sched-name" type="text" value="${h(current.name || '')}" placeholder="每日全量扫库"></div>
        <div class="field"><label>执行时间</label><input id="sched-time" type="time" value="${h(current.time || '03:00')}"></div>
        <div class="field"><label>执行日</label><div class="fchips" id="sched-days">
          ${WEEKDAY_LABELS.map((label, i) => `<button class="gb ${days.has(i) ? 'on' : ''}" data-day="${i}">周${label}</button>`).join('')}
        </div></div>
        <div class="field"><label>采集模块</label><div class="scan-options">
          ${MODULES.map((m) => `<label class="check-card"><input type="checkbox" class="sched-module" value="${m.key}" ${modules.has(m.key) ? 'checked' : ''}><span class="name">${m.label}</span></label>`).join('')}
        </div></div>
        <label class="checkbox-line" style="margin-bottom:8px"><input type="checkbox" id="sched-details" ${current.details ? 'checked' : ''}> 同步详情与分集（自动下载必需）</label>
        <label class="checkbox-line"><input type="checkbox" id="sched-enabled" ${current.enabled ? 'checked' : ''}> 创建后立即启用</label>
        <div class="scan-note" style="margin-top:12px">定时任务执行的是全量扫库（最新 + 热播 + 精选），会按订阅设置自动下载新集。</div>
      </div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn pri" id="sched-save">保存</button></div>
    `, { wide: true });
    $$('#sched-days .gb', modal.mask).forEach((btn) => btn.addEventListener('click', () => btn.classList.toggle('on')));
    modal.q('#sched-save').addEventListener('click', async () => {
      const name = modal.q('#sched-name').value.trim() || '全量扫库';
      const time = modal.q('#sched-time').value || '03:00';
      const selectedDays = $$('#sched-days .gb', modal.mask).filter((b) => b.classList.contains('on')).map((b) => Number(b.dataset.day));
      const selectedModules = $$('.sched-module', modal.mask).filter((i) => i.checked).map((i) => i.value);
      if (!selectedDays.length) { toast('至少选择一天', 'err'); return; }
      if (!selectedModules.length) { toast('至少选择一个模块', 'err'); return; }
      const payload = {
        name, time, weekdays: selectedDays, modules: selectedModules,
        details: modal.q('#sched-details').checked,
        enabled: modal.q('#sched-enabled').checked,
      };
      try {
        if (editing) await api(`/schedules/${schedule.id}`, { method: 'PUT', body: payload });
        else await api('/schedules', { method: 'POST', body: payload });
        modal.close();
        toast(editing ? '定时任务已更新' : '定时任务已创建', 'ok');
        renderSchedulesView();
      } catch (err) { toast(err.message, 'err'); }
    });
  }

  // --------------------------------------------------------------------- tags
  async function renderTagsView() {
    await loadTags();
    const tags = state.tags || [];
    const excluded = state.excludedTags || [];
    const excludedNames = new Set(excluded.map((t) => t.name));
    const siteTags = (state.stats && state.stats.site_tags) || [];
    const allNames = [...new Set([...siteTags.map((t) => t.name), ...tags.map((t) => t.name), ...excluded.map((t) => t.name)])];
    $('#view').innerHTML = `
      <div class="page-head"><h1>标签管理</h1><p>整理媒体库；排除的标签会全局隐藏相关剧集</p></div>

      <div class="panel" style="margin-bottom:14px">
        <h2 style="font-size:13px;margin-bottom:6px">排除标签</h2>
        <p class="hint" style="margin-bottom:9px">加入后，含该标签的剧不再出现在媒体库、更新提醒、订阅和统计中。恢复后重新显示，文件不会删除。</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <input id="exclude-tag-input" list="all-tag-names" type="text" placeholder="输入或选择要排除的标签，例如：乱伦 / 已下载" style="height:36px;padding:0 11px;border-radius:8px;border:1px solid var(--line2);background:var(--bg2);outline:none;min-width:240px;flex:1;max-width:380px">
          <datalist id="all-tag-names">${allNames.map((n) => `<option value="${h(n)}"></option>`).join('')}</datalist>
          <button class="btn pri" data-action="exclude-tag">排除标签</button>
        </div>
        <div class="excluded-list" style="margin-top:10px">
          ${excluded.length ? excluded.map((t) => `<span class="excluded-chip">${h(t.name)}<button data-action="restore-tag" data-name="${h(t.name)}" title="恢复显示">恢复</button></span>`).join('') : '<span class="hint">还没有排除标签</span>'}
        </div>
      </div>

      <div class="panel" style="margin-bottom:14px">
        <h2 style="font-size:13px;margin-bottom:9px">题材标签 <small style="color:var(--dim);font-weight:400">${siteTags.length} 个</small></h2>
        <div class="tag-grid">
          ${siteTags.length ? siteTags.map((tag) => `<div class="tag-card">
            <span class="tag-dot" style="background:#7db6d9"></span>
            <b>${h(tag.name)}</b><small>${tag.count} 部</small>
            <button class="icon" data-action="set-site-tag" data-site-tag="${h(tag.name)}" title="筛选">⌕</button>
            <button class="icon" data-action="exclude-tag" data-name="${h(tag.name)}" title="排除该标签">⊘</button>
          </div>`).join('') : '<div class="tag-empty">暂无可排除的题材标签</div>'}
        </div>
      </div>

      <div class="panel">
        <h2 style="font-size:13px;margin-bottom:9px">自建标签</h2>
        <div style="display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
          <input id="new-tag-name" type="text" placeholder="新标签，例如：精品 / 待二刷" style="height:36px;padding:0 11px;border-radius:8px;border:1px solid var(--line2);background:var(--bg2);outline:none;min-width:240px;flex:1;max-width:380px">
          <input id="new-tag-color" type="color" value="#ff5c7a" style="width:36px;height:36px;border:1px solid var(--line2);border-radius:8px;background:var(--bg2);padding:3px">
          <button class="btn pri" data-action="tag-create">创建标签</button>
        </div>
        <div class="tag-grid">
          ${tags.length ? tags.filter((tag) => !excludedNames.has(tag.name)).map((tag) => `<div class="tag-card">
            <span class="tag-dot" style="background:${h(tag.color)}"></span>
            <b>${h(tag.name)}</b><small>${tag.count} 部${tag.kind === 'system' ? ' · 自动' : ''}</small>
            <button class="icon" data-action="set-tag" data-tag="${h(tag.name)}" title="筛选">⌕</button>
            <button class="icon" data-action="exclude-tag" data-name="${h(tag.name)}" title="排除该标签">⊘</button>
            ${tag.kind === 'system' ? '' : `<button class="icon" data-action="tag-delete" data-id="${tag.id}" title="删除">×</button>`}
          </div>`).join('') : '<div class="tag-empty">还没有自建标签</div>'}
        </div>
      </div>`;
    const input = $('#exclude-tag-input');
    if (input) input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        $('[data-action="exclude-tag"]').click();
      }
    });
  }

  // ----------------------------------------------------------------- settings
  async function renderSettingsView() {
    await loadHealth();
    try { state.settings = (await api('/settings')).settings || {}; } catch (_) {}
    const s = state.settings || {};
    const health = state.health || {};
    $('#view').innerHTML = `
      <div class="page-head"><h1>设置</h1><p>下载、刮削与网络</p></div>
      <div class="settings-grid">
        <div class="panel">
          <h2>下载与刮削</h2>
          <p class="hint">修改后对新任务生效；下载并发数重启程序后生效。</p>
          <div class="field"><label>下载根目录</label>
            <div style="display:flex;gap:7px">
              <input type="text" data-setting="download_root" id="setting-root" value="${h(s.download_root || '')}" style="flex:1">
              <button class="btn sm" data-action="browse-path">浏览</button>
              <button class="btn sm" data-action="check-path">检测</button>
            </div>
            <div class="help" id="path-check">Jellyfin 建议把 原创 / 短剧 / 漫剧 三个子目录分别添加为「电视剧」媒体库。</div>
            <div class="help">也可用环境变量 <code>HUANGGUO_DOWNLOAD_ROOT</code> 指定默认下载目录（Docker 挂载时用）。</div>
          </div>
          <div class="field"><label>目录模板</label><input type="text" data-setting="folder_template" value="${h(s.folder_template || '{module}/{title} ({year})')}">
            <div class="help">可用变量：{module} {title} {year} {site_id}。修改后只影响新下载。</div>
          </div>
          <div class="field"><label>默认画质</label>
            <select data-setting="quality">
              <option value="hevc" ${s.quality === 'hevc' ? 'selected' : ''}>HEVC 原画（约 17 MB/分钟，推荐）</option>
              <option value="h264" ${s.quality === 'h264' ? 'selected' : ''}>H264 原画（约 30–60 MB/分钟）</option>
              <option value="720p" ${s.quality === '720p' ? 'selected' : ''}>720P 压缩（转码慢）</option>
            </select></div>
          <div class="field"><label>下载并发</label><input type="number" min="1" max="6" data-setting="concurrency" value="${h(s.concurrency || '2')}">
            <div class="help">同时下载的分集数，建议 2–3。</div></div>
          <div class="field"><label>详情扫描并发</label><input type="number" min="1" max="12" data-setting="scan_workers" value="${h(s.scan_workers || '6')}"></div>
          <div class="field"><label>请求间隔（秒）</label><input type="number" min="0" max="5" step="0.05" data-setting="request_delay" value="${h(s.request_delay || '0.15')}"></div>
          <div class="field"><label>HTTP 代理（可选）</label><input type="text" data-setting="proxy" value="${h(s.proxy || '')}" placeholder="http://127.0.0.1:7890"></div>
          <div class="field"><label>站点 Cookie（可选）</label><textarea data-setting="cookie" placeholder="登录后复制 Cookie，公开内容一般无需填写">${h(s.cookie || '')}</textarea></div>
          <button class="btn pri" data-action="settings-save">保存设置</button>
          <button class="btn ghost" data-action="open-root">打开下载目录</button>
        </div>
        <div>
          <div class="panel" style="margin-bottom:14px">
            <h2>刮削选项</h2>
            <p class="hint">Emby / Jellyfin / Kodi 兼容。</p>
            <div class="switch-row"><div><div class="label">写入 NFO</div><div class="desc">tvshow.nfo 与分集 nfo</div></div><button class="switch ${s.write_nfo === '1' ? 'on' : ''}" data-switch="write_nfo"></button></div>
            <div class="switch-row"><div><div class="label">生成 fanart</div><div class="desc">模糊背景图 16:9</div></div><button class="switch ${s.write_fanart === '1' ? 'on' : ''}" data-switch="write_fanart"></button></div>
            <div class="switch-row"><div><div class="label">完成后打「已下载」</div><div class="desc">全集完成时自动</div></div><button class="switch ${s.auto_tag_done === '1' ? 'on' : ''}" data-switch="auto_tag_done"></button></div>
          </div>
          <div class="panel">
            <h2>运行状态</h2>
            <div class="health-list">
              <div class="health-item"><span>ffmpeg</span><b style="color:${health.ffmpeg ? 'var(--ok)' : 'var(--accent)'}">${health.ffmpeg ? '已就绪' : '未找到'}</b></div>
              <div class="health-item"><span>下载线程</span><b>${health.workers || 0} 路</b></div>
              <div class="health-item"><span>磁盘剩余</span><b>${fmtBytes(health.disk_free || 0)}</b></div>
              <div class="health-item"><span>媒体库占用</span><b>${fmtBytes((state.stats && state.stats.storage_bytes) || 0)}</b></div>
              <div class="health-item"><span>服务版本</span><b>${h(health.version || '1.6.1')}</b></div>
            </div>
            <div style="display:flex;gap:6px;margin-top:10px">
              <button class="btn sm" data-action="site-check" data-source="huangguo">测试黄果</button>
              <button class="btn sm" data-action="site-check" data-source="yeguo">测试野果</button>
            </div>
            <div class="hint" id="site-check-result"></div>
          </div>
          <div class="panel" style="margin-top:14px">
            <h2>媒体维护</h2>
            <p class="hint">为已下载但缺少封面的分集抽帧生成 <code>-thumb.jpg</code>。</p>
            <button class="btn sm" data-action="all-covers">补全全部已下载集封面</button>
          </div>
          <div class="panel" style="margin-top:14px;color:var(--dim);font-size:11px;line-height:1.8">
            本地采集器，仅用于个人媒体库整理。请遵守站点条款与当地法律法规，不要公开传播下载内容。
          </div>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------------ modals
  const modalStack = [];

  function openModal(inner, opts = {}) {
    const mask = document.createElement('div');
    mask.className = 'mask';
    mask.innerHTML = `<div class="modal ${opts.wide ? 'wide' : ''} ${opts.className || ''}">${inner}</div>`;
    document.getElementById('modal-root').appendChild(mask);
    document.body.classList.add('modal-open');
    const close = () => {
      if (opts.onClose) { try { opts.onClose(); } catch (_) {} }
      mask.remove();
      const index = modalStack.indexOf(close);
      if (index >= 0) modalStack.splice(index, 1);
      if (!modalStack.length) document.body.classList.remove('modal-open');
    };
    modalStack.push(close);
    mask.addEventListener('click', (event) => { if (event.target === mask) close(); });
    $$('[data-close]', mask).forEach((btn) => btn.addEventListener('click', close));
    return { mask, close, q: (sel) => mask.querySelector(sel) };
  }

  function modalHead(title) {
    return `<div class="mhead"><h2>${h(title)}</h2></div><button class="close" data-close>×</button>`;
  }

  function openFolderPicker() {
    const input = $('#setting-root');
    const modal = openModal(`
      ${modalHead('选择下载目录')}
      <div class="mbody">
        <div class="picker-tools">
          <button class="btn sm" id="picker-up">↑ 上一级</button>
          <span class="picker-current" id="picker-current"></span>
        </div>
        <div class="picker-drives" id="picker-drives"></div>
        <div class="picker-list" id="picker-list"></div>
        <div class="picker-new">
          <input id="picker-new-name" type="text" placeholder="在当前目录新建文件夹">
          <button class="btn sm" id="picker-new-btn">新建</button>
        </div>
        <div class="sc-sub" id="picker-info"></div>
      </div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn pri" id="picker-select">选择此目录</button></div>
    `, { wide: true });

    let current = '';
    let parent = '';
    let data = null;

    function render() {
      if (!data) return;
      current = data.current;
      parent = data.parent;
      modal.q('#picker-current').textContent = current;
      modal.q('#picker-up').disabled = !parent;
      modal.q('#picker-drives').innerHTML = (data.drives || []).map((drive) =>
        `<button class="picker-drive" data-path="${h(drive)}">${h(drive)}</button>`).join('');
      modal.q('#picker-list').innerHTML = (data.dirs || []).map((dir) => `
        <button class="picker-item" data-path="${h(dir.path)}">
          <span class="picker-ico">▸</span><span class="picker-name">${h(dir.name)}</span>
          ${dir.writable ? '' : '<span class="tag">只读</span>'}
        </button>`).join('') || '<div class="tag-empty">没有子文件夹</div>';
      modal.q('#picker-info').textContent = data.writable ? '当前目录可写' : '当前目录不可写，请选择其他目录';
      modal.q('#picker-select').disabled = !data.writable;
    }

    async function load(path) {
      try {
        data = await api('/fs/list?path=' + encodeURIComponent(path || ''));
        render();
      } catch (err) { toast(err.message, 'err'); }
    }

    modal.q('#picker-list').addEventListener('click', (event) => {
      const item = event.target.closest('[data-path]');
      if (item) load(item.dataset.path);
    });
    modal.q('#picker-drives').addEventListener('click', (event) => {
      const item = event.target.closest('[data-path]');
      if (item) load(item.dataset.path);
    });
    modal.q('#picker-up').addEventListener('click', () => { if (parent) load(parent); });
    modal.q('#picker-new-btn').addEventListener('click', async () => {
      const name = modal.q('#picker-new-name').value.trim();
      if (!name) { toast('请输入文件夹名', 'err'); return; }
      try {
        const res = await api('/fs/mkdir', { method: 'POST', body: { parent: current, name } });
        modal.q('#picker-new-name').value = '';
        toast('文件夹已创建', 'ok');
        load(res.path);
      } catch (err) { toast(err.message, 'err'); }
    });
    modal.q('#picker-select').addEventListener('click', async () => {
      if (!data) return;
      if (input) input.value = data.current;
      try {
        const res = await api('/settings/check-path', { method: 'POST', body: { path: data.current } });
        const message = $('#path-check');
        if (message) message.innerHTML = `目录可用：<code>${h(res.path)}</code> · 剩余 ${fmtBytes(res.free || 0)}`;
      } catch (err) { toast(err.message, 'err'); }
      modal.close();
      toast('已选择下载目录，记得点「保存设置」', 'ok');
    });
    load(input ? input.value : '');
  }

  function openScanModal() {
    const modal = openModal(`
      ${modalHead('采集站点内容')}
      <div class="mbody">
        <div class="scan-options">
          <label class="check-card"><input type="checkbox" value="original" class="scan-module" checked><span class="name">原创</span><span class="desc">黄果原创</span></label>
          <label class="check-card"><input type="checkbox" value="duanju" class="scan-module" checked><span class="name">短剧</span><span class="desc">AI 短剧</span></label>
          <label class="check-card"><input type="checkbox" value="manju" class="scan-module" checked><span class="name">漫剧</span><span class="desc">AI 漫剧</span></label>
          <label class="check-card"><input type="checkbox" value="yeguo" class="scan-module" checked><span class="name">野果短剧</span><span class="desc">yeguodj.com</span></label>
        </div>
        <div class="field"><label>采集页数（留空 = 全部）</label><input type="number" id="scan-pages" min="1" placeholder="例如 3，只采前 3 页"></div>
        <label class="checkbox-line" style="margin-bottom:7px"><input type="checkbox" id="scan-details" checked> 同步每部剧的详情与分集（下载必需）</label>
        <label class="checkbox-line" style="margin-bottom:12px"><input type="checkbox" id="scan-refresh"> 强制刷新已有详情</label>
        <div class="scan-note">黄果每个模块扫「最近更新 / 当前热播 / 精选推荐」，野果扫探索列表（约 75 页 / 1500 部）。同名剧只保留集数最多的版本。首次全量约 5–12 分钟。</div>
      </div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn pri" id="scan-start">开始采集</button></div>
    `);
    modal.q('#scan-start').addEventListener('click', async () => {
      const modules = $$('.scan-module', modal.mask).filter((el) => el.checked).map((el) => el.value);
      if (!modules.length) { toast('至少选择一个模块', 'err'); return; }
      const pages = modal.q('#scan-pages').value.trim();
      modal.q('#scan-start').disabled = true;
      try {
        const res = await api('/scan', { method: 'POST', body: {
          modules, pages: pages ? Number(pages) : null,
          details: modal.q('#scan-details').checked,
          refresh: modal.q('#scan-refresh').checked,
        } });
        modal.close();
        toast(res.message || '已开始采集', 'ok');
        state.lastScanState = 'running';
        refreshActivity();
      } catch (err) {
        modal.q('#scan-start').disabled = false;
        toast(err.message, 'err');
      }
    });
  }

  function openFilterModal() {
    const draft = {
      module: state.module, status: state.status, tag: state.tag,
      siteTag: state.siteTag, scoreMin: state.scoreMin, year: state.year,
    };
    const status = (state.stats && state.stats.status) || {};
    const byModule = (state.stats && state.stats.by_module) || {};
    const years = (state.stats && state.stats.years) || [];
    const scores = [[0, '不限'], [8, '8.0+'], [8.5, '8.5+'], [9, '9.0+']];
    const siteTags = (state.stats && state.stats.site_tags) || [];
    const tagCloud = (state.tags || []).slice().sort((a, b) => (b.count || 0) - (a.count || 0));
    const modal = openModal(`
      <div class="fmodal">
        <div class="fhead"><h3>筛选</h3></div>
        <div class="fbody">
          <div class="fgroup"><div class="gh">模块</div><div class="fchips">
            <button class="gb ${draft.module === 'all' ? 'on' : ''}" data-fk="module" data-fv="all">全部 <i>${state.stats.total || 0}</i></button>
            ${MODULES.map((m) => `<button class="gb ${draft.module === m.key ? 'on' : ''}" data-fk="module" data-fv="${m.key}">${m.label} <i>${byModule[m.key] || 0}</i></button>`).join('')}
          </div></div>
          <div class="fgroup"><div class="gh">状态</div><div class="fchips">
            ${STATUSES.map((s) => `<button class="gb ${draft.status === s.key ? 'on' : ''}" data-fk="status" data-fv="${s.key}">${s.label} <i>${status[s.key] || 0}</i></button>`).join('')}
          </div></div>
          <div class="fgroup"><div class="gh">评分</div><div class="fchips">
            ${scores.map(([v, label]) => `<button class="gb ${Number(draft.scoreMin) === v ? 'on' : ''}" data-fk="scoreMin" data-fv="${v}">${label}</button>`).join('')}
          </div></div>
          ${years.length ? `<div class="fgroup"><div class="gh">年份</div><div class="fchips">
            <button class="gb ${!draft.year ? 'on' : ''}" data-fk="year" data-fv="0">全部</button>
            ${years.map((y) => `<button class="gb ${Number(draft.year) === y.year ? 'on' : ''}" data-fk="year" data-fv="${y.year}">${y.year} <i>${y.count}</i></button>`).join('')}
          </div></div>` : ''}
          ${siteTags.length ? `<div class="fgroup"><div class="gh">题材</div><div class="fchips">
            <button class="gb ${!draft.siteTag ? 'on' : ''}" data-fk="siteTag" data-fv="">全部</button>
            ${siteTags.map((t) => `<button class="gb ${draft.siteTag === t.name ? 'on' : ''}" data-fk="siteTag" data-fv="${h(t.name)}">${h(t.name)} <i>${t.count}</i></button>`).join('')}
          </div></div>` : ''}
          <div class="fgroup"><div class="gh">自建标签</div><div class="fchips">
            <button class="gb ${!draft.tag ? 'on' : ''}" data-fk="tag" data-fv="">全部</button>
            ${tagCloud.length ? tagCloud.map((t) => `<button class="gb ${draft.tag === t.name ? 'on' : ''}" data-fk="tag" data-fv="${h(t.name)}">${h(t.name)} <i>${t.count}</i></button>`).join('') : '<span style="color:var(--dim);font-size:11px;padding:5px 2px">还没有自建标签，可在「标签管理」创建</span>'}
          </div></div>
        </div>
        <div class="ffoot">
          <span class="n">已选 <b id="f-count">0</b> 个条件</span>
          <button class="btn sm ghost" id="f-clear">清除筛选</button>
          <button class="btn sm pri" id="f-apply">显示结果</button>
        </div>
      </div>
      <button class="close" data-close>×</button>
    `, { wide: true });

    const draftCount = () => [draft.module !== 'all', draft.status !== 'all', !!draft.tag, !!draft.siteTag, !!draft.scoreMin, !!draft.year].filter(Boolean).length;
    const refreshDraftCount = () => {
      const el = modal.q('#f-count');
      if (el) el.textContent = draftCount();
    };
    refreshDraftCount();

    $$('.gb', modal.mask).forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.fk;
        const value = btn.dataset.fv;
        const group = btn.parentElement;
        $$('.gb', group).forEach((b) => b.classList.remove('on'));
        btn.classList.add('on');
        draft[key] = ['scoreMin', 'year'].includes(key) ? Number(value) : value;
        refreshDraftCount();
      });
    });
    modal.q('#f-clear').addEventListener('click', () => {
      state.module = 'all'; state.status = 'all'; state.tag = ''; state.siteTag = '';
      state.scoreMin = 0; state.year = 0; state.q = '';
      $('#search-input').value = '';
      $('#search-wrap').classList.remove('has');
      modal.close();
      if (state.route.name === 'module') navigate('/library'); else applyFilters();
    });
    modal.q('#f-apply').addEventListener('click', () => {
      state.module = draft.module;
      state.status = draft.status;
      state.tag = draft.tag;
      state.siteTag = draft.siteTag;
      state.scoreMin = draft.scoreMin;
      state.year = draft.year;
      modal.close();
      if (state.module !== 'all') {
        if (state.route.name === 'module' && state.route.module === state.module) applyFilters();
        else navigate(`/module/${state.module}`);
      } else if (state.route.name === 'module') {
        navigate('/library');
      } else {
        applyFilters();
      }
    });
  }

  async function openDownloadDialog(ids, scope = 'all', eps = []) {
    ids = ids.map(Number).filter(Boolean);
    if (!ids.length) return;
    let detail = null;
    let count = 0;
    let duration = 0;
    let title = '';
    if (ids.length === 1) {
      try {
        detail = (await api(`/dramas/${ids[0]}`)).drama;
        if (!(detail.episodes || []).length || (detail.episodes || []).length < (detail.total_ep || 0)) {
          detail = (await api(`/dramas/${ids[0]}/scan`, { method: 'POST' })).drama;
        }
        let list = detail.episodes || [];
        list = list.filter((e) => e.state !== 'done');
        if (scope === 'eps') list = list.filter((e) => eps.includes(e.ep));
        count = list.length;
        duration = list.reduce((sum, e) => sum + (Number(e.duration) || 0), 0) || count * 480;
        title = detail.title;
        if (!count) { toast('没有需要下载的分集', 'err'); return; }
      } catch (err) { toast(err.message, 'err'); return; }
    } else {
      for (const id of ids) {
        const item = state.items.find((i) => i.id === id);
        if (item) {
          const total = item.total_ep || 0;
          const done = item.done_ep || 0;
          count += total > 0 ? Math.max(0, total - done) : 1;
        } else {
          count += 6;
        }
      }
      if (!count) { toast('所选剧集都已下载完成', 'err'); return; }
      duration = count * 480;
      title = `${ids.length} 部剧集`;
    }
    const estimate = {};
    Object.entries(QUALITY_MBPS).forEach(([key, mbps]) => { estimate[key] = duration * mbps * 1e6 / 8; });
    const free = (state.health && state.health.disk_free) || 0;
    const modal = openModal(`
      ${modalHead('确认下载')}
      <div class="mbody">
        <div class="dl-summary">
          ${detail ? `<img src="${h(detail.cover || '')}" alt="" onerror="this.style.visibility='hidden'">` : ''}
          <div><div class="t">${h(title)}</div><div class="s">共 ${count} 集 · 预计时长 ${Math.round(duration / 60)} 分钟</div></div>
        </div>
        <div class="quality-list">
          ${['hevc', 'h264', '720p'].map((key) => `<label class="quality-opt">
            <input type="radio" name="dl-quality" value="${key}" ${state.quality === key ? 'checked' : ''}>
            <span class="q">${QUALITY_LABEL[key]}</span>
            <span class="size">约 ${fmtBytes(estimate[key])}</span>
          </label>`).join('')}
        </div>
        ${free && estimate.hevc > free ? `<div class="scan-note" style="margin-top:12px;border-color:rgba(255,92,122,.35);color:#ff9b9e">磁盘剩余 ${fmtBytes(free)}，当前体积可能放不下，请先清理空间。</div>` : ''}
        <div class="scan-note" style="margin-top:12px">后台下载，可在「下载队列」看进度；完成后自动写入 NFO/海报。</div>
      </div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn pri" id="dl-confirm">开始下载</button></div>
    `);
    modal.q('#dl-confirm').addEventListener('click', async () => {
      const quality = modal.q('input[name="dl-quality"]:checked').value;
      state.quality = quality;
      localStorage.setItem('hg_quality', quality);
      modal.q('#dl-confirm').disabled = true;
      try {
        let res;
        if (ids.length === 1) {
          res = await api(`/dramas/${ids[0]}/download`, { method: 'POST', body: { scope, eps, quality } });
        } else {
          res = await api('/dramas/batch', { method: 'POST', body: { ids, action: scope === 'new' ? 'download_new' : 'download', quality } });
        }
        modal.close();
        toast(res.message || '已加入下载队列', 'ok');
        refreshActivity();
        if (state.route.name === 'drama') loadDetail(state.route.id);
      } catch (err) {
        modal.q('#dl-confirm').disabled = false;
        toast(err.message, 'err');
      }
    });
  }

  async function openTagDialog(ids) {
    ids = ids.map(Number).filter(Boolean);
    if (!ids.length) return;
    await loadTags();
    let current = new Set();
    if (ids.length === 1 && state.detail && state.detail.id === ids[0]) {
      current = new Set((state.detail.tags || []).filter((t) => t.kind !== 'system').map((t) => t.name));
    }
    const available = state.tags.filter((t) => t.kind !== 'system');
    const modal = openModal(`
      ${modalHead('编辑标签')}
      <div class="mbody">
        <p class="hint" style="color:var(--dim);font-size:11.5px;margin:0 0 12px">点击标签切换选中；输入新名称会同时创建。</p>
        <div class="fchips" id="tag-picker" style="margin-bottom:14px">
          ${available.length ? available.map((t) => `<button class="gb ${current.has(t.name) ? 'on' : ''}" data-tag="${h(t.name)}">${h(t.name)} <i>${t.count}</i></button>`).join('') : '<span style="color:var(--dim);font-size:11.5px">暂无标签</span>'}
        </div>
        <div class="field"><label>新标签（逗号分隔）</label><input type="text" id="tag-new" placeholder="例如：精品, 待二刷"></div>
      </div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn pri" id="tag-save">保存</button></div>
    `);
    $$('[data-tag]', modal.mask).forEach((chip) => chip.addEventListener('click', () => chip.classList.toggle('on')));
    modal.q('#tag-save').addEventListener('click', async () => {
      const picked = $$('[data-tag]', modal.mask).filter((el) => el.classList.contains('on')).map((el) => el.dataset.tag);
      const fresh = modal.q('#tag-new').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      const add = [...new Set([...picked, ...fresh])];
      const remove = [...current].filter((name) => !picked.includes(name));
      try {
        for (const id of ids) await api(`/dramas/${id}/tags`, { method: 'POST', body: { add, remove } });
        modal.close();
        toast('标签已更新', 'ok');
        await loadTags();
        if (state.route.name === 'drama') loadDetail(state.route.id);
        else if (state.route.name === 'tags') renderTagsView();
      } catch (err) { toast(err.message, 'err'); }
    });
  }

  async function openPlayer(dramaId, startEp) {
    let detail = state.detail;
    if (!detail || detail.id !== dramaId) {
      try { detail = (await api(`/dramas/${dramaId}`)).drama; } catch (err) { toast(err.message, 'err'); return; }
    }
    const episodes = detail.episodes || [];
    if (!episodes.length) { toast('还没有分集数据，请先同步详情', 'err'); return; }
    const epNumbers = episodes.map((e) => e.ep);
    let current = epNumbers.includes(Number(startEp)) ? Number(startEp) : epNumbers[0];
    let hls = null;
    const modal = openModal(`
      <div class="mhead" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px 6px"><h2 style="font-size:14px">${h(detail.title)}</h2></div>
      <button class="close" data-close>×</button>
      <div class="mbody" style="padding-top:6px">
        <div class="player-stage"><video id="player-video" controls playsinline></video><div class="player-loading" id="player-loading">正在加载…</div></div>
        <div class="player-bar"><div><div class="title" id="player-title"></div><div class="sub" id="player-sub"></div></div>
          <div class="player-nav"><button class="btn sm" id="player-prev">上一集</button><button class="btn sm" id="player-next">下一集</button></div></div>
        <div class="player-eps" id="player-eps">${episodes.map((ep) => `<button data-ep="${ep.ep}" class="${ep.ep === current ? 'active' : ''}">${ep.ep}</button>`).join('')}</div>
      </div>
    `, { wide: true, className: 'player-modal', onClose: () => { if (hls) hls.destroy(); } });
    const video = modal.q('#player-video');
    const loading = modal.q('#player-loading');
    const titleEl = modal.q('#player-title');
    const subEl = modal.q('#player-sub');

    function load(ep) {
      current = ep;
      loading.style.display = 'grid';
      loading.textContent = '正在加载…';
      titleEl.textContent = `第 ${ep} 集`;
      subEl.textContent = `共 ${detail.total_ep || episodes.length} 集 · ${QUALITY_LABEL[state.quality] || ''}`;
      $$('#player-eps button', modal.mask).forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.ep) === ep));
      if (hls) { hls.destroy(); hls = null; }
      video.removeAttribute('src');
      video.load();
      const src = `/api/watch/${dramaId}/${ep}`;
      if (window.Hls && window.Hls.isSupported()) {
        hls = new window.Hls({ maxBufferLength: 40, enableWorker: true });
        hls.loadSource(src);
        hls.attachMedia(video);
        hls.on(window.Hls.Events.MANIFEST_PARSED, () => { loading.style.display = 'none'; video.play().catch(() => {}); });
        hls.on(window.Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            loading.style.display = 'grid';
            loading.textContent = data.type === window.Hls.ErrorTypes.NETWORK_ERROR ? '播放失败：视频源不可用' : '播放失败：' + data.details;
          }
        });
      } else {
        video.src = src;
        video.play().catch(() => {});
        loading.style.display = 'none';
      }
    }

    modal.q('#player-prev').addEventListener('click', () => {
      const i = epNumbers.indexOf(current);
      if (i > 0) load(epNumbers[i - 1]);
    });
    modal.q('#player-next').addEventListener('click', () => {
      const i = epNumbers.indexOf(current);
      if (i >= 0 && i < epNumbers.length - 1) load(epNumbers[i + 1]);
    });
    modal.q('#player-eps').addEventListener('click', (event) => {
      const btn = event.target.closest('button[data-ep]');
      if (btn) load(Number(btn.dataset.ep));
    });
    load(current);
  }

  // ------------------------------------------------------------------ filters
  function applyFilters() {
    state.page = 1;
    state.selected.clear();
    updateBatchBar();
    updateHeader();
    updateSidebarCounts();
    closeMobileSidebar();
    if (!['library', 'module'].includes(state.route.name)) {
      navigate('/library');
    } else {
      loadLibrary();
    }
  }

  function clearFilter(kind) {
    if (kind === 'module') { state.module = 'all'; navigate('/library'); return; }
    if (kind === 'status') state.status = 'all';
    if (kind === 'tag') state.tag = '';
    if (kind === 'siteTag') state.siteTag = '';
    if (kind === 'score') state.scoreMin = 0;
    if (kind === 'year') state.year = 0;
    if (kind === 'q') { state.q = ''; $('#search-input').value = ''; $('#search-wrap').classList.remove('has'); }
    applyFilters();
  }

  function clearAllFilters() {
    state.module = 'all'; state.status = 'all'; state.tag = ''; state.siteTag = ''; state.scoreMin = 0; state.year = 0; state.q = '';
    $('#search-input').value = '';
    $('#search-wrap').classList.remove('has');
    if (state.route.name !== 'library') navigate('/library');
    else applyFilters();
  }

  // ------------------------------------------------------------------ actions
  async function handleAction(el) {
    const action = el.dataset.action;
    const id = Number(el.dataset.id || 0);
    switch (action) {
      case 'open-scan': openScanModal(); break;
      case 'open-filter': openFilterModal(); break;
      case 'cancel-scan': await api('/scan/cancel', { method: 'POST' }).catch(() => {}); refreshActivity(); break;
      case 'open-root': {
        try { const res = await api('/open-root', { method: 'POST' }); toast(res.message || '已打开下载目录', 'ok'); }
        catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'reload': render(); break;
      case 'go-library': navigate('/library'); break;
      case 'manage-tags': navigate('/tags'); break;
      case 'set-module': {
        const mod = el.dataset.module;
        state.module = mod; state.page = 1;
        if (mod === 'all') {
          if (state.route.name === 'library') loadLibrary(); else navigate('/library');
        } else if (state.route.name === 'module' && state.route.module === mod) {
          loadLibrary();
        } else {
          navigate(`/module/${mod}`);
        }
        break;
      }
      case 'set-status':
        state.status = el.dataset.status;
        applyFilters();
        break;
      case 'set-tag':
        state.tag = el.dataset.tag;
        state.siteTag = '';
        applyFilters();
        break;
      case 'set-site-tag':
        state.siteTag = el.dataset.siteTag;
        state.tag = '';
        applyFilters();
        break;
      case 'clear-filter': clearFilter(el.dataset.kind); break;
      case 'clear-all': clearAllFilters(); break;
      case 'toggle-unwatched':
        state.status = state.status === 'none' ? 'all' : 'none';
        applyFilters();
        break;
      case 'page':
        state.page = Number(el.dataset.page);
        loadLibrary();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        break;
      case 'select-page': selectPage(); break;
      case 'toggle-select': toggleSelect(id); break;
      case 'open-detail': navigate(`/drama/${id}`); break;
      case 'quick-watch': openPlayer(id, 1); break;
      case 'toggle-fav': {
        try {
          const res = await api(`/dramas/${id}/favorite`, { method: 'POST', body: {} });
          const item = state.items.find((i) => i.id === id);
          if (item) item.favorite = res.favorite ? 1 : 0;
          el.classList.toggle('on', !!res.favorite);
          toast(res.favorite ? '已收藏' : '已取消收藏', 'ok');
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'toggle-subscription': {
        try {
          const item = state.items.find((i) => i.id === id);
          const enable = !(item && item.subscribed);
          const res = await api(`/dramas/${id}/subscribe`, {
            method: 'POST',
            body: { enabled: enable, quality: state.quality, auto_download: true },
          });
          if (item) {
            item.subscribed = enable;
            item.sub_quality = res.quality || state.quality;
          }
          el.classList.toggle('on', enable);
          el.title = enable ? '取消订阅' : '订阅追更';
          toast(res.message || (enable ? '已订阅' : '已取消订阅'), 'ok');
          loadStats();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'detail-subscribe': {
        const enable = !state.detail.subscribed;
        try {
          const res = await api(`/dramas/${state.detail.id}/subscribe`, {
            method: 'POST',
            body: { enabled: enable, quality: state.quality, auto_download: true },
          });
          state.detail.subscribed = enable;
          renderDetailView();
          toast(res.message || (enable ? '已订阅' : '已取消订阅'), 'ok');
          loadStats();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'quick-download': openDownloadDialog([id], el.dataset.scope || 'all'); break;
      case 'batch-download': openDownloadDialog([...state.selected], 'all'); break;
      case 'batch-download-new': openDownloadDialog([...state.selected], 'new'); break;
      case 'batch-tag': openTagDialog([...state.selected]); break;
      case 'batch-scan': {
        const res = await api('/dramas/batch', { method: 'POST', body: { ids: [...state.selected], action: 'scan' } }).catch((err) => ({ message: err.message }));
        toast(res.message || '已开始同步', 'ok');
        break;
      }
      case 'batch-fav': {
        const res = await api('/dramas/batch', { method: 'POST', body: { ids: [...state.selected], action: 'favorite', value: 1 } }).catch((err) => ({ message: err.message }));
        toast(res.message || '已收藏', 'ok');
        break;
      }
      case 'batch-clear':
        state.selected.clear();
        syncSelectionDOM();
        break;
      case 'detail-fav': {
        const res = await api(`/dramas/${state.detail.id}/favorite`, { method: 'POST', body: {} });
        state.detail.favorite = res.favorite ? 1 : 0;
        renderDetailView();
        toast(res.favorite ? '已收藏' : '已取消收藏', 'ok');
        break;
      }
      case 'detail-download-all': openDownloadDialog([state.detail.id], 'all'); break;
      case 'detail-download-new': openDownloadDialog([state.detail.id], 'new'); break;
      case 'detail-download-selected': {
        const eps = [...state.epSelected];
        if (!eps.length) { toast('先选中要下载的分集', 'err'); break; }
        openDownloadDialog([state.detail.id], 'eps', eps);
        break;
      }
      case 'detail-watch': openPlayer(state.detail.id, state.epSelected.size ? Math.min(...state.epSelected) : 1); break;
      case 'detail-scan': {
        el.disabled = true;
        try {
          const res = await api(`/dramas/${state.detail.id}/scan`, { method: 'POST' });
          state.detail = res.drama;
          renderDetailView();
          toast('详情已同步', 'ok');
          loadStats();
        } catch (err) { toast(err.message, 'err'); }
        el.disabled = false;
        break;
      }
      case 'detail-nfo': {
        el.disabled = true;
        try { const res = await api(`/dramas/${state.detail.id}/nfo`, { method: 'POST' }); toast(res.message, 'ok'); }
        catch (err) { toast(err.message, 'err'); }
        el.disabled = false;
        break;
      }
      case 'detail-covers': {
        try {
          const res = await api('/covers/backfill', { method: 'POST', body: { drama_ids: [state.detail.id] } });
          toast(res.message || '开始补全集封面', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'batch-covers': {
        const ids = [...state.selected];
        if (!ids.length) { toast('先选择剧集', 'err'); break; }
        try {
          const res = await api('/covers/backfill', { method: 'POST', body: { drama_ids: ids } });
          toast(res.message || '开始补全集封面', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'all-covers': {
        if (!confirm('为所有已下载分集补全封面？')) break;
        try {
          const res = await api('/covers/backfill', { method: 'POST', body: { all: true } });
          toast(res.message || '开始补全', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'detail-folder': {
        try { const res = await api(`/dramas/${state.detail.id}/open-folder`, { method: 'POST' }); toast(res.message || '已打开目录', 'ok'); }
        catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'detail-tag-add': openTagDialog([state.detail.id]); break;
      case 'detail-tag-remove': {
        await api(`/dramas/${state.detail.id}/tags`, { method: 'POST', body: { remove: [el.dataset.name] } });
        state.detail.tags = (state.detail.tags || []).filter((t) => t.name !== el.dataset.name);
        renderDetailView();
        loadTags();
        break;
      }
      case 'detail-plot': {
        const plot = $('#detail-plot');
        const clamped = plot.classList.toggle('clamped');
        el.textContent = clamped ? '展开简介' : '收起简介';
        break;
      }
      case 'ep-select-missing':
        state.epSelected.clear();
        (state.detail.episodes || []).forEach((ep) => { if (ep.state !== 'done') state.epSelected.add(ep.ep); });
        updateEpSelection();
        break;
      case 'ep-select-all':
        state.epSelected = new Set((state.detail.episodes || []).map((ep) => ep.ep));
        updateEpSelection();
        break;
      case 'ep-clear':
        state.epSelected.clear();
        updateEpSelection();
        break;
      case 'update-download': openDownloadDialog([id], 'new'); break;
      case 'update-ignore':
        await api(`/dramas/${id}/clear-update`, { method: 'POST' });
        toast('已忽略该更新', 'ok');
        renderUpdatesView();
        loadStats();
        break;
      case 'update-all': {
        const ids = $$('[data-action="update-download"]').map((b) => Number(b.dataset.id));
        if (ids.length) openDownloadDialog(ids, 'new');
        break;
      }
      case 'sub-download': openDownloadDialog([id], 'new'); break;
      case 'sub-download-all': {
        try {
          const res = await api('/subscriptions/download', { method: 'POST', body: {} });
          toast(res.message || '已加入队列', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'sub-unsubscribe': {
        if (!confirm('取消订阅？不会删除已下载文件。')) break;
        try {
          await api(`/subscriptions/${id}`, { method: 'DELETE' });
          toast('已取消订阅', 'ok');
          renderSubscriptionsView();
          loadStats();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'sched-new': openScheduleModal(); break;
      case 'sched-edit': {
        const schedule = state.schedules.find((s) => Number(s.id) === id);
        if (schedule) openScheduleModal(schedule);
        break;
      }
      case 'sched-toggle': {
        const schedule = state.schedules.find((s) => Number(s.id) === id);
        if (!schedule) break;
        try {
          await api(`/schedules/${id}`, { method: 'PUT', body: {
            name: schedule.name, time: schedule.time, weekdays: schedule.weekdays,
            modules: schedule.modules, details: !!schedule.details, enabled: !schedule.enabled,
          } });
          renderSchedulesView();
          toast(schedule.enabled ? '已暂停' : '已启用', 'ok');
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'sched-run': {
        try {
          const res = await api(`/schedules/${id}/run`, { method: 'POST' });
          toast(res.message || '已开始执行', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'sched-delete': {
        if (!confirm('删除这个定时任务？')) break;
        try {
          await api(`/schedules/${id}`, { method: 'DELETE' });
          toast('定时任务已删除', 'ok');
          renderSchedulesView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'task-cancel':
        try { await api(`/tasks/${id}/cancel`, { method: 'POST' }); toast('已请求取消', 'ok'); refreshActivity(); }
        catch (err) { toast(err.message, 'err'); }
        break;
      case 'retry-all-failed': {
        try {
          const res = await api('/tasks/retry-failed', { method: 'POST' });
          toast(res.message || '已重新入队', 'ok');
          await renderDownloadsView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'clear-failed': {
        if (!confirm('清理所有失败任务记录？分集状态会保留，可重新下载。')) break;
        try {
          await api('/tasks/clear', { method: 'POST', body: { states: ['failed'] } });
          toast('已清理失败记录', 'ok');
          await renderDownloadsView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'task-error': {
        const task = (state.tasks || []).find((t) => Number(t.id) === Number(el.dataset.id));
        if (!task) break;
        openModal(`
          ${modalHead('下载失败')}
          <div class="mbody">
            <div class="task-error-title">${h(task.drama_title || '')} · 第 ${task.ep} 集</div>
            <pre class="task-error-box">${h(task.error || '未知错误')}</pre>
          </div>
          <div class="mfoot"><button class="btn pri" data-close>关闭</button></div>
        `);
        break;
      }
      case 'task-retry': {
        const dramaId = Number(el.dataset.drama);
        const ep = Number(el.dataset.ep);
        try {
          await api(`/dramas/${dramaId}/download`, { method: 'POST', body: { scope: 'eps', eps: [ep], quality: state.quality, force: true } });
          toast('已重新加入队列', 'ok');
          refreshActivity();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'queue-toggle': {
        const paused = !!((state.activity && state.activity.summary) || {}).paused;
        try {
          const res = await api(paused ? '/downloads/resume' : '/downloads/pause', { method: 'POST' });
          toast(res.message || (paused ? '已继续队列' : '已暂停队列'), 'ok');
          await refreshActivity();
          await renderDownloadsView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'tasks-clear':
        await api('/tasks/clear', { method: 'POST' });
        toast('已清理任务记录', 'ok');
        renderDownloadsView();
        break;
      case 'exclude-tag': {
        const input = $('#exclude-tag-input');
        const name = String(el.dataset.name || (input ? input.value : '') || '').trim();
        if (!name) { toast('请输入要排除的标签', 'err'); break; }
        try {
          const res = await api('/excluded-tags', { method: 'POST', body: { name } });
          state.excludedTags = res.excluded || [];
          if (state.tag === name) state.tag = '';
          if (state.siteTag === name) state.siteTag = '';
          await loadStats();
          await loadTags();
          toast(res.message || '已排除标签', 'ok');
          renderTagsView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'restore-tag': {
        const name = String(el.dataset.name || '').trim();
        if (!name) break;
        try {
          const res = await api(`/excluded-tags?name=${encodeURIComponent(name)}`, { method: 'DELETE' });
          state.excludedTags = res.excluded || [];
          await loadStats();
          await loadTags();
          toast(res.message || '已恢复标签', 'ok');
          renderTagsView();
        } catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'tag-create': {
        const name = $('#new-tag-name').value.trim();
        const color = $('#new-tag-color').value;
        if (!name) { toast('请输入标签名', 'err'); break; }
        try { await api('/tags', { method: 'POST', body: { name, color } }); toast('标签已创建', 'ok'); renderTagsView(); }
        catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'tag-delete':
        if (!confirm('删除这个标签？不会删除剧集文件。')) break;
        await api(`/tags/${id}`, { method: 'DELETE' });
        renderTagsView();
        break;
      case 'settings-save': {
        const payload = {};
        $$('[data-setting]').forEach((input) => { payload[input.dataset.setting] = input.value; });
        $$('[data-switch]').forEach((btn) => { payload[btn.dataset.switch] = btn.classList.contains('on') ? '1' : '0'; });
        try { await api('/settings', { method: 'PUT', body: payload }); toast('设置已保存', 'ok'); loadStats(); }
        catch (err) { toast(err.message, 'err'); }
        break;
      }
      case 'check-path': {
        const input = $('#setting-root');
        const message = $('#path-check');
        if (!input) break;
        try {
          const res = await api('/settings/check-path', { method: 'POST', body: { path: input.value.trim() } });
          if (message) message.innerHTML = `目录可用：<code>${h(res.path)}</code> · 剩余 ${fmtBytes(res.free || 0)}`;
          toast('目录可用', 'ok');
        } catch (err) {
          if (message) message.textContent = err.message;
          toast(err.message, 'err');
        }
        break;
      }
      case 'browse-path':
        openFolderPicker();
        break;
      case 'site-check': {
        el.disabled = true;
        const source = el.dataset.source || 'huangguo';
        const result = $('#site-check-result');
        if (result) result.textContent = '正在连接站点…';
        try {
          const res = await api(`/site-check?source=${encodeURIComponent(source)}`);
          if (result) result.textContent = res.message || `站点连接正常 · ${res.latency_ms}ms`;
          toast(res.message || '站点连接正常', 'ok');
        } catch (err) {
          if (result) result.textContent = err.message;
          toast(err.message, 'err');
        } finally {
          el.disabled = false;
        }
        break;
      }
      default: break;
    }
  }

  // ------------------------------------------------------------------- events
  document.addEventListener('click', async (event) => {
    const actionEl = event.target.closest('[data-action]');
    if (actionEl) {
      event.preventDefault();
      event.stopPropagation();
      try { await handleAction(actionEl); } catch (err) { toast(err.message, 'err'); }
      return;
    }
    const switchEl = event.target.closest('[data-switch]');
    if (switchEl) { switchEl.classList.toggle('on'); return; }
    const card = event.target.closest('.card[data-id]');
    if (card) { navigate(`/drama/${card.dataset.id}`); return; }
    const epTile = event.target.closest('.ep[data-ep]');
    if (epTile && state.route.name === 'drama') {
      const ep = Number(epTile.dataset.ep);
      if (state.epSelected.has(ep)) state.epSelected.delete(ep); else state.epSelected.add(ep);
      updateEpSelection();
    }
  });

  document.addEventListener('dblclick', (event) => {
    const epTile = event.target.closest('.ep[data-ep]');
    if (epTile && state.route.name === 'drama' && state.detail) openPlayer(state.detail.id, Number(epTile.dataset.ep));
  });

  document.addEventListener('change', (event) => {
    if (event.target.id === 'sort-filter') {
      state.sort = event.target.value; state.page = 1; loadLibrary();
    } else if (event.target.id === 'detail-quality') {
      state.quality = event.target.value;
      localStorage.setItem('hg_quality', state.quality);
    } else if (event.target.matches('[data-sub-quality], [data-sub-auto]')) {
      const row = event.target.closest('.sub-row');
      if (!row) return;
      const id = Number(event.target.dataset.id);
      const quality = row.querySelector('[data-sub-quality]').value;
      const auto = row.querySelector('[data-sub-auto]').checked;
      api('/subscriptions/auto', { method: 'POST', body: { drama_id: id, quality, auto_download: auto } })
        .then(() => {
          const item = state.subscriptions.find((i) => i.id === id);
          if (item) { item.sub_quality = quality; item.sub_auto = auto; }
          toast('订阅设置已保存', 'ok');
        })
        .catch((err) => toast(err.message, 'err'));
    }
  });

  const debouncedSearch = debounce(() => {
    state.q = $('#search-input').value.trim();
    $('#search-wrap').classList.toggle('has', !!state.q);
    applyFilters();
  }, 320);

  $('#search-input').addEventListener('input', debouncedSearch);
  $('#search-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      state.q = event.target.value.trim();
      $('#search-wrap').classList.toggle('has', !!state.q);
      applyFilters();
    }
  });
  $('#search-clear').addEventListener('click', () => {
    $('#search-input').value = '';
    state.q = '';
    $('#search-wrap').classList.remove('has');
    applyFilters();
  });
  $('#failure-close').addEventListener('click', () => {
    state.failureDismissed = true;
    $('#failure-banner').classList.add('hidden');
  });
  $('#tag-search').addEventListener('input', (event) => {
    state.tagSearch = event.target.value;
    renderTagList();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modalStack.length) { modalStack[modalStack.length - 1](); return; }
    if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $('#search-input').focus();
    }
  });

  $('#btn-scan').addEventListener('click', openScanModal);
  $('#btn-filter').addEventListener('click', openFilterModal);
  $('#btn-open-root').addEventListener('click', async () => {
    try { const res = await api('/open-root', { method: 'POST' }); toast(res.message || '已打开目录', 'ok'); }
    catch (err) { toast(err.message, 'err'); }
  });
  $('#scan-cancel').addEventListener('click', async () => {
    try { await api('/scan/cancel', { method: 'POST' }); toast('正在停止…', 'ok'); } catch (err) { toast(err.message, 'err'); }
  });

  function closeMobileSidebar() {
    $('#sidebar').classList.remove('open');
    $('#side-backdrop').classList.remove('show');
  }
  $('#mobile-menu').addEventListener('click', () => {
    $('#sidebar').classList.toggle('open');
    $('#side-backdrop').classList.toggle('show');
  });
  $('#side-backdrop').addEventListener('click', closeMobileSidebar);

  window.addEventListener('hashchange', () => {
    state.route = parseHash();
    if (state.route.name === 'module') state.module = state.route.module;
    if (state.route.name === 'library') state.module = 'all';
    state.selected.clear();
    updateBatchBar();
    closeMobileSidebar();
    render();
  });

  // -------------------------------------------------------------------- boot
  let renderToken = 0;

  async function render() {
    updateDocumentTitle();
    updateHeader();
    const r = state.route;
    if (r.name === 'library' || r.name === 'module') {
      if (r.name === 'module') state.module = r.module;
      await loadLibrary();
    } else if (r.name === 'drama') {
      await loadDetail(r.id);
    } else if (r.name === 'updates') {
      await renderUpdatesView();
    } else if (r.name === 'downloads') {
      await renderDownloadsView();
    } else if (r.name === 'tags') {
      await renderTagsView();
    } else if (r.name === 'subscriptions') {
      await renderSubscriptionsView();
    } else if (r.name === 'schedules') {
      await renderSchedulesView();
    } else if (r.name === 'settings') {
      await renderSettingsView();
    }
    updateSidebarCounts();
    window.scrollTo({ top: 0 });
  }

  async function boot() {
    state.route = parseHash();
    if (state.route.name === 'module') state.module = state.route.module;
    await Promise.all([loadStats(), loadTags(), loadHealth()]);
    renderSidebar();
    await render();
    refreshActivity();
    setInterval(refreshActivity, 1600);
  }

  boot();
})();











