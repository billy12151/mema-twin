"""v0.7.1 沉淀质量治理（开发方案 v1.3，两轮四路评审定稿）：
W1 沉淀判据同文（写法才沉淀、非项目特有约定）/ W3 工具参数确定性检测
（compile 标注 + submit 只提醒不拦）/ W4 persona 预算放宽 / W5 skill 文件头。

纯文案部分（T1/T10）只钉「判据与预算在各处存在」；宿主读了会不会照做靠夜编
产物复核（方案 §5 首个夜编后的复核）。夹具用等价脱敏样例，不含生产证据原文。
"""
import os
import pathlib
import re
import subprocess

from mema_twin import compile_actions, flow, scan, server, templates, toolcheck

REPO = pathlib.Path(__file__).resolve().parent.parent


def _dims(wt="proposal"):
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": "提案", "code": wt,
                      "label_zh": "提案", "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": "本人", "code": "self",
                     "label_zh": "本人", "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": "同步", "code": "sync_info",
                    "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
    }


def _write(content, wt="proposal", **kw):
    data = {"content": content, "work_type": wt, "audience": "self",
            "purpose": "sync_info", "client": "zcode"}
    data.update(kw)
    out = server._twin_impl("write", data)
    assert out["ok"], out
    return out


# ---- T1：判据逐位置同文（精确子串；晨报清单句与 gov :19/:41 不作断言位） ----

