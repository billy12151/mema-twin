# Changelog

## [0.3.9] — 2026-09-10

- **素材包维度可见（F1）**：compile 素材证据行渲染 `(受众:code/用途:code)` 标签（code 直渲染，与分区
  标题/submit 对账同符号系；空值省略段，两值全空省略括号）+ 证据节前 `> 维度分布：受众 X×N；用途 Y×M`
  摘要（空值跳过）——`_TYPE_RULES` 要求按受众/用途分条件段，素材此前看不见任何维度值（分叉靠猜）；
  画像模式不加（audience 恒定是噪音）。配套反向守门规则「单一取值写无条件规则，不制造单分支条件段」
  （防机械分叉撑大正文提前引爆预算）。
- **编译规则「简练优先」（F3）**：_TYPE_RULES/_AUDIENCE_RULES 同步新增——最小可执行表述、同类合并、
  示例只留一个、不写空洞要求；禁止为省字符抽象掉动作/条件/例外（一手压缩一手锁具体性）。
- **status 逐版本体积曲线（F3）**：`versions[].size_chars` 每版都带（此前仅 active 版），完整增长
  曲线可见，预算悬崖可测距。
- **work_type 域重划（#958，七域改六域按产出物目标）**：规格与执行/分析与复盘/记录与同步/说服与传播/
  教学与传授/法律与契约。四处同步：taxonomy.py 播种源 + db._migrate 幂等 UPDATE（is_custom=0 行，
  custom 不碰）+ 升级测试（预构造旧域库）+ README 域清单行（顺带修英文摘要存量数字
  34→33、10→9、9→8 对齐枚举实际）。最强单点修正：
  user_manual 从「产品与研发」移入「教学与传授」。
- **F6 清理**：twin_meta `last_scan_at` 死键删除（flow.ensure_schema 幂等，twin_scan v0.3.7 退役后
  无代码读取）；`compile_actions._fetch_evidence_find` 兜底召回退役（无在世证据时正确行为是素材为空；
  索引真丢失的正解=按 twin:wt:* tags 从 mema 重建，需要时再写脚本）——sink.find 函数保留（测试防联网
  stub 依赖属性存在）。
- **安装卫生（F5，复发两次根修）**：新增 Makefile `install-skill`（cp -p 分发三宿主技能目录，禁 mv）；
  README 安装节改推 make；.gitignore 补 .DS_Store。
- **task_revise guidance 补句**：帮助文案补「修订反馈中的可复用偏好走 twin.write 沉淀」（与
  task_submit 口径对齐）。

## [0.3.8] — 2026-09-09

- **删任务评审环（task_submit 即终点）**：状态机六态砍三态（`planning → submitted 终态` /
  superseded；approved/rejected/pending 退役为存量展示行）；删 `task_review`/`task_pending`
  动作与 `twin_task_reviews` 表（ensure_schema 幂等 DROP）；task_submit 在状态迁移后重读库内
  最新交付稿落盘 `deliverables/task-N.md`（OSError/sqlite3.Error 降级 warning 不回滚）；supersede
  与 status 的 open_tasks 仅收 planning（存量僵尸 submitted 自动出清）；task_resume 仅 planning、
  task_revise 仅 submitted、task_close 仅 planning；存量 approved/rejected 死端文案直说无迁移入口。
  依据：评审 verdict 零下游消费者（25 任务仅 1 approved、10 submitted 一周无人裁定），依赖
  Agent 转达的软提示实测等于不存在，用户反馈走对话 → twin.write 主路径。
- **归一门（三维度清单硬约束 + 强制用户裁定）**：write/task_start 已提供的维度值未命中清单 →
  **整笔打回**（`unmatched_value`，附动态候选清单 + 每值落 pending 裁定票据，task_start 打回在
  建档之前无半建任务，多维未命中一次性全报）；Agent 必须问用户：resolve map（原值进别名表，
  同一说法终身只问一次）/ canonicalize（新码即刻入列）/ reject（不入体系，不得重试原值）；
  「已裁定不可重复裁定」报错指引并发场景直接重试写入；gate_reject 落票据前重查一次，并发他方
  已裁定的维度就地转命中（竞窗收窄）。删三个枚举的 other 杂项桶；audience/purpose 仅 task_start
  可选不变、write 三维必填不变；write 的 tags 校验提前到门前（miss+脏 tags 不制造裁定义务）。
  残留风险（明示接受）：门保证清单在场+新值必经用户，不能证明 Agent 真问了用户。
