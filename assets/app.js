import {observeCovers, activateCover} from './covers.js';
import {PAGE_SIZE,PRESETS,defaultFilters,selectItems,summaryCount,highSummaryRange,filterParams,readFilters,displayName} from './filters.js';
const icons = {
  library: '<path d="M4 4h5v16H4zM12 4h4v16h-4zM19 5l3 14"/>',
  chart: '<path d="M4 20h17M7 16v-5M12 16V4M17 16V8"/>',
  history: '<path d="M3 11a9 9 0 1 1 2.7 7M3 4v7h7M12 7v5l3 2"/>',
  bookmark: '<path d="M6 4h12v17l-6-4-6 4z"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  external: '<path d="M14 3h7v7M21 3l-10 10M10 4H4v16h16v-6"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.info}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('zh-CN');
const seasonNumber = n => Number(n || 0).toLocaleString('zh-CN', {maximumFractionDigits: 1});
const pct = (n, digits = 1) => n == null ? '—' : `${(n * 100).toFixed(digits)}%`;
const featureWeight = n => n > 0 && n < .001 ? '&lt;0.1%' : pct(n);
const safeUrl = value => { try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const coverUrl = item => item.image && /^https:\/\/lain\.bgm\.tv\//.test(item.image) ? item.image : /^https:\/\/cdn\.myanimelist\.net\//.test(item.external_image || '') ? item.external_image : `https://api.bgm.tv/v0/subjects/${Number(item.id)}/image?type=common`;
const image = (item, className = '') => `<span class="cover-media" data-state="idle"><img class="${className}" data-src="${esc(coverUrl(item))}" data-cover-state="idle" alt="${esc(displayName(item))}的封面" decoding="async" referrerpolicy="no-referrer"><span class="cover-spinner" aria-hidden="true"></span><span class="cover-error">封面暂缺</span></span>`;
const app = document.querySelector('#app');
let data;
let saved;
try { const ids = JSON.parse(localStorage.getItem('tsuzuku-watchlist') || '[]'); saved = new Set(Array.isArray(ids) ? ids.map(Number).filter(Number.isFinite) : []); } catch { saved = new Set(); }
const state = { ...defaultFilters(), view:'library', page:1, importance:'mdi', dictionary:'', dictionaryExpanded:false, backtestYear:'' };
let returnFocus = null;
let detailRequest=0;
let libraryPromise, analysisPromise;
const detailCache = new Map();
const loading = message => `<div class="loading-line" role="status"><span class="loading-spinner" aria-hidden="true"></span>${esc(message)}</div>`;
function defaultRange() { return data.initial_range || {from:'2006-01-01',to:''}; }
function syncFilterUrl(push=false) {
  const url=new URL(location.href),params=filterParams(state,defaultRange());url.search='';url.hash=state.view+(params?'?'+params:'');
  if(url.href!==location.href)history[push?'pushState':'replaceState'](null,'',url);
}
function changeFilters(patch,{preset='',push=false}={}) {
  Object.assign(state,patch,preset?{context:preset}:{},{preset,page:1});
  syncFilterUrl(push);renderResults();
}
function syncControls() {
  for(const [id,key] of Object.entries({'anime-search':'query','source-filter':'source','status-filter':'scope','sort-filter':'sort','date-from':'dateFrom','date-to':'dateTo','probability-min':'probabilityMin','probability-max':'probabilityMax'})) {
    const el=document.getElementById(id);if(el && el.value!==String(state[key]))el.value=state[key];
  }
  document.querySelector('#probability-value').textContent=`${state.probabilityMin}–${state.probabilityMax}%`;
  const slider=document.querySelector('.range-sliders');
  slider.style.setProperty('--low',state.probabilityMin+'%');slider.style.setProperty('--high',state.probabilityMax+'%');
  for(const [id,other] of [['min','probabilityMax'],['max','probabilityMin']]){
    const el=document.querySelector('#probability-'+id);
    el.setAttribute(id==='min'?'aria-valuemax':'aria-valuemin',state[other]);
    el.setAttribute('aria-valuetext',`${el.value}%`);
  }
}


const titles = { library: '续作片单', features: '特征实验室', backtest: '时间回测', watchlist: '我的关注', about: '模型与数据' };
const shortTags = item => item.tags.filter(t => !/^\d{4}|日本|TV|WEB|OVA|[\u3040-\u30ff]/.test(t) && t.length < 9 && !['漫画改','漫改','原创','小说改','游戏改'].includes(t)).slice(0, 3);
const tagButtons = tags => tags.map(t => `<button class="tag" data-tag="${esc(t)}">${esc(t)}</button>`).join('');

document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
document.querySelector('#menu-toggle').innerHTML = icon('menu');
document.querySelector('#menu-toggle').addEventListener('click', e => {
  const open = document.querySelector('.site-sidebar').classList.toggle('open');
  e.currentTarget.setAttribute('aria-expanded', String(open));
  e.currentTarget.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
});

function notice() {
  const sample = data.mode === 'api_sample';
  const strict = data.validation === 'temporal_release_events';
  const message = sample ? `联调预览：${num(data.coverage.anime_records)} 条 Bangumi 数据；当前特征回看历史，非严格回测。` : strict ? '发行事件与动态证据按时间截断；原作类型等固定属性由当前归档重建。' : `2006–${data.snapshot_at.slice(0,4)} 全量归档 · ${num(data.coverage.records_since_2006)} 条动画记录。当前标签与原作资料回看历史，仍属探索回测。`;
  return `<div class="notice"><div>${icon('info')}<span>${message}</span></div><a href="#about">数据说明 ${icon('arrow')}</a></div>`;
}

function heading(title, sub, action = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${sub}</p></div>${action}</div>`;
}

function filteredItems() {
  return selectItems(state.view==='watchlist'?data.items.filter(i=>saved.has(i.id)):data.items,state);
}
function isInitialSelection() {
  return state.view==='library' && Object.entries(defaultFilters(defaultRange())).every(([key,value])=>['preset','context'].includes(key)||state[key]===value);
}
const sourceStatus = source => ({completed:'原作已完结',ongoing:'原作连载中',unknown:'原作状态待核实'})[source?.status] || '原作待关联';
const visibleDisc = e => e.disc_average != null || e.bd_copies != null ? e : e.previous_disc || e;
function discValue(e) { e=visibleDisc(e); return e.disc_average != null ? num(e.disc_average) : e.bd_copies != null ? num(e.bd_copies) : '—'; }
function discLabel(e) { if(e.disc_average == null && e.bd_copies == null && e.previous_disc) return e.previous_disc.disc_provider ? '前作已知卷累平' : '前作碟片卷均'; return e.disc_provider ? '已知卷累平 · BD+DVD' : e.disc_average != null ? '碟片卷均 · BD+DVD' : '碟片销量'; }
function endpointValue(e) { return e.adaptation_endpoint != null ? `${e.adaptation_endpoint} / ${e.adaptation_total}<small>${esc(e.adaptation_unit)}</small>` : e.source_total_units && e.adapted_units != null ? `${e.adapted_units} / ${e.source_total_units}<small>${esc(e.unit)}</small>` : '—<small>待核实</small>'; }

function card(item, idx) {
  const previous = item.series.filter(s => s.id !== item.id && s.date && s.date <= (data.prediction_at || data.snapshot_at));
  const members = [...previous.slice(-2), item];
  const art = `<div class="poster-column"><div class="cover-stack" data-poster-stack style="--poster-count:${members.length}">${members.map((s,n) => `<button class="poster-leaf ${n === members.length-1 ? 'active' : ''}" data-poster="${n}" data-detail="${item.id}" data-poster-name="${esc(displayName(s))}" style="--poster-index:${n};z-index:${8-Math.abs(n-members.length+1)}" aria-label="查看作品详情：${esc(displayName(s))}" aria-pressed="${n === members.length-1}">${image(s,'cover',idx>3)}<span class="poster-year">${esc(s.date?.slice(0,4) || s.platform)}</span></button>`).join('')}</div><div class="poster-tabs" aria-label="选择系列封面">${members.map((s,n)=>`<button data-poster-tab="${n}" aria-label="显示${esc(displayName(s))}封面" aria-pressed="${n===members.length-1}">${n+1}</button>`).join('')}<span>悬浮切换</span></div><p class="poster-caption" title="${esc(displayName(item))}">${esc(displayName(item))}</p></div>`;
  const e = item.enrichment, original = item.original;
  const value = item.status === 'predict' ? `<strong class="prediction-value">${(item.probability * 100).toFixed(0)}<small>%</small></strong>` : `<strong class="prediction-known">${item.status === 'known' ? '下一季已收录' : '系列已完结'}</strong>`;
  const progress = item.status === 'predict' ? `<div class="probability-bar" role="meter" aria-label="将来还会有下一季的模型估计概率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${(item.probability * 100).toFixed(1)}"><span style="width:${item.probability * 100}%"></span></div>` : '';
  return `<article class="anime-card" data-id="${item.id}">
    <div class="card-main">${art}<div class="card-info"><div class="season-priority"><strong>${seasonNumber(item.progress.equivalent)}<small>季进度</small></strong><span>${item.progress.ova_episodes ? `含 ${item.progress.ova_episodes} 集 OVA` : `${item.progress.regular} 季正式作品`}</span></div><div class="card-title-row"><button class="card-title" data-detail="${item.id}">${esc(displayName(item))}</button><button class="bookmark-button" data-save="${item.id}" aria-label="${saved.has(item.id) ? '取消关注' : '关注'} ${esc(displayName(item))}" aria-pressed="${saved.has(item.id)}">${icon('bookmark')}</button></div>
    <p class="japanese-name" lang="ja">${esc(item.name)}</p><div class="card-meta"><span>${esc(item.date.slice(0, 7).replace('-', ' / '))}</span><span>${esc(item.source)}</span><span>${item.episodes ? item.episodes + ' 集' : item.platform}</span></div>
    <div class="card-tags">${tagButtons(shortTags(item))}</div>
    <div class="prediction-line"><span class="label">${item.status === 'predict' ? '将来还会有下一季' : '系列最新状态'}</span>${value}</div>${progress}</div></div>
    ${item.tags.length ? `<details class="all-tags"><summary><span>全部标签 <b>${item.tags.length}</b></span><span class="tags-open-label">展开</span><span class="tags-close-label">收起</span></summary><div class="tag-list">${tagButtons(item.tags)}</div></details>` : ''}
    <div class="original-strip"><span class="source-kind">${esc(item.source)}</span>${original ? `<a href="${esc(safeUrl(original.url))}" target="_blank" rel="noreferrer" title="关联原作：${esc(original.name_cn)}">${esc(original.name_cn)} ${icon('external')}</a><span class="source-status ${original.status}">${sourceStatus(original)}</span>` : `<span class="muted">${item.source === '原创' ? '原创动画 · 无出版原作' : '尚无明确原作关联'}</span>`}</div>
    <div class="card-evidence"><div class="evidence-cell"><div class="evidence-label">原作已出版</div><div class="evidence-value">${original?.volumes != null ? num(original.volumes)+'<small>卷</small>' : '—<small>待核实</small>'}</div></div>
    <div class="evidence-cell"><div class="evidence-label">改编终点 / 出版量</div><div class="evidence-value ${e.adaptation_endpoint == null ? 'missing' : ''}">${endpointValue(e)}</div></div>
    <div class="evidence-cell"><div class="evidence-label">${discLabel(e)}</div><div class="evidence-value ${discValue(e) === '—' ? 'missing' : ''}">${discValue(e)}<small>${discValue(e) === '—' ? '待补充' : '张'}</small></div></div></div>
    <div class="card-foot"><span class="foot-status"><span></span>${item.status === 'predict' ? '时间截断模型 · 未校准' : item.status === 'completed' ? '查看完结证据' : `${item.series_count || item.series.length} 部关联作品`}</span><button class="text-button" data-detail="${item.id}">${item.status === 'predict' ? '查看预测依据' : '查看系列档案'} ${icon('arrow')}</button></div></article>`;
}

function renderCounts() {
  const items=state.view==='watchlist'?data.items.filter(i=>saved.has(i.id)):data.items;
  const high=highSummaryRange(state);
  return [['recent','近一年有续作'],['high','预测高概率'],['uncontinued','还没有续作'],['all','全部作品']].map(([key,name])=>{
    const count=data.libraryLoaded?summaryCount(items,key,state):data.initial_counts[key];
    const canShow=data.libraryLoaded||(state.view==='library'&&(key!=='high'||(high.probabilityMin===30&&high.probabilityMax===100)));
    return `<button class="count-filter ${state.preset===key?'selected':''}" data-preset="${key}" aria-pressed="${state.preset===key}" ${key==='high'?'title="未进入时预览 30–100% 的作品数；进入后随概率滑块变化。点击恢复全部状态、30–100% 和社区评分排序。"':''}><span>${name}</span><strong>${canShow?num(count):'—'}</strong><small>${{recent:'近一年续作 · 最新播出优先',high:`${state.context==='high'?'当前':'切换后'}概率 ${high.probabilityMin}–${high.probabilityMax}%`,uncontinued:state.preset==='uncontinued'&&isInitialSelection()?'首页默认 · 首播至少一年前':'尚无后续正篇 · 截止日期不限',all:'全部系列 · 不限日期与状态'}[key]}</small>${state.preset===key?'<i class="filter-dot"></i>':''}</button>`;
  }).join('');
}

function renderResults({force=false}={}) {
  const items = filteredItems();
  const pendingSelection=!data.libraryLoaded&&!isInitialSelection();
  const count=data.libraryLoaded?items.length:pendingSelection?null:data.initial_total;
  syncControls();
  document.querySelector('#count-filters').innerHTML = renderCounts();
  document.querySelector('#result-count').innerHTML = `<strong>${state.view === 'watchlist' ? '关注的故事' : '系列观察档案'}</strong><span> ${count==null?'完整片单读取中':num(count)+' 个系列'}</span>${state.tag ? ` <span class="filter-tag">${esc(state.tag)}<button data-clear-tag aria-label="清除标签筛选">×</button></span>` : ''}`;
  const grid=document.querySelector('#anime-grid'), ids=items.slice(0,state.page*PAGE_SIZE).map(i=>i.id).join(',');
  if(pendingSelection){grid.dataset.displayed='loading';grid.innerHTML=loading('正在查找完整片单…');}
  else if(force || !items.length || grid.dataset.displayed!==ids) {
  grid.dataset.displayed=ids;
  grid.innerHTML = items.length ? items.slice(0, state.page * PAGE_SIZE).map(card).join('') : `<div class="empty-state"><h2>${state.view === 'watchlist' && !saved.size ? '留一个位置，等它的下一季。' : '没有找到符合条件的系列'}</h2><p>${state.view === 'watchlist' && !saved.size ? '在片单中点击书签，就能把想继续追的动画放在这里。关注记录只保存在当前浏览器。' : '这些条件下没有匹配作品。可分别调整状态、概率、日期、原作或标签，也可以清除全部筛选。'}</p>${state.view === 'watchlist' && !saved.size ? '<a class="primary-button" href="#library">浏览续作片单</a>' : '<button class="outline-button" data-reset>清除筛选</button>'}</div>`;
  }
  const rangeNote=document.querySelector('#range-note');
  if(rangeNote)rangeNote.textContent=state.dateFrom && state.dateTo && state.dateFrom>state.dateTo ? '起始日期不能晚于结束日期。' : `首播范围：${state.dateFrom || '不限'} 至 ${state.dateTo || '不限'}${state.probabilityMin===0&&state.probabilityMax===100?' · 全部概率包含无预测值作品':' · 当前概率区间仅含有预测值作品'}`;
  document.querySelectorAll('#count-filters button,.controls input,.controls select,[data-reset],[data-export]').forEach(el=>el.disabled=!data.libraryLoaded);
  document.querySelector('#pagination').innerHTML = !data.libraryLoaded ? `${loading('片单已就绪，正在准备完整筛选…')}<button class="text-button" data-load-library>重试</button>` : items.length > state.page * PAGE_SIZE ? `<span>已显示 ${state.page * PAGE_SIZE} / ${num(count)} 个系列</span><button class="outline-button" data-more>再看 ${PAGE_SIZE} 个系列 ${icon('arrow')}</button>` : items.length ? `<span>已显示全部 ${num(count)} 个系列</span>` : '';
  attachImageFallbacks();
}

function library() {
  const maxDate=data.prediction_at || data.snapshot_at;
  app.innerHTML=heading(state.view==='watchlist'?'把期待，留给下一季。':'故事，还会继续吗？',state.view==='watchlist'?'收藏你想继续追的系列，看看数据里的下一种可能。':'从第一季到下一季，用数据读懂每一份期待。',`<button class="outline-button" data-export>${icon('download')}<span>导出片单</span></button>`)+`
    <div class="count-filters" id="count-filters" aria-label="快捷筛选预设"></div>
    <p class="scope-note">高概率数字预览切换后的结果，进入后随概率滑块变化。首页默认保留日期限制；手动调整后取消高亮。</p>
    <div class="controls">
      <label class="search-box">${icon('search')}<input id="anime-search" type="search" placeholder="搜索片名、公司、标签" aria-label="搜索动画、制作公司或标签"><kbd>/</kbd></label>
      <fieldset class="date-range"><legend>首播日期</legend><label><span>从</span><input id="date-from" type="date" aria-label="首播起始日期" max="${maxDate}"></label><span class="range-separator">—</span><label><span>至</span><input id="date-to" type="date" aria-label="首播结束日期" max="${maxDate}"></label></fieldset>
      <label class="select-label"><span>状态</span><select id="status-filter" aria-label="续作状态">${[['all','全部状态'],['recent','近一年有续作'],['uncontinued','还没有续作'],['known','下一季已收录'],['completed','已完结']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label>
      <label class="select-label"><span class="sr-only">原作类型</span><select id="source-filter" aria-label="原作类型"><option value="">全部原作</option>${['漫画改','小说改','原创','游戏改','未知'].map(s=>`<option>${s}</option>`).join('')}</select></label>
      <label class="select-label"><span class="sr-only">排序</span><select id="sort-filter" aria-label="排序">${[['probability','续作概率优先'],['score','社区评分优先'],['date','最新播出优先'],['seasons','季度进度优先']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label>
      <fieldset class="probability-range"><legend>续作概率</legend><output id="probability-value" for="probability-min probability-max"></output><div class="range-sliders"><div class="range-track"></div><input id="probability-min" type="range" min="0" max="100" step="1" aria-label="续作概率下限"><input id="probability-max" type="range" min="0" max="100" step="1" aria-label="续作概率上限"></div><div class="range-labels"><span>0%</span><span>100%</span></div></fieldset>
    </div>
    <div class="date-presets"><button class="text-button" data-date-preset="older">一年以前</button><button class="text-button" data-date-preset="all">不限日期</button><span id="range-note"></span></div>
    <div class="section-line"><div id="result-count" aria-live="polite"></div><button class="text-button" data-reset>清除筛选 ${icon('arrow')}</button></div><div class="anime-grid" id="anime-grid"></div><div class="load-more" id="pagination"></div>`;
  renderResults();
  document.querySelector('#anime-search').addEventListener('input',e=>changeFilters({query:e.target.value}));
  for(const [id,key] of [['source-filter','source'],['sort-filter','sort'],['status-filter','scope'],['date-from','dateFrom'],['date-to','dateTo']])document.getElementById(id).addEventListener('change',e=>changeFilters({[key]:e.target.value},{push:true}));
  for(const [id,key] of [['min','probabilityMin'],['max','probabilityMax']])document.getElementById('probability-'+id).addEventListener('input',e=>{
    const value=Number(e.target.value);
    changeFilters({[key]:id==='min'?Math.min(value,state.probabilityMax):Math.max(value,state.probabilityMin)});
  });
}

const definitions = {
  age_days:'最新一季首播到预测截点的天数，由生存公式单独处理等待时间。',
  regular_seasons:'截点前已首播的正式 TV / WEB 季数；按续集关系归组。',
  season_equivalent:'正式季数 + 0.1 × 每集至少 21 分钟的已播 OVA 集数。',
  release_year:'首播年份编码为连续数值，例如 2015年 → 2015；不拆成布尔标签。',
  release_month:'首播月份编码为 1–12。',
  release_quarter:'由首播月份计算 1–4 季度。',
  month_sin:'首播月份的正弦周期编码，与余弦一起表达年末与年初的相邻关系。',
  month_cos:'首播月份的余弦周期编码。',
  gap_variation_days:'当前季首播前已发生的续季间隔，最大值减最小值。',
  prior_year_releases:'当前季首播之前 365 天已发行的日本 TV / WEB 正篇数量；不包含同日及未来首播。',
  series_age_days:'从系列第一季到当前季首播的天数，只使用当时已播作品。',
  previous_gap_days:'当前季和上一季的首播间隔（天）；没有前作时以 0 占位并附缺失标记。',
  mean_gap_days:'系列此前各季首播间隔的平均值，排除未来作品。',
  cadence_missing:'尚无足够的已播正篇，不能计算历史续季间隔。',
  seasons_last_3y:'当前季首播时，过去三年里已首播的正式季数。',
  recent_extra_count:'当前季首播前两年内，已首播的非正篇关联作品数量。',
  log_previous_disc_average:'同系列最近一部有合格历史记录的前作，其 BD + DVD 卷均销量的 log(1 + 张数)。',
  log_previous_disc_average_missing:'这一季首播前没有可用的前作碟片销量观测。数值以 0 占位，同时输入该缺失标记；不等同于销量为零。',
  log_previous_recent_disc_average:'前作 BD + DVD 已知卷累平的 log(1 + 张数)，与旧卷均口径分开；仅在训练中有合格观察值时保留。',
  log_previous_recent_disc_average_missing:'缺少这一季首播前可用的前作已知卷累平观测。',
  source_age_days:'本季同类型出版原作的最早发行日距本季首播的天数；未来发行的原作不纳入。固定日期由当前归档重建，不使用当前卷数。',
  source_age_missing:'缺少本季首播前的原作发行日期，或原创 / 游戏等不适用出版原作日期的类型。',
  studio_missing:'Bangumi 动画制作署名未提供，单独标记未知，不当作没有制作公司。',
  studio_prior_releases:'该制作公司在本季首播前已发行的 TV / WEB 正篇数。同日和未来作品不计入；合制时取最大值避免重复计数。',
  studio_prior_year_releases:'制作公司在本季首播前一年内的正篇发行数，只统计已经发生的发行事件。'
};

function definition(key) {
  if (key.startsWith('studio=')) return `动画制作署名是否包含“${key.slice(7)}”，取值为 0 或 1。固定署名由当前归档重建；类别只依据训练集筛选。`;
  if (key.startsWith('tag=')) return `Bangumi 标签“${key.slice(4)}”是否存在，取值为 0 或 1。用户标签可能有同义词与噪声；相关性不代表制作原因。`;
  if (key.startsWith('source=')) return `原作类型是否为“${key.slice(7)}”，取值为 0 或 1。漫画、小说、原创、游戏、未知分别编码，没有大小次序。假定类型在首播时已确定，由当前归档重建。`;
  if (key.startsWith('platform=')) return `当前作品是否为 ${key.slice(9)} 播出形式，取值为 0 或 1。`;
  return definitions[key] || '模型输入特征。缺失值通过独立标记处理。';
}

function featureChart() {
  const permutation = state.importance === 'permutation';
  const source = permutation ? data.backtest?.permutation || [] : data.model.global_importance.filter(r=>r.value>0);
  const rows = source.slice(0, 12);
  const max = Math.max(...rows.map(r => Math.abs(r.value)), .00001);
  document.querySelector('#importance-bars').innerHTML = rows.length ? rows.map(r => `<div class="feature-row"><span class="feature-name" title="${esc(definition(r.key))}">${esc(r.name)}</span><div class="feature-track"><div class="feature-fill" style="width:${Math.abs(r.value) / max * 100}%;${r.value < 0 ? 'background:#a69aa3' : ''}"></div></div><span class="feature-number">${permutation ? (r.value > 0 ? '+' : '') + r.value.toPrecision(2) : featureWeight(r.value)}</span></div>`).join('') : '<p class="muted">没有足够的留出样本，置换重要性暂不可用。</p>';
  document.querySelector('#importance-caption').textContent = permutation ? `最近回测（${data.backtest?.cutoff || '—'}）中，一个独立系列留出折上的删失损失增量，重复 3 次。越大越有帮助；负值表示打乱后更好。评估该回测模型权重前 20 项，与当前主模型的训练权重分别展示。` : `${data.model.importance_description}；这里显示前 12 项。权重不是因果效应；等待时长在生存公式中单独计算。`;
  document.querySelectorAll('[data-importance]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.importance === state.importance)));
}

function dictionary() {
  const rows=data.model.global_importance;
  const q=state.dictionary.trim().toLowerCase();
  const filtered=rows.filter(r=>`${r.name} ${r.key} ${definition(r.key)}`.toLowerCase().includes(q));
  const visible=state.dictionaryExpanded?filtered:filtered.slice(0,24);
  document.querySelector('#dictionary-count').textContent=`已显示 ${num(visible.length)} / ${num(filtered.length)} 项${q?'匹配结果':''}`;
  const toggle=document.querySelector('[data-dictionary-toggle]');toggle.hidden=filtered.length<=24;
  toggle.textContent=state.dictionaryExpanded?'收起词典':`查看全部 ${num(filtered.length)} 项`;
  toggle.setAttribute('aria-expanded',String(state.dictionaryExpanded));
  document.querySelector('#dictionary-items').innerHTML=visible.length?visible.map(r=>`<div class="dictionary-item" data-feature="${esc(r.key)}"><h3>${esc(r.name)}</h3><p>${esc(definition(r.key))}</p><span class="weight">${r.key==='age_days'?'已使用 · 通过生存公式计算':'模型特征权重 '+featureWeight(r.value)}</span></div>`).join(''):'<p class="muted">没有匹配的模型特征，可搜索“季度”“年份”或“前作销量”。</p>';
}

function trainingCoverage() {
  const c=data.model.training_coverage;
  if(!c)return '';
  const salesUsed=data.model.global_importance.some(r=>r.key.startsWith('log_previous_'));
  return `<section class="panel full-panel" id="training-coverage"><div class="season-analysis"><div><h2>训练时点的资料覆盖</h2><p>每条训练记录是一季，分母为 ${num(c.n)} 条；可用资料还需通过训练内的贡献筛选。</p><div class="coverage-list">${[['已知原作类型',c.source_known],['有效制作署名',c.studio_known],['首播前原作发行日期',c.source_age_known],['首播时可用的前作销量',c.previous_sales_known]].map(([name,v])=>`<div class="coverage-row"><div><span>${name}</span><span class="num">${num(v)} / ${num(c.n)}</span></div><div class="track"><span style="width:${v/c.n*100}%"></span></div></div>`).join('')}</div></div><div class="season-insight"><span>首播时可用的前作销量</span><strong>${num(c.previous_sales_known)} / ${num(c.n)}</strong><p>${salesUsed?'前作销量相关字段已保留在本次模型中；缺失值与真实零销量分别处理。':'前作销量候选字段在本次训练中贡献为零，已从模型剔除。资料仍可在作品旁查看。'}当前季销量排除；首播之后才取得的记录不倒填。</p><p>已知来源不保证预测一定更准，缺失率也不是特征重要性。</p></div></div></section>`;
}

function sourceAnalysis() {
  const sources=(data.model.source_breakdown||[]).filter(s=>s.importance>0);
  if(!sources.length)return '';
  const max=Math.max(...sources.map(s=>s.importance),.00001);
  return `<section class="panel full-panel" id="source-analysis"><div class="season-analysis"><div><h2>改编来源的实际贡献</h2><p class="panel-subtitle">${sources.length} 项来源编码保留 · 条形长度按本组最大权重归一</p><div class="coverage-list">${sources.map(s=>`<div class="coverage-row"><div><span>${esc(s.source)} · ${num(s.n)} 条</span><span class="num">权重 ${featureWeight(s.importance)}</span></div><div class="track"><span style="width:${s.importance/max*100}%"></span></div></div>`).join('')}</div></div><div class="season-insight"><span>原作类型合计权重</span><strong>${pct(sources.reduce((sum,s)=>sum+s.importance,0))}</strong><p>原作类型分别编码，没有大小次序。训练中零贡献的类别列已经剔除，随后重新拟合。权重表示模型依赖程度，不是该类作品的续作概率，也不代表因果。</p></div></div></section>`;
}

function featuresView() {
  const c = data.coverage;
  const n = c.displayed;
  app.innerHTML = heading('每一个判断，都有迹可循。', `保留 ${num(data.model.features)} 项输入${data.model.feature_selection ? ` · 已剔除 ${num(data.model.feature_selection.zero_removed.length)} 项零贡献字段` : ''}。`) + notice() +
    `<div class="analysis-grid"><section class="panel"><h2>哪些特征，最影响判断？</h2><p class="panel-subtitle">全局重要性 / 数值越大，模型越依赖这一信息</p><div class="tabs" role="tablist" aria-label="重要性计算方法"><button data-importance="mdi" role="tab" aria-selected="${state.importance === 'mdi'}">模型特征权重</button><button data-importance="permutation" role="tab" aria-selected="${state.importance === 'permutation'}">留出集置换检验</button></div><div class="feature-bars" id="importance-bars"></div><p class="feature-caption" id="importance-caption"></p></section>
    <section class="panel"><h2>现在能查到多少资料？</h2><p class="panel-subtitle">展示资料覆盖 · ${num(n)} 个系列 · 不代表历史训练可用</p><div class="coverage-list">${[['已关联出版原作',c.original],['当前原作卷数',c.source_volumes],['原作完结 / 连载状态',c.source_status],['已核实改编终点',c.adaptation],['当前可查销量记录',c.bd]].map(([name,v]) => `<div class="coverage-row"><div><span>${name}</span><span class="num">${num(v)} / ${num(n)}</span></div><div class="track"><span style="width:${n ? v/n*100 : 0}%"></span></div></div>`).join('')}</div>
    <div class="annotation">${icon('info')}<span>展示资料来自 Bangumi、MangaUpdates、旧销量研究与 2021 年起的新销量汇总。展示覆盖率不等于训练覆盖率：晚于观察时点的值不进入历史模型。</span></div><div class="explain-note"><h3>严格控制每一项信息的时间</h3><p>原作类型、制作署名与发行日期由当前归档重建，假定首播时已确定。动态证据仅使用首播前的合格观测。当前季销量不参与训练。缺失数值带有缺失标记；全无合格值、常量与零贡献的字段，仅在当次训练数据内筛除，再重新训练。</p></div><div class="explain-note"><h3>重要性 ≠ 促成续作的原因</h3><p>标签可能相互关联。想知道一部具体动画为什么得到这个概率，请在片单中打开“预测依据”。</p></div></section></div>
    ${sourceAnalysis()}${trainingCoverage()}<section class="panel full-panel"><div class="season-analysis"><div><h2>已经有几季，还会影响下一季吗？</h2><p class="panel-subtitle">历史训练样本中已观察到续作的比例 / 按当时正式季数分组</p><div id="season-plot" class="season-plot"></div><p class="small-note">${(data.model.season_breakdown || []).map(b=>`${b.label}：${b.positive_n}/${b.n} 条`).join('；')}。每一季只计一次；未观察到续作不代表永远没有，样本观察时长各不相同。</p></div><div class="season-insight"><span>模型中的季度特征</span><strong>${pct(data.model.global_importance.filter(r=>['regular_seasons','season_equivalent'].includes(r.key)).reduce((sum,r)=>sum+r.value,0))}</strong><p>正式季数与含 OVA 的季度进度，合计模型特征权重。</p><p>左侧是样本中的相关性。不同季数的样本量与作品构成不同，不能解释成“多做一季就会提高续作机会”。</p></div></div></section>

    <section class="panel full-panel"><div class="panel-head"><div><h2>把近二十年的作品，都放进视野。</h2><p class="panel-subtitle">2006 年起 · 官方归档中的非成人动画 · 所有地区与形式</p></div><strong>${num(c.records_since_2006)} <span class="muted">条已首播记录</span></strong></div><div class="year-bars" aria-label="各年动画记录数">${(c.years || []).map(b=>{const height=b.n/Math.max(...c.years.map(t=>t.n))*140;return `<div class="year-bar" tabindex="0" style="--height:${height}px" aria-label="${b.year} 年 ${b.n} 条记录"><b>${b.year} · ${num(b.n)}</b><i style="height:${height}px"></i><span>${String(b.year).slice(2)}</span></div>`}).join('')}</div><p class="small-note">预测片单进一步筛选日本 TV / WEB 正篇，并合并续作。图中包含当年日期已知的待播作品；当年数据尚未完整。记录数不等于独立系列数。</p></section>
    <section class="panel full-panel"><div class="panel-head"><div><h2>特征词典</h2><p class="panel-subtitle">当前模型实际使用的输入、编码与权重</p></div><label class="search-box">${icon('search')}<input id="dictionary-search" type="search" placeholder="搜索季度、年份、前作销量" aria-label="搜索特征词典" value="${esc(state.dictionary)}"></label></div><p class="dictionary-note">只列当前模型保留的输入。零贡献字段已从训练输入中删除并重新拟合；等待天数通过生存公式使用，不按树分裂权重计算。</p><div class="dictionary-toolbar"><span id="dictionary-count" aria-live="polite"></span><button class="outline-button" data-dictionary-toggle aria-controls="dictionary-items" aria-expanded="false"></button></div><div class="dictionary-grid" id="dictionary-items"></div></section>`;
  featureChart(); dictionary();
  requestAnimationFrame(drawSeason);
  document.querySelector('#dictionary-search').addEventListener('input', e => { state.dictionary = e.target.value; state.dictionaryExpanded = false; dictionary(); });
}

function activeReport() {
  const years = data.annual_backtest?.years || [];
  return years.find(r => r.cutoff === state.backtestYear) || data.backtest;
}

function backtest() {
  const r = activeReport();
  if (!r) { app.innerHTML = heading('长期续作，逐年验收。','等待有足够观察期的数据。'); return; }
  const years = data.annual_backtest?.years || [r];
  const summary = data.annual_backtest?.summary;
  const m = r.metrics, [[pendingOut,pendingIn],[miss,hits]] = r.confusion;
  const selected = pendingIn + hits;
  const titleMap = new Map(data.items.flatMap(i => [i,...i.series]).map(i=>[i.id,i.name_cn || i.name]));
  app.innerHTML = heading('长期续作，逐年验收。', '预测将来是否还会有下一季；每年检查当年实际推出续作的作品是否被提前识别。', `<button class="outline-button" data-report>${icon('download')}<span>下载完整报告</span></button>`) + notice() +
    `<div class="backtest-toolbar"><div><strong>${years.length} 个年度截点</strong><span>${summary ? `累计找回 ${num(summary.confusion[1][1])} / ${num(summary.positive_n)} 次年度续作 · ${pct(summary.recall)}` : '本次只运行最新截点'}</span></div><label>查看年度 <select id="backtest-year" aria-label="回测截点年份">${[...years].reverse().map(y=>`<option value="${y.cutoff}" ${y.cutoff===r.cutoff?'selected':''}>${y.cutoff.slice(0,4)} 截点 → ${y.outcome_end.slice(0,4)}</option>`).join('')}</select></label></div>
    <div class="stats-strip"><div class="stat"><span class="stat-label">当年续作召回率</span><div class="stat-number">${pct(m.recall)}</div><p class="stat-note">找回 ${hits} / ${r.positive_n} 个续作</p></div><div class="stat"><span class="stat-label">当年续作比例</span><div class="stat-number">${pct(m.precision)}</div><p class="stat-note">候选名单中 ${hits} / ${selected} 部在 ${r.outcome_end.slice(0,4)} 年推出续作</p></div><div class="stat"><span class="stat-label">前 10% 名单召回率</span><div class="stat-number">${pct(r.ranking.recall)}</div><p class="stat-note">固定名额 ${r.ranking.top_n} 部 · 命中 ${r.ranking.hits} 部</p></div><div class="stat"><span class="stat-label">截点前全部系列</span><div class="stat-number">${num(r.test_n)}</div><p class="stat-note">含多年前作品 · 按年向前验证</p></div></div>
    <section class="panel full-panel"><div class="panel-head"><div><h2>能持续找回续作，才有参考价值。</h2><p class="panel-subtitle">按年查看召回率，保留固定 50% 阈值作为参照</p></div><span class="small-note">2010 年起回测 · 2006 年起训练资料</span></div><div class="chart-legend"><span><i></i>历史验证选择阈值</span><span><i></i>固定 50% 阈值</span></div><div id="annual-plot" class="annual-plot"></div><details class="annual-details"><summary>查看 ${years.length} 年完整数字</summary><div class="table-wrap" tabindex="0" aria-label="完整年度回测表，可左右滚动"><table class="backtest-table annual-table"><thead><tr><th>截点 → 验收年</th><th>全部系列</th><th>当年正例</th><th>命中</th><th>召回率</th><th>当年续作比例</th><th>入选阈值</th></tr></thead><tbody>${years.map(y=>`<tr ${y.cutoff===r.cutoff?'class="active-year"':''}><td><button data-year="${y.cutoff}" class="text-button">${y.cutoff.slice(0,4)} → ${y.outcome_end.slice(0,4)}</button></td><td>${num(y.test_n)}</td><td>${y.positive_n}</td><td>${y.confusion[1][1]}</td><td>${pct(y.metrics.recall)}</td><td>${pct(y.metrics.precision)}</td><td>${pct(y.threshold)}</td></tr>`).join('')}</tbody></table></div></details></section>
    <div class="timeline-strip"><div class="timeline-stop">特征、结果已知截止<strong>${r.cutoff}</strong></div>${icon('arrow')}<div class="timeline-stop">长期续作概率筛选<strong>≥ ${pct(r.threshold)}</strong></div>${icon('arrow')}<div class="timeline-stop">当年续作验收<strong>${r.outcome_end}</strong></div></div>
    <div class="analysis-grid backtest-analysis"><section class="panel"><h2>${r.outcome_end.slice(0,4)} 年，哪些续作被找到了？</h2><p class="panel-subtitle">入选阈值在测试年份之前确定；当年未续作不等于长期预测错误</p><div class="confusion" aria-label="年度续作识别结果"><span></span><span>未入选</span><span>入选关注</span><span>当年<br>未续作</span><div class="confusion-cell"><strong>${pendingOut}</strong>继续观察</div><div class="confusion-cell"><strong>${pendingIn}</strong>入选，尚未兑现</div><span>当年<br>有续作</span><div class="confusion-cell"><strong>${miss}</strong>漏掉续作</div><div class="confusion-cell hit"><strong>${hits}</strong>正确命中</div></div><div class="metric-list"><div>年度检索 PR-AUC<strong>${m.pr_auc?.toFixed(3) ?? '—'}</strong></div><div>当年正例比例<strong>${pct(r.positive_n/r.test_n)}</strong></div><div>固定 50% 召回率<strong>${pct(r.fixed_50_metrics.recall)}</strong></div><div>名单占全部系列<strong>${pct(selected/r.test_n)}</strong></div></div><p class="small-note">每次预测覆盖此前所有符合范围的系列，而非仅上一年新番。当年续作比例 = 候选名单中当年有续作的作品数 ÷ 入选作品数，不代表长期预测准确率。PR-AUC 评价当年续作的排序；不把未续作样本当成永久负例。</p></section>
    <section class="panel"><h2>长期概率与当年续作，分开检验。</h2><p class="panel-subtitle">下图仅校验模型推导出的年度概率，不是片单的长期概率</p><div class="chart-legend"><span><i></i>实际当年续作率</span><span><i></i>预计当年续作率</span></div><div class="calibration-plot" id="calibration-plot"></div><p class="small-note">各分组样本数：${r.calibration.map(b=>b.n).join(" / ")}。年度 Brier ${m.annual_brier.toFixed(4)}（越低越好）。长期概率来自混合生存模型，依赖等待时间分布的尾部假设；有限历史无法直接验证“永远不会”。</p></section></div>
    <section class="panel full-panel"><h2>这一年如何选模型？</h2><p class="panel-subtitle">${esc(r.selection.name)} · 比较只使用截点前资料，后续观察期的条件删失损失越低越好</p><div class="table-wrap"><table class="backtest-table"><thead><tr><th>候选方法</th><th>验证删失损失 ↓</th><th>验证年度 PR-AUC ↑</th></tr></thead><tbody>${r.selection.candidates.map(c=>`<tr><td>${esc(c.name)}${c.id===r.selection.selected?' · 已采用':''}</td><td>${c.censored_nll.toFixed(4)}</td><td>${c.annual_pr_auc.toFixed(3)}</td></tr>`).join('')}</tbody></table></div><p class="small-note">随机森林、极端随机树、梯度提升和逻辑回归共同比较。筛选阈值汇总前两个完整验证年的 F2 选择，并约束当年续作比例；这些是调参结果，不是独立测试成绩。</p></section>
    <section class="panel full-panel"><h2>逐部查看预测</h2><p class="panel-subtitle">按长期概率降序列出 20 部；“尚未兑现”保留等待状态</p><div class="table-wrap"><table class="backtest-table"><thead><tr><th>测试作品</th><th>长期概率</th><th>当年结果</th><th>识别状态</th></tr></thead><tbody>${[...r.predictions].sort((a,b)=>b.probability-a.probability).slice(0,20).map(p=>`<tr><td><a href="https://bgm.tv/subject/${p.id}" target="_blank" rel="noreferrer">${esc(p.name||titleMap.get(p.id)||'作品名称待补充')}</a></td><td>${pct(p.probability)}</td><td>${p.actual?'有续作':'暂未续作'}</td><td>${p.actual?(p.predicted?'正确命中':'漏掉续作'):(p.predicted?'尚未兑现':'继续观察')}</td></tr>`).join('')}</tbody></table></div><p class="small-note">发行事件截断回测：动态现值已排除，未来发行节点不参与历史特征和系列合并。基础日期与关系仍由当前归档重建；未还原当年的续作官宣信息。下载报告可查看全部样本。</p></section>`;
  document.querySelector('#backtest-year').addEventListener('change', e=>{state.backtestYear=e.target.value;backtest();});
  requestAnimationFrame(()=>{drawCalibration();drawAnnual();});
}

function drawAnnual() {
  const plot=document.querySelector('#annual-plot');if(!plot || !window.Charts)return;
  const years=data.annual_backtest?.years || [data.backtest];
  window.Charts.line(plot,{labels:years.map(r=>r.outcome_end.slice(0,4)),series:[{name:'历史验证阈值',values:years.map(r=>r.metrics.recall*100)},{name:'固定 50%',dash:true,values:years.map(r=>r.fixed_50_metrics.recall*100)}],yTicks:[0,25,50,75,100],max:100,points:true,format:v=>`${v.toFixed(1)}%`,tickFormat:v=>`${v}%`});
}

function drawCalibration() {
  const plot = document.querySelector('#calibration-plot');
  if (!plot || !window.Charts) return;
  const bins = activeReport().calibration;
  window.Charts.line(plot, {labels: bins.map(b => `${b.low*100}–${b.high*100}%`),
    series: [{name:'实际当年续作率', values:bins.map(b => b.observed == null ? null : b.observed*100)}, {name:'预计当年续作率', dash:true, values:bins.map(b => b.predicted == null ? null : b.predicted*100)}], yTicks:[0,25,50,75,100], max:100, points:true, format:v => `${v.toFixed(0)}%`, tickFormat:v=>`${v}%`});
}

function drawSeason() {
  const plot=document.querySelector('#season-plot');
  if(!plot || !window.Charts) return;
  const bins=data.model.season_breakdown || [];
  window.Charts.bars(plot,{labels:bins.map(b=>b.label),values:bins.map(b=>b.rate == null ? null : b.rate*100),height:190,highlight:[0,1,2,3],format:v=>`${v.toFixed(1)}%`});
}

function about() {
  const policy=data.temporal_policy;
  app.innerHTML=heading('把方法与边界，摊开来说。','数据来源、时间约束与可复现的训练。')+notice()+`<div class="about-layout"><section class="panel prose">
  <h2>预测的是将来是否还会有下一季</h2><p>目标不限定未来一年；每个系列的当前最新正篇都可预测后续一季。已知下一季与有证据的完结状态另行展示。首页默认“还没有续作”，只看首播至少一年前的作品，按长期续作概率排序。概率区间默认 0–100%。四个入口恢复固定的整套筛选：清空搜索、标签、原作与日期限制；高概率设为全部状态、30–100% 和社区评分优先，没有续作设为对应状态、0–100% 和概率优先。搜索、标签及其他筛选会保存在地址栏，可直接分享。</p>
  <h2>严格截断了什么？</h2><p>训练、模型选择、阈值选择只能看到年度截点及以前的结果；下一年仅用于最终验收。系列特征只由当前季首播前已经发行的节点重建，未来作品不能连接历史系列。使用随机森林、极端随机树、梯度提升和逻辑回归的 6 个配置，分别搭配精简和完整特征，共比较 12 组，依据更早起点之后的条件删失损失选模型。阈值使用截点之前两个完整年度的 F2，每次拟合都仅依据训练集删除常量和零贡献字段，重新训练直到保留项稳定；等待天数通过生存公式单独使用。</p><p>评分、收藏、题材评价标签、原作卷数与完结状态没有历史快照，已从模型移除。原作类型和动画制作署名作为固定作品属性单独编码，原作发行年龄与公司历史产量只使用首播前的发行日期。今天新采集的销量、MAL 和改编进度保存实际采集日，不按作品首播日倒填。页面仍展示这些参考资料，显示它们并不表示参与了训练。</p><p><strong>这不是完整历史快照回测。</strong>${esc(policy?.limitations || '基础发行事实来自当前归档。')} 现有数据无法恢复二十年来每个字段的历史版本。CI 会继续保存真实快照，供后续验证使用。</p>
  ${data.model.optimization?`<h2>剔除无贡献输入后的回测</h2><p>${data.model.optimization.cutoff_range} 年的 ${data.model.optimization.years} 个截点中，年度平均 PR-AUC 从 ${data.model.optimization.before.macro_pr_auc.toFixed(3)} 变为 ${data.model.optimization.after.macro_pr_auc.toFixed(3)}，召回率从 ${pct(data.model.optimization.before.recall)} 变为 ${pct(data.model.optimization.after.recall)}，当年续作比例从 ${pct(data.model.optimization.before.precision)} 变为 ${pct(data.model.optimization.after.precision)}。这些是严格截点下的年度检索指标，不是长期概率准确率。每年均独立训练和筛选，没有依据验收年份选择字段。</p><p>当前模型保留 ${num(data.model.features)} 项输入；其中等待天数用于生存公式，其余字段均有非零模型权重。特征越少不等于准确率必然提高，完整逐年结果在时间回测中展示。</p>`:''}
  <h2>年度指标如何理解？</h2><p>每个年度验收覆盖截点以前的所有系列；当年正好有续作的为当年正例。“当年续作召回率”看这些续作找回了多少；“当年续作比例”看候选名单里当年出续作的占比。没有在当年续作不代表永不续作，因此不报告长期分类准确率。片单使用混合生存模型的长期条件概率，依赖等待时间分布假设，尚未声称校准。</p>
  <h2>季度与附属作品分别统计</h2><p>TV / WEB 正篇每季计 1；每集至少 21 分钟的已播 OVA 计 0.1。详情右上角另列剧场版、总集篇与普通番外数量。历史模型只使用有逐集日期的 OVA；无日期的汇总集数不提前回填。原作完结不等于动画完结，下拉菜单中的已完结状态仍须有明确证据。</p>
  <h2>销量可以继续更新</h2><p>旧数据来自 <a href="https://github.com/zhouandrewc/anime-sales-prediction" target="_blank" rel="noreferrer">Anime Sales Predictions 的固定 2020 年快照</a>，其来源为 Someanithing。现在新增 <a href="https://www.lanovelibrary.com/2025-hegemony-anime-ranking/" target="_blank" rel="noreferrer">ラノベライブラリ年度销量汇总</a>，采集 2021 年起各年列表，按标题与首播年唯一匹配。新来源为非官方 BD + DVD 已知卷累平，不把未知卷当零，排除再版；与首卷首周以及旧来源口径分开。缺失或匹配有歧义的记录不填数。</p>
  <h2>MyAnimeList 与更多来源</h2><p><a href="https://myanimelist.net/apiconfig/references/api/v2" target="_blank" rel="noreferrer">MyAnimeList 官方 API v2</a>支持作品资料、评分统计、类型与制作公司；公共读取需要应用 Client ID。后台支持 MAL_CLIENT_ID，并可尝试有限速的 Jikan 非官方接口；凭据不会放进网页。跨站 ID、封面地址、标签与制作信息还可从 <a href="https://github.com/manami-project/anime-offline-database" target="_blank" rel="noreferrer">anime-offline-database</a> 精确关联；该归档最后更新为 2026-07-04，不能当作持续更新的 MAL 实时数据。</p><p>原作通过 Bangumi 跨媒体关系关联，改编终点来自 MangaUpdates。每条外部记录保留来源和取得日期。任何缺少时点依据的新特征都先保存资料，不混入旧回测。</p>
  <h2>更新与页面加载</h2><p>GitHub Actions 每三个月更新一次：1、4、7、10 月 1 日北京时间 08:30 下载最新归档、分批补充资料、训练当前模型并输出静态页面；完整逐年重训只在本地运行。首页先读取精简片单，后台加载完整筛选索引；回测、作品详情和完整报告按需读取。封面全部远端外链，最多 3 个并发，先完成已展示作品的全部首图，再自动加载背面封面，本站不缓存图片。</p>
  </section></div>`;
}

function originalDetail(item) {
  const o=item.original;
  if(!o) return `<div class="original-detail"><h3>${item.source === '原创' ? '原创动画' : '原作尚待关联'}</h3><p>${item.source === '原创' ? '没有出版原作的剩余量特征。' : '没有足够的关联证据确认原作方向。'}</p></div>`;
  return `<div class="original-detail"><h3>改编自 <a href="${esc(safeUrl(o.url))}" target="_blank" rel="noreferrer">${esc(o.name_cn)} ${icon('external')}</a></h3><p>${esc(o.kind)} · ${sourceStatus(o)} · ${o.volumes != null ? `${o.volumes} 卷` : '卷数待核实'}${o.chapters != null ? ` · ${o.chapters} 话` : ''}${o.evidence?.ended ? ` · 连载结束 ${esc(o.evidence.ended)}` : ''}</p><p class="source-proof">册数原文：${esc(o.evidence?.volumes || '未填写')}。资料来自当前书籍条目，可能存在更新延迟。</p></div>`;
}

async function detail(id) {
  returnFocus=document.activeElement;const request=++detailRequest;
  const modal=document.querySelector('#detail-dialog');
  document.querySelector('#detail-content').innerHTML=`<div class="detail-top"><span id="detail-title">系列观察档案</span><button class="icon-button" data-close aria-label="关闭详情">${icon('close')}</button></div><div class="detail-body">${loading('正在读取作品详情…')}</div>`;
  if(!modal.open)modal.showModal();
  let item;
  try {item=detailCache.get(id)||await fetchData(`details/${id}.json`);detailCache.set(id,item);}
  catch(error){if(modal.open&&request===detailRequest)document.querySelector('#detail-content .detail-body').innerHTML=`<p>${esc(error.message)}</p><button class="outline-button" data-detail="${id}">重试详情</button>`;return;}
  if(!modal.open||request!==detailRequest)return;
  const e = item.enrichment, p = item.progress, disc = visibleDisc(e);
  const max = Math.max(...item.explanation.map(r => Math.abs(r.contribution)), .01);
  document.querySelector('#detail-content').innerHTML = `<div class="detail-top"><span>系列观察档案</span><button class="icon-button" data-close aria-label="关闭详情">${icon('close')}</button></div><div class="detail-body"><div class="detail-identity">${image(item, '', false)}<div><h2 id="detail-title">${esc(displayName(item))}</h2><p lang="ja">${esc(item.name)}</p><a href="https://bgm.tv/subject/${item.id}" target="_blank" rel="noreferrer">在 Bangumi 查看 ${icon('external')}</a></div></div>
    <div class="detail-prediction"><div class="prediction-block"><span>将来还会有下一季</span><strong>${item.status === 'predict' ? pct(item.probability, 0) : item.status === 'known' ? '已收录' : '系列已完结'}</strong><p>${item.status === 'predict' ? '考虑等待时长的长期估计，不限定一年' : item.status === 'known' ? '已知关联不计作模型预测命中' : esc(item.completion?.reason || '依据明确完结信息归档')}</p></div><div class="season-block"><strong>${seasonNumber(p.equivalent)} <small>季进度</small></strong><p>${p.regular} 季正式作品 + ${p.ova_episodes} 集合格 OVA</p><div class="extra-counts"><span><b>${p.movies || 0}</b> 部剧场版</span><span><b>${p.summaries || 0}</b> 部总集篇</span><span><b>${p.side_stories || 0}</b> 部普通番外</span></div><p>${p.ova_unverified ? `${p.ova_unverified} 部 OVA 时长或集数待核实` : '附属作品分别统计，季进度仍按正篇与合格 OVA 折算'}</p></div></div>
    ${item.status === 'predict' ? `<section class="detail-section"><h3>是什么推高或拉低了概率？</h3>${item.explanation.map(r => `<div class="contribution-row ${r.contribution > 0 ? 'positive' : ''}"><span title="${esc(definition(r.key))}">${esc(r.name)}</span><div class="contribution-axis"><span class="${r.contribution < 0 ? 'minus' : ''}" style="width:${Math.abs(r.contribution)/max*48}%"></span></div><span>${r.contribution >= 0 ? '+' : ''}${(r.contribution*100).toFixed(1)} pp</span></div>`).join('')}<p class="small-note">将单项特征替换为训练中位值，比较替换前后概率。向右表示当前取值更有利，向左更不利；差值不可相加，不代表因果。仅检查权重最高的 20 项及等待时长。</p></section>` : ''}
    ${originalDetail(item)}
    <section class="detail-section"><h3>作品标签 <span class="muted">${item.tags.length} 个</span></h3><div class="tag-list detail-tags">${item.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('') || '<span class="muted">暂无标签</span>'}</div></section>
    ${item.completion ? `<p class="source-proof">完结依据：${esc(item.completion.reason)} · <a class="proof-link" href="${esc(safeUrl(item.completion.url))}" target="_blank" rel="noreferrer">核对条目</a></p>` : ''}
    <section class="detail-section"><h3>改编进度与商业证据</h3><div class="detail-evidence"><div><strong>原作改编到哪里了？</strong><p>${e.adaptation_endpoint != null ? `终点位于第 ${e.adaptation_endpoint} ${esc(e.adaptation_unit)} / 原作已出版 ${e.adaptation_total} ${esc(e.adaptation_unit)}。这是叙事终点，不能直接视作逐章覆盖率或精确剩余容量。` : e.source_total_units && e.adapted_units != null ? `已核实改编 ${e.adapted_units} / ${e.source_total_units} ${esc(e.unit)}。` : '尚无能确认当前季数、单位一致的数值进度。'}</p>${e.adaptation_note ? `<p class="source-proof">原始记录 · 起点：${esc(e.adaptation_note.start)}<br>终点：${esc(e.adaptation_note.end)}</p>` : ''}${e.adaptation_url ? `<p class="source-proof"><a class="proof-link" href="${esc(safeUrl(e.adaptation_url))}" target="_blank" rel="noreferrer">MangaUpdates 记录</a> · 核对于 ${esc(e.adaptation_observed_at)}</p>` : ''}</div><div><strong>${discLabel(e)}</strong><p>${disc.disc_average != null ? `${e.disc_average == null ? '前作参考：' : ''}${num(disc.disc_average)} 张 / 卷。BD + DVD 卷均口径，${disc.disc_provider ? esc(disc.disc_provider)+'，采集于 '+esc(disc.disc_observed_at)+'。'+esc(disc.disc_note || '') : '2020 年研究快照，非最新累计销量。'}` : e.bd_copies != null ? `${num(e.bd_copies)} 张，首卷首周口径。` : '暂未找到可精确匹配的销量记录。空缺保留为未知。'}</p>${disc.disc_url ? `<p class="source-proof"><a class="proof-link" href="${esc(safeUrl(disc.disc_url))}" target="_blank" rel="noreferrer">销量来源</a> · ${esc(disc.disc_observed_at)}<br>匹配标题：${esc(disc.disc_title)}（${disc.disc_year}）</p>` : ''}</div></div></section>
    ${item.external_anime ? `<section class="detail-section"><h3>跨站资料</h3><p class="small-note"><a class="proof-link" href="${esc(safeUrl(item.external_anime.url))}" target="_blank" rel="noreferrer">MyAnimeList #${item.external_anime.mal_id} ${icon('external')}</a> · 经 ${esc(item.external_anime.provider)} 按标题、年份与形式关联。资料日期 ${esc(item.external_anime.upstream_updated_at || item.external_anime.observed_at)}。</p>${item.mal_evidence ? `<p class="small-note">MAL 评分 ${item.mal_evidence.score ?? '—'} · ${num(item.mal_evidence.votes)} 人评分 · ${esc(item.mal_evidence.observed_at)} 采集</p>` : ''}<details class="external-tags"><summary>查看跨站标签（${item.external_anime.tags?.length || 0}）</summary><div class="tag-list">${(item.external_anime.tags || []).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div></details><p class="small-note">当前补充资料仅供查阅，不倒填到历史训练。</p></section>` : ''}
    <section class="detail-section community-detail"><h3>社区信息 · 辅助参考</h3><p class="small-note">Bangumi 评分 ${item.score ? Number(item.score).toFixed(1) : '—'} / 10 · ${num(item.votes)} 人评分。当前社区快照没有历史观察时间，已排除出严格截断模型；仅供参考。</p></section>
    <section class="detail-section"><h3>把这个系列，放在一起 <span class="muted">${item.series.length} 部</span></h3><div class="series-timeline">${item.series.map(s => `<a class="series-item" href="https://bgm.tv/subject/${s.id}" target="_blank" rel="noreferrer">${image(s)}<p>${esc(displayName(s))}</p><span>${esc(s.date || '日期未定')}<br>${esc(s.platform)}${s.platform === 'OVA' && s.duration_minutes ? ` / ${s.duration_minutes} 分钟` : ''}</span></a>`).join('')}</div></section><p class="small-note">快照 ${esc(data.snapshot_at)}。${item.japanese_inferred ? '日本作品范围根据日文名推断，尚待地区资料确认。' : ''}${data.mode === 'api_sample' ? '小规模 API 联调样本，关联作品可能尚未完整。' : ''}</p></div>`;
  if(!document.querySelector('#detail-dialog').open)document.querySelector('#detail-dialog').showModal();
  attachImageFallbacks();
}

function attachImageFallbacks() { observeCovers(); }

function activatePoster(button) {
  const column = button.closest('.poster-column');
  if (!column) return;
  const index = Number(button.dataset.poster ?? button.dataset.posterTab);
  const posters = [...column.querySelectorAll('[data-poster]')];
  posters.forEach((p,n) => {
    p.classList.toggle('active',n===index);
    p.style.zIndex = 8-Math.abs(n-index);
    p.setAttribute('aria-pressed',String(n===index));
  });
  column.querySelectorAll('[data-poster-tab]').forEach((p,n)=>p.setAttribute('aria-pressed',String(n===index)));
  const name = posters[index]?.dataset.posterName || '';
  column.querySelector('.poster-caption').textContent = name;
  column.querySelector('.poster-caption').title = name;
  activateCover(column);
}
for (const event of ['pointerover','focusin','click']) document.addEventListener(event,e=>{
  const button=e.target.closest('[data-poster],[data-poster-tab]');
  if(button) activatePoster(button);
});

function toast(message) {
  const el = document.querySelector('#toast'); el.textContent = message; el.classList.add('visible');
  clearTimeout(toast.timeout); toast.timeout = setTimeout(() => el.classList.remove('visible'), 2500);
}

function save(id) {
  const item = data.items.find(i => i.id === id);
  if (saved.has(id)) saved.delete(id); else saved.add(id);
  try { localStorage.setItem('tsuzuku-watchlist', JSON.stringify([...saved])); } catch { toast('浏览器未允许保存，关注仅在本次打开有效。'); return; }
  if (['library','watchlist'].includes(state.view)) renderResults({force:true});
  toast(`${saved.has(id) ? '已关注' : '已取消关注'}《${displayName(item)}》`);
}

function download(name, body, type='application/json') {
  const url = URL.createObjectURL(new Blob([body], {type}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function csvCell(value) {
  let s = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}

document.addEventListener('click', async e => {
  const b = e.target.closest('button');
  if (!b || !data) return;
  if (b.dataset.year) { state.backtestYear=b.dataset.year; backtest(); }
  if (b.dataset.detail) detail(Number(b.dataset.detail));
  if (b.dataset.save) save(Number(b.dataset.save));
  if (b.dataset.preset && PRESETS[b.dataset.preset]) changeFilters(PRESETS[b.dataset.preset],{preset:b.dataset.preset,push:true});
  if (b.dataset.tag) changeFilters({tag:b.dataset.tag},{push:true});
  if (b.hasAttribute('data-clear-tag')) changeFilters({tag:''},{push:true});
  if (b.hasAttribute('data-reset')) changeFilters({...defaultFilters({from:'',to:''}),scope:'all',context:''},{push:true});
  if (b.dataset.datePreset) {const range=b.dataset.datePreset==='older'?defaultRange():{from:'',to:''};changeFilters({dateFrom:range.from,dateTo:range.to},{push:true});}
  if (b.hasAttribute('data-load-library')) loadLibrary();
  if (b.hasAttribute('data-retry-analysis')) loadAnalysis();
  if (b.hasAttribute('data-more')) { state.page++; renderResults(); }
  if (b.hasAttribute('data-close')) document.querySelector('#detail-dialog').close();
  if (b.dataset.importance) { state.importance = b.dataset.importance; featureChart(); }
  if (b.hasAttribute('data-dictionary-toggle')) { state.dictionaryExpanded = !state.dictionaryExpanded; dictionary(); }
  if (b.hasAttribute('data-report')) { b.disabled=true; try {const report=await fetchData('backtest-report.json');download('tsuzuku-backtest.json',JSON.stringify(report,null,2));}catch(error){toast('报告暂时未能下载，请重试。');}finally{b.disabled=false;} }
  if (b.hasAttribute('data-export')) {
    const rows = [['Bangumi ID','动画','首播','正式季数','含OVA进度','状态','将来续作概率(模型估计)','快照','验证模式'], ...filteredItems().map(i => [i.id,displayName(i),i.date,i.progress.regular,i.progress.equivalent,i.status,i.probability ?? '',data.snapshot_at,data.validation])];
    download('tsuzuku-watchlist.csv', '\uFEFF' + rows.map(r => r.map(csvCell).join(',')).join('\r\n'), 'text/csv;charset=utf-8');
    toast('已导出当前筛选结果');
  }
});
const dialog = document.querySelector('#detail-dialog');
dialog.addEventListener('close', () => returnFocus?.isConnected && returnFocus.focus());
dialog.addEventListener('click', e => { if(e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
document.addEventListener('keydown', e => { if (e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !dialog.open) { const input = document.querySelector('#anime-search'); if(input) { e.preventDefault(); input.focus(); } } if (e.key === 'Escape') { document.querySelector('.site-sidebar').classList.remove('open'); document.querySelector('#menu-toggle').setAttribute('aria-expanded','false'); } });

function navigate() {
  if (!data) return;
  const key = location.hash.slice(1).split('?')[0] || 'library';
  const nextView = Object.hasOwn(titles, key) ? key : 'library';
  state.view=nextView;
  if(['library','watchlist'].includes(nextView)) {
    const query=location.hash.includes('?')?location.hash.slice(location.hash.indexOf('?')+1):location.search.slice(1);
    Object.assign(state,readFilters(new URLSearchParams(query),defaultRange()),{page:1});
    if(nextView==='watchlist'&&!query)Object.assign(state,{scope:'all',preset:'',context:'',dateFrom:'',dateTo:''});
  }
  document.title = `${titles[nextView]} · 续作观测室`;
  document.querySelector('#current-section').textContent = titles[nextView];
  document.querySelectorAll('[data-view]').forEach(a => {if(a.dataset.view === nextView) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');});
  document.querySelector('.site-sidebar').classList.remove('open');
  document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');
  if (dialog.open) dialog.close();
  if (['library','watchlist'].includes(nextView)) library();
  else if (nextView === 'features') {featuresView();if(!data.analysisLoaded)loadAnalysis();}
  else if (nextView === 'backtest') {if(data.analysisLoaded)backtest();else{app.innerHTML=heading('时间回测','按需加载年度报告。')+loading('正在读取回测结果…');loadAnalysis();}}
  else about();
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', navigate);
// pushState entries for filters also restore on browser back/forward.
window.addEventListener('popstate', navigate);
window.addEventListener('resize', () => { clearTimeout(drawCalibration.timer); drawCalibration.timer = setTimeout(()=>{drawCalibration();drawSeason();drawAnnual();}, 150); });

async function fetchData(name) {
  const response=await fetch('./data/'+name+(data?.version?'?v='+encodeURIComponent(data.version):''));
  if(!response.ok)throw new Error(`HTTP ${response.status}`);
  const result=await response.json();
  if(result.version && data?.version && result.version!==data.version)throw new Error('数据版本已更新，请刷新页面');
  return result;
}
async function loadLibrary() {
  if(data.libraryLoaded)return;
  if(libraryPromise)return libraryPromise;
  libraryPromise=(async()=>{
    try{const result=await fetchData('library.json');data.items=result.items;data.libraryLoaded=true;if(['library','watchlist'].includes(state.view))renderResults();}
    catch(error){if(['library','watchlist'].includes(state.view))document.querySelector('#pagination').innerHTML=`<span>${esc(error.message)}</span><button class="outline-button" data-load-library>重试完整片单</button>`;}
    finally{libraryPromise=null;}
  })();return libraryPromise;
}
async function loadAnalysis() {
  if(data.analysisLoaded)return;
  if(analysisPromise)return analysisPromise;
  analysisPromise=(async()=>{try{Object.assign(data,await fetchData('analysis.json'),{analysisLoaded:true});if(state.view==='backtest')backtest();if(state.view==='features')featureChart();}catch(error){if(state.view==='backtest')app.innerHTML=heading('报告暂未加载成功',error.message)+'<button class="outline-button" data-retry-analysis>重试</button>';}finally{analysisPromise=null;}})();
  return analysisPromise;
}
async function init() {
  try {
    app.innerHTML=heading('故事，还会继续吗？','正在载入片单，封面稍后显示。')+loading('正在读取首页资料…')+'<div class="skeleton-grid" aria-hidden="true"><div></div><div></div></div>';
    data=await fetchData('bootstrap.json');data.libraryLoaded=false;data.analysisLoaded=false;
    if(data.schema_version!==1||!Array.isArray(data.items))throw new Error('不支持的数据版本');
    document.querySelector('#data-stamp').textContent=`数据快照 ${data.snapshot_at.replaceAll('-',' / ')}`;
    document.querySelector('#model-summary').innerHTML=`${num(data.model.features)} 个模型输入<br>${num(data.model.training_samples)} 条训练样本`;
    navigate();
    // Paint the first page before downloading the full search index.
    requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(loadLibrary,100)));
  } catch(error) {
    app.innerHTML=heading('片单暂时没有加载成功','请检查连接后重试。')+`<div class="empty-state"><p>${esc(error.message)}</p><button class="primary-button" id="retry">重新载入</button></div>`;
    document.querySelector('#data-stamp').textContent='数据加载失败';document.querySelector('#retry').onclick=()=>{data=undefined;init();};
  }
}
init();
