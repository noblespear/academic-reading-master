# 批注与自动问答

三视图共享store，各自锚定实际文字。英文offset不能套在中文上；source_refs用于跳转，不能伪装成同一选区。

键入保存草稿，点击提交自动排队。问答默认折叠且独立于正文；选区末尾气泡展开浮层。身份色固定，重叠异色并列色条/编号；状态用独立角标、文字及未读标记。

anchor含view、language、content_id、field、content_version、start/end、exact、prefix/suffix，跨段保存fragments。canonical排除按钮、浮层、数学辅助节点；offset+exact校验，必要时上下文重定位，多候选标unlocated。不得保存屏幕像素作为唯一定位。

定位与可见性分开：折叠讲解中的锚点仍然有效，但不参与高亮、气泡或跨段选区捕获。显式检查所有关闭的details祖先，不依赖缓存矩形或computed display。展开、滚动、缩放、字体及图片加载后从当前正文重新测量；数学原子只测一次，重叠色条不移动选区背景。气泡不得遮挡选区或后续正文，必要时放到旁边空白处，并以细虚线指回末端。

追问保留线程；编辑递增修订并保留旧答案；删除失效未完成任务；查看新答后消未读。服务独占状态写入，不能整份JSON覆盖答案。AI导读按价值设置，无固定密度。答疑见answer_worker_guide.md，接口见bridge_protocol.md。