- **twin_types 存量迁移（幂等）**：删 3 行内置 other 种子（播种是 INSERT-only，只改内置枚举
  不动库会静默失效）、audience/self 行摘「私人」（保留治理追加的「本人」，不整行覆写）、
  twin_pending_values 删旧列 first_seen_memory_id；upsert_pending 返回值改回查（DO UPDATE 路径
  lastrowid 不可靠）；append_alias 对「内置枚举有、twin_types 无行」的码补播行（防未来加内置码
  后 map 炸 unknown canonical）。
- **删双跑全家**：compare_offer（task_start 一次性提议）/ compare_hint（submit 响应提示）与
  persona_origin/compare_prev/compare_offered 三族 meta 退役（ensure_schema 幂等清理；保留
  last_scheduled_compile_at——夜间停转保险丝的刷新点，spec 文案补明 origin 三重用途）；死代码
  一并清（flow.claim_meta、backfill_evidence_codes、write 成功响应 pending 键、defer_pending
  机制）。get 的 version 参数、零阻力 rollback、验证门 G1/G2、空转阻尼、nightly_rejected、预算
  可见保留为质量安全网。否决理由存档：救济免费且可逆（rollback 可再滚回）时事前确认无价值，
  双跑可触发场景为空集。
- **server.py 模块拆分（单文件 ≤1000 行）**：identity（client 身份 contextvar）/ pref_actions
  （write/get/taxonomy/pending/resolve/void/status）/ compile_actions（compile/submit/rollback+
  验证门+证据取数）/ task_actions（任务流+注入 helpers）；server.py 瘦身为工具面（260 行，
  最大模块 512 行）；请求头经 contextvar 注入（复用 sink notices 模式），动作调用点形态不变，
  `_twin_impl` 仍可从 `mema_twin.server` 导入；taxonomy action 动态化（内置+自建+治理别名，
  与匹配侧同源）。
- **夜间 spec**：submit 步骤删双跑句；新增 pending 明细调用（`twin(action="pending")`）——
  归一门待裁票据进晨报汇总，留用户在场裁定，夜间不代裁。宿主已建定时任务需按新 spec 整体重建。
- **self 别名**：删「私人」留「本人」（语义空间由 自己/自用/个人 覆盖）。
- 设计文档：`~/ZCodeProject/docs/mema-twin-v0.3.8-design-2026-09-09.md`（含两轮评审明细：
  轮 1 自审 6 项、轮 2 对抗性 13 项全部并入；处置对账 mema #948→方向采纳/机制已改、
  #949→版本门否决+评审环删除）。184 测试全绿。

## [0.3.7] — 2026-09-07

- **夜间编译验证门（瘦身版，#907/#908）**：夜间（`origin=scheduled`）submit 落版前过确定性
  检查，只拦灾难形态——素材回声（复述素材包标记，标题类子串 + 节标题行首匹配（改层级也
  命中）；active 旧版含同标记时沿袭降级为警告，防自锁链）与分区标题（ATX 零标题即畸形稿），
  未过拒绝落版（`validation_failed`：active 不变、证据未消耗、次晚自动重试）。**空转阻尼**：
  提交不含任何 active 版未吸收的新证据且非 stale 触发 → 拒绝（`no_new_evidence`），防
  「夜夜 mint 新版 → 每早双跑提议轰炸」。证据未全覆盖（类型=在世证据超集、画像=集合相等）
  与交互式违规只警告不拦（用户治理压过自动化，#904 原则回归）。连续被拒不落版累计于
  `nightly_reject:{code}`（twin_meta，status 的 nightly_rejected 常显、成功落版即清）——
  持续被拒与持续空转两个闭环的唯一出口信号。G5 体积漂移运行时通道删除（防线移交编译规则
  硬预算 + status 体积可见）。