def test_t1_judgment_phrase_at_every_position():
    skill = (REPO / "skill" / "SKILL.md").read_text(encoding="utf-8")
    gov = (REPO / "skill-governance" / "SKILL.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    spec_text = str(scan.SCHEDULED_TASKS_SPEC)

    assert "换一个需求、或换一个" in templates.WRITE_GUIDANCE
    assert "换一个需求" in "\n".join(templates._TYPE_RULES)
    assert "换一个需求" in "\n".join(templates._AUDIENCE_RULES)
    assert "换一个需求" in skill            # 分流规则新增条
    assert readme.count("换一个需求") >= 2   # README :57 与动作表 write 行
    assert "换一个需求" in spec_text        # scan.py compile instruction
    assert "换一个需求" in gov             # gov SKILL ②第 3 条
    assert "换一个需求" in server._HELP_ENTRIES["task_submit"]
    assert "换一个需求" in server._HELP_ENTRIES["task_revise"]
    # 「且非项目特有约定」限定必须出现在两个终判位（write 引导 ② 与编译规则新增条）；
    # ② 的措辞是「④ 清单复检」形态，断言其排除清单关键词
    assert "项目特有的写法约定" in templates.WRITE_GUIDANCE
    assert "且非项目特有约定" in "\n".join(templates._TYPE_RULES)
    assert "且非项目特有约定" in "\n".join(templates._AUDIENCE_RULES)


# ---- T2：task_submit guidance 口径 ----

def test_t2_task_submit_guidance_writes_only_style_rules():
    t = flow.insert_task(brief="写周报", status="planning", dims=_dims())
    out = server._twin_impl("task_submit",
                            {"task_id": t["id"], "deliverable_md": "# 周报\n"})
    assert out["ok"]
    assert "换一个需求" in out["guidance"]
    assert "业务口径变更不沉淀" in out["guidance"]
    assert "reflection 未写入" not in out["guidance"]  # 反断言（test_v07x:149 同源）


# ---- T3–T6：toolcheck 检测 ----

def test_t3_hard_positives():
    cases = [
        ' bgColor:"#F2F2F2"',
        " bold:true",
        " textIndent:true",
        "（table-cell.bgColor）",
        "`POST /v1/pages`",
        " indent=3",
        " contentFile",
        " action='help'",
        " color: #F2F2F2",
    ]
    for text in cases:
        hits = toolcheck.scan(text)
        assert hits and hits[0]["level"] == "hard", (text, hits)


def test_t4_soft_positives_beat_hard():
    hits = toolcheck.scan("见 https://joyspace.jd.com/table/x?orgId=1 说明")
    assert len(hits) == 1 and hits[0]["level"] == "soft" and hits[0]["kind"] == "url"
    for text in ("原型给持久链接 zero.jd.com/demo/。",
                 "服务在 127.0.0.1:8000。",
                 "本地 localhost:8000 调试。"):
        hits = toolcheck.scan(text)
        assert hits and hits[0]["level"] == "soft", (text, hits)


def test_t5_negatives():
    negatives = [
        "<!-- src: 1476 -->",
        "交付 .drawio",
        "命名：`PRD｜主题-V1.0.md`",
        "编号用 `1.`",
        "标题如 `### N.x 标题`",
        "head-to-head",
        "memory.catalog",
        "2026-11-27 交付",
        "减字 20%-30%",
        "iOS 与 iPhone 与 macOS",
        "eBay 与 iCloud",
        "模板文件 prd-template.md",
        "GET/POST 都支持",
        "第 3 条共 60 条字符数 10000",
        "截止 deadline=2026-12-01 前提测",       # kv 数字值接日期尾
        "date: 2026-01-01 交付",                 # kv 冒号值接日期尾
        "count= 3条",                            # kv 数字值接数量（带空格）
        "count = 3 条",
    ]
    for text in negatives:
        assert toolcheck.scan(text) == [], (text, toolcheck.scan(text))


def test_t6_comment_exemption_variants_and_no_double_report():
    assert toolcheck.scan("<!-- src: 1476 -->") == []
    assert toolcheck.scan("<!--src:1476-->") == []
    assert toolcheck.scan("<!-- src=1476 -->") == []
    hits = toolcheck.scan('<!-- bgColor:"#F2F2F2" -->')
    assert hits and hits[0]["kind"] == "kv"      # 非归因注释照扫
    line = '规则行 bgColor:"#F2F2F2" 与 textIndent:true'
    hits = toolcheck.scan(line)
    assert [h["kind"] for h in hits] == ["kv", "bool"]   # 两个独立片段各报一次
    assert "camel" not in [h["kind"] for h in hits]      # kv/bool 占位后 camel 不双报
    # 多行归因注释：占位保留换行，后续行号不前移
    multi = '<!-- src: 1\n2 -->\n规则 bgColor:"#FFF"'
    hits = toolcheck.scan(multi)
    assert len(hits) == 1 and hits[0]["line"] == 3
    # snippet 截断到 40 字符（T7 帽的检测侧一半）
    long_url = "见 https://joyspace.jd.com/" + "x" * 60
    hits = toolcheck.scan(long_url)
    assert len(hits[0]["snippet"]) == 40
    # 中文日期值豁免（v1 有、终版保留）；带时刻的时间戳是参数值不豁免
    assert toolcheck.scan("deadline=2026年12月前完成") == []
    hits = toolcheck.scan('a1="2026-01-01 10:00"')
    assert hits and hits[0]["level"] == "hard"
    hits = toolcheck.scan("run_at=2026-12-01T10:00")
    assert hits and hits[0]["level"] == "hard"
    # Python 风格布尔（本仓证据常含调用示例）
    for text in ("compact=True", "with_plan=False", "done=None"):
        hits = toolcheck.scan(text)
        assert hits and hits[0]["kind"] == "bool", (text, hits)


# ---- T7：compile 标注与帽 ----

def test_t7_compile_tool_clause_flags_and_caps():
    _write('样式走块接口 bgColor:"#F2F2F2" 实测有效')          # 硬档
    out = server._twin_impl("compile", {"work_type": "proposal"})
    assert out["ok"]
    assert out["tool_clause_flags"][0]["level"] == "hard"
    assert "tool_clause_flags" in out
    assert "<!-- tool-flag: hard -->" in out["material"]

    # 多行证据：标注在条目最后一行末尾（含内容收尾词之后），不在 - [id] 首行
    _write("首行是写法要求\n次行带参数 bold:true 收尾")
    out2 = server._twin_impl("compile", {"work_type": "proposal"})
    row = [f for f in out2["tool_clause_flags"]
           if f["level"] == "hard"]
    assert row
    assert "收尾<!-- tool-flag: hard -->" in out2["material"]
    # 标注所在行不是 - [id] 首行（多行证据标注在末行）
    idx = out2["material"].find("收尾<!-- tool-flag: hard -->")
    line_start = out2["material"].rfind("\n", 0, idx) + 1
    assert not out2["material"][line_start:idx].startswith("- [")

    # 无命中 → 键缺席、素材包无标注（编译规则文案里的 <!-- tool-flag --> 字样不算）
    _write("交付前用 grep 检查待确认残留为零", wt="work_report")
    out3 = server._twin_impl("compile", {"work_type": "work_report"})
    assert "tool_clause_flags" not in out3
    assert "<!-- tool-flag: " not in out3["material"]

    # 帽：单证据 4 处命中 → snippets 只留 3；13 条命中证据 → flags 12 + flags_total
    four = 'a1:"x"\nb2:true\n`POST /x`\nccDd 标识'
    _write(four)
    out4 = server._twin_impl("compile", {"work_type": "proposal"})
    target = [f for f in out4["tool_clause_flags"] if len(f["snippets"]) >= 3]
    assert target and all(len(f["snippets"]) <= 3 for f in out4["tool_clause_flags"])
    for i in range(13):
        _write(f"批量参数样例 {i}：xy{i}:\"v\"")
    out5 = server._twin_impl("compile", {"work_type": "proposal"})
    assert len(out5["tool_clause_flags"]) == 12   # 帽：只留前 12 条
    assert out5["flags_total"] == 16              # 此前 3 条命中证据 + 13 条批量


# ---- T8/T9：submit 只提醒不拦 ----

def test_t8_submit_hard_residue_warns_but_publishes():
    _write("占位证据一条")
    out = server._twin_impl("submit", {
        "work_type": "proposal",
        "prompt_md": "# Persona Prompt｜提案\n\n- 规则行 bgColor:\"#F2F2F2\"\n",
        "source_memory_ids": [1], "client": "zcode"})
    assert out["ok"]                              # 只提醒，照常落版
    assert any("[验证门:工具条款残留]" in w for w in out.get("warnings", []))
    st = server._twin_impl("status", {})
    assert not st.get("nightly_rejected")     # 未累计（只提醒，非拒绝）


def test_t9_submit_soft_link_warns_but_publishes():
    out = server._twin_impl("submit", {
        "work_type": "proposal",
        "prompt_md": "# Persona Prompt｜提案 v2\n\n- 引用资料给持久链接 zero.jd.com/demo/。\n",
        "source_memory_ids": [1], "client": "zcode"})
    assert out["ok"]
    assert any("[验证门:工具条款提醒]" in w for w in out.get("warnings", []))
    assert not any("[验证门:工具条款残留]" in w for w in out.get("warnings", []))


def test_t9b_submit_scheduled_source_also_warns_but_publishes():
    _write("占位证据一条")
    out = server._twin_impl("submit", {
        "work_type": "proposal",
        "prompt_md": "# Persona Prompt｜提案 v3\n\n- 样式行 bgColor:\"#F2F2F2\"\n",
        "source_memory_ids": [1], "origin": "scheduled", "client": "workbuddy"})
    assert out["ok"]                              # scheduled 来源同样只提醒
    assert any("[验证门:工具条款残留]" in w for w in out.get("warnings", []))
    st = server._twin_impl("status", {})
    assert not st.get("nightly_rejected")


# ---- T10：预算同文与常量 ----

def test_t10_budget_numbers_consistent():
    assert templates.BUDGET_TYPE_CHARS == 10000
    assert templates.BUDGET_TYPE_RULES == 70
    assert templates.BUDGET_AUD_CHARS == 4000       # D1b：画像不放宽
    assert templates.BUDGET_AUD_RULES == 40
    gov = (REPO / "skill-governance" / "SKILL.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "≤10000 字符或 ≤70 条" in gov
    assert "8000 字符或 ≤60 条" not in gov          # 旧数字清干净
    assert "10000 字符/70 条" in readme
    assert "8000 字符/60 条" not in readme
    # 画像两处同文不放宽（D1b）
    assert "≤4000 字符或 ≤40 条" in gov
    assert "画像 4000/40" in readme
    # 检查名短语在 README 不因换行拆断（去空白后断言）
    assert "工具条款残留" in re.sub(r"\s", "", readme)
    assert "工具条款提醒" in re.sub(r"\s", "", readme)
    # 编译规则文案内插常量（改常量即自动跟随）
    type_rules = "\n".join(templates._TYPE_RULES)
    aud_rules = "\n".join(templates._AUDIENCE_RULES)
    assert f"≤{templates.BUDGET_TYPE_CHARS} 字符或 ≤{templates.BUDGET_TYPE_RULES} 条" in type_rules
    assert f"≤{templates.BUDGET_AUD_CHARS} 字符或 ≤{templates.BUDGET_AUD_RULES} 条" in aud_rules


# ---- T11：skill 文件头（版本戳挪到 frontmatter 之后） ----

def test_t11_skill_stamp_after_frontmatter(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "JINGLEAI_ACCOUNT": "tester"}
    r1 = subprocess.run(["make", "install-skill"], cwd=REPO, env=env,
                        capture_output=True, text=True, timeout=60)
    assert r1.returncode == 0, r1.stderr
    stamp = "<!-- mema-twin skill version:"
    hosts = [
        tmp_path / ".zcode/skills/mema-twin/SKILL.md",
        tmp_path / ".jingleAI/accounts/tester/skills/mema-twin/SKILL.md",
        tmp_path / ".workbuddy/skills/mema-twin/SKILL.md",
        tmp_path / ".zcode/skills/mema-twin-governance/SKILL.md",
        tmp_path / ".jingleAI/accounts/tester/skills/mema-twin-governance/SKILL.md",
        tmp_path / ".workbuddy/skills/mema-twin-governance/SKILL.md",
    ]
    for p in hosts:
        text = p.read_text(encoding="utf-8")
        assert text.splitlines()[0] == "---", p            # 六副本首行是 frontmatter
        assert re.search(r"description: \S", text)         # description 键有非空值
        lines = [ln for ln in text.splitlines() if ln.startswith(stamp)]
        if "governance" not in str(p):                     # gov 副本无戳（cp -p）
            assert len(lines) == 1, p                      # 戳恰一行
            idx = text.splitlines().index(lines[0])
            assert idx > 3                                 # 在 frontmatter 之后
    r2 = subprocess.run(["make", "check-skill"], cwd=REPO, env=env,
                        capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0, r2.stdout + r2.stderr
