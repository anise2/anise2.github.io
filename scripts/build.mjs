import {writeSiteData} from './site-data.mjs';
import {mkdir, cp, rm, readFile, writeFile} from 'node:fs/promises';
const data = JSON.parse(await readFile('data/catalog.json','utf8'));
if (data.schema_version !== 1 || !data.items?.length) throw new Error('Valid catalog data is required');
await rm('dist', {recursive:true, force:true});
await mkdir('dist/data', {recursive:true});
await cp('index.html','dist/index.html');
await cp('assets','dist/assets',{recursive:true});
const sizes=await writeSiteData(data,'dist');
// Development uses the same split artifacts; the large source catalog is never an initial request.
await writeSiteData(data,'.');
console.log('Initial JSON: '+Math.round(sizes.bootstrap/1024)+' KB; original catalog: '+Math.round(sizes.catalog/1024)+' KB');
await writeFile('dist/.nojekyll','');
console.log(`Static build: dist/ (${data.items.length} series, no image files copied)`);
