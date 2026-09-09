from mema_twin import taxonomy


def test_counts():
    assert len(taxonomy.all_types("work_type")) == 33
    assert len(taxonomy.all_types("audience")) == 9
    assert len(taxonomy.all_types("purpose")) == 8


def test_no_other_bucket():
    """v0.3.8 归一门：无 other 杂项桶——真长尾走 canonicalize 立新码。"""
    for kind in taxonomy.KINDS:
        assert taxonomy.by_code(kind, "other") is None
        assert taxonomy.match_exact(kind, "其他") is None
        assert taxonomy.match_exact(kind, "其它") is None


def test_self_aliases_after_cleanup():
    """v0.3.8 C：删「私人」留「本人」。"""
    t = taxonomy.by_code("audience", "self")
    assert "本人" in t.aliases
    assert "私人" not in t.aliases
    assert taxonomy.match_exact("audience", "本人").code == "self"
    assert taxonomy.match_exact("audience", "私人") is None


def test_codes_unique_per_kind():
    for kind in taxonomy.KINDS:
        codes = [t.code for t in taxonomy.all_types(kind)]
        assert len(codes) == len(set(codes)), kind


def test_alias_collision_free_within_kind():
    for kind in taxonomy.KINDS:
        seen: dict[str, str] = {}
        for t in taxonomy.all_types(kind):
            for key in (t.code, t.zh, t.en, *t.aliases):
                kk = key.strip().casefold()
                owner = seen.setdefault(kk, t.code)
                assert owner == t.code, \
                    f"{kind} 别名冲突: {kk} 同时属于 {t.code} 与 {owner}"


def test_match_exact():
    assert taxonomy.match_exact("work_type", "周报").code == "work_report"
    assert taxonomy.match_exact("work_type", "PPT").code == "presentation"
    assert taxonomy.match_exact("work_type", "PRD").code == "product_doc"
    assert taxonomy.match_exact("work_type", "标书").code == "bid_document"
    assert taxonomy.match_exact("audience", "甲方").code == "external_client"
    assert taxonomy.match_exact("purpose", "述职答辩").code == "review_defense"
    assert taxonomy.match_exact("work_type", "  PPT  ").code == "presentation"
    assert taxonomy.match_exact("work_type", "不存在的类型") is None


def test_no_cross_kind_leak():
    assert taxonomy.match_exact("audience", "周报") is None
    assert taxonomy.match_exact("purpose", "PPT") is None
