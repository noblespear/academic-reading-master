# Reader v3 本地桥接协议 v1

本文描述 `scripts/serve_reader_v3.py`、`reader_store.py`、`bridge_client.py` 与 `bridges/*` **已经实现的行为**。协议版本为 v1；浏览器 API 的 `api_version` 为 `3`。本地服务与 SQLite 不依赖模型 SDK，不选择固定模型，也不保存宿主认证；宿主差异集中在适配器。当前适配器是独立的 Codex app-server stdio 进程，另有明确标记为测试用途的 mock。

## 1. 进程、存储与安全边界

正常启动的进程关系：

```text
操作系统 / 打开阅读器.bat
  └─ open_reader.py（启动就绪后退出）
      └─ serve_reader_v3.py（隐藏、分离的后台 supervisor）
          └─ bridge_client.py（由服务持有与监督）
              └─ codex app-server --stdio（独立宿主进程）
```

服务和 bridge 不由聊天中的 tool session、协作 agent 或浏览器页面维持。关闭阅读页或聊天后，它们继续运行。服务监督 bridge，异常退出后以有限退避重启；模型宿主由 bridge 持有 stdio。Windows 使用隐藏进程标志，launcher 使用 `DETACHED_PROCESS` 和独立进程组。其他系统使用独立 session。

每个 vault 的权威数据位于 `<vault>/.reader/state.sqlite3`，启用 WAL、`synchronous=FULL` 与 SQLite 事务。`papers/<paper_id>/annotations.json` 是原子生成的导出快照，不能作为并发写入接口。模型只提交 `answer + sources`，由服务事务性更新 SQLite 后投影 JSON。浏览器按单条批注写 API，不发送整个批注数组覆盖文件。

本机运行信息：

| 文件 | 用途 |
|---|---|
| `.reader/server.json` | launcher 复用服务与停止服务所需的 PID、URL、本地浏览器 token；停止后写入 `stopped:true` 并移除 token |
| `.reader/bridge-config.json` | 只供本地子进程读取的 bridge URL、独立 bridge token、vault、适配器与可选覆盖；服务停止后删除 |
| `.reader/service.log` / `.reader/bridge.log` | 服务与 bridge 日志；HTTP handler 不记录带 token 的请求 URL |
| `.reader/imports/<paper>-<hash>.json` | 迁移旧批注前保留的原始字节备份 |

服务只绑定 `127.0.0.1`。所有请求验证 `Host` 必须是实际端口的 `127.0.0.1:<port>` 或 `localhost:<port>`；有 `Origin` 时必须同源，`Sec-Fetch-Site: cross-site` 被拒绝。API 不提供跨域授权。写操作必须是 `application/json` 对象，body 上限 2 MiB。浏览器 token 与 bridge token 独立，不能互换；本地 Python HTTP 客户端显式绕过系统 HTTP proxy，避免本地 token 被送给代理。

论文 ID 只允许 `[A-Za-z0-9][A-Za-z0-9_.-]{0,159}`，目录必须是 `<vault>/papers` 的实际直接子目录。静态资源路径在解析后仍须位于该论文目录；隐藏路径、越界路径、`annotations.json`、脚本和 SQLite 文件不能从静态路由读取。完整原始 PDF 使用该论文的 `source.pdf`。这些控制不替代本机用户对 `.reader` 文件的访问权限；不要把该目录发布到外部静态服务器。

## 2. 浏览器 API

所有 API 都是同一 loopback origin 上的请求。`GET /api/session` 和 `GET /api/ping` 无需 token，但仍验证 Host、来源与跨站请求标记。其余浏览器 API 使用 `X-Reader-Token: <session.token>`；SSE 可通过 `token` 查询参数携带同一个 token，以兼容原生 `EventSource`。

### 获取会话与状态

```http
GET /api/session?paper_id=example-paper
```

```json
{"ok":true,"paper_id":"example-paper","api_version":3,"token":"<local browser token>"}
```

多论文共用一个服务时建议传 `paper_id`；省略时返回服务启动时的默认论文。`/api/ping` 返回相同版本和 paper ID，不返回 token。

`GET /api/status` 返回：

```json
{
  "api_version":3,
  "bridges":[{"id":"bridge_...","adapter":"codex","connected":true,"last_seen":1790787600.0,"info":{"pid":123,"host_pid":456,"model_override":null,"effort_override":null}}],
  "bridge_available":true,
  "counts":{"queued":1,"running":1,"completed":4},
  "unread_count":2,
  "revision":45,
  "stopping":false
}
```

