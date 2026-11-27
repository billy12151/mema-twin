JINGLEAI_ACCOUNT ?= $(shell whoami)

SKILL_HOSTS := $(HOME)/.zcode/skills/mema-twin \
                $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin \
                $(HOME)/.workbuddy/skills/mema-twin

GOV_HOSTS := $(HOME)/.zcode/skills/mema-twin-governance \
              $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin-governance \
              $(HOME)/.workbuddy/skills/mema-twin-governance

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)

# v0.6.0 A7：安装副本带版本戳；check-skill 剔除戳行后 diff——宿主真读的是安装
# 副本，滞后一版就是活冲突（v0.4.8 副本教退役流程的事故先例）。
# v0.7.1：戳挪到 frontmatter（第二个 --- 行）之后——此前戳在首行会顶掉
# frontmatter，宿主读不到 description（ZCode 实测 description 为空）。
# recipe 里 /^---$$/ 必须双 $（make 转义）。
define stamp_skill
	awk -v v='$(VERSION)' '{ print } /^---$$/ { d++; if (d == 2) printf "<!-- mema-twin skill version: %s -->\n", v }' skill/SKILL.md > "$(1)/SKILL.md"
endef

.PHONY: install-skill
install-skill:
	@for d in $(SKILL_HOSTS); do mkdir -p "$$d" && $(call stamp_skill,$$d) && echo "→ $$d (v$(VERSION))"; done
	@for d in $(GOV_HOSTS); do mkdir -p "$$d" && cp -p skill-governance/SKILL.md "$$d/SKILL.md" && echo "→ $$d"; done

# /bin/sh 无 process substitution：副本去戳落临时文件再与 repo 原件 diff。
# v0.7.1 加首行校验：新旧两种戳格式去戳后都能过 diff，只有首行校验能抓「戳退化回首行」
.PHONY: check-skill
check-skill:
	@fail=0; tmp=$$(mktemp); \
	for d in $(SKILL_HOSTS); do \
		if [ ! -f "$$d/SKILL.md" ]; then echo "✗ 缺副本: $$d/SKILL.md"; fail=1; continue; fi; \
		if [ "$$(head -n 1 "$$d/SKILL.md")" != "---" ]; then echo "✗ 首行非 frontmatter: $$d/SKILL.md（重跑 make install-skill）"; fail=1; fi; \
		grep -v '^<!-- mema-twin skill version:' "$$d/SKILL.md" > "$$tmp"; \
		if diff -q skill/SKILL.md "$$tmp" >/dev/null; then echo "✓ $$d"; \
		else echo "✗ 滞后/不一致: $$d/SKILL.md（重跑 make install-skill）"; fail=1; fi; \
	done; \
	for d in $(GOV_HOSTS); do \
		if [ ! -f "$$d/SKILL.md" ]; then echo "✗ 缺副本: $$d/SKILL.md"; fail=1; continue; fi; \
		if [ "$$(head -n 1 "$$d/SKILL.md")" != "---" ]; then echo "✗ 首行非 frontmatter: $$d/SKILL.md"; fail=1; fi; \
		if diff -q skill-governance/SKILL.md "$$d/SKILL.md" >/dev/null 2>&1; then echo "✓ $$d"; \
		else echo "✗ 滞后/不一致: $$d/SKILL.md（重跑 make install-skill）"; fail=1; fi; \
	done; \
	rm -f "$$tmp"; exit $$fail

.PHONY: test
test:
	.venv/bin/python -m pytest -q
