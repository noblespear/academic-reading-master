/* Fixed teaching components. Configurations are data; expressions never execute JavaScript. */
(function () {
  'use strict';
  const registry = Object.create(null);
  function evaluate(expression, values, budget) {
    budget = budget || {depth:0,nodes:0};
    if (++budget.nodes > 128 || budget.depth > 12) throw new Error('计算配置过大');
    if (typeof expression === 'number' && Number.isFinite(expression)) return expression;
    if (expression && typeof expression.parameter === 'string') {
      const n = values[expression.parameter]; if (!Number.isFinite(n)) throw new Error('参数不存在'); return n;
    }
    if (!expression || !Array.isArray(expression.args)) throw new Error('计算配置无效');
    budget.depth++; const args = expression.args.map(arg => evaluate(arg,values,budget)); budget.depth--;
    let value;
    switch(expression.op) {
      case 'add': case 'sum': value=args.reduce((a,b)=>a+b,0); break;
      case 'subtract': if(args.length!==2)throw new Error('减法需要两个量');value=args[0]-args[1];break;
      case 'multiply': value=args.reduce((a,b)=>a*b,1);break;
      case 'divide': if(args.length!==2||args[1]===0)throw new Error('分母为零时没有定义');value=args[0]/args[1];break;
      case 'power': if(args.length!==2)throw new Error('乘方需要两个量');if(Math.abs(args[1])>1024)throw new Error('指数超过演示范围');value=Math.pow(args[0],args[1]);break;
      case 'mean': if(!args.length)throw new Error('没有用于平均的量');value=args.reduce((a,b)=>a+b,0)/args.length;break;
      default: throw new Error('不支持的计算操作');
    }
    if (!Number.isFinite(value)) throw new Error('这个参数组合没有有限结果'); return value;
  }
  function cacheTrace(sequence, capacity) {
    let cache=[],hits=0;
    return sequence.map((request,i) => {
      request=String(request); const hit=cache.includes(request);let evicted=null;
      if(hit){hits++;cache=cache.filter(x=>x!==request);}else if(cache.length>=capacity)evicted=cache.shift();
      cache.push(request);return {request,hit,evicted,cache:cache.slice(),hits,total:i+1,ratio:hits/(i+1)};
    });
  }
  // Conservative interval evaluation decides whether two finite samples can be
  // joined. A denominator interval containing zero (including an unsampled
  // zero such as (x-c)^2) is deliberately left disconnected.
  function expressionRange(expression,values,parameter,lower,upper,budget) {
    budget=budget||{depth:0,nodes:0};
    if(++budget.nodes>128||budget.depth>12)return null;
    if(typeof expression==='number'&&Number.isFinite(expression))return [expression,expression];
    if(expression&&typeof expression.parameter==='string'){
      const value=values[expression.parameter];return expression.parameter===parameter?[lower,upper]:Number.isFinite(value)?[value,value]:null;
    }
    if(!expression||!Array.isArray(expression.args))return null;
    budget.depth++;const args=expression.args.map(arg=>expressionRange(arg,values,parameter,lower,upper,budget));budget.depth--;
    if(args.some(arg=>!arg))return null;
    function finite(a,b){return Number.isFinite(a)&&Number.isFinite(b)?[Math.min(a,b),Math.max(a,b)]:null;}
    function product(a,b){const candidates=[a[0]*b[0],a[0]*b[1],a[1]*b[0],a[1]*b[1]];return finite(Math.min(...candidates),Math.max(...candidates));}
    switch(expression.op){
      case 'add': case 'sum':return finite(args.reduce((sum,arg)=>sum+arg[0],0),args.reduce((sum,arg)=>sum+arg[1],0));
      case 'subtract':return args.length===2?finite(args[0][0]-args[1][1],args[0][1]-args[1][0]):null;
      case 'multiply':{let result=[1,1];for(const arg of args){result=product(result,arg);if(!result)return null;}return result;}
      case 'divide':{
        if(args.length!==2||args[1][0]<=0&&args[1][1]>=0)return null;
        const reciprocal=finite(1/args[1][1],1/args[1][0]);return reciprocal?product(args[0],reciprocal):null;
      }
      case 'mean':return args.length?finite(args.reduce((sum,arg)=>sum+arg[0],0)/args.length,args.reduce((sum,arg)=>sum+arg[1],0)/args.length):null;
      case 'power':{
        if(args.length!==2)return null;const [base,exponent]=args;
        if(exponent[0]===exponent[1]){
          const n=exponent[0];if(Math.abs(n)>1024||n<0&&base[0]<=0&&base[1]>=0||!Number.isInteger(n)&&base[0]<0)return null;
          const candidates=[Math.pow(base[0],n),Math.pow(base[1],n)];
          if(n>0&&Number.isInteger(n)&&n%2===0&&base[0]<=0&&base[1]>=0)candidates.push(0);
          return finite(Math.min(...candidates),Math.max(...candidates));
        }
        if(base[0]<=0||Math.max(Math.abs(exponent[0]),Math.abs(exponent[1]))>1024)return null;
        const candidates=[Math.pow(base[0],exponent[0]),Math.pow(base[0],exponent[1]),Math.pow(base[1],exponent[0]),Math.pow(base[1],exponent[1])];
        return finite(Math.min(...candidates),Math.max(...candidates));
      }
      default:return null;
    }
  }
  function render(aid, host) {
    const {el,button}=host, root=el('section','learning-component');
    root.dataset.aidType=aid.type; root.dataset.aidId=aid.id || '';
    if(aid.title)root.append(el('h4','learning-component-title',aid.title));
    if(aid.evidence_kind==='teaching_example')root.append(el('span','teaching-label','教学示例'));
    if(aid.evidence_kind==='paper')root.append(el('span','visual-source-label',aid.type==='method_pipeline'?'研究框架 · 按原文整理':'论文结果 · 原文数值'));
    const key=host.context.scope + '/' + (aid.id || aid.type), stored=host.studyState[key] || {}, config=aid.config || {};
    function save(value){host.studyState[key]=Object.assign({},host.studyState[key],value);host.onStudyChange(key,host.studyState[key]);host.onLayout();}
    const tools={el,button,text:host.text,root,config,stored,save,host};
    try {
      if(!registry[aid.type])throw new Error('当前模板不支持这类演示');registry[aid.type](tools);
    } catch(error) {
      root.append(el('div','content-warning',error.message));
      if(aid.fallback&&Array.isArray(aid.fallback.blocks))host.renderDocument(root,aid.fallback,{scope:key+'/fallback',path:'fallback.blocks.'});
      else root.append(el('p',null,aid.fallback_text || '请查看本段的静态说明。'));
    }
    const fallback=el('details','component-fallback');fallback.append(el('summary',null,'看文字说明'));
    if(aid.fallback&&Array.isArray(aid.fallback.blocks))host.renderDocument(fallback,aid.fallback,{scope:key+'/fallback',path:'fallback.blocks.'});
    else fallback.append(el('p',null,aid.fallback_text || '操作后显示的结果属于教学设定。'));
    root.append(fallback);
    if(host.context.showSourceLinks!==false&&Array.isArray(aid.source_refs)&&host.sourceButton){const refs=el('div','block-sources');aid.source_refs.forEach(ref=>refs.append(host.sourceButton(ref)));root.append(refs);}
    return root;
  }
  registry.concept_cards = function ({root,config,host,el,text}) {
    (config.concept_ids || []).forEach((id,i) => {
      const index=(host.catalog.concepts||[]).findIndex(c=>c.id===id),c=host.catalog.concepts[index];if(!c)throw new Error('词语说明不存在：'+id);
      const card=el('section','concept-card');text(card,c.label,'config.concept_ids.'+i+'.label','concept-label');
      text(card,c.context_meaning,'config.concept_ids.'+i+'.context_meaning');text(card,c.plain_definition,'config.concept_ids.'+i+'.plain_definition');
      if(c.example){const details=el('details');details.append(el('summary',null,'具体例子'));text(details,c.example,'config.concept_ids.'+i+'.example');card.append(details);}
      if((c.prerequisite_ids||[]).length){const prereq=el('div','concept-prerequisites','先认识：');c.prerequisite_ids.forEach(pid=>{const p=(host.catalog.concepts||[]).find(x=>x.id===pid);if(p){const jump=host.button(p.label,'concept-link',()=>{const found=Array.from(document.querySelectorAll('[data-concept-id]')).find(n=>n.dataset.conceptId===pid);if(found)found.scrollIntoView({block:'center'});});prereq.append(jump);}});card.append(prereq);}
      card.dataset.conceptId=id;root.append(card);
    });
  };
  registry.steps = function ({root,config,stored,save,el,button,text,host}) {
    const steps=config.steps || [];if(!steps.length)throw new Error('演示没有步骤');
    let index=Math.max(0,Math.min(steps.length-1,Number(stored.step ?? config.initial_step) || 0));
    const stage=el('div','step-stage'), controls=el('div','component-controls'),counter=el('span','step-counter');
    const previous=button('上一步','',()=>update(index-1)),next=button('下一步','',()=>update(index+1)),reset=button('重置','',()=>update(0));
    controls.append(previous,counter,next,reset);root.append(stage,controls);
    function update(wanted){index=Math.max(0,Math.min(steps.length-1,wanted));stage.replaceChildren();const step=steps[index];
      text(stage,step.title,'config.steps.'+index+'.title','step-title');text(stage,step.text,'config.steps.'+index+'.text');
      if(step.blocks)host.renderDocument(stage,{version:1,blocks:step.blocks},{scope:host.context.scope+'/step-'+index,path:'config.steps.'+index+'.blocks.'});
      counter.textContent=(index+1)+' / '+steps.length;previous.disabled=index===0;next.disabled=index===steps.length-1;save({step:index});host.typeset(stage);
    }update(index);
  };
  function explorer({root,config,stored,save,el,button,text,host}) {
    const parameters=config.parameters || [];if(parameters.length>8)throw new Error('参数太多');let values={};
    const output=el('output','explorer-result'),inputs=[];
    let expression=config.expression;
    if(!expression&&config.model==='ratio'){const convert=v=>typeof v==='string'?{parameter:v}:v;expression={op:'divide',args:[convert(config.numerator),convert(config.denominator)]};}
    if(config.latex){const equation=el('div','formula-display explorer-equation');equation.append(host.mathAtom(config.latex,true,config.latex));root.append(equation);}
    (config.symbols || []).forEach((symbol,i)=>text(root,String(symbol.symbol)+'：'+symbol.meaning,'config.symbols.'+i+'.meaning','symbol-explanation'));
    parameters.forEach(p=>{
      const label=el('label','parameter-control'),name=el('span',null,p.label||p.id),input=el('input'),value=el('output');
      input.type='range';input.min=String(p.min);input.max=String(p.max);input.step=String(p.step||1);
      const candidate=stored.values&&stored.values[p.id];values[p.id]=Number.isFinite(candidate)?Math.max(p.min,Math.min(p.max,candidate)):Number(p.value);
      input.value=String(values[p.id]); input.setAttribute('aria-label',p.label||p.id);label.append(name,input,value);root.append(label);inputs.push({p,input,value});
      input.addEventListener('input',()=>{values[p.id]=Number(input.value);update();});
    });
    const dependencies=new Set();
    function collect(node,depth){if(depth>12||!node||typeof node!=='object')return;if(typeof node.parameter==='string')dependencies.add(node.parameter);else if(Array.isArray(node.args))node.args.forEach(arg=>collect(arg,depth+1));}
    collect(expression,0);
    const variable=parameters.find(p=>dependencies.has(p.id)&&Number.isFinite(p.min)&&Number.isFinite(p.max)&&p.min<p.max);
    let redrawCurve=null;
    if(variable){
      root.classList.add('formula-visual');
      const figure=el('figure','formula-curve-figure'),drawing=el('div','formula-curve-drawing'),caption=el('figcaption','formula-curve-note');
      figure.append(drawing,caption);root.append(figure);
      function format(value){if(!Number.isFinite(value))return '无定义';const absolute=Math.abs(value);return absolute!==0&&(absolute>=100000||absolute<0.001)?value.toExponential(2):String(Number(value.toPrecision(5)));}
      function draw(width){
        const samples=[],segments=[];let segment=[];
        const count=240,span=variable.max-variable.min;
        for(let i=0;i<=count;i++){
          const x=variable.min+span*i/count,v=Object.assign({},values,{[variable.id]:x});let y=null;
          try{y=evaluate(expression,v);}catch(_){}
          if(y===null){if(segment.length)segments.push(segment);segment=[];samples.push({x,y:null});continue;}
          const previous=samples[samples.length-1];
          if(previous&&previous.y!==null&&!expressionRange(expression,values,variable.id,previous.x,x)){
            if(segment.length)segments.push(segment);segment=[];
          }
          const sample={x,y};samples.push(sample);segment.push(sample);
        }
        if(segment.length)segments.push(segment);
        let current=null;try{current=evaluate(expression,values);}catch(_){}
        const finite=samples.filter(p=>p.y!==null).map(p=>p.y);if(Number.isFinite(current))finite.push(current);
        const fixed=parameters.filter(p=>p.id!==variable.id).map(p=>(p.label||p.id)+'='+format(values[p.id])+(p.unit?' '+p.unit:''));
        const relation='横轴改变'+(variable.label||variable.id)+(fixed.length?'；'+fixed.join('，')+'固定在当前值':'')+'。曲线按给定公式计算，不是测量数据；圆点表示当前设定。';
        caption.textContent=relation;
        if(!finite.length){drawing.replaceChildren(el('p','content-warning','当前条件下，整个横轴范围都没有有限结果。'));return;}
        const minimum=Math.min(0,...finite),maximum=Math.max(0,...finite),padding=(maximum-minimum||Math.max(Math.abs(maximum),1))*0.09;
        const yMin=minimum-padding,yMax=maximum+padding,left=66,right=21,plotWidth=Math.max(60,width-left-right),plotHeight=190;
        if(!Number.isFinite(yMin)||!Number.isFinite(yMax)||!Number.isFinite(yMax-yMin)){drawing.replaceChildren(el('p','content-warning','数值跨度超过当前图形的有限排版范围，请查看当前设定的计算结果。'));return;}
        const yLabel=(config.result_label||'结果')+(config.unit?' ('+config.unit+')':''),labelLines=visualTextLines(yLabel,width-20),top=22+labelLines.length*17,bottom=top+plotHeight;
        const xLabel=(variable.label||variable.id)+(variable.unit?' ('+variable.unit+')':''),xLines=visualTextLines(xLabel,width-24),height=bottom+48+(xLines.length-1)*17;
        const scaleX=x=>left+(x-variable.min)/span*plotWidth,scaleY=y=>top+(yMax-y)/(yMax-yMin)*plotHeight;
        const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'img',class:'formula-curve-svg','data-x-parameter':variable.id,'data-curve-segments':segments.filter(s=>s.length>1).length,'data-y-min':yMin,'data-y-max':yMax});
        svg.append(svgElement('title',{},(variable.label||variable.id)+'与'+(config.result_label||'结果')+'的关系'),svgElement('desc',{},relation+' 当前'+(variable.label||variable.id)+'='+format(values[variable.id])+'，'+(config.result_label||'结果')+'='+format(current)+'。无定义位置不连接。'));
        svgLabel(svg,yLabel,10,16,width-20,'formula-axis-title axis-title','start');
        const frame=svgElement('rect',{x:left,y:top,width:plotWidth,height:plotHeight,class:'formula-chart-frame','data-chart-frame':''});svg.append(frame);
        [minimum,minimum+(maximum-minimum)/2,maximum].filter((value,i,list)=>list.indexOf(value)===i).forEach(value=>{const y=scaleY(value);svg.append(svgElement('line',{x1:left,y1:y,x2:left+plotWidth,y2:y,class:'formula-chart-grid'}),svgElement('text',{x:left-8,y:y+4,'text-anchor':'end',class:'formula-axis-tick'},format(value)));});
        if(yMin<0&&yMax>0)svg.append(svgElement('line',{x1:left,y1:scaleY(0),x2:left+plotWidth,y2:scaleY(0),class:'formula-zero-line'}));
        [0,0.5,1].forEach(fraction=>{const value=variable.min+span*fraction,x=scaleX(value);svg.append(svgElement('line',{x1:x,y1:bottom,x2:x,y2:bottom+5,class:'formula-chart-tick'}),svgElement('text',{x,y:bottom+21,'text-anchor':fraction===0?'start':fraction===1?'end':'middle',class:'formula-axis-tick'},format(value)));});
        svgLabel(svg,xLabel,left+plotWidth/2,bottom+43,width-24,'formula-axis-title axis-title');
        svg.querySelectorAll('.axis-title').forEach((node,i)=>node.dataset.axis=i===0?'y':'x');
        const path=segments.filter(s=>s.length>1).map(points=>points.map((p,i)=>(i?'L':'M')+scaleX(p.x).toFixed(2)+' '+scaleY(p.y).toFixed(2)).join(' ')).join(' ');
        if(path)svg.append(svgElement('path',{d:path,class:'formula-function-line','data-formula-curve':''}));
        if(Number.isFinite(current)){
          const x=scaleX(values[variable.id]),y=scaleY(current);
          svg.append(svgElement('line',{x1:x,y1:y,x2:x,y2:bottom,class:'formula-current-guide'}));
          const point=svgElement('circle',{cx:x,cy:y,r:5,class:'formula-current-point','data-current-x':values[variable.id],'data-current-y':current});point.append(svgElement('title',{},'当前设定：'+format(values[variable.id])+'，结果 '+format(current)));svg.append(point);
        }
        drawing.replaceChildren(svg);root.dataset.curveParameter=variable.id;
      }
      redrawCurve=responsiveVisual(drawing,draw,host);
    }else if(parameters.length)root.append(el('p','formula-curve-note','这些滑块未对应公式中具有变化范围的参数，因此不绘制函数曲线。'));
    output.setAttribute('aria-live','polite');
    root.append(output,button('重置','component-reset',()=>{inputs.forEach(({p,input})=>{input.value=String(p.value);values[p.id]=Number(p.value);});update();}));
    function update(){inputs.forEach(({p,value})=>value.textContent=String(values[p.id])+(p.unit?' '+p.unit:''));
      try {
        const result=evaluate(expression,values);output.textContent=(config.result_label||'结果')+'：'+String(Number(result.toPrecision(6)))+(config.unit?' '+config.unit:'');output.dataset.result=String(result);output.classList.remove('result-error');
      }catch(error){output.textContent=error.message;delete output.dataset.result;output.classList.add('result-error');}
      if(redrawCurve)redrawCurve();save({values:Object.assign({},values)});
    }update();
  }
  registry.formula_explorer=explorer;registry.parameter_explorer=explorer;
  registry.prediction=function({root,config,stored,save,el,button,text}) {
    text(root,config.question,'config.question','prediction-question');const choices=el('div','prediction-choices'),feedback=el('div','prediction-feedback');feedback.setAttribute('aria-live','polite');
    let choice=stored.choice || null,revealed=!!stored.revealed;const buttons=[];
    (config.choices||[]).forEach((option,i)=>{
      const b=button(option.text || option.label,'',()=>{choice=option.id;revealed=false;update();});b.dataset.choiceId=option.id;buttons.push(b);choices.append(b);
    });const reveal=button('看解释','',()=>{if(choice){revealed=true;update();}}),reset=button('重置','',()=>{choice=null;revealed=false;update();});
    root.append(choices,reveal,reset,feedback);
    function update(){buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.choiceId===choice)));reveal.disabled=!choice;feedback.replaceChildren();
      if(revealed){const i=(config.choices||[]).findIndex(x=>x.id===choice),option=config.choices[i],correct=choice===(config.correct_choice_id || config.correct_id);
        feedback.append(el('strong',null,correct?'预测正确':'再想一步'));if(option)text(feedback,option.feedback,'config.choices.'+i+'.feedback');text(feedback,config.explanation,'config.explanation');
      }save({choice,revealed});
    }update();
  };
  registry.lru_cache=function({root,config,stored,save,el,button}) {
    const sequence=config.sequence || config.requests || [];if(!sequence.length||sequence.length>200)throw new Error('读取序列无效');
    let capacity=Math.max(config.min_capacity||1,Math.min(config.max_capacity||20,Number(stored.capacity||config.capacity)||3)),step=Math.max(0,Math.min(sequence.length-1,Number(stored.step)||0));
    const label=el('label','parameter-control'),input=el('input'),value=el('output'),stage=el('div','cache-stage'),stats=el('output','cache-stats');
    input.type='range';input.min=String(config.min_capacity||1);input.max=String(config.max_capacity||20);input.step='1';input.value=String(capacity);input.setAttribute('aria-label','缓存容量');label.append(el('span',null,'容量'),input,value);
    const controls=el('div','component-controls'),previous=button('上一步','',()=>{step--;update();}),next=button('下一步','',()=>{step++;update();});
    controls.append(previous,next,button('重置','',()=>{step=0;capacity=config.capacity||3;input.value=String(capacity);update();}));root.append(label,stage,stats,controls);
    input.addEventListener('input',()=>{capacity=Number(input.value);update();});
    function update(){step=Math.max(0,Math.min(sequence.length-1,step));const trace=cacheTrace(sequence,capacity),row=trace[step];
      value.textContent=capacity+' 项';stage.replaceChildren(el('p',null,'第 '+(step+1)+' / '+sequence.length+' 次：读取 '+row.request+'，'+(row.hit?'命中':'未命中')+(row.evicted?'，移出 '+row.evicted:'')));
      const slots=el('div','cache-slots');row.cache.forEach((item,i)=>slots.append(el('span',null,item+(i===row.cache.length-1?' · 最近使用':''))));stage.append(slots);
      stats.textContent='命中 '+row.hits+' / '+row.total+'，比例 '+(100*row.ratio).toFixed(2)+'%';stats.dataset.hits=String(row.hits);stats.dataset.total=String(row.total);stats.dataset.ratio=String(row.ratio);previous.disabled=step===0;next.disabled=step===sequence.length-1;save({capacity,step});
    }update();
  };
  // Visual mechanisms are a bounded, declarative graph/table contract. Paper data
  // supplies labels and observations, never markup, event handlers, or source code.
  let visualSerial=0;
  const svgNS='http://www.w3.org/2000/svg';
  function svgElement(tag,attributes,text) {
    const node=document.createElementNS(svgNS,tag);
    Object.entries(attributes||{}).forEach(([name,value])=>node.setAttribute(name,String(value)));
    if(text!==undefined)node.textContent=String(text);return node;
  }
  function visualList(value,label,max,allowEmpty) {
    if(!Array.isArray(value)||value.length>max||(!allowEmpty&&!value.length))throw new Error(label+'的数量无效');return value;
  }
  function visualString(value,label,max) {
    if(typeof value!=='string'||!value.trim()||value.length>(max||200))throw new Error(label+'无效');return value;
  }
  function indexedVisualItems(items,label,max) {
    const map=new Map();visualList(items,label,max).forEach(item=>{
      if(!item||typeof item!=='object')throw new Error(label+'条目无效');
      const id=visualString(item.id,label+'标识',160);if(map.has(id))throw new Error(label+'标识重复');
      visualString(item.label,label+'名称',80);map.set(id,item);
    });return map;
  }
  function visualIds(ids,map,label) {
    const set=new Set();visualList(ids,label,map.size,true).forEach(id=>{
      if(!map.has(id)||set.has(id))throw new Error(label+'引用不存在或重复');set.add(id);
    });return set;
  }
  function visualTextLines(label,maxWidth) {
    const lines=[];let line='',width=0;
    Array.from(String(label)).forEach(char=>{const size=/[^\x00-\xff]/.test(char)?13:7;
      if(width+size>maxWidth&&line){lines.push(line);line='';width=0;}line+=char;width+=size;
    });if(line)lines.push(line);return lines;
  }
  function svgLabel(svg,label,x,y,maxWidth,cls,anchor) {
    // The SVG remains text, including labels such as "<script>".
    const lines=visualTextLines(label,maxWidth),node=svgElement('text',{x,y,'text-anchor':anchor||'middle',class:cls||'visual-label'});
    lines.forEach((part,i)=>node.append(svgElement('tspan',{x,dy:i?17:0},part)));
    svg.append(node);return lines.length;
  }
  function responsiveVisual(container,draw,host) {
    let previous=0;
    function redraw(){const width=Math.max(160,Math.round(container.getBoundingClientRect().width||600));
      if(width!==previous){previous=width;draw(width);host.onLayout();}}
    if(typeof ResizeObserver==='function') {
      const observer=new ResizeObserver(()=>{if(container.isConnected)redraw();else observer.disconnect();});observer.observe(container);
    }
    redraw();return ()=>draw(previous||600);
  }
  registry.method_pipeline=function({root,config,stored,save,el,button,text,host}) {
    const idPattern=/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
    function fields(value,allowed,label){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!allowed.includes(key)))throw new Error(label+'含未登记字段');}
    fields(config,['summary','nodes','edges','reading_order'],'方法流水线');visualString(config.summary,'全图说明',4000);
    const nodes=indexedVisualItems(config.nodes,'阶段',40),edges=new Map(),cells=new Set(),pairs=new Set();
    if(nodes.size<2)throw new Error('方法图至少需要两个阶段');
    nodes.forEach(node=>{
      fields(node,['id','label','summary','inputs','outputs','role','why_needed','handoff','column','row'],'阶段');
      if(!idPattern.test(node.id)||!Number.isInteger(node.column)||node.column<0||node.column>39||!Number.isInteger(node.row)||node.row<0||node.row>39)throw new Error('阶段标识或网格位置无效');
      ['summary','inputs','outputs'].forEach(key=>visualString(node[key],'阶段说明',4000));
      ['role','why_needed','handoff'].forEach(key=>{if(node[key]!==undefined)visualString(node[key],'阶段补充',4000);});
      const cell=node.column+','+node.row;if(cells.has(cell))throw new Error('两个阶段不能占同一位置');cells.add(cell);
    });
    visualList(config.edges,'方法关系',120).forEach(edge=>{
      fields(edge,['id','from','to','kind','label','flow','cause'],'关系');
      if(!idPattern.test(edge.id)||edges.has(edge.id)||!nodes.has(edge.from)||!nodes.has(edge.to)||edge.from===edge.to)throw new Error('方法关系引用无效');
      if(!['flow','branch','feedback','dependency'].includes(edge.kind))throw new Error('关系需要区分流程、分支、反馈或依赖');visualString(edge.label,'关系说明',240);
      ['flow','cause'].forEach(key=>visualString(edge[key],'关系详解',4000));
      const pair=JSON.stringify([edge.from,edge.to]);if(pairs.has(pair))throw new Error('有向关系重复');pairs.add(pair);edges.set(edge.id,edge);
    });
    const order=config.reading_order||Array.from(nodes.keys());
    if(!Array.isArray(order)||order.length!==nodes.size||new Set(order).size!==nodes.size||order.some(id=>!nodes.has(id)))throw new Error('阅读顺序须包含每个阶段一次');
    const kinds={flow:'流程',branch:'分支',feedback:'反馈',dependency:'依赖'},name=id=>nodes.get(id).label;
    let selected=nodes.has(stored.selected_node_id)?stored.selected_node_id:null;
    let selectedEdge=edges.has(stored.selected_edge_id)?stored.selected_edge_id:null;if(selectedEdge)selected=null;
    root.classList.add('learning-visual','method-pipeline');
    const figure=el('figure','method-pipeline-figure'),drawing=el('div','method-pipeline-drawing'),legend=el('figcaption','method-pipeline-legend');
    Object.entries(kinds).forEach(([kind,label])=>legend.append(el('span','method-relation-key method-relation-'+kind,label)));
    figure.append(drawing,legend);
    const controls=el('div','method-pipeline-controls'),previous=button('上一节点','visual-stage-button',()=>move(-1)),next=button('下一节点','visual-stage-button',()=>move(1)),reset=button('返回全貌','visual-stage-button',()=>update(null,true));
    const status=el('output','method-pipeline-status');status.setAttribute('aria-live','polite');controls.append(previous,next,reset,status);
    const description=el('section','method-pipeline-description');description.setAttribute('aria-label','所选节点或关系说明');description.setAttribute('aria-live','polite');description.tabIndex=0;
    const relations=el('details','method-pipeline-relations'),relationList=el('ol'),relationButtons=new Map();relations.append(el('summary',null,'选择关系，查看关联内容与理由'),relationList);
    edges.forEach((edge,id)=>{
      const li=el('li');li.dataset.edgeId=id;
      const choice=button(name(edge.from)+' → '+name(edge.to)+' · '+kinds[edge.kind]+'：'+edge.label,'method-pipeline-edge-choice',()=>update(null,true,id));
      choice.dataset.edgeId=id;choice.setAttribute('aria-label','关系：'+name(edge.from)+' → '+name(edge.to));choice.setAttribute('aria-pressed',String(id===selectedEdge));
      choice.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();update(null,true);}});
      li.append(choice);relationList.append(li);relationButtons.set(id,choice);
    });
    const readingNote=el('p','method-pipeline-reading-note');root.append(figure,controls,description,readingNote,relations);
    const markerId='method-arrow-'+(++visualSerial),nodeElements=new Map(),edgeElements=new Map();
    function move(delta){const current=selected===null?-1:order.indexOf(selected),index=Math.max(0,Math.min(order.length-1,current+delta));update(order[index],true);}
    function focusNode(id){const group=nodeElements.get(id);if(group)group.focus();}
    // Route through a rectilinear visibility grid. Node rectangles (including
    // unrelated intermediate modules) are obstacles; only the final short
    // port stubs touch the selected endpoints. A small A* heap keeps wider
    // author grids bounded, with a turn cost to prefer readable polylines.
    function routeCoordinate(value){return Math.round(value*10000)/10000;}
    function routeBetween(start,end,boxes,width,height,occupied){
      const xs=[8,width-8,routeCoordinate(start.x),routeCoordinate(end.x)],ys=[8,height-8,routeCoordinate(start.y),routeCoordinate(end.y)];
      // Previously routed segments become additional visibility-grid
      // coordinates.  Without their endpoints in the grid, a candidate
      // segment could jump over the occupied interval and silently share a
      // long horizontal/vertical stroke with another semantic edge.
      const addLanes=(values,coordinate,limit)=>[-8,8].forEach(offset=>{const lane=routeCoordinate(coordinate+offset);if(lane>=8&&lane<=limit-8)values.push(lane);});
      if(occupied){
        // Keep a small set of neighboring lanes around every reserved line.
        // Endpoints alone are insufficient: when a port or an earlier edge
        // occupies the only obstacle-free lane, a later edge must be able to
        // step around it without sharing the same stroke.
        occupied.horizontal.forEach((intervals,y)=>{ys.push(routeCoordinate(y));addLanes(ys,y,height);intervals.forEach(([left,right])=>{xs.push(routeCoordinate(left),routeCoordinate(right));if(right-left<=40){addLanes(xs,left,width);addLanes(xs,right,width);}});});
        occupied.vertical.forEach((intervals,x)=>{xs.push(routeCoordinate(x));addLanes(xs,x,width);intervals.forEach(([top,bottom])=>{ys.push(routeCoordinate(top),routeCoordinate(bottom));if(bottom-top<=40){addLanes(ys,top,height);addLanes(ys,bottom,height);}});});
      }
      boxes.forEach(p=>{
        xs.push(routeCoordinate(p.left-12),routeCoordinate(p.right+12));ys.push(routeCoordinate(p.top-12),routeCoordinate(p.bottom+12));
        if(p.isPort){
          xs.push(routeCoordinate(p.left),routeCoordinate(p.right));ys.push(routeCoordinate(p.top),routeCoordinate(p.bottom));
          addLanes(xs,p.left,width);addLanes(xs,p.right,width);addLanes(ys,p.top,height);addLanes(ys,p.bottom,height);
        }
      });
      const unique=values=>Array.from(new Set(values.map(routeCoordinate))).filter(n=>n>=0).sort((a,b)=>a-b),xx=unique(xs),yy=unique(ys),nx=xx.length;
      const sx=xx.indexOf(routeCoordinate(start.x)),sy=yy.indexOf(routeCoordinate(start.y)),tx=xx.indexOf(routeCoordinate(end.x)),ty=yy.indexOf(routeCoordinate(end.y));
      function overlaps(left,right,otherLeft,otherRight){return Math.min(right,otherRight)-Math.max(left,otherLeft)>0.01;}
      function occupiedBy(a,b){
        if(!occupied)return false;
        if(a.x===b.x){
          const top=Math.min(a.y,b.y),bottom=Math.max(a.y,b.y);
          return Array.from(occupied.vertical.entries()).some(([x,intervals])=>Math.abs(x-a.x)<6&&intervals.some(([from,to])=>overlaps(top,bottom,from,to)));
        }
        const left=Math.min(a.x,b.x),right=Math.max(a.x,b.x);
        return Array.from(occupied.horizontal.entries()).some(([y,intervals])=>Math.abs(y-a.y)<6&&intervals.some(([from,to])=>overlaps(left,right,from,to)));
      }
      function clear(a,b){return !boxes.some(p=>{
        const margin=p.isPort?0.01:8;
        return a.x===b.x? a.x>p.left-margin&&a.x<p.right+margin&&Math.max(a.y,b.y)>p.top-margin&&Math.min(a.y,b.y)<p.bottom+margin : a.y>p.top-margin&&a.y<p.bottom+margin&&Math.max(a.x,b.x)>p.left-margin&&Math.min(a.x,b.x)<p.right+margin;
      })&&!occupiedBy(a,b);}
      const heap=[],cost=new Map(),parents=new Map();
      function push(item){heap.push(item);let i=heap.length-1;while(i){const p=(i-1)>>1;if(heap[p].f<=item.f)break;heap[i]=heap[p];i=p;}heap[i]=item;}
      function pop(){const first=heap[0],last=heap.pop();if(heap.length){let i=0;while(i*2+1<heap.length){let child=i*2+1;if(child+1<heap.length&&heap[child+1].f<heap[child].f)child++;if(last.f<=heap[child].f)break;heap[i]=heap[child];i=child;}heap[i]=last;}return first;}
      const initial=(sy*nx+sx)*3;cost.set(initial,0);push({x:sx,y:sy,dir:0,key:initial,g:0,f:Math.abs(start.x-end.x)+Math.abs(start.y-end.y)});
      let finish=null,expanded=0;
      const maxExpanded=Math.min(nx*yy.length*3,150000);
      while(heap.length){if(++expanded>maxExpanded)throw new Error('方法图搜索空间超过当前画布上限');const current=pop();if(current.g!==cost.get(current.key))continue;if(current.x===tx&&current.y===ty){finish=current.key;break;}
        [[current.x-1,current.y,1],[current.x+1,current.y,1],[current.x,current.y-1,2],[current.x,current.y+1,2]].forEach(([x,y,dir])=>{
          if(x<0||x>=nx||y<0||y>=yy.length)return;const a={x:xx[current.x],y:yy[current.y]},b={x:xx[x],y:yy[y]};if(!clear(a,b))return;
          const key=(y*nx+x)*3+dir,g=current.g+Math.abs(a.x-b.x)+Math.abs(a.y-b.y)+(current.dir&&current.dir!==dir?18:0);
          if(g>=(cost.get(key)??Infinity))return;cost.set(key,g);parents.set(key,current.key);push({x,y,dir,key,g,f:g+Math.abs(b.x-end.x)+Math.abs(b.y-end.y)});
        });
      }
      if(finish===null)throw new Error('方法图无法在模块外完成布线，请调整显式网格');
      const points=[];for(let key=finish;key!==undefined;key=parents.get(key)){const cell=Math.floor(key/3);points.push({x:xx[cell%nx],y:yy[Math.floor(cell/nx)]});}points.reverse();return points;
    }
    function simplify(points){const distinct=points.filter((p,i)=>!i||p.x!==points[i-1].x||p.y!==points[i-1].y);return distinct.filter((p,i)=>!i||i===distinct.length-1||!(distinct[i-1].x===p.x&&p.x===distinct[i+1].x||distinct[i-1].y===p.y&&p.y===distinct[i+1].y));}
    function polylineLength(points){let length=0;for(let i=1;i<points.length;i++)length+=Math.hypot(points[i].x-points[i-1].x,points[i].y-points[i-1].y);return length;}
    function draw(width){
      nodeElements.clear();edgeElements.clear();
      // The author grid remains the source of truth. At a narrow width a map
      // may be turned so that its author columns become vertical stages and
      // its author rows remain parallel lanes. This changes presentation only:
      // every edge still uses its declared from/to direction and kind.
      const authorColumns=Math.max(...Array.from(nodes.values()).map(n=>n.column))+1;
      const authorRows=Math.max(...Array.from(nodes.values()).map(n=>n.row))+1;
      const margin=Math.max(12,Math.min(30,Math.round(width*0.04))),available=Math.max(120,width-margin*2);
      const explicitCell=available/authorColumns,turnedCell=available/authorRows;
      const readableCell=width<420?92:108;
      const turned=explicitCell<readableCell&&turnedCell>explicitCell;
      const layoutMode=turned?'turned':'explicit',columns=turned?authorRows:authorColumns,rowCount=turned?authorColumns:authorRows;
      // Leave a wider gutter between adjacent modules.  The router uses the
      // gutter for independent lanes when several relations enter or leave
      // the same pair of modules; a 24px gutter put every port on one line.
      const cellWidth=available/columns,nodeWidth=Math.max(44,Math.min(168,cellWidth-32)),positions=new Map();
      const feedbackCount=Array.from(edges.values()).filter(edge=>edge.kind==='feedback').length;
      const nodeHeight=Math.max(54,...Array.from(nodes.values()).map(n=>visualTextLines(n.label,Math.max(28,nodeWidth-18)).length*18+22));
      const gap=Math.max(38,Math.min(58,width<420?44:54)),feedbackLaneGap=20;
      // Keep every feedback lane above the first node's obstacle margin so
      // its top-port stub cannot run through that node on a narrow map.
      const top=feedbackCount?24+(feedbackCount-1)*feedbackLaneGap+Math.ceil(nodeHeight/2)+12:28;
      const height=Math.ceil(top+rowCount*nodeHeight+Math.max(0,rowCount-1)*gap+28);
      nodes.forEach((node,id)=>{
        const column=turned?node.row:node.column,row=turned?node.column:node.row;
        positions.set(id,{column,row,authorColumn:node.column,authorRow:node.row});
      });
      positions.forEach(p=>{p.x=margin+(p.column+.5)*cellWidth;p.y=top+p.row*(nodeHeight+gap)+nodeHeight/2;p.left=p.x-nodeWidth/2;p.right=p.x+nodeWidth/2;p.top=p.y-nodeHeight/2;p.bottom=p.y+nodeHeight/2;});
      const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'img',class:'method-pipeline-svg','aria-label':'完整研究框架的有向关系'}),nodeLayer=el('div','method-pipeline-node-layer');nodeLayer.setAttribute('role','group');nodeLayer.setAttribute('aria-label','点击研究节点');
      svg.append(svgElement('title',{},'完整研究框架'),svgElement('desc',{},config.summary+' 连线方向及类型依照论文实际的研究关系。'));
      const defs=svgElement('defs'),marker=svgElement('marker',{id:markerId,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto'});marker.append(svgElement('path',{d:'M 0 1 L 9 5 L 0 9 Z',fill:'context-stroke'}));defs.append(marker);svg.append(defs);
      const edgePorts=new Map(),portGroups=new Map(),portSlots=new Map();
      function addPortGroup(nodeId,side,id,role,anchor,distance){const key=nodeId+'|'+side;if(!portGroups.has(key))portGroups.set(key,[]);portGroups.get(key).push({id,role,anchor,distance,order:portGroups.get(key).length});}
      edges.forEach((edge,id)=>{
        const a=positions.get(edge.from),b=positions.get(edge.to);
        const distance=Math.abs(a.x-b.x)+Math.abs(a.y-b.y);
        if(edge.kind==='feedback'){
          const sideLoop=turned&&a.column===b.column&&a.row!==b.row,side='right';
          edgePorts.set(id,{sourceSide:sideLoop?side:'top',targetSide:sideLoop?side:'top',sideLoop,side});
          addPortGroup(edge.from,sideLoop?side:'top',id,'source',b,distance);addPortGroup(edge.to,sideLoop?side:'top',id,'target',a,distance);
        }else{
          // A cross-row edge leaves through the vertical side of its source
          // and enters the vertical side of its target. Same-row edges keep
          // their left/right ports. This preserves displayed flow direction
          // and gives cross-row branches independent vertical entry segments.
          const horizontal=a.row===b.row,forward=horizontal?b.x>a.x:b.y>a.y;
          const sourceSide=horizontal?(forward?'right':'left'):(forward?'bottom':'top'),targetSide=horizontal?(forward?'left':'right'):(forward?'top':'bottom');
          edgePorts.set(id,{sourceSide,targetSide});
          addPortGroup(edge.from,sourceSide,id,'source',b,distance);addPortGroup(edge.to,targetSide,id,'target',a,distance);
        }
      });
      // Put ports in the geometric order of their opposite endpoint.  This
      // keeps two branches leaving the same side from crossing immediately;
      // the occupancy-aware router below then reserves the short branch's
      // channel before routing the longer dependency.
      portGroups.forEach((entries,key)=>{
        const side=key.slice(key.indexOf('|')+1),horizontal=side==='top'||side==='bottom';
        entries.sort((left,right)=>{
          const a=horizontal?left.anchor.x:left.anchor.y,b=horizontal?right.anchor.x:right.anchor.y;
          return a-b||left.distance-right.distance||left.order-right.order;
        });
        entries.forEach((entry,index)=>portSlots.set(entry.id+'|'+entry.role,{index,count:entries.length}));
      });
      function portSlot(id,role){return portSlots.get(id+'|'+role)||{index:0,count:1};}
      function port(p,side,arrow,slot){
        const offset=arrow?3:0,along=slot.count>1?(slot.index/(slot.count-1)-.5)*(side==='top'||side==='bottom'?Math.max(0,p.right-p.left-24):Math.max(0,p.bottom-p.top-20)):0;
        const point=(x,y)=>({x:routeCoordinate(x),y:routeCoordinate(y)});
        return side==='right'?{stub:point(p.right+offset,p.y+along),outer:point(p.right+12,p.y+along)}:side==='left'?{stub:point(p.left-offset,p.y+along),outer:point(p.left-12,p.y+along)}:side==='top'?{stub:point(p.x+along,p.top-offset),outer:point(p.x+along,p.top-12)}:{stub:point(p.x+along,p.bottom+offset),outer:point(p.x+along,p.bottom+12)};
      }
      const portGeometry=new Map(),portEntries=[];
      edges.forEach((edge,id)=>{
        const a=positions.get(edge.from),b=positions.get(edge.to),edgePort=edgePorts.get(id);
        const source=port(a,edgePort.sourceSide,false,portSlot(id,'source')),target=port(b,edgePort.targetSide,true,portSlot(id,'target'));
        source.side=edgePort.sourceSide;target.side=edgePort.targetSide;
        portGeometry.set(id,{source,target});
        portEntries.push({id,role:'source',port:source,node:a,distance:Math.abs(a.x-b.x)+Math.abs(a.y-b.y),order:0});
        portEntries.push({id,role:'target',port:target,node:b,distance:Math.abs(a.x-b.x)+Math.abs(a.y-b.y),order:1});
      });
      // Adjacent nodes can put one edge's source outer point on another
      // edge's target outer point in the middle of their gap. Keep a direct
      // edge's own pair together, but nudge later, longer-edge endpoints by
      // a small bounded amount so unrelated relationships never form a
      // misleading shared junction.
      const acceptedPortPoints=new Set();
      const pointKey=point=>routeCoordinate(point.x)+','+routeCoordinate(point.y);
      const shiftPort=(entry,delta)=>{
        const vertical=entry.port.side==='left'||entry.port.side==='right';
        [entry.port.stub,entry.port.outer].forEach(point=>{
          if(vertical)point.y=routeCoordinate(point.y+delta);else point.x=routeCoordinate(point.x+delta);
        });
      };
      portEntries.sort((left,right)=>left.distance-right.distance||left.id.localeCompare(right.id)||left.order-right.order).forEach(entry=>{
        const original=pointKey(entry.port.outer),sameEdge=acceptedPortPoints.has(entry.id+'|'+original);
        if(!sameEdge&&acceptedPortPoints.has(original)){
          const vertical=entry.port.side==='left'||entry.port.side==='right',minimum=vertical?entry.node.top+5:entry.node.left+5,maximum=vertical?entry.node.bottom-5:entry.node.right-5;
          for(const delta of [-4,4,-8,8,-12,12]){
            const coordinate=vertical?entry.port.outer.y+delta:entry.port.outer.x+delta;
            if(coordinate<minimum||coordinate>maximum)continue;
            shiftPort(entry,delta);
            if(!acceptedPortPoints.has(pointKey(entry.port.outer)))break;
            shiftPort(entry,-delta);
          }
        }
        acceptedPortPoints.add(pointKey(entry.port.outer));acceptedPortPoints.add(entry.id+'|'+pointKey(entry.port.outer));
      });
      function routingBoxes(edgeId){
        const boxes=Array.from(positions.values());
        // Treat every other port exit as a tiny obstacle. This stops a long
        // route from passing through a future port and trapping that edge
        // between a node and an already occupied channel. The current edge's
        // own endpoints remain available to routePart.
        portGeometry.forEach((geometry,id)=>{
          if(id===edgeId)return;
          [geometry.source.outer,geometry.target.outer].forEach(point=>boxes.push({left:point.x-0.01,right:point.x+0.01,top:point.y-0.01,bottom:point.y+0.01,isPort:true}));
        });
        return boxes;
      }
      let feedbackLane=0;const hitEntries=[],routePoints=new Map();
      const occupied={horizontal:new Map(),vertical:new Map()};
      function reserveSegment(a,b){
        if(a.x===b.x&&a.y===b.y)return;
        const map=a.x===b.x?occupied.vertical:occupied.horizontal,coordinate=routeCoordinate(a.x===b.x?a.x:a.y),left=a.x===b.x?Math.min(a.y,b.y):Math.min(a.x,b.x),right=a.x===b.x?Math.max(a.y,b.y):Math.max(a.x,b.x);
        if(!map.has(coordinate))map.set(coordinate,[]);map.get(coordinate).push([left,right]);
      }
      function reservePoints(points){for(let i=1;i<points.length;i++)reserveSegment(points[i-1],points[i]);}
      function addPortReservations(source,target){reserveSegment(source.stub,source.outer);reserveSegment(target.outer,target.stub);}
      function routePart(start,end,boxes){const part=simplify(routeBetween(start,end,boxes,width,height,occupied));reservePoints(part);return part;}
      function routeEdge(edge){
        try{
          const id=edge.id,boxes=routingBoxes(id);let points;
          const edgePort=edgePorts.get(id),geometry=portGeometry.get(id),source=geometry.source,target=geometry.target;
          if(edge.kind==='feedback'){
            if(edgePort.sideLoop){
              // A turned map can put a feedback source and target in one visual
              // column. Route that loop around the right canvas margin so its
              // vertical leg is single and clear instead of retracing a top
              // lane at the same x-coordinate.
              const laneX=width-20,sourceLane={x:laneX,y:source.outer.y},targetLane={x:laneX,y:target.outer.y};
              const first=routePart(source.outer,sourceLane,boxes),second=routePart(sourceLane,targetLane,boxes),third=routePart(targetLane,target.outer,boxes);
              points=[source.stub,...first,...second.slice(1),...third.slice(1),target.stub];
            }else{
              const lane=24+(feedbackLane++)*20,left={x:source.outer.x,y:lane},right={x:target.outer.x,y:lane};
              const first=routePart(source.outer,left,boxes),bridge=routePart(left,right,boxes),second=routePart(right,target.outer,boxes);
              points=[source.stub,...first,...bridge.slice(1),...second.slice(1),target.stub];
            }
          }else{
            const middle=routePart(source.outer,target.outer,boxes);points=[source.stub,...middle,target.stub];
          }
          return simplify(points);
        }catch(error){throw new Error('方法图边 '+edge.id+' 无法布线：'+error.message);}
      }
      // Route short, local relationships first. Long dependencies and
      // branches then have to find a separate channel around those direct
      // paths, while the original declaration order remains the render order.
      const routeOrder=Array.from(edges.values()).sort((left,right)=>{
        const a=positions.get(left.from),b=positions.get(left.to),c=positions.get(right.from),d=positions.get(right.to);
        const distance=({from,to})=>Math.abs(from.x-to.x)+Math.abs(from.y-to.y);
        return distance({from:a,to:b})-distance({from:c,to:d})||left.id.localeCompare(right.id);
      });
      // All endpoint stubs are fixed geometry. Reserve them before routing
      // any middle section so a later edge cannot claim a visible stub as its
      // own channel and create a misleading junction.
      edges.forEach(edge=>{const geometry=portGeometry.get(edge.id);addPortReservations(geometry.source,geometry.target);});
      routeOrder.forEach(edge=>routePoints.set(edge.id,routeEdge(edge)));
      edges.forEach((edge,id)=>{
        const points=routePoints.get(id),path=points.map((p,i)=>(i?'L ':'M ')+p.x+' '+p.y).join(' ');
        const line=svgElement('path',{d:path,class:'method-pipeline-edge method-pipeline-edge-'+edge.kind,'marker-end':'url(#'+markerId+')','data-edge-id':id,'data-from':edge.from,'data-to':edge.to,'data-kind':edge.kind});
        const title=name(edge.from)+' → '+name(edge.to)+' · '+kinds[edge.kind]+'：'+edge.label;
        line.append(svgElement('title',{},title));
        const hit=svgElement('path',{d:path,class:'method-pipeline-edge-hit','data-edge-id':id,'aria-hidden':'true'});hit.append(svgElement('title',{},title));
        hit.addEventListener('click',()=>update(null,true,id));hit.addEventListener('pointerenter',()=>line.classList.add('is-hovered'));hit.addEventListener('pointerleave',()=>line.classList.remove('is-hovered'));
        // Keep visible routes in declaration order. Transparent hit paths are
        // appended after all visible routes, longest first, so a short branch
        // gets the final paint order at a shared port without changing edges,
        // arrows, or the declared topology.
        svg.append(line);hitEntries.push({hit,length:polylineLength(points),order:hitEntries.length});edgeElements.set(id,line);
        // Labels and explanations belong to the panel below the graph,
        // the edge tooltip and its keyboard list, rather than routed SVG text.
      });
      hitEntries.sort((a,b)=>b.length-a.length||a.order-b.order).forEach(({hit})=>svg.append(hit));
      nodes.forEach((node,id)=>{
        const p=positions.get(id),group=button(node.label,'method-pipeline-node',()=>update(id,true));group.dataset.nodeId=id;group.setAttribute('aria-pressed',String(id===selected));
        Object.assign(group.style,{left:(p.x-nodeWidth/2)+'px',top:(p.y-nodeHeight/2)+'px',width:nodeWidth+'px',height:nodeHeight+'px'});
        group.addEventListener('keydown',event=>{
          if(['ArrowRight','ArrowDown','ArrowLeft','ArrowUp'].includes(event.key)){event.preventDefault();const index=order.indexOf(id),delta=['ArrowRight','ArrowDown'].includes(event.key)?1:-1;focusNode(order[Math.max(0,Math.min(order.length-1,index+delta))]);}
          else if(event.key==='Escape'){event.preventDefault();update(null,true);}
        });nodeLayer.append(group);nodeElements.set(id,group);
      });
      const canvas=el('div','method-pipeline-canvas');canvas.style.width='100%';canvas.style.height=height+'px';canvas.dataset.layoutMode=layoutMode;canvas.append(svg,nodeLayer);drawing.replaceChildren(canvas);highlight();root.dataset.layoutColumns=String(authorColumns);root.dataset.layoutRows=String(authorRows);root.dataset.visualColumns=String(columns);root.dataset.visualRows=String(rowCount);root.dataset.layoutMode=layoutMode;root.dataset.mapWidth=String(width);root.dataset.scrollable='false';
      readingNote.textContent='点击节点看概要、依据或输入、结果或输出；点击连线看关联内容与理由，也可展开关系列表选择。上一/下一按阅读顺序浏览。'+(turned?'窄屏按原网格方向纵向重排，分支、汇合和反馈仍按登记关系连线。':'');
    }
    function highlight(){
      const related=new Set();edges.forEach((edge,id)=>{const active=selectedEdge!==null?id===selectedEdge:selected!==null&&(edge.from===selected||edge.to===selected);edgeElements.get(id)?.classList.toggle('is-related',active);edgeElements.get(id)?.classList.toggle('is-selected',id===selectedEdge);if(active){related.add(edge.from);related.add(edge.to);}});
      nodeElements.forEach((group,id)=>{group.classList.toggle('is-selected',id===selected);group.classList.toggle('is-related',id!==selected&&related.has(id));group.setAttribute('aria-pressed',String(id===selected));});
      relationButtons.forEach((choice,id)=>{choice.setAttribute('aria-pressed',String(id===selectedEdge));choice.closest('li').classList.toggle('is-related',selectedEdge!==null?id===selectedEdge:selected!==null&&(edges.get(id).from===selected||edges.get(id).to===selected));});
    }
    function update(id,userAction,edgeId){
      selectedEdge=edges.has(edgeId)?edgeId:null;selected=selectedEdge===null&&nodes.has(id)?id:null;root.dataset.selectedNodeId=selected||'';root.dataset.selectedEdgeId=selectedEdge||'';description.replaceChildren();description.scrollTop=0;
      function detail(field,label,value,path){const p=el('div','method-pipeline-field method-pipeline-'+field);p.append(el('strong',null,label+'：'));text(p,value,path);description.append(p);}
      if(selected===null&&selectedEdge===null){text(description,config.summary,'config.summary','method-pipeline-summary');status.textContent='完整框架';}
      else if(selectedEdge!==null){
        const edge=edges.get(selectedEdge),index=Array.from(edges.keys()).indexOf(selectedEdge);description.append(el('h5',null,name(edge.from)+' → '+name(edge.to)+' · '+kinds[edge.kind]));
        detail('label','关系',edge.label,'config.edges.'+index+'.label');detail('flow','关联内容',edge.flow,'config.edges.'+index+'.flow');detail('cause','关系为何成立',edge.cause,'config.edges.'+index+'.cause');status.textContent='当前关系：'+name(edge.from)+' → '+name(edge.to);
      }else{
        const node=nodes.get(selected),index=Array.from(nodes.keys()).indexOf(selected);description.append(el('h5',null,node.label));
        [['summary','概要'],['inputs','依据 / 输入'],['outputs','结果 / 输出'],['role','作用'],['why_needed','为什么需要'],['handoff','如何衔接']].forEach(([field,label])=>{if(node[field]!==undefined)detail(field,label,node[field],'config.nodes.'+index+'.'+field);});
        const related=el('ul','method-pipeline-related');edges.forEach((edge,id)=>{if(edge.from===selected||edge.to===selected){const li=el('li');text(li,name(edge.from)+' → '+name(edge.to)+' · '+kinds[edge.kind]+'：'+edge.label,'config.edges.'+Array.from(edges.keys()).indexOf(id)+'.label');related.append(li);}});if(related.childElementCount)description.append(related);
        status.textContent='当前：'+node.label;
      }
      const index=selected===null?-1:order.indexOf(selected);previous.disabled=index<=0;next.disabled=index===order.length-1;reset.disabled=selected===null&&selectedEdge===null;highlight();host.typeset(description);host.onLayout();
      if(userAction)save({selected_node_id:selected||'',selected_edge_id:selectedEdge||''});
    }
    responsiveVisual(drawing,draw,host);update(selected,false,selectedEdge);
  };
  registry.weighted_degree=function({root,config,stored,save,el,button,host}) {
    function fields(value,allowed,label) {
      if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!allowed.includes(key)))throw new Error(label+'含未登记字段');
    }
    fields(config,['nodes','edges','adjustable_edge_id','selected_node_id','initial_mode','tie_break'],'加权度配置');
    const nodes=indexedVisualItems(config.nodes,'节点',4),edges=new Map(),pairs=new Set(),idPattern=/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
    if(nodes.size!==4)throw new Error('这个演示需要四个节点');
    nodes.forEach(node=>{fields(node,['id','label'],'节点');if(!idPattern.test(node.id)||node.label.length>12)throw new Error('节点需要有效标识和十二字以内的名称');});
    visualList(config.edges,'边',6).forEach(edge=>{
      fields(edge,['id','from','to','weight'],'边');
      if(typeof edge.id!=='string'||!idPattern.test(edge.id)||edges.has(edge.id)||!nodes.has(edge.from)||!nodes.has(edge.to)||edge.from===edge.to)throw new Error('边的标识或两端无效');
      const pair=JSON.stringify([edge.from,edge.to].sort());if(pairs.has(pair))throw new Error('同一对节点只画一条边');pairs.add(pair);
      if(!Number.isFinite(edge.weight)||edge.weight<0||edge.weight>1||Math.abs(edge.weight*10-Math.round(edge.weight*10))>1e-8)throw new Error('边权须为零到一之间的一位小数');
      edges.set(edge.id,edge);
    });
    if(!edges.has(config.adjustable_edge_id)||config.tie_break!=='node_order'||(config.initial_mode!==undefined&&!['all','prune_one'].includes(config.initial_mode))||(config.selected_node_id!==undefined&&!nodes.has(config.selected_node_id)))throw new Error('调节边、初始状态或教学并列规则无效');
    root.classList.add('learning-visual','weighted-degree');
    const order=Array.from(nodes.keys()),adjustable=edges.get(config.adjustable_edge_id),name=id=>nodes.get(id).label,format=n=>n.toFixed(1);
    let weight=Number.isFinite(stored.weight)?Math.round(Math.max(0,Math.min(1,stored.weight))*10)/10:adjustable.weight;
    let mode=['all','prune_one'].includes(stored.mode)?stored.mode:(config.initial_mode||'all');
    let selected=nodes.has(stored.selected_node_id)?stored.selected_node_id:(config.selected_node_id||adjustable.from),scores={},ranked=[],removed=null;
    const weightControl=el('label','degree-weight-control'),input=el('input'),weightValue=el('output');
    input.type='range';input.min='0';input.max='1';input.step='0.1';input.setAttribute('aria-label','调节 '+name(adjustable.from)+' → '+name(adjustable.to)+' 的边权');
    weightControl.append(el('span',null,name(adjustable.from)+' → '+name(adjustable.to)+' 的边权'),weightValue,input);
    const controls=el('div','degree-controls'),allButton=button('全部出场','visual-stage-button',()=>{mode='all';update(true);}),pruneButton=button('按分数移除1人','visual-stage-button',()=>{mode='prune_one';update(true);});
    const selection=el('label','degree-node-select'),select=el('select');selection.append(el('span',null,'看谁的算式'),select);select.setAttribute('aria-label','看谁的算式');
    nodes.forEach(node=>{const option=el('option',null,node.label);option.value=node.id;select.append(option);});
    controls.append(allButton,pruneButton,selection);
    const figure=el('figure','visual-figure'),drawing=el('div','degree-drawing'),legend=el('figcaption','degree-legend');
    legend.append(el('span','degree-legend-in','交给选中节点'),el('span','degree-legend-out','选中节点交给别人'),el('span',null,'灰虚框：本轮退出，相关箭头已撤下'));
    figure.append(drawing,legend);
    const calculation=el('div','degree-calculation'),incoming=el('p','degree-incoming'),outgoing=el('p','degree-outgoing'),expanded=el('p','degree-expanded');calculation.append(incoming,outgoing,expanded);
    const result=el('output','degree-result');result.setAttribute('aria-live','polite');
    const rule=el('p','degree-rule','同分时，按列出顺序 '+order.map(name).join('、')+' 先移除靠前者；这是教学约定。');
    const boundary=el('p','degree-boundary','这里把筛选前整张图的相邻边权相加。退出后不重算本轮分数；分数不表示真实贡献、正确率或 token 用量。');
    root.append(weightControl,controls,figure,calculation,result,rule,boundary);
    const markerId='degree-arrow-'+(++visualSerial);
    function edgeTicks(edge){return edge.id===adjustable.id?Math.round(weight*10):Math.round(edge.weight*10);}
    function draw(width) {
      const nodeWidth=Math.min(96,width*.32-16),labelRows=Math.max(...order.map(id=>visualTextLines(name(id),nodeWidth-10).length)),nodeHeight=Math.max(66,(labelRows+2)*17+10),gap=nodeHeight+36;
      const top=nodeHeight/2+9,height=top+gap*2+nodeHeight/2+9,positions=new Map([[order[0],{x:width/2,y:top}],[order[1],{x:width*.18,y:top+gap}],[order[2],{x:width*.82,y:top+gap}],[order[3],{x:width/2,y:top+gap*2}]]);
      const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'img',class:'learning-degree-svg'});
      svg.append(svgElement('title',{},'相邻边权相加，再移除最低分节点'),svgElement('desc',{},order.map(id=>name(id)+' 筛选前分数 '+format(scores[id])).join('；')+'。'+(removed?name(removed)+' 本轮退出，相关箭头已撤下。':'当前全部出场。')+' 当前选中 '+name(selected)+'，下方展开其入边和出边的相加过程。'));
      const defs=svgElement('defs'),marker=svgElement('marker',{id:markerId,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svgElement('path',{d:'M 0 1 L 9 5 L 0 9 Z',fill:'context-stroke'}));defs.append(marker);svg.append(defs);
      let activeEdges=0;
      edges.forEach((edge,id)=>{
        if(edge.from===removed||edge.to===removed)return;activeEdges++;
        const from=positions.get(edge.from),to=positions.get(edge.to),dx=to.x-from.x,dy=to.y-from.y;
        const fraction=Math.min(dx?nodeWidth/2/Math.abs(dx):Infinity,dy?nodeHeight/2/Math.abs(dy):Infinity),length=Math.hypot(dx,dy),start={x:from.x+dx*fraction,y:from.y+dy*fraction},end={x:to.x-dx*(fraction+3/length),y:to.y-dy*(fraction+3/length)};
        const contribution=edge.to===selected?'incoming':edge.from===selected?'outgoing':'other';
        const path=svgElement('path',{d:'M '+start.x+' '+start.y+' L '+end.x+' '+end.y,class:'degree-edge degree-edge-'+contribution,'marker-end':'url(#'+markerId+')','data-edge-id':id,'data-weight':format(edgeTicks(edge)/10),'data-contribution':contribution});
        path.append(svgElement('title',{},name(edge.from)+' → '+name(edge.to)+'，边权 '+format(edgeTicks(edge)/10)));svg.append(path);
        let x=(start.x+end.x)/2,y=(start.y+end.y)/2-9;if(Math.abs(dx)<1){x+=18;y-=nodeHeight/2;}
        svg.append(svgElement('text',{x,y,'text-anchor':'middle',class:'degree-edge-label','data-edge-label':id},format(edgeTicks(edge)/10)));
      });
      order.forEach(id=>{
        const p=positions.get(id),group=svgElement('g',{'data-node-id':id,'data-score':format(scores[id]),'data-active':String(id!==removed),class:'degree-node'+(id===selected?' is-selected':'')+(id===removed?' is-removed':'')});
        group.append(svgElement('rect',{x:p.x-nodeWidth/2,y:p.y-nodeHeight/2,width:nodeWidth,height:nodeHeight,rx:6}));
        const rows=visualTextLines(name(id),nodeWidth-10).length,labelY=p.y-(rows-1)*8.5-14;
        svgLabel(group,name(id),p.x,labelY,nodeWidth-10,'degree-node-label');
        group.append(svgElement('text',{x:p.x,y:labelY+(rows-1)*17+18,'text-anchor':'middle',class:'degree-score'},format(scores[id])));
        group.append(svgElement('text',{x:p.x,y:labelY+(rows-1)*17+35,'text-anchor':'middle',class:'degree-node-state'},id===removed?'本轮退出':'出场'));
        svg.append(group);
      });
      drawing.replaceChildren(svg);root.dataset.activeNodes=String(order.length-(removed?1:0));root.dataset.activeEdges=String(activeEdges);
    }
    // Measure this figure directly, including widths below the older visuals' 240px floor.
    let previousWidth=0;
    function redraw(){const width=Math.max(180,Math.round(drawing.getBoundingClientRect().width||600));previousWidth=width;draw(width);host.onLayout();}
    if(typeof ResizeObserver==='function'){const observer=new ResizeObserver(()=>{if(!drawing.isConnected)observer.disconnect();else if(Math.round(drawing.getBoundingClientRect().width)!==previousWidth)redraw();});observer.observe(drawing);}
    input.addEventListener('input',()=>{weight=Number(input.value);update(true);});select.addEventListener('change',()=>{selected=select.value;update(true);});
    function update(userAction){
      const totals=new Map(order.map(id=>[id,0]));edges.forEach(edge=>{const ticks=edgeTicks(edge);totals.set(edge.from,totals.get(edge.from)+ticks);totals.set(edge.to,totals.get(edge.to)+ticks);});
      scores=Object.fromEntries(order.map(id=>[id,totals.get(id)/10]));ranked=order.slice().sort((a,b)=>totals.get(a)-totals.get(b)||order.indexOf(a)-order.indexOf(b));removed=mode==='prune_one'?ranked[0]:null;
      const inEdges=Array.from(edges.values()).filter(edge=>edge.to===selected),outEdges=Array.from(edges.values()).filter(edge=>edge.from===selected),inTicks=inEdges.reduce((sum,edge)=>sum+edgeTicks(edge),0),outTicks=outEdges.reduce((sum,edge)=>sum+edgeTicks(edge),0);
      const contributions=list=>list.length?list.map(edge=>name(edge.from)+' → '+name(edge.to)+' '+format(edgeTicks(edge)/10)).join(' + '):'没有，记 0.0';
      incoming.textContent='交给 '+name(selected)+'：'+contributions(inEdges)+' = '+format(inTicks/10);
      outgoing.textContent=name(selected)+' 交给别人：'+contributions(outEdges)+' = '+format(outTicks/10);
      const sum=list=>list.length?list.map(edge=>format(edgeTicks(edge)/10)).join(' + '):'0.0';
      expanded.textContent=name(selected)+' 的筛选前分数 = ('+sum(inEdges)+') + ('+sum(outEdges)+') = '+format(scores[selected]);
      const ties=ranked.filter(id=>totals.get(id)===totals.get(ranked[0])),sequence=ranked.map((id,i)=>name(id)+' '+format(scores[id])+(i===ranked.length-1?'':totals.get(id)===totals.get(ranked[i+1])?' = ':' < ')).join('');
      result.textContent='筛选前从低到高：'+sequence+'。'+(ties.length>1?'最低分并列，按教学顺序选 '+name(ranked[0])+'。':'最低分是 '+name(ranked[0])+'。')+(removed?'本轮 '+name(removed)+' 退出。':'当前全部出场。');
      input.value=String(weight);weightValue.textContent=format(weight);select.value=selected;allButton.setAttribute('aria-pressed',String(mode==='all'));pruneButton.setAttribute('aria-pressed',String(mode==='prune_one'));
      root.dataset.mode=mode;root.dataset.selectedNodeId=selected;root.dataset.removedNodeId=removed||'';root.dataset.lowestNodeId=ranked[0];redraw();
      if(userAction)save({weight,mode,selected_node_id:selected,scores:Object.assign({},scores),removed_node_id:removed,tied_lowest_ids:ties});
    }
    update(false);
  };
  registry.graph_walkthrough=function({root,config,stored,save,el,button,text,host}) {
    const nodes=indexedVisualItems(config.nodes,'节点',24),edges=new Map(),rounds=new Map();
    nodes.forEach(node=>{const round=node.round===undefined?'当前轮':visualString(node.round,'轮次',40);
      if(!rounds.has(round))rounds.set(round,[]);rounds.get(round).push(node);});
    if(rounds.size>4||Array.from(rounds.values()).some(group=>group.length>6))throw new Error('每轮图请限制为六个节点，最多四轮');
    visualList(config.edges,'通信边',100,true).forEach(edge=>{
      if(!edge||edges.has(edge.id)||typeof edge.id!=='string'||!edge.id||edge.id.length>160||!nodes.has(edge.from)||!nodes.has(edge.to)||edge.from===edge.to)throw new Error('通信边引用无效');
      if(edge.kind!==undefined&&!['intra','inter'].includes(edge.kind))throw new Error('通信边类型无效');
      if(edge.label!==undefined)visualString(edge.label,'通信边名称',120);edges.set(edge.id,edge);
    });
    const stages=visualList(config.stages,'方法阶段',20),stageIds=new Set();
    stages.forEach(stage=>{
      visualString(stage.id,'阶段标识',160);if(stageIds.has(stage.id))throw new Error('阶段标识重复');stageIds.add(stage.id);
      visualString(stage.title,'阶段名称',80);visualString(stage.text,'阶段解释',4000);
      const activeNodes=visualIds(stage.active_node_ids,nodes,'出场节点'),activeEdges=visualIds(stage.active_edge_ids,edges,'通信路径');
      activeEdges.forEach(id=>{const edge=edges.get(id);if(!activeNodes.has(edge.from)||!activeNodes.has(edge.to))throw new Error('通信边两端必须在本阶段出场');});
      visualIds(stage.highlight_node_ids||[],nodes,'强调节点');visualIds(stage.highlight_edge_ids||[],edges,'强调路径');
      [['node_annotations','node_id',nodes],['edge_annotations','edge_id',edges]].forEach(([key,idKey,map])=>{
        const seen=new Set();visualList(stage[key]||[],key,map.size,true).forEach(annotation=>{
          if(!annotation||!map.has(annotation[idKey])||seen.has(annotation[idKey]))throw new Error('图形说明引用无效');
          seen.add(annotation[idKey]);visualString(annotation.text,'图形说明',240);
        });
      });
    });
    root.classList.add('learning-visual','graph-walkthrough');
    const stageButtons=el('div','visual-stage-controls'),figure=el('figure','visual-figure'),drawing=el('div','graph-drawing'),description=el('div','visual-stage-description'),status=el('output','visual-status'),routes=el('details','visual-routes'),routesBody=el('div');
    status.setAttribute('aria-live','polite');routes.append(el('summary',null,'看谁把结果交给谁'),routesBody);
    const legend=el('figcaption','visual-legend','框表示某个角色在这一轮处理题目；箭头表示把回答交给谁。实线在本轮传话，虚线把回答交给下一轮。');
    figure.append(drawing,legend);stageButtons.setAttribute('role','group');stageButtons.setAttribute('aria-label','方法阶段');
    root.append(stageButtons,figure,description,routes);
    let index=Math.max(0,Math.min(stages.length-1,Number.isInteger(stored.stage)?stored.stage:(Number.isInteger(config.initial_stage)?config.initial_stage:0)));
    const buttons=stages.map((stage,i)=>{const b=button(stage.title,'visual-stage-button',()=>update(i,true));stageButtons.append(b);return b;});
    stageButtons.append(status);
    const markerId='learning-arrow-'+(++visualSerial);
    function draw(width) {
      const stage=stages[index],activeNodes=new Set(stage.active_node_ids),activeEdges=new Set(stage.active_edge_ids),highlightNodes=new Set(stage.highlight_node_ids||[]),highlightEdges=new Set(stage.highlight_edge_ids||[]);
      const groups=Array.from(rounds.entries()),columns=width>=620?Math.min(2,groups.length):1,nodeWidth=Math.min(174,Math.max(100,width/columns-96)),positions=new Map();
      const nodeHeight=Math.max(52,...Array.from(nodes.values()).map(node=>visualTextLines(node.label,nodeWidth-22).length*17+22)),rowHeight=nodeHeight+36;
      const groupHeight=72+(Math.max(...groups.map(([,items])=>items.length))-1)*rowHeight+nodeHeight/2+42,height=Math.ceil(groups.length/columns)*groupHeight-14;
      const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'img',class:'learning-graph-svg'});
      svg.append(svgElement('title',{},stage.title),svgElement('desc',{},(config.aria_summary||'')+' '+stage.text+' 当前有'+activeNodes.size+'个出场节点、'+activeEdges.size+'条通信边。'));
      const defs=svgElement('defs'),marker=svgElement('marker',{id:markerId,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});
      marker.append(svgElement('path',{d:'M 0 1 L 9 5 L 0 9 Z',class:'graph-arrow'}));defs.append(marker);svg.append(defs);
      groups.forEach(([round,items],groupIndex)=>{
        const column=groupIndex%columns,row=Math.floor(groupIndex/columns),x=(column+.5)*width/columns,yBase=row*groupHeight;
        svgLabel(svg,round,x,yBase+27,width/columns-24,'graph-round-label');
        items.forEach((node,i)=>positions.set(node.id,{x,y:yBase+72+i*rowHeight,group:groupIndex,index:i}));
      });
      const edgeLayer=svgElement('g',{'data-graph-layer':'edges'});svg.append(edgeLayer);
      edges.forEach((edge,id)=>{if(!activeEdges.has(id))return;
        const from=positions.get(edge.from),to=positions.get(edge.to);let d;
        if(from.group===to.group) {
          if(Math.abs(to.index-from.index)===1){const direction=to.y>from.y?1:-1;d='M '+from.x+' '+(from.y+nodeHeight/2*direction)+' L '+to.x+' '+(to.y-(nodeHeight/2+2)*direction);}
          else {const bend=Math.max(12,from.x-nodeWidth/2-22-11*Math.abs(to.index-from.index));d='M '+(from.x-nodeWidth/2)+' '+from.y+' C '+bend+' '+from.y+' '+bend+' '+to.y+' '+(to.x-nodeWidth/2-2)+' '+to.y;}
        } else if(columns>1&&Math.floor(from.group/columns)===Math.floor(to.group/columns)) {
          const direction=to.x>from.x?1:-1,start=from.x+direction*nodeWidth/2,end=to.x-direction*(nodeWidth/2+3),middle=(start+end)/2;
          d='M '+start+' '+from.y+' C '+middle+' '+from.y+' '+middle+' '+to.y+' '+end+' '+to.y;
        } else {
          const side=Math.min(width-12,Math.max(from.x,to.x)+nodeWidth/2+35+(Array.from(edges.keys()).indexOf(id)%3)*7);
          d='M '+(from.x+nodeWidth/2)+' '+from.y+' C '+side+' '+from.y+' '+side+' '+to.y+' '+(to.x+nodeWidth/2+3)+' '+to.y;
        }
        const kind=edge.kind||((nodes.get(edge.from).round||'当前轮')===(nodes.get(edge.to).round||'当前轮')?'intra':'inter');
        const path=svgElement('path',{d,class:'graph-edge graph-edge-'+kind+(highlightEdges.has(id)?' is-highlighted':''),'marker-end':'url(#'+markerId+')','data-edge-id':id,'data-from':edge.from,'data-to':edge.to});
        path.append(svgElement('title',{},nodes.get(edge.from).label+' → '+nodes.get(edge.to).label+(edge.label?'；'+edge.label:'')));edgeLayer.append(path);
      });
      const nodeLayer=svgElement('g',{'data-graph-layer':'nodes'});svg.append(nodeLayer);
      nodes.forEach((node,id)=>{const p=positions.get(id);
        const active=activeNodes.has(id),group=svgElement('g',{'data-node-id':id,'data-node-state':active?'active':'inactive',class:'graph-node'+(!active?' is-inactive':'')+(highlightNodes.has(id)&&active?' is-highlighted':'')});
        group.append(svgElement('rect',{x:p.x-nodeWidth/2,y:p.y-nodeHeight/2,width:nodeWidth,height:nodeHeight,rx:5}));
        const labelLines=visualTextLines(node.label,nodeWidth-22).length;
        svgLabel(group,node.label,p.x,p.y+4-(labelLines-1)*8.5,nodeWidth-22,'graph-node-label');
        if(!active)svgLabel(group,'本阶段未出场',p.x,p.y+nodeHeight/2+16,nodeWidth+34,'graph-absent-label');
        group.append(svgElement('title',{},node.label+(!active?'；本阶段未出场':'')+(node.detail?'；'+node.detail:'')));nodeLayer.append(group);
      });
      if(!activeNodes.size)svgLabel(svg,'本阶段没有出场节点',width/2,height-12,width-24,'visual-label');
      drawing.replaceChildren(svg);root.dataset.activeNodes=String(activeNodes.size);root.dataset.activeEdges=String(activeEdges.size);
    }
    const redraw=responsiveVisual(drawing,draw,host);
    function update(wanted,userAction) {
      index=wanted;const stage=stages[index];buttons.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===index)));
      description.replaceChildren();text(description,stage.text,'config.stages.'+index+'.text','visual-stage-text');
      status.textContent=stage.active_node_ids.length+' 次出场 · '+stage.active_edge_ids.length+' 条传话路线';root.dataset.stageId=stage.id;
      routesBody.replaceChildren();const annotations=el('ul','visual-annotations');
      (stage.node_annotations||[]).forEach((annotation,i)=>{const row=el('li');row.append(el('strong',null,nodes.get(annotation.node_id).label+'：'));text(row,annotation.text,'config.stages.'+index+'.node_annotations.'+i+'.text');annotations.append(row);});
      (stage.edge_annotations||[]).forEach((annotation,i)=>{const edge=edges.get(annotation.edge_id),row=el('li');row.append(el('strong',null,nodes.get(edge.from).label+' → '+nodes.get(edge.to).label+'：'));text(row,annotation.text,'config.stages.'+index+'.edge_annotations.'+i+'.text');annotations.append(row);});
      if(annotations.children.length)description.append(annotations);
      Array.from(rounds.keys()).forEach(round=>{const ids=stage.active_node_ids.filter(id=>(nodes.get(id).round||'当前轮')===round);routesBody.append(el('p',null,round+'出场：'+(ids.length?ids.map(id=>nodes.get(id).label).join('、'):'无')));});
      if(stage.active_edge_ids.length){const list=el('ul');stage.active_edge_ids.forEach(id=>{const edge=edges.get(id);list.append(el('li',null,nodes.get(edge.from).label+' → '+nodes.get(edge.to).label+(edge.kind==='inter'?'（跨轮）':'')));});routesBody.append(list);}
      else routesBody.append(el('p',null,'本阶段没有通信边。'));
      redraw();host.typeset(description);if(userAction)save({stage:index});else host.onLayout();
    }
    update(index,false);
  };
  registry.comparison_chart=function({root,config,stored,save,el,text,host}) {
    const models=indexedVisualItems(config.models,'模型',16),tasks=indexedVisualItems(config.tasks,'任务',64),series=indexedVisualItems(config.series,'对照方法',12),observations=new Map();
    visualString(config.metric_label,'指标名称',80);if(config.unit!=='%')throw new Error('当前比较图使用百分比单位');
    tasks.forEach(task=>{visualString(task.description,'任务解释',2000);visualString(task.denominator,'指标分母',2000);});
    visualList(config.observations,'论文观测',8192,true).forEach(observation=>{
      if(!observation||!models.has(observation.model_id)||!tasks.has(observation.task_id)||!series.has(observation.series_id)||!Number.isFinite(observation.value)||observation.value<0||observation.value>100)throw new Error('比较图观测或引用无效');
      const key=JSON.stringify([observation.model_id,observation.task_id,observation.series_id]);if(observations.has(key))throw new Error('比较图观测重复');observations.set(key,observation.value);
    });
    if(config.baseline_series_id!==undefined&&!series.has(config.baseline_series_id))throw new Error('比较基线不存在');
    root.classList.add('learning-visual','comparison-chart');
    let modelId=models.has(stored.model_id)?stored.model_id:(models.has(config.default_model_id)?config.default_model_id:models.keys().next().value),taskId=tasks.has(stored.task_id)?stored.task_id:(tasks.has(config.default_task_id)?config.default_task_id:tasks.keys().next().value);
    const controls=el('div','visual-chart-controls'),drawing=el('div','comparison-drawing'),description=el('div','visual-chart-description'),baselineDescription=el('p','visual-baseline-description'),status=el('p','visual-chart-status');status.setAttribute('aria-live','polite');
    function select(label,items,id,onChange){const wrapper=el('label','visual-select'),input=el('select');wrapper.append(el('span',null,label));items.forEach(item=>{const option=el('option',null,item.label);option.value=item.id;input.append(option);});input.value=id;input.setAttribute('aria-label',label);input.addEventListener('change',()=>onChange(input.value));if(items.size>1)wrapper.append(input);else wrapper.append(el('span','visual-fixed-choice',items.get(id).label));controls.append(wrapper);return input;}
    const modelSelect=select('使用的模型',models,modelId,id=>{modelId=id;update(true);}),taskSelect=select('看哪项成绩',tasks,taskId,id=>{taskId=id;update(true);});
    if(config.baseline_series_id)baselineDescription.textContent='括号内差值：相对 '+series.get(config.baseline_series_id).label+'，单位为百分点。';
    root.append(controls,description,drawing,baselineDescription,status);
    if(config.note)text(root,config.note,'config.note','visual-data-note');
    const lookup=id=>observations.get(JSON.stringify([modelId,taskId,id]));
    function draw(width){
      const items=Array.from(series.values()),left=12,right=12,plotWidth=width-left-right,rows=[];let cursor=20;
      items.forEach(item=>{const nameLines=visualTextLines(item.label,plotWidth),height=Math.max(58,nameLines.length*17+43);rows.push({y:cursor,lines:nameLines});cursor+=height;});
      const height=cursor+58;
      const svg=svgElement('svg',{viewBox:'0 0 '+width+' '+height,width,height,role:'img',class:'learning-comparison-svg'});
      svg.append(svgElement('title',{},models.get(modelId).label+' · '+tasks.get(taskId).label+' · '+config.metric_label),svgElement('desc',{},items.map(item=>item.label+'：'+(lookup(item.id)===undefined?'没有报告':lookup(item.id).toFixed(2)+'%')).join('；')));
      items.forEach((item,i)=>{const value=lookup(item.id),row=rows[i],y=row.y,nameWidth=Array.from(item.label).reduce((sum,char)=>sum+(/[^\x00-\xff]/.test(char)?13:7),0);
        const baseline=config.baseline_series_id&&lookup(config.baseline_series_id),delta=Number.isFinite(value)&&Number.isFinite(baseline)?value-baseline:null;
        const label=value===undefined?'未报告':value.toFixed(2)+'%'+(delta===null||item.id===config.baseline_series_id?'':' ('+(delta>=0?'+':'')+delta.toFixed(2)+')');
        const together=nameWidth+label.length*7+16<=plotWidth,valueY=together?y:y+row.lines.length*17,barY=valueY+11;
        svgLabel(svg,item.label,left,y,together?Math.max(nameWidth,1):plotWidth,'chart-series-label','start');
        svg.append(svgElement('text',{x:width-right,y:valueY,'text-anchor':'end',class:'chart-value-label'},label));
        svg.append(svgElement('rect',{x:left,y:barY,width:plotWidth,height:18,class:'chart-bar-track'}));
        if(value!==undefined){const bar=svgElement('rect',{x:left,y:barY,width:plotWidth*value/100,height:18,class:'chart-bar'+(item.id===config.baseline_series_id?' chart-bar-baseline':''),'data-series-id':item.id,'data-value':value});bar.append(svgElement('title',{},item.label+'：'+label));svg.append(bar);}
      });
      const axisY=cursor;
      [0,50,100].forEach(tick=>{const x=left+plotWidth*tick/100;svg.append(svgElement('line',{x1:x,y1:axisY-5,x2:x,y2:axisY,class:'chart-tick'}));svg.append(svgElement('text',{x,y:axisY+18,'text-anchor':tick===0?'start':tick===100?'end':'middle',class:'chart-axis-label'},tick+'%'));});
      svg.append(svgElement('text',{x:width/2,y:height-12,'text-anchor':'middle',class:'chart-axis-label axis-title','data-axis':'x'},config.metric_label+' (%)'));
      drawing.replaceChildren(svg);root.dataset.modelId=modelId;root.dataset.taskId=taskId;
    }
    const redraw=responsiveVisual(drawing,draw,host);
    function update(userAction){const task=tasks.get(taskId),taskIndex=Array.from(tasks.keys()).indexOf(taskId);description.replaceChildren();
      text(description,task.description,'config.tasks.'+taskIndex+'.description','visual-task-description');text(description,task.denominator,'config.tasks.'+taskIndex+'.denominator','visual-denominator');
      const baseline=config.baseline_series_id&&lookup(config.baseline_series_id),values=Array.from(series.values()).map(item=>item.label+' '+(lookup(item.id)===undefined?'未报告':lookup(item.id).toFixed(2)+'%'));
      status.textContent=models.get(modelId).label+' · '+task.label+'。'+values.join('；')+(Number.isFinite(baseline)?'。括号内表示相对 '+series.get(config.baseline_series_id).label+' 的百分点差（本值减基线值）。':'');
      modelSelect.value=modelId;taskSelect.value=taskId;redraw();host.typeset(description);if(userAction)save({model_id:modelId,task_id:taskId});else host.onLayout();
    }
    update(false);
  };
  // Compatibility names from early teaching configurations; primary names are documented in v3.1.
  registry.step_walkthrough=registry.steps;registry.cache_walkthrough=registry.lru_cache;
  window.LearningComponents=Object.freeze({render,evaluate,cacheTrace,supportedTypes:Object.keys(registry)});
})();
