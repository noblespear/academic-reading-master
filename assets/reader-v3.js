/* Academic Reading Master v3. User/model text is rendered only through DOM text nodes.
 * Canonical offsets count UTF-16 text and math source once, never math renderer hidden DOM.
 */
(function () {
  'use strict';
  let D = window.PAPER_DATA;
  const preview = window.READER_PREVIEW === true;
  const bodyFirst = String(D && D.content_contract_version || '') === '3.2';
  const previewMemory = Object.create(null);
  const $ = id => document.getElementById(id);
  const VIEW_NAMES = {overview:'概览',explanation:'详解',translation:'翻译'};
  const STATUS_NAMES = {unknown:'结果待确认',cancelled:'已取消',interrupted:'已中断',draft:'草稿',queued:'排队中',running:'回答中',answered:'已回答',failed:'回答失败',waiting_bridge:'等待答疑助手'};
  const COLORS = ['#497da7','#8b6daa','#af8a49','#528d83','#a56f84','#647eaf'];
  const scroll = $('reading-scroll'), content = $('paper-content'), layer = $('annotation-layer');
  const state = {view:'overview',positions:{},annotations:[],drafts:[],token:null,online:false,selected:null,selectedAnchor:null,panelMode:'list',filter:'all',polling:false,renderQueued:false,notified:new Set()};
  const renderedPanel = {key:null,snapshot:null};
  const translationJob = {info:null,pending:false,timer:null,loading:false};
  const paperId = String(D && D.id || 'paper'), version = D && (D.content_version || 1);
  const storagePrefix = 'arm_v3_' + paperId + '_';
  const colorCache = readLocal('colors', {});
  function readLocal(key, fallback) { if(preview)return Object.prototype.hasOwnProperty.call(previewMemory,key)?previewMemory[key]:fallback;try { const raw=localStorage.getItem(storagePrefix + key); return raw===null ? fallback : JSON.parse(raw); } catch (_) { return fallback; } }
  function writeLocal(key, value) { if(preview){previewMemory[key]=value;return true;}try { localStorage.setItem(storagePrefix + key, JSON.stringify(value)); if(['drafts','edits','followups','rich_edits','followup_rich','study_state','plain_language'].includes(key))scheduleDraftSync(); return true; } catch (_) { return false; } }
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
  function actualBodyBlocks(blocks){return blocks.flatMap(b=>[b,...(bodyFirst&&['details','quote'].includes(b.type)?actualBodyBlocks(b.blocks||[]):[])]);}
  function sourceView(id){return ['translation','explanation','overview'].find(v=>sections(v).some(s=>s.id===id||actualBodyBlocks(s.blocks||[]).some(b=>b.id===id)));}
  function sourceButton(ref){const label=ref.label||((String(ref.url||'').trim()?'外部来源':'原文')+(ref.page?' · p.'+ref.page:''));return button(label,'source-link',()=>jumpSource(ref));}
  function sourceLinks(refs){
    const box=el('details','block-sources source-details');
    const pages=[...new Set(refs.filter(r=>!String(r.url||'').trim()).map(r=>r.page).filter(Boolean))];
    box.append(el('summary',null,'来源'+(pages.length?' · p.'+pages.join('、'):'')));
    const links=el('div','source-detail-links');refs.forEach(ref=>links.append(sourceButton(ref)));box.append(links);
    box.addEventListener('toggle',scheduleOverlay);return box;
  }
  function jumpSource(ref){
    if(String(ref.url||'').trim()){let url=null;try{if(typeof ref.url==='string'&&/^https?:\/\//i.test(ref.url.trim())){const parsed=new URL(ref.url.trim());if(['http:','https:'].includes(parsed.protocol))url=parsed.href;}}catch(_){}if(url)window.open(url,'_blank','noopener,noreferrer');else toast('这条外部来源的网址无效。');return;}
    const id=ref.block_id||ref.content_id||ref.section_id;
    if(id){const wanted=sourceView(id);if(wanted){switchView(wanted);requestAnimationFrame(()=>{const target=Array.from(content.querySelectorAll('[data-content-id],[data-section-id],[data-rich-id]')).find(n=>n.dataset.contentId===id||n.dataset.sectionId===id||n.dataset.richId===id);if(target){revealField(target);target.scrollIntoView({block:'center'});target.classList.add('jump-flash');setTimeout(()=>target.classList.remove('jump-flash'),1600);scheduleOverlay();}});return;}}
    if(ref.page){window.open('source.pdf#page='+encodeURIComponent(ref.page),'_blank','noopener');return;}
    toast('这条来源没有可跳转的位置。');
  }
  function inlineLink(value){
    if(typeof value!=='string')return null;const href=value.trim();
    if(href.startsWith('#')){
      let id;try{id=decodeURIComponent(href.slice(1));}catch(_){return null;}
      if(!id||!sourceView(id))return null;
      const link=el('a');link.href=href;link.dataset.sourceContentId=id;
      link.addEventListener('click',event=>{event.preventDefault();jumpSource({content_id:id});});return link;
    }
    const url=safeURL(href,false);if(!url)return null;
    const link=el('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';return link;
  }
  // Minimal safe inline markdown. No HTML parser, raw HTML remains visible as text.
  function literal(parent,text){if(!text)return;const broken=/\\[()[\]]|(?<!\\)\$(?!\d+(?:[.,]\d+)?(?:\s|[.,;:!?)]|$))/.test(text);if(broken){const n=el('span','math-error');n.append(el('span','math-error-label','公式渲染错误：数学分隔符未闭合'),el('code','math-error-source',text));n.setAttribute('role','alert');parent.append(n);}else parent.append(document.createTextNode(text));}
  function inline(parent, text) {
    const s=String(text==null?'':text);
    // Mask explicit code/math before looking for Markdown. Offsets stay in the
    // original string, and recursive formatting can still contain these atoms.
    const tokens=[],atoms=/`([^`\n]+)`|\$\$([\s\S]+?)\$\$|\$([^$\n]+)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g;
    const masked=s.replace(atoms,(raw,code,display,dollar,paren,bracket,index)=>{
      tokens.push({index,end:index+raw.length,raw,code,latex:display||dollar||paren||bracket||'',display:!!(display||bracket),dollar});
      return '\0'.repeat(raw.length);
    });
    // Preserve markers attached to Latin/Greek identifiers (S*, map*, S*max),
    // while retaining ordinary Chinese prose such as 这里*重点*文字.
    const re=/\*\*([^*\n]+)\*\*|(?<![\p{Script=Latin}\p{Script=Greek}\p{N}_*\\])\*([^\s*\n](?:[^*\n]*[^\s*\n])?)\*(?![\p{Script=Latin}\p{Script=Greek}\p{N}_*])|\[([^\]\n]+)\]\(([^)\s]+)\)/gu;
    let last=0,m;
    while((m=re.exec(masked))){const kind=m[1]?'strong':m[2]?'em':'link',padding=kind==='strong'?2:1;tokens.push({index:m.index,end:re.lastIndex,kind,text:s.slice(m.index+padding,m.index+padding+(m[1]||m[2]||m[3]).length),href:kind==='link'?s.slice(m.index+m[3].length+3,re.lastIndex-1):undefined});}
    tokens.sort((a,b)=>a.index-b.index);
    tokens.forEach(token=>{
      if(token.index<last)return;
      if(token.index>last)literal(parent,s.slice(last,token.index));
      let node;
      if(token.kind){node=token.kind==='link'?(inlineLink(token.href)||el('span')):el(token.kind);inline(node,token.text);}
      else if(token.code!==undefined)node=el('code',null,token.code);
      else{const suspicious=token.dollar&&(s[token.index-1]==='\\'||/^\s|\s$/.test(token.latex)||/^(?:[A-Za-z_][\w.-]*[\\/]|[A-Za-z]:[\\/])/.test(token.latex));node=suspicious?document.createTextNode(token.raw):mathAtom(token.latex,token.display,token.raw);}
      parent.append(node);last=token.end;
    });
    if(last<s.length)literal(parent,s.slice(last));
  }
  function mathAtom(latex, display, canonical){const n=el('span','math-atom');n.dataset.canonical=canonical===undefined?String(latex):canonical;n.dataset.latex=String(latex);n.dataset.display=String(!!display);const render=el('span','math-render');n.append(render);return n;}
  function proseInline(parent,text){
    const parsed=el('span');inline(parsed,text);
    let paragraph=el('span','prose-paragraph');parent.append(paragraph);
    Array.from(parsed.childNodes).forEach(node=>{
      if(node.nodeType!==3){paragraph.append(node);return;}
      node.nodeValue.split(/(\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*)/).forEach((part,i)=>{
        if(i%2){parent.append(document.createTextNode(part));paragraph=el('span','prose-paragraph');parent.append(paragraph);}
        else if(part)paragraph.append(document.createTextNode(part));
      });
    });
  }
  function field(parent, text, id, name, language, cls){const f=el('div','anchor-field'+(cls?' '+cls:''));Object.assign(f.dataset,{contentId:id,field:name,language,view:state.view,contentVersion:String(version)});if(language==='zh'&&f.classList.contains('prose-field'))proseInline(f,text);else inline(f,text);parent.append(f);return f;}
  function displayField(data, names){for(const name of names){if(typeof data[name+'_latex']==='string')return{name:name+'_latex',text:data[name+'_latex']};if(data[name]!==undefined&&data[name]!==null&&String(data[name]))return{name,text:String(data[name])};}return null;}
  function captionField(b,lang){return displayField(b,lang==='en'?['caption_en','fig_caption_en']:['caption_zh','fig_caption_zh','caption']);}
  function appendFigure(frame,b,lang){const caption=captionField(b,lang),url=safeURL(b.src||b.asset,true)||(b.type==='diagram'?safeSVG(b.svg):null);if(url){const img=el('img','figure-image');img.src=url;img.alt=b.alt||(caption?caption.text:'论文图示');img.loading='lazy';img.addEventListener('load',scheduleOverlay);img.addEventListener('error',()=>{img.replaceWith(el('div','asset-placeholder','图示资源无法加载'));scheduleOverlay();});frame.append(renderer.imageZoom(img));}else frame.append(el('div','asset-placeholder','图示暂无独立资源。请查看原文 PDF。'));if(caption)field(frame,caption.text,b.id,caption.name,lang,'block-caption');return caption;}
  function legacyTable(frame,b,lang){
    if(typeof b.table_html!=='string'||b.table_html.length>1000000)return false;
    // Parse inertly, then build a new table from structural and inline whitelists.
    // No source node, attribute, or HTML string is inserted into the live document.
    const parsed=new DOMParser().parseFromString(b.table_html,'text/html'),source=parsed.querySelector('table');if(!source)return false;
    const table=el('table','data-table legacy-data-table'),forbidden=new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','IMG','VIDEO','AUDIO','SVG','MATH','INPUT','BUTTON','FORM','LINK','META']),tags=new Set(['B','STRONG','I','EM','U','SUB','SUP','BR','A','CODE']);
    function copyInline(target,node){if(node.nodeType===3){inline(target,node.nodeValue);return;}if(node.nodeType!==1||forbidden.has(node.tagName))return;let next=target;if(tags.has(node.tagName)){if(node.tagName==='A'){const link=inlineLink(node.getAttribute('href'));if(link){next=link;target.append(next);}}else{next=el(node.tagName.toLowerCase());target.append(next);}}Array.from(node.childNodes).forEach(child=>copyInline(next,child));}
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
  function revealField(root){let parent=root;while(parent){if(parent.tagName==='DETAILS')parent.open=true;if(parent.classList.contains('ai-supplement')&&parent.hidden)setPlainLanguage(true);parent=parent.parentElement;}scheduleOverlay();}
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
  function paperRenderContext() {return {showSourceLinks:state.view==='translation',figureNotes:state.view!=='translation'};}
  function renderLegacyBlock(b,lang,context){
    const frame=el('div','block-frame language-'+lang);Object.assign(frame.dataset,{contentId:b.id,language:lang,view:state.view});
    frame.append(button('✎','block-question',()=>newBlockDraft(frame,b)));
    frame.lastChild.setAttribute('aria-label','对整块内容提问');
    const suffix=lang==='en'?'en':'zh', bodyField=lang==='zh'&&typeof b.text_zh_latex==='string'?'text_zh_latex':'text_'+suffix, body=b[bodyField] || (lang==='en'&&!(b.type==='formula'&&String(b.latex||'').trim())?'原文暂缺':'');
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
      if(body)field(frame,body,b.id,bodyField,lang,lang==='zh'?'prose-field':undefined);
    }else if(b.type==='table'){
      const rows=b['rows_'+suffix]||[];if(rows.length){const table=el('table','data-table');rows.forEach((row,i)=>{const tr=el('tr');(Array.isArray(row)?row:[row]).forEach((cell,j)=>{const td=el(i===0?'th':'td');field(td,cell,b.id,'rows_'+suffix+'.'+i+'.'+j,lang);tr.append(td);});table.append(tr);});frame.append(table);}else if(b.src||b.asset)appendFigure(frame,b,lang);else if(!legacyTable(frame,b,lang))frame.append(el('div','asset-placeholder','表格暂无结构化内容。请查看原文 PDF。'));
      const caption=captionField(b,lang);if(caption&&!(b.src||b.asset))field(frame,caption.text,b.id,caption.name,lang,'block-caption');if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='list'){
      const list=el(b.ordered?'ol':'ul','original-list');(b.items||[]).forEach((item,i)=>{const value=typeof item==='object'&&item!==null?displayField(item,['text_'+suffix]):{name:'text_'+suffix,text:String(item)};if(!value)return;const li=el('li');field(li,value.text,b.id,'items.'+i+'.'+value.name,lang);list.append(li);});frame.append(list);if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else if(b.type==='algorithm'){
      frame.classList.add('algorithm-card');const title=displayField(b,['algo_title']);if(title)field(frame,title.text,b.id,title.name,lang,'algorithm-title');const name=Array.isArray(b.algo_lines_latex)?'algo_lines_latex':'algo_lines',lines=el('div','algorithm-lines');(b[name]||[]).forEach((line,i)=>field(lines,line,b.id,name+'.'+i,lang,'algorithm-line'));frame.append(lines);if(b[bodyField])field(frame,body,b.id,bodyField,lang);
    }else field(frame,body,b.id,bodyField,lang,['paragraph','para'].includes(b.type)&&lang==='zh'?'prose-field':undefined);
    originalExplanations(frame,b,lang);
    if((!context||context.showSourceLinks!==false)&&(b.source_refs||[]).length)frame.append(sourceLinks(b.source_refs));
    return frame;
  }
  state.studyState = readLocal('study_state', {});
  const studyStateAtOpen = Object.assign({},state.studyState);
  const renderer = window.ContentRenderer.create({
    el, button, inline, proseInline, mathAtom, field, safeURL, safeSVG, typeset,
    onLayout:scheduleOverlay, renderLegacy:renderLegacyBlock, sourceButton,sourceLinks,legacyTable,
    catalog:D && D.learning_catalog || {concepts:[],aids:[]},
    studyState:state.studyState,
    onStudyChange:() => writeLocal('study_state',state.studyState)
  });
  function renderBlock(b, lang, previousBlock) {
    const context=Object.assign(paperRenderContext(),{language:lang,legacy:true,anchorId:b.id,previousBlock,scope:'paper/'+state.view+'/'+b.id});
    const frame=renderer.renderBlock(b,context);
    frame.classList.add(['figure','diagram','table','learning_aid'].includes(b.type)?'content-visual':'content-prose');
    if(!frame.classList.contains('block-frame')) {
      frame.classList.add('block-frame','language-'+lang);
      Object.assign(frame.dataset,{contentId:b.id,language:lang,view:state.view});
      const ask=button('✎','block-question',()=>newBlockDraft(frame,b));ask.setAttribute('aria-label','对整块内容提问');frame.prepend(ask);
    }
    if(lang==='zh' && state.view!=='translation') {
      const card=!bodyFirst&&b.learning?appendLearning(frame,b,context):null;
      if(bodyFirst&&b.learning)frame.append(el('p','content-warning','此3.2内容含旧补充层，请将必要讲解并入正文后重新校验。'));
      // Only an explicit top-level placement makes an aid part of the paper
      // explanation. Nested learning blocks always stay on their AI surface.
      if(b.type==='learning_aid'&&(bodyFirst||b.placement==='body'))frame.dataset.learningPlacement='body';
      if(!bodyFirst&&b.type==='learning_aid'&&b.placement!=='body') {
        const surface=card||supplementSurface(b.id);
        Array.from(frame.children).filter(node=>node!==surface&&!node.classList.contains('block-question')).forEach(node=>surface.append(node));
        if(!card)frame.append(surface);
      }
    }
    return frame;
  }
  function learningText(parent,value,b,path,cls) {
    if(value===undefined || value===null || value==='')return;
    if(Array.isArray(value)) { value.forEach((v,i)=>learningText(parent,v,b,path+'.'+i,cls));return; }
    field(parent,typeof value==='object'?JSON.stringify(value):String(value),b.id,path,'zh',cls);
  }
  function learningHeading(parent,title) { parent.append(el('h4','learning-heading',title)); }
  function teachingExample(parent,example,b,path) {
    if(!example)return;
    const box=el('section','teaching-example');box.append(el('span','teaching-label','教学示例'));
    learningText(box,example.input,b,path+'.input');
    (example.steps||[]).forEach((step,i)=>{
      if(typeof step==='string')learningText(box,step,b,path+'.steps.'+i);
      else {learningText(box,step.text,b,path+'.steps.'+i+'.text');if(step.latex){const f=field(box,'',b.id,path+'.steps.'+i+'.latex','zh','formula-display');f.append(mathAtom(step.latex,true,step.latex));}}
    });
    learningText(box,example.result,b,path+'.result','example-result');learningText(box,example.interpretation,b,path+'.interpretation');parent.append(box);
  }
  function formulaExplanation(parent,formula,b,index) {
    const path='learning.formula_explanations.'+index,box=el('section','formula-explanation');learningHeading(box,'公式讲解');
    const reference=formula.formula_ref || formula.source_formula_id,source=['overview','explanation','translation'].flatMap(v=>sections(v).flatMap(s=>s.blocks||[])).find(x=>x.id===reference);
    const latex=formula.latex || source && source.latex;
    if(latex){const f=field(box,'',b.id,path+'.latex','zh','formula-display');f.append(mathAtom(latex,true,latex));}
    learningText(box,formula.purpose,b,path+'.purpose');
    const table=el('table','data-table symbol-table');(formula.symbols||[]).forEach((symbol,i)=>{const row=el('tr'),symbolCell=el('th'),meaning=el('td'),symbolField=field(symbolCell,'',b.id,path+'.symbols.'+i+'.symbol','zh');symbolField.append(mathAtom(symbol.symbol,false,symbol.symbol));learningText(meaning,symbol.meaning,b,path+'.symbols.'+i+'.meaning');['unit','shape','range'].forEach(key=>learningText(meaning,symbol[key],b,path+'.symbols.'+i+'.'+key,'symbol-detail'));row.append(symbolCell,meaning);table.append(row);});box.append(table);
    (formula.operations||[]).forEach((op,i)=>{learningText(box,typeof op==='string'?op:op.operator,b,path+'.operations.'+i+(typeof op==='string'?'':'.operator'),'operation-name');if(op.meaning)learningText(box,op.meaning,b,path+'.operations.'+i+'.meaning');});
    const steps=el('ol','formula-steps');(formula.steps||[]).forEach((step,i)=>{const item=el('li');learningText(item,typeof step==='string'?step:step.text,b,path+'.steps.'+i+(typeof step==='string'?'':'.text'));if(step.latex){const f=field(item,'',b.id,path+'.steps.'+i+'.latex','zh','formula-display');f.append(mathAtom(step.latex,true,step.latex));}learningText(item,step.condition,b,path+'.steps.'+i+'.condition');steps.append(item);});box.append(steps);
    teachingExample(box,formula.example,b,path+'.example');
    [['variation','改变条件会怎样'],['boundary','适用条件'],['paper_connection','回到论文']].forEach(([key,label])=>{if(formula[key]){learningHeading(box,label);learningText(box,formula[key],b,path+'.'+key);}});
    if(paperRenderContext().showSourceLinks&&(formula.source_refs||[]).length)box.append(sourceLinks(formula.source_refs));parent.append(box);
  }
  function supplementSurface(id) {const card=el('section','ai-supplement');card.hidden=!state.plainLanguage;card.dataset.learningId=id;card.append(el('h3','ai-supplement-title','AI说人话'));return card;}
  function appendLearning(frame,b,context) {
    const learning=b.learning,card=supplementSurface(learning.id || b.id);
    const narrative=learning.presentation==='narrative';
    if(!narrative){
    learningText(card,learning.plain_summary,b,'learning.plain_summary','plain-summary');
    if((learning.concept_ids||[]).length){learningHeading(card,'词语与背景');learning.concept_ids.forEach((id,i)=>{
      const concept=(D.learning_catalog&&D.learning_catalog.concepts||[]).find(c=>c.id===id);if(!concept){card.append(el('p','content-warning','词语说明不存在：'+id));return;}
      const row=el('section','concept-card');row.dataset.conceptId=id;
      ['label','context_meaning','plain_definition','example'].forEach(key=>learningText(row,concept[key],b,'learning.concept_ids.'+i+'.'+key,key==='label'?'concept-label':null));card.append(row);
    });}
    (learning.formula_explanations||[]).forEach((formula,i)=>formulaExplanation(card,formula,b,i));
    if(learning.analogy){learningHeading(card,'生活类比');learningText(card,learning.analogy.text,b,'learning.analogy.text');(learning.analogy.mappings||[]).forEach((mapping,i)=>{const row=el('div','analogy-mapping');learningText(row,mapping.everyday,b,'learning.analogy.mappings.'+i+'.everyday');row.append(el('span',null,'↔'));learningText(row,mapping.paper,b,'learning.analogy.mappings.'+i+'.paper');card.append(row);});learningText(card,learning.analogy.boundary,b,'learning.analogy.boundary','analogy-boundary');}
    if(learning.example){learningHeading(card,'具体例子');teachingExample(card,learning.example,b,'learning.example');}
    }
    if(Array.isArray(learning.blocks))learning.blocks.forEach((child,i)=>{
      const scope=child.type==='learning_aid'&&child.id?'paper/'+state.view+'/'+child.id:context.scope+'/'+(child.id||i);
      // A moved component keeps its original paper scope. New selections use
      // the parent learning path; old anchors are resolved by its stable ID.
      const node=renderer.renderBlock(child,Object.assign({},context,{legacy:false,path:'learning.blocks.'+i+'.',scope}));
      if(child.type==='learning_aid'&&child.id)node.dataset.legacyContentId=child.id;
      card.append(node);
    });
    (learning.aid_ids||[]).forEach((id,i)=>{const aid=(D.learning_catalog&&D.learning_catalog.aids||[]).find(a=>a.id===id);if(!aid){card.append(el('p','content-warning','学习演示不存在：'+id));return;}card.append(window.LearningComponents.render(aid,{el,button,inline,mathAtom,typeset,onLayout:scheduleOverlay,catalog:D.learning_catalog,studyState:state.studyState,onStudyChange:()=>writeLocal('study_state',state.studyState),context:Object.assign({},context,{scope:context.scope+'/learning'}),text:(p,v,path,cls)=>learningText(p,v,b,'learning.aid_ids.'+i+'.'+path,cls),renderDocument:(p,d,c)=>renderer.renderDocument(p,d,Object.assign({},context,c,{legacy:false}))}));});
    frame.append(card);return card;
  }
  function setPlainLanguage(enabled) {
    const reference=Array.from(content.querySelectorAll('.block-frame')).find(n=>n.getBoundingClientRect().bottom>scroll.getBoundingClientRect().top+20);
    const offset=reference && reference.getBoundingClientRect().top;state.plainLanguage=!!enabled;writeLocal('plain_language',state.plainLanguage);
    $('plain-language-toggle').setAttribute('aria-checked',String(state.plainLanguage));content.querySelectorAll('.ai-supplement,.learning-unavailable').forEach(card=>card.hidden=!state.plainLanguage);
    if(reference)scroll.scrollTop+=reference.getBoundingClientRect().top-offset;typeset(content);scheduleOverlay();
  }
  function renderPaper(){
    $('paper-short-title').textContent=D.title_cn||D.title_en||paperId;
    const h=$('paper-heading');h.replaceChildren(el('div','paper-kicker',(D.venue||'RESEARCH PAPER')+(D.year?' · '+D.year:'')));h.append(el('h1',null,D.title_cn||D.title_en||paperId));if(D.title_en&&D.title_cn)h.append(el('p','paper-title-en',D.title_en));const authors=Array.isArray(D.authors)?D.authors.map(x=>typeof x==='string'?x:(x.name||'')).join(' · '):D.authors||'';h.append(el('div','paper-meta',authors+(D.level==='novice'?'　/　新手阅读':'　/　论文伴读')));
    renderView();
  }
  function renderView(){
    clearTimeout(translationJob.timer);
    content.replaceChildren();$('toc-list').replaceChildren();$('toc-view-label').textContent=VIEW_NAMES[state.view]+'目录';$('section-count').textContent=sections(state.view).length+' 节';
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.view===state.view)));
    scroll.querySelector('.paper-width').classList.toggle('translation-width',state.view==='translation');
    $('plain-language-toggle').hidden=bodyFirst||state.view==='translation';
    $('plain-language-toggle').setAttribute('aria-checked',String(!!state.plainLanguage));
    const intro=$('view-intro');intro.replaceChildren();if(state.view==='translation'){const heads=el('div','language-headings');heads.append(el('span',null,'English · 原文'),el('span',null,'中文 · 译文'));intro.append(heads);}else intro.append(el('span','view-description',state.view==='overview'?'问题背景、方法框架、创新点、实验与效果、开销与论文边界。':'沿论文主要章节展开方法、数学关系与证据。'));
    if(state.view==='overview'){const blocks=(D.overview||{}).pipeline_blocks||D.pipeline_blocks;if(Array.isArray(blocks)&&blocks.length){intro.classList.add('with-pipeline-map');const map=el('nav','pipeline-map');map.setAttribute('aria-label','方法流水线');blocks.forEach((item,i)=>{if(i)map.append(el('span','pipeline-map-arrow','→'));const link=button('','pipeline-map-node',()=>{const target=Array.from(content.querySelectorAll('.block-row')).find(n=>n.dataset.blockId===item.content_id);if(target){target.scrollIntoView({block:'start'});target.classList.add('jump-flash');setTimeout(()=>target.classList.remove('jump-flash'),1600);scheduleOverlay();}});link.append(el('span','pipeline-map-step',item.step===undefined?i+1:item.step),el('span',null,item.name||item.content_id));map.append(link);});intro.append(map);}}else intro.classList.remove('with-pipeline-map');
    sections(state.view).forEach((s,i)=>{
      const section=el('section','paper-section');section.dataset.sectionId=s.id;const heading=el('div','section-heading');heading.append(el('h2',null,s.title_cn||s.title_en||('章节 '+(i+1))));if(paperRenderContext().showSourceLinks&&s.page){const a=el('a','page-link','原文 p.'+s.page+' ↗');a.href='source.pdf#page='+s.page;a.target='_blank';a.rel='noopener';heading.append(a);}else if(paperRenderContext().showSourceLinks&&Array.isArray(s.source_refs)&&s.source_refs.length){const refs=el('span','chapter-sources');s.source_refs.forEach(ref=>refs.append(sourceButton(ref)));heading.append(refs);}section.append(heading);
      (s.blocks||[]).forEach((b,index,blocks)=>{const row=el('div','block-row'+(state.view==='translation'?' translation-row':''));row.dataset.blockId=b.id;if(state.view==='translation')row.append(renderBlock(b,'en'),renderBlock(b,'zh'));else {if(renderer.isFigureNote(b,blocks[index-1]))row.classList.add('figure-note-row');if(renderer.isFigureNote(blocks[index+1],b))row.classList.add('figure-with-note');row.append(renderBlock(b,'zh',blocks[index-1]));}section.append(row);});content.append(section);
      const t=button(s.title_cn||s.title_en||s.id,null,()=>{section.scrollIntoView({block:'start'});document.body.classList.remove('toc-visible');scheduleOverlay();});t.dataset.sectionId=s.id;$('toc-list').append(t);
    });
    if(!sections(state.view).length){if(state.view==='translation'&&D.translation&&D.translation.status==='deferred')renderTranslationPanel();else content.append(el('p','empty-view','本视图尚无内容。请在 paper-data.js 中提供完整的 '+VIEW_NAMES[state.view]+' 章节。'));}
    if(!bodyFirst&&state.view!=='translation'&&!sections(state.view).some(s=>(s.blocks||[]).some(b=>b.learning||(b.type==='learning_aid'&&b.placement!=='body')))){
      const unavailable=el('div','learning-unavailable','本篇还没有补充说明。');unavailable.hidden=!state.plainLanguage;
      const first=sections(state.view).flatMap(s=>s.blocks||[])[0];if(first){const supplement=button('为这一节补充解释','quiet-button',()=>{const frame=Array.from(content.querySelectorAll('.block-frame')).find(n=>n.dataset.contentId===first.id);if(frame){const draft=newBlockDraft(frame,first);draft.user_note='请为这一节补充词语、背景和关键公式的解释。';persistDrafts();renderDetail(true);}});supplement.disabled=!state.online;unavailable.append(supplement);}content.prepend(unavailable);
    }
    typeset(content);scheduleOverlay();
  }
  function renderTranslationPanel(){
    const panel=el('section','translation-generator');panel.id='translation-generator';panel.append(el('h2',null,'按需生成全文翻译'),el('p',null,'概览与详解已经可以阅读。需要逐段双语对照时，再生成全文翻译；打开这个页面不会启动生成。'));
    const status=el('p','translation-job-status');status.id='translation-job-status';status.setAttribute('role','status');panel.append(status);
    const generate=button('生成完整双语翻译','primary-button',generateTranslation);generate.id='generate-translation';panel.append(generate);content.append(panel);updateTranslationPanel();
    if(!preview&&!state.stopped)checkTranslation();
  }
  function updateTranslationPanel(){
    const status=$('translation-job-status'),generate=$('generate-translation');if(!status||!generate)return;
    const info=translationJob.info||{status:'deferred'},active=['queued','running'].includes(info.status),labels={deferred:'尚未生成全文翻译。',queued:'已排队，等待生成。',running:'正在生成并核对双语翻译，可继续阅读概览与详解。',failed:'翻译生成失败。',interrupted:'翻译任务已中断。',ready:'翻译已完成，正在载入。'};
    const progress=info.progress&&typeof info.progress==='object'?((info.progress.total?info.progress.completed+'/'+info.progress.total+' 批次 · ':'')+({preparing:'准备原文',translating:'翻译',auditing:'检查覆盖',reviewing:'核对译文'}[info.progress.phase]||'')):String(info.progress||'');
    status.textContent=preview?'这是隔离预览；请在正式阅读器中生成翻译。':(labels[info.status]||'正在查询翻译状态。')+(progress?' '+progress:'')+(info.error?' '+String(info.error):'');
    if(info.unknown_delivery&&!active)status.textContent+=' 上次请求的执行结果尚未确认，请先核对已有会话。';
    generate.disabled=preview||state.stopped||translationJob.pending||active||info.status==='ready'||!!info.unknown_delivery||(['failed','interrupted'].includes(info.status)&&info.retryable===false);
    generate.textContent=active?'翻译生成中':translationJob.pending?'正在提交…':['failed','interrupted'].includes(info.status)?'重新生成翻译':'生成完整双语翻译';
  }
  async function checkTranslation(){
    if(preview||state.stopped||state.view!=='translation'||translationJob.loading)return;
    translationJob.loading=true;clearTimeout(translationJob.timer);
    try{translationJob.info=await api('/api/papers/'+encodeURIComponent(paperId)+'/translation');updateTranslationPanel();if(translationJob.info.status==='ready'){await loadTranslation(translationJob.info);return;}}
    catch(error){translationJob.info=Object.assign({},translationJob.info||{status:'deferred'},{error:error.message});updateTranslationPanel();}
    finally{translationJob.loading=false;}
    if(state.view==='translation'&&!state.stopped&&translationJob.info&&['queued','running'].includes(translationJob.info.status))translationJob.timer=setTimeout(checkTranslation,3000);
  }
  async function generateTranslation(){
    const info=translationJob.info||{};if(preview||state.stopped||translationJob.pending||['queued','running','ready'].includes(info.status)||info.unknown_delivery||(['failed','interrupted'].includes(info.status)&&info.retryable===false))return;
    translationJob.pending=true;updateTranslationPanel();let requestId=readLocal('translation_request',null);if(!requestId||['failed','interrupted'].includes(info.status)){requestId=crypto.randomUUID();writeLocal('translation_request',requestId);}
    try{translationJob.info=await api('/api/papers/'+encodeURIComponent(paperId)+'/translation','POST',{request_id:requestId,action:'generate'});}
    catch(error){translationJob.info=Object.assign({},info,{error:error.message});if(error.data&&error.data.unknown_delivery)translationJob.info.unknown_delivery=true;}
    finally{translationJob.pending=false;updateTranslationPanel();}
    if(translationJob.info&&translationJob.info.status==='ready'){try{await loadTranslation(translationJob.info);}catch(error){translationJob.info.error=error.message;updateTranslationPanel();}}else if(state.view==='translation')checkTranslation();
  }
  async function loadTranslation(info){
    if(sections('translation').length)return;if(!/^[a-f0-9]{64}$/i.test(info.content_hash||''))throw new Error('翻译资源标识无效，请重新打开阅读器。');
    await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='paper-data.js?v='+info.content_hash;script.onload=()=>{script.remove();resolve();};script.onerror=()=>{script.remove();reject(new Error('翻译载入失败，请重新打开阅读器。'));};document.head.append(script);});
    const next=window.PAPER_DATA;if(!next||next.id!==paperId||Number(next.schema_version)!==3||!Array.isArray(next.sections)||!next.sections.length||!next.translation||next.translation.status!=='ready')throw new Error('翻译尚未完整写入，请重新打开阅读器。');
    D=next;writeLocal('translation_request',null);if(state.view==='translation')renderView();
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
  function movedAid(id){const matches=Array.from(content.querySelectorAll('[data-legacy-content-id]')).filter(n=>n.dataset.legacyContentId===id);return matches.length===1?matches[0]:null;}
  function findField(f){const found=Array.from(content.querySelectorAll('.anchor-field')).find(n=>n.dataset.view===(f.view||state.view)&&n.dataset.contentId===f.content_id&&n.dataset.language===(f.language||'zh')&&n.dataset.field===f.field);if(found)return found;
    if((f.view||state.view)!==state.view)return null;const moved=movedAid(f.content_id);if(!moved)return null;
    const matches=Array.from(moved.querySelectorAll('.anchor-field')).filter(n=>n.dataset.language===(f.language||'zh')&&n.dataset.field.replace(/^learning\.blocks\.\d+\./,'')===f.field);return matches.length===1?matches[0]:null;}
  function locateFragment(f){const root=findField(f);if(!root)return{status:'unlocated',reason:'此视图找不到原内容块'};const text=canonicalIndex(root).text;const exact=String(f.exact||'');if(!exact)return{status:'unlocated',reason:'缺少选中文字'};if(f.content_version!==undefined&&String(f.content_version)===String(version)&&Number.isInteger(f.start)&&Number.isInteger(f.end)&&text.slice(f.start,f.end)===exact)return{status:'located',root,range:rangeFor(root,f.start,f.end),start:f.start,end:f.end};const matches=[];let p=0;while((p=text.indexOf(exact,p))>=0){const prefix=String(f.prefix||''),suffix=String(f.suffix||'');if((!prefix||text.slice(Math.max(0,p-prefix.length),p)===prefix)&&(!suffix||text.slice(p+exact.length,p+exact.length+suffix.length)===suffix))matches.push(p);p+=Math.max(1,exact.length);}if(Number.isInteger(f.occurrence)&&f.occurrence>=0){const start=matches[f.occurrence];return start===undefined?{status:'unlocated',reason:'正文中找不到指定的词语位置'}:{status:'located',root,range:rangeFor(root,start,start+exact.length),start,end:start+exact.length};}if(matches.length!==1)return{status:'unlocated',reason:matches.length?'相同引文出现多次，无法唯一定位':'内容已更新，原引文尚未匹配'};return{status:'located',root,range:rangeFor(root,matches[0],matches[0]+exact.length),start:matches[0],end:matches[0]+exact.length};}
  function anchorFor(a){
    if(a.anchor)return a.anchor;
    // Legacy anchors remain on the historical explanation blocks with their original IDs.
    if(a.block_id){const view='explanation';const lang='zh';const block=sections(view).flatMap(s=>s.blocks||[]).find(b=>b.id===a.block_id),field=block&&typeof block.text_zh_latex==='string'?'text_zh_latex':'text_zh';if(!a.selected_text)return{kind:'block',view,language:lang,content_id:a.block_id,content_version:version,fragments:[]};return{kind:'text',view,language:lang,content_id:a.block_id,field,content_version:0,exact:a.selected_text,prefix:'',suffix:'',fragments:[{view,language:lang,content_id:a.block_id,field,content_version:0,exact:a.selected_text,prefix:'',suffix:''}]};}
    return null;
  }
  function locateAnnotation(a){const anchor=anchorFor(a);if(!anchor)return{status:'unlocated',reason:'历史批注没有定位信息',ranges:[]};if(anchor.migration_status==='unlocated')return{status:'unlocated',reason:anchor.reason||'历史选区不能唯一恢复',ranges:[]};if(anchor.view!==state.view)return{status:'other-view',ranges:[],view:anchor.view};if(anchor.kind==='block'){const block=Array.from(content.querySelectorAll('.block-frame')).find(n=>n.dataset.contentId===anchor.content_id&&n.dataset.language===(anchor.language||'zh'))||((anchor.language||'zh')==='zh'?movedAid(anchor.content_id):null);return block?{status:'located',block,ranges:[]}:{status:'unlocated',reason:'原内容块已移除',ranges:[]};}const fragments=anchor.fragments&&anchor.fragments.length?anchor.fragments:[anchor];const parts=fragments.map(locateFragment);const failed=parts.find(x=>x.status==='unlocated');return{status:failed?'unlocated':'located',reason:failed&&failed.reason,ranges:parts.filter(p=>p.range).map(p=>p.range),parts};}
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
  function newBlockDraft(frame,b){const anchor={kind:'block',view:state.view,language:frame.dataset.language,content_id:b.id,field:'block',content_version:version,start:0,end:0,exact:'',prefix:'',suffix:'',fragments:[]};const draft=newDraft(anchor,b['caption_'+(frame.dataset.language==='en'?'en':'zh')]||b.latex||(frame.dataset.language==='zh'&&b.text_zh_latex)||b['text_'+(frame.dataset.language==='en'?'en':'zh')]||'整块图表 / 公式');draft.study_state={version:1,components:Object.assign({},state.studyState)};return draft;}
  function showPanel(){const p=$('annotation-panel');p.hidden=false;p.classList.remove('collapsed');$('panel-collapse').textContent='−';$('panel-collapse').setAttribute('aria-label','折叠问答');}
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
  function legacyMarkdown(parent,text){
    const lines=String(text||'').split('\n');let list=null,code=null,math=null;
    lines.forEach(line=>{
      if(math){const end=line.indexOf(math.close);if(end>=0){math.parts.push(line.slice(0,end));parent.append(mathAtom(math.parts.join('\n'),true,math.open+math.parts.join('\n')+math.close));const rest=line.slice(end+math.close.length);if(rest.trim()){const n=el('p');inline(n,rest);parent.append(n);}math=null;}else math.parts.push(line);return;}
      if(line.startsWith('```')){if(code){code=null;}else{code=el('pre');parent.append(code);}list=null;return;}if(code){code.append(document.createTextNode(line+'\n'));return;}
      const display=/^\s*(\$\$|\\\[)(.*)$/.exec(line);if(display){const close=display[1]==='$$'?'$$':'\\]',end=display[2].indexOf(close);if(end>=0){parent.append(mathAtom(display[2].slice(0,end),true,line.trim()));}else math={open:display[1],close,parts:[display[2]]};list=null;return;}
      const h=/^(#{1,4})\s+(.+)$/.exec(line);if(h){const n=el(h[1].length<3?'h3':'h4');inline(n,h[2]);parent.append(n);list=null;return;}const li=/^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);if(li){if(!list){list=el(/^\s*\d/.test(line)?'ol':'ul');parent.append(list);}const n=el('li');inline(n,li[1]);list.append(n);return;}list=null;if(line.trim()){const n=el('p');inline(n,line);parent.append(n);}else parent.append(document.createTextNode('\n'));
    });
    if(math){const n=el('span','math-error');n.append(el('span','math-error-label','公式渲染错误：显示公式缺少结束定界符'),el('code','math-error-source',math.open+math.parts.join('\n')));parent.append(n);}
  }
  function paperAssets(document) {
    const blocks=['overview','explanation','translation'].flatMap(v=>sections(v).flatMap(s=>s.blocks||[]));
    const assets=new Set();function collect(value){if(!value||typeof value!=='object')return;if(typeof value.src==='string')assets.add(value.src);if(typeof value.asset==='string')assets.add(value.asset);Object.values(value).forEach(v=>{if(v&&typeof v==='object')collect(v);});}
    // Already saved source context belongs to the user's question/history.
    // Keep its confined images usable without reading ignored paper metadata.
    const preserved=(document&&document.blocks||[]).filter(b=>String(b.id).startsWith('default-context-'));
    [blocks,preserved,D.learning_catalog&&D.learning_catalog.aids].forEach(collect);return Array.from(assets);
  }
  function richContext(scope,attachments,document) {return {scope,registeredOnly:true,attachments:attachments||[],paperAssets:paperAssets(document),legacy:false};}
  function markdown(parent,text,context) {renderer.markdown(parent,text,context || richContext('annotation/markdown',[]));}
  function renderRich(parent,document,fallback,scope,attachments) {
    if(document&&Array.isArray(document.blocks))renderer.renderDocument(parent,document,richContext(scope,attachments,document));
    else markdown(parent,fallback,richContext(scope,attachments));
  }
  function richDraft(a,followup) {
    const key=followup?'followup_rich':'rich_edits',cache=readLocal(key,{});
    if(cache[a.id])return cache[a.id];
    return {note_content:followup?null:a.note_content||null,attachments:followup?[]:(a.attachments||[]),study_state:followup?null:a.study_state||null,editor_extra_blocks:followup?[]:a.editor_extra_blocks};
  }
  function persistRichDraft(a,data,followup) {
    if(a.local&&!followup){Object.assign(a,data);persistDrafts();}
    else {const key=followup?'followup_rich':'rich_edits',cache=readLocal(key,{});cache[a.id]=data;writeLocal(key,cache);}
  }
  function updateRichText(a,value,followup) {
    const data=richDraft(a,followup),old=data.note_content&&data.note_content.blocks||[];
    if(!Array.isArray(data.editor_extra_blocks))data.editor_extra_blocks=old.filter(b=>!isEditableTextBlock(b)&&!String(b.id).startsWith('reader-text-'));
    const parsed=renderer.markdownDocument(value);
    function markGenerated(blocks){blocks.forEach(b=>{b.id='reader-text-'+b.id;if(b.blocks)markGenerated(b.blocks);});}markGenerated(parsed.blocks);
    parsed.blocks=parsed.blocks.concat(data.editor_extra_blocks);data.note_content=parsed;
    data.study_state={version:1,components:Object.assign({},data.study_state&&data.study_state.components||{},state.studyState)};persistRichDraft(a,data,followup);
  }
  function isEditableTextBlock(b) {return !String(b.id).startsWith('default-context-')&&['paragraph','para','heading','list','quote','separator'].includes(b.type);}
  function initialEditorText(a) {
    if(!a.note_content||!Array.isArray(a.note_content.blocks))return a.user_note||'';
    // Externally supplied rich blocks stay editable through their own preview/controls;
    // the textarea edits textual blocks without duplicating the rich fallback string.
    function markdownText(b){const value=b.text_zh_latex??b.text_zh??b.text??'';
      if(b.type==='heading')return '#'.repeat(Math.max(1,Math.min(6,b.level||3)))+' '+value;
      if(b.type==='list')return (b.items||[]).map((x,i)=>(b.ordered?(i+1)+'. ':'- ')+(typeof x==='string'?x:x.text_zh||x.text||'')).join('\n');
      if(b.type==='quote')return (b.blocks?b.blocks.map(markdownText).join('\n'):value).split('\n').map(line=>'> '+line).join('\n');
      if(b.type==='formula')return '$$\n'+(b.latex||'')+'\n$$';
      if(b.type==='code')return '```'+(b.language||'')+'\n'+(b.code??value)+'\n```';
      if(b.type==='figure')return '!['+(b.alt||b.caption_zh||b.caption||'')+']('+(b.src||b.asset||'')+')';
      if(b.type==='table'){const rows=(b.rows_zh||b.rows||[]).slice(),headers=b.headers_zh||b.headers||rows.shift()||[];const cells=row=>'| '+row.map(v=>String(v).replace(/\|/g,'\\|')).join(' | ')+' |';return [cells(headers),cells(headers.map(()=>'---'))].concat(rows.map(cells)).join('\n');}
      if(b.type==='separator')return '---';return value;
    }return a.note_content.blocks.filter(b=>isEditableTextBlock(b)||String(b.id).startsWith('reader-text-')).map(markdownText).join('\n\n');
  }
  function installRichEditor(editor,input,a,followup) {
    const tools=el('div','rich-editor-tools'),file=el('input');file.type='file';file.accept='image/png,image/jpeg,image/webp';file.hidden=true;
    const preview=el('div','note-rich-preview'),caption=el('input','attachment-caption');caption.type='text';caption.placeholder='图片说明';caption.setAttribute('aria-label','图片说明');
    tools.append(button('上传图片','',()=>file.click()),caption,file);
    const figureSelect=el('select');figureSelect.setAttribute('aria-label','引用论文图像');figureSelect.append(el('option',null,'引用论文图像'));
    const seen=new Set();['overview','explanation','translation'].flatMap(v=>sections(v).flatMap(s=>s.blocks||[])).filter(b=>(b.src||b.asset)&&!seen.has(b.id)&&seen.add(b.id)).forEach(b=>{const option=el('option',null,b.caption_zh||b.caption||b.id);option.value=b.id;figureSelect.append(option);});
    figureSelect.addEventListener('change',()=>{const b=['overview','explanation','translation'].flatMap(v=>sections(v).flatMap(s=>s.blocks||[])).find(b=>b.id===figureSelect.value);if(b){appendBlock({id:'reference-'+uid(),type:'figure',src:b.src||b.asset,caption_zh:caption.value||b.caption_zh||b.caption||'',source_refs:b.source_refs});figureSelect.selectedIndex=0;}});tools.append(figureSelect);
    const aidSelect=el('select');aidSelect.setAttribute('aria-label','引用学习组件');aidSelect.append(el('option',null,'引用学习组件'));
    (D.learning_catalog&&D.learning_catalog.aids||[]).forEach(aid=>{const option=el('option',null,aid.title||aid.id);option.value=aid.id;aidSelect.append(option);});
    if(aidSelect.options.length>1){aidSelect.addEventListener('change',()=>{if(aidSelect.selectedIndex){appendBlock({id:'reference-'+uid(),type:'learning_aid',aid_id:aidSelect.value});aidSelect.selectedIndex=0;}});tools.append(aidSelect);}
    editor.insertBefore(tools,input.nextSibling);editor.append(preview);
    function updatePreview(){const data=richDraft(a,followup);preview.replaceChildren();if(data.note_content)renderRich(preview,data.note_content,'','editor/'+a.id+(followup?'/followup':''),data.attachments);typeset(preview);}
    function appendBlock(block){const data=richDraft(a,followup);if(!Array.isArray(data.editor_extra_blocks))data.editor_extra_blocks=(data.note_content&&data.note_content.blocks||[]).filter(b=>!isEditableTextBlock(b)&&!String(b.id).startsWith('reader-text-'));data.editor_extra_blocks.push(block);persistRichDraft(a,data,followup);updateRichText(a,input.value,followup);updatePreview();}
    async function uploadImage(image) {
      if(!['image/png','image/jpeg','image/webp'].includes(image.type)||image.size>1048576){toast('请选择 1 MiB 以内的 PNG、JPEG 或 WebP 图片。');return;}
      const dataURL=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(image);});
      const pending={id:uid(),paper_id:paperId,name:image.name||'截图.png',media_type:image.type,data_base64:String(dataURL).split(',')[1],alt:caption.value||'批注截图',caption:caption.value||''};
      const data=richDraft(a,followup);data.pending_uploads=(data.pending_uploads||[]).concat(pending);persistRichDraft(a,data,followup);
      await sendUpload(pending);
    }
    async function sendUpload(pending){
      try{const result=await api('/api/assets','POST',pending),data=richDraft(a,followup);data.attachments=(data.attachments||[]).concat(result.attachment);data.pending_uploads=(data.pending_uploads||[]).filter(x=>x.id!==pending.id);persistRichDraft(a,data,followup);appendBlock({id:result.attachment.id,type:'figure',src:result.attachment.src,alt:result.attachment.alt,caption:result.attachment.caption});toast('图片已保存。');}
      catch(error){toast(error.message+' 图片草稿仍在本地。');showPending();}
    }
    function showPending(){tools.querySelectorAll('.retry-upload').forEach(n=>n.remove());(richDraft(a,followup).pending_uploads||[]).forEach(p=>tools.append(button('重试图片','retry-upload',()=>sendUpload(p))));}
    file.addEventListener('change',async()=>{for(const image of file.files)await uploadImage(image);file.value='';});
    input.addEventListener('paste',event=>{const files=Array.from(event.clipboardData&&event.clipboardData.files||[]).filter(f=>f.type.startsWith('image/'));if(files.length){event.preventDefault();files.forEach(uploadImage);}});
    input.addEventListener('input',()=>{updateRichText(a,input.value,followup);updatePreview();});showPending();updatePreview();
  }
  function renderDetail(focus){
    const a=findAnnotation(state.selected);if(!a){openList();return;}const edits=readLocal('edits',{}),followups=readLocal('followups',{}),position=beginPanelRender('detail:'+a.id,[state.view,annotationNumber(a),a,edits[a.id],followups[a.id]]);if(!position){if(focus){const input=$('annotation-note');if(input)input.focus({preventScroll:true});}return;}const body=$('panel-body');$('panel-title').textContent='#'+annotationNumber(a)+' · '+(VIEW_NAMES[(anchorFor(a)||{}).view]||'历史批注');const top=el('div','annotation-tools');top.append(button('← 批注列表','',openList),button('定位原文','',()=>jumpAnnotation(a)));body.append(top,renderStatus(a));
    if(a.selected_text)body.append(el('blockquote','annotation-quote',a.selected_text));const loc=locateAnnotation(a);if(loc.status==='unlocated')body.append(el('div','anchor-warning','未定位：'+loc.reason+'。批注仍然保留，可以编辑或删除。'));if(loc.status==='other-view')body.append(button('前往'+VIEW_NAMES[loc.view]+'中的批注','quiet-button',()=>jumpAnnotation(a)));
    const cached=edits[a.id];const editor=el('div','annotation-editor');const label=el('label',null,a.local?'写下你的问题':'问题（编辑后可重新提交）');const input=el('textarea');input.id='annotation-note';input.value=cached===undefined?initialEditorText(a):cached;label.htmlFor=input.id;editor.append(label,input);const actions=el('div','editor-actions');const saved=el('span','draft-status',a.local?'草稿已自动保存':'修改会自动保存到本地');const submit=button(a.local?'提交并提问':'保存并重新提问','primary-button',async()=>{submit.disabled=true;try{await submitAnnotation(a,input.value);}finally{submit.disabled=false;}});actions.append(saved,submit);editor.append(actions);body.append(editor);
    input.addEventListener('input',()=>{if(a.local){a.user_note=input.value;a.updated_at=new Date().toISOString();persistDrafts();}else{const e=readLocal('edits',{});e[a.id]=input.value;writeLocal('edits',e);}saved.textContent='草稿已自动保存';});
    installRichEditor(editor,input,a,false);
    const tools=el('div','annotation-tools');if(!a.local){tools.append(button('保存修改','',async()=>saveEdit(a,input.value,false)));if(['failed','waiting_bridge'].includes(a.status))tools.append(button('重试答疑','',()=>mutation(a,'retry',{})));if(a.unread)tools.append(button('标为已读','',()=>markRead(a)));}tools.append(button('删除批注','danger-button',()=>deleteAnnotation(a)));body.append(tools);
    const history=el('div','answer-history');let turns=Array.isArray(a.history)?a.history:[];
    if(!turns.length&&(a.answer||a.answer_content))turns=[{user_note:a.user_note,note_content:a.note_content,answer:a.answer,answer_content:a.answer_content,attachments:a.attachments,sources:a.sources,status:a.status}];
    turns.forEach((turn,i)=>{
      const t=el('section','answer-turn'),head=el('div','turn-heading');head.append(el('span',null,'问答 '+(i+1)),el('span',null,turn.answered_at?new Date(turn.answered_at).toLocaleString():STATUS_NAMES[turn.status]||''));t.append(head);
      if(turn.user_note||turn.note_content){const question=el('div','turn-question');renderRich(question,turn.note_content,turn.user_note,'annotation/'+a.id+'/turn/'+i+'/question',turn.attachments||a.attachments);t.append(question);}
      if(turn.answer||turn.answer_content){const ans=el('div','answer-content ai-answer');ans.append(el('h3','answer-title','答疑'));renderRich(ans,turn.answer_content,turn.answer_latex||((a.legacy&&turn.answer===a.legacy.answer&&a.answer_latex)||turn.answer),'annotation/'+a.id+'/turn/'+i+'/answer',turn.attachments||a.attachments);t.append(ans);
        if(Array.isArray(turn.sources)&&turn.sources.length)t.append(sourceLinks(turn.sources));
      }else t.append(el('div','pending-answer',pendingText(turn.status||a.status)));history.append(t);
    });if(!turns.length)history.append(el('div','pending-answer',pendingText(a.status)));body.append(history);
    if(a.answer&&!a.local){const follow=el('div','annotation-editor');const lab=el('label',null,'继续追问');const ta=el('textarea');ta.id='annotation-followup';ta.placeholder='围绕这条回答继续提问…';ta.value=readLocal('followups',{})[a.id]||'';lab.htmlFor=ta.id;ta.addEventListener('input',()=>{const f=readLocal('followups',{});f[a.id]=ta.value;writeLocal('followups',f);});follow.append(lab,ta);const row=el('div','editor-actions');row.append(el('span','draft-status','追问草稿自动保存'),button('提交追问','primary-button',async()=>{if(!ta.value.trim()){toast('请先填写追问。');return;}const ok=await mutation(a,'followups',Object.assign({user_note:ta.value,client_request_id:uid()},submissionRich(a,true)));if(ok){const f=readLocal('followups',{});delete f[a.id];writeLocal('followups',f);clearRichDraft(a,true);renderDetail();}}));follow.append(row);body.append(follow);}
    const followupInput=$('annotation-followup');if(followupInput)installRichEditor(followupInput.parentElement,followupInput,a,true);
    if(a.status==='unknown'){const warning=el('div','anchor-warning','这条问题的结果尚未确认，后续问题会等待。');warning.append(button('确认重试','quiet-button',()=>mutation(a,'retry',{client_request_id:uid()})),button('取消问题','quiet-button',()=>mutation(a,'cancel',{client_request_id:uid()})));body.append(warning);}
    typeset(body);finishPanelRender(position);if(focus)setTimeout(()=>input.focus({preventScroll:true}),0);
  }
  function pendingText(status){return status==='draft'?'点击提交，答疑助手会自动处理这条问题。':status==='waiting_bridge'?'问题已保存。答疑助手连接后会继续处理。':status==='failed'?'这次回答未完成。可点击重试。':status==='running'?'答疑助手正在阅读相关内容并准备回答。':'问题已进入队列，回答到达时会提示你。';}
  function jumpAnnotation(a){const anchor=anchorFor(a);if(!anchor){toast('历史批注缺少定位信息。');return;}if(anchor.view!==state.view)switchView(anchor.view);requestAnimationFrame(()=>{const loc=locateAnnotation(a);if(loc.status!=='located'){toast('未能唯一定位原文：'+(loc.reason||'内容缺失'));if(state.selected===a.id)renderDetail();return;}const n=loc.block||(loc.parts&&loc.parts[0]&&loc.parts[0].root);if(n){(loc.parts||[]).forEach(part=>{if(part.root)revealField(part.root);});revealField(n);n.scrollIntoView({block:'center'});}scheduleOverlay();});}
  async function refreshSession() {
    if(preview)return;
    if(state.sessionRefresh)return state.sessionRefresh;
    state.sessionRefresh=(async()=>{
      const response=await fetch('/api/session?paper_id='+encodeURIComponent(paperId),{cache:'no-store'});
      if(!response.ok)throw new Error('本地服务不可用');const session=await response.json();
      if(session.paper_id&&session.paper_id!==paperId)throw new Error('论文服务不匹配');
      const changed=state.token!==session.token||state.serviceInstance!==session.service_instance_id;
      state.token=session.token;state.serviceInstance=session.service_instance_id;
      if(changed&&state.events){state.events.close();state.events=null;}connectEvents();return session;
    })();
    try{return await state.sessionRefresh;}finally{state.sessionRefresh=null;}
  }
  async function api(path,method,body,retried) {
    if(preview)throw new Error('预览仅显示内容。');
    if(!state.token)await refreshSession();
    const response=await fetch(path,{method:method||'GET',headers:Object.assign({'X-Reader-Token':state.token},body===undefined?{}:{'Content-Type':'application/json'}),body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
    if(response.status===401&&!retried){await refreshSession();return api(path,method,body,true);}
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok){const error=new Error(data.error||data.message||(response.status===409?'内容已在别处更新。你的草稿仍在本地，请查看最新内容后再提交。':'请求失败 ('+response.status+')'));error.status=response.status;error.data=data;throw error;}return data;
  }
  function submissionRich(a,followup) {
    const data=richDraft(a,followup);if((data.pending_uploads||[]).length)throw new Error('图片尚未上传，问题和图片草稿仍在本地。');
    return {note_content:data.note_content||null,attachments:data.attachments||[],study_state:data.study_state||{version:1,components:Object.assign({},state.studyState)}};
  }
  function clearRichDraft(a,followup) {const key=followup?'followup_rich':'rich_edits',cache=readLocal(key,{});delete cache[a.id];writeLocal(key,cache);}
  const base='/api/papers/'+encodeURIComponent(paperId)+'/annotations';
  function mergeAnnotation(raw,notify){const a=normalizeAnnotation(raw),old=state.annotations.find(x=>x.id===a.id);if(raw.deleted){state.annotations=state.annotations.filter(x=>x.id!==a.id);}else if(!old||Number(a.revision||0)>=Number(old.revision||0)){state.annotations=state.annotations.filter(x=>x.id!==a.id).concat(a);if(notify&&a.unread&&a.answer&&(!old||a.answer!==old.answer)&&!state.notified.has(a.id+':'+a.revision)){state.notified.add(a.id+':'+a.revision);toast('新回答已到达 · 批注 #'+annotationNumber(a));}}writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();if(!$('annotation-panel').hidden){if(state.panelMode==='list')renderList();else if(state.selected===a.id&&!$('panel-body').contains(document.activeElement))renderDetail();}}
  async function submitAnnotation(a,note){if(!note.trim()){toast('请先写下你的问题。');return;}if(!a.local)return saveEdit(a,note,true);a.user_note=note;persistDrafts();try{const data=await api(base,'POST',Object.assign({id:a.id.replace(/^draft-/,''),block_id:a.block_id,anchor:a.anchor,identity_color:colorFor(a),selected_text:a.selected_text,user_note:note,submit:true,client_request_id:a.id},submissionRich(a,false)));mergeAnnotation(data.annotation,false);state.drafts=state.drafts.filter(x=>x.id!==a.id);await removeManagedDraft(a.id);persistDrafts();openAnnotation(data.annotation.id);toast('问题已提交，回答到达时会提示。');}catch(e){toast(e.message);}}
  async function saveEdit(a,note,submit){if(!note.trim()){toast('请先写下你的问题。');return false;}try{const data=await api(base+'/'+encodeURIComponent(a.id),'PATCH',Object.assign({revision:a.revision,user_note:note,submit:!!submit},submissionRich(a,false)));mergeAnnotation(data.annotation,false);const e=readLocal('edits',{});delete e[a.id];writeLocal('edits',e);clearRichDraft(a,false);renderDetail();toast(submit?'修改已保存并进入答疑队列。':'修改已保存。');return true;}catch(e){if(e.status===409&&e.data.current){mergeAnnotation(e.data.current,false);renderDetail();}toast(e.message,true);return false;}}
  async function mutation(a,action,payload){try{const data=await api(base+'/'+encodeURIComponent(a.id)+'/'+action,'POST',Object.assign({revision:a.revision},payload));if(data.annotation)mergeAnnotation(data.annotation,false);renderDetail();return true;}catch(e){if(e.status===409&&e.data.current){mergeAnnotation(e.data.current,false);renderDetail();}toast(e.message);return false;}}
  async function markRead(a){if(!a.unread)return;await mutation(a,'read',{});}
  async function deleteAnnotation(a){if(a.local){state.drafts=state.drafts.filter(x=>x.id!==a.id);await removeManagedDraft(a.id);persistDrafts();openList();toast('草稿已删除。');return;}try{await api(base+'/'+encodeURIComponent(a.id),'DELETE',{revision:a.revision});state.annotations=state.annotations.filter(x=>x.id!==a.id);writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();openList();toast('批注已删除。');}catch(e){toast(e.message);}}
  function connection(label,offline){$('connection-status').textContent=label;$('connection-status').classList.toggle('offline',!!offline);}
  function showAssistant(assistant) {
    state.assistant=assistant;const actions=$('assistant-actions');actions.replaceChildren();$('service-help').hidden=true;
    const labels={ready:'答疑可用',connected:'宿主已连接，答疑尚未验证',waiting:'答疑未连接',connecting:'正在恢复答疑',paused:'答疑已暂停',error:'答疑连接异常',unknown:'答疑状态尚未验证'};
    connection(assistant.message||labels[assistant.state]||labels.unknown,assistant.state!=='ready');
    if(assistant.pending_count&&['waiting','paused','error'].includes(assistant.state))actions.append(el('div','pending-count','已保存 '+assistant.pending_count+' 个问题'));
    const names={ensure:'连接答疑',enable:'开启答疑',pause:'暂停本库答疑',rebuild:'重建会话'};
    (assistant.allowed_actions||[]).forEach(action=>{const b=button(names[action]||action,'quiet-button',()=>assistantAction(action));b.disabled=!!state.assistantBusy;actions.append(b);});
    if((assistant.blocked_tasks||[]).length)actions.append(el('div','anchor-warning','有问题的结果尚未确认。请在批注中明确重试或取消。'));
  }
  async function assistantAction(action) {
    if(state.assistantBusy)return;state.assistantBusy=true;if(state.assistant)showAssistant(state.assistant);
    try{const payload={paper_id:paperId,request_id:uid()};if(action==='enable')payload.enable=true;
      const response=await api('/api/assistant/'+(action==='enable'?'ensure':action),'POST',payload);if(response.assistant)showAssistant(response.assistant);await poll();
    }catch(error){toast(error.message,true);}finally{state.assistantBusy=false;if(state.assistant)showAssistant(state.assistant);}
  }
  const draftsBase='/api/papers/'+encodeURIComponent(paperId)+'/drafts';
  const managedDrafts=readLocal('draft_meta',{});let draftSyncing=false;
  const draftTombstones=readLocal('draft_tombstones',{});
  function scheduleDraftSync() {if(preview)return;clearTimeout(scheduleDraftSync.timer);scheduleDraftSync.timer=setTimeout(()=>{if(state.online&&state.assistant)syncManagedDrafts();},600);}
  async function removeManagedDraft(id) {
    const record=managedDrafts[id];draftTombstones[id]={revision:record&&record.revision||null};writeLocal('draft_tombstones',draftTombstones);
    if(!state.online||!state.assistant)return;
    await deleteManagedTombstone(id);
  }
  async function deleteManagedTombstone(id) {
    const tombstone=draftTombstones[id];if(!tombstone||!tombstone.revision)return;
    try{await api(draftsBase+'/'+encodeURIComponent(id),'DELETE',{revision:tombstone.revision});delete managedDrafts[id];delete draftTombstones[id];writeLocal('draft_meta',managedDrafts);writeLocal('draft_tombstones',draftTombstones);}
    catch(error){toast(error.status===409?'草稿已在别处更新，本地删除已保留，请先核对另一页面的修改。':'草稿删除会在连接恢复后同步。');}
  }
  async function syncManagedDrafts() {
    if(preview||draftSyncing||!state.assistant||state.stopped)return;draftSyncing=true;
    try{
      if(!state.managedLoaded){const response=await api(draftsBase);(response.drafts||[]).forEach(record=>{
        if(draftTombstones[record.id]){if(!draftTombstones[record.id].revision)draftTombstones[record.id].revision=record.revision;return;}
        managedDrafts[record.id]={revision:record.revision,snapshot:JSON.stringify(record.data)};
        if(record.id==='editor-state'){const data=record.data||{};['edits','followups','rich_edits','followup_rich'].forEach(key=>{const local=readLocal(key,{});writeLocal(key,Object.assign({},data[key]||{},local));});
          if(localStorage.getItem(storagePrefix+'plain_language')===null&&typeof data.plain_language==='boolean')setPlainLanguage(data.plain_language);
          Object.assign(state.studyState,data.study_state||{},studyStateAtOpen);
          if(!Object.keys(studyStateAtOpen).length&&Object.keys(data.study_state||{}).length&&!$('panel-body').contains(document.activeElement)){const top=scroll.scrollTop;renderView();scroll.scrollTop=top;}
        }else if(!state.drafts.some(d=>d.id===record.id)&&!state.annotations.some(a=>'draft-'+a.id===record.id)){state.drafts.push(normalizeAnnotation(Object.assign({},record.data,{local:true})));}
      });state.managedLoaded=true;writeLocal('drafts',state.drafts);updateCounts();}
      for(const id of Object.keys(draftTombstones))await deleteManagedTombstone(id);
      const editor={edits:readLocal('edits',{}),followups:readLocal('followups',{}),rich_edits:readLocal('rich_edits',{}),followup_rich:readLocal('followup_rich',{}),plain_language:state.plainLanguage,study_state:state.studyState};
      const records=state.drafts.map(d=>({id:d.id,data:d})).concat({id:'editor-state',data:editor});
      for(const record of records){if(draftTombstones[record.id])continue;const snapshot=JSON.stringify(record.data),previous=managedDrafts[record.id];if(previous&&previous.snapshot===snapshot)continue;
        try{const response=await api(draftsBase,'POST',{id:record.id,data:record.data,revision:previous&&previous.revision||0});managedDrafts[record.id]={revision:response.draft.revision,snapshot};if(draftTombstones[record.id]){draftTombstones[record.id].revision=response.draft.revision;await deleteManagedTombstone(record.id);}}
        catch(error){if(error.status===409){toast('另一个页面更新了草稿，本地草稿已保留。');}else throw error;}
      }writeLocal('draft_meta',managedDrafts);
    }catch(_){/* Local copies remain authoritative until a managed sync succeeds. */}finally{draftSyncing=false;}
  }
  async function poll() {
    if(preview||state.polling||state.stopped)return;state.polling=true;
    try{
      if(!state.token)await refreshSession();const data=await api(base);
      if(Array.isArray(data.annotations)){
        const incoming=data.annotations.map(normalizeAnnotation),previous=state.annotations;
        incoming.forEach(a=>{const old=previous.find(x=>x.id===a.id);if(a.unread&&(a.answer||a.answer_content)&&old&&(a.answer!==old.answer||JSON.stringify(a.answer_content)!==JSON.stringify(old.answer_content))&&!state.notified.has(a.id+':'+a.revision)){state.notified.add(a.id+':'+a.revision);toast('新回答已到达 · 批注 #'+annotationNumber(a));}});
        state.annotations=incoming;writeLocal('cache',state.annotations);updateCounts();scheduleOverlay();
        if(!$('annotation-panel').hidden&&!$('panel-body').contains(document.activeElement)){if(state.panelMode==='list')renderList();else renderDetail();}
      }
      state.online=true;const status=await api('/api/status?paper_id='+encodeURIComponent(paperId));
      if(status.assistant){if(state.serviceInstance&&status.assistant.service_instance_id&&state.serviceInstance!==status.assistant.service_instance_id)await refreshSession();showAssistant(status.assistant);await syncManagedDrafts();}
      else connection(status.bridge_available?'已连接 · 答疑助手在线':'已连接 · 等待答疑助手',!status.bridge_available);
      content.querySelectorAll('.learning-unavailable button').forEach(b=>b.disabled=false);
    }catch(_){state.online=false;connection('本地服务未连接 · 草稿仍保留，请运行本篇启动器',true);$('service-help').hidden=false;}
    finally{state.polling=false;}
  }
  function connectEvents() {
    if(preview||typeof EventSource==='undefined'||state.events||state.stopped)return;
    const events=new EventSource('/api/events?token='+encodeURIComponent(state.token)+'&paper_id='+encodeURIComponent(paperId));state.events=events;
    events.addEventListener('annotation',event=>{try{const data=JSON.parse(event.data);if(!data.paper_id||data.paper_id===paperId){if(data.annotation)mergeAnnotation(data.annotation,true);else poll();}}catch(_){}});
    events.addEventListener('status',()=>poll());events.onopen=()=>{state.eventFailures=0;};
    events.onerror=()=>{events.close();if(state.events===events)state.events=null;connection('连接恢复中 · 草稿自动保存',true);
      clearTimeout(connectEvents.timer);const delay=Math.min(30000,1000*Math.pow(2,Math.min(5,state.eventFailures||0)));state.eventFailures=(state.eventFailures||0)+1;
      connectEvents.timer=setTimeout(async()=>{if(!state.stopped){try{await refreshSession();await poll();}catch(_){$('service-help').hidden=false;}}},delay);
    };
  }
  if(!D||Number(D.schema_version)!==3){$('paper-heading').append(el('h1',null,'缺少 v3 论文数据'));content.append(el('p','empty-view','请使用完整的 schema_version: 3 paper-data.js。旧版论文继续使用原 reader.html。'));return;}
  state.positions=readLocal('positions',{});state.drafts=readLocal('drafts',[]).map(normalizeAnnotation);state.annotations=readLocal('cache',[]).map(normalizeAnnotation);state.plainLanguage=readLocal('plain_language',['novice','beginner'].includes(D.level));const savedView=readLocal('view','overview');if(VIEW_NAMES[savedView])state.view=savedView;
  $('plain-language-toggle').addEventListener('click',()=>setPlainLanguage(!state.plainLanguage));
  $('copy-launch-command').addEventListener('click',async()=>{try{await navigator.clipboard.writeText('打开阅读器.bat');toast('启动器名称已复制。');}catch(_){toast('请双击本篇“打开阅读器.bat”。');}});
  $('export-drafts').addEventListener('click',()=>{const blob=new Blob([JSON.stringify({paper_id:paperId,drafts:state.drafts,edits:readLocal('edits',{}),rich_edits:readLocal('rich_edits',{}),followups:readLocal('followups',{}),followup_rich:readLocal('followup_rich',{})},null,2)],{type:'application/json'});const link=el('a');link.href=URL.createObjectURL(blob);link.download=paperId+'-drafts.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);});
  document.querySelectorAll('.view-tabs [data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
  $('annotations-toggle').addEventListener('click',()=>{if(!$('annotation-panel').hidden&&state.panelMode==='list')closePanel();else openList();});$('panel-close').addEventListener('click',closePanel);$('panel-collapse').addEventListener('click',()=>{const collapsed=$('annotation-panel').classList.toggle('collapsed');$('panel-collapse').textContent=collapsed?'＋':'−';$('panel-collapse').setAttribute('aria-label',collapsed?'展开问答':'折叠问答');scheduleOverlay();});
  $('panel-expand').addEventListener('click',()=>{const p=$('annotation-panel'),expanded=p.classList.toggle('expanded');p.classList.remove('collapsed');$('panel-collapse').textContent='−';$('panel-collapse').setAttribute('aria-label','折叠问答');$('panel-expand').textContent=expanded?'↙':'↗';$('panel-expand').setAttribute('aria-pressed',String(expanded));$('panel-expand').setAttribute('aria-label',expanded?'还原问答宽度':'放大问答');scheduleOverlay();});
  $('toc-toggle').addEventListener('click',()=>{document.body.classList.toggle(innerWidth<=760?'toc-visible':'toc-hidden');scheduleOverlay();});
  $('selection-action').addEventListener('mousedown',e=>e.preventDefault());$('selection-action').addEventListener('click',()=>{if(state.selectedAnchor)newDraft(state.selectedAnchor,state.selectedAnchor.fragments.map(f=>f.exact).join('\n'));});
  content.addEventListener('mouseup',()=>setTimeout(selectionChanged,0));document.addEventListener('selectionchange',()=>{clearTimeout(selectionChanged.timer);selectionChanged.timer=setTimeout(selectionChanged,90);});document.addEventListener('keydown',e=>{if(e.key==='Escape'){closePanel();$('selection-action').hidden=true;document.body.classList.remove('toc-visible');}});
  scroll.addEventListener('scroll',()=>{scheduleOverlay();$('selection-action').hidden=true;clearTimeout(savePosition.timer);savePosition.timer=setTimeout(savePosition,180);},{passive:true});window.addEventListener('resize',scheduleOverlay);window.addEventListener('beforeunload',savePosition);
  if(window.ResizeObserver)new ResizeObserver(scheduleOverlay).observe(content);if(document.fonts&&document.fonts.ready)document.fonts.ready.then(scheduleOverlay);
  renderPaper();scroll.scrollTop=(state.positions[state.view]||{}).top||0;updateCounts();
  const pollingTimer=preview?null:setInterval(poll,5000);
  if(preview){document.body.classList.add('reader-preview');connection('内容预览',false);$('annotations-toggle').hidden=true;$('stop-service').hidden=true;$('service-help').hidden=true;$('assistant-actions').hidden=true;}else poll();
  $('stop-service').addEventListener('click',async()=>{if(state.stopped)return;const b=$('stop-service');b.disabled=true;state.stopped=true;try{await api('/api/stop','POST',{});clearInterval(pollingTimer);if(state.events)state.events.close();state.online=false;connection('后台服务已停止 · 重新打开启动器继续',true);toast('后台服务已停止，问题和历史答案已保留。');}catch(e){state.stopped=false;b.disabled=false;toast(e.message);}});
  // Expose deterministic primitives for browser regression tests and anchor inspection.
  window.ReaderV3=Object.freeze({canonicalIndex,rangeFor,captureSelection,locateFragment,locateAnnotation,switchView,allAnnotations,newDraft,openAnnotation,openList,poll,drawAnnotations,setPlainLanguage,renderer,assistantAction,syncManagedDrafts,getState:()=>state});
})();
