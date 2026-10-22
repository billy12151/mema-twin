JINGLEAI_ACCOUNT ?= dev-user

SKILL_HOSTS := $(HOME)/.zcode/skills/mema-twin \
                $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin \
                $(HOME)/.workbuddy/skills/mema-twin

GOV_HOSTS := $(HOME)/.zcode/skills/mema-twin-governance \
              $(HOME)/.jingleAI/accounts/$(JINGLEAI_ACCOUNT)/skills/mema-twin-governance \
              $(HOME)/.workbuddy/skills/mema-twin-governance

.PHONY: install-skill
install-skill:
	@for d in $(SKILL_HOSTS); do mkdir -p "$$d" && cp -p skill/SKILL.md "$$d/SKILL.md" && echo "→ $$d"; done
	@for d in $(GOV_HOSTS); do mkdir -p "$$d" && cp -p skill-governance/SKILL.md "$$d/SKILL.md" && echo "→ $$d"; done

.PHONY: test
test:
	.venv/bin/python -m pytest -q
