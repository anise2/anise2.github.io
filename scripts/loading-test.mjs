import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {readFile,stat,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const server=spawn('python3',['-m','http.server','5175','--bind','127.0.0.1','--directory','dist'],{stdio:'ignore'});
let browser,releaseBootstrap,releaseIndex,releaseImages;
const bootGate=new Promise(r=>releaseBootstrap=r), indexGate=new Promise(r=>releaseIndex=r), imageGate=new Promise(r=>releaseImages=r);
const requests=[];let concurrent=0,maxConcurrent=0,imageRequests=0;
const catalogItems=JSON.parse(await readFile('dist/data/library.json','utf8')).items;
const expectedProbabilityCount=(min,max)=>catalogItems.filter(i=>i.probability!=null&&i.probability>=min&&i.probability<=max).length;
try {
  for(let i=0;i<30;i++){try{if((await fetch('http://127.0.0.1:5175')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,...(process.env.CI?{}:{channel:'chrome'}),args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('request',r=>requests.push(r.url()));
  await page.route('**/data/bootstrap.json',async route=>{await bootGate;await route.continue();});
  await page.route('**/data/library.json?*',async route=>{await indexGate;await route.continue();});
  await page.route(/https:\/\/(api\.bgm\.tv|lain\.bgm\.tv|cdn\.myanimelist\.net)\//,async route=>{
    concurrent++;imageRequests++;maxConcurrent=Math.max(maxConcurrent,concurrent);
    await imageGate;concurrent--;await route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="130"><rect width="90" height="130" fill="#eadce3"/></svg>'});
  });
  await page.goto('http://127.0.0.1:5175',{waitUntil:'domcontentloaded'});
  await page.locator('h1').waitFor();
  assert(await page.locator('.loading-spinner').first().isVisible(),'Loading feedback before any catalog data arrives');
  assert.equal(await page.locator('.anime-card').count(),0);
  const start=Date.now();releaseBootstrap();
  await page.locator('.anime-card').first().waitFor();
  const firstCardsMs=Date.now()-start;
  assert.equal(await page.locator('.anime-card').count(),6);
  assert.equal(Number((await page.locator('[data-preset="high"] strong').textContent()).replaceAll(',','')),expectedProbabilityCount(.3,1),'Homepage previews the high preset, independently of its 0–100 slider');
  assert.equal(await page.locator('[data-preset="uncontinued"]').getAttribute('aria-pressed'),'true');
  assert.notEqual(await page.locator('#date-to').inputValue(),'');
  assert(await page.locator('#anime-search').isDisabled(),'Initial cards do not await full index');
  await page.waitForTimeout(500);
  assert(imageRequests>0&&imageRequests<=3,'Front covers occupy the bounded initial queue');
  assert(maxConcurrent<=3,'Remote images are concurrency limited');
  assert(!requests.some(u=>/catalog\.json|analysis\.json|backtest-report\.json/.test(u)),'Reports and full catalog are not startup dependencies');
  assert(await page.locator('.poster-leaf.active .cover-spinner').first().isVisible(),'Spinner while remote cover is pending');
  await mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/loading-before-images.png',animations:'disabled'});
  const startupImageRequests=imageRequests;
  releaseIndex();
  await page.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
  assert.equal(imageRequests,maxConcurrent,'Index arrival preserves existing covers instead of restarting requests');
  assert.equal(await page.locator('.poster-leaf:not(.active) img[src]').count(),0,'Back covers do not compete with front covers');
  // Images remain blocked throughout these filter interactions.
  await page.locator('#probability-min').fill('5');
  assert.equal(await page.locator('#status-filter').inputValue(),'uncontinued');
  assert.equal(await page.locator('.count-filter.selected').count(),0);
  await page.locator('#anime-search').fill('this-cannot-match-zzzz');
  assert.equal(await page.locator('.anime-card').count(),0);
  await page.locator('#anime-search').fill('');
  await page.locator('#probability-min').fill('0');
  assert.equal(await page.locator('.anime-card').count(),6);
  assert(await page.locator('#anime-search').isEnabled(),'Blocked remote images never disable filters');
  releaseImages();
  await page.waitForFunction(()=>document.querySelector('.cover-media[data-state="loaded"]'));
  const sizes={initialBytes:(await stat('dist/data/bootstrap.json')).size,indexBytes:(await stat('dist/data/library.json')).size,
    fullCatalogBytes:(await stat('data/catalog.json')).size,firstCardsMs,firstViewportImageRequests:startupImageRequests,maxConcurrent};
  assert(sizes.initialBytes<150000,'Initial JSON remains bounded');
  await writeFile('test-results/loading-metrics.json',JSON.stringify(sizes,null,2));
  // Hold individual responses: back covers must wait for ALL six fronts,
  // including below-fold cards, and then load without hovering or scrolling.
  const ordered=await browser.newPage({viewport:{width:1440,height:1000}});
  const coverRoutes=[];
  await ordered.route(/https:\/\/(api\.bgm\.tv|lain\.bgm\.tv|cdn\.myanimelist\.net)\//,route=>{coverRoutes.push(route);});
  try {
    await ordered.goto('http://127.0.0.1:5175',{waitUntil:'domcontentloaded'});
    await ordered.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
    await ordered.waitForFunction(()=>document.querySelectorAll('[data-cover-front="true"][src]').length===3);
    const frontUrls=await ordered.locator('[data-cover-front="true"]').evaluateAll(imgs=>imgs.map(img=>img.dataset.src));
    const backCount=await ordered.locator('[data-cover-front="false"]').count();
    assert.equal(frontUrls.length,6);assert(backCount>0,'Fixture includes layered covers');
    for(let i=0;i<6;i++){
      await ordered.waitForFunction(n=>document.querySelectorAll('[data-cover-front="true"][src]').length>=n,Math.min(6,i+3));
      assert(frontUrls.includes(coverRoutes[i].request().url()),'Only front requests before the barrier');
      assert.equal(await ordered.locator('[data-cover-front="false"][src]').count(),0,'No back request while any front is unresolved');
      assert(await ordered.locator('[data-cover-state="loading"]').count()<=3);
      if(i===5)await coverRoutes[i].abort(); // A failed remote front must not stall the backs forever.
      else await coverRoutes[i].fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="130"/>'});
      await ordered.waitForFunction(n=>document.querySelectorAll('[data-cover-front="true"][data-cover-state="loaded"], [data-cover-front="true"][data-cover-state="error"]').length===n,i+1);
    }
    for(let i=0;i<backCount;i++){
      await ordered.waitForFunction(n=>document.querySelectorAll('[data-cover-front="false"][src]').length>=n,i+1);
      assert(!frontUrls.includes(coverRoutes[6+i].request().url()));
      assert(await ordered.locator('[data-cover-state="loading"]').count()<=3);
      await coverRoutes[6+i].fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="130"/>'});
    }
    await ordered.waitForFunction(()=>[...document.querySelectorAll('[data-cover-front="false"]')].every(img=>img.dataset.coverState==='loaded'));
    assert.equal(coverRoutes.length,6+backCount);
    assert.equal(await ordered.evaluate(()=>scrollY),0,'Background covers load without scrolling');
    console.log(`Cover order checks passed: all 6 fronts settled, then ${backCount} backs loaded automatically.`);
  }finally{await ordered.close();}
  const shared=await browser.newPage();
  let releaseSearch;
  const searchGate=new Promise(resolve=>releaseSearch=resolve);
  await shared.route('**/data/library.json?*',async route=>{await searchGate;await route.continue();});
  await shared.route(/https:\/\/(api\.bgm\.tv|lain\.bgm\.tv|cdn\.myanimelist\.net)\//,route=>route.abort());
  try {
    const matchingItem=catalogItems.find(i=>i.probability>=.2&&i.probability<=.8);
    await shared.goto('http://127.0.0.1:5175/#library?context=high&min=20&max=80&q='+encodeURIComponent(matchingItem.name_cn||matchingItem.name),{waitUntil:'domcontentloaded'});
    await shared.locator('#anime-search').waitFor();
    assert((await shared.locator('#result-count').textContent()).includes('完整片单读取中'),'A shared search must not display homepage counts before the full index arrives');
    assert.equal(await shared.locator('.empty-state').count(),0,'Partial bootstrap data must not imply zero search results');
    assert.equal(await shared.locator('[data-preset="high"] strong').textContent(),'—','A nondefault shared range must not show the default bootstrap count');
    releaseSearch();
    await shared.waitForFunction(()=>document.querySelector('#anime-search')?.disabled===false);
    assert(await shared.locator('.anime-card').count()>0);
    assert.equal(Number((await shared.locator('[data-preset="high"] strong').textContent()).replaceAll(',','')),expectedProbabilityCount(.2,.8),'Count follows the shared probability range and ignores the search query');
  } finally {releaseSearch();await shared.close();}
  console.log('Slow-network loading checks passed:',sizes);
}finally{releaseBootstrap?.();releaseIndex?.();releaseImages?.();await browser?.close();server.kill();}
