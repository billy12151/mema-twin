from mema_twin import taxonomy


def test_counts():
    assert len(taxonomy.all_types("work_type")) == 34
    assert len(taxonomy.all_types("audience")) == 10
    assert len(taxonomy.all_types("purpose")) == 9


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