`counts` 的键是内部 task 状态，不是批注显示状态。bridge 最近 60 秒有注册、认领、ack 或 heartbeat 才被视为在线。

### 单条批注路由

| 方法与路径 | JSON body | 行为 |
|---|---|---|
| `GET /api/papers/{paper}/annotations` | 无 | 返回 `{paper_id, annotations, revision}`；`revision` 是全局事件游标 |
| `POST /api/papers/{paper}/annotations` | `{id?, block_id?, anchor?, identity_color?, selected_text?, user_note, submit?, client_request_id?}` | 创建；`submit` 默认 `true`，`false` 保存 draft |
| `PATCH /api/papers/{paper}/annotations/{id}` | `{revision, user_note?, selected_text?, anchor?, block_id?, submit?, client_request_id?}` | CAS 修改并增加内容 revision；`submit` 默认 `false` |
| `DELETE /api/papers/{paper}/annotations/{id}` | `{revision}` | CAS 写 tombstone，并取消未投递任务 |
| `POST /api/papers/{paper}/annotations/{id}/followups` | `{revision, user_note, client_request_id?}` | 新追问；保留历史、revision 加一并排队 |
| `POST /api/papers/{paper}/annotations/{id}/retry` | `{revision, client_request_id?}` | 用户明确请求重新投递；沿用当前问题与锚点、revision 加一 |
| `POST /api/papers/{paper}/annotations/{id}/submit` | `{revision, client_request_id?}` | 提交已保存 draft；revision 加一并排队 |
| `POST /api/papers/{paper}/annotations/{id}/read` | `{revision?}` | 清除 unread；不增加内容 revision |
| `POST /api/stop` | `{}` | 明确停止本 vault 的服务、bridge 与当前宿主 turn |

创建、修改、追问、重试、提交与标为已读返回 `{annotation, task}`，无新任务时 `task:null`；创建使用 HTTP 201，其他成功使用 200。删除返回 `{ok:true, annotation:<tombstone>}`。内容 CAS 冲突返回 HTTP 409：

```json
{"ok":false,"error":"revision conflict; reload before editing","current":{"id":"anno_...","revision":4}}
```

客户端应展示冲突并重新加载，不应拿旧内容无条件覆盖当前 revision。标为已读可以省略 revision，由服务读取当前 revision；并发发生内容修改时仍可能返回 409。

`client_request_id` 应在用户一次操作的网络重试间保持不变。服务保存请求内容的规范化指纹和原始响应：相同 key 与相同内容返回同一响应，不再次创建任务；同一 key 改用不同内容返回 409。客户端应重新获取列表以读取之后已经完成的答案，而不是把幂等返回的旧 queued 快照当作最新状态。

### 批注结构与状态

```json
{
  "id":"anno_...",
  "paper_id":"example-paper",
  "revision":2,
  "deleted":false,
  "block_id":"body-p3-5",
  "anchor":{"view":"translation","language":"zh","content_id":"body-p3-5","field":"text_zh","content_version":"example-v3-1","fragments":[]},
  "selected_text":"...",
  "user_note":"这段方法如何工作？",
  "status":"answered",
  "unread":true,
  "identity_color":"#497da7",
  "answer":"...",
  "sources":[],
  "task_id":"task_...",
  "error":"",
  "history":[{"task_id":"task_...","revision":2,"user_note":"...","answer":"...","sources":[],"status":"answered","created_at":"...","answered_at":"..."}],
  "created_at":"...",
  "updated_at":"...",
  "answered_at":"..."
}
```

`anchor` 作为完整对象持久保留，包括跨段 fragments 和内容版本。core 不依赖具体视图 DOM。新增时可携带合法 identity_color 以保留草稿身份色；服务仍核对同选区已有颜色，冲突时自动另分配。颜色存入 SQLite，后续问答状态变化不改变它。阅读器提供“停止后台服务”按钮。批注显示状态为 `draft`、`queued`、`running`、`answered`、`failed`、`waiting_bridge`。`unread` 是独立布尔值：新答案发布时置为 true，用户明确阅读后清除。离线 bridge 下，列表将 canonical `queued` 投影成 `waiting_bridge`；JSON 导出仍保存 canonical 状态。

