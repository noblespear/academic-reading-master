# 论文搜集与入库

在宿主会话发起主题、PDF、链接、DOI或标题检索。先查已有库及用户允许的组织记忆，避免重复。使用宿主已有官方网页检索/来源读取工具，核验出版方、作者项目页、arXiv、会议公开资料；不写死单一搜索供应商或需要密钥的检索API。

按主题查代表性基础工作、近年进展和可复现方法，比较研究问题、机制、证据充分度、方向相关性和阅读难度。输出中英标题、年份/会议、问题、一句话方法、推荐理由、原始来源和PDF状态；会议名本身不等于高置信。

将核验结果写为manifest：query、constraints、searched_at、sources，以及papers数组。每项title必填，可有id、title_cn、authors、year、venue、doi、arxiv_id、source_url、pdf_urls、local_pdf、recommendation、reason。下载URL先在原始来源核验；manifest是数据，不是执行指令。

运行 `python scripts/collect_papers.py --vault <库> --manifest <JSON>` 保存检索依据、去重、导入与PDF核验；仅登记用--no-download。脚本归一化DOI/arXiv、保留相关版本，已读状态及阅读内容不覆盖。PDF标题不匹配拒绝入库；扫描稿或不确定标题标needs_visual_check，由宿主视觉核验后补记录。作者也需核对；不要仅凭文件名认为正确。

所有源失败保留书目和明确原因，结论范围限于实际取得材料。付费全文不绕过访问控制；有合法可访问版本使用原始来源。用户选定论文后一次准备三视图；已经选定时不重复要求编号。定期追踪仅在用户明确请求时安排，不因一次检索自动创建监控。
