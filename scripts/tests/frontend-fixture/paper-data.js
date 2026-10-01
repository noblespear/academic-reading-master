window.PAPER_DATA = {
  schema_version: 3, id: 'frontend_v3_fixture', content_version: 'fixture-1',
  title_en: 'Stable Reading Anchors for Bilingual Scientific Documents',
  title_cn: '双语科学文档的稳定阅读锚点', authors: ['Reader Regression Suite'],
  year: 2026, venue: 'FRONTEND FIXTURE', level: 'novice',
  overview: {sections: [
    {id: 'ov-map', title_cn: '从问题到方法', page: 1, blocks: [
      {id: 'ov-summary', type: 'paragraph', text_zh: '论文要解决的是：当一篇文章有很多段落、公式和图表时，怎样让读者随时找到自己的问题。**稳定锚点**记录原文位置，问答浮层保留思路，来源链接帮助核对证据。', source_refs: [{block_id: 'tr-p1', page: 1, label: '引言 · 第 1 段'}]},
      {id: 'ov-diagram', type: 'diagram', svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 180"><rect width="720" height="180" fill="#f5f8fb"/><rect x="28" y="45" width="175" height="85" rx="7" fill="#e4edf5"/><rect x="271" y="45" width="175" height="85" rx="7" fill="#e4edf5"/><rect x="514" y="45" width="175" height="85" rx="7" fill="#e4edf5"/><path d="M210 88H259M453 88H502" stroke="#789bb8" stroke-width="3"/><text x="115" y="95" font-size="22" text-anchor="middle" fill="#3d6384">理解问题</text><text x="359" y="95" font-size="22" text-anchor="middle" fill="#3d6384">定位证据</text><text x="602" y="95" font-size="22" text-anchor="middle" fill="#3d6384">澄清思路</text><script>window.__injected=true</script></svg>', caption_zh: '阅读路径：先理解问题，再定位证据，最后澄清自己的思路。', source_refs: [{section_id: 'tr-sec1', page: 1}]},
      {id: 'ov-check', type: 'paragraph', text_zh: '自测：**位置锚点保存哪些信息？** 如果相同引文出现两次，系统应该怎样处理？如果原文排版改变，批注还应当跟着原文走吗？'}
    ]},
    {id: 'ov-benefits', title_cn: '方法带来的变化', page: 2, blocks: [{id: 'ov-benefit',type: 'paragraph',text_zh: '逐块对应的原文与译文共用同一行。中文变长时，整行会一起增高；下一段仍从相同的位置开始。你不需要来回对齐两列。'},{id:'ov-repeat',type:'paragraph',text_zh:'重复引文 重复引文。重复引文必须唯一定位，不能默认选择第一处。'}]}
  ]},
  explanation: {sections: [{id:'ex-anchors',title_cn:'锚点怎样恢复',page:2,blocks:[
    {id:'legacy-block-1',type:'paragraph',text_zh:'锚点不是只保存一句话。它还记录**内容块、语言、字段和版本**，并保留选中文本前后的上下文。这样即使段落中有加粗标记，位置也可以恢复。旧式向量 x⃗。',text_zh_latex:'锚点不是只保存一句话。它还记录**内容块、语言、字段和版本**，并保留选中文本前后的上下文。这样即使段落中有加粗标记，位置也可以恢复。向量 $\\mathbf{x}$。',source_refs:[{block_id:'tr-p1',page:1,label:'原文定位策略'}]},
      {id:'ex-cross',type:'paragraph',text_zh:'当选择跨越两个段落时，批注保存多个片段。所有片段共同组成一次选词。回答仍然显示在独立的问答浮层中。内联公式 $\\alpha + \\beta = \\gamma$ 和 \\(\\frac{1}{2}\\) 应离线渲染。金额 $5 and $10、路径 $HOME/foo$ 保持文字。'},
    {id:'ex-formula',type:'formula',latex:'E = mc^2',text_zh:'公式显示和索引各自承担任务：显示用于阅读，索引只计算规范公式源文本一次。',source_refs:[{block_id:'tr-f',page:2}]}
  ]}]},
  sections: [
    {id:'tr-sec1',title_en:'Introduction',title_cn:'引言',page:1,blocks:[
      {id:'tr-p1',type:'paragraph',text_en:'Reading scientific papers requires **stable anchors** across formatting, equations, and translation. A reader may select a word, a sentence, or several paragraphs. Each selection should preserve its context.',text_zh:'阅读科学论文时，**稳定锚点**需要跨越排版、公式和翻译。读者可能只选择一个词，也可能选择一句话，或者同时选择多个段落。每次选择都应当保留前后的上下文。为了形成这种对应关系，我们把原文和译文按段落放在同一行；当译文需要更多文字解释时，这一行会自动增高，后面的段落仍然保持对齐。这个故意较长的段落用于检验英文较短、中文较长时的逐行同步行为。'},
      {id:'tr-p2',type:'paragraph',text_en:'The next paragraph remains aligned. An annotation uses source offsets and exact context rather than the screen coordinates.',text_zh:'下一段仍保持对齐。批注记录源文本中的偏移和确切上下文，因此调整窗口大小也不会破坏位置。'},
      {id:'tr-fig',type:'figure',src:'fixture-figure.svg',caption_en:'Figure 1. Shared rows preserve the reading correspondence.',caption_zh:'图 1。共同行高保留原文与译文之间的阅读对应关系。',text_en:'Both columns reference the same asset.',text_zh:'两列引用同一张图示资源。'}
    ]},
    {id:'tr-sec2',title_cn:'公式与证据',title_en:'Equations and evidence',page:2,blocks:[
      {id:'tr-f',type:'formula',latex:'E = mc^2',text_en:'The equation is indexed once using canonical source, excluding hidden accessibility nodes.',text_zh:'公式只以规范源文本索引一次，数学排版工具添加的隐藏辅助节点不参与计数。'},
      {id:'tr-t',type:'table',rows_en:[['Item','Behavior'],['Rows','Share maximum height'],['Annotations','Keep separate status']],rows_zh:[['项目','行为'],['对照行','使用两边最大高度'],['批注','状态使用独立角标']],caption_en:'Table 1. Reader contracts.',caption_zh:'表 1。阅读器交互合同。'},
      {id:'tr-table-image',type:'table',src:'fixture-figure.svg',caption_en:'Table 2. Source table region.',caption_zh:'表2。原表区域图。'},
      {id:'tr-inject',type:'paragraph',text_en:'Raw markup: <img src=x onerror="window.__injected=true">. Unsafe links remain plain text: [click](javascript:alert(1)).',text_zh:'不可信内容应作为文字显示：<script>window.__injected=true</script>。用户批注和模型回答不能执行脚本。'}
    ]}
  ]
};
