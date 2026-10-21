import {mkdir, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {PAGE_SIZE,PRESETS,defaultFilters,selectItems,displayName,summaryCount} from '../assets/filters.js';
export function yearBefore(day) {
  const d=new Date(day+'T00:00:00Z'), month=d.getUTCMonth();
  d.setUTCFullYear(d.getUTCFullYear()-1);
  if(d.getUTCMonth()!==month)d.setUTCDate(0);
  return d.toISOString().slice(0,10);
}
export async function writeSiteData(data,directory) {
  const root=directory+'/data';
  await mkdir(root+'/details',{recursive:true});
  const write=(file,value)=>writeFile(root+'/'+file,JSON.stringify(value));
  const compact=data.items.map(item=>{
    const series=item.series.filter(s=>s.id!==item.id&&s.date&&s.date<=(data.prediction_at||data.snapshot_at)).slice(-2);
    const fields=['id','name','name_cn','date','platform','source','studio','episodes','score','votes','tags','image','external_image','status','probability','progress','recent_sequel','has_sequel'];
    const evidenceFields=['disc_average','disc_provider','bd_copies','adaptation_endpoint','adaptation_total','adaptation_unit','adapted_units','source_total_units','unit','previous_disc'];
    const evidence=Object.fromEntries(evidenceFields.filter(k=>item.enrichment[k]!=null).map(k=>[k,item.enrichment[k]]));
    const original=item.original?Object.fromEntries(['name_cn','url','status','volumes'].map(k=>[k,item.original[k]])):null;
    return {...Object.fromEntries(fields.map(k=>[k,item[k]])),enrichment:evidence,original,series,series_count:item.series.length,series_names:[...new Set(item.series.flatMap(s=>[s.name_cn,s.name]).filter(Boolean))]};
  });
  const {items,backtest,annual_backtest,...meta}=data;
  const today=data.prediction_at||data.snapshot_at,from='2006-01-01',to=yearBefore(today);
  const initial=defaultFilters({from,to});
  const selected=selectItems(compact,initial);
  const counts=Object.fromEntries(Object.keys(PRESETS).map(key=>[key,summaryCount(compact,key,initial)]));
  const version=createHash('sha256').update('all-works-preset-v5').update(JSON.stringify(data)).digest('hex').slice(0,12);
  await write('bootstrap.json',{...meta,version,initial_range:{from,to},initial_counts:counts,initial_total:selected.length,items:selected.slice(0,PAGE_SIZE)});
  await write('library.json',{version,items:compact});
  const titles=new Map(items.flatMap(i=>[...i.series,i]).map(i=>[i.id,displayName(i)]));
  const trim=r=>r?{...r,predictions:[...r.predictions].sort((a,b)=>b.probability-a.probability).slice(0,20).map(p=>({...p,name:titles.get(p.id)||''}))}:null;
  await write('analysis.json',{version,backtest:trim(backtest),annual_backtest:annual_backtest?{...annual_backtest,years:annual_backtest.years.map(trim)}:null});
  await write('backtest-report.json',annual_backtest||{...backtest,snapshot_at:data.snapshot_at,validation:data.validation});
  for(const item of items)await write(`details/${item.id}.json`,{version,...item});
  return {bootstrap:Buffer.byteLength(JSON.stringify({...meta,items:selected.slice(0,PAGE_SIZE)})),catalog:Buffer.byteLength(JSON.stringify(data))};
}