- **全量投影编译**：compile 素材改为该类型**全部在世证据**（`db.alive_evidence`，
  uncompiled+compiled、排除 void；与验证门 G3 期望集同源；find 兜底仅 alive 为空时触发）——
  每版从头重编、弱底稿不遗传、证据库成为唯一事实源、persona 降为可抛弃投影。配套编译规则
  三件套：**含义稳定表达自由**（证据未变动的规则含义必须不变，合并/条件化/重组自由——
  禁止的是无因语义变化）、**硬预算**（类型 8000 字符/60 条、画像 4000/40，"条"=顶层列表行，
  拍板可调；超限强制合并淘汰并写明依据）、**变更分级**（版本间变更区分语义变更——须归因
  证据 id 或预算——与纯表达优化）。status 附各类型 active 的 size_chars/over_budget。
- **冲突链路（notice 透传 + void）**：twin.write 及一切触达 mema 的动作透传 notice
  （sink 全通道出口收集进 contextvar 并发隔离——mema 投递先到先得，twin 内部 read 也会
  claim，只挂 write 响应会被吞；外层响应统一带 `mema_notices` + 分诊分层指引：
  similar_active_memory 疑似重复静默分诊、语义冲突 notice 才问用户三选项）。新增 `void`
  动作：行级作废（status='void'）全链路排除（compile 全量集合/增补/画像投影/统计）、单向
  不可逆，是「新替旧/撤销新写的」的执行机制。**persona_stale**：曾入编译的证据被作废 →
  标记该类型 → 夜间任务自动重编剔除该条款（成功落版即清）；受众侧由 audience_stale 计数差
  触发重抽象（audience_evidence 与 status aud_counts 均排除 void）。素材包常驻「已作废
  条款」节（按 `<!-- src -->` 溯源防旧版参考带回作废条款，纳入 G1 标记与漂移守卫）。
- **twin_scan 退役（单定时任务收敛）**：职能已被夜间编译任务（吸收证据/画像重抽象/stale
  自愈/汇总输出治理计数）与 status 常显（pending_count、open_tasks、open_conflicts——
  后者经 memory_review 查 twin 桶 open 冲突，短超时软失败）覆盖；`scan` 动作与 run_scan
  删除，spec 收敛为单一 twin_nightly_compile；scan_notice 瘦身为只看
  last_scheduled_compile_at 的 7 天停转保险丝。spec 扩展：persona_stale 触发重编、
  validation_failed/no_new_evidence 处置（不为过门改产物）、保守封套（无证据动机不整体
  重组）、汇总输出带治理计数。
- 实施两轮 review（常规 + 对抗性）修复：空转阻尼加证据基座收缩旁路（void 后期望集缩小，
  画像/类型重抽象提交必是旧集真子集，不加旁路会被阻尼永久拦死、stale 永不清零）；画像模式
  作废条款节改按 audience 查；**submit source ids 对账**（去重 + 多余 id：scheduled 拒绝
  foreign_ids、交互式剔除+警告——幽灵 id 会让基座旁路永久放行空转、重复 id 虚增吸收数让
  画像 stale 永差）；回滚复活作废条款守卫（目标版 source 与作废证据有交集 → 重标
  persona_stale）；G2 与 G1 行首口径对齐（「##标题」无空格也认）；twin 工具改 async +
  to_thread（compile 全量 read 最坏独占事件循环 11 分钟、拖挂其他宿主）；作废条款节补
  剔除指引；notice 附带扩到非 ok 响应、guidance 补工具缺失 fallback 与 mema retire 职责
  说明；void 幂等；spec 补保守封套/notice 记录/治理计数；review_conflicts 提 limit；
  task_recent limit 脏类型打回；测试防真实联网。
- 已知边界：mema 侧直改记忆（不经 void）twin 感知不到；无标记有标题但内容垃圾的产物过门
  （内容质量留给双跑对比与真实任务）；notice 先到先得（其他宿主可能先 claim）。
  测试 140→173。

## [0.3.6] — 2026-09-06

- 两轮 review（常规 + 对抗性）修复：audience_stale 排除 work_type 滞留行（夜夜重编死循环）、
  rollback 开放 aud- 通道（坏画像可回退）、受众型夜间落版同刷 last_scheduled_compile_at、
  有画像受众先过滤再 LIMIT 3（防挤占）、aud- submit 吸收数不对称守卫（多报/漏报/混入他受众
  id 均警告）、素材包 vNone 守卫、**内置"领导"别名回退**（用户治理裁定优先于内置枚举，
  真实库存在 领导→direct_manager 裁定，不得静默翻转）、存量 aud- 撞名类型 status 告警。

