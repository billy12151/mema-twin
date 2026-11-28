# Changelog

## [0.7.2] — 2026-11-28

jingleAI 全链路实测（19 次调用）反馈四项修复。**宿主适配须知**：①taxonomy `compact` 非布尔值由「静默按 false 返回完整投影」改为 **invalid_input 打回**（此前 help 就写打回，本次实现对齐）；②全新工种（0 在世证据）task_start/task_resume/task_revise 不再注入同域 fallback 全文（实测会议纪要模板注入测试报告构成失真），改为返回 `fallback_pointer`（新数据键，{work_type, version, domain}）+ `fallback_persona_note`（含 donor 实名与 get 指路）；有在世证据的工种行为不变。645 测试绿。**发版顺序**：先 bump 三件套再 `make install-skill`（版本戳取自 pyproject，顺序反了 check-skill 抓不到——v0.7.0 教训）。

### Changed

- **taxonomy compact 非布尔硬拒**（F1）：`"true"`/`1` 等非布尔 → invalid_input（field=compact）；布尔 false/null 等于未传=完整版——help「非布尔会被打回」的旧承诺本次兑现（start_first 同款句式）。
- **全新工种 fallback 指针化**（F3）：0 在世证据 → 不注同域全文（口径可能不适用，实测失真），回 `fallback_pointer` + note（donor 实名 + get 指路，指针自足照 v0.6.9 先例）；有在世证据（类型在积累）维持全文垫底；task_start/task_resume/task_revise 三通道一致；playbook 注入不受影响（独立通道）。
- **kind 报错补指路**（F2）：传工种 code 当 kind（如 `["work_report"]`）的报错追加「数组元素是 kind 名，不是工种 code；查某工种的枚举清单用 kind='work_type'」。
- **help plan_set 措辞对齐实测**（F4）：「0 个或多个 planning 任务时打回并附清单」→「0 个时打回并提示先 task_start 建档；多个时打回并附 planning 任务清单」。
- **SKILL.md 补 v0.7.0 新字段说明**（F4）：write 条补 `cross_type_echo`/`echo_guidance`、task_submit 条补 `steps_reflection_not_written`；fallback 条改两档表述并补 `fallback_pointer`。

### Fixed

- help task_start/task_resume/task_revise 的 fallback 描述同步两档口径（此前三处会在零证据场景误导宿主把 fallback_persona_md 缺席当故障排查）；README 同域 fallback 节与 task_start 表格行同步。

## [0.7.3] — 2026-11-28

owner 指出：宿主已传 available_tools 且服务端已回 tool_gap 时，playbook_note 末尾仍写「强烈建议：下次 task_start 传 available_tools」——句子没跟着状态走。

### Changed

- **playbook_note 尾句跟随 available_tools 状态**：已传 → 追加比对回执「已按本宿主工具面板比对，缺口见 tool_gap。」（有缺口）或「…面板条目均已在场（无缺口）。」（全命中）；未传 → 保留原「强烈建议」句。task_start/task_resume/task_revise 三通道一致（同函数）。边界：playbook 无工具面板条目时明示「工具面板比对未执行」。

### Fixed

- playbook 无工具面板条目且宿主已传 available_tools 时，不再有「传了还劝传」的自相矛盾提示（此前任何状态都无条件附建议句）。

## [0.7.4] — 2026-11-28

纯 SKILL 文档修复（服务端行为无变更，wheel 与 0.7.3 代码一致）：0.7.3 的 playbook_note 两态回执没同步到 SKILL——`playbook_md` 条目原写「有 `available_tools_required` 就在下次 task_start 传 available_tools」，但 0.7.3 起**本次已传时** `available_tools_required` 照样置位且 note 已是比对回执，照旧文案会引导宿主白传一次。改为两态表述：传了→看回执（缺口见 tool_gap / 无缺口无需再传）；没传→note 劝传，下次补传即可。

## [0.7.5] — 2026-11-28

jingleAI 0.7.4 实测反馈（不阻断级）：`available_tools_required: true` 在已传 available_tools 时仍置位，只看字段不看 note 的宿主会误读成「要求再传一次」。

### Changed（宿主适配须知：required 语义收紧）

- **`available_tools_required` 语义收紧为字面义**：仅本次未传 available_tools 时置位（=确实待补传）；**已传后不再置位**——已有宿主以它为「要传」信号的，修正后不再重传（期望行为，note 回执兜底）。
- **新增 `available_tools_checked: true`**：本次已传且面板比对执行后置位（缺口见 `tool_gap`，无缺口=面板均已在场）；playbook 无工具面板条目时不置（note 明示「比对未执行」）。
- 三态速查：`required=true`=本次未传 / `checked=true`=已传已比对 / 两键皆无=未比对或无 playbook。空数组 `[]` 视为未传（0.7.3 起既有口径不变）。SKILL playbook_md 条已同步三态表述。

## [Unreleased]

## [0.7.1] — 2026-11-27（2026-11-28 扩充：沉淀质量治理）

沉淀质量治理（owner 2026-11-28 批准按方案实施；开发方案 v1.3 存 mema #1741，两轮四路独立评审）。**宿主适配须知**：submit 响应 warnings 可能出现 `[验证门:工具条款残留]` / `[验证门:工具条款提醒]`——**只提醒不拦，照常落版**；compile 响应可能带 `tool_clause_flags`（证据含工具参数/链接片段的标注）；persona 硬预算放宽为 **10000 字符 / 70 条**（画像维持 4000/40）；task_submit guidance 文案变更（写法类才沉淀）。

### Added

- **W3 工具参数确定性检测**（新模块 `mema_twin/toolcheck.py`）：compile 时对每条证据扫描工具参数片段（硬档）与链接/内部域名（软档），命中条目写入响应 `tool_clause_flags` 并在素材包证据行尾加 `<!-- tool-flag: hard|soft -->` 标注；submit 落版前对产物正文做同样扫描，硬档（工具条款残留）与软档（工具条款提醒）都进 warnings、**两种来源照常落版**（D9：不做夜间硬拒——#951「质量类交互不配阻断」/#956「不给落版加前置门」）。检测要点：只豁免 `<!-- src: … -->` 归因注释（容忍空白/等号变体）；先命中先占位、类序 url→inner→http→kv→bool→kebab_dot→camel 防双报；camel 白名单 9 项；kebab 扩展名豁免；环视词边界防中文紧贴漏报；帽=每证据 snippets ≤3、flags ≤12 条超出记 `flags_total`。存量回归基线（2026-11-28 对生产库只读实测，终版规则）：非 void 证据 149 条命中 13（硬 8 软 5，含等号布尔新增命中的 twin 机制参数残留一条）、在用正文 20 份命中 9 行（硬 6 软 3），逐行人工复核全部为参数/调用示例或合法例外。
- **W2 建议作废清单（文案）**：夜编晨报「建议作废」五类（通篇业务口径 / 被同源合并完全覆盖 / 被新证据取代 / 纯工具条款 / 拆半丢弃的业务半句），只汇总不安置；gov SKILL ②第 5 条冲突裁定改为「先判断关系」（收窄/特例→合成一条带适用条件的规则；否定→取新并注明；两型之外回落以新为准）。

### Changed

- **W1 沉淀判据同文（纯文案，十处）**：新判据「换一个需求、或换一个项目仍适用、且非项目特有约定的产出物写法才沉淀；本需求本项目的事实（含项目特有写法约定）不沉淀」——WRITE_GUIDANCE 第②步追加一问（在「会打回」之后、④ 清单复检）、第④步扩写；_TYPE_RULES/_AUDIENCE_RULES 各新增一条并补守门句（工具标注处理、合并留痕、变更节不复述参数名）；task_submit guidance 与 server help（task_submit/task_revise）同步；skill/SKILL.md 分流规则新增条（:36 加「判据见下条」指向）与 :44/:46/:57/:80 对齐；README :57/:130/:132-133/:144 同步；scan.py spec compile/submit instruction 同步（宿主 prompt 不自动跟随，需按新 spec 重建宿主定时任务）。
- **W4 persona 硬预算放宽**：`BUDGET_TYPE_CHARS` 8000→**10000**、`BUDGET_TYPE_RULES` 60→**70**（D1a，产地=装下现状与待吸收）；画像 4000/40 不放宽（D1b）。背景：product_doc v17–v22 六版 60 条顶格、v23 59 条/7995 字符，夜编自报「下一条新证据起必须走交互式整体重组」。
- **W5 skill 版本戳挪位**：`make install-skill` 的版本戳从文件首行挪到 frontmatter 之后——此前戳行顶掉 frontmatter，ZCode 读到的 mema-twin description 为空（governance 副本无戳故正常）；`make check-skill` 新增首行 frontmatter 校验（新旧两种戳格式去戳后 diff 均等价，只有首行校验能抓退化）。
- skill 文档补齐两条已上线行为口径（纯文案，服务端行为无变更）：同会话第二次建档起 `task_start` 不回 `guidance`（**省略非出错**，首次建档仍带）；`status` 的版本明细只回 active 当前行（**非历史丢失**），历史版本经 `get(version=N)` 按需查。`make install-skill` 同步三宿主副本。

