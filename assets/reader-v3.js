/* Academic Reading Master v3. User/model text is rendered only through DOM text nodes.
 * Canonical offsets count UTF-16 text and math source once, never math renderer hidden DOM.
 */
(function () {
  'use strict';
  const D = window.PAPER_DATA;
  const $ = id => document.getElementById(id);
  const VIEW_NAMES = {overview:'概览',explanation:'详解',translation:'翻译'};
  const STATUS_NAMES = {draft:'草稿',queued:'排队中',running:'回答中',answered:'已回答',failed:'回答失败',waiting_bridge:'等待答疑助手'};
  const COLORS = ['#497da7','#8b6daa','#af8a49','#528d83','#a56f84','#647eaf'];
  const scroll = $('reading-scroll'), content = $('paper-content'), layer = $('annotation-layer');
  const state = {view:'overview',positions:{},annotations:[],drafts:[],token:null,online:false,selected:null,selectedAnchor:null,panelMode:'list',filter:'all',polling:false,renderQueued:false,notified:new Set()};
  const renderedPanel = {key:null,snapshot:null};
  const paperId = String(D && D.id || 'paper'), version = D && (D.content_version || 1);
  const storagePrefix = 'arm_v3_' + paperId + '_';
  const colorCache = readLocal('colors', {});
  function readLocal(key, fallback) { try { return JSON.parse(localStorage.getItem(storagePrefix + key)) || fallback; } catch (_) { return fallback; } }
  function writeLocal(key, value) { try { localStorage.setItem(storagePrefix + key, JSON.stringify(value)); return true; } catch (_) { return false; } }
  function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=String(text); return n; }
  function button(text, cls, fn) { const n=el('button',cls,text); n.type='button'; if(fn)n.addEventListener('click',fn); return n; }
  function uid() { return window.crypto && crypto.randomUUID ? crypto.randomUUID() : 'a-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2); }
  function toast(message, persist) { $('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);if(!persist)toast.timer=setTimeout(()=>$('toast').hidden=true,4500); }
  function safeURL(value, asset) { if(typeof value!=='string'||!value.trim())return null;try{const u=new URL(value,location.href);if(!['http:','https:','file:'].includes(u.protocol))return null;if(asset && (u.origin!==location.origin || !u.pathname.startsWith(new URL('.',location.href).pathname)))return null;return u.href;}catch(_){return null;} }
  function safeSVG(value){
    if(typeof value!=='string'||value.length>1000000)return null;
    const doc=new DOMParser().parseFromString(value,'image/svg+xml'),svg=doc.documentElement;
    if(svg.localName!=='svg'||doc.querySelector('parsererror'))return null;
    const tags=new Set(['svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','defs','marker','linearGradient','radialGradient','stop','clipPath','mask','title','desc','use']);
    const attrs=new Set(['xmlns','viewBox','width','height','x','y','x1','x2','y1','y2','cx','cy','r','rx','ry','d','points','fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-linecap','stroke-linejoin','stroke-dasharray','opacity','transform','id','font-size','font-family','font-weight','text-anchor','dominant-baseline','dx','dy','marker-start','marker-mid','marker-end','markerWidth','markerHeight','refX','refY','orient','offset','stop-color','stop-opacity','gradientUnits','gradientTransform','clip-path','mask','href','preserveAspectRatio']);
    Array.from(svg.querySelectorAll('*')).forEach(n=>{if(!tags.has(n.localName))n.remove();});
    [svg].concat(Array.from(svg.querySelectorAll('*'))).forEach(n=>Array.from(n.attributes).forEach(a=>{const v=a.value;if(!attrs.has(a.name)||(/^href$/.test(a.name)&&!v.startsWith('#'))||(/url\s*\(/i.test(v)&&!/^url\(#[\w-]+\)$/.test(v)))n.removeAttribute(a.name);}));
    return URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));
  }
  function allAnnotations(){ return state.annotations.concat(state.drafts); }
  function findAnnotation(id){ return allAnnotations().find(a=>a.id===id); }
  function anchorOverlap(a,b){const x=anchorFor(a),y=anchorFor(b);if(!x||!y)return false;const xf=x.fragments&&x.fragments.length?x.fragments:[x],yf=y.fragments&&y.fragments.length?y.fragments:[y];return xf.some(f=>yf.some(g=>f.content_id===g.content_id&&(f.view||x.view)===(g.view||y.view)&&(f.language||x.language||'zh')===(g.language||y.language||'zh')&&(x.kind==='block'||y.kind==='block'||(f.field===g.field&&Number.isFinite(f.start)&&Number.isFinite(g.start)&&f.start<g.end&&g.start<f.end))));}
  function colorFor(a){const identity=String(a.id).replace(/^draft-/,'');if(typeof a.identity_color==='string'&&/^(#[0-9a-fA-F]{6}|hsl\([\d., %]+\))$/.test(a.identity_color)){if(colorCache[identity]!==a.identity_color){colorCache[identity]=a.identity_color;writeLocal('colors',colorCache);}return a.identity_color;}if(colorCache[identity])return colorCache[identity];let h=0;for(let i=0;i<identity.length;i++)h=((h<<5)-h+identity.charCodeAt(i))|0;const used=new Set(allAnnotations().filter(b=>b.id!==a.id&&anchorOverlap(a,b)).map(b=>b.identity_color||colorCache[String(b.id).replace(/^draft-/,'')]).filter(Boolean));let color;for(let i=0;i<COLORS.length;i++){const candidate=COLORS[(Math.abs(h)+i)%COLORS.length];if(!used.has(candidate)){color=candidate;break;}}if(!color){let hue=Math.abs(h)%360;do{color='hsl('+hue+' 42% 42%)';hue=(hue+137.508)%360;}while(used.has(color));}colorCache[identity]=color;writeLocal('colors',colorCache);return color;}
  function annotationNumber(a){ return allAnnotations().findIndex(x=>x.id===a.id)+1; }
  function normalizeAnnotation(a){ return Object.assign({},a,{user_note:a.user_note===undefined?(a.note||''):a.user_note,selected_text:a.selected_text===undefined?(a.quote||''):a.selected_text,status:a.status||(a.answer?'answered':'draft'),unread:!!a.unread}); }
  function sections(view){ return view==='translation'?(D.sections||[]):((D[view]||{}).sections||[]); }
  function sourceButton(ref){const label=ref.label||('原文'+(ref.page?' · p.'+ref.page:''));return button(label,'source-link',()=>jumpSource(ref));}
  function jumpSource(ref){
    const id=ref.block_id||ref.content_id||ref.section_id;
    if(id){const candidates=['translation','explanation','overview'];const wanted=candidates.find(v=>sections(v).some(s=>s.id===id||(s.blocks||[]).some(b=>b.id===id)));if(wanted){switchView(wanted);requestAnimationFrame(()=>{const target=Array.from(content.querySelectorAll('[data-content-id],[data-section-id]')).find(n=>n.dataset.contentId===id||n.dataset.sectionId===id);if(target){target.scrollIntoView({block:'center'});target.classList.add('jump-flash');setTimeout(()=>target.classList.remove('jump-flash'),1600);scheduleOverlay();}});return;}}
    if(ref.page){window.open('source.pdf#page='+encodeURIComponent(ref.page),'_blank','noopener');return;}
    const u=safeURL(ref.url,false);if(u)window.open(u,'_blank','noopener');else toast('这条来源没有可跳转的位置。');
  }
  // Minimal safe inline markdown. No HTML parser, raw HTML remains visible as text.
  function literal(parent,text){if(!text)return;const broken=/\\[()[\]]|(?<!\\)\$(?!\d+(?:[.,]\d+)?(?:\s|[.,;:!?)]|$))/.test(text);if(broken){const n=el('span','math-error');n.append(el('span','math-error-label','公式渲染错误：数学分隔符未闭合'),el('code','math-error-source',text));n.setAttribute('role','alert');parent.append(n);}else parent.append(document.createTextNode(text));}
  function inline(parent, text) {
    const s=String(text==null?'':text);
    const re=/(\*\*([^*\n]+)\*\*|`([^`\n]+)`|\*([^*\n]+)\*|\[([^\]\n]+)\]\(([^)\s]+)\)|\$\$([\s\S]+?)\$\$|\$([^$\n]+)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\])/g;
    let last=0,m;
    while((m=re.exec(s))){if(m.index>last)literal(parent,s.slice(last,m.index));let node;if(m[2]){node=el('strong');inline(node,m[2]);}else if(m[3])node=el('code',null,m[3]);else if(m[4]){node=el('em');inline(node,m[4]);}else if(m[5]){const url=safeURL(m[6],false);node=el(url?'a':'span');inline(node,m[5]);if(url){node.href=url;node.target='_blank';node.rel='noopener noreferrer';}}else{const latex=m[7]||m[8]||m[9]||m[10]||'';const suspicious=m[8]&&(s[m.index-1]==='\\'||/^\s|\s$/.test(latex)||/^(?:[A-Za-z_][\w.-]*[\\/]|[A-Za-z]:[\\/])/.test(latex));node=suspicious?document.createTextNode(m[0]):mathAtom(latex,!!(m[7]||m[10]),m[0]);}parent.append(node);last=re.lastIndex;}
    if(last<s.length)literal(parent,s.slice(last));
  }
  function mathAtom(latex, display, canonical){const n=el('span','math-atom');n.dataset.canonical=canonical===undefined?String(latex):canonical;n.dataset.latex=String(latex);n.dataset.display=String(!!display);const render=el('span','math-render');n.append(render);return n;}
  function field(parent, text, id, name, language, cls){const f=el('div','anchor-field'+(cls?' '+cls:''));Object.assign(f.dataset,{contentId:id,field:name,language,view:state.view,contentVersion:String(version)});inline(f,text);parent.append(f);return f;}
  function displayField(data, names){for(const name of names){if(typeof data[name+'_latex']==='string')return{name:name+'_latex',text:data[name+'_latex']};if(data[name]!==undefined&&data[name]!==null&&String(data[name]))return{name,text:String(data[name])};}return null;}
  function captionField(b,lang){return displayField(b,lang==='en'?['caption_en','fig_caption_en']:['caption_zh','fig_caption_zh','caption']);}
  function appendFigure(frame,b,lang){const caption=captionField(b,lang),url=safeURL(b.src||b.asset,true)||(b.type==='diagram'?safeSVG(b.svg):null);if(url){const img=el('img','figure-image');img.src=url;img.alt=caption?caption.text:'论文图示';img.loading='lazy';img.addEventListener('load',scheduleOverlay);img.addEventListener('error',()=>{img.replaceWith(el('div','asset-placeholder','图示资源无法加载'));scheduleOverlay();});frame.append(img);}else frame.append(el('div','asset-placeholder','图示暂无独立资源。请查看原文 PDF。'));if(caption)field(frame,caption.text,b.id,caption.name,lang,'block-caption');return caption;}
  function legacyTable(frame,b,lang){
    if(typeof b.table_html!=='string'||b.table_html.length>1000000)return false;
    // Parse inertly, then build a new table from structural and inline whitelists.
    // No source node, attribute, or HTML string is inserted into the live document.
    const parsed=new DOMParser().parseFromString(b.table_html,'text/html'),source=parsed.querySelector('table');if(!source)return false;
    const table=el('table','data-table legacy-data-table'),forbidden=new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','IMG','VIDEO','AUDIO','SVG','MATH','INPUT','BUTTON','FORM','LINK','META']),tags=new Set(['B','STRONG','I','EM','U','SUB','SUP','BR','A','CODE']);
    function copyInline(target,node){if(node.nodeType===3){inline(target,node.nodeValue);return;}if(node.nodeType!==1||forbidden.has(node.tagName))return;let next=target;if(tags.has(node.tagName)){if(node.tagName==='A'){const url=safeURL(node.getAttribute('href'),false);if(url){next=el('a');next.href=url;next.target='_blank';next.rel='noopener noreferrer';target.append(next);}}else{next=el(node.tagName.toLowerCase());target.append(next);}}Array.from(node.childNodes).forEach(child=>copyInline(next,child));}
    function cellField(target,node,name){const f=field(target,'',b.id,name,lang);Array.from(node.childNodes).forEach(child=>copyInline(f,child));return f;}
    const caption=Array.from(source.children).find(n=>n.tagName==='CAPTION');if(caption){const c=el('caption');cellField(c,caption,'table_html.caption');table.append(c);}
    const rows=Array.from(source.querySelectorAll('tr')).filter(row=>row.closest('table')===source);rows.forEach((row,i)=>{const tr=el('tr');Array.from(row.children).filter(cell=>cell.tagName==='TD'||cell.tagName==='TH').forEach((cell,j)=>{const td=el(cell.tagName.toLowerCase());for(const attr of ['rowspan','colspan']){const value=cell.getAttribute(attr);if(value&&/^\d{1,3}$/.test(value)&&Number(value)>0)td.setAttribute(attr,value);}cellField(td,cell,'table_html.rows.'+i+'.cells.'+j);tr.append(td);});if(tr.children.length)table.append(tr);});if(!rows.length)return false;frame.append(table);return true;
  }
  function originalExplanations(frame,b,lang){
    if(lang!=='zh'||!Array.isArray(b.annotations)||!b.annotations.length)return;
    const details=el('details','original-explanations'),summary=el('summary',null,'原有讲解 · '+b.annotations.length+' 条');details.append(summary);
    b.annotations.forEach((a,i)=>{if(!a||typeof a!=='object')return;const note=el('section','original-explanation'),title=displayField(a,['title']),body=displayField(a,['body']);if(title)field(note,title.text,b.id,'annotations.'+i+'.'+title.name,lang,'original-explanation-title');if(body)field(note,body.text,b.id,'annotations.'+i+'.'+body.name,lang,'original-explanation-body');details.append(note);});
    details.addEventListener('toggle',()=>{typeset(details);scheduleOverlay();});frame.append(details);
  }
  function revealField(root){let parent=root;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}scheduleOverlay();}
  function annotationVisible(root){
    if(!root||!root.isConnected||!content.contains(root))return false;
    // Closed <details> can expose cached, non-zero descendant rectangles.
    // Their computed visibility/display also remain visible/block in Chromium.
    for(let n=root;n;n=n.parentElement){
      if(n.hidden||n.getAttribute('aria-hidden')==='true')return false;
      if(n.tagName==='DETAILS'&&!n.open){const summary=Array.from(n.children).find(child=>child.tagName==='SUMMARY');if(!summary||!summary.contains(root))return false;}
    }
    const style=getComputedStyle(root);return style.display!=='none'&&!['hidden','collapse'].includes(style.visibility)&&root.getClientRects().length>0;
  }
  function annotationRects(part){
    if(!part||!annotationVisible(part.root))return[];
    const index=canonicalIndex(part.root),rects=[];
    // Measure text nodes and each math atom once. Range.getClientRects() across
    // element boundaries also includes nested inline/accessibility rectangles.
    index.segments.forEach(seg=>{
      const start=Math.max(seg.start,part.start),end=Math.min(seg.end,part.end);if(end<=start)return;
      if(seg.atom)rects.push(seg.node.getBoundingClientRect());
      else{const range=document.createRange();range.setStart(seg.node,start-seg.start);range.setEnd(seg.node,end-seg.start);rects.push(...range.getClientRects());}
    });
    const merged=[];rects.filter(r=>r.width>0&&r.height>0).forEach(r=>{
      const previous=merged[merged.length-1];
      if(previous&&Math.abs(previous.top-r.top)<1&&Math.abs(previous.bottom-r.bottom)<1&&r.left<=previous.right+1&&r.right>=previous.left-1){previous.left=Math.min(previous.left,r.left);previous.right=Math.max(previous.right,r.right);previous.width=previous.right-previous.left;}
      else merged.push({left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height});
    });return merged;
  }
  function renderBlock(b,lang){
    const frame=el('div','block-frame language-'+lang);Object.assign(frame.dataset,{contentId:b.id,language:lang,view:state.view});
    frame.append(button('✎','block-question',()=>newBlockDraft(frame,b)));
    frame.lastChild.setAttribute('aria-label','对整块内容提问');
    const suffix=lang==='en'?'en':'zh', bodyField=lang==='zh'&&typeof b.text_zh_latex==='string'?'text_zh_latex':'text_'+suffix, body=b[bodyField] || (lang==='en'?'原文暂缺':'');
    if(Array.isArray(b.pipeline_fields)){
      frame.classList.add('pipeline-card');const head=el('div','pipeline-card-heading');if(b.step!==undefined)head.append(el('span','pipeline-step',b.step));const title=displayField(b,['title']);if(title)field(head,title.text,b.id,title.name,lang,'pipeline-title');frame.append(head);
      b.pipeline_fields.forEach((item,i)=>{if(!item||typeof item!=='object')return;const row=el('div','pipeline-field');if(item.label)row.append(el('span','pipeline-field-label',item.label));const value=displayField(item,['text']);if(value)field(row,value.text,b.id,'pipeline_fields.'+i+'.'+value.name,lang,'pipeline-field-value');frame.append(row);});
    }else if(b.q!==undefined||b.q_latex!==undefined){
      frame.classList.add('self-check-card');const question=displayField(b,['q']),hint=displayField(b,['hint']);if(question)field(frame,question.text,b.id,question.name,lang,'self-check-question');if(hint){const details=el('details','self-check-hint'),summary=el('summary',null,'显示提示');details.append(summary);field(details,hint.text,b.id,hint.name,lang,'self-check-hint-text');details.addEventListener('toggle',()=>{summary.textContent=details.open?'收起提示':'显示提示';scheduleOverlay();});frame.append(details);}
    }else if(b.type==='figure'||b.type==='diagram'){
      const caption=appendFigure(frame,b,lang);if(b[bodyField]&&(!caption||body!==caption.text))field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='formula'){
      const f=el('div','anchor-field formula-display');Object.assign(f.dataset,{contentId:b.id,field:'latex',language:lang,view:state.view,contentVersion:String(version)});f.append(mathAtom(b.latex||'',true,b.latex||''));frame.append(f);
      if(b.eq_ref)field(frame,b.eq_ref,b.id,'eq_ref',lang,'formula-reference');
      if(body)field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='table'){
      const rows=b['rows_'+suffix]||[];if(rows.length){const table=el('table','data-table');rows.forEach((row,i)=>{const tr=el('tr');(Array.isArray(row)?row:[row]).forEach((cell,j)=>{const td=el(i===0?'th':'td');field(td,cell,b.id,'rows_'+suffix+'.'+i+'.'+j,lang);tr.append(td);});table.append(tr);});frame.append(table);}else if(b.src||b.asset)appendFigure(frame,b,lang);else if(!legacyTable(frame,b,lang))frame.append(el('div','asset-placeholder','表格暂无结构化内容。请查看原文 PDF。'));
      const caption=captionField(b,lang);if(caption&&!(b.src||b.asset))field(frame,caption.text,b.id,caption.name,lang,'block-caption');if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='list'){
      const list=el(b.ordered?'ol':'ul','original-list');(b.items||[]).forEach((item,i)=>{const value=typeof item==='object'&&item!==null?displayField(item,['text_'+suffix]):{name:'text_'+suffix,text:String(item)};if(!value)return;const li=el('li');field(li,value.text,b.id,'items.'+i+'.'+value.name,lang);list.append(li);});frame.append(list);if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='algorithm'){
      frame.classList.add('algorithm-card');const title=displayField(b,['algo_title']);if(title)field(frame,title.text,b.id,title.name,lang,'algorithm-title');const name=Array.isArray(b.algo_lines_latex)?'algo_lines_latex':'algo_lines',lines=el('div','algorithm-lines');(b[name]||[]).forEach((line,i)=>field(lines,line,b.id,name+'.'+i,lang,'algorithm-line'));frame.append(lines);if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else field(frame,body,b.id,bodyField,lang);
    originalExplanations(frame,b,lang);
    if((b.source_refs||[]).length){const refs=el('div','block-sources');b.source_refs.forEach(r=>refs.append(sourceButton(r)));frame.append(refs);}
    return frame;
  }
  function renderPaper(){
    $('paper-short-title').textContent=D.title_cn||D.title_en||paperId;
    const h=$('paper-heading');h.replaceChildren(el('div','paper-kicker',(D.venue||'RESEARCH PAPER')+(D.year?' · '+D.year:'')));h.append(el('h1',null,D.title_cn||D.title_en||paperId));if(D.title_en&&D.title_cn)h.append(el('p','paper-title-en',D.title_en));const authors=Array.isArray(D.authors)?D.authors.map(x=>typeof x==='string'?x:(x.name||'')).join(' · '):D.authors||'';h.append(el('div','paper-meta',authors+(D.level==='novice'?'　/　新手阅读':'　/　论文伴读')));
    renderView();
  }
  function renderView(){
    content.replaceChildren();$('toc-list').replaceChildren();$('toc-view-label').textContent=VIEW_NAMES[state.view]+'目录';$('section-count').textContent=sections(state.view).length+' 节';
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.view===state.view)));
    scroll.querySelector('.paper-width').classList.toggle('translation-width',state.view==='translation');
    const intro=$('view-intro');intro.replaceChildren();if(state.view==='translation'){const heads=el('div','language-headings');heads.append(el('span',null,'English · 原文'),el('span',null,'中文 · 译文'));intro.append(heads);}else intro.append(el('span','view-description',state.view==='overview'?'先建立整篇论文的路线图，再沿来源进入细节。':'按章节理解方法、证据和边界。每段保留原文来源。'));
    if(state.view==='overview'){const blocks=(D.overview||{}).pipeline_blocks||D.pipeline_blocks;if(Array.isArray(blocks)&&blocks.length){intro.classList.add('with-pipeline-map');const map=el('nav','pipeline-map');map.setAttribute('aria-label','方法流水线');blocks.forEach((item,i)=>{if(i)map.append(el('span','pipeline-map-arrow','→'));const link=button('','pipeline-map-node',()=>{const target=Array.from(content.querySelectorAll('.block-row')).find(n=>n.dataset.blockId===item.content_id);if(target){target.scrollIntoView({block:'start'});target.classList.add('jump-flash');setTimeout(()=>target.classList.remove('jump-flash'),1600);scheduleOverlay();}});link.append(el('span','pipeline-map-step',item.step===undefined?i+1:item.step),el('span',null,item.name||item.content_id));map.append(link);});intro.append(map);}}else intro.classList.remove('with-pipeline-map');
    sections(state.view).forEach((s,i)=>{
      const section=el('section','paper-section');section.dataset.sectionId=s.id;const heading=el('div','section-heading');heading.append(el('h2',null,s.title_cn||s.title_en||('章节 '+(i+1))));if(s.page){const a=el('a','page-link','原文 p.'+s.page+' ↗');a.href='source.pdf#page='+s.page;a.target='_blank';a.rel='noopener';heading.append(a);}section.append(heading);
      (s.blocks||[]).forEach(b=>{const row=el('div','block-row'+(state.view==='translation'?' translation-row':''));row.dataset.blockId=b.id;if(state.view==='translation')row.append(renderBlock(b,'en'),renderBlock(b,'zh'));else row.append(renderBlock(b,'zh'));section.append(row);});content.append(section);
      const t=button(s.title_cn||s.title_en||s.id,null,()=>{section.scrollIntoView({block:'start'});document.body.classList.remove('toc-visible');scheduleOverlay();});t.dataset.sectionId=s.id;$('toc-list').append(t);
    });
    if(!sections(state.view).length)content.append(el('p','empty-view','本视图尚无内容。请在 paper-data.js 中提供完整的 '+VIEW_NAMES[state.view]+' 章节。'));
    typeset(content);scheduleOverlay();
  }
  function savePosition(){const visible=Array.from(content.querySelectorAll('.paper-section')).find(s=>s.getBoundingClientRect().bottom>scroll.getBoundingClientRect().top+30);state.positions[state.view]={top:scroll.scrollTop,section_id:visible&&visible.dataset.sectionId};writeLocal('positions',state.positions);writeLocal('view',state.view);}
  function switchView(view){if(!VIEW_NAMES[view]||view===state.view)return;savePosition();state.view=view;state.selectedAnchor=null;$('selection-action').hidden=true;renderView();scroll.scrollTop=(state.positions[view]||{}).top||0;writeLocal('view',view);scheduleOverlay();}
  function typeset(root){root.querySelectorAll('.math-atom').forEach(atom=>{if(atom.dataset.mathState)return;const target=atom.querySelector('.math-render');try{if(!window.katex)throw new Error('本地数学排版库未加载');if(!atom.dataset.latex.trim())throw new Error('公式缺少 LaTeX 源码');window.katex.render(atom.dataset.latex,target,{displayMode:atom.dataset.display==='true',throwOnError:true,strict:'error',trust:false,maxExpand:1000,maxSize:20,output:'htmlAndMathml'});atom.dataset.mathState='rendered';}catch(error){atom.dataset.mathState='error';atom.classList.add('math-error');target.replaceChildren(el('span','math-error-label','公式渲染错误：'+error.message),el('code','math-error-source',atom.dataset.latex));target.setAttribute('role','alert');}});scheduleOverlay();}
  // Text and math are indexed independently of rendered formula accessibility nodes.
  function canonicalIndex(root){const segments=[];let text='';function walk(node){if(node.nodeType===3){if(node.nodeValue){segments.push({node,start:text.length,end:text.length+node.nodeValue.length,atom:false});text+=node.nodeValue;}return;}if(node.nodeType!==1)return;if(node.classList.contains('math-atom')){const t=node.dataset.canonical||'';segments.push({node,start:text.length,end:text.length+t.length,atom:true});text+=t;return;}if(node.matches('[aria-hidden="true"],.MJX_Assistive_MathML,mjx-assistive-mml,script,style,button'))return;Array.from(node.childNodes).forEach(walk);}walk(root);return{text,segments};}
  function boundary(seg,offset,end){if(!seg.atom)return{node:seg.node,offset:Math.max(0,Math.min(seg.node.nodeValue.length,offset-seg.start))};const p=seg.node.parentNode,i=Array.prototype.indexOf.call(p.childNodes,seg.node);return{node:p,offset:i+(end?1:0)};}
  function rangeFor(root,start,end){const idx=canonicalIndex(root);if(start<0||end>idx.text.length||end<=start)return null;const first=idx.segments.find(s=>s.end>start),last=idx.segments.slice().reverse().find(s=>s.start<end);if(!first||!last)return null;const a=boundary(first,start,false),b=boundary(last,end,true),r=document.createRange();try{r.setStart(a.node,a.offset);r.setEnd(b.node,b.offset);return r;}catch(_){return null;}}
  function fragmentFromSelection(f,range){
    const idx=canonicalIndex(f);let start=null,end=null;
    idx.segments.forEach(seg=>{try{if(!range.intersectsNode(seg.node))return;if(seg.atom){if(start===null)start=seg.start;end=seg.end;}else{let a=range.startContainer===seg.node?range.startOffset:0,b=range.endContainer===seg.node?range.endOffset:seg.node.nodeValue.length;if(b<=a)return;if(start===null)start=seg.start+a;end=seg.start+b;}}catch(_){}});
    if(start===null||end===null||end<=start)return null;
    return{view:f.dataset.view,language:f.dataset.language,content_id:f.dataset.contentId,field:f.dataset.field,content_version:version,start,end,exact:idx.text.slice(start,end),prefix:idx.text.slice(Math.max(0,start-40),start),suffix:idx.text.slice(end,end+40)};
  }
  function captureSelection(){const selection=window.getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return null;const r=selection.getRangeAt(0);if(!content.contains(r.commonAncestorContainer))return null;const fragments=[];content.querySelectorAll('.anchor-field').forEach(f=>{if(annotationVisible(f)&&r.intersectsNode(f)){const part=fragmentFromSelection(f,r);if(part)fragments.push(part);}});if(!fragments.length||!fragments.map(f=>f.exact).join('').trim())return null;const first=fragments[0];return Object.assign({},first,{kind:'text',language:fragments.every(f=>f.language===first.language)?first.language:'mixed',fragments});}
  function findField(f){return Array.from(content.querySelectorAll('.anchor-field')).find(n=>n.dataset.view===(f.view||state.view)&&n.dataset.contentId===f.content_id&&n.dataset.language===(f.language||'zh')&&n.dataset.field===f.field);}
  function locateFragment(f){const root=findField(f);if(!root)return{status:'unlocated',reason:'此视图找不到原内容块'};const text=canonicalIndex(root).text;const exact=String(f.exact||'');if(!exact)return{status:'unlocated',reason:'缺少选中文字'};if(f.content_version!==undefined&&String(f.content_version)===String(version)&&Number.isInteger(f.start)&&Number.isInteger(f.end)&&text.slice(f.start,f.end)===exact)return{status:'located',root,range:rangeFor(root,f.start,f.end),start:f.start,end:f.end};const matches=[];let p=0;while((p=text.indexOf(exact,p))>=0){const prefix=String(f.prefix||''),suffix=String(f.suffix||'');if((!prefix||text.slice(Math.max(0,p-prefix.length),p)===prefix)&&(!suffix||text.slice(p+exact.length,p+exact.length+suffix.length)===suffix))matches.push(p);p+=Math.max(1,exact.length);}if(matches.length!==1)return{status:'unlocated',reason:matches.length?'相同引文出现多次，无法唯一定位':'内容已更新，原引文尚未匹配'};return{status:'located',root,range:rangeFor(root,matches[0],matches[0]+exact.length),start:matches[0],end:matches[0]+exact.length};}
  function anchorFor(a){
    if(a.anchor)return a.anchor;
    // Legacy anchors remain on the historical explanation blocks with their original IDs.
    if(a.block_id){const view='explanation';const lang='zh';const block=sections(view).flatMap(s=>s.blocks||[]).find(b=>b.id===a.block_id),field=block&&typeof block.text_zh_latex==='string'?'text_zh_latex':'text_zh';if(!a.selected_text)return{kind:'block',view,language:lang,content_id:a.block_id,content_version:version,fragments:[]};return{kind:'text',view,language:lang,content_id:a.block_id,field,content_version:0,exact:a.selected_text,prefix:'',suffix:'',fragments:[{view,language:lang,content_id:a.block_id,field,content_version:0,exact:a.selected_text,prefix:'',suffix:''}]};}
    return null;
  }
  function locateAnnotation(a){const anchor=anchorFor(a);if(!anchor)return{status:'unlocated',reason:'历史批注没有定位信息',ranges:[]};if(anchor.migration_status==='unlocated')return{status:'unlocated',reason:anchor.reason||'历史选区不能唯一恢复',ranges:[]};if(anchor.view!==state.view)return{status:'other-view',ranges:[],view:anchor.view};if(anchor.kind==='block'){const block=Array.from(content.querySelectorAll('.block-frame')).find(n=>n.dataset.contentId===anchor.content_id&&n.dataset.language===(anchor.language||'zh'));return block?{status:'located',block,ranges:[]}:{status:'unlocated',reason:'原内容块已移除',ranges:[]};}const fragments=anchor.fragments&&anchor.fragments.length?anchor.fragments:[anchor];const parts=fragments.map(locateFragment);const failed=parts.find(x=>x.status==='unlocated');return{status:failed?'unlocated':'located',reason:failed&&failed.reason,ranges:parts.filter(p=>p.range).map(p=>p.range),parts};}
  function scheduleOverlay(){if(state.renderQueued)return;state.renderQueued=true;requestAnimationFrame(()=>{state.renderQueued=false;drawAnnotations();updateTOC();});}
  function drawAnnotations(){
    layer.replaceChildren();const bounds=scroll.getBoundingClientRect(),tails=[],stripes=[];
    const entries=allAnnotations().map((a,index)=>{const loc=locateAnnotation(a);if(loc.status!=='located')return null;const rects=loc.block?(annotationVisible(loc.block)?[loc.block.getBoundingClientRect()]:[]):(loc.parts||[]).flatMap(annotationRects);return rects.length?{a,index,loc,rects,color:colorFor(a)}:null;}).filter(Boolean);
    if(!entries.length)return;
    const selectedRects=entries.flatMap(entry=>entry.rects).filter(r=>r.bottom>bounds.top&&r.top<bounds.bottom&&r.right>bounds.left&&r.left<bounds.right);
    const textRects=Array.from(content.querySelectorAll('.anchor-field')).filter(root=>{if(!annotationVisible(root))return false;const r=root.getBoundingClientRect();return r.bottom>bounds.top&&r.top<bounds.bottom&&r.right>bounds.left&&r.left<bounds.right;}).flatMap(root=>annotationRects({root,start:0,end:Infinity}));
    const obstacleRects=selectedRects.concat(textRects);
    entries.forEach(({a,index,loc,rects,color})=>{
      rects.forEach(r=>{if(r.bottom<=bounds.top||r.top>=bounds.bottom||r.right<=bounds.left||r.left>=bounds.right)return;const mark=el('div','annotation-highlight'+(loc.block?' block-highlight':''));mark.dataset.annotationId=a.id;mark.style.setProperty('--ann-color',color);Object.assign(mark.style,{left:Math.max(bounds.left,r.left)+'px',top:Math.max(bounds.top,r.top)+'px',width:Math.max(0,Math.min(bounds.right,r.right)-Math.max(bounds.left,r.left))+'px',height:Math.max(0,Math.min(bounds.bottom,r.bottom)-Math.max(bounds.top,r.top))+'px'});if(!loc.block){const used=new Set(stripes.filter(s=>s.annotationId!==a.id&&s.left<r.right&&r.left<s.right&&s.top<r.bottom&&r.top<s.bottom).map(s=>s.lane));let lane=0;while(used.has(lane))lane++;stripes.push({annotationId:a.id,left:r.left,right:r.right,top:r.top,bottom:r.bottom,lane});mark.dataset.lane=String(lane);const line=el('span','annotation-underline');line.style.bottom=-(lane*2)+'px';mark.append(line);}layer.append(mark);});
      const tail=rects[rects.length-1];if(tail.bottom<=bounds.top||tail.top>=bounds.bottom||tail.right<=bounds.left||tail.left>=bounds.right)return;
      const minY=bounds.top+4,maxY=bounds.bottom-26,naturalX=Math.max(bounds.left+5,Math.min(bounds.right-30,tail.right+5)),naturalY=tail.bottom-21,wantedY=Math.min(maxY,Math.max(minY,naturalY));let x=naturalX,y=wantedY;
      // Preserve the horizontal relationship to the selection tail. Moving a
      // colliding bubble left places it over a different word in the selection.
      const available=(cx,cy)=>cy>=minY&&cy<=maxY&&!tails.some(t=>Math.abs(t.x-cx)<28&&Math.abs(t.y-cy)<24)&&!obstacleRects.some(r=>cx<r.right+2&&cx+25>r.left-2&&cy<r.bottom+2&&cy+22>r.top-2);
      if(!available(x,y)){
        const root=loc.block||(loc.parts&&loc.parts[loc.parts.length-1]&&loc.parts[loc.parts.length-1].root),frame=root&&root.closest('.block-frame');
        const gutter=frame?Math.min(bounds.right-30,frame.getBoundingClientRect().right+4):bounds.right-30;
        const columns=Array.from(new Set([naturalX,Math.max(naturalX,gutter),bounds.right-30])).filter(cx=>cx>=bounds.left+5&&cx<=bounds.right-30);
        let found=false;for(let distance=0;distance<=bounds.height;distance+=24){for(const cy of distance?[wantedY+distance,wantedY-distance]:[wantedY]){for(const cx of columns){if(available(cx,cy)){x=cx;y=cy;found=true;break;}}if(found)break;}if(found)break;}if(!found)return;
      }
      tails.push({x,y});
      if(Math.abs(y-naturalY)>1||Math.abs(x-(tail.right+5))>1){
        const sx=Math.max(bounds.left+1,Math.min(bounds.right-1,tail.right+1)),sy=Math.min(bounds.bottom-1,tail.bottom+1),routeY=Math.min(bounds.bottom-1,tail.bottom+4);
        const points=[[sx,sy],[sx,routeY],[x-4,routeY],[x-4,y+11],[x,y+11]];
        points.slice(1).forEach((point,i)=>{const from=points[i],dx=point[0]-from[0],dy=point[1]-from[1];if(Math.hypot(dx,dy)<.5)return;const connector=el('span','annotation-connector');connector.dataset.annotationId=a.id;connector.setAttribute('aria-hidden','true');connector.style.setProperty('--ann-color',color);Object.assign(connector.style,{left:from[0]+'px',top:from[1]+'px',width:Math.hypot(dx,dy)+'px',transform:'rotate('+Math.atan2(dy,dx)+'rad)'});layer.append(connector);});
      }
      const marker=button(String(index+1),'annotation-marker',()=>openAnnotation(a.id));marker.dataset.annotationId=a.id;marker.dataset.anchorX=String(tail.right);marker.dataset.anchorY=String(tail.bottom);marker.style.setProperty('--ann-color',color);marker.style.left=x+'px';marker.style.top=y+'px';marker.title='#'+(index+1)+' · '+(a.user_note||'批注')+' · '+(STATUS_NAMES[a.status]||a.status);marker.setAttribute('aria-label',marker.title);const dot=el('span','status-dot status-'+a.status+(a.unread?' unread':''));marker.append(dot);layer.append(marker);
    });
  }
  function updateTOC(){const top=scroll.getBoundingClientRect().top+70;let active=null;Array.from(content.querySelectorAll('.paper-section')).forEach(s=>{if(s.getBoundingClientRect().top<=top)active=s.dataset.sectionId;});if(!active)active=(sections(state.view)[0]||{}).id;$('toc-list').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.sectionId===active));}
  function selectionChanged(){const anchor=captureSelection();if(!anchor){$('selection-action').hidden=true;return;}state.selectedAnchor=anchor;const rects=anchor.fragments.map(locateFragment).flatMap(annotationRects),tail=rects[rects.length-1];if(!tail)return;const b=$('selection-action');b.hidden=false;b.style.left=Math.min(innerWidth-130,Math.max(8,tail.right+5))+'px';b.style.top=Math.min(innerHeight-50,Math.max(74,tail.bottom+8))+'px';}
  function persistDrafts(){writeLocal('drafts',state.drafts);scheduleOverlay();updateCounts();}
  function newDraft(anchor,quote){const a={id:'draft-'+uid(),paper_id:paperId,anchor,block_id:anchor.content_id,selected_text:quote,user_note:'',status:'draft',revision:0,local:true,created_at:new Date().toISOString(),updated_at:new Date().toISOString(),history:[]};state.drafts.push(a);persistDrafts();window.getSelection().removeAllRanges();$('selection-action').hidden=true;openAnnotation(a.id,true);return a;}
  function newBlockDraft(frame,b){const anchor={kind:'block',view:state.view,language:frame.dataset.language,content_id:b.id,field:'block',content_version:version,start:0,end:0,exact:'',prefix:'',suffix:'',fragments:[]};newDraft(anchor,b['caption_'+(frame.dataset.language==='en'?'en':'zh')]||b.latex||(frame.dataset.language==='zh'&&b.text_zh_latex)||b['text_'+(frame.dataset.language==='en'?'en':'zh')]||'整块图表 / 公式');}
  function showPanel(){const p=$('annotation-panel');p.hidden=false;p.classList.remove('collapsed');$('panel-collapse').textContent='−';}
  function closePanel(){$('annotation-panel').hidden=true;state.selected=null;}
  function updateCounts(){const all=allAnnotations(),unread=all.filter(a=>a.unread).length;$('annotation-count').textContent=all.length;$('unread-count').textContent=unread+' 新';$('unread-count').hidden=!unread;}
  function renderStatus(a){const s=el('span','annotation-status');s.append(el('span','status-dot status-'+a.status),document.createTextNode(STATUS_NAMES[a.status]||a.status||'草稿'));if(a.unread)s.append(el('span','unread-label','新回答'));return s;}
  function beginPanelRender(key,value){
    const body=$('panel-body'),snapshot=JSON.stringify(value),samePanel=renderedPanel.key===key;
    // Polling/SSE may return the same data repeatedly. Keep its DOM, selection
    // and scroll anchor intact; an actual update keeps the current viewport.
    if(samePanel&&renderedPanel.snapshot===snapshot)return null;
    const position={top:samePanel?body.scrollTop:0,left:samePanel?body.scrollLeft:0};
    renderedPanel.key=key;renderedPanel.snapshot=snapshot;body.replaceChildren();return position;
  }
  function finishPanelRender(position){const body=$('panel-body');body.scrollTop=position.top;body.scrollLeft=position.left;}
  function openList(){state.panelMode='list';state.selected=null;showPanel();renderList();}
  function renderList(){const position=beginPanelRender('list:'+state.filter,[state.view,allAnnotations()]);if(!position)return;const body=$('panel-body');$('panel-title').textContent='批注与问答';const tabs=el('div','panel-tabs');[['all','全部'],['unread','新回答'],['pending','待回答'],['unlocated','未定位']].forEach(([key,label])=>tabs.append(button(label,state.filter===key?'active':'',()=>{state.filter=key;renderList();})));body.append(tabs);const list=el('div','annotation-list');body.append(list);
    const items=allAnnotations().filter(a=>state.filter==='all'||(state.filter==='unread'&&a.unread)||(state.filter==='pending'&&a.status!=='answered')||(state.filter==='unlocated'&&(()=>{const anchor=anchorFor(a)||{},view=anchor.view;return anchor.migration_status==='unlocated'||!view||(view===state.view?locateAnnotation(a).status==='unlocated':!sections(view).some(s=>(s.blocks||[]).some(b=>b.id===anchor.content_id)));})()));
    if(!items.length)list.append(el('p','empty-list',state.filter==='all'?'选中正文中的词句，或点击图表旁的铅笔，开始一条批注。':'当前没有此类批注。'));
    items.forEach(a=>{const item=button('','annotation-list-item',()=>openAnnotation(a.id));item.style.setProperty('--ann-color',colorFor(a));const head=el('div','list-item-head');head.append(el('span',null,'#'+annotationNumber(a)+' · '+(VIEW_NAMES[(anchorFor(a)||{}).view]||'历史批注')),renderStatus(a));item.append(head,el('div','list-question',a.user_note||'尚未填写的问题'),el('div','list-quote',a.selected_text||'整块批注'));list.append(item);});updateCounts();finishPanelRender(position);}
  function openAnnotation(id,focus){const a=findAnnotation(id);if(!a)return;state.selected=id;state.panelMode='detail';showPanel();renderDetail(!!focus);if(a.unread)markRead(a);}
  function markdown(parent,text){
    const lines=String(text||'').split('\n');let list=null,code=null,math=null;
    lines.forEach(line=>{
      if(math){const end=line.indexOf(math.close);if(end>=0){math.parts.push(line.slice(0,end));parent.append(mathAtom(math.parts.join('\n'),true,math.open+math.parts.join('\n')+math.close));const rest=line.slice(end+math.close.length);if(rest.trim()){const n=el('p');inline(n,rest);parent.append(n);}math=null;}else math.parts.push(line);return;}
      if(line.startsWith('```')){if(code){code=null;}else{code=el('pre');parent.append(code);}list=null;return;}if(code){code.append(document.createTextNode(line+'\n'));return;}
      const display=/^\s*(\$\$|\\\[)(.*)$/.exec(line);if(display){const close=display[1]==='$$'?'$$':'\\]',end=display[2].indexOf(close);if(end>=0){parent.append(mathAtom(display[2].slice(0,end),true,line.trim()));}else math={open:display[1],close,parts:[display[2]]};list=null;return;}
      const h=/^(#{1,4})\s+(.+)$/.exec(line);if(h){const n=el(h[1].length<3?'h3':'h4');inline(n,h[2]);parent.append(n);list=null;return;}const li=/^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);if(li){if(!list){list=el(/^\s*\d/.test(line)?'ol':'ul');parent.append(list);}const n=el('li');inline(n,li[1]);list.append(n);return;}list=null;if(line.trim()){const n=el('p');inline(n,line);parent.append(n);}else parent.append(document.createTextNode('\n'));
    });
    if(math){const n=el('span','math-error');n.append(el('span','math-error-label','公式渲染错误：显示公式缺少结束定界符'),el('code','math-error-source',math.open+math.parts.join('\n')));parent.append(n);}
  }
  function renderDetail(focus){
    const a=findAnnotation(state.selected);if(!a){openList();return;}const edits=readLocal('edits',{}),followups=readLocal('followups',{}),position=beginPanelRender('detail:'+a.id,[state.view,annotationNumber(a),a,edits[a.id],followups[a.id]]);if(!position){if(focus){const input=$('annotation-note');if(input)input.focus({preventScroll:true});}return;}const body=$('panel-body');$('panel-title').textContent='#'+annotationNumber(a)+' · '+(VIEW_NAMES[(anchorFor(a)||{}).view]||'历史批注');const top=el('div','annotation-tools');top.append(button('← 批注列表','',openList),button('定位原文','',()=>jumpAnnotation(a)));body.append(top,renderStatus(a));
    if(a.selected_text)body.append(el('blockquote','annotation-quote',a.selected_text));const loc=locateAnnotation(a);if(loc.status==='unlocated')body.append(el('div','anchor-warning','未定位：'+loc.reason+'。批注仍然保留，可以编辑或删除。'));if(loc.status==='other-view')body.append(button('前往'+VIEW_NAMES[loc.view]+'中的批注','quiet-button',()=>jumpAnnotation(a)));
    const cached=edits[a.id];const editor=el('div','annotation-editor');const label=el('label',null,a.local?'写下你的问题':'问题（编辑后可重新提交）');const input=el('textarea');input.id='annotation-note';input.value=cached===undefined?(a.user_note||''):cached;label.htmlFor=input.id;editor.append(label,input);const actions=el('div','editor-actions');const saved=el('span','draft-status',a.local?'草稿已自动保存':'修改会自动保存到本地');const submit=button(a.local?'提交并提问':'保存并重新提问','primary-button',async()=>{submit.disabled=true;try{await submitAnnotation(a,input.value);}finally{submit.disabled=false;}});actions.append(saved,submit);editor.append(actions);body.append(editor);
    input.addEventListener('input',()=>{if(a.local){a.user_note=input.value;a.updated_at=new Date().toISOString();persistDrafts();}else{const e=readLocal('edits',{});e[a.id]=input.value;writeLocal('edits',e);}saved.textContent='草稿已自动保存';});
    const tools=el('div','annotation-tools');if(!a.local){tools.append(button('保存修改','',async()=>saveEdit(a,input.value,false)));if(['failed','waiting_bridge'].includes(a.status))tools.append(button('重试答疑','',()=>mutation(a,'retry',{})));if(a.unread)tools.append(button('标为已读','',()=>markRead(a)));}tools.append(button('删除批注','danger-button',()=>deleteAnnotation(a)));body.append(tools);
    const history=el('div','answer-history');let turns=Array.isArray(a.history)?a.history:[];if(!turns.length&&a.answer)turns=[{user_note:a.user_note,answer:a.answer,sources:a.sources,status:a.status}];turns.forEach((turn,i)=>{const t=el('section','answer-turn'),head=el('div','turn-heading');head.append(el('span',null,'问答 '+(i+1)),el('span',null,turn.answered_at?new Date(turn.answered_at).toLocaleString():STATUS_NAMES[turn.status]||''));t.append(head);if(turn.user_note)t.append(el('div','turn-question',turn.user_note));if(turn.answer){const ans=el('div','answer-content');markdown(ans,turn.answer_latex||((a.legacy&&turn.answer===a.legacy.answer&&a.answer_latex)||turn.answer));t.append(ans);if(Array.isArray(turn.sources)&&turn.sources.length){const refs=el('div','answer-sources');turn.sources.forEach(r=>refs.append(sourceButton(r)));t.append(refs);}}else t.append(el('div','pending-answer',pendingText(turn.status||a.status)));history.append(t);});if(!turns.length)history.append(el('div','pending-answer',pendingText(a.status)));body.append(history);
    if(a.answer&&!a.local){const follow=el('div','annotation-editor');const lab=el('label',null,'继续追问');const ta=el('textarea');ta.id='annotation-followup';ta.placeholder='围绕这条回答继续提问…';ta.value=readLocal('followups',{})[a.id]||'';lab.htmlFor=ta.id;ta.addEventListener('input',()=>{const f=readLocal('followups',{});f[a.id]=ta.value;writeLocal('followups',f);});follow.append(lab,ta);const row=el('div','editor-actions');row.append(el('span','draft-status','追问草稿自动保存'),button('提交追问','primary-button',async()=>{if(!ta.value.trim()){toast('请先填写追问。');return;}const ok=await mutation(a,'followups',{user_note:ta.value,client_request_id:uid()});if(ok){const f=readLocal('followups',{});delete f[a.id];writeLocal('followups',f);renderDetail();}}));follow.append(row);body.append(follow);}
    typeset(body);finishPanelRender(position);if(focus)setTimeout(()=>input.focus({preventScroll:true}),0);
  }
  function pendingText(status){return status==='draft'?'点击提交，答疑助手会自动处理这条问题。':status==='waiting_bridge'?'问题已保存。答疑助手连接后会继续处理。':status==='failed'?'这次回答未完成。可点击重试。':status==='running'?'答疑助手正在阅读相关内容并准备回答。':'问题已进入队列，回答到达时会提示你。';}
  function jumpAnnotation(a){const anchor=anchorFor(a);if(!anchor){toast('历史批注缺少定位信息。');return;}if(anchor.view!==state.view)switchView(anchor.view);requestAnimationFrame(()=>{const loc=locateAnnotation(a);if(loc.status!=='located'){toast('未能唯一定位原文：'+(loc.reason||'内容缺失'));if(state.selected===a.id)renderDetail();return;}const n=loc.block||(loc.parts&&loc.parts[0]&&loc.parts[0].root);if(n){(loc.parts||[]).forEach(part=>{if(part.root)revealField(part.root);});revealField(n);n.scrollIntoView({block:'center'});}scheduleOverlay();});}
  async function api(path,method,body){if(!state.token)throw new Error('本地答疑服务尚未连接，草稿已保存在浏览器。');const r=await fetch(path,{method:method||'GET',headers:Object.assign({'X-Reader-Token':state.token},body===undefined?{}:{'Content-Type':'application/json'}),body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});let data={};try{data=await r.json();}catch(_){}if(!r.ok){const e=new Error(r.status===409?'批注已在别处更新。你的编辑草稿仍在本地，请查看最新回答后再提交。':(data.error||data.message||('请求失败 ('+r.status+')')));e.status=r.status;e.data=data;throw e;}return data;}
  const base='/api/papers/'+encodeURIComponent(paperId)+'/annotations';
  function mergeAnnotation(raw,notify){const a=normalizeAnnotation(raw),old=state.annotations.find(x=>x.id===a.id);if(raw.deleted){state.annotations=state.annotations.filter(x=>x.id!==a.id);}else if(!old||Number(a.revision||0)>=Number(old.revision||0)){state.annotations=state.annotations.filter(x=>x.id!==a.id).concat(a);if(notify&&a.unread&&a.answer&&(!old||a.answer!==old.answer)&&!state.notified.has(a.id+':'+a.revision)){state.notified.add(a.id+':'+a.revision);toast('新回答已到达 · 批注 #'+annotationNumber(a));}}writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();if(!$('annotation-panel').hidden){if(state.panelMode==='list')renderList();else if(state.selected===a.id&&!$('panel-body').contains(document.activeElement))renderDetail();}}
  async function submitAnnotation(a,note){if(!note.trim()){toast('请先写下你的问题。');return;}if(!a.local)return saveEdit(a,note,true);a.user_note=note;persistDrafts();try{const data=await api(base,'POST',{id:a.id.replace(/^draft-/,''),block_id:a.block_id,anchor:a.anchor,identity_color:colorFor(a),selected_text:a.selected_text,user_note:note,submit:true,client_request_id:a.id});mergeAnnotation(data.annotation,false);state.drafts=state.drafts.filter(x=>x.id!==a.id);persistDrafts();openAnnotation(data.annotation.id);toast('问题已提交，回答到达时会提示。');}catch(e){toast(e.message);}}
  async function saveEdit(a,note,submit){if(!note.trim()){toast('请先写下你的问题。');return false;}try{const data=await api(base+'/'+encodeURIComponent(a.id),'PATCH',{revision:a.revision,user_note:note,submit:!!submit});mergeAnnotation(data.annotation,false);const e=readLocal('edits',{});delete e[a.id];writeLocal('edits',e);renderDetail();toast(submit?'修改已保存并进入答疑队列。':'修改已保存。');return true;}catch(e){if(e.status===409&&e.data.current){mergeAnnotation(e.data.current,false);renderDetail();}toast(e.message,true);return false;}}
  async function mutation(a,action,payload){try{const data=await api(base+'/'+encodeURIComponent(a.id)+'/'+action,'POST',Object.assign({revision:a.revision},payload));if(data.annotation)mergeAnnotation(data.annotation,false);renderDetail();return true;}catch(e){if(e.status===409&&e.data.current){mergeAnnotation(e.data.current,false);renderDetail();}toast(e.message);return false;}}
  async function markRead(a){if(!a.unread)return;await mutation(a,'read',{});}
  async function deleteAnnotation(a){if(a.local){state.drafts=state.drafts.filter(x=>x.id!==a.id);persistDrafts();openList();toast('草稿已删除。');return;}try{await api(base+'/'+encodeURIComponent(a.id),'DELETE',{revision:a.revision});state.annotations=state.annotations.filter(x=>x.id!==a.id);writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();openList();toast('批注已删除。');}catch(e){toast(e.message);}}
  function connection(label,offline){$('connection-status').textContent=label;$('connection-status').classList.toggle('offline',!!offline);}
  async function poll(){if(state.polling||state.stopped)return;state.polling=true;try{if(!state.token){const r=await fetch('/api/session?paper_id='+encodeURIComponent(paperId),{cache:'no-store'});if(!r.ok)throw new Error('服务不可用');const session=await r.json();if(session.paper_id&&session.paper_id!==paperId)throw new Error('论文服务不匹配');state.token=session.token;connectEvents();}const data=await api(base);if(Array.isArray(data.annotations)){const incoming=data.annotations.map(normalizeAnnotation);const previous=state.annotations;incoming.forEach(a=>{const old=previous.find(x=>x.id===a.id);if(a.unread&&a.answer&&old&&a.answer!==old.answer&&!state.notified.has(a.id+':'+a.revision)){state.notified.add(a.id+':'+a.revision);toast('新回答已到达 · 批注 #'+annotationNumber(a));}});state.annotations=incoming;writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();if(!$('annotation-panel').hidden&&!$('panel-body').contains(document.activeElement)){if(state.panelMode==='list')renderList();else renderDetail();}}state.online=true;const status=await api('/api/status');connection(status.bridge_available?'已连接 · 答疑助手在线':'已连接 · 等待答疑助手',!status.bridge_available);}catch(_){state.online=false;connection('离线 · 草稿仍自动保存在本地',true);}finally{state.polling=false;}}
  function connectEvents(){if(typeof EventSource==='undefined'||state.events)return;const es=new EventSource('/api/events?token='+encodeURIComponent(state.token)+'&paper_id='+encodeURIComponent(paperId));state.events=es;es.addEventListener('annotation',event=>{try{const data=JSON.parse(event.data);if(!data.paper_id||data.paper_id===paperId){if(data.annotation)mergeAnnotation(data.annotation,true);else poll();}}catch(_){}});es.addEventListener('status',()=>poll());es.onerror=()=>connection('连接恢复中 · 草稿自动保存',true);}
  if(!D||Number(D.schema_version)!==3){$('paper-heading').append(el('h1',null,'缺少 v3 论文数据'));content.append(el('p','empty-view','请使用完整的 schema_version: 3 paper-data.js。旧版论文继续使用原 reader.html。'));return;}
  state.positions=readLocal('positions',{});state.drafts=readLocal('drafts',[]).map(normalizeAnnotation);state.annotations=readLocal('cache',[]).map(normalizeAnnotation);const savedView=readLocal('view','overview');if(VIEW_NAMES[savedView])state.view=savedView;
  document.querySelectorAll('.view-tabs [data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
  $('annotations-toggle').addEventListener('click',()=>{if(!$('annotation-panel').hidden&&state.panelMode==='list')closePanel();else openList();});$('panel-close').addEventListener('click',closePanel);$('panel-collapse').addEventListener('click',()=>{const collapsed=$('annotation-panel').classList.toggle('collapsed');$('panel-collapse').textContent=collapsed?'＋':'−';$('panel-collapse').setAttribute('aria-label',collapsed?'展开问答':'折叠问答');});
  $('toc-toggle').addEventListener('click',()=>{document.body.classList.toggle(innerWidth<=760?'toc-visible':'toc-hidden');scheduleOverlay();});
  $('selection-action').addEventListener('mousedown',e=>e.preventDefault());$('selection-action').addEventListener('click',()=>{if(state.selectedAnchor)newDraft(state.selectedAnchor,state.selectedAnchor.fragments.map(f=>f.exact).join('\n'));});
  content.addEventListener('mouseup',()=>setTimeout(selectionChanged,0));document.addEventListener('selectionchange',()=>{clearTimeout(selectionChanged.timer);selectionChanged.timer=setTimeout(selectionChanged,90);});document.addEventListener('keydown',e=>{if(e.key==='Escape'){closePanel();$('selection-action').hidden=true;document.body.classList.remove('toc-visible');}});
  scroll.addEventListener('scroll',()=>{scheduleOverlay();$('selection-action').hidden=true;clearTimeout(savePosition.timer);savePosition.timer=setTimeout(savePosition,180);},{passive:true});window.addEventListener('resize',scheduleOverlay);window.addEventListener('beforeunload',savePosition);
  if(window.ResizeObserver)new ResizeObserver(scheduleOverlay).observe(content);if(document.fonts&&document.fonts.ready)document.fonts.ready.then(scheduleOverlay);
  renderPaper();scroll.scrollTop=(state.positions[state.view]||{}).top||0;updateCounts();poll();const pollingTimer=setInterval(poll,5000);
  $('stop-service').addEventListener('click',async()=>{if(state.stopped)return;const b=$('stop-service');b.disabled=true;state.stopped=true;try{await api('/api/stop','POST',{});clearInterval(pollingTimer);if(state.events)state.events.close();state.online=false;connection('后台服务已停止 · 重新打开启动器继续',true);toast('后台服务已停止，问题和历史答案已保留。');}catch(e){state.stopped=false;b.disabled=false;toast(e.message);}});
  // Expose deterministic primitives for browser regression tests and anchor inspection.
  window.ReaderV3=Object.freeze({canonicalIndex,rangeFor,captureSelection,locateFragment,locateAnnotation,switchView,allAnnotations,newDraft,openAnnotation,openList,poll,drawAnnotations,getState:()=>state});
})();