- **受众画像（audience profiles）**：解决"对同一受众跨类型产出要重复说同一批要求"——
  `aud-{受众}` 伪类型存于 twin_prompt_versions（复用版本/rollback/镜像，零 DDL）。
  task_start/task_resume 带 audience 时注入 `audience_profile_md`（画像全文，note 显式
  优先级链：本类型增补 > 类型 persona > 受众画像）；画像未编出时以雏形（跨类型证据
  ≤5 条，排除本类型行防与增补重复）垫底。write 新增 `scope=audience`（受众级偏好，
  work_type 省略、audience 必须归一成功，tag 走 twin:aud 命名空间）。compile 素材包内嵌
  ≤3 个已有画像作跨类型参考（守门句：只迁口径类）；`compile(aud-x)` 切换为画像素材
  模式（该受众全部证据不分 compiled、受众专属规则）。夜间任务扩展：status 新增
  `audience_stale` 触发器（证据数≠画像吸收数才重抽象），spec 增加 compile/submit
  受众步骤；受众编译为派生投影，不消耗证据（不 mark_compiled、不触发双跑提议）。
  防串味：`aud-` 为保留前缀（canonicalize 禁用）、status/统计/resolve 全程过滤、
  存量撞名类型 status 显式告警。注意：内置枚举未新增"领导"别名——用户既有治理裁定
  （领导→direct_manager 一类）优先于内置枚举，不得静默翻转。

## [0.3.5] — 2026-09-06

- 两轮 review（常规 + 对抗性）修复：增补/提议的优先级声明一律钉死版本号（漏列 source id
  时旧证据也走增补，不再宣称"编译后新增"；#896 出生标签经得起后续版本）；compare_offered
  一次性标记改原子抢占（多宿主并发 task_start 只问一次）；submit 对未吸收证据当场警告并
  限 prompt_md ≤100k；task_id 严格矫正（浮点不再静默截断）；task_start 延迟落 pending、
  限维度长度、增补取数后置（缩小并发让位竞窗）；自定义 code 白名单字符集
  （[A-Za-z0-9_-]，防 hint 内嵌示例破损与镜像路径污染）；增补全跳过时仍上报 skipped。
  测试 89→117 全绿。
- **新版首任务双跑对比提议（Shadow Twin 的人力裁决最小版）**：夜间定时任务落版
  （`submit` 新增 `origin=scheduled` 白名单标记）后的第一个 `task_start` 附
  `persona_compare_offer`（版本对 + 引导，**不含旧版全文**——用户同意双跑后 Agent 才按需
  `get(work_type, version=旧版)` 取，防拿错版 + 省 token）；同一版本只提议一次（附加即落标），
  交互式编译永不触发。`get` 新增可选 `version` 参数取历史版本全文（镜像降级下版本化读取
  明确报错）。交互式 `submit` 返回 `compare_hint`（双跑玩法提示，转告用户，对比与否用户自决）。
  提议/hint 均带 #896 出生标签："旧版仅对比参考、非执行依据，对比后以新版为执行依据"。
- **编译规则升级（条件化 + 禁止项）与隐式信号**：编译规则新增两行——规则写成条件化策略
  （注明适用条件与例外），用户明确厌恶的行为编为高优先级禁止项（单独分区置顶）；SKILL 把
  评审轮次中的采纳/忽略/跳过询问等隐式行为列为可沉淀信号（判据不变，一次性异常不写）。
  persona 从"风格清单"升级为"条件化工作策略 + 禁止清单"。
- **未编译增补注入（overlay）**：`task_start` / `task_resume` 注入 active persona 时，同步携带
  该 work_type 的未编译偏好证据全文（`persona_supplement` + `persona_supplement_note`），显式
  优先级声明"与 prompt 冲突时以增补为准"——新偏好下一任务即生效，不等编译。短路相等分支
  同样带增补（增补是新进场材料，上文没有）。上限 10 条截旧留新并提醒手动 compile（用户没建
  夜间任务时的保险丝）。空 persona 分支（有证据无 prompt）原始证据当雏形注入，第一天就有
  分身效果。软失败：mema 读挂跳过（连接级失败 fail-fast 跳过剩余，不拖慢开工）、全部失败
  则无增补字段、persona 照常注入。增补只走 twin_evidence 索引，不走 compile 的 find 兜底。