## [0.7.0] — 2026-11-26

规则/playbook 分流治理（开发方案双轮评审后实施，mema #1731；twin 任务 #1162 交付）。背景：执行经验混进 persona、同一段偏好 33 工种扇出后被推翻无法回收（JoySpace 列宽案例）——根因是白天写入口与夜间编译侧的分流判据不同文。判据主轴换为**可移植性**：换宿主、换工具还成立吗？

**运维须知（W1 不是零行为变更）**：persona 编译规则新增「只为产物层规则立条」后，每次重编会把工具条款剔除并在「版本间变更」标注「转 playbook / 技能文档候选」（晨报汇总不安置，需人工安置；playbook 溯源门要求任务 id，无溯源的进技能文档）。受众画像编译规则同款守门句关死 aud-engineering v10「列宽不可写」类事故路径。

### Added

- **W3 跨工种同文扇出提示**：write 响应新增 `cross_type_echo`（count/work_types/memory_ids 前 8）+ `echo_guidance`（软提示不拦截）——同一段文字已写在同受众 ≥2 个其他工种时当场可见（scope=audience 写入阈值 1，引导工种级/受众级二选一）；同受众长度窗内活跃行超 5000 回 `cross_type_echo_skipped=over_scan_cap`（不做部分检测）。与 add_local_evidence 同一 NFKC 词法去重键。
- **W2.1 reflection 丢弃显式告知**：步骤已 done 再补 `step_update(done, reflection)` → no_op 响应 guidance 明示「本次 reflection 未写入」并指路 `tool_log(task_id, note)`；task_submit 带 steps 批量补打卡时，成功收口路径新增 `steps_reflection_not_written` 计数（被拒路径走 hint）——只对真带 reflection 的 no_op 计数。

### Changed

- **W1 判据单点真源+各处同文（纯文案）**：WRITE_GUIDANCE 重写为四步判据（可移植性 → 打回 → 拆出去处 → 一次性事实）；`_TYPE_RULES`/`_AUDIENCE_RULES` 补「只为产物层/口径类规则立条」（被剔除条款证据 id 仍照抄进 source_memory_ids，防永久增补注入）；`_PLAYBOOK_RULES` 分流判据补来源与可移植性；help 四处（task_submit/step_update/tool_log/task_evaluate）与 task_submit 响应 guidance 同步执行经验通道；skill/SKILL.md 分流节改五条路由（含「稿子会被打回的是规则，稿子没差、只是路走弯了的是 playbook」助记）；skill-governance/SKILL.md 与 scan.py SCHEDULED_TASKS_SPEC 真源同步（保守封套豁免句+晨报两项汇总）；README 四行 doc 镜像同步。
- submit help 的 source_memory_ids 指针由 evidence_ids 修正为 source_ids_suggested（后者含缺正文 skipped 行——漏列会以增补形式永久注入，W1.2 下变成承重墙）。
- `_cross_type_echo` 的 aud- 前缀排除加 LIKE ESCAPE 转义（裸 `_` 是单字符通配符，防未来 `aud*` 自建码被误排）。

### Fixed（两轮 review，轮 2 对抗）

- task_submit 成功收口路径丢批量 steps 的 no_op 提示（提示原挂在被拒路径，而全闭环任务的补打卡必然全 no_op——恰在最常触发处缺席）；noop 计数分列「带 reflection」子集，裸幂等打卡不再误报。
- 方案 §六 T15 用例原文自相矛盾（aud- 行不计入时不可能产出 count==1 的回显），按规格语义勘误钉住并记入方案修订记录。

## [0.6.9] — 2026-11-23

owner 提出：未知申报键警告的码表教学 note（~310 字符）在循环场景每次重复不划算。

### Changed

- **码表教学 note 走 S6 频控**：同 client 首次全量教学（合法码 code（zh）清单），第二次起换一句话指针（~50 字符）；`ignored`/`total` 事实数据逐调用保留。实现为 task_actions 独立 seen-set（租户前缀隔离+容量帽，`_TS_GUIDANCE_SEEN` 同款）——**不可复用 compile_actions 共用 set**：task_start guidance 每次建档先占同一 tenant:client 全局键，会把首次码表错杀成指针（实施中实踩，测试钉住）。

### Fixed（实施后双 review）

- **指针自足（R2 P1-1）**：二次起指针自带 `twin(action="taxonomy", kind="audience")` 实时重取指引——上下文压缩（申报键最常错场景）下此前响应不可见、help 不含码表，原指针自纠断链。
- **无身份宿主豁免（R2 P1-2）**：http 多宿主形态（tenant 已路由）而 client 缺失时不占槽恒全量——无头宿主共 `tenant:` 键互串「假指针」；stdio 单进程单宿主不受影响（无 client 也占槽，循环省钱场景主战场）。
- **占位后置（R1/R2 P2-1）**：seen 键在码表构建成功之后才落——DB 瞬断异常不留「已占位未送达」的永久假指针态。
- SKILL/README 宿主可见面同步频控行为（「带合法码清单」旧承诺修正）。

## [0.6.8] — 2026-11-23

jingleAI 0.6.7 实测反馈三则（#1 未识别申报键静默、#2 aud- 前缀双形态混淆、#4 help 缺形状说明）。方案双轮评审修订后实施（R2 P0：归一域漏自建受众码；R2 P1-2 设计反转：放弃别名匹配防漂移假短路）。

### Added

- **申报键未知码警告**：task_start/task_resume/task_revise 响应新增 `have_audience_versions_unknown`（ignored 前 10 条/单键 64 字符帽 + total + 实时合法码清单 note）——未识别键**不硬拒任务**（申报是省 token 优化，兜底=画像全文照注，宁重复不缺席），逐调用可见直至改对；不受 S6 静态抑制。
- help(task_start) 补 `have_audience_versions` 形状段（键=受众裸码、aud- 自动归一、短路回显三键、宁重复不缺席）——0.6.7 CHANGELOG 声称补过、实际只改了 task_resume 指针，本次补真实缺口；task_revise 补同口径+跨会话抄号告诫。

### Changed

- **aud- 前缀自动归一**：申报键 `aud-direct_manager` 静默剥前缀归一为 `direct_manager`（保留命名空间双射，宿主从 get/自救指引抄 aud- 形态零成本兼容）。归一域只认**精确裸码（内置 ∪ 治理自建码）**——中文名/别名不用于申报（别名表随发版漂移→撞号假短路→画像静默缺席，v0.6.7 P0 同类）；同码多版取 max 胜出（与键序无关，跨语言宿主安全）。
- 键分类与值校验下沉 `_normalize_have_audience_versions`（连库取自建码；先分类后校验——未识别键不论值好坏一律软忽略，识别键坏值仍硬拒）。

### Fixed（实施后对抗 review）

- **lone surrogate 炸 http 序列化（R2 P1）**：未知申报键/B8 未知 data 键回显原样放行 `\udXXX` 转义键时，http 部署形态的 SDK 序列化层抛 PydanticSerializationError——宿主拿不到响应、任务已建档、警告永不可见（JS 宿主 JSON.stringify 可无意触发）。回显前 encode/decode 置换 U+FFFD 保证 JSON-safe（B8 同源隐患一并兜底）。
- 警告 note 合法码清单补 zh 对照（`code（zh）` 联合列出，R2 P2-4）；「aud- 前缀会被自动归一」措辞收窄为「aud-<合法裸码>」（R1 P2-3，`aud-高层` 等非法余数仍进 unknown）。

## [0.6.7] — 2026-11-23

**P0 修复（部署后对抗 review 实锤复现）**：v0.6.5 的 have_audience_version 申报只比版本号不绑受众身份——同会话换受众且版本撞号（生产现成撞号对：direct_manager v5 == self v5）时静默谎报 unchanged，宿主拿上一受众画像执行新受众任务。

### Changed（破坏性：参数形状变更）

- **have_audience_version（单值）废弃 → have_audience_versions（{受众码: 版本号} dict）**：短路必须**受众身份+版本双匹配**；宿主带上会话已注入的全部 {受众: 版本}，换受众回来也能安全短路。旧单值键按 B8 unknown field 打回（显式迁移信号；jingleAI 需移除旧参改传 dict）。
- 短路响应回显 `audience_profile_code`（宿主可核对身份）。

### Fixed

- help task_start 补 have_audience_versions 说明（v0.6.5 漏写——宿主顺 help 链读到空）。
- `mema_twin.__version__` 哑雷修正（0.5.1 → 0.6.7，落后 5 版无消费方）。

### 已知边界（v0.6.4 review 记录）