`identity_color` 由服务首次持久分配，身份只取 annotation ID，不取 author/source。先尝试 ID 哈希对应的六色候选，再避开当前重叠批注占用的颜色；六色耗尽时分配 HSL。重叠比较 fragments 的 view、language、content_id、field 与半开区间 start/end；整块与同块文本视为重叠，未知旧 offsets 保守视为覆盖字段。颜色写入 SQLite 与 JSON，跨标签与重开保持；后续锚点修改不改变已有颜色。位置修改后新出现的视觉碰撞由前端并列色条处理，不能通过重染旧身份颜色来掩盖。

一次内容修改、追问或重试会清空当前顶层 answer/sources，历史答案保留在 `history`。答案完成、失败与标为已读不会增加内容 revision。删除后 SQLite tombstone 保留，同一个 ID 不能重新创建；晚到的模型结果与再次迁移旧 JSON 都不能复活该条批注。

旧数据存在 `answer_latex` 时，迁移在当前批注与对应旧 history turn 中保留这个仅供数学展示的字段，同时保留原 answer 和原 answered_at。编辑或新结果会移除顶层 answer_latex，避免旧展示覆盖新答案；旧 history 的 answer_latex 不变。

## 3. Bridge 本地 HTTP 协议

所有 bridge 请求都是 `POST /api/bridge/<action>`，使用私有 `X-Reader-Token: <bridge token>` 与 `Content-Type: application/json`。统一包含 `bridge_id`。这一 token 从 supervisor 生成的本地配置读取，不由浏览器获取。bridge 不需要浏览器保持打开。

### 注册与注销

```http
POST /api/bridge/register
```

```json
{"bridge_id":"bridge_abc","adapter":"codex","info":{"pid":123,"host_pid":456,"model_override":null,"effort_override":null}}
```

返回 `{ok:true, bridge_id, lease_seconds:45}`。`adapter` 是宿主类型标识，用于维护每论文每宿主的独立持续会话。`info` 是运行信息，不得包含宿主认证。bridge 正常退出时调用 `/api/bridge/unregister`，body 只有 `bridge_id`，将在线时间归零并发布离线状态事件，避免正常重启等待 60 秒在线过期。

### 长轮询认领

```json
{"bridge_id":"bridge_abc","wait_seconds":20}
```

发送到 `/api/bridge/claim`。`wait_seconds` 被限制在 0–25 秒。无任务时返回 `{task:null}`；有任务时返回 `{task:<task>}`。

```json
{
  "task":{
    "id":"task_<stable hash>",
    "paper_id":"example-paper",
    "annotation_id":"anno_...",
    "revision":2,
    "state":"claimed",
    "payload":{"request_id":"request_...","paper_id":"example-paper","annotation_id":"anno_...","revision":2,"user_note":"...","selected_text":"...","anchor":{},"block_id":"...","history":[],"purpose":"followup"},
    "bridge_id":"bridge_abc",
    "claim_token":"<fresh lease nonce>",
    "lease_generation":1,
    "lease_until":1790787645.0,
    "thread_id":null,
    "turn_id":null,
    "session_thread_id":"<prior thread id or null>",
    "created_at":"...",
    "updated_at":"...",
    "error":null,
    "result":null
  }
}
```

task ID 由 `paper_id + annotation_id + revision` 的哈希确定。该 revision 同一 task 只创建一次。请求去重另由 `client_request_id` 实现；未提供时 payload 的 `request_id` 使用 task ID。

每次 claim 或 recovery adopt 都增加 `lease_generation`，并生成新的 `claim_token`。实际租约写入校验使用 `task_id + bridge_id + claim_token`；generation 用于审计，客户端无需另传 generation。旧 nonce 在新的认领后不能 ack 或发布结果。

同论文存在 `claimed/running/unknown` task 时，该论文后续 queued task 不能认领。不同论文可被不同 bridge 同时认领；当前内置 bridge worker 的处理循环一次等待一个 turn，因此默认单个 worker 的吞吐是串行，但会话仍按论文隔离。core 没有把不同论文合并成一个宿主 thread。

### ACK 与持续会话

bridge 先启动或恢复该论文会话，然后向 `/api/bridge/ack` 提交：

```json
{"bridge_id":"bridge_abc","task_id":"task_...","claim_token":"...","thread_id":"thread_..."}
```

