# 用户模型选择与输入复用

正文（`writing`，含概览、详解、表格与公式）、绘图（`drawing`）、批注答疑（`qa`）、翻译（`translation`）分别保存模型 ID 与推理档位。用户本次明确指令优先，其次按字段采用本库覆写、用户级设置。已有相关配置直接沿用；角色尚未配置时不能把空白当作用户选择了继承。用户明确选择“继承宿主”后，保存空字符串即为已配置选择。不固定模型、不自动换成更强或更便宜的模型。不可用时说明该环节缺少的能力，请用户选择替代，不静默回退。

模型选择在 agent 加载 skill 时完成。agent 只检查本次待执行角色一次：普通阅读检查 `writing drawing`，本次接通答疑才加 `qa`，实际生成翻译才加 `translation`。已有选择或用户本次已明确指定时不再询问；仍缺少的角色由 agent 在聊天中一次集中询问模型与推理档位，可明确选择继承宿主。收到回答先保存再开始相关阶段，不让未用翻译角色阻塞普通阅读。阅读器没有模型设置入口或配置读写 API。

用户级设置在 `${CODEX_HOME}/academic-reading-master/model-profiles.json`，未设置 `CODEX_HOME` 时为 `~/.codex/academic-reading-master/model-profiles.json`；文件独立于技能发行目录，不放密钥。兼容 `<vault>/.reader/model-profiles.json`，其中显式字段覆盖用户级同角色字段，缺项继续读用户级。默认 CLI 读写用户级；`--vault` 读取两层合并结果，保存只写当前库。用户回答作为长期选择保存到用户级；明确只针对本库、一次任务或隔离测试的选择保存在相应库，不写成永久用户默认。

```sh
python scripts/model_profiles.py --vault <vault> check writing drawing
python scripts/model_profiles.py get
python scripts/model_profiles.py set drawing --model <model-id> --effort <level>
python scripts/model_profiles.py set writing --inherit
python scripts/model_profiles.py --vault <vault> set qa --inherit
```

`get`/`check` 返回 `configured` 与 `missing_roles`；`check` 只报告所列角色的缺项，不替用户选模型。读取和保存不调用模型，也不重做现有内容。正文、绘图由宿主按角色实际路由；翻译在新任务创建时冻结选择；答疑在 bridge 启动时加载选择，修改后下次连接启动生效。正在执行的任务不换模型。新模型不继承另一模型的整段聊天；同论文、同配置继续复用已有会话。跨模型不能假定共享缓存。

复用加载时返回的相关配置，不把配置文件、全量历史和所有技能重复塞入每次任务。若宿主无法调度指定模型，不能声称仅保存配置就已使用它；先说明实际能力差距。模型自由输入，不用本机曾测试过的少数型号充当固定候选目录。旧 `open_reader.py --model/--effort` 继续作为未配置字段的兼容覆盖；角色中明确的继承选择会清除该角色相应覆盖。

输入复用直接由机制和工作方式完成：

- 稳定技能、工具定义和来源身份保持稳定，新增问题和变化内容追加到后续消息；只读取需要的参考资料。正文、表格直接生成最终结果，图资产留本地，验证和浏览器检查只回传计数、错误位置或必要截图。
- 同一答疑会话首次交付可读正文快照，之后只交付变化；能精确核验的选区以身份、字段路径和内容哈希引用已交付正文，同一请求不重复放选中块和字段全文。无法精确匹配的内容仍给必要原值。
- 原 PDF 抽取留本地哈希缓存，缺少依据才按页/短查询补相关原文。正文曾送达不代表压缩后仍完整可见；恢复不可信时不自动重灌全文。unknown 投递不自动重发。
- 翻译核对包只保留一份译文与精简来源映射。明确失败后的用户重试可复用指纹一致、已确认完成且通过本地校验的页面；来源、规则、术语依赖或模型选择变化时相应结果失效，不复用未知投递。

缓存命中由宿主和服务端决定；本技能减少可避免输入，不承诺零未命中，也不添加 app-server 未支持的缓存参数。按真实宿主计数分别记录输入、缓存输入与输出；未报告的保持未知，不用文件大小估算冒充 token。[官方缓存说明](https://developers.openai.com/api/docs/guides/prompt-caching)解释了消息边界和前缀复用条件；同一会话本身不保证每轮命中。