- tool_gap 归一只修**形态差**（twin.*、write/write_file）；**语义差**（中英命名如「JoySpace 上传」vs create_joyspace_page、CLI-vs-宿主工具名）修不了——product_doc playbook 对 jingleAI 仍 3 条误报。演进方向（待办）：面板条目带机器可读工具标识（编译规则层）。CLI 类条目（draw.io/OfficeCLI）对有 bash 的宿主属可证伪误报，暂接受（advisory 面）。
- 复合面板条目（write / edit / read）原子匹配=漏报方向：宿主缺 edit 无提示。
- audit 表无参数摘要列，静默错注类事故不可审计（待办）。

## [0.6.6] — 2026-11-23

jingleAI 反馈第三弹处置（两采纳三否决——过度裁内容会打折扣执行，owner 警告在先）。586 测试绿。

### Added

- **受众画像 note 补自救指引**：画像全文注入的 note 尾部追加「若上文不可见（已被上下文压缩），twin(get, work_type=aud-{受众}) 取全文」——对齐 persona hint 既有句式（申报式机制的自救闭环）。
- **task_start 建档路由指引纳入静态抑制**（S6 同型）：同 client 第二次建档起省略 330B guidance（指引宿主已知晓，纯重复税）；首会话/新宿主不受影响。

### Declined（否决记录）

- 「服务端按 client 记住上次注入、force_full 重新要」：否决——client≠会话，服务端状态化的跨会话串扰会让分身**静默缺席**（比申报式失败模式「多浪费 token」危险）；宁重复不缺席（#895）不动。digest 比对同理（上下文压缩后 digest 残留、全文丢失，比对不出来）。
- 「playbook 裁到 8 条 / 画像变更段只回 1-2 版」：否决——内容裁切，执行打折扣风险（owner 警告在先）；playbook 内容治理已走 global v4 内容路线。
- 「空 persona 压一行」：维持——编译内容质量问题，自然收敛。

## [0.6.5] — 2026-11-23

jingleAI 真实任务反馈第二弹：受众画像注入接入版本申报开关（persona 的 have_persona_version 同款范式，#895）。583+2 测试绿。

### Added

- **have_audience_version 申报短路**：task_start / task_resume / task_revise 加可选 `have_audience_version`——同会话已注入同版本受众画像时响应只回 `audience_profile_unchanged: true` + `audience_profile_version`（不重复注入全文）；版本变更 → 全文重注入附变更说明。单次 task_start 注入量实测省画像全文（2.4-4k 字符）。
- 画像全文注入时响应带 `audience_profile_version`（此前无版本身份暴露，Agent 申报无从取值）。

### Notes

- 雏形注入（画像未编出时的证据垫底）与镜像降级读取无版本身份，不可申报、全文照旧；未申报 → 全文照旧（宁重复不缺席，#895 同款取舍）。
- 回滚 0.6.5→0.6.4 安全（0.6.4 不读 have_audience_version，B8 会按 unknown field 打回——宿主降级时移除该参数即可）。

## [0.6.4] — 2026-11-23

真实宿主（jingleAI task 1143 全链路实测）反馈优化，两处小修 + 一项内容治理（零代码）。583 测试绿。

### Changed

- **tool_gap 归一**：playbook 工具面板条目与宿主 available_tools 的比对从字面集合差改为归一匹配——`twin.` 前缀条目（本服务自身 action）宿主挂 twin 工具即可用；其余条目小写剥分隔符双向子串（`write_file` vs `write` 命中）。修 jingleAI 实测 14 条全量误报（真实在场工具被报缺口、反向刺激宿主反复传参）。已知过宽（短 token 子串误命中→少报缺口）为文档化行为：gap 是纯 advisory 信号，少报好于误报。
- **status 当前行制**（owner 拍板，取代 v0.3.9「增长曲线」拍板）：版本/画像明细**只回当前行**（active 优先，无 active 回最高版本）——全量历史无真实消费方（夜间任务只看 uncompiled/stale 信号；宿主只要 active+体积；rollback 版本发现有兜底：省略 version 回上一版/报错自带可用列表/get(version=N) 按需）。响应 ~17KB→KB 级；完整增长曲线观测走 console（DB 直连）。

### Content governance（零代码）

- global playbook v4 落版：剔除工种特异条目（sales-poster 重复条目删除、sessions_spawn/cloc 一次性经验删除）；电商资质两条挪入 marketing_copy v2（溯源 1123）。话术润色条目暂留 global（溯源任务 96 已不存在，待有 comm_copy 执行记录再挪）。draw.io 保留（owner 拍板）。

## [0.6.3] — 2026-11-22

D4 快照二义性结构化修复（v0.6.2 R2 对抗实锤：task_start 撞票的快照是建档 brief 非偏好正文，照抄 write 会产出垃圾证据；单列小改，580 测试绿）。

### Added

- **twin_pending_values 加 source_kind 列**（write|task_start|空串=旧票）：撞票来源结构化标记，pending 响应 items 与 resolve 响应均带 `content_source`。
- **resolve 响应按来源区别引导**：write 来源 → content_source_note「可直接照抄 write」；task_start 来源 → 「快照是建档 brief 非偏好——照抄 write 会产出垃圾证据，仅当含真实偏好时重写后再 write」。help pending/resolve 同步。

### Notes

- reject 时 content_source 同样在场（note 按 map/canonicalize 附）；无快照票据不出 content_source 键。回滚 0.6.3→0.6.2 安全（0.6.2 不读 source_kind 列）。

## [0.6.2] — 2026-11-22

维度治理闭环（方案 `~/ZCodeProject/docs/mema-twin-v062-dimension-plan.md` v3，方案 R1+R2 与实施后 R1+R2 四轮独立评审）。3 个新列幂等迁移+回填，零破坏性变更。

### Added

- **维度说明字段（description）**（D1）：twin_types 加列，内置 50 项全量补写（适用边界+易混淆项，≤120 字）；canonicalize 的 new_type 支持带 description 落库（≤200 帽）；taxonomy 完整版带/compact 不带。
- **gate 打回带全量清单**（D2）：write/task_start 撞归一门的打回 fields 附 all_values（全量现有值+说明）、similar（difflib top3 近似标注）、candidates（全量形状加法）——「看全量再决定」零前置往返。
- **票据正文快照与宿主归属**（D4）：twin_pending_values 加 content_snapshot（打回时正文全量）/client 两列；pending 响应显式投影（preview+chars，防全文泄漏）；resolve 响应（单条/批量/reject）附 content_snapshot 全文——跨会话积压 3 步闭环（pending→resolve→write）零丢失。
- **孤儿票提示**（D5）：write 成功时对同宿主挂票快照做相似比对（ratio≥0.82），疑似被本次取代的挂票以 orphan_pending_tickets 提示 Agent 显式 reject（服务端不自动关）。

### Changed

- **resolve(canonicalize) 相似确认门**（D3）：新 code/zh 与现有值 ratio≥0.82 或精确撞正名/别名 → 打回 require_confirm（带相似清单+all_values），confirm=true 放行——防清单膨胀；打回场景（Agent 刚看过全量清单）天然无相似零新增往返。批量模式两段式：confirm 预扫摘出疑似重复票不进事务，余票单事务。
- **「写前查清单」约定保留+理由写透**（D6）：预查的真正价值是减少打断 owner 次数与防清单膨胀（API 账打平，打断权重更高）——SKILL/help 理由重写。
- resolve(map) 无效 code 打回附 all_values（替换裸报）。
- B8 白名单：resolve 加 confirm 键。

### 已知边界与回滚（R2 对抗实测后记录）

- **回滚 0.6.2→0.6.1 必须同步重发 0.6.1 SKILL 安装副本**（make install-skill）：0.6.2 SKILL 教的 all_values/similar/orphan 键在 0.6.1 响应不存在；且 0.6.1 的 pending 走 SELECT *，会回显 0.6.2 期间积累的快照全文（体积回归）；resolve 带 confirm 会被 0.6.1 按 unknown field 打回。
- **D4 快照二义性（0.6.3 待办）**：task_start 撞票的快照是建档 brief 而非偏好正文，resolve 响应已加提示（help+响应内引导），结构化标记（source_kind 列）待 0.6.3。
- 已知边界：D5 孤儿提示在同正文 duplicate 写入时每次都出（挂票未关前，~1.6KB/次，非死循环）；批量硬失败会丢弃 confirm_required 信息（修复硬失败票重跑即见）；canonicalize 相似门的别名检测存在「别名+后缀」绕过边界（ratio<0.82 时直过）；自建码不传 description 时 all_values 该项说明为空（help 建议 canonicalize 带说明）。

## [0.6.1] — 2026-11-21

E2E 实测卡点修复 1-8（方案 `~/ZCodeProject/docs/mema-twin-v061-scan-notice-dedup-plan.md` v3，方案 R1+R2 与实施后 R1+R2 共四轮独立评审）。零 DB 迁移。

### Changed — 行为变更