- **夜间编译定时任务进单一真源 spec**：`SCHEDULED_TASKS_SPEC` 新增 `twin_nightly_compile`
  （cadence daily，status→compile→submit；无证据不编不落版，避免版本号空转；凌晨无人值守
  不向用户提问）。`scan_notice` 的 agent_instruction 从只提每周扫描改为两项任务一起建议
  （夜间编译 + 每周治理扫描，可只选其一）；`scan` 的未编译建议补夜间任务出口；SKILL.md
  口径同步（版本更新触发条件、scan_notice 引导）。产品仍不起任何调度——Agent 拿 spec 在
  宿主平台创建等价任务（与 twin_scan 同款平台无关模式）。

## [0.3.4] — 2026-09-05

- **have_persona_version 注入短路（agent 申报式）**：`task_start` / `task_resume` 接受
  可选 `have_persona_version`——同一会话此前注入过同 work_type 且版本号仍在场时由
  agent 申报（服务端看不见会话，「已注入」的事实归看得见上下文的一方）。与 active
  相等 → 不再返回 `persona_prompt_md`，改回 `persona_unchanged: true` + 一行引导
  （沿用上文；不可见调 `get` 取全文）；不等（中途重编/回滚过）→ 全文重注入并附中性
  变更说明「版本已从 v{n} 变更为 v{m}」；未传/mirror 降级（无版本身份）→ 照常全文
  注入。失败模式全软：忘传/传错/上下文被压缩都回落现状行为，绝不出现分身静默不在场。
  零服务端状态，stdio/http 行为一致。
- 两轮 review（常规+对抗性）修复：help/README 动作口径同步（compile 描述、rollback 行、
  have_persona_version 说明）；超范围版本号（>2^63-1）在矫正层打回 invalid_input，
  不再溢出到兜底 internal_error；underscore/符号前缀数字串（"1_0"/"+2"）实测拦截。
  测试 75→89 全绿。
- **rollback 动作（persona 版本回滚，零阻力）**：`twin(action="rollback")`，`work_type`
  必填，`version` 省略回上一版本（active 之外号最大的行）、传 n 回指定版本。事务内切换
  active 指针、刷新 activated_at、重写 `active.md` 镜像（v{n}.md 不动；镜像失败降级为
  警告不击穿）。无确认、无警告、无拦截——即使效果是新版本习得的能力从 active 消失；
  报错仅限客观不可能操作（无版本可回滚 / 目标不存在，附可用版本列表）。目标已是 active
  幂等成功。不删历史（retired 可再激活）；版本号永不回收（回滚后 submit 仍 MAX+1）。
- **compile 会话治理**：素材包旧版标题从「当前版本 prompt」改为「旧版本 prompt（编译
  参考，非执行依据）」并注明「新稿 submit 落版后即取代它」；编译规则同步改词（与旧版本
  冲突以新证据为准）。动机：原标题在 submit 落新版后成为永久留在上下文里的假话，且无法
  回收——标签必须出生即经得起时间。`submit` 响应新增 `supersedes`（被取代的旧 active
  版本号）：落版即裁决，不等下一次注入。SKILL.md 引导编译走独立会话、做完即弃（编译会话
  内新旧版本同屏，会话隔离是避免冲突 persona 的唯一硬手段）。
- **会话分工提示下沉到服务端响应**（用户实测反馈：引导只写 SKILL.md 时，未装 skill 的
  http 共接宿主完全看不到）：`compile` / `submit` 响应新增 `session_note`，且为**转告
  用户的显式指令**（非仅供 Agent 自读的信息，句式与 task_review 的「提醒用户」同款）——
  compile 侧「请告知用户：后续交付任务建议换新会话重新开始」；submit 侧「请提醒用户：
  v{n} 已生效（取代 v{m}），建议后续任务换新会话重新开始；若坚持本会话继续，下次
  task_start 传 have_persona_version=〈n〉」。

## [0.3.3] — 2026-09-04

- **偏好存储桶写死**：`workspace=mema-twin` 从 env（`MEMA_TWIN_WORKSPACE`）改为代码
  常量，env 一律无效（含坏值，不再打回）。与 0.3.2 的 agent-id 写死对齐：身份类
  配置（agent_id/workspace）产品内定，部署类配置（URL/TRANSPORT/CLIENT_ID）才留 env。