**必须先持久 ACK，再发起模型 turn。** ACK 将 task 设为 `running`，租约续期 45 秒，并在 `(paper_id, adapter)` 下保存持续 thread ID。随后调用宿主 `turn/start`；收到明确 turn ID 后再次 ACK：

```json
{"bridge_id":"bridge_abc","task_id":"task_...","claim_token":"...","thread_id":"thread_...","turn_id":"turn_..."}
```

这样，发出 turn 到收到 turn/start 响应之间的崩溃仍有持久 thread 可核对。HTTP ACK 返回 `{ok:true}`。已过期的未 ACK claim 不能再确认；只有认领中的当前 nonce 能继续写入。

### 心跳与取消控制

发送到 `/api/bridge/heartbeat`：

```json
{"bridge_id":"bridge_abc","tasks":[{"task_id":"task_...","claim_token":"..."}]}
```

返回：

```json
{"ok":true,"controls":[{"task_id":"task_...","action":"interrupt","thread_id":"thread_...","turn_id":"turn_..."}],"stop":false}
```

当前 worker 在等待模型时约每 4 秒 heartbeat；active lease 延长 45 秒。无 active task 时 `tasks:[]` 也维持 bridge 在线。用户删除或更改了任务对应的批注 revision 时，heartbeat 返回 `interrupt`。适配器调用宿主 `turn/interrupt`，等待终态后结束旧任务；旧结果仍必须经过服务 CAS。

停止服务时 heartbeat 返回 `stop:true`，claim 返回 `{stop:true, task:null}`。worker 先 interrupt 当前 turn，再关闭 app-server 并注销。服务记录 stopping 事件，等待 bridge 正常退出；超时仅清理自己创建的 bridge 进程树，随后关闭 HTTP 服务。

### 结构化结果与错误

发送到 `/api/bridge/result`：

```json
{"bridge_id":"bridge_abc","task_id":"task_...","claim_token":"...","result":{"answer":"...","sources":[{"label":"Section 3.5","locator":"source.pdf#page=4","source_id":"paper","content_id":"body-p4-3","url":"","page":4}]}}
```

错误结果改为 `error:<string>` 并省略 result。成功返回 `{ok:true, applied:true}`。重复终态投递返回 `{ok:true, applied:false, duplicate:true}`；批注已经删除或 revision 已变更时返回 `applied:false`，task 标记为 `stale`。结果 HTTP 重发是幂等的，模型 `turn/start` 重发不是。

core 要求 answer 是非空字符串、sources 是对象数组，总结构不超过 1 MB。非空 `content_id` 或 `block_id` 必须能在当前论文各视图找到；page 必须是正整数，且不能超出已有正文/引用页码证据；非空 URL 必须为 HTTP(S)。这种校验确认引用身份和结构，**不能证明答案的每一句推理都得到来源支持**。

Codex 适配器的 `outputSchema` 更严格，每项 source 必须包含 `label/locator/source_id/content_id/url/page`，缺失字符串用 `""`，缺失页码用 `null`。core 本身接受其他宿主提供额外的结构化 source 字段。前端对 URL 与文本还需安全处理。

任务发布时匹配保存的 annotation revision；宿主 `thread_id/turn_id` 只用于定位实际投递与恢复，不代替 revision CAS。模型输出不能自行修改用户问题、锚点、history、status 或 JSON 文件。

## 4. 未知投递与重启恢复

内部 task 状态是 `queued`、`claimed`、`running`、`unknown`、`completed`、`failed`、`cancelled`、`stale`。

| 失效点 | 服务行为 |
|---|---|
| claim 后、ACK 前租约过期 | 回到 queued；尚未确认投递，允许新 claim 与新 nonce |
| ACK 后租约过期 | 进入 unknown；当前批注显示 failed 和“投递结果未知”提示，同论文后续投递阻塞 |
| 旧 task 对应批注已删除或 revision 改变 | 过期任务可标 stale；其结果无法发布到新 revision |
| 用户明确 retry | 增加内容 revision、创建新 task，退役旧 unknown；适配器启动 replacement 前先核对/interrupt 旧 active turn |

服务没有“看不到响应就再发一次模型请求”的分支。`unknown` 需要宿主历史核对或用户明确重试。

新 bridge 注册后调用 `/api/bridge/recover`，返回该 `adapter` 的 `running/unknown` task 列表。该列表是候选摘要；其中 payload 可能是数据库保存的 JSON 字符串，worker 不直接用它执行。对每条候选调用 `/api/bridge/adopt`：