- **scan_notice 判据切换为积压驱动**（卡点 8，owner 方向）：旧「夜间任务 7 天没跑心跳」判据退役（心跳 meta 不再写入）——手动编译勤但没建任务的库不再被重复打扰、评估积压（消化走 playbook 落版）不再漏报。新判据：存在积压（未编译偏好/条款待重编/画像待重抽象/经验待评估）且其逐信号锚距今 >3 天才提醒；响应附 `backlog` 明细与 `overdue_signals`。**夜编清章**：夜间会话领走的提醒章由 task_evaluate(origin=scheduled) 当晚清除，白天人类会话重新可见。AGENT_INSTRUCTION 加无人值守分支（不提问/不建任务）。
- **write 去重键规范化**（卡点 7）：比对键 NFKC 归一+空白压一（落库仍原样）——全角/多空白变体开始命中 duplicate=true。
- **单条 step_update 的 task_id 从静默忽略变校验**（卡点 1/3）：传了即校验步骤归属，不符打回附正确归属与 task_get 指路；不传行为不变。

### Added

- **similar_existing 相似提示**（卡点 7）：write 落库后 difflib 快扫同工种在世证据（字面相似 ratio≥0.82，纯词法非语义判定，不拦截不落票），`similar_existing:[{memory_id,subject,ratio}]` 最多 3 条；护栏：任一侧 >2000 字符跳过/长度差 >4 倍跳过/最近 100 行帽/quick_ratio 预筛。
- **plan_set `seq_map`**（卡点 2）：序号→全局步骤 id 换算表；help 明示 open_questions 的 step_ids 入参=序号、响应=全局 id（同名异义）。
- **scan_notice `backlog`/`overdue_signals`**：提醒携带积压明细，Agent 免二次查询。

### Fixed

- 卡点 4：help 明示 `session` 仅 task_start/task_resume/task_revise/task_submit/todo 五动作接受（plan/step 族不消费，传了会被 B8 打回）。
- 卡点 5：compile 的 next 文案明示「source_memory_ids 直接复制 source_ids_suggested 原样」。
- 卡点 6：write hint / compile note / task_evaluate note 同 client 第二次起省略（指引非契约；duplicate note/session_note/next 恒在，submit/playbook_submit 的 session_note 不压；client=宿主/进程归属——跨会话共享抑制、重启重现，与 session 的会话隔离语义不同）。
- 脏时间戳锚（旧 meta rec.at 任意文本）不再炸 status（锚解析失败视为不超窗；scan_notice 挂载加观测件旁路）。
- make check-skill 版本戳机制沿用；SKILL 安装副本同步重发。
- **回滚注意**：0.6.1 运行期间心跳 meta 停更，回滚 0.6.0 后至次夜真实心跳前 scan_notice 会按旧判据出现 ≤24h 假提醒窗（噪音，勿照办建任务）；可手动 flow.set_meta("last_scheduled_playbook_at", now) 消除。

## [0.6.0] — 2026-11-21

Agent 调用工效改进（评估报告 `~/ZCodeProject/docs/mema-twin-agent-ergonomics-review-20261121.md`，实施级方案 v3 过 R1+R2 两轮独立评审；owner 2026-11-21 拍板 A+B 全部 13 条）。范围：全部为响应层/校验层改动，**零 DB schema 迁移**。

### Changed — 宿主适配须知（行为变更）

- **data 顶层未知键硬拒**（B8）：任何动作 `data` 含未声明键 → `invalid_input` + `allowed`（合法键清单）+ `did_you_mean`（difflib 近邻，无命中 null）。此前冗余键静默忽略、拼写错键报成 required 归因误导。校验位于身份/租户解析之后 handler 之前（auth 优先，拒绝全走审计）；`data` 非 dict（含 falsy 0/""/[]）先结构化打回。白名单落表口径=逐 handler 清点，`_ACTION_KEYS` 与 `_ACTIONS` 不同步启动即 fail-fast。
- **unknown action 错误形态变化**（A2）：`action` 由 `Literal[*_ACTIONS]` 生成入参 enum（ListTools 可见），非法值改由 MCP arg_model 拒为 ToolError 协议错误（此前结构化 invalid_input+actions 清单；该分支现仅 console 直调可达）。`output_schema` 仍为 None（v0.3.11 拍板不变）。
- **夜间 spec 载荷重构**（A8-3，对抗 R2 抓出的上线即炸项）：scan.py 9 处 calls 的说明文本（原 `"rule"`/`"note"` 键混在 data 里）全部外置 `instruction` 键，`data` 只放真参数——B8 上线前不重构会打断夜间编译/心跳；死参 `audience:true` 删除。governance SKILL「带着也无害」口径同步删除。

### Added

- **`help` 按 action 单查**（A1）：`{"topic":"<动作名>"}` 返回单条手册（27 动作+scheduled_tasks；unknown topic 附 topics 清单）；全量模式不变。
- **`evidence` 列举动作**（B3，26→27）：只读列偏好证据索引（memory_id/维度/subject/status/created_at/content_chars，不回正文），`status`/`work_type`/`limit`（默认 50 上限 200）过滤——void「不在证据索引中」死胡同的自纠通道。
- **resolve 批量裁定**（B4）：`tickets=[{pending_id,decision,code?|new_type?}]` 1..20 条，单事务全落/全回滚带 `failed_index`（db 三 helper 加 `commit` 参数联动）；与单条互斥。
- **plan_set `start_first`**（B5）：计划落库同事务把无依赖且未被 open blocking 锚定的首步标 in_progress（响应 `started_step_id`；全锚定 → `start_first_skipped=true`+说明，不产出卡死步骤）；严格布尔校验。
- **compile 结构化 id 数组**（B1）：响应附 `evidence_ids`/`source_ids_suggested`（在世全集，含缺正文 skipped 行，与 submit 对账集同源）/`voided_ids`——submit 的 `source_memory_ids` 直接复制，消灭 fetch-then-parse（G3 警告与 scheduled 阻尼「疑似漏列」的共同根源）。
- **compile 素材包软闸**（B6）：`MEMA_TWIN_COMPILE_BUDGET_CHARS` 默认 60000（对齐 playbook 60k 先例），超闸只警告不截断（全量投影拍板不破）；env 非法回落默认+warnings 提示。
- **裸报错统一补指路**（A4，≥13 处）：task_*/step/resolve/tool_log 的 not_found 与 unknown code 附「task_recent/task_get/taxonomy 可查」类下一步指引（一处 planning guard 修三动作）；plan_revise steps_remove 被依赖一次列全全部依赖方（A5）。
- **非法迁移报错附合法目标表**（A5）：从 `_TRANSITIONS` 生成「'done' 允许的下一状态：…」；参数类 internal_error 附 help 指路、存储类不挂（区分 database is locked 与参数畸形）。

### Fixed

- **rollback 显式 no-op 布尔**（B2）：已 active 时不再 pop `already_active`（对齐 void/step_update 口径；playbook_rollback 同款）。
- **void 响应副作用显式键化**（B7 第一步）：`persona_stale`/`audience_stale` 从 note 长句拼接提为结构化键，note 单句化；prose 键域冻结条款入 README。
- **pending status 四值校验**（A8-4）：仅接受 `pending|mapped|canonicalized|rejected`（此前未知值静默返回空 items 归因误导）。
- **help 硬限制全量载明**（A6）：write 8000/brief 2000/available_tools 帽/prompt_md 10 万/tool_log 字段帽/pb- 保留/origin=scheduled/task_evaluate clamp/taxonomy compact 严格布尔/step_update 批量互斥全集——此前只活在代码与报错里。
- **死面清理**（A8）：README 三行 v0.4.7 滞后、examples 死配置 MEMA_TWIN_MEMA_URL 删除（保留 CLIENT_ID）、stall 阈值与冷却双常量分开表述。
- **安装副本版本戳**（A7）：`make install-skill` 注入版本注释行 + 新 `make check-skill`（去戳 diff 全宿主副本，滞后即红）——修复 v0.4.8 安装副本教退役流程的活冲突。

### Notes

- `_ACTIONS`/`_action_help` 上移至工具定义前（FastMCP 装饰期即求值注解的顺序要求，R1 spike 验证）。
- spec 重构配有扫描测试：全部 calls 的 data 键 ∈ `_ACTION_KEYS`（防再犯）。

## [0.5.1] — 2026-11-16

偏好归因（批次 `memory-writer-20261116`）：证据行落「调用方」标识，供 `/mema/` 记忆详情显示「写入方」。

### Added

- **`twin_evidence.client`**：`TEXT NOT NULL DEFAULT ''`。`pref_actions` 写入时取 `identity.effective_client(data)`（http 头权威，stdio 回落 `data.client`）落行；此前该值只喂审计，证据行无从回答「这条偏好是哪个宿主写的」。`add_local_evidence` / `record_evidence` 增 `client` 形参，默认空串（无头/导入行与历史行同义）。
- **历史归因回填**：迁移期按同库 `twin_audit(action='write', ok=1)` 回填 `client`，窗口 ±1s（`created_at` 是插入时刻、审计 `ts` 是请求结束时刻，实测漏 3 条），同一审计秒出现多个 client 时宁缺勿错；只填空值，幂等；`LIMIT 5000` 为锁上界，余量下个进程续跑。
- 新增 `tests/test_v051_client.py`（11 例）：建表/旧库 ALTER/写入落值/去重不改归属/回填窗口与歧义/幂等。