- **client 头透传（http 多宿主归属）**：http 模式下 twin 读宿主连接自带的
  `X-Mema-Client` 头并透传给 mema（取头方式与 mema core `request_identity` 同款）。
  头是权威身份：`data.client` 只能与头一致或省略，不一致打回 invalid_input
  （mema `_identity_mismatch` 同款语义，堵跨宿主冒充）；stdio 无头时
  `data.client` > `MEMA_TWIN_CLIENT_ID` env。脏值/重复头/非字符串/含首尾空白
  一律打回；http 绑定仅允许 loopback（非 loopback 拒绝启动）；
  `MEMA_TWIN_CLIENT_ID` env 脏值启动即报错退出。
- 建档归属修正：`task_start` / `task_resume` / `task_revise` 建（子）任务时 client
  取当前调用者（头/显式），不再恒落 env 默认。resume/revise 子任务记录当前执行者
  而非继承原档。注意归属覆盖建档动作；`task_submit` / `task_review` 行本身不带
  client（评审审计沿任务链关联建档者）。
- `sink.find` / `sink.read_memory` 增加 `client` 透传参数（compile 兜底召回路径同样
  携带真实宿主身份）。
- examples：stdio 模板删 `MEMA_TWIN_WORKSPACE`；新增 `examples/zcode.http.mcp.json`
  （`type=http` + `X-Mema-Client` 头模板）。
- 两轮 review（常规+对抗性）修复：入口统一身份校验（fail-fast）、重复头/空白/类型混淆拒绝、loopback 绑定守卫、env 启动校验、compile 路径身份统一。测试 58→75 全绿。

## [0.3.2] — 2026-09-03

- **agent-id 写死**：`X-Mema-Agent-Id` 从 env（`MEMA_TWIN_AGENT_ID`）改为模块常量
  `mema-twin`（子 agent 范式：client 标识宿主、agent_id 标识写入者）。用户侧配置面
  只剩 client；env 从代码/文档/配置模板全部移除。

## [0.3.1] — 2026-09-03

- **http 传输（多 Agent 共接）**：`MEMA_TWIN_TRANSPORT=stdio|http`（默认 stdio）；
  http 时 `MEMA_TWIN_HTTP_HOST/PORT`（默认 `127.0.0.1:8765`），端点 `/mcp` 无状态
  直调（免 initialize，mema 同款形态）。`twin.write` 的 `data.client` 可覆盖默认
  client，共接宿主归属分离（agent_id 恒 `mema-twin`）。

## [0.3.0] — 2026-09-03

- **移除 embed 近邻归一档**：归一收敛为 精确/别名 → pending 两档。候选枚举仅 53 项，
  语义选码由调用方 LLM 对 `taxonomy` 清单完成（SKILL 硬流程：先查清单再写；清单无
  合适项给原始值，进 pending 由用户裁定）——本地小模型 0.75 阈值静默错归无人工
  检查点、吃掉 pending 治理信号，且 llama-cpp-python + GGUF 是只为 53 个枚举背的
  重依赖。移除后 twin 自身零向量模型，mema-team 集成天然满足单向量模型约束。
- 删除 `mema_twin/embed.py`、`MEMA_TWIN_EMBED_MODEL` env、pyproject `core` extra
  （memory-arbiter-mcp / llama-cpp-python 依赖随之取消）。
- 修复 server.py 各 handler 的 sqlite 连接泄漏（全部 try/finally 收口）；write 的
  `tags` 参数非列表时打回 invalid_input（原会被逐字符拆成 tag）。
- **许可证定为 Apache-2.0**（2026-09-03 拍板）：twin 实现简单，闭源买不到保护、
  开源是信任与采用率的前提；收费叙事保留在 mema-team（协调价值），twin 作其漏斗顶部。
- **仓库转 public 进入阶段 1 开源试用**（2026-09-03）：GitHub 分发（`uv tool install
  git+https://github.com/billy12151/mema-twin`），README 补快速开始（MCP 客户端配置
  模板 + 前置 mema HTTP MCP 说明）；官方 MCP Registry 暂不上（包类型仅认 npm/PyPI/
  NuGet/Cargo/OCI/MCPB，Python 的最小路径是 PyPI，当前选择跳过）。

## [0.2.0] — 2026-09-03

M1 + M2 落地（mema-core 0.15.4 基线），两轮 review（常规+对抗性）修复 28 项发现，61 测试绿：

