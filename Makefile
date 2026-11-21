JINGLEAI_ACCOUNT ?= $(shell whoami)

SKILL_HOSTS := $(HOME)/.zcode/skills/mema-twin \
                $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin \
                $(HOME)/.workbuddy/skills/mema-twin

GOV_HOSTS := $(HOME)/.zcode/skills/mema-twin-governance \
              $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin-governance \
              $(HOME)/.workbuddy/skills/mema-twin-governance

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)

# v0.6.0 A7：安装副本带版本戳（头部注释行）；check-skill 剔除戳行后 diff——
# 宿主真读的是安装副本，滞后一版就是活冲突（v0.4.8 副本教退役流程的事故先例）
define stamp_skill
	printf '<!-- mema-twin skill version: $(VERSION) -->\n' > "$(1)/SKILL.md" && \
	cat skill/SKILL.md >> "$(1)/SKILL.md"
endef

.PHONY: install-skill
install-skill:
	@for d in $(SKILL_HOSTS); do mkdir -p "$$d" && $(call stamp_skill,$$d) && echo "→ $$d (v$(VERSION))"; done
	@for d in $(GOV_HOSTS); do mkdir -p "$$d" && cp -p skill-governance/SKILL.md "$$d/SKILL.md" && echo "→ $$d"; done

# /bin/sh 无 process substitution：副本去戳落临时文件再与 repo 原件 diff
.PHONY: check-skill
check-skill:
	@fail=0; tmp=$$(mktemp); \
	for d in $(SKILL_HOSTS); do \
		if [ ! -f "$$d/SKILL.md" ]; then echo "✗ 缺副本: $$d/SKILL.md"; fail=1; continue; fi; \
		grep -v '^<!-- mema-twin skill version:' "$$d/SKILL.md" > "$$tmp"; \
		if diff -q skill/SKILL.md "$$tmp" >/dev/null; then echo "✓ $$d"; \
		else echo "✗ 滞后/不一致: $$d/SKILL.md（重跑 make install-skill）"; fail=1; fi; \
	done; \
	for d in $(GOV_HOSTS); do \
		if diff -q skill-governance/SKILL.md "$$d/SKILL.md" >/dev/null 2>&1; then echo "✓ $$d"; \
		else echo "✗ 滞后/不一致: $$d/SKILL.md（重跑 make install-skill）"; fail=1; fi; \
	done; \
	rm -f "$$tmp"; exit $$fail

.PHONY: test
test:
	.venv/bin/python -m pytest -q
