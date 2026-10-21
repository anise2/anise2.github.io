// Remote hotlinks only. Load every rendered card's front before automatically
// filling its back posters. Detail images remain lazy; all share a bounded queue.
const queue=[];
const jobs=new Map();
const observed=new Set();
let active=0,scheduled=false;
const LIMIT=3;
const foreground=img=>img.dataset.coverFront==='true'||!img.closest('.poster-leaf')||!!img.closest('.poster-leaf.active');
function pump(){
  scheduled=false;
  for(const [img,finish] of jobs)if(!img.isConnected)finish('cancelled');
  for(let i=queue.length-1;i>=0;i--)if(!queue[i].isConnected||queue[i].dataset.coverState!=='queued')queue.splice(i,1);
  queue.sort((a,b)=>Number(foreground(b))-Number(foreground(a)));
  while(active<LIMIT&&queue.length){
    // A free slot is not enough: every front must finish (or fail) first.
    if(!foreground(queue[0])&&[...jobs.keys()].some(foreground))break;
    const img=queue.shift();
    active++;img.dataset.coverState='loading';img.parentElement.dataset.state='loading';
    let finished=false;
    const finish=state=>{
      if(finished)return;finished=true;clearTimeout(timer);active--;jobs.delete(img);
      img.onload=img.onerror=null;
      img.dataset.coverState=state==='cancelled'?'idle':state;
      if(state==='cancelled')img.removeAttribute('src');
      else if(img.parentElement)img.parentElement.dataset.state=state;
      schedule();
    };
    const timer=setTimeout(()=>{img.removeAttribute('src');finish('error');},18000);
    jobs.set(img,finish);img.onload=()=>finish('loaded');img.onerror=()=>finish('error');
    img.fetchPriority=foreground(img)?'high':'low';
    img.src=img.dataset.src;
  }
}
function schedule(){if(!scheduled){scheduled=true;setTimeout(pump,80);}}
function enqueue(img){
  if(!img||img.dataset.coverState!=='idle')return;
  img.dataset.coverState='queued';queue.push(img);schedule();
}
const observer=new IntersectionObserver(entries=>{
  for(const e of entries)if(e.isIntersecting){
    enqueue(e.target);observer.unobserve(e.target);observed.delete(e.target);
  }
},{rootMargin:'100px'});
export function observeCovers(){
  for(const img of observed)if(!img.isConnected){observer.unobserve(img);observed.delete(img);}
  for(const [img,finish] of jobs)if(!img.isConnected)finish('cancelled');
  document.querySelectorAll('img[data-cover-state="idle"]:not([data-observed])').forEach(img=>{
    img.dataset.observed='true';
    const leaf=img.closest('.poster-leaf');
    if(leaf){img.dataset.coverFront=String(leaf.classList.contains('active'));enqueue(img);}
    else{observed.add(img);observer.observe(img);}
  });
}
export function activateCover(column){
  enqueue(column.querySelector('.poster-leaf.active img'));
  schedule();
}