```json
{"bridge_id":"bridge_new","task_id":"task_..."}
```

只有相同 adapter，且旧 bridge 已正常注销或超过 60 秒未在线，才允许新 bridge 接管；原 owner 自己也可 reconcile。接管返回解析后的 `{task}`，带新 generation 和 nonce。

Codex 适配器恢复流程：

1. 使用持久 `thread_id` 调用 `thread/resume`，然后 `thread/read`，`includeTurns:true`。
2. 优先按持久 `turn_id` 找原 turn。若崩溃发生在 turn/start 响应之前，没有保存 turn ID，则按稳定 `clientUserMessageId` 或完整任务标记找到原 userMessage 所属 turn。
3. 原 turn 为 `completed`：读取该 turn 的最终 `agentMessage`，解析 answer/sources 并重新投递 HTTP result，不开启新 turn。
4. 原 turn 为 `inProgress`：等待原 turn 完成并持续 heartbeat，收到取消则 interrupt。
5. 原 turn 明确 `failed/interrupted`：记录失败，后续由用户明确 retry。
6. 找不到持久 thread、找不到原 turn、无法确认终态：保留未知状态，不调用 `turn/start`。

在正常新任务开始前，适配器也检查 resume 返回的活跃旧 turn；replacement 必须先 interrupt 并收到完成确认，以免 `turn/start` 被宿主当作对旧 turn 的 steering。

旧 JSON 迁移映射 `quote→selected_text`、`note→user_note`，完整旧记录保存在 `legacy`，已有回答进入 answered 与 history。迁移不创建 task，也不自动再次调用模型。同 ID 已存在，包括 tombstone 时，不覆盖 SQLite。首次迁移前保留原始字节备份。

如果旧阅读笔记移到新版 explanation 视图，迁移命令支持 `--anchor-map <migration_anchor_map.json>`。文件结构是 `{paper_id, content_version, anchors:{annotation_id:anchor}}`，anchor 含 `view/language/content_id/field/content_version/fragments`。先保留原始字节备份；首次导入时先应用已验证 map，再分配 identity_color，避免位置修复后产生本可避免的颜色碰撞。对于已导入的第一版 legacy 批注，只修复 anchor，既有颜色固定；其他旧字段、history、回答、revision、时间戳与 raw backup 保持原样。已删除、已被用户修订或有活跃 task 的记录会跳过，不重新投递问题。

map 顶层 content_version 必须与 paper-data 一致；anchor 可以保留历史版本 `0`，让前端用 exact/prefix/suffix 唯一匹配而非信任旧 offsets。可定位 anchor 的 content ID 必须存在于指定视图；明确 `migration_status:"unlocated"` 的记录允许保留失效 ID、reason 与原 exact，并展示待核对状态。数学语法规范化仅可改变有证据且唯一匹配的 anchor.exact，须用 `syntax_only_normalization` 标记；用户原 quote 不改写。

## 5. SSE 与快照同步

```http
GET /api/events?paper_id=example-paper&after=0&token=<browser token>
```

服务返回 `text/event-stream`，支持 `Last-Event-ID` 或 `after`。省略游标时从当前全局序号之后监听，不回放全部历史。单次拉取事件最多 200 条，循环继续读取。约 5 秒空闲时发送 SSE 注释 heartbeat。

```text
id: 48
event: annotation
data: {"paper_id":"example-paper","annotation":{"id":"anno_...","revision":2,"status":"answered","unread":true},"revision":48}

id: 49
event: status
data: {"bridge_id":"bridge_abc","connected":false,"revision":49}
```

事件 data 顶层 `revision` 是全局 event seq，`annotation.revision` 才是内容 CAS revision。删除事件携带 `annotation.deleted:true` 的 tombstone。按 paper 过滤时也包含全局 status 事件。

列表 API 是可重载快照，SSE 是变化通知。前端应保留轮询兜底，因为在线过期导致的 `waiting_bridge` 是读取时投影，且浏览器可能暂停 SSE。SQLite 事务先保存事件和数据，再由服务原子替换 annotations.json；JSON 文件失效或断流时以 API/SQLite 为准。

## 6. 启动、部署、停止与宿主适配

使用已安装 skill 的 scripts 与目标 vault 部署样例；将两个路径占位符替换为本机实际目录：

