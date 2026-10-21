export const PAGE_SIZE = 6;
const PRESET_BASE={query:'',tag:'',source:'',dateFrom:'',dateTo:'',probabilityMin:0,probabilityMax:100,sort:'probability'};
export const PRESETS = {
  recent: {...PRESET_BASE,scope:'recent',sort:'date'},
  high: {...PRESET_BASE,scope:'all',probabilityMin:30,sort:'score'},
  uncontinued: {...PRESET_BASE,scope:'uncontinued'},
  all: {...PRESET_BASE,scope:'all'},
};
export function defaultFilters(range={from:'2006-01-01',to:''}) {
  // Homepage intentionally highlights no sequel while retaining its older-date range.
  return {scope:'uncontinued',preset:'uncontinued',context:'home',query:'',tag:'',source:'',dateFrom:range.from,dateTo:range.to,sort:'probability',probabilityMin:0,probabilityMax:100};
}
export function matches(item,f) {
  const q=f.query.trim().toLocaleLowerCase();
  if(q && ![item.name,item.name_cn,item.studio,...(item.series_names||item.series.flatMap(s=>[s.name_cn,s.name])),item.original?.name_cn,...item.tags].some(s=>String(s||'').toLocaleLowerCase().includes(q)))return false;
  if(f.source && item.source!==f.source || f.tag && !item.tags.includes(f.tag))return false;
  if(f.dateFrom && item.date<f.dateFrom || f.dateTo && item.date>f.dateTo)return false;
  if(f.scope==='recent' && (!item.recent_sequel || item.status==='completed'))return false;
  if(f.scope==='uncontinued' && (item.has_sequel || item.status!=='predict'))return false;
  if(f.scope==='completed' && item.status!=='completed')return false;
  if(f.scope==='known' && item.status!=='known')return false;
  // The full range does not exclude completed/known works without a prediction.
  if(f.probabilityMin!==0 || f.probabilityMax!==100) {
    if(item.probability==null || item.probability<f.probabilityMin/100 || item.probability>f.probabilityMax/100)return false;
  }
  return true;
}
export function selectItems(items,f) {
  return items.filter(i=>matches(i,f)).sort((a,b)=>
    (f.sort==='score' ? (b.score||0)-(a.score||0) : f.sort==='date' ? b.date.localeCompare(a.date) : f.sort==='seasons' ? b.progress.equivalent-a.progress.equivalent : (b.probability??-1)-(a.probability??-1)) || b.date.localeCompare(a.date) || a.id-b.id);
}
// Outside the high preset, preview what clicking it would select. Inside that
// context, slider edits stay live even after manual edits clear the highlight.
export function highSummaryRange(filters) {
  return filters.context==='high'?filters:PRESETS.high;
}
export function summaryCount(items,key,range) {
  const high=highSummaryRange(range);
  const f=key==='high'?{...PRESETS.high,probabilityMin:high.probabilityMin,probabilityMax:high.probabilityMax}:PRESETS[key];
  return items.filter(i=>(key!=='high'||Number.isFinite(i.probability))&&matches(i,f)).length;
}
const fields={q:'query',tag:'tag',source:'source',status:'scope',from:'dateFrom',to:'dateTo',sort:'sort',min:'probabilityMin',max:'probabilityMax',preset:'preset',context:'context'};
const filterFields=Object.entries(fields).filter(([,key])=>!['preset','context'].includes(key));
const sameFilters=(a,b)=>filterFields.every(([,key])=>a[key]===b[key]);
const contextBase=(context,range)=>context==='home'?defaultFilters(range):PRESETS[context]||{...PRESET_BASE,scope:'all'};
export function filterParams(f,range) {
  const params=new URLSearchParams();
  if(f.preset==='uncontinued'&&f.context==='home'&&sameFilters(f,defaultFilters(range)))return '';
  const preset=Object.hasOwn(PRESETS,f.preset)&&sameFilters(f,PRESETS[f.preset])?f.preset:'';
  const context=preset||f.context||'all';
  const base=contextBase(context,range);
  if(preset)params.set('preset',preset);
  else if(context!=='home')params.set('context',context);
  for(const [param,key] of filterFields)if(f[key]!==base[key])params.set(param,String(f[key]));
  // A manually restored default keeps highlights off, even after reloading.
  if(!params.size)params.set('preset','');
  return params.toString();
}
export function readFilters(params,range) {
  if(!params.size)return defaultFilters(range);
  // Old shared completed-preset links now restore the manual dropdown filter.
  if(params.get('preset')==='completed'||params.get('context')==='completed'){
    params=new URLSearchParams(params);params.delete('preset');params.set('context','all');
    for(const [key,value] of Object.entries({status:'completed',sort:'date',from:'',to:''}))if(!params.has(key))params.set(key,value);
  }
  const preset=Object.hasOwn(PRESETS,params.get('preset'))?params.get('preset'):'';
  const requestedContext=params.has('context')?params.get('context'):preset||'home';
  const context=requestedContext==='home'||Object.hasOwn(PRESETS,requestedContext)?requestedContext:'all';
  const f={...contextBase(preset||context,range),preset,context};
  for(const [param,key] of Object.entries(fields))if(params.has(param))f[key]=params.get(param);
  for(const key of ['probabilityMin','probabilityMax'])f[key]=Math.max(0,Math.min(100,Math.round(Number(f[key])||0)));
  if(f.probabilityMin>f.probabilityMax)[f.probabilityMin,f.probabilityMax]=[f.probabilityMax,f.probabilityMin];
  if(!['all','recent','uncontinued','completed','known'].includes(f.scope))f.scope='all';
  if(!['','漫画改','小说改','原创','游戏改','未知'].includes(f.source))f.source='';
  if(!['probability','score','date','seasons'].includes(f.sort))f.sort='probability';
  for(const key of ['dateFrom','dateTo'])if(f[key] && (!/^\d{4}-\d{2}-\d{2}$/.test(f[key]) || !Number.isFinite(Date.parse(f[key]))))f[key]='';
  f.context=context;
  const homepage=f.preset==='uncontinued'&&sameFilters(f,defaultFilters(range));
  if(homepage)f.context='home';
  if(!homepage && (!Object.hasOwn(PRESETS,f.preset) || Object.entries(PRESETS[f.preset]).some(([key,value])=>f[key]!==value)))f.preset='';
  return f;
}
export const displayName = item => [item?.name_cn,item?.name].find(s=>s?.trim()&&!/^Bangumi\s*#\d+$/i.test(s.trim()))?.trim() || '作品名称待补充';