- **embed 近邻归一档（M1.1）**：别名 miss 后先语义近邻（阈值 0.75），命中映射
  canonical（matched_by=embed，不落别名）；复用 mema-core ManagedEmbedder（懒加载
  共享同一 GGUF），模型路径 env `MEMA_TWIN_EMBED_MODEL` → mema 配置 → 禁用，
  任何失败 fail-open 走 pending。
- **证据指针索引 twin_evidence（M1.3）**：write 成功登记 mema memory id + 三维度；
  compile 按 id 精确 `memory read` 取全文，召回无丢失；submit 回写编译标记；
  索引为空时退回 find 兜底（`include_content=true`，适配 0.15.4 find 索引页化）。
- **交付任务流（M2.1，机制改造自 plan-mode-mcp）**：task_start/submit/review/pending/
  resume/revise/recent/get + 会话 todo。可审计（任务行不可变追加 + append-only 评审表
  + lineage）、可中断（pending）、可继续（resume 恢复 todos）；task_start/resume 注入
  persona prompt；评审通过交付稿落 `deliverables/` 文件。
- **定时扫描（M2.2，模式照搬 mema scan_tasks）**：`scan` 动作汇总未编译偏好/pending
  积压/开放任务并产出建议；status 携带自消失安装提醒（7 天内跑过即不再提示）；
  help(topic="scheduled_tasks") 与提醒同源渲染；twin 自身不起调度。
- SKILL.md/README 更新；SKILL 安装位 `~/.zcode/skills/mema-twin/`；
  pyproject `core` extra 钉 mema-core ≥0.15.4 + llama-cpp-python。
- **画像人级全局（2026-09-03 定案）**：twin 三表（twin_evidence/twin_prompt_versions/
  twin_tasks）删除 workspace 列与 `data.workspace` 覆盖入口，镜像/交付路径降层
  （`prompts/<work_type>/`、`deliverables/task-N.md`），scan 提醒全局唯一；mema 侧
  保留 `mema-twin` 存储桶（治理查全 + 偏好内去重/冲突 + 不污染项目空间），agent-id
  定案为子 agent 范式（client 识宿主、agent_id 识写入者）。

## [0.1.0] — 2026-09-02

阶段 0 骨架（15 测试绿）：

- 三维度 canonical 枚举 v1：34 work_type（七域）/ 10 audience / 9 purpose，含精确与别名归一、防冲突测试。
- pending 治理：归一未命中的长尾入 `twin_pending_values`，map / canonicalize / reject 三种裁定，治理别名不被内置播种覆盖。
- prompt 版本存储：DB 为准 + `prompts/<workspace>/<work_type>/vN.md`、`active.md` 原子写镜像；`get` 支持 DB→镜像降级读取。
- 单工具 MCP server `twin(action, data)`：write / get / compile / submit / status / taxonomy / pending / resolve / help 九动作；write 缺三字段打回 invalid_input，非阻塞 hint 引导 compile。
- 偏好记忆经本机 mema HTTP MCP（`127.0.0.1:8000/mcp`）读写：无状态 tools/call 直调 + `X-Mema-Client` / `X-Mema-Agent-Id` 身份头，SSE data 帧解析。

## [0.3.1] — 2026-09-03

- **HTTP 传输支持（多 Agent 共接）**：`MEMA_TWIN_TRANSPORT=http` 启动 streamable-http
  （默认 `127.0.0.1:8765/mcp`，`MEMA_TWIN_HTTP_HOST/PORT` 可调），端点无状态直调
  （免 initialize，mema 同款）；stdio 仍为默认。
- **多 Agent 身份**：`twin.write` 新增可选 `data.client`，随调用覆盖发往 mema 的
  `X-Mema-Client`（agent_id 仍固定 `mema-twin` 子 agent 范式）——多个宿主 Agent 共接
  一个 http twin 时，写入归属在 mema 审计中可辨。

## [0.3.2] — 2026-09-03

- **agent-id 写死不暴露**：`X-Mema-Agent-Id: mema-twin` 成为产品内部常量（子 agent
  范式的写入者标识），移除 `MEMA_TWIN_AGENT_ID` 环境变量；用户可配置面只剩
  `MEMA_TWIN_CLIENT_ID`（多 Agent 差异只走 client / `data.client`）。