### Notes

- 回填用 `id IN (SELECT … LIMIT n)` 而非 `UPDATE … LIMIT`：标准 SQLite 构建未开 `SQLITE_ENABLE_UPDATE_DELETE_LIMIT`，后者语法错（首版实现即被新用例逮到）。
- 去重命中不覆盖既有行 `client`：重复写入不产生新证据，改归属等于篡改历史。

## [0.5.0] — 2026-11-12

拆分自持（#1604）：偏好本体从 mema 迁入 twin 自持库，mema 数据面通道整体退役。
方案 `~/ZCodeProject/docs/mema-twin-separation-plan.md`（R1 功能性 14 项 + R2 对抗性 14 项全处置，含云端真库拓扑纠偏：生产=京东云单进程多租户 twin）；实施两轮 review。

### Added

- **偏好自持存储**：`twin_evidence` 加 `content`/`tags`/`source_ref` 三列（旧库 ALTER 幂等迁移，旧指针行 content='' 由导入器回填）——write 不再外呼 mema，正文/用户 tags/source_ref 全部落本库；本库自此为偏好唯一载体（备份见 README 新增小节）。
- **词法去重底线**（owner 拍板「接受语义冲突检测缺位」）：同 work_type 在世证据 sha256(content.strip()) 比对，完全相同 → 返回 `ok:true + duplicate:true + 既有 id`（幂等成功语义，对齐 mema 0.16.6 去重门先例，宿主勿重试）；去重 SELECT 与发号/插入同 `BEGIN IMMEDIATE` 事务（双宿主同文并发不双插）。
- **本地发号**：`db.add_local_evidence` 发号 `MAX(memory_id)+1`，裸 INSERT + 撞 UNIQUE 回退重试（弃 INSERT OR IGNORE——并发发号实测静默丢写的坑）；写路径恒本地发号；import 走 `record_evidence(memory_id=原值)` 显式直插（迁移保原 id，`<!-- src: N -->` 溯源不断链）。
- **迁移工具**：`python -m mema_twin.import_mema <jsonl> [--tenant uid] [--users-dir] [--dry-run]`——吃 mema 侧 `scripts/export_twin_bucket.py`（memory-arbiter 仓）产出的 backup envelope JSONL；双模式（既有指针行回填 content+subject / 缺失行插新行，三维从 tags 还原含 `twin:aud:` 受众级特判）；按 envelope.workspace_canonical 过滤（#976 痕迹行防误迁）、非 active 跳过、无三维 tag 行 skip 人工看、撞号计冲突；幂等重跑；完成落 `import_done` meta（console 迁移横幅判据）。
- **独立本地 Console**：`mema-twin-console` 命令（127.0.0.1:8766，仅 loopback；**仅 default 单机形态**，多租户渲染引导横幅）——从 mema console /twin 页（d6a1714..b8af279 六 commit 资产）整体迁入并简化：写面进程内直调 `_twin_impl`（spawn stdio MCP 方案退役，client='console' 审计归因）、证据抽屉直读本地 content（mema 联查退役）、门卫四件套保留（Host 白名单/415/X-Console-Request/OPTIONS 无 CORS）；「按产出类型浏览」形态与可用性修复原样保留。
- 新增测试 tests/test_v050_separation.py（31 例：发号/去重/旧库迁移/import 端到端/幂等/missing_content/响应键/收缩钉）。

### Changed

- **sink 收缩**：数据面（remember/find/read_memory/review_conflicts/notices 透传/token 透传）整体退役；只剩 `whoami`（多租户路由）+ 三态错误类 + `_env_client`（身份 env 校验）。多租户 whoami 网络不可达新映射 `gateway_unreachable`；`MEMA_TWIN_MEMA_URL` 语义收窄为「网关 whoami 定位（仅多租户）」；`MEMA_TWIN_MEMA_TOKEN` 不再消费（仅存互斥名单）。
- **冲突链路退役**：compile 的 conflict_warning 节、status 的 open_conflicts 键、mema_notices/mema_notices_guidance 出口与分诊文案——偏好不在 mema 后写时语义冲突检测不再服务 twin（R2 拍板接受缺位，真裁定靠编译期模型 + console 人工审）。
- **write/status/help 文案**：write 响应改 `memory_id/evidence_id/duplicate` 键族；void note 改「正文已在本库归档」；help 同步（去 mema retire 句、加 duplicate 勿重试句）。
- 文档面：README（自持表述/env 表/备份/迁移/Console 三新节）、skill/SKILL.md（双写禁令改「mema-twin 桶已冻结」+ duplicate 句 + mema_notices 段退役）、skill-governance/SKILL.md（同）、deploy/mema-twin.service（MEMA_TWIN_MEMA_URL 注释化）；`__version__` 从落伍的 0.3.8 钉到 0.5.0。

### Removed

- `mema_twin/sink` 数据面函数全家与 `_BUCKET`/`_memory_id_of`；tests/test_notices.py 整文件；sink 数据面测试族（test_sink_token.py 重写为收缩面）。全量 476 passed（含 console 43 例；import 冲突/无 tag 行以退出码 2 阻断后续部署步骤）。

## [0.4.8] — 2026-11-09

- **v0.4.8 task_revise 补注入面 + get 补未编译增补**（来源：2026-11-09
  jingleAI PRD 修订链路事故——revise 生成子任务不注入 persona/增补，跨会话
  返工时上下文已压缩，已编译偏好（未评审保持 V1.0、文档信息/修订记录不占
  编号）白纸黑字却不在场，agent 连犯三条规范；方案
  `~/ZCodeProject/docs/twin-v048-revise-persona-injection-plan.md`，两轮方案
  评审（设计轮 4 P2+6 P3、对抗轮 2 P2+4 P3）全部吸收）：
  - **task_revise 注入面同 task_resume**：persona（#895 have_persona_version
    申报短路）+ 未编译增补 persona_supplement + 受众画像 + 无专属 persona 时
    同域 fallback + playbook。短路分支同样带增补（#895 陷阱）。**revise 的
    guidance/help 不教短路**（对抗轮 P2-1）：revise 主场景是跨会话返工，
    agent 可从任务行抄 persona_version 假申报导致全文缺席、事故重演——
    help 明写「仅同一会话上文已有该版本全文才申报」。
  - **子任务行 persona_version 语义**：从抄父行改为记本次实际读取的 active
    版本（父行是建档时版本号，中途 compile/rollback 后已失真；消费面仅展示
    投影，无逻辑消费）。
  - **校验补齐（无副作用先行）**：new_brief 过 _brief_guard 2000 帽（既有
    缺口顺带修）；have_persona_version 脏值在一切状态变更前打回。
  - **get 读 active 附未编译增补**：逃生口修完整——get 是 revise 等链路
    「上文不可见」时的取回口，此前只回全文不带增补。指定 version 历史读与
    aud- 受众画像分支不附（增补锚定 active 语义；aud- 证据行不参与编译
    生命周期，AR-2）。软失败不阻断全文返回。
  - 文案同步：server help（task_revise/get）、task_resume 拦截文案、
    skill/SKILL.md（版本行/task_revise/同会话短路句扩列+限定/get）、
    README 三处。
  - **superseded 态 revise 拦截文案纠偏**（施工对抗轮 P2-1）：对被 revise 换代
    的 superseded 任务重试 revise，旧文案误称「历史遗留状态…重新 task_start」
    ——改为指路 task_recent 取最新代（resume 续作/再 revise）。
  - 测试 417 绿（397 基线 + 20 新增：revise 注入 F1-F8（test_flow）+
    supplement/get/playbook/画像/elif 分支/软失败/superseded 门文案共 12 例
    （test_server_boundary））。

## [0.4.7] — 2026-11-04

