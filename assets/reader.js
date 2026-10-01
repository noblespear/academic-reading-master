/**
 * Academic Reading Master - 阅读器引擎 v4（固定模板，永不按论文重写）
 * v4 变更：
 * - 服务模式：页面由 serve_reader.py 提供（http://127.0.0.1）时，批注经
 *   /api/annotations 直写论文文件夹——零弹窗零授权；file:// 直开时回退 FSA 链路
 * - 原文 PDF 侧栏：PDF.js 渲染论文目录现成的 source.pdf（仅服务模式可用），
 *   章节标题/目录点击联动跳页，翻页反向显示页码；替代全文转录原文（省一半生成成本）
 * - 挡位徽标（paper-data.js 顶层 level: novice|expert）
 * - 深度剖析区：paper-data.js 顶层 pass3（边界 + Gap）在文末渲染
 * v3 变更：
 * - 全站零 emoji；批注浮窗跟随光标（视口 clamp）；保存/轮询全自动；
 * - diagram 示意图板块 + 流水线总览示意图条；目录 scrollspy
 */
(function () {
  "use strict";

  const DATA = window.PAPER_DATA;
  if (!DATA || !DATA.id) {
    document.body.innerHTML =
      '<div style="padding:60px; text-align:center; font-family:sans-serif;">' +
      "<h2>paper-data.js 加载失败或缺少 id 字段</h2>" +
      "<p>请检查本目录的 paper-data.js 是否存在且为合法 JSON（deploy_reader.py 可校验）。</p></div>";
    return;
  }

  const paperId = DATA.id;
  const STORAGE_KEY = "arm_annotations_" + paperId;
  const ANN_META = {
    concept: "概念解释",
    "key-conclusion": "关键结论",
    method: "方法理解",
    formula: "公式解读",
    figure: "图表解读",
    experiment: "实验分析",
    "reading-reminder": "阅读提醒",
    "critical-thinking": "批判性思考",
  };

  const ICONS = {
    pencil:
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>',
    close:
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  };

  let annotations = [];

  /* ============================== 工具函数 ============================== */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // 先全部转义，再放行白名单标签（b/i/em/strong/br/sub/sup 与安全的 a href）
  function inline(s) {
    return esc(s)
      .replace(/&lt;(\/?)(b|i|em|strong|br|sub|sup)&gt;/g, "<$1$2>")
      .replace(/&lt;a\s+href=&quot;(https?:\/\/[^&]*?)&quot;&gt;/g, '<a href="$1" target="_blank" rel="noopener">')
      .replace(/&lt;\/a&gt;/g, "</a>");
  }

  // 轻量 Markdown 渲染（用于批注与 AI 回答的显示）：**粗**、`代码`、- 列表、换行
  function mdLite(s) {
    const lines = String(s == null ? "" : s).split(/\r?\n/);
    const out = [];
    let inList = false;
    lines.forEach(function (raw) {
      const line = inline(raw);
      const li = line.match(/^(\s*)[-*]\s+(.*)$/);
      if (li) {
        if (!inList) { out.push("<ul class='md-lite-list'>"); inList = true; }
        out.push("<li>" + li[2].replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>") + "</li>");
        return;
      }
      if (inList) { out.push("</ul>"); inList = false; }
      if (line.trim() === "") { out.push("<br>"); return; }
      out.push("<p>" + line.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>") + "</p>");
    });
    if (inList) out.push("</ul>");
    return out.join("");
  }

  function sanitizeTable(html) {
    return String(html)
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  }

  // diagram 板块的 SVG 消毒：去脚本/事件/外链跳转，只留纯绘图
  function sanitizeSvg(svg) {
    return String(svg)
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, "")
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      .replace(/((?:xlink:)?href)\s*=\s*("javascript:[^"]*"|'javascript:[^']*')/gi, "");
  }

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function typeset(elx) {
    if (window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise(elx ? [elx] : undefined).catch(function () {});
    } else if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) {
      window.MathJax.startup.promise.then(function () { window.MathJax.typesetPromise(elx ? [elx] : undefined); }).catch(function () {});
    }
  }

  /* ============================== 渲染引擎 ============================== */

  function renderHeader() {
    document.title = (DATA.title_cn || DATA.title_en || paperId) + " · 论文精读伴读";
    document.getElementById("nav-paper-title").textContent = DATA.title_cn || DATA.title_en || "论文精读伴读";
    const lv = document.getElementById("level-badge");
    if (lv && DATA.level) {
      lv.textContent = DATA.level === "novice" ? "新手档" : "熟练档";
      lv.title = "挡位：" + lv.textContent + "（切换挡位后重跑第二遍生效）";
      lv.style.display = "";
    }
    const h = document.getElementById("paper-header");
    const parts = [];
    if (DATA.authors) parts.push("<strong>作者</strong>&nbsp;" + esc(DATA.authors));
    if (DATA.affiliations) parts.push("<strong>机构</strong>&nbsp;" + esc(DATA.affiliations));
    if (DATA.venue) parts.push("<strong>发表于</strong>&nbsp;" + esc(DATA.venue));
    if (DATA.year) parts.push("<strong>年份</strong>&nbsp;" + esc(DATA.year));
    h.innerHTML =
      '<h1 class="paper-title-cn">' + esc(DATA.title_cn || DATA.title_en) + "</h1>" +
      (DATA.title_en && DATA.title_cn ? '<div class="paper-title-en">' + esc(DATA.title_en) + "</div>" : "") +
      (DATA.novelty
        ? '<div class="novelty-block"><span class="novelty-label">核心创新</span><span class="novelty-text">' + inline(DATA.novelty) + "</span></div>"
        : "") +
      '<div class="paper-meta-bar">' + parts.join('<span class="meta-dot">·</span>') + "</div>";
  }

  function renderTOC() {
    const toc = document.getElementById("toc-list");
    const items = [];
    if (DATA.pipeline && DATA.pipeline.length) {
      items.push('<li class="toc-item"><a class="toc-link toc-pipeline" href="#pipeline-view">方法流水线<span style="font-weight:400"> · 五分钟读懂主线</span></a></li>');
    }
    (DATA.sections || []).forEach(function (sec) {
      const num = sec.number && sec.number !== "0" ? '<span class="toc-num">' + esc(sec.number) + "</span> " : "";
      items.push(
        '<li class="toc-item"><a class="toc-link" href="#' + esc(sec.id) + '"' +
        (sec.page ? ' data-page="' + esc(sec.page) + '"' : "") + ">" +
        num + esc(sec.title_cn || sec.title_en) + "</a></li>"
      );
    });
    if (DATA.pass3 && (DATA.pass3.boundary || DATA.pass3.gaps)) {
      items.push('<li class="toc-item"><a class="toc-link" href="#pass3-view">深度剖析<span style="font-weight:400"> · 边界与 Gap</span></a></li>');
    }
    toc.innerHTML = items.join("");
  }

  function initScrollspy() {
    const map = {};
    document.querySelectorAll(".toc-link").forEach(function (l) {
      const href = l.getAttribute("href");
      if (href && href.charAt(0) === "#") map[href.slice(1)] = l;
    });
    const targets = [];
    const pv = document.getElementById("pipeline-view");
    if (pv && pv.innerHTML.trim()) targets.push(pv);
    document.querySelectorAll(".paper-section").forEach(function (s) { targets.push(s); });
    if (!("IntersectionObserver" in window) || !targets.length) return;
    const obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        Object.keys(map).forEach(function (k) { map[k].classList.remove("active"); });
        const link = map[en.target.id];
        if (link) link.classList.add("active");
      });
    }, { rootMargin: "-15% 0px -70% 0px", threshold: 0 });
    targets.forEach(function (t) { obs.observe(t); });
  }

  function annCard(a) {
    const label = ANN_META[a.type] || "批注";
    return (
      '<div class="annotation-card ' + esc(a.type) + '">' +
      '<div class="annotation-header">AI 精读批注 · ' + label + (a.title ? "｜" + esc(a.title) : "") + "</div>" +
      '<div class="annotation-body">' + inline(a.body) + "</div></div>"
    );
  }

  const PIPE_FIELDS = [
    ["goal", "目标", ""],
    ["input", "输入", ""],
    ["tech", "技术·黑盒", "f-tech"],
    ["why", "为何用它", ""],
    ["gain", "效果与证据", ""],
    ["benefit_next", "衔接", ""],
  ];

  // 示意图条上的技术短名：取冒号/括号前的主名，超长截断
  function shortTech(t) {
    if (!t) return "";
    let s = String(t);
    const cut = s.search(/[：（(]/);
    if (cut > 0) s = s.slice(0, cut);
    s = s.replace(/[，。,]/g, "");
    if (s.length > 16) s = s.slice(0, 16) + "…";
    return s;
  }

  function renderPipeline() {
    if (!DATA.pipeline || !DATA.pipeline.length) return;
    const host = document.getElementById("pipeline-view");
    let html = '<h2 class="section-title">方法流水线<span class="section-title-en"> · 先读这里，五分钟大致理解</span></h2>';
    html += '<div class="pipeline-intro">每一行只看三件事：<b>输入是什么、用了什么技术（只当黑盒：知道名字和作用即可）、达到什么效果</b>——不纠结技术内部怎么实现，细节在第三遍按需展开。先看下图主线，再逐节点细读，最后做下方自测。</div>';
    // 总览示意图条（点击跳到对应节点卡片）
    html += '<div class="pipe-diagram">' + DATA.pipeline.map(function (p, i) {
      const stepNo = p.step != null ? p.step : i + 1;
      const techShort = shortTech(p.tech);
      return (i > 0 ? '<span class="pipe-dg-arrow">→</span>' : "") +
        '<a class="pipe-dg-node" href="#pipe-' + esc(stepNo) + '"><span class="pipe-dg-step">' + esc(stepNo) + "</span>" +
        '<span class="pipe-dg-name">' + esc(p.name) + "</span>" +
        (techShort ? '<span class="pipe-dg-tech">' + esc(techShort) + "</span>" : "") + "</a>";
    }).join("") + "</div>";
    html += '<div class="pipeline-flow">';
    DATA.pipeline.forEach(function (p, i) {
      const stepNo = p.step != null ? p.step : i + 1;
      html += '<div class="pipeline-node" id="' + esc("pipe-" + stepNo) + '">';
      html += '<span class="pipeline-step">' + esc(stepNo) + "</span>";
      html += '<div class="pipeline-card"><div class="pipeline-card-head"><span class="pipeline-name">' + esc(p.name) + "</span></div>";
      PIPE_FIELDS.forEach(function (f) {
        if (p[f[0]]) {
          html += '<div class="pipeline-field' + (f[2] ? " " + f[2] : "") + '"><span class="pipeline-label">' + f[1] + '</span><span class="pipeline-value">' + inline(p[f[0]]) + "</span></div>";
        }
      });
      html += "</div></div>";
    });
    html += "</div>";

    if (DATA.self_check && DATA.self_check.length) {
      html += '<div class="self-check-card"><div class="self-check-title">复述自测 · 能向别人讲清这篇论文吗？</div><ol class="self-check-list">';
      DATA.self_check.forEach(function (c) {
        html += '<li><span class="sc-q">' + inline(c.q) + "</span>" +
          (c.hint ? ' <button class="hint-btn" onclick="window.armToggleHint(this)">显示提示</button><div class="hint-text" style="display:none">' + inline(c.hint) + "</div>" : "") +
          "</li>";
      });
      html += "</ol><div class='self-check-foot'>全部能答上来 → 大致理解达成，可进第三遍或读下一篇；答不上 → 下钻对应章节。</div></div>";
    }
    host.innerHTML = html;
  }

  window.armToggleHint = function (btn) {
    const h = btn.parentNode.querySelector(".hint-text");
    if (h) {
      h.style.display = h.style.display === "none" ? "block" : "none";
      btn.textContent = h.style.display === "none" ? "显示提示" : "收起提示";
    }
  };

  function renderBlock(b) {
    const wrap = el("div", "block block-" + b.type);
    wrap.setAttribute("data-block-id", b.id || "");

    if (b.type === "para") {
      const zh = el("div", "translated-text");
      zh.innerHTML = inline(b.text_zh || "");
      const btn = el("span", "toggle-orig-btn", "EN");
      btn.title = "显示 / 隐藏英文原文";
      const fold = el("div", "original-text-fold", inline(b.text_en || ""));
      const line = el("div", "paragraph-block");
      line.appendChild(zh);
      if (b.text_en) { zh.appendChild(document.createTextNode(" ")); zh.appendChild(btn); line.appendChild(fold); }
      wrap.appendChild(line);
    } else if (b.type === "formula") {
      const holder = el("div", "formula-block");
      const tex = el("div", "formula-tex");
      tex.textContent = "$$" + (b.latex || "") + "$$";
      holder.appendChild(tex);
      if (b.eq_ref) holder.appendChild(el("div", "formula-ref", esc(b.eq_ref)));
      if (b.text_zh) holder.appendChild(el("div", "formula-note", inline(b.text_zh)));
      wrap.appendChild(holder);
    } else if (b.type === "figure") {
      const fig = el("div", "figure-container");
      if (b.asset) {
        const img = el("img", "figure-image");
        img.src = b.asset; img.alt = b.fig_caption_zh || b.fig_caption_en || "";
        img.loading = "lazy";
        fig.appendChild(img);
      } else {
        fig.appendChild(el("div", "figure-placeholder", "（未提取到原图，见下方文字解读）"));
      }
      const caps = [];
      if (b.fig_caption_zh) caps.push("图｜" + esc(b.fig_caption_zh));
      if (b.fig_caption_en) caps.push('<span class="cap-en">' + esc(b.fig_caption_en) + "</span>");
      if (caps.length) fig.appendChild(el("div", "figure-caption", caps.join("<br>")));
      wrap.appendChild(fig);
    } else if (b.type === "table") {
      const t = el("div", "table-block");
      t.innerHTML = sanitizeTable(b.table_html || "");
      if (b.text_zh) t.appendChild(el("div", "table-note", inline(b.text_zh)));
      wrap.appendChild(t);
    } else if (b.type === "list") {
      const ul = el("ul", "list-block");
      (b.items || []).forEach(function (it) {
        const li = el("li");
        li.innerHTML = inline(it.text_zh || "") +
          (it.text_en ? ' <span class="toggle-orig-btn" title="显示 / 隐藏英文原文">EN</span><span class="orig-inline"> ' + inline(it.text_en) + "</span>" : "");
        ul.appendChild(li);
      });
      wrap.appendChild(ul);
    } else if (b.type === "algorithm") {
      const algo = el("div", "algorithm-block");
      algo.appendChild(el("div", "algorithm-title", "算法｜" + esc(b.algo_title || "伪代码")));
      const pre = el("pre", "algorithm-lines");
      pre.textContent = (b.algo_lines || []).join("\n");
      algo.appendChild(pre);
      wrap.appendChild(algo);
    } else if (b.type === "diagram") {
      // AI 绘制的结构/流程示意图（内联 SVG，白底学术风）
      const d = el("div", "diagram-block");
      d.innerHTML = sanitizeSvg(b.svg || "");
      if (b.caption) d.appendChild(el("div", "diagram-caption", "图｜" + inline(b.caption)));
      wrap.appendChild(d);
    } else {
      wrap.appendChild(el("div", "translated-text", inline(b.text_zh || b.text_en || "")));
    }

    (b.annotations || []).forEach(function (a) { wrap.insertAdjacentHTML("beforeend", annCard(a)); });

    // 板块批注按钮（公式/表格/图/算法无法划词时的主通道；段落也可整段批注）
    const annoBtn = el("button", "block-anno-btn", ICONS.pencil);
    annoBtn.setAttribute("data-block-id", b.id || "");
    annoBtn.title = "批注本板块（可反复编辑）";
    wrap.appendChild(annoBtn);

    return wrap;
  }

  function renderSections() {
    const host = document.getElementById("paper-content");
    (DATA.sections || []).forEach(function (sec) {
      const secEl = el("section", "paper-section");
      secEl.id = sec.id || "";
      secEl.setAttribute("data-sec-title", sec.title_cn || sec.title_en || "");
      if (sec.page) secEl.setAttribute("data-page", sec.page);
      const head = el("h2", "section-title");
      head.innerHTML =
        (sec.number && sec.number !== "0" ? '<span class="sec-num">' + esc(sec.number) + "</span>" : "") +
        '<span class="section-title-text">' + esc(sec.title_cn || sec.title_en) +
        (sec.title_en && sec.title_cn ? '<span class="section-title-en"> · ' + esc(sec.title_en) + "</span>" : "") + "</span>";
      secEl.appendChild(head);
      (sec.blocks || []).forEach(function (b) { secEl.appendChild(renderBlock(b)); });
      host.appendChild(secEl);
    });
  }

  /* ---------- 深度剖析区（paper-data.js 顶层 pass3：边界 + Gap） ---------- */

  function renderPass3() {
    const p3 = DATA.pass3;
    if (!p3 || (!p3.boundary && !p3.gaps)) return;
    const host = el("section", "paper-section");
    host.id = "pass3-view";
    let html = '<h2 class="section-title">深度剖析<span class="section-title-en"> · 第三遍产出（边界与 Gap）</span></h2>';

    const b = p3.boundary || {};
    const groups = [
      ["隐式假设", b.assumptions],
      ["失效场景", b.failure_cases],
      ["复杂度与成本", b.cost],
    ];
    if (groups.some(function (g) { return g[1] && g[1].length; })) {
      html += '<div class="p3-card"><div class="p3-card-title">边界剖析</div>';
      groups.forEach(function (g) {
        if (g[1] && g[1].length) {
          html += '<div class="p3-group"><div class="p3-group-label">' + g[0] + "</div><ul>" +
            g[1].map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + "</ul></div>";
        }
      });
      html += "</div>";
    }

    if (p3.gaps && p3.gaps.length) {
      html += '<div class="p3-card"><div class="p3-card-title">可拓展方向</div>';
      p3.gaps.forEach(function (g) {
        html += '<div class="gap-item">' +
          '<span class="gap-chip gap-' + esc(g.type === "跨域结合" ? "cross" : g.type === "痛点攻坚" ? "hard" : "incr") + '">' + esc(g.type || "改进") + "</span>" +
          '<div class="gap-idea">' + inline(g.idea) + "</div>" +
          '<div class="gap-meta">' +
          (g.linked_limitation ? "<span>呼应局限：" + inline(g.linked_limitation) + "</span>" : "") +
          (g.difficulty ? "<span>难度：" + esc(g.difficulty) + "</span>" : "") +
          (g.validation ? "<span>最小验证：" + inline(g.validation) + "</span>" : "") +
          "</div></div>";
      });
      html += "</div>";
    }
    host.innerHTML = html;
    document.querySelector(".paper-main").appendChild(host);
  }

  /* ============================== 视图模式（分段控件） ============================== */

  let bilingual = false;
  function initViewMode() {
    const seg = document.getElementById("seg-view");
    function setView(bi) {
      bilingual = bi;
      document.getElementById("layout-root").classList.toggle("show-en", bi);
      seg.querySelectorAll(".seg-btn").forEach(function (x) {
        x.classList.toggle("active", (x.getAttribute("data-mode") === "bi") === bi);
      });
      try { localStorage.setItem("arm_view_" + paperId, bi ? "bi" : "zh"); } catch (e) {}
    }
    seg.querySelectorAll(".seg-btn").forEach(function (b) {
      b.addEventListener("click", function () { setView(b.getAttribute("data-mode") === "bi"); });
    });
    let saved = null;
    try { saved = localStorage.getItem("arm_view_" + paperId); } catch (e) {}
    setView(saved === "bi");
  }

  /* ============================== 批注数据 ============================== */

  function loadAnnotations() {
    try { annotations = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]") || []; }
    catch (e) { annotations = []; }
  }

  function persistAnnotations() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(annotations)); } catch (e) {}
    scheduleAutoWrite();
  }

  function buildExportObject() {
    return { paper_id: paperId, updated_at: new Date().toISOString(), annotations: annotations };
  }

  function getAnno(id) {
    for (let i = 0; i < annotations.length; i++) if (annotations[i].id === id) return annotations[i];
    return null;
  }

  /* ============================== 批注浮窗 ============================== */

  let popEl = null, popCtx = null, popEditingId = null, popAnchor = null, popMode = "edit";
  let tooltipEl = null, pendingRange = null;

  function staticAnchor(rect) {
    const doc = { left: rect.left + window.scrollX, top: rect.top + window.scrollY, width: rect.width, height: rect.height };
    return function () {
      return {
        left: doc.left - window.scrollX, top: doc.top - window.scrollY,
        width: doc.width, height: doc.height,
        right: doc.left + doc.width - window.scrollX, bottom: doc.top + doc.height - window.scrollY,
      };
    };
  }

  function elAnchor(elx) {
    return function () { return elx.getBoundingClientRect(); };
  }

  function buildPopover() {
    popEl = el("div", "anno-popover");
    popEl.innerHTML = '<div class="pop-arrow"></div><div class="pop-inner"></div>';
    document.body.appendChild(popEl);

    document.addEventListener("mousedown", function (e) {
      if (!popEl.classList.contains("open")) return;
      if (popEl.contains(e.target)) return;
      if (e.target.closest(".floating-tooltip")) return;
      closePopover();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && popEl.classList.contains("open")) closePopover();
    });
    let raf = null;
    function schedulePlace() {
      if (!popEl.classList.contains("open") || raf) return;
      raf = requestAnimationFrame(function () { raf = null; placePopover(); });
    }
    window.addEventListener("scroll", schedulePlace, { passive: true, capture: true });
    window.addEventListener("resize", schedulePlace, { passive: true });
  }

  function placePopover() {
    if (!popAnchor || !popEl.classList.contains("open")) return;
    const r = popAnchor();
    const W = 340;
    const H = popEl.offsetHeight || 200;
    const cx = r.left + r.width / 2;
    let x = cx - W / 2;
    x = Math.max(8, Math.min(x, window.innerWidth - W - 8));
    let y = r.bottom + 10;
    let flip = false;
    if (y + H + 12 > window.innerHeight && r.top - H - 10 > 8) {
      y = r.top - H - 10;
      flip = true;
    }
    if (y + H + 12 > window.innerHeight) y = Math.max(8, window.innerHeight - H - 12);
    popEl.style.left = (x + window.scrollX) + "px";
    popEl.style.top = (y + window.scrollY) + "px";
    popEl.classList.toggle("flip", flip);
    const arrow = popEl.querySelector(".pop-arrow");
    arrow.style.left = (Math.max(16, Math.min(cx - x, W - 16)) - 5) + "px";
  }

  function quoteHtml(ctx) {
    if (ctx.quote) {
      return '<div class="pop-quote"><span class="pop-quote-mark">“</span>' + esc(String(ctx.quote)) + '<span class="pop-quote-mark">”</span></div>';
    }
    return '<div class="pop-quote">整个板块 · ' + esc(ctx.section_title || "") + (ctx.block_label ? " · " + esc(ctx.block_label) : "") + "</div>";
  }

  function openPopover(ctx, existing, anchorFn) {
    popCtx = ctx;
    popEditingId = existing ? existing.id : null;
    popAnchor = anchorFn;
    hideTooltip();
    renderPopover(existing ? "view" : "edit");
    popEl.classList.add("open");
    placePopover();
    if (!existing) {
      setTimeout(function () { const t = popEl.querySelector(".pop-textarea"); if (t) t.focus(); }, 30);
    }
  }

  function closePopover() {
    if (!popEl) return;
    popEl.classList.remove("open");
    popCtx = null; popEditingId = null; popAnchor = null;
  }

  function renderPopover(mode) {
    popMode = mode;
    const inner = popEl.querySelector(".pop-inner");
    const a = popEditingId ? getAnno(popEditingId) : null;
    if (mode === "edit") {
      inner.innerHTML = quoteHtml(popCtx) +
        '<div class="pop-main"><textarea class="pop-textarea" rows="4" placeholder="写下你的疑问或想法…（可反复编辑；AI 在第三遍逐条回答，回答会直接出现在这里）"></textarea></div>' +
        '<div class="pop-foot">' +
        (a ? '<button class="pop-btn pop-del" data-act="del">删除</button>' : '<span style="margin-right:auto"></span>') +
        '<button class="pop-btn" data-act="cancel">取消</button>' +
        '<button class="pop-save" data-act="save">保存</button>' +
        "</div>";
      const ta = inner.querySelector(".pop-textarea");
      ta.value = a ? a.note : "";
      ta.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); savePopover(); }
      });
    } else {
      let mid = '<div class="pop-main pop-view"><div class="pop-note">' + mdLite(a ? a.note : "") + "</div>";
      if (a && a.answer) {
        mid += '<div class="pop-answer"><div class="pop-answer-label">AI 回答' +
          (a.answered_at ? '<span class="pop-answer-time">' + esc(a.answered_at.slice(0, 10)) + "</span>" : "") +
          '</div><div class="pop-answer-body">' + mdLite(a.answer) + "</div></div>";
      } else {
        mid += '<div class="pop-pending-hint">AI 尚未回答 · 进入第三遍时逐条解答，写回后本页自动更新</div>';
      }
      mid += "</div>";
      inner.innerHTML = quoteHtml(popCtx) + mid +
        '<div class="pop-foot"><button class="pop-btn pop-del" data-act="del">删除</button>' +
        '<button class="pop-btn" data-act="close">关闭</button>' +
        '<button class="pop-btn" data-act="edit">编辑</button></div>';
      typeset(inner);
    }
    inner.querySelectorAll("[data-act]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const act = btn.getAttribute("data-act");
        if (act === "save") savePopover();
        else if (act === "del") { if (deleteAnnotation(popEditingId, true)) closePopover(); }
        else if (act === "edit") {
          renderPopover("edit");
          setTimeout(function () { const t = popEl.querySelector(".pop-textarea"); if (t) t.focus(); }, 30);
        }
        else if (act === "cancel") { if (popEditingId) renderPopover("view"); else closePopover(); }
        else if (act === "close") closePopover();
      });
    });
    placePopover();
  }

  async function savePopover() {
    const ta = popEl.querySelector(".pop-textarea");
    const note = ta ? ta.value.trim() : "";
    if (!note) { if (ta) ta.focus(); return; }
    const now = new Date().toISOString();
    if (popEditingId) {
      const a = getAnno(popEditingId);
      if (a) { a.note = note; a.updated_at = now; }
    } else {
      const anno = {
        id: "anno_" + Date.now(),
        type: popCtx.type,
        section_id: popCtx.section_id,
        section_title: popCtx.section_title,
        block_id: popCtx.block_id,
        quote: popCtx.quote || "",
        note: note,
        answer: "",
        answered_at: "",
        created_at: now,
        updated_at: now,
      };
      annotations.push(anno);
      const blockEl = anno.block_id ? document.querySelector('[data-block-id="' + anno.block_id + '"]') : null;
      if (blockEl) {
        if (anno.type === "text" && anno.quote) {
          if (!wrapQuoteInBlock(blockEl, anno.quote, anno.id)) blockEl.classList.add("has-anno-fallback");
        } else {
          blockEl.classList.add("has-anno-fallback");
        }
      }
    }
    persistAnnotations();
    // 服务模式零操作；file:// 且尚未绑定保存位置时，就地引导选择一次 PaperVault 根目录
    if (!SERVER.on && !fsaBound() && fsaSupported()) { await bindDirectory(); }
    rerenderAnnoUI();
    closePopover();
  }

  function deleteAnnotation(id, needConfirm) {
    const a = getAnno(id);
    if (!a) return false;
    if (needConfirm && !window.confirm("确定删除这条批注？")) return false;
    annotations = annotations.filter(function (x) { return x.id !== id; });
    persistAnnotations();
    const m = document.querySelector('.user-anno-mark[data-anno-id="' + id + '"]');
    if (m) {
      const p = m.parentNode;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    }
    if (a.block_id && !annotations.some(function (x) { return x.block_id === a.block_id; })) {
      const b = document.querySelector('[data-block-id="' + a.block_id + '"]');
      if (b) b.classList.remove("has-anno-fallback");
    }
    rerenderAnnoUI();
    return true;
  }

  /* ---------- 划词气泡 ---------- */

  function hideTooltip() {
    if (tooltipEl) tooltipEl.style.display = "none";
    pendingRange = null;
  }

  function initTooltip() {
    tooltipEl = el("button", "floating-tooltip", ICONS.pencil + "<span>批注</span>");
    tooltipEl.style.display = "none";
    document.body.appendChild(tooltipEl);

    document.addEventListener("mouseup", function (e) {
      if (e.target.closest(".anno-popover") || e.target.closest(".drawer-panel") ||
          e.target.closest(".floating-tooltip") || e.target.closest(".toc-sidebar")) return;
      const sel = window.getSelection();
      const text = sel.toString().trim();
      if (text.length > 1 && sel.rangeCount > 0) {
        const range = sel.getRangeAt(0).cloneRange();
        const rect = range.getBoundingClientRect();
        const cx = Math.max(44, Math.min(rect.left + rect.width / 2, window.innerWidth - 44));
        tooltipEl.style.left = (cx + window.scrollX) + "px";
        tooltipEl.style.top = (rect.top + window.scrollY - 8) + "px";
        tooltipEl.style.display = "inline-flex";
        pendingRange = range;
      } else {
        hideTooltip();
      }
    });

    tooltipEl.addEventListener("click", function () {
      if (!pendingRange) return;
      const range = pendingRange;
      const text = range.toString().trim();
      const rect = range.getBoundingClientRect();
      let node = range.commonAncestorContainer;
      if (node.nodeType === 3) node = node.parentNode;
      const blockEl = node.closest ? node.closest("[data-block-id]") : null;
      const secEl = node.closest ? node.closest(".paper-section") : null;
      window.getSelection().removeAllRanges();
      openPopover({
        type: "text",
        section_id: secEl ? secEl.id : "section",
        section_title: secEl ? secEl.getAttribute("data-sec-title") : "正文章节",
        block_id: blockEl ? blockEl.getAttribute("data-block-id") : null,
        block_label: blockEl ? blockTypeLabel(blockEl) : "",
        quote: text,
      }, null, staticAnchor(rect));
    });

    document.addEventListener("scroll", hideTooltip, { passive: true, capture: true });
    document.addEventListener("mousedown", function (e) {
      if (tooltipEl && !tooltipEl.contains(e.target)) hideTooltip();
    });
  }

  function blockTypeLabel(blockEl) {
    if (!blockEl) return "板块";
    for (const cls of blockEl.classList) {
      if (cls.indexOf("block-") === 0 && cls !== "block") {
        return { "block-formula": "公式", "block-table": "表格", "block-figure": "图", "block-algorithm": "算法", "block-list": "列表", "block-para": "段落" }[cls] || cls;
      }
    }
    return "板块";
  }

  /* ---------- 板块批注按钮 / 高亮点击（事件委托） ---------- */

  function initBlockAnnoButtons() {
    document.addEventListener("click", function (e) {
      const btn = e.target.closest(".block-anno-btn");
      if (btn) {
        const blockId = btn.getAttribute("data-block-id");
        const blockEl = document.querySelector('[data-block-id="' + blockId + '"]');
        const secEl = blockEl ? blockEl.closest(".paper-section") : null;
        const existing = annotations
          .filter(function (a) { return a.block_id === blockId && a.type === "block"; })
          .sort(function (x, y) { return (x.updated_at < y.updated_at ? 1 : -1); })[0];
        const ctx = {
          type: "block",
          section_id: existing ? existing.section_id : (secEl ? secEl.id : "section"),
          section_title: existing ? existing.section_title : (secEl ? secEl.getAttribute("data-sec-title") : "章节"),
          block_id: blockId,
          block_label: blockTypeLabel(blockEl),
          quote: "",
        };
        openPopover(ctx, existing || null, elAnchor(btn));
        return;
      }
      const mark = e.target.closest(".user-anno-mark");
      if (mark) {
        const a = getAnno(mark.getAttribute("data-anno-id"));
        if (a) {
          openPopover(
            { type: a.type, section_id: a.section_id, section_title: a.section_title, block_id: a.block_id, block_label: "", quote: a.quote },
            a, elAnchor(mark)
          );
        }
      }
    });
  }

  /* ---------- 内联高亮：写入与重放 ---------- */

  function wrapQuoteInBlock(blockEl, quote, annoId) {
    if (!blockEl || !quote) return false;
    const walker = document.createTreeWalker(blockEl, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      if (node.parentNode && node.parentNode.classList && node.parentNode.classList.contains("user-anno-mark")) continue;
      const idx = node.nodeValue.indexOf(quote);
      if (idx >= 0) {
        try {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + quote.length);
          const span = document.createElement("span");
          span.className = "user-anno-mark" + (getAnno(annoId) && getAnno(annoId).answer ? " answered" : "");
          span.setAttribute("data-anno-id", annoId);
          range.surroundContents(span);
          return true;
        } catch (err) { return false; }
      }
    }
    return false;
  }

  function replayHighlights() {
    annotations.forEach(function (a) {
      const blockEl = a.block_id ? document.querySelector('[data-block-id="' + a.block_id + '"]') : null;
      if (!blockEl) return;
      if (a.type === "text" && a.quote) {
        if (!wrapQuoteInBlock(blockEl, a.quote, a.id)) blockEl.classList.add("has-anno-fallback");
      } else {
        blockEl.classList.add("has-anno-fallback");
      }
    });
  }

  /* ---------- AI 回答卡（原位渲染） ---------- */

  function renderAnswerCards() {
    document.querySelectorAll(".answer-card").forEach(function (c) { c.remove(); });
    annotations.forEach(function (a) {
      if (!a.answer) return;
      const blockEl = a.block_id ? document.querySelector('[data-block-id="' + a.block_id + '"]') : null;
      if (!blockEl) return;
      const card = el("div", "answer-card");
      card.innerHTML =
        '<div class="answer-head">AI 回答' +
        (a.answered_at ? '<span class="answer-time">' + esc(a.answered_at.slice(0, 10)) + "</span>" : "") +
        "</div>" +
        '<div class="answer-q">' + esc(a.note) +
        (a.quote ? " —— “" + esc(String(a.quote).slice(0, 120)) + "”" : "") +
        "</div>" +
        '<div class="answer-body">' + mdLite(a.answer) + "</div>";
      blockEl.appendChild(card);
      typeset(card);
    });
  }

  // 打印完整性：未回答的批注也生成占位卡——"个人精读版 PDF"里问题不丢失
  window.addEventListener("beforeprint", function () {
    annotations.forEach(function (a) {
      if (a.answer) return;
      const blockEl = a.block_id ? document.querySelector('[data-block-id="' + a.block_id + '"]') : null;
      if (!blockEl) return;
      const card = el("div", "answer-card pending");
      card.innerHTML =
        '<div class="answer-head">我的批注（待回答）</div>' +
        '<div class="answer-q">' + esc(a.note) +
        (a.quote ? " —— “" + esc(String(a.quote).slice(0, 120)) + "”" : "") +
        "</div>";
      blockEl.appendChild(card);
    });
  });
  window.addEventListener("afterprint", function () {
    document.querySelectorAll(".answer-card.pending").forEach(function (c) { c.remove(); });
  });

  function rerenderAnnoUI() {
    renderAnswerCards();
    renderDrawer();
    annotations.forEach(function (a) {
      const m = document.querySelector('.user-anno-mark[data-anno-id="' + a.id + '"]');
      if (m) m.classList.toggle("answered", !!a.answer);
    });
    // 浮窗处于查看模式且对应批注刚被 AI 回答 → 就地刷新浮窗内容
    if (popEl && popEl.classList.contains("open") && popMode === "view" && popEditingId) {
      renderPopover("view");
    }
  }

  /* ============================== 批注抽屉 ============================== */

  let drawerPanel = null, drawerOverlay = null, drawerBody = null, countBadge = null;

  function buildDrawer() {
    drawerOverlay = el("div", "drawer-overlay");
    document.body.appendChild(drawerOverlay);
    drawerPanel = el("div", "drawer-panel");
    drawerPanel.innerHTML =
      '<div class="drawer-header"><span class="drawer-title">批注 <span class="count-badge" id="q-count">0</span></span>' +
      '<button class="drawer-close-btn" title="关闭">' + ICONS.close + "</button></div>" +
      '<div class="drawer-body" id="drawer-q-list"></div>' +
      '<div class="drawer-footer">' +
      '<button class="pop-btn" id="btn-copy-clipboard">复制全部批注（可粘贴给 AI）</button>' +
      '<button class="pop-btn" id="btn-export-json">导出备份 annotations.json</button>' +
      '<div class="drawer-tip">划词或悬停板块点铅笔图标即可写批注。由本地服务打开时批注全自动保存到论文文件夹（零弹窗）；直接双击 reader.html 打开时，首次保存会引导选择一次保存位置。AI 在第三遍逐条回答并写回，本页每 20 秒自动检测更新。</div>' +
      "</div>";
    document.body.appendChild(drawerPanel);
    drawerBody = drawerPanel.querySelector("#drawer-q-list");
    countBadge = drawerPanel.querySelector("#q-count");

    document.getElementById("btn-open-drawer").addEventListener("click", openDrawer);
    drawerOverlay.addEventListener("click", closeDrawer);
    drawerPanel.querySelector(".drawer-close-btn").addEventListener("click", closeDrawer);
    drawerPanel.querySelector("#btn-copy-clipboard").addEventListener("click", copyToClipboard);
    drawerPanel.querySelector("#btn-export-json").addEventListener("click", downloadJson);
  }

  function openDrawer() { drawerOverlay.style.display = "block"; drawerPanel.classList.add("open"); renderDrawer(); }
  function closeDrawer() { drawerOverlay.style.display = "none"; drawerPanel.classList.remove("open"); }

  function renderDrawer() {
    if (!drawerBody) return;
    const sorted = annotations.slice().sort(function (x, y) {
      const ax = x.answer ? 1 : 0, ay = y.answer ? 1 : 0;
      if (ax !== ay) return ax - ay; // 未答在前
      return x.created_at < y.created_at ? 1 : -1;
    });
    countBadge.innerText = annotations.length;
    document.getElementById("top-q-count").innerText = annotations.length;
    if (!sorted.length) {
      drawerBody.innerHTML =
        '<div class="drawer-empty">暂无批注<br><span style="font-size:12.5px">选中正文文字，或悬停公式 / 表格 / 图块点铅笔图标</span></div>';
      return;
    }
    drawerBody.innerHTML = sorted.map(function (item) {
      const answered = !!item.answer;
      return (
        '<div class="question-list-item' + (answered ? " answered-item" : "") + '" data-id="' + esc(item.id) + '">' +
        '<div class="q-meta"><span class="chip ' + (answered ? "done" : "pending") + '">' + (answered ? "已答" : "待答") + "</span>" +
        "<span>[" + esc(item.section_title) + "]" + (item.type === "block" ? " · 板块批注" : "") + "</span></div>" +
        (item.quote ? '<div class="q-quote">“' + esc(String(item.quote).length > 80 ? item.quote.slice(0, 80) + "…" : item.quote) + "”</div>" : "") +
        '<div class="q-content">' + esc(item.note) + "</div>" +
        (answered ? '<div class="q-answer-preview">' + esc(String(item.answer).slice(0, 140)) + "</div>" : "") +
        '<div class="q-actions">' +
        '<button class="pop-btn" onclick="window.armGotoAnnotation(\'' + item.id + '\')">查看</button>' +
        '<button class="pop-btn" style="color:var(--danger);margin-left:auto" onclick="window.armDeleteAnnotation(\'' + item.id + '\')">删除</button>' +
        "</div></div>"
      );
    }).join("");
  }

  window.armGotoAnnotation = function (id) {
    const a = getAnno(id);
    if (!a) return;
    const target = document.querySelector('.user-anno-mark[data-anno-id="' + id + '"]') ||
      (a.block_id ? document.querySelector('[data-block-id="' + a.block_id + '"]') : null);
    closeDrawer();
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("flash");
    setTimeout(function () { target.classList.remove("flash"); }, 1200);
    setTimeout(function () {
      openPopover(
        { type: a.type, section_id: a.section_id, section_title: a.section_title, block_id: a.block_id, block_label: a.type === "block" ? blockTypeLabel(target) : "", quote: a.quote },
        a, elAnchor(target)
      );
    }, 450);
  };

  window.armDeleteAnnotation = function (id) { deleteAnnotation(id, false); };

  function copyToClipboard() {
    if (!annotations.length) { showToast("当前批注为空"); return; }
    let text = "【文献批注清单 - " + paperId + "】\n\n";
    annotations.forEach(function (a, i) {
      text += (i + 1) + ". [" + a.section_title + "]" + (a.quote ? " 原文：\"" + a.quote + "\"" : "（板块批注）") + "\n   我的批注: " + a.note + "\n";
      if (a.answer) text += "   AI回答: " + a.answer + "\n";
      text += "\n";
    });
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { showToast("批注清单已复制到剪贴板"); },
        function () { window.prompt("复制失败，请手动全选复制：", text); }
      );
    } else {
      window.prompt("请手动全选复制以下内容：", text);
    }
  }

  /* ================= 存储：服务模式（零弹窗）/ FSA 兜底 / localStorage ================= */

  const SERVER = { on: false };

  const IDB_NAME = "arm-fs-handles";
  const IDB_STORE = "handles";
  let vaultDirHandle = null;   // PaperVault 根目录句柄（绑一次全库通用）
  let paperDirHandle = null;   // 单篇论文目录句柄
  let annoFileHandle = null;   // 解析出的 annotations.json 句柄（缓存）
  let legacyFileHandle = null; // v3.0 及之前绑定的文件句柄（兼容）
  let autoWriteTimer = null;
  let lastModified = 0;
  let pollTimer = null;
  let toastEl = null, toastTimer = null;

  function showToast(msg) {
    if (!toastEl) { toastEl = el("div", "arm-toast"); document.body.appendChild(toastEl); }
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 3000);
  }

  function idbOpen() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) return reject(new Error("no idb"));
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = function () { req.result.createObjectStore(IDB_STORE); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function idbSet(key, val) {
    return idbOpen().then(function (db) {
      return new Promise(function (resolve, reject) {
        const tx = db.transaction(IDB_STORE, "readwrite");
        tx.objectStore(IDB_STORE).put(val, key);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function idbGet(key) {
    return idbOpen().then(function (db) {
      return new Promise(function (resolve, reject) {
        const tx = db.transaction(IDB_STORE, "readonly");
        const req = tx.objectStore(IDB_STORE).get(key);
        req.onsuccess = function () { resolve(req.result || null); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function setStatus(text, ok) {
    const s = document.getElementById("save-status");
    if (!s) return;
    s.textContent = text;
    s.classList.toggle("ok", !!ok);
    s.classList.remove("clickable");
    s.onclick = null;
    s.title = "";
  }

  // 未绑定状态：状态行本身成为绑定入口
  function setStatusBind(text) {
    const s = document.getElementById("save-status");
    if (!s) return;
    s.textContent = text || "批注仅暂存浏览器 · 点击选择保存位置（PaperVault 根目录）";
    s.classList.remove("ok");
    s.classList.add("clickable");
    s.title = "选择一次 PaperVault 根目录，之后所有论文的批注全自动写回，不再弹任何对话框";
    s.onclick = function () {
      bindDirectory().then(function (ok) { if (ok) showToast("已绑定保存位置 · 之后全自动写回"); });
    };
  }

  function fsaSupported() {
    return typeof window.showDirectoryPicker === "function" || typeof window.showSaveFilePicker === "function";
  }

  function fsaBound() {
    return !!(vaultDirHandle || paperDirHandle || legacyFileHandle || annoFileHandle);
  }

  async function ensurePermission(handle, needRequest) {
    if (!handle || !handle.queryPermission) return false;
    const opts = { mode: "readwrite" };
    if ((await handle.queryPermission(opts)) === "granted") return true;
    if (needRequest && handle.requestPermission && (await handle.requestPermission(opts)) === "granted") return true;
    return false;
  }

  // 解析本篇论文的 annotations.json 句柄：
  // vault 根目录 → papers/<paper_id>/annotations.json；论文目录 → 直接取；都没有 → 旧版文件句柄
  async function resolveAnnoFile(create) {
    if (annoFileHandle) return annoFileHandle;
    if (vaultDirHandle) {
      try {
        const papersDir = await vaultDirHandle.getDirectoryHandle("papers", { create: false });
        const paperDir = await papersDir.getDirectoryHandle(paperId, { create: false });
        annoFileHandle = await paperDir.getFileHandle("annotations.json", { create: !!create });
        return annoFileHandle;
      } catch (e) {}
    }
    if (paperDirHandle) {
      try {
        annoFileHandle = await paperDirHandle.getFileHandle("annotations.json", { create: !!create });
        return annoFileHandle;
      } catch (e) {}
    }
    return legacyFileHandle || null;
  }

  async function writeAnnotationsFile() {
    // 首选：本地服务 API 直写论文文件夹（零弹窗）
    if (SERVER.on) {
      try {
        const r = await fetch("/api/annotations", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildExportObject()),
        });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || "put failed");
        lastModified = j.mtime || lastModified;
        setStatus("已自动保存 · " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }), true);
        return true;
      } catch (err) {
        setStatus("保存失败 · 本地服务未响应", false);
        return false;
      }
    }
    const fh = await resolveAnnoFile(true);
    if (!fh || !(await ensurePermission(fh, false))) return false;
    try {
      const writable = await fh.createWritable();
      await writable.write(new Blob([JSON.stringify(buildExportObject(), null, 2)], { type: "application/json" }));
      await writable.close();
      try { lastModified = (await fh.getFile()).lastModified; } catch (e) {}
      setStatus("批注自动写回 · " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }), true);
      return true;
    } catch (err) {
      setStatusBind("写回失败 · 点击重新选择保存位置");
      return false;
    }
  }

  function scheduleAutoWrite() {
    if (!SERVER.on && !fsaBound()) return;
    if (autoWriteTimer) clearTimeout(autoWriteTimer);
    autoWriteTimer = setTimeout(writeAnnotationsFile, 1000);
  }

  // 绑定保存位置（一次即可）：优先选 PaperVault 根目录 → 之后所有论文静默读写；
  // 选论文自己的文件夹 → 仅该论文。必须在真实用户手势链内调用（浮窗保存 / 状态行点击）。
  async function bindDirectory() {
    if (typeof window.showDirectoryPicker !== "function") return bindLegacyFile();
    let dir;
    try {
      dir = await window.showDirectoryPicker({ id: "arm_vault", mode: "readwrite", startIn: "documents" });
    } catch (err) {
      if (err && err.name === "AbortError") { setStatusBind(); return false; }
      showToast("选择文件夹失败：" + err.message);
      return false;
    }
    // 自动识别所选目录：含 paper-data.js → 论文目录；含 papers/<paper_id> → PaperVault 根
    try {
      await dir.getFileHandle("paper-data.js");
      paperDirHandle = dir;
      await idbSet("dir_" + paperId, dir);
      annoFileHandle = null;
    } catch (e) {
      try {
        const papersDir = await dir.getDirectoryHandle("papers", { create: false });
        await papersDir.getDirectoryHandle(paperId, { create: false });
        vaultDirHandle = dir;
        await idbSet("vault_dir", dir);
        annoFileHandle = null;
      } catch (e2) {
        showToast("这个文件夹里没找到这篇论文：请选 PaperVault 根目录（含 papers 子文件夹）或该论文自己的文件夹");
        return false;
      }
    }
    lastModified = 0;
    await writeAnnotationsFile();
    startPolling();
    return true;
  }

  // 兜底：仅支持文件选择器的浏览器
  async function bindLegacyFile() {
    if (typeof window.showSaveFilePicker !== "function") { downloadJson(); return false; }
    try {
      if (legacyFileHandle && (await ensurePermission(legacyFileHandle, true))) { await writeAnnotationsFile(); startPolling(); return true; }
      legacyFileHandle = await window.showSaveFilePicker({
        suggestedName: "annotations.json",
        types: [{ description: "JSON", accept: { "application/json": [".json"] } }],
      });
      await idbSet(paperId, legacyFileHandle);
      lastModified = 0;
      await writeAnnotationsFile();
      startPolling();
      return true;
    } catch (err) {
      if (err && err.name === "AbortError") { setStatusBind(); return false; }
      showToast("写回失败：" + err.message + " · 已改为导出备份");
      downloadJson();
      return false;
    }
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(buildExportObject(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "annotations.json";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    showToast("已导出 annotations.json · 请移入论文文件夹（papers/" + paperId + "/）");
  }

  // 磁盘合并：本地无 answer 而磁盘有（AI 已答）→ 采纳；磁盘 answered_at 更新（AI 修订过答案）→ 采纳修订；磁盘独有的批注 → 并入
  function mergeDisk(data) {
    const byId = {};
    (data.annotations || []).forEach(function (a) { byId[a.id] = a; });
    let answers = 0, added = 0;
    annotations.forEach(function (a) {
      const o = byId[a.id];
      if (o) {
        const diskNewer = o.answer && (!a.answer || (o.answered_at && o.answered_at > (a.answered_at || "")));
        if (diskNewer && a.answer !== o.answer) {
          a.answer = o.answer;
          a.answered_at = o.answered_at || a.answered_at;
          answers++;
        } else if (diskNewer) {
          a.answered_at = o.answered_at;
        }
        delete byId[a.id];
      }
    });
    Object.keys(byId).forEach(function (k) { annotations.push(byId[k]); added++; });
    return { answers: answers, added: added };
  }

  // AI 写回轮询：每 20s + 页面回到前台时检测 lastModified
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(pollDisk, 20000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") pollDisk();
    });
  }

  async function pollDisk() {
    if (document.visibilityState !== "visible") return;
    if (SERVER.on) {
      try {
        const r = await fetch("/api/annotations", { cache: "no-store" });
        const j = await r.json();
        if (j.mtime && j.mtime !== lastModified) {
          lastModified = j.mtime;
          const res = mergeDisk(j.data || {});
          if (res.answers > 0 || res.added > 0) {
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(annotations)); } catch (e) {}
            rerenderAnnoUI();
            if (res.answers > 0) showToast("AI 已更新 " + res.answers + " 条回答");
          }
        }
      } catch (e) {}
      return;
    }
    if (!fsaBound()) return;
    const fh = await resolveAnnoFile(false);
    if (!fh || !(await ensurePermission(fh, false))) return;
    try {
      const f = await fh.getFile();
      if (lastModified && f.lastModified <= lastModified) return;
      const data = JSON.parse(await f.text());
      lastModified = f.lastModified;
      const r = mergeDisk(data);
      if (r.answers > 0 || r.added > 0) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(annotations)); } catch (e) {}
        rerenderAnnoUI();
        if (r.answers > 0) showToast("AI 已更新 " + r.answers + " 条回答");
      }
    } catch (e) { /* 磁盘文件暂时不可读则跳过本轮 */ }
  }

  // 启动探测：优先服务模式（零弹窗）；失败才走 FSA 兜底
  async function initStorage() {
    try {
      const r = await fetch("/api/ping", { cache: "no-store" });
      const j = await r.json();
      if (j && j.ok && j.paper_id === paperId) {
        SERVER.on = true;
        try {
          const r2 = await fetch("/api/annotations", { cache: "no-store" });
          const j2 = await r2.json();
          lastModified = j2.mtime || 0;
          const res = mergeDisk(j2.data || {});
          if (res.answers > 0 || res.added > 0) {
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(annotations)); } catch (e) {}
            rerenderAnnoUI();
          }
        } catch (e) {}
        setStatus("已连接本地服务 · 批注自动保存", true);
        startPolling();
        return;
      }
    } catch (e) {}
    await initFSA();
  }

  // FSA 兜底启动：恢复目录/文件句柄 → 权限静默检查 → 读盘合并（AI 可能已写 answer）→ 开始轮询
  async function initFSA() {
    if (!fsaSupported()) { setStatus("此浏览器不支持自动写回 · 批注保存在本地，可从批注面板导出备份", false); return; }
    try { vaultDirHandle = (await idbGet("vault_dir")) || null; } catch (e) {}
    try { paperDirHandle = (await idbGet("dir_" + paperId)) || null; } catch (e) {}
    try { legacyFileHandle = (await idbGet(paperId)) || null; } catch (e) {}
    if (!fsaBound()) { setStatusBind("批注暂存浏览器 · 保存第一条批注时选择一次保存位置即可"); return; }
    let ok = false;
    if (vaultDirHandle && (await ensurePermission(vaultDirHandle, false))) ok = true;
    else if (paperDirHandle && (await ensurePermission(paperDirHandle, false))) ok = true;
    else if (legacyFileHandle && (await ensurePermission(legacyFileHandle, false))) ok = true;
    if (!ok) { setStatusBind("点击恢复自动写回（浏览器重启后需重新点一次）"); return; }
    try {
      const fh = await resolveAnnoFile(false);
      if (fh) {
        const file = await fh.getFile();
        lastModified = file.lastModified;
        const data = JSON.parse(await file.text());
        const r = mergeDisk(data);
        if (r.answers > 0 || r.added > 0) {
          try { localStorage.setItem(STORAGE_KEY, JSON.stringify(annotations)); } catch (e) {}
          rerenderAnnoUI();
        }
      }
    } catch (e) { /* 磁盘文件可能尚不存在：首次写入时自动创建 */ }
    setStatus("批注自动写回 · 已绑定", true);
    startPolling();
  }

  /* ============================== 其余交互 & 启动 ============================== */

  /* ============================== 原文 PDF 侧栏（服务模式专用） ============================== */

  let pdfDoc = null, pdfSlots = [], pdfRendered = {}, pdfJsPromise = null, pdfScrollTarget = 1;

  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve();
    if (pdfJsPromise) return pdfJsPromise;
    pdfJsPromise = new Promise(function (resolve, reject) {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js";
      s.onload = function () {
        try {
          window.pdfjsLib.GlobalWorkerOptions.workerSrc =
            "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";
          resolve();
        } catch (e) { reject(e); }
      };
      s.onerror = function () { reject(new Error("PDF.js 加载失败（需联网）")); };
      document.head.appendChild(s);
    });
    return pdfJsPromise;
  }

  function togglePdfPane() {
    const open = document.body.classList.toggle("pdf-open");
    try { localStorage.setItem("arm_pdf_open_" + paperId, open ? "1" : "0"); } catch (e) {}
    if (open) pdfEnsureLoaded();
  }

  async function pdfEnsureLoaded() {
    const vp = document.getElementById("pdf-viewport");
    try {
      await loadPdfJs();
      if (!pdfDoc) {
        pdfDoc = await window.pdfjsLib.getDocument({ url: "source.pdf" }).promise;
        vp.innerHTML = "";
        pdfSlots = [];
        for (let i = 1; i <= pdfDoc.numPages; i++) {
          const slot = el("div", "pdf-slot");
          slot.setAttribute("data-page", i);
          slot.innerHTML = '<span class="pdf-slot-num">' + i + "</span>";
          vp.appendChild(slot);
          pdfSlots.push(slot);
        }
        vp.addEventListener("scroll", pdfOnScroll, { passive: true });
        updatePdfLabel(1);
        await renderPdfPage(1);
        renderPdfRange(2);
      }
    } catch (err) {
      vp.innerHTML = '<div class="pdf-error">原文 PDF 加载失败：' + esc(err.message || String(err)) + "</div>";
    }
  }

  async function renderPdfPage(n) {
    if (!pdfDoc || pdfRendered[n]) return;
    pdfRendered[n] = true;
    try {
      const page = await pdfDoc.getPage(n);
      const vp0 = page.getViewport({ scale: 1 });
      const paneW = (document.getElementById("pdf-pane").clientWidth || 460) - 28;
      const scale = Math.max(0.4, paneW / vp0.width);
      const vp = page.getViewport({ scale: scale });
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      const slot = pdfSlots[n - 1];
      slot.style.minHeight = "0";
      slot.appendChild(canvas);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    } catch (e) {
      pdfRendered[n] = false;
    }
  }

  async function renderPdfRange(from) {
    if (!pdfDoc) return;
    for (let i = from; i <= pdfDoc.numPages; i++) {
      if (!document.body.classList.contains("pdf-open")) return; // 面板已关则停止
      await renderPdfPage(i);
    }
  }

  function pdfGoto(n) {
    if (!pdfDoc || !n || n < 1 || n > pdfDoc.numPages) return;
    const slot = pdfSlots[n - 1];
    slot.scrollIntoView({ block: "start" });
    updatePdfLabel(n);
    pdfScrollTarget = n;
    renderPdfPage(n).then(function () { renderPdfRange(1); });
  }

  function pdfOnScroll() {
    const vpTop = document.getElementById("pdf-viewport").getBoundingClientRect().top;
    let cur = pdfScrollTarget;
    for (let i = 0; i < pdfSlots.length; i++) {
      if (pdfSlots[i].getBoundingClientRect().bottom > vpTop + 40) { cur = i + 1; break; }
    }
    updatePdfLabel(cur);
  }

  function updatePdfLabel(n) {
    const lb = document.getElementById("pdf-page-label");
    if (lb) lb.textContent = pdfDoc ? "第 " + n + " / " + pdfDoc.numPages + " 页" : "";
  }

  // 服务模式 + source.pdf 存在 → 显示"原文"按钮；恢复上次开合状态
  async function initPdf() {
    const btn = document.getElementById("btn-pdf");
    if (!SERVER.on || window.innerWidth < 1000) return;
    try {
      const r = await fetch("source.pdf", { method: "HEAD", cache: "no-store" });
      if (!r.ok) return;
    } catch (e) { return; }
    btn.style.display = "";
    btn.addEventListener("click", togglePdfPane);
    const closeBtn = document.getElementById("pdf-close");
    if (closeBtn) closeBtn.addEventListener("click", togglePdfPane);
    let saved = null;
    try { saved = localStorage.getItem("arm_pdf_open_" + paperId); } catch (e) {}
    // 服务模式下默认打开原文侧栏（用户明确关闭过则尊重其选择）
    const wantOpen = saved === null ? true : saved === "1";
    if (wantOpen && window.innerWidth >= 1000) {
      document.body.classList.add("pdf-open");
      pdfEnsureLoaded();
    }
    // 章节标题 / 目录点击 → PDF 联动跳页（面板开着才生效）
    document.addEventListener("click", function (e) {
      if (!document.body.classList.contains("pdf-open") || !pdfDoc) return;
      let page = null;
      const tocLink = e.target.closest(".toc-link");
      if (tocLink) page = tocLink.getAttribute("data-page");
      if (!page) {
        const head = e.target.closest(".section-title");
        if (head) {
          const sec = head.closest(".paper-section");
          page = sec ? sec.getAttribute("data-page") : null;
        }
      }
      if (page) pdfGoto(parseInt(page, 10));
    });
  }

  function initOrigToggles() {
    document.addEventListener("click", function (e) {
      const btn = e.target.closest(".toggle-orig-btn");
      if (!btn) return;
      const fold = btn.parentNode.querySelector(".original-text-fold");
      if (fold) fold.classList.toggle("show");
      const origInline = btn.parentNode.querySelector(".orig-inline");
      if (origInline) origInline.classList.toggle("show");
    });
  }

  function initBurger() {
    const burger = document.getElementById("btn-burger");
    const sb = document.getElementById("toc-sidebar");
    if (!burger || !sb) return;
    burger.addEventListener("click", function (e) {
      e.stopPropagation();
      sb.classList.toggle("mobile-open");
    });
    document.addEventListener("click", function (e) {
      if (!sb.classList.contains("mobile-open")) return;
      if (e.target.closest(".toc-link")) { sb.classList.remove("mobile-open"); return; }
      if (!sb.contains(e.target) && !burger.contains(e.target)) sb.classList.remove("mobile-open");
    });
  }

  function initPrint() {
    const btn = document.getElementById("btn-print");
    if (btn) btn.addEventListener("click", function () { window.print(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderHeader();
    renderTOC();
    renderPipeline();
    renderSections();
    renderPass3();
    initOrigToggles();
    initViewMode();
    initScrollspy();
    initBurger();
    initPrint();
    loadAnnotations();
    buildPopover();
    buildDrawer();
    initTooltip();
    initBlockAnnoButtons();
    replayHighlights();
    renderAnswerCards();
    renderDrawer();
    typeset();
    initStorage().then(initPdf);
  });
})();