```powershell
python "<skill目录>\scripts\deploy_reader.py" --vault "<PaperVault目录>" --paper example-paper
python "<skill目录>\scripts\migrate_reader_v3.py" --vault "<PaperVault目录>" --paper example-paper
python "<skill目录>\scripts\open_reader.py" --vault "<PaperVault目录>" example-paper
```

有历史锚点映射时，用以下命令代替普通 migrate 命令：

```powershell
python "<skill目录>\scripts\migrate_reader_v3.py" --vault "<PaperVault目录>" --paper example-paper --anchor-map "<PaperVault目录>\papers\example-paper\migration_anchor_map.json"
```

部署脚本检测 `schema_version>=3`，使用 `reader-v3.html→reader.html`、`reader-v3.js`、`styles-v3.css`，递归复制 `assets/math`。公式块必须有 LaTeX，并经过基本括号/分隔符结构检查；这种检查不代替浏览器实际数学渲染 QA。已有 schema v1/v2 的另外论文仍使用原模板与原 `serve_reader.py`；旧服务文件没有替换。

通常启动继承宿主配置，不传 model 或 effort。只在用户明确需要覆盖时使用：

```powershell
python "<skill目录>\scripts\open_reader.py" --vault "<PaperVault目录>" example-paper --model <host-model-id> --effort <host-supported-effort>
```

可用启动选项：`--no-open`、`--port 0`、`--bridge codex|mock|none`、`--foreground`、`--codex-bin <absolute executable path>`。`mock` 产生带 `[MOCK 测试回答]` 前缀的测试结果，不是模型回答；`none` 允许保存问题但暂不启动 bridge。`--serve` 保留旧启动命令兼容；`--file` 仅用于文件阅读兜底，v3后台问答仍需服务。

明确停止：

```powershell
python "<skill目录>\scripts\open_reader.py" --vault "<PaperVault目录>" --stop
```

也可以从已认证的阅读页面 POST `/api/stop`。停止整个 vault 服务，其他页面也会失去在线 API；现有数据保留，可再次启动。

当前 Codex adapter 使用：

```text
initialize(clientInfo, capabilities) → initialized
thread/start 或 thread/resume（read-only sandbox，approvalPolicy=never）
turn/start（text input，稳定 clientUserMessageId，可选 model/effort，outputSchema）
item/completed → turn/completed
thread/read(includeTurns=true) 用于精确历史核对
turn/interrupt 用于删除、修订、replacement 与停止
```

最终答案只从对应 thread/turn 的 `agentMessage` 提取，优先 `final_answer`；completion payload 不带最终 item 时按同 turn 读取历史。JSON解析或来源验证失败会记录失败，不把任意宿主文本直接当已完成答案。LaTeX 必须保留反斜杠并使用 `\(...\)` 或 `\[...\]`，数学图片不能替代公式。

其他宿主可以实现 `bridges/base.py` 的 `HostBridge`：`session/start/wait/recover/interrupt/close`，保持凭据在本机适配器内，并遵守先 ACK、同 paper 串行、unknown 不重发、结果结构化的协议。当前 CLI 只选择内置 codex/mock/none；新增适配器需在 worker/launcher 的适配器选择处登记。core 的 SQLite、HTTP、CAS、事件与数据投影不需要改成某个宿主专用实现。

## 7. 验证与接口兼容性

官方协议参考：[OpenAI app-server 文档](https://learn.chatgpt.com/docs/app-server)。宿主接口可能随版本更新；接入时可用本机 `codex app-server --help` 和 `generate-json-schema` 核对接口。模型、认证与运行时由使用者自己的宿主提供。

使用随仓库提供的合成数据测试核心契约，无需真实论文或模型调用：

```sh
python -m unittest discover -s scripts/tests -p test_reader_v3.py -v
```

如需验证真实宿主，可显式运行以下命令。它会调用模型，并要求新的专用输出目录；不得对正式文献库注入故障。

```sh
python scripts/tests/live_verify_bridge.py --live --recover-restart --output <新的专用测试输出目录> --timeout 120
```

该测试检查后台进程存活、同论文会话复用、丢失结果后的恢复和追问。测试输出属于本机运行资料，不应提交或随 skill 分发。运行时优先复用已有 Python、Node.js 和宿主配置；宿主可执行文件不在 PATH 时用 `--codex-bin` 指定。宿主无法访问登录信息或用户目录时应修复其运行权限，不在 skill 中复制凭据或写死用户目录。