- **v0.4.7 往返次数优化**（来源：宿主会话复盘 18min/27 次往返归因，方案
  `~/ZCodeProject/docs/twin-v047-roundtrip-optimization-plan.md` v1.1，两轮方案
  对抗评审 + 两轮实施 review 全部吸收）：
  - **plan_set task_id 缺省回退**：恰一个 planning 任务时自动回退并回显
    `task_id_resolved`+`resolved_brief`（供 agent 核对防劫持陈尸任务）；0 或
    >1（瞬态并发残留）拒绝并附 id+brief 清单。task_submit/plan_revise 不做
    回退（关键写路径显式优于隐式）。
  - **step_update 批量**：`steps=[{step_id,status,reason?,reflection?}]`
    （1..50）+ 顶层 task_id 必填（跨任务拒；server 边界 stall_advisory/audit
    挂载依赖它）；单事务任一条被拒整笔回滚（`failed_index`），失败原子性强
    于逐条；`applied`/`noop` 分列；回滚同步恢复进程内 no_op 计数快照（防重试
    consecutive_noop 虚高）；批量 step_id 采用 _coerce_task_id 同款严格口径。
    **审计口径变化**：批量一次调用落一行审计（task_id 在、step_id 空），
    step 维度的精确账改由响应 results 承载——按任务维度归因不变。
  - **task_submit 附带 steps**：先补打卡再收口一次往返；与收口非同一事务，
    steps 落库后的一切拒绝路径带 `steps_applied` + 重试引导（打卡幂等，补齐
    open_steps 直接重交）。
  - **taxonomy kind 数组/`'all'`/compact**：多类形状=响应含 `kinds` 键（顶层
    `count_total`，条目附 `kind` 防拍平错配；数组传单元素也走多类形状）；
    `compact=true` 精简投影（code/zh/aliases 前 5——归一引导保留，完整别名
    面仍只在完整响应）。单 kind 形状逐键不变。
  - **compile 冲突警示（0.18.3 策展面 twin 侧，owner 拍板 2026-11-01 Q7=A）**：
    素材含未裁决冲突时插警示头 + `conflict_warning` 字段（键投影 ≤10 条）。
    实现走 `sink.review_conflicts`（与 status.open_conflicts 同通道同口径），
    **不直开 memory.sqlite3**（C 形态不变量）；调用点在逐条证据 read 之前
    （mema 挂死先 10s 软失败）；软失败不阻断编译。**口径变化**：冲突桶过滤
    只认当前 bucket（不再含 'mema-twin-dev'），与 status 计数一致。
  - help 文案四处同步；skill 文档同步（plan_set 缺省/批量打卡/task_submit
    steps/taxonomy 数组与 compact/tool_log entries 结构）。
  - 测试 397 绿（374 基线 + 23 新增）。
- **Bearer 前缀可选**（2026-10-25 owner 拍板）：宿主 MCP 配置的 Authorization
  头与 `MEMA_TWIN_MEMA_TOKEN` env 均可直接写裸 token——twin 自动补
  `Bearer ` 前缀（协议面不变：网关硬性要求前缀；`Bearer` 无体仍打回）。
- **多租户形态（MEMA_TWIN_USERS_DIR，C 同目录同居）**：twin 与鉴权网关同机
  服务多用户——宿主各自在 MCP 配置带 Authorization 头，twin 原样透传（写谁的
  库由网关按 token 裁决，服务器零凭证落盘）；租户经网关 /whoami 解析（不缓存，
  吊销即时生效），账本/镜像按租户落 `users/<uid>/twin.sqlite3` 与
  `users/<uid>/twin-files/`——与该用户 memory.sqlite3 同目录同备份单元，core
  的库迁移机制对 twin 文件零接触。防御纵深（两轮方案对抗评审 + 两轮实施
  review 全部采纳）：USERS_DIR 形态缺头/坏 token 除 help 外全拒（unauthorized /
  tenant_unavailable 两态错误面）；租户 contextvar 未设时 db.connect() 即抛
  （fail-closed 覆盖 audit/normalize 等全部直连点）；SQLite authorizer 引擎
  强制 twin_* 命名空间（白名单外含 ATTACH 一律 DENY；writable_schema PRAGMA
  引擎层封死——直写 sqlite_master 的唯一使能开关；CREATE TABLE 的内部
  sqlite_master INSERT 经探针确认不可拆分保留）；进程内会话 todo 桶/文件镜像/
  stall 冷却键全部带租户维度（P0：todo 桶原实现同 session 名跨租户互见互覆）；
  playbook 镜像路径用 canonical 解析段而非原始 key + 路径字符别名归一拒绝
  （P0：alias 链携带 `../` 实测跨租户写镜像）；USERS_DIR 与静态 token/镜像/DB
  env 互斥（启动校验 + 解析点双重强制）、stdio 传输在多租户形态启动即拒；
  TenantError 落结构化错误不裸逃逸；twin 不代建 users/<uid>/ 目录（由网关
  /whoami 预建 store 时产生）。default 单用户形态行为零变化。测试 369 绿
  （340 基线 + 29 新）。
- **鉴权端点支持（MEMA_TWIN_MEMA_TOKEN）**：sink 新增 `Authorization: Bearer`
  头——env 非空才发，未设/空 = 不发头（本地无鉴权 mema 形态零变化）。与
  `MEMA_TWIN_MEMA_URL` 指向带鉴权的 mema 端点组合，偏好写入落到该端点背后的
  目标库；twin 自有元数据仍落独立 twin.sqlite3（schema 不同、生命周期不同，
  设计如此不合文件）。token env 校验对齐 MEMA_TWIN_CLIENT_ID 同款纪律（含
  空白/换行/注入形态就地打回，启动即校验）；token 明文走 EnvironmentFile
  不进 unit 文件。
- **sink HTTP 错误面拆分**：HTTPError（此前被 URLError 父类分支吞掉）单独
  捕获——401/421 是「被拒」不是「不可达」，401 报文明指 MEMA_TWIN_MEMA_TOKEN、
  421 指 host 门，响应 detail 透出 200 字符。鉴权端点部署首故障是 token 缺失，
  旧报文「不可达」会把排查错误地引向网络层。
- **部署件**：`deploy/mema-twin.service` systemd 模板（loopback 硬门 /
  UMask=0077 / `<...>` 占位符，自托管可用）。
- 测试 340 绿（332 基线 + 8 新：token 头/不发头/身份头共存/脏值打回/启动校验/
  401 与 421 报文语义/真连不上仍报不可达）。
- **SKILL.md 按消费者拆分 + 去重精简**（docs）：skill 从 279 行/24.5KB 收到
  两个文件——`skill/SKILL.md`（交互工作流：查证协议、分流、路由、执行门）+
  `skill-governance/SKILL.md`（夜间治理/编译会话专用：两支线流程、
  origin=scheduled 口径、硬预算自检、冲突裁定、验证门被拒处置、晨报汇总不代裁）。
  拆分理由：夜间会话 80% 内容是交互死重；夜间 skill 被唯一消费者整份读，
  无「触点不读文件」风险。三轮精简的共同原则：同一事实只留一处权威表述
  （write 抽象口径并入分流、status 判空合一、建档默认走计划三处合一）；
  help 已覆盖的入参与基础语义一律委托 `twin(action="help")`，只留路由、执行门
  与 help 未列的实测口径（task_evaluate 的 origin、available_tools、aud- 前缀
  无需 audience、X-Mema-Client、status 判空三形态等）；夜间执行细节以
  `scan.py` SCHEDULED_TASKS_SPEC 为单一真源。Makefile install-skill 同步分发
  两个 skill 到三宿主。语义核对：路由/信号 38 项、独有事实 69 项零丢失。

## [0.4.6] — 2026-10-21

「agent 困在流程」事故的修复与观测补齐（issue #1384；事故定性：宿主
agent 行为空转，twin 无锁死 bug，但防跳步围栏的硬报错客观上延长空转）。

- **step_update 同状态重复打卡幂等化**：迁移表新增恒等迁移（in_progress→in_progress、
  done→done）标 `idempotent`，在写库前短路返回 `ok + no_op`——同状态重复打卡从
  「不允许从 X 迁到 X」的硬报错改为「已是 X，本次无变更」+ 继续执行指引。不改库、
  不动 decided_at（挂墙计时基线不被打卡刷新）、不触三门（门只拦推进新步骤）。
  `consecutive_noop` 给出连续无变更次数，≥3 时 guidance 明示空转与三个出口
  （红队 P1-3：单 `no_op` 布尔会让按 ok 判成败的宿主把空转识别为成功）。
- **plan_revise 零实变不再谎报「已修订」**：steps_update 全是恒等迁移时走
  `no_changes` 分支（v0.4.0 只为「仅 revision_reason」建过该分支）——此前会回
  「计划已修订，被阻塞的步骤现在可推进」的成功假信号，正是事故同款模式。
- **挂墙 advisory（stall_advisory 独立键）**：某步骤 in_progress 超过阈值
  （`MEMA_TWIN_STALL_MINUTES`，默认 30 分钟）无推进时，后续 ok 响应附出口指引。
  挂独立键而非 warnings——warnings 是真故障通道（落盘失败/步骤跳过/降级提醒），
  混入会让宿主分诊失灵。同 (task_id, step_id) 有 30 分钟冷却窗（进程内 dict +
  twin_meta 键双兜底），不每轮刷。措辞按「先自救再放弃」排序，task_close 降为
  最后手段并带前提；独立开关 `MEMA_TWIN_STALL_ADVISORY`（与审计解耦）。
- **action 级审计日志（twin_audit 表）**：每次 twin 调用落一行
  （action/ok/error/ms/client/task_id/step_id，**不记正文**），环形保留近 2 万行
  （行数帽 + 字节帽双投影）。回应事故调查的观测盲区：此前 HTTP 200 分不清业务
  成败、服务端不留调用记录，事后只能靠 DB 状态推断。`status` 的
  `plan_stats.audit` 给 total/failed/unreachable/last_24h/top_actions——
  `unreachable` 单列，mema 离线期的失败不混进「空转失败率」。client 取连接头
  （http 模式权威身份），仅读 data.client 在多宿主部署下恒为 null。
