/* Shared, DOM-only paper/annotation renderer. No model HTML or scripts enter the DOM. */
(function () {
  'use strict';
  const SUPPORTED = ['paragraph','para','heading','list','formula','table','figure','diagram','algorithm','code','quote','details','learning_aid','separator'];

  function isFigureNote(block, previous) {
    return !!(block && block.type === 'quote' && previous && ['figure','diagram','table'].includes(previous.type));
  }

  function create(host) {
    const {el, inline, mathAtom} = host;
    function text(parent, value, ctx, path, cls) {
      if (value === undefined || value === null || value === '') return null;
      if (ctx.anchorId && host.field) return host.field(parent, String(value), ctx.anchorId, path, ctx.language || 'zh', cls);
      const node = el('div', cls || 'rich-text');
      if(ctx.language==='zh'&&node.classList.contains('prose-field')&&host.proseInline)host.proseInline(node,String(value));else inline(node, String(value));
      parent.append(node);
      return node;
    }
    function localAsset(src, ctx) {
      if (typeof src !== 'string' || /(^|[\\/])\.\.([\\/]|$)|^[\\/]|^[a-z][\w+.-]*:/i.test(src)) return null;
      if (ctx.registeredOnly && !(ctx.attachments || []).some(a => a.src === src) && !(ctx.paperAssets || []).includes(src)) return null;
      return host.safeURL(src, true);
    }
    function imageZoom(img) {
      const zoom = el('button', 'image-zoom');
      zoom.type = 'button'; zoom.setAttribute('aria-label', '放大查看图像'); zoom.append(img);
      zoom.addEventListener('click', () => {
        const dialog = el('dialog', 'image-dialog'), full = img.cloneNode(), toolbar=el('div','image-dialog-toolbar'), canvas=el('div','image-dialog-canvas'), title=el('span','image-dialog-title',img.alt), scale=el('button','quiet-button','实际尺寸'), close = el('button', 'quiet-button', '关闭');
        dialog.setAttribute('aria-label','放大图像');scale.type='button';scale.setAttribute('aria-pressed','false');
        scale.addEventListener('click',()=>{const actual=canvas.classList.toggle('actual-size');scale.textContent=actual?'适应窗口':'实际尺寸';scale.setAttribute('aria-pressed',String(actual));});
        close.type = 'button'; close.addEventListener('click', () => dialog.close());
        full.loading = 'eager';canvas.append(full);toolbar.append(title,scale,close);dialog.append(toolbar,canvas);document.body.append(dialog);
        dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
        dialog.addEventListener('close', () => dialog.remove()); dialog.showModal();
      });
      return zoom;
    }
    function figure(parent, b, ctx, path) {
      const src = localAsset(b.src || b.asset, ctx) || (b.type === 'diagram' && host.safeSVG ? host.safeSVG(b.svg) : null);
      if (!src) { parent.append(el('div', 'asset-placeholder', '图像未登记或路径无效。')); return; }
      const img = el('img', 'figure-image');
      img.src = src;
      img.alt = String(b.alt || b.caption_zh || b.caption || '图示');
      img.loading = 'lazy';
      img.addEventListener('load', host.onLayout);
      img.addEventListener('error', () => { img.replaceWith(el('div', 'asset-placeholder', '图像资源无法加载。')); host.onLayout(); });
      parent.append(imageZoom(img));
      const captionKey = b.caption_zh !== undefined ? 'caption_zh' : b.caption !== undefined ? 'caption' : 'fig_caption_zh';
      text(parent, b[captionKey], ctx, path + captionKey, 'block-caption');
    }
    function renderBlock(b, context) {
      const ctx = Object.assign({language:'zh',scope:'content'}, context), path = ctx.path || '';
      if (!b || typeof b !== 'object') return el('div', 'content-warning', '内容块格式无效。');
      const modernTable=b.type==='table'&&(b.headers||b.headers_zh||b.rows);
      if (ctx.legacy && host.renderLegacy && !modernTable && !['code','quote','details','learning_aid','heading','separator'].includes(b.type)) return host.renderLegacy(b, ctx.language, ctx);
      const node = el('div', 'rich-block rich-' + String(b.type || 'paragraph').replace(/[^\w-]/g, ''));
      if(['figure','diagram','table','learning_aid'].includes(b.type))node.classList.add('content-visual');
      if (ctx.figureNotes && isFigureNote(b, ctx.previousBlock)) node.classList.add('figure-note');
      node.dataset.richId = String(b.id || '');
      const bodyKeys=ctx.language==='en'?['text_en_latex','text_en','text']:['text_zh_latex','text_zh','text'];
      const bodyKey=bodyKeys.find(key=>b[key]!==undefined)||'text';
      switch (b.type) {
        case 'heading': {
          const level = Math.max(2, Math.min(6, Number(b.level) || 3)), heading = el('h' + level);
          text(heading, b[bodyKey] || b.title, ctx, path + (b[bodyKey] ? bodyKey : 'title')); node.append(heading); break;
        }
        case 'formula': {
          const f = ctx.anchorId ? host.field(node, '', ctx.anchorId, path + 'latex', ctx.language, 'formula-display') : el('div', 'formula-display');
          f.append(mathAtom(b.latex || '', true, b.latex || '')); if (!ctx.anchorId) node.append(f);
          text(node, b.eq_ref, ctx, path + 'eq_ref', 'formula-reference'); text(node, b[bodyKey], ctx, path + bodyKey,ctx.language==='zh'?'rich-text prose-field':undefined); break;
        }
        case 'list': {
          const list = el(b.ordered ? 'ol' : 'ul');
          (b.items || []).forEach((item, i) => {
            const li = el('li'), key = typeof item === 'object' && item !== null ? (item.text_zh_latex !== undefined ? 'text_zh_latex' : item.text_zh !== undefined ? 'text_zh' : 'text') : '';
            text(li, key ? item[key] : item, ctx, path + 'items.' + i + (key ? '.' + key : '')); list.append(li);
          }); node.append(list); break;
        }
        case 'table': {
          const suffix=ctx.language==='en'?'en':'zh';
          const rowsKey = Array.isArray(b['rows_'+suffix]) ? 'rows_'+suffix : 'rows', rows = b[rowsKey] || [];
          if (!rows.length && (b.src || b.asset)) { figure(node,b,ctx,path); break; }
          if (!rows.length && b.table_html && host.legacyTable) {host.legacyTable(node,b,ctx.language);break;}
          const wrap = el('div','table-scroll'), table = el('table','data-table');
          const headersKey=Array.isArray(b['headers_'+suffix])?'headers_'+suffix:'headers',headers=b[headersKey];
          if(Array.isArray(headers)){const tr=el('tr');headers.forEach((cell,j)=>{const th=el('th');text(th,cell,ctx,path+headersKey+'.'+j);tr.append(th);});table.append(tr);}
          rows.forEach((row,i) => {
            const tr = el('tr'); (Array.isArray(row) ? row : [row]).forEach((cell,j) => {
              const td = el(!headers && i === 0 && b.header !== false ? 'th' : 'td'); text(td,cell,ctx,path + rowsKey + '.' + i + '.' + j); tr.append(td);
            }); table.append(tr);
          }); wrap.append(table); node.append(wrap);
          const captionKey=b['caption_'+suffix]!==undefined?'caption_'+suffix:'caption';
          text(node,b[captionKey],ctx,path+captionKey,'block-caption'); break;
        }
        case 'figure': case 'diagram': figure(node,b,ctx,path); break;
        case 'code': {
          const pre = el('pre','code-block'), code = el('code',null,b.code !== undefined ? b.code : b[bodyKey] || '');
          pre.append(code); node.append(pre); break;
        }
        case 'algorithm': {
          text(node,b.algo_title || b.title,ctx,path + (b.algo_title ? 'algo_title' : 'title'),'algorithm-title');
          const name = Array.isArray(b.algo_lines_latex) ? 'algo_lines_latex' : Array.isArray(b.algo_lines) ? 'algo_lines' : 'lines';
          (b[name] || []).forEach((line,i) => text(node,line,ctx,path + name + '.' + i,'algorithm-line')); break;
        }
        case 'quote': {
          const quote = el('blockquote','rich-quote'); text(quote,b[bodyKey],ctx,path + bodyKey);
          if (b.blocks) renderDocument(quote,{version:1,blocks:b.blocks},Object.assign({},ctx,{path:path + 'blocks.'})); node.append(quote); break;
        }
        case 'details': {
          const details = el('details','rich-details'), summary = el('summary');
          text(summary,b.title || b.summary || '展开内容',ctx,path + (b.title ? 'title' : 'summary')); details.append(summary);
          renderDocument(details,{version:1,blocks:b.blocks || []},Object.assign({},ctx,{path:path + 'blocks.'}));
          details.addEventListener('toggle',() => { host.typeset(details); host.onLayout(); }); node.append(details); break;
        }
        case 'learning_aid': {
          const aid = b.aid || (host.catalog.aids || []).find(a => a.id === b.aid_id);
          if (window.LearningComponents && aid) node.append(window.LearningComponents.render(aid,Object.assign({},host,{text:(p,v,key,cls) => text(p,v,ctx,path + (b.aid ? 'aid.' : 'aid_id.') + key,cls),context:ctx,renderDocument:(p,d,c) => renderDocument(p,d,Object.assign({},ctx,c))})));
          else if (aid && aid.fallback && Array.isArray(aid.fallback.blocks)) renderDocument(node,aid.fallback,Object.assign({},ctx,{path:path + (b.aid ? 'aid.' : 'aid_id.') + 'fallback.blocks.'}));
          else text(node,(aid && aid.fallback_text) || b.fallback_text || '学习演示暂不可用。',ctx,path + ((aid && aid.fallback_text) ? (b.aid ? 'aid.' : 'aid_id.') : '') + 'fallback_text'); break;
        }
        case 'separator': node.append(el('hr')); break;
        default: text(node,b[bodyKey] || b.fallback_text || '',ctx,path + bodyKey,ctx.language==='zh'?'rich-text prose-field':undefined);
      }
      if (ctx.showSourceLinks !== false && b.source_refs && host.sourceButton) {
        if(b.source_refs.length&&host.sourceLinks)node.append(host.sourceLinks(b.source_refs));
        else if(b.source_refs.length){const refs = el('div','block-sources'); b.source_refs.forEach(ref => refs.append(host.sourceButton(ref))); node.append(refs);}
      }
      return node;
    }
    function renderDocument(parent, document, context) {
      if (!document || !Array.isArray(document.blocks)) return;
      document.blocks.forEach((b,i) => parent.append(renderBlock(b,Object.assign({},context,{previousBlock:document.blocks[i-1],path:(context && context.path || '') + i + '.',scope:(context && context.scope || 'document') + '/' + (b.id || i)}))));
      host.typeset(parent); host.onLayout();
    }
    function splitCells(line) { return line.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(s => s.trim().replace(/\\\|/g,'|')); }
    function markdownDocument(value, counter) {
      counter=counter||{next:0};
      const lines = String(value || '').split('\n'), blocks = []; let i = 0;
      const add = b => blocks.push(Object.assign({id:'md-' + counter.next++},b));
      while (i < lines.length) {
        const line = lines[i]; if (!line.trim()) { i++; continue; }
        const fence = /^\s*```(.*)$/.exec(line);
        if (fence) { const code = []; i++; while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]); if(i<lines.length)i++; add({type:'code',language:fence[1].trim(),code:code.join('\n')}); continue; }
        const display = /^\s*(\$\$|\\\[)(.*)$/.exec(line);
        if (display) {
          const close = display[1] === '$$' ? '$$' : '\\]', parts = [], start = display[2]; let end = start.indexOf(close); i++;
          if(end>=0)parts.push(start.slice(0,end));else {parts.push(start); while(i<lines.length){end=lines[i].indexOf(close);if(end>=0){parts.push(lines[i++].slice(0,end));break;}parts.push(lines[i++]);}}
          if(end>=0)add({type:'formula',latex:parts.join('\n')});else add({type:'paragraph',text:display[1]+parts.join('\n')}); continue;
        }
        const heading = /^(#{1,6})\s+(.+)$/.exec(line);
        if (heading) { add({type:'heading',level:heading[1].length,text:heading[2]}); i++; continue; }
        if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { add({type:'separator'});i++;continue; }
        if (i + 1 < lines.length && line.includes('|') && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])) {
          const rows = [splitCells(line)]; i+=2; while(i<lines.length && lines[i].trim() && lines[i].includes('|'))rows.push(splitCells(lines[i++]));add({type:'table',rows});continue;
        }
        const image = /^\s*!\[([^\]]*)\]\(([^\s)]+)\)\s*$/.exec(line);
        if (image) { add({type:'figure',src:image[2],alt:image[1],caption:image[1]});i++;continue; }
        if (/^\s*>/.test(line)) {const quote=[];while(i<lines.length&&/^\s*>/.test(lines[i]))quote.push(lines[i++].replace(/^\s*>\s?/,''));add({type:'quote',blocks:markdownDocument(quote.join('\n'),counter).blocks});continue;}
        const li = /^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);
        if (li) {const items=[],ordered=/^\s*\d/.test(line);while(i<lines.length){const item=/^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(lines[i]);if(!item)break;items.push(item[1]);i++;}add({type:'list',ordered,items});continue;}
        add({type:'paragraph',text:line});i++;
      }
      return {version:1,blocks};
    }
    function markdown(parent,value,context) {renderDocument(parent,markdownDocument(value),context || {});}
    return Object.freeze({renderBlock,renderDocument,markdown,markdownDocument,text,localAsset,imageZoom,isFigureNote});
  }
  window.ContentRenderer = Object.freeze({create,supportedTypes:SUPPORTED});
})();
