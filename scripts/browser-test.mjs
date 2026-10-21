import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const port = 5174;
const server = spawn('python3',['-m','http.server',String(port),'--bind','127.0.0.1'],{stdio:'ignore'});
const origin = `http://127.0.0.1:${port}`;
let browser;
const errors=[];
const catalog=JSON.parse(await readFile('data/catalog.json','utf8'));
const itemById=new Map(catalog.items.map(i=>[i.id,i]));
const bootstrap=JSON.parse(await readFile('data/bootstrap.json','utf8'));
const older=i=>i.date>=bootstrap.initial_range.from&&i.date<=bootstrap.initial_range.to;
try {
  for(let i=0;i<30;i++){try{if((await fetch(origin)).ok)break;}catch{} await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,...(process.env.CI?{}:{channel:'chrome'}),args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(origin,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  await page.locator('.anime-card').first().waitFor();
  assert(await page.locator('.anime-card').count()>0);
  assert.equal(await page.locator('#status-filter').inputValue(),'uncontinued');
  assert.equal(await page.locator('.notice').count(),0);
  assert.equal(await page.locator('[data-preset=completed]').count(),0);
  assert.equal(Number((await page.locator('[data-preset=all] strong').textContent()).replaceAll(',','')),catalog.items.length);
  assert.equal(await page.locator('#status-filter option[value=completed]').count(),1);
  assert.equal(await page.locator('.count-filter.selected').count(),1);
  assert.equal(await page.locator('[data-preset="uncontinued"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#date-from').inputValue(),bootstrap.initial_range.from);
  assert.equal(await page.locator('#date-to').inputValue(),bootstrap.initial_range.to);
  const expectedHome=catalog.items.filter(i=>older(i)&&!i.has_sequel&&i.status==='predict').sort((a,b)=>b.probability-a.probability||b.date.localeCompare(a.date)||a.id-b.id).slice(0,6).map(i=>i.id);
  assert.deepEqual(await page.locator('.anime-card').evaluateAll(cards=>cards.map(c=>Number(c.dataset.id))),expectedHome);
  assert.equal(await page.locator('#probability-min').inputValue(),'0');
  assert.equal(await page.locator('#probability-max').inputValue(),'100');
  assert.equal(await page.locator('.anime-card').count(),6);
  const highPreview=catalog.items.filter(i=>i.probability!=null&&i.probability>=.3&&i.probability<=1).length;
  assert.equal(Number((await page.locator('[data-preset="high"] strong').textContent()).replaceAll(',','')),highPreview);
  assert((await page.locator('[data-preset="high"] small').textContent()).includes('切换后概率 30–100%'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.equal(await page.locator('[data-preset="uncontinued"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#date-to').inputValue(),bootstrap.initial_range.to);
  await page.locator('#anime-search').fill('玛丽');
  assert.equal(new URL(page.url()).hash,'#library?q='+encodeURIComponent('玛丽'),'Homepage search omits unchanged defaults');
  const searchIds=await page.locator('.anime-card').evaluateAll(cards=>cards.map(c=>c.dataset.id));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.deepEqual(await page.locator('.anime-card').evaluateAll(cards=>cards.map(c=>c.dataset.id)),searchIds);
  assert.equal(await page.locator('#date-to').inputValue(),bootstrap.initial_range.to);
  assert.equal(await page.locator('.count-filter.selected').count(),0);
  await page.goto(origin+'/#library');await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  await page.locator('#date-to').fill('2020-12-31');
  assert.equal(new URL(page.url()).hash,'#library?to=2020-12-31');
  await page.goto(origin+'/#library');await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  await page.locator('[data-more]').click();
  assert.equal(await page.locator('.anime-card').count(),12);
  const initialCard=page.locator('.anime-card').first();
  const initialItem=itemById.get(Number(await initialCard.getAttribute('data-id')));
  await initialCard.locator('.all-tags summary').click();
  assert.deepEqual(await initialCard.locator('.tag-list .tag').allTextContents(),initialItem.tags);
  assert(await initialCard.locator('.tag-list .tag').last().isVisible());
  await page.keyboard.press('Enter');
  assert(!(await initialCard.locator('.tag-list').isVisible()));
  await initialCard.locator('.all-tags summary').click();
  const clickedTag=initialItem.tags.at(-1);
  await initialCard.locator('.tag-list .tag').last().click();
  assert((await page.locator('.filter-tag').textContent()).includes(clickedTag));
  for(const id of await page.locator('.anime-card').evaluateAll(cards=>cards.map(c=>Number(c.dataset.id)))) assert(itemById.get(id).tags.includes(clickedTag));
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('tag'),clickedTag);
  assert.equal(await page.locator('.count-filter.selected').count(),0);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert((await page.locator('.filter-tag').textContent()).includes(clickedTag));
  await page.locator('[data-clear-tag]').click();
  const fan=page.locator('.poster-column').first();
  if(await fan.locator('[data-poster]').count()>1){
    const firstPoster=fan.locator('[data-poster="0"]');
    await firstPoster.hover({position:{x:7,y:50}});
    assert.equal(await firstPoster.getAttribute('aria-pressed'),'true');
    assert.equal(await fan.locator('.poster-caption').textContent(),await firstPoster.getAttribute('data-poster-name'));
    await fan.locator('[data-poster-tab]').last().focus();
    assert.equal(await fan.locator('[data-poster]').last().getAttribute('aria-pressed'),'true');
  }
  assert(!/\.0\s/.test(await page.locator('.season-priority').first().textContent()));
  await mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/library-desktop.png',fullPage:true});
  await page.locator('#anime-search').fill('this-cannot-match-zzzz');
  assert.equal(await page.locator('.anime-card').count(),0);
  assert(await page.locator('.empty-state').isVisible());
  await page.locator('[data-reset]').first().click();
  const firstTitle=await page.locator('.card-title').first().textContent();
  await page.locator('.bookmark-button').first().click();
  await page.goto(origin+'/#watchlist');
  await page.locator('.anime-card').first().waitFor();
  assert.equal(await page.locator('.anime-card').count(),1);
  assert.equal(await page.locator('.card-title').first().textContent(),firstTitle);
  await page.reload();
  await page.locator('.anime-card').first().waitFor();
  assert.equal(await page.locator('.anime-card').count(),1);
  await page.locator('.card-title').first().click();
  assert(await page.locator('dialog').isVisible());
  const detailItem=itemById.get(Number(await page.locator('.anime-card').first().getAttribute('data-id')));
  await page.locator('.detail-tags').waitFor();
  assert.equal(await page.locator('.extra-counts span').count(),3);
  assert.deepEqual(await page.locator('.detail-tags .tag').allTextContents(),detailItem.tags);
  await page.screenshot({path:'test-results/detail-desktop.png'});
  await page.keyboard.press('Escape');
  assert(!(await page.locator('dialog').isVisible()));
  const poster=page.locator('.anime-card').first().locator('.poster-leaf.active');
  await poster.click();
  await page.locator('.detail-tags').waitFor();
  assert(await page.locator('dialog').isVisible());
  assert.equal(await poster.getAttribute('data-detail'),String(detailItem.id));
  assert.deepEqual(await page.locator('.detail-tags .tag').allTextContents(),detailItem.tags);
  await page.keyboard.press('Escape');
  await poster.focus();
  await page.keyboard.press('Enter');
  await page.locator('.detail-tags').waitFor();
  assert(await page.locator('dialog').isVisible());
  await page.keyboard.press('Escape');
  await page.goto(origin+'/#features');
  await page.locator('.feature-row').first().waitFor();
  await page.locator('[data-importance="permutation"]').click();
  assert.equal(await page.locator('[data-importance="permutation"]').getAttribute('aria-selected'),'true');
  await page.locator('#dictionary-search').fill('季度');
  assert(await page.locator('.dictionary-item').count()>0);
  await page.locator('#dictionary-search').fill('年份');
  assert((await page.locator('#dictionary-items').textContent()).includes('2015'));
  await page.locator('#dictionary-search').fill('this-cannot-match-zzzz');
  assert.equal(await page.locator('.dictionary-item').count(),0);
  assert(!(await page.locator('[data-dictionary-toggle]').isVisible()));
  await page.locator('#dictionary-search').fill('');
  if(await page.locator('[data-dictionary-toggle]').isVisible())await page.locator('[data-dictionary-toggle]').click();
  assert.equal(await page.locator('.dictionary-item').count(),catalog.model.features);
  const allFeatureKeys=await page.locator('.dictionary-item').evaluateAll(items=>items.map(i=>i.dataset.feature));
  assert.deepEqual(allFeatureKeys,catalog.model.global_importance.map(f=>f.key));
  if(catalog.model.feature_selection){
    assert(catalog.model.global_importance.every(f=>f.key==='age_days'||f.value>0));
    assert(!(await page.locator('#dictionary-items').textContent()).includes('当前树分裂权重 0%'));
    assert.equal(catalog.model.feature_selection.retained_inputs,catalog.model.features);
  }
  if(catalog.model.source_breakdown){
    const usedSources=catalog.model.source_breakdown.filter(s=>s.importance>0);
    assert.equal(await page.locator('#source-analysis .coverage-row').count(),usedSources.length);
    for(const source of usedSources)assert(allFeatureKeys.includes('source='+source.source));
    assert((await page.locator('#training-coverage').textContent()).includes(`${catalog.model.training_coverage.previous_sales_known} / ${catalog.model.training_samples.toLocaleString('zh-CN')}`));
  }
  assert.equal(await page.locator('[data-dictionary-scope]').count(),0);
  assert.equal(await page.locator('[data-dictionary-toggle]').isVisible(),catalog.model.features>24);
  for(const key of ['log_disc_average','log_bd_copies','source_remaining_ratio','remaining_missing'])assert(!allFeatureKeys.includes(key));
  const years=catalog.coverage.years.length;
  assert.equal(await page.locator('.year-bar').count(),years);
  assert.equal(await page.getByText('加入社区评分，预测真的更好吗？').count(),0);
  await page.screenshot({path:'test-results/features-desktop.png',fullPage:true});
  await page.goto(origin+'/#backtest');
  await page.locator('.confusion').waitFor();
  await page.waitForFunction(()=>document.querySelector('#calibration-plot svg'));
  await page.waitForFunction(()=>document.querySelector('#annual-plot svg'));
  const yearsOptions=page.locator('#backtest-year option');
  if(await yearsOptions.count()>1){
    const oldest=await yearsOptions.last().getAttribute('value');
    await page.locator('#backtest-year').selectOption(oldest);
    assert.equal(await page.locator('#backtest-year').inputValue(),oldest);
    assert((await page.locator('.timeline-strip').textContent()).includes(oldest));
    await page.locator('#backtest-year').selectOption(await page.locator('#backtest-year option').first().getAttribute('value'));
  }
  await page.locator('.annual-details summary').click();
  assert(await page.locator('.annual-table').isVisible());
  assert((await page.locator('.confusion').textContent()).includes('尚未兑现'));
  assert(!(await page.locator('#app').textContent()).includes('正确排除'));
  assert.equal(await page.locator('.stat-label').nth(1).textContent(),'当年续作比例');
  const latestReport=catalog.backtest;
  const hits=latestReport.confusion[1][1], selected=latestReport.confusion[0][1]+hits;
  assert.equal(await page.locator('.stat-number').nth(1).textContent(),`${(hits/selected*100).toFixed(1)}%`);
  assert((await page.locator('.stat-note').nth(1).textContent()).includes(`${hits} / ${selected}`));
  assert(!(await page.locator('#app').textContent()).includes('Bangumi #'));
  const specific=page.locator('a[href="https://bgm.tv/subject/467784"]');
  if(await specific.count())assert((await specific.textContent()).includes('美妙宠物'));
  await page.screenshot({path:'test-results/backtest-desktop.png',fullPage:true});
  const download=page.waitForEvent('download');
  await page.locator('[data-report]').click();
  const reportDownload=await download;
  assert.equal(reportDownload.suggestedFilename(),'tsuzuku-backtest.json');
  await reportDownload.saveAs('test-results/downloaded-backtest.json');
  const exported=JSON.parse(await readFile('test-results/downloaded-backtest.json','utf8'));
  const expectedYears=await page.locator('#backtest-year option').count();
  assert.equal(exported.years?.length || 1,expectedYears);

  await page.goto(origin+'/#library');
  await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  const controls=async()=>Object.fromEntries(await page.locator('.controls input,.controls select').evaluateAll(els=>els.map(el=>[el.id,el.value])));
  const range=async(id,value)=>{await page.locator('#probability-'+id).fill(String(value));};
  const noHighlight=async()=>assert.equal(await page.locator('.count-filter.selected').count(),0);
  const ids=async()=>page.locator('.anime-card').evaluateAll(cards=>cards.map(c=>Number(c.dataset.id)));
  const fixedControls={
    'anime-search':'','date-from':'','date-to':'','source-filter':'','probability-min':'0','probability-max':'100','sort-filter':'probability','status-filter':'all'
  };
  const presetControls={
    high:{...fixedControls,'probability-min':'30','sort-filter':'score'},
    uncontinued:{...fixedControls,'status-filter':'uncontinued'},
    recent:{...fixedControls,'status-filter':'recent','sort-filter':'date'},
    all:{...fixedControls}
  };
  const expectedCounts={
    high:catalog.items.filter(i=>i.probability!=null&&i.probability>=.3&&i.probability<=1).length,
    uncontinued:catalog.items.filter(i=>!i.has_sequel&&i.status==='predict').length,
    recent:catalog.items.filter(i=>i.recent_sequel&&i.status!=='completed').length,
    all:catalog.items.length
  };
  let highContext=false;
  const assertCounts=async()=>{
    const low=highContext?Number(await page.locator('#probability-min').inputValue())/100:.3;
    const high=highContext?Number(await page.locator('#probability-max').inputValue())/100:1;
    for(const [key,presetN] of Object.entries(expectedCounts)) {
      const n=key==='high'?catalog.items.filter(i=>i.probability!=null&&i.probability>=low&&i.probability<=high).length:presetN;
      assert.equal(Number((await page.locator(`[data-preset="${key}"] strong`).textContent()).replaceAll(',','')),n,`${key} count: probability tracks slider; other counts use their presets`);
    }
    assert((await page.locator('[data-preset="high"] small').textContent()).includes(`${Math.round(low*100)}–${Math.round(high*100)}%`));
  };
  await assertCounts();
  const dirtyControls=async()=>{
    const tag=page.locator('.card-tags [data-tag]').first();if(await tag.count())await tag.click();
    await page.locator('#source-filter').selectOption('漫画改');
    await page.locator('#date-from').fill('2014-01-01');
    await page.locator('#date-to').fill('2022-12-31');
    await page.locator('#anime-search').fill('this-must-be-cleared');
    await noHighlight();await assertCounts();
  };
  await dirtyControls();
  await page.locator('[data-preset="high"]').click();highContext=true;
  assert.equal(new URL(page.url()).hash,'#library?preset=high');
  assert.deepEqual(await controls(),presetControls.high);
  assert.equal(await page.locator('.filter-tag').count(),0);
  assert.equal(await page.locator('[data-preset="high"]').getAttribute('aria-pressed'),'true');
  assert(expectedCounts.high>0);assert.equal(await page.locator('.anime-card').count(),Math.min(6,expectedCounts.high));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.deepEqual(await controls(),presetControls.high);
  assert.equal(await page.locator('[data-preset="high"]').getAttribute('aria-pressed'),'true');
  let before=await controls();
  await range('min',20);
  assert.deepEqual(await controls(),{...before,'probability-min':'20'});await noHighlight();await assertCounts();
  await range('max',80);
  assert.equal(new URL(page.url()).hash,'#library?context=high&min=20&max=80');
  assert.deepEqual(await controls(),{...before,'probability-min':'20','probability-max':'80'});
  const matches=catalog.items.filter(i=>i.probability!=null&&i.probability>=.2&&i.probability<=.8).sort((a,b)=>b.score-a.score||b.date.localeCompare(a.date)||a.id-b.id);
  assert.deepEqual(await ids(),matches.slice(0,6).map(i=>i.id));
  await assertCounts();
  const countBeforeDate=await page.locator('[data-preset="high"] strong').textContent();
  await page.locator('#date-from').fill('2015-01-01');
  await page.locator('#date-to').fill('2015-12-31');
  assert.equal(await page.locator('[data-preset="high"] strong').textContent(),countBeforeDate);
  await assertCounts();
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  await assertCounts();
  assert.equal(await page.locator('[data-preset="high"] strong').textContent(),countBeforeDate);
  await page.locator('[data-preset="uncontinued"]').click();highContext=false;
  assert.deepEqual(await controls(),presetControls.uncontinued);
  await assertCounts();
  assert.equal(await page.locator('[data-preset="uncontinued"]').getAttribute('aria-pressed'),'true');
  // Manual range changes remain independent; endpoints are inclusive and cannot cross.
  await page.locator('#probability-min').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#probability-min').inputValue(),'1');await noHighlight();
  await range('max',80);await range('min',90);assert.equal(await page.locator('#probability-min').inputValue(),'80');
  await range('max',10);assert.equal(await page.locator('#probability-max').inputValue(),'80');await assertCounts();
  await range('min',0);await range('max',100);await assertCounts();
  // Browser back restores a filter edit and its exact controls.
  before=await controls();await page.locator('#sort-filter').selectOption('seasons');
  await page.goBack();assert.deepEqual(await controls(),before);
  await page.goForward();assert.equal(await page.locator('#sort-filter').inputValue(),'seasons');
  const share=page.url();await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.equal(page.url(),share);assert.equal(await page.locator('#sort-filter').inputValue(),'seasons');await noHighlight();
  for(const preset of ['recent','all','high','uncontinued']){
    await dirtyControls();await range('min',90);
    await page.locator(`[data-preset="${preset}"]`).click();highContext=preset==='high';
    assert.equal(new URL(page.url()).hash,`#library?preset=${preset}`);
    assert.deepEqual(await controls(),presetControls[preset]);
    assert.equal(await page.locator('.filter-tag').count(),0);
    await assertCounts();
    assert.equal(await page.locator('.anime-card').count(),Math.min(6,expectedCounts[preset]));
    const expectedStatus=await page.locator('#status-filter').inputValue();
    await range('min',1);await noHighlight();
    assert.equal(await page.locator('#status-filter').inputValue(),expectedStatus);
  }
  await page.locator('[data-preset=all]').click();highContext=false;
  await page.locator('#status-filter').selectOption('completed');await noHighlight();
  assert.equal(await page.locator('[data-preset=all] strong').textContent(),catalog.items.length.toLocaleString('zh-CN'));
  for(const id of await ids())assert.equal(itemById.get(id).status,'completed');
  // Short links inherit homepage defaults, matching a search typed on the homepage.
  const needle='玛丽';
  await page.goto(origin+'/#library?q='+encodeURIComponent(needle));
  await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.equal(await page.locator('#anime-search').inputValue(),needle);
  assert.equal(await page.locator('#status-filter').inputValue(),'uncontinued');assert.equal(await page.locator('#date-to').inputValue(),bootstrap.initial_range.to);
  assert((await ids()).length>0);
  const tag='科幻';await page.goto(origin+'/?tag='+encodeURIComponent(tag)+'#library');
  await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  for(const id of await ids())assert(itemById.get(id).tags.includes(tag));
  await page.locator('#anime-search').fill('A & B + # / ?');
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('q'),'A & B + # / ?');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.equal(await page.locator('#anime-search').inputValue(),'A & B + # / ?');
  await page.goto(origin+'/#library');await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  for(const width of [768,390]){
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:`test-results/library-${width}.png`,fullPage:true});
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    assert(!overflow,`Horizontal overflow at ${width}`);
    const tags=page.locator('.all-tags').first();
    await tags.locator('summary').click();
    assert(await tags.locator('.tag-list .tag').last().isVisible());
    assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),`Expanded tags overflow at ${width}`);
    await page.locator('.anime-card').first().screenshot({path:`test-results/expanded-tags-${width}.png`});
    await tags.locator('summary').click();
    const mobilePoster=page.locator('.poster-column').first();
    if(await mobilePoster.locator('[data-poster-tab]').count()>1){
      await mobilePoster.locator('[data-poster-tab]').first().click();
      assert.equal(await mobilePoster.locator('[data-poster]').first().getAttribute('aria-pressed'),'true');
    }
    for(const view of ['features','backtest','about']){
      await page.goto(origin+'/#'+view);await page.locator('h1').waitFor();
      if(view==='backtest') {
        await page.locator('.annual-details summary').click();
        await page.screenshot({path:`test-results/backtest-${width}.png`,fullPage:true});
      }
      if(view==='features') {
        if(await page.locator('[data-dictionary-toggle]').getAttribute('aria-expanded')==='false' && await page.locator('[data-dictionary-toggle]').isVisible())await page.locator('[data-dictionary-toggle]').click();
        assert.equal(await page.locator('.dictionary-item').count(),allFeatureKeys.length);
        assert.equal(await page.locator('[data-dictionary-toggle]').isVisible(),catalog.model.features>24);
      }
      assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),`${view} overflow at ${width}`);
    }
    await page.goto(origin+'/#library');await page.locator('.anime-card').first().waitFor();
  }
  await page.locator('#menu-toggle').click();
  assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'),'true');
  await page.locator('#navigation [data-view="features"]').click();
  await page.waitForFunction(()=>document.querySelector('#menu-toggle').getAttribute('aria-expanded')==='false');
  assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'),'false');
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: 6-card homepage, complete presets, homepage highlight, preview counts and live high-preset probability counts, and manual sliders, URL/back/refresh, tags, active features only, details, annual reports, 3 widths.');
} finally { await browser?.close();server.kill(); }