- **首版实现被两轮独立对抗评审推翻并重写**：审计原用 twin_meta 单列 JSON 环形
  缓冲，评审实测三重问题——满环时每次调用全量 json.loads+dumps 重写 2.8MB blob
  （62~156ms/次，`help` 从 0.3ms 变 61ms）、写放大 ~112KB/次、跨进程读-改-写
  最坏丢 48%、脏 JSON 一次 append 静默抹掉 299 行真实记录、注释称「<2MB」实测
  4.52MB。换独立表 + 环形 DELETE 后单行 INSERT ~0.1ms（19x），sqlite 事务自带
  跨进程串行，读-改-写从根上消失。旧 JSON 缓冲一次性灌入新表再删键（幂等，
  损坏则弃键不静默重置掩盖证据）。环形 DELETE 用 id 阈值删（走主键索引）而非
  NOT IN 子查询（全表扫 + bloom filter，复审实测同场景 6.93ms vs 0.005ms），
  且仅超帽时才删——实测每次调用记账成本：未满环 ~1.9ms、满环 20000 行
  ~2.0ms（对比 JSON 首版满环 62~156ms）。
- 记账未修：恒等迁移绕过三门（done→done 也绕过 blocking 疑问门——done 步骤本
  不该再被拦，可接受）；`consecutive_noop` 为进程内计数、服务重启清零（精确账
  在 twin_audit 表按 action=step_update 查）；advisory 无按步骤性质的分层阈值
  （长任务如编译 persona 会误触发，靠「正常推进请忽略」措辞兜底）；审计行无
  保留期限（环形帽即事实期限）。
- 测试 327 绿（316 基线 + 33 新，其中 11 条为评审发现的回归）。

## [0.4.5] — 2026-10-01

F4 同域 fallback 注入（mema #956 2026-09-10 拍板，原 v0.3.9 后主项；方案
docs/mema-twin-f4-fallback-design-2026-10-01.md 经方案对抗评审 5P2/7P3 修订 +
实施后两轮独立评审——轮1 功能核对无 P1/P2，轮2 对抗无 P1/1P2 已修/8P3 修4记4）。

- **fallback 注入**：task_start/task_resume 在本工种无专属 active persona（含
  增补在场与纯 hint 两个子分支）时注入同域最成熟工种（活证据最多，tie-break
  版本高者→code 字典序最大）的 persona 全文 + 其余同域候选各一行身份摘要 +
  降级声明（垫底定位/权威来源优先/内容业务规则不适用）；硬返回无转达口子；
  专属 persona 落版（mirror 降级读到同样算在场）自动退出。候选域归属双源
  （twin_types 优先回落 taxonomy，老库缺行不丢候选）；aud- 伪类型不参与借出；
  custom 码按 domain 判定不做 is_custom 过滤；donor 单快照读出（无二次读）。
- **轮2 P2-1 修复**：canonicalize 的 domain 入口归一（strip+压平空白）+
  读取侧同款归一——此前历史脏 domain（前后空格）会让同域匹配静默失联，
  F4 主场景（custom 码借入借出）被一个空格击穿。
- **轮2 P3 顺手修**：空正文 donor 跳过、病态 TEXT 版本号跳过（不再炸
  task_start 归罪 invalid_input）、note 渲染 label_zh 压平换行+超长截断
  （「各一行」契约）、resume 侧 help/SKILL 补声明。
- 记账未修：专属落版竞窗内 note 一次性失真（下次调用自愈）、fallback 三段
  查询非同快照（垫底选择轻微漂移可接受）、fallback 无版本短路（§6 明确不做，
  resume 链重复注入 ~persona 体积，狗粮期有反馈再议）、全域 persona 全量拉取
  读放大（真库规模无感）。
- 测试 294 绿（274 基线 + fallback 20 条）。

## [0.4.4] — 2026-10-01

执行经验采纳落地（方案 docs/mema-twin-exec-experience-adoption-2026-10-01.md，
采纳 workbuddy 建议；两轮评审，轮2 独立对抗无 P1）。

- **W1 经验流失提醒**：task_submit 对未建计划的交付在 warnings 追加 advisory 提醒
  （不拦）：执行经验不会进 playbook 沉淀——重复执行型工作下次先 plan_set、一次性
  事务下次不建档。含 replanned 跳过行的任务算「有计划」不提醒（行存在性口径，
  补测试钉住）。真硬门仍只留两个（reflection/收口门），两档路由判断不动。
- **W2 采纳率观测**：plan_stats 新增 tasks_submitted / submitted_with_plan
  （采纳率=后者÷前者，分母 0 无读数；历史 submitted 会稀释读数，看增量任务）。
- **顺手修（轮2 对抗 P3-4 既有缺陷）**：task_resume 的「原任务没有 todos」警告
  从直接赋值改为追加式——此前会覆盖刚写入的 deps_dropped/questions_dropped 深拷贝
  降级警告。
- SKILL.md 单独 docs commit 补 0.4.1–0.4.3 同步欠账（含一处事实错误修正：
  plan_stats 整桶缺席条件是 status 内部软失败，非「无计划数据」——空库仍返回
  全零桶）。测试 274 绿（270 + 采纳 4 条）。

## [0.4.3] — 2026-09-26

定时任务提醒加 3 天抑制窗口（用户拍板，治多宿主重复询问）。

- **scan_notice 提示抑制**：每次实际提示时全局盖章（twin_meta 单键
  `scan_notice_shown_at`，claim 即 delivered 与 twin_notices 同款取舍），
  3 天窗口内所有宿主静默；窗口过后心跳仍未跑过则恢复提示并重盖。心跳新鲜
  （夜间任务在转）时与抑制无关直接静默。多宿主重复询问压到 3 天最多一次，
  由第一个看到的 agent 提问；心跳 7 天窗口吸收单晚失败，3 天抑制叠在其上。
- 测试 270 绿（抑制/恢复/心跳优先三态回归；既有二次调用 scan_notice 的
  测试改为显式重置盖章模拟 3 天后）。

## [0.4.2] — 2026-09-26

升级提示版本源防陈旧（发版后实测发现）。

- **current_version 取 max(installed, repo)**：可编辑安装的 dist-info 元数据是
  安装时快照，不随 pyproject bump 更新（实测 dist-info 停在 0.3.11 而代码已
  0.4.1）——只信 importlib.metadata 会对着自家新版本误报 update_available。
  仓库在场时解析仓库 pyproject 与安装态取较新者；current 偏高只会抑制提示，
  不会制造假升级。发版流程提示：bump 版本后重装一次 venv（`uv pip install -e .
  --no-deps`）可让元数据同步，但不再强依赖。
- 测试 268 绿（新增陈旧快照纠正/仓库缺位回退回归）。

## [0.4.1] — 2026-09-26

夜间定时任务合并为单任务双支线（用户拍板；响应 v0.4.0 双任务口径引入的配置/心跳
复杂度）。

- **spec 合并**：`twin_nightly_compile` 单任务顺序执行两支线——①执行经验评估
  （status→`task_evaluate`→`playbook_submit`）放最前（轻且快，防被最重的编译段
  吃掉会话预算后永远轮不到；水位在，漏一晚次晚自动补）②persona 编译
  （pending→compile→submit→画像 compile→submit）。两支线各自保留无素材早退规则。
- **心跳与保险丝单键化**：`task_evaluate` 新增 `origin=scheduled` 入参（唯一合法
  值），调用成功即刷 `last_scheduled_playbook_at` 心跳——与当晚有无新经验无关；
  `scan_notice` 保险丝回退单键（编译支线连续无素材不再误报停转；老形态只建编译
  任务的宿主永远过不了保险丝，会被持续引导升级成合并 spec）。
- 已建 v0.4.0 双任务的宿主：把第二个任务删掉、把编译任务更新成合并 spec（或
  直接按新 spec 重建一个）；只建了编译任务的宿主：按新 spec 更新现有任务。
- 测试 267 绿（scan spec 单任务断言 / 保险丝单键断言 / task_evaluate 心跳与
  origin 校验同步更新，新增心跳回归 1 条）。

## [0.4.0] — 2026-09-26

工业级执行能力改造（方案 mema 记忆 #1079，三轮拍板收敛 + 实施规格
`docs/mema-twin-v0.4-exec-design-2026-09-26.md`；P0 升级提示 / R0 选码收敛 /
P1 执行计划层 / P2 深拷贝与收口 / P3 playbook 闭环）。

- **P1 执行计划层（两档路由 + 六态步骤状态机 + 三门）**：建档任务默认走计划（唯一豁免是
  无重复执行价值的一次性事务；既已建档必须 `plan_set` 后动手，task_start guidance 强硬
  文案 +「拿不准要不要建计划时，建」方向性默认）。`plan_set` 落 `twin_plan_steps`
  六态步骤（pending/in_progress/done/failed/blocked/skipped；闭环=done∪skipped）与
  `twin_plan_questions` open_questions，直接放行无审批门（用户拍板：计划写完不强制
  人工确认）；重复调用即重排（未完结步骤 skipped(replanned) + 清 open 疑问，done 保留）。
  `step_update` to=in_progress 过三门（顺序固定）：blocking 疑问门（open_questions
  半硬——blocking 未解答关联步骤推进被拒，`plan_revise(answers=…)` 写回解锁；
  task_submit 只警告不拒）→ 依赖门（依赖满足=done∪skipped，附 open_steps 清单）→
  单一 in_progress 门（并发互斥）。pending 直跳 done/failed 自动标 `backfilled`
  （允许补打卡，审计可见，且豁免单一门）；failed 必带 reflection（服务端硬门，
  沿用 flow.set_status 先例的缺失报告口径）。并发防写：条件 UPDATE
  `WHERE id AND status=frm`，rowcount=0 打回当前态。
- **P2 收口门与深拷贝**：task_submit 建档任务（twin_plan_steps 行存在）未闭环步骤
  整笔打回（附 open_steps 清单 + 对账引导），闭环后 outcome=success 落任务行
  （twin_tasks 新列，迁移探表幂等 ALTER）；task_close 入参 outcome（failed|superseded，
  脏值打回）并批量 skipped(reason=closed)。`plan_revise` 四类修订
  （steps_remove/steps_update/steps_add/answers）单次提交固定顺序执行——remove 仅
  pending 且不被本代未删步骤依赖（含 pending 依赖方）；steps_update 改 deps 过环检测
  （DFS）；add 过环校验。task_resume/task_revise 深拷贝未完结步骤到新 task_id
  （origin_step_id 溯源、depends_on 重映射、旧代已终结依赖剥除并计数 deps_dropped、
  open 疑问拷贝 answered 不拷），旧代任务 superseded。
- **P3 执行经验 playbook 闭环**：独立表 `twin_playbooks`（不复用 twin_prompt_versions
  ——源是执行记录非偏好证据，构不同）。夜间任务 twin_nightly_playbook_evaluate：
  `task_evaluate` 按 twin_meta 水位（单调推进，显式 ids 也推进）打包已闭环任务执行
  记录（reflection/tool_log/步骤路径/跨宿主按 client 分层）出素材包（体积护栏
  step≤50/tool≤20）→ 会话模型提炼 → `playbook_submit` 落版（origin=scheduled 过
  验证门：溯源校验 foreign_task_ids 剔除后拒绝、G1 素材回声（含 active 自锁守卫沿袭
  降级）、G2 无标题、空转阻尼 source_task_ids⊆active 拒；交互式违规只警告落版）；
  `playbook_rollback` 同 persona rollback 语义。task_start/task_resume 注入 active
  playbook（work_type→global 两级回退）——**连续性分级**（用户拍板）：playbook 记
  last_used_client，同宿主轻注入（全文+近期验证提示），换宿主重注入（+降级链提示
  +`available_tools_required`），下次 task_start 传 available_tools 服务端逐条比对
  工具面板回 `tool_gap`；playbook 是 advisory，与实际环境冲突按实际执行。
  `tool_log` 批量记工具经验（1..50，outcome ∈ success/fail/degraded）。
  pb- 前缀进 code 保留段（validate_code_segment）防与 playbook key 混淆。
- **P0 升级提示**：`update_monitor` daemon 线程每 24h 拉 GitHub 双通道
  （raw.githubusercontent → contents API 兜底）比对 pyproject 版本，`twin_notices`
  随 ok 响应携带（仅 ok；挂接全 try/except 兜底——通知故障不得影响工具可用性）；
  update_available 同版本 7 天抑制、post_upgrade 真升级发一次（首装建基线不发）；
  版本单源 importlib.metadata（修 0.3.8/0.3.11 drift）回落仓库 pyproject；
  env `MEMA_TWIN_UPDATE_CHECK=0` 关闭。
- **R0 classify_code 单点收敛**：`store.classify_code(code)` 统一 aud- 特判伪类型分派
  （此前 ≥8 处散落判定）；compile_actions 两处替换为单函数调用。
- **SKILL.md/README 同步**：SKILL.md 新增「执行流（v0.4）」段（两档路由、打卡与追认、
  open_questions 咨询流、tool_log 采样规则、playbook advisory 底线、twin_notices
  分诊）；README 设计要点/动作表/环境变量三处同步（新表、7 个新 action、
  update env）；status 响应新增 `plan_stats` 桶。
- **两轮 review 修复（轮1 8 项 + 轮2 独立对抗评审 1 P1/7 P2）**：status 补
  `playbook_rejected` 计数面（被拒提示此前引用了不存在的字段）；pending→skipped
  与 done→skipped 必带 reason（拍板 ⑧ skipped reason 必填）；单一 in_progress
  不变量折进条件 UPDATE（跨宿主竞窗原子兜底）；收口门折进 submit 条件 UPDATE
  堵死 TOCTOU（门查后他宿主 plan_set 插步不再绕过）+ task_close 单事务（竞争
  失败不再留下已强跳步骤）；task_evaluate 工具记录截断恢复每任务口径（SELECT
  漏 task_id 曾退化成全局 20 条闸，多任务批次后位任务工具证据被永久吞掉）；
  交互式 playbook_submit 的 G1/G2 违规警告可见化（此前静默丢弃）+ 全无效溯源
  拒落版（防 source_task_ids=[] 的 active 版令空转阻尼永久失效）；tool_gap
  解析兼容缩进行（与编译规则示例自洽）；reflection/reason/brief/answer/tool_log
  字段长度帽 + 素材包 60k 总闸（渲染截断，库内全文保留）；`_find_cycle` 迭代化
  （深依赖链不再 RecursionError 抛穿）；plan_set 无条件清遗留 open 疑问；深拷贝
  丢弃锚点全失的僵尸 blocking 疑问并回传 questions_dropped；revision_reason-only
  如实回 no_changes；blocking 入参严格布尔且必须锚定步骤；task_id/step_id 显式
  id 范围界（0..2^63-1）；scan_notice 升级双任务口径（评估任务停转同样触发重
  提醒）；升级检查常驻进程链式续检（兑现「daemon 24h 比对」）；首次注入文案
  如实（不再对从未使用的 playbook 谎称「其他宿主使用过」）；测试防真实联网；
  .gitignore 挡 playbooks/。266 绿（基线 197 + exec 37 / playbook 19 /
  update_monitor 12 / scan 评审回归）。
- 测试 247 绿（基线 197 + 新增 test_exec 25 / test_playbook 13 / test_update_monitor 12）。

## [0.3.11] — 2026-09-14

- **线格式 content 单写钉死（对齐 mema 0.16.5 flip）**：工具响应显式构造 `CallToolResult`——单一
  `content[0].text` 承载 compact JSON（`separators=(",", ":")`，吃掉 FastMCP indent=2 副本的线体积），
  无 `structuredContent`，`tools/list` 不带 outputSchema。此前单写纯属侥幸：裸 `-> dict` 注解使 FastMCP
  生成 outputSchema 失败、SDK 才跳过默认双写（structuredContent + indent=2 content 副本并存，mema
  实测 2-3x 线膨胀）；任何给返回加 TypedDict/BaseModel 注解或 `structured_output=True` 的重构都会
  不惊动任何人地翻回双写——现以显式构造 + `-> CallToolResult` 注解（FastMCP designed 逃生口）把
  单写钉成设计。响应体语义不变（ok/error dict 原样 json 序列化），信封回归测试锁定（单块文本、
  compact、structuredContent 为 None、与 `_twin_impl` 输出等价）。对抗性评审一轮无 P1：补注册
  元数据断言（`output_schema is None`——只测信封防不住「改注解翻双写还硬错误」，评审 P2）、
  `json.dumps(default=str)` 对齐旧路径非 JSON 值静默兜底（评审 P3）。

## [0.3.10] — 2026-09-11

- **write 任务上下文维度继承（#959 用户拍板）**：`write` 新增可选 `task_id`——改稿/返工现场沉淀偏好时，
  缺省维度直接沿用所属任务行的三维度（task_start 建档时已过归一门的 canonical code），不再从零重判
  打扰用户。规则：显式传入优先于继承；只补必填维度（`scope=audience` 不继承 work_type）；任务行维度
  为空时回退 required 报错并注明「继承不到」（不静默猜）；裸写不传 task_id 照旧全显式走归一门
  （#951 数据入口正确性不动）；task_id 不存在/类型脏直接 invalid_input。响应新增 `dims_inherited`
  列出继承项（透明面）；审计面不变（twin_evidence 三 code 列 + twin:wt/au/pu tags）。
  依据：维度值在 task_start 已被用户确认过，返工现场重复重判属多余打扰且重复有出错概率——
  继承消除猜的必要（优于「暂定归属」只降低猜错代价）。

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
