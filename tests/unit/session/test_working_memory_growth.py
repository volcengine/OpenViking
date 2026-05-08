# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Working Memory growth behavior and anti-bloat guards."""

from openviking.session.session import Session


def _wm(
    *,
    current_state: str = "Actively updating Working Memory behavior.",
    key_facts: str = "- Decision 0: keep the current WM shape.",
    files_context: str = "- openviking/session/session.py - WM merge logic.",
    errors: str = "",
    open_issues: str = "",
) -> str:
    sections = {
        "Session Title": "Working Memory Growth Checks",
        "Current State": current_state,
        "Task & Goals": "Understand whether current WM sections can grow without bound.",
        "Key Facts & Decisions": key_facts,
        "Files & Context": files_context,
        "Errors & Corrections": errors,
        "Open Issues": open_issues,
    }
    parts = ["# Working Memory", ""]
    for header, body in sections.items():
        parts.extend([f"## {header}", body, ""])
    return "\n".join(parts).rstrip() + "\n"


def _keep_all() -> dict:
    return {
        "Session Title": {"op": "KEEP"},
        "Current State": {"op": "KEEP"},
        "Task & Goals": {"op": "KEEP"},
        "Key Facts & Decisions": {"op": "KEEP"},
        "Files & Context": {"op": "KEEP"},
        "Errors & Corrections": {"op": "KEEP"},
        "Open Issues": {"op": "KEEP"},
    }


def _section_items(wm: str, header: str) -> list[str]:
    parsed = Session._parse_wm_sections(wm)
    return Session._wm_extract_bullet_items(parsed.get(f"## {header}", ""))


def _section_body(wm: str, header: str) -> str:
    parsed = Session._parse_wm_sections(wm)
    return parsed.get(f"## {header}", "")


# =====================================================================
# Monotonic-growth tests (unchanged behavior for APPEND-driven sections)
# =====================================================================

def test_key_facts_section_grows_monotonically_under_repeated_appends():
    merged = _wm()

    for idx in range(1, 51):
        ops = _keep_all()
        ops["Key Facts & Decisions"] = {
            "op": "APPEND",
            "items": [f"Decision {idx}: keep module_{idx}.py because contract {idx} is stable."],
        }
        merged = Session._merge_wm_sections(merged, ops)

    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) == 51
    assert items[0] == "Decision 0: keep the current WM shape."
    assert items[-1] == "Decision 50: keep module_50.py because contract 50 is stable."


def test_errors_and_corrections_section_grows_with_each_new_error():
    merged = _wm(errors="- Round 0: initial mismatch between prompt and merge logic.")

    for idx in range(1, 41):
        ops = _keep_all()
        ops["Errors & Corrections"] = {
            "op": "APPEND",
            "items": [f"Round {idx}: corrected assumption {idx} after verification."],
        }
        merged = Session._merge_wm_sections(merged, ops)

    items = _section_items(merged, "Errors & Corrections")

    assert len(items) == 41
    assert items[0] == "Round 0: initial mismatch between prompt and merge logic."
    assert items[-1] == "Round 40: corrected assumption 40 after verification."


def test_files_and_context_section_can_keep_growing_with_unique_paths():
    merged = _wm(files_context="- src/file_0.py - initial reference.")

    for idx in range(1, 31):
        ops = _keep_all()
        ops["Files & Context"] = {
            "op": "APPEND",
            "items": [f"src/file_{idx}.py - referenced during round {idx}."],
        }
        merged = Session._merge_wm_sections(merged, ops)

    items = _section_items(merged, "Files & Context")

    assert len(items) == 31
    assert items[0] == "src/file_0.py - initial reference."
    assert items[-1] == "src/file_30.py - referenced during round 30."


# =====================================================================
# Key Facts consolidation guard — Layer 1: bullet-count ratio
# =====================================================================

def test_key_facts_consolidation_rejected_when_trivially_small():
    """A 1-bullet UPDATE for 41 old bullets (2.4%) is rejected.

    The rejected UPDATE's content is checked for genuinely new items.
    Since the consolidation summary is just a restatement, it gets
    salvaged as a new APPEND item (not present verbatim in old content).
    Old items are preserved, and the new summary is appended.
    """
    key_facts = "\n".join(
        f"- Decision {idx}: keep module_{idx}.py because API contract {idx} is stable."
        for idx in range(1, 42)
    )
    old_wm = _wm(key_facts=key_facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": (
            "- Consolidated summary: keep modules 1-41 because their API contracts are stable."
        ),
    }

    merged = Session._merge_wm_sections(old_wm, ops)
    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) == 42
    assert "Decision 1: keep module_1.py because API contract 1 is stable." in items
    assert any("Consolidated summary" in it for it in items)


# =====================================================================
# Key Facts consolidation guard — Layer 2: anchor coverage
# =====================================================================

_RICH_KEY_FACTS = "\n".join([
    "- Caroline adopted a rescue dog named Biscuit on 15 March 2024.",
    "- Melanie's family took 3 camping trips to Yellowstone because the kids love hiking.",
    "- Caroline competed in swimming contest on 10 June 2023, won gold medal.",
    "- Caroline competed in swimming contest on 9 June 2024, brought friends to cheer.",
    "- Sweden trip planned for August 2025 with budget of 5000 dollars.",
    "- Decided to use Python because the team has 4 years experience.",
    "- Marcus is Caroline's brother, lives in Portland.",
    "- Caroline volunteers at the shelter every Saturday morning.",
    "- Adoption agency: Bright Futures, contacted on 2024-01-20.",
    "- Melanie decided to homeschool the kids because local schools are overcrowded.",
    "- Caroline's art exhibition on 22 November 2024 at Gallery One.",
    "- Biscuit has a vet appointment every 6 months at Pawsome Clinic.",
    "- Marcus committed to help with the Sweden trip logistics.",
    "- Caroline chose watercolor over oil painting because of studio ventilation.",
    "- Family reunion planned for December 2025 in Portland.",
    "- Melanie's oldest child starts college in September 2026.",
    "- Caroline resolved to run a half-marathon in Spring 2025.",
    "- Budget for art supplies: 200 dollars per month.",
    "- Caroline's neighbor Jake offered to pet-sit Biscuit during Sweden trip.",
    "- Melanie agreed to share camping gear for the next Yellowstone trip.",
    "- Caroline took a ceramics class starting January 2025.",
    "- Marcus and Caroline share a joint savings account for family events.",
    "- Bright Futures agency requires 3 home visits before approval.",
    "- Caroline prefers morning runs, usually 5 miles along the river trail.",
    "- Melanie's youngest allergic to peanuts, diagnosed at age 2.",
    "- Gallery One charges 500 dollars for exhibition space.",
    "- Caroline decided to switch from acrylic to watercolor in March 2024.",
    "- Portland family home has 4 bedrooms, enough for reunion guests.",
    "- Sweden itinerary: Stockholm 3 days, Gothenburg 2 days, countryside 2 days.",
    "- Caroline's running group meets every Wednesday at 6 AM.",
    "- Melanie committed to volunteer at kids' school fundraiser.",
    "- Jake is a retired teacher, lives next door since 2020.",
    "- Caroline's half-marathon target time: under 2 hours.",
    "- Art exhibition features 12 watercolor paintings.",
    "- Melanie's camping trips always in July because of school schedule.",
    "- Caroline and Marcus talk every Sunday evening by phone.",
    "- Biscuit is a 3 years old golden retriever mix.",
    "- Bright Futures requires background checks completed by February 2025.",
    "- Caroline's studio is in the garage, converted in 2023.",
    "- Melanie drives a minivan because she has 3 kids.",
    "- Family agreed on a $2000 budget cap for the reunion dinner.",
])

_GOOD_CONSOLIDATION = "\n".join([
    "- Caroline adopted rescue dog Biscuit (golden retriever mix, 3 years old) on 15 March 2024; vet at Pawsome Clinic every 6 months. Neighbor Jake (retired teacher, next door since 2020) offered to pet-sit during Sweden trip.",
    "- Caroline regularly competes in swimming contests; most recently on 9 June 2024, won 2 gold medals total.",
    "- Caroline's art: switched from acrylic to watercolor (March 2024) because of studio ventilation; exhibition on 22 November 2024 at Gallery One (12 paintings, 500 dollars space); studio in garage, converted 2023. Budget 200 dollars/month.",
    "- Caroline took ceramics class starting January 2025; resolved to run half-marathon Spring 2025 (target under 2 hours, runs 5 miles mornings along river trail, group meets Wednesdays 6 AM).",
    "- Caroline volunteers at shelter every Saturday morning.",
    "- Marcus is Caroline's brother, lives in Portland; committed to help with Sweden trip; they share joint savings for family events; talk every Sunday evening.",
    "- Melanie's family: 3 camping trips to Yellowstone (always July, because school schedule, kids love hiking); agreed to share gear. Oldest starts college September 2026. Youngest allergic to peanuts (diagnosed age 2). Melanie drives minivan because 3 kids. Decided to homeschool because local schools overcrowded. Committed to school fundraiser volunteer.",
    "- Sweden trip planned August 2025, budget 5000 dollars: Stockholm 3 days, Gothenburg 2 days, countryside 2 days.",
    "- Adoption agency Bright Futures, contacted 2024-01-20: requires 3 home visits and background checks by February 2025.",
    "- Decided to use Python because team has 4 years experience.",
    "- Family reunion December 2025 in Portland (4-bedroom home, enough for guests); agreed $2000 budget cap for reunion dinner.",
])


def test_key_facts_consolidation_accepted_when_anchors_preserved():
    """A 41-to-11 consolidation (26.8%) with high anchor coverage passes."""
    old_wm = _wm(key_facts=_RICH_KEY_FACTS)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": _GOOD_CONSOLIDATION,
    }

    merged = Session._merge_wm_sections(old_wm, ops)
    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) <= 15
    body = _section_body(merged, "Key Facts & Decisions")
    assert "Caroline" in body
    assert "Melanie" in body
    assert "Marcus" in body
    assert "Biscuit" in body
    assert "Yellowstone" in body
    assert "Sweden" in body
    assert "Portland" in body


def test_key_facts_consolidation_rejected_when_anchors_missing():
    """An UPDATE that drops most names/dates/numbers is rejected.

    Old items are preserved; any genuinely new items from the rejected
    UPDATE are salvaged via APPEND.
    """
    old_wm = _wm(key_facts=_RICH_KEY_FACTS)
    vague_items = [
        "- The user has various personal plans and family activities.",
        "- Several trips are being planned for the coming year.",
        "- Art and running are important hobbies.",
        "- Family relationships are strong and supportive.",
        "- Multiple commitments have been made regarding future events.",
        "- The user works with technology and has team preferences.",
        "- Pet care arrangements are in place.",
        "- Educational decisions have been discussed.",
    ]
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": "\n".join(vague_items),
    }

    merged = Session._merge_wm_sections(old_wm, ops)
    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) >= 41
    body = _section_body(merged, "Key Facts & Decisions")
    assert "Caroline adopted a rescue dog named Biscuit" in body


def test_key_facts_consolidation_accepted_at_low_volume_with_anchors():
    """A 41-to-10 consolidation (~24% volume) passes when anchors are kept."""
    old_wm = _wm(key_facts=_RICH_KEY_FACTS)

    consolidated = "\n".join([
        "- Caroline adopted Biscuit (golden retriever, 3 years old) on 15 March 2024; vet every 6 months at Pawsome Clinic.",
        "- Caroline competes in swimming contests regularly; latest 9 June 2024, 2 gold medals total.",
        "- Caroline's art: watercolor (switched March 2024 because ventilation); exhibition 22 November 2024 at Gallery One, 12 paintings, 500 dollars. Studio in garage since 2023. Budget 200 dollars/month. Also took ceramics January 2025.",
        "- Caroline runs half-marathon Spring 2025, target under 2 hours; runs 5 miles mornings, group Wednesdays 6 AM. Volunteers shelter Saturdays.",
        "- Marcus: Caroline's brother, Portland. Joint savings, Sunday calls. Committed to Sweden trip help.",
        "- Melanie: 3 kids, minivan, homeschooling because overcrowded schools. Oldest college September 2026, youngest peanut allergy diagnosed age 2. Camping Yellowstone in July (3 trips, kids love hiking). Fundraiser volunteer.",
        "- Sweden August 2025, 5000 dollars: Stockholm 3 days, Gothenburg 2 days, countryside 2 days. Jake (neighbor since 2020, retired teacher) pet-sits Biscuit.",
        "- Adoption: Bright Futures contacted 2024-01-20; 3 home visits, background checks by February 2025.",
        "- Team decided Python because 4 years experience.",
        "- Family reunion December 2025 Portland, 4 bedrooms, $2000 dinner cap.",
    ])

    ops = _keep_all()
    ops["Key Facts & Decisions"] = {"op": "UPDATE", "content": consolidated}

    merged = Session._merge_wm_sections(old_wm, ops)
    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) == 10
    body = _section_body(merged, "Key Facts & Decisions")
    for name in ["Caroline", "Melanie", "Marcus", "Biscuit", "Jake"]:
        assert name in body
    for date in ["15 March 2024", "2024-01-20", "December 2025"]:
        assert date in body


# =====================================================================
# _extract_lexical_anchors — mixed-case and multi-word entities
# =====================================================================

def test_extract_anchors_catches_mixed_case_names():
    """OpenAI, McCloud, JavaScript etc. should be captured as anchors."""
    text = (
        "- Used OpenAI API for the project.\n"
        "- McCloud reviewed the PR on 2024-03-15.\n"
        "- Rewrote the frontend in JavaScript."
    )
    anchors = Session._extract_lexical_anchors(text)
    assert "openai" in anchors
    assert "mccloud" in anchors
    assert "javascript" in anchors


def test_extract_anchors_catches_standard_proper_nouns():
    """Standard Titlecase names like Caroline, Sweden, Portland."""
    text = "- Caroline traveled to Sweden and visited Portland."
    anchors = Session._extract_lexical_anchors(text)
    assert "caroline" in anchors
    assert "sweden" in anchors
    assert "portland" in anchors


def test_extract_anchors_filters_stopwords():
    """Common English words should be filtered even if capitalized."""
    text = "The And Other Some These Those"
    anchors = Session._extract_lexical_anchors(text)
    assert "the" not in anchors
    assert "other" not in anchors
    assert "some" not in anchors


# =====================================================================
# Salvage: rejected consolidation still preserves new facts (P1 fix)
# =====================================================================

def test_key_facts_rejected_consolidation_salvages_new_facts():
    """When consolidation is rejected (layer1 or layer2), genuinely new
    items from the UPDATE content are APPENDed so current-round facts
    are not silently lost."""
    key_facts = "\n".join(
        f"- Decision {idx}: keep module_{idx}.py because API contract {idx} is stable."
        for idx in range(1, 42)
    )
    old_wm = _wm(key_facts=key_facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": (
            "- Summary: all modules stable.\n"
            "- NEW: Caroline adopted a puppy named Max on 2024-06-15."
        ),
    }

    merged = Session._merge_wm_sections(old_wm, ops)
    items = _section_items(merged, "Key Facts & Decisions")

    assert len(items) >= 42
    assert "Decision 1: keep module_1.py because API contract 1 is stable." in items
    body = _section_body(merged, "Key Facts & Decisions")
    assert "Caroline adopted a puppy named Max" in body


# =====================================================================
# _build_wm_section_reminders
# =====================================================================

def test_build_wm_section_reminders_triggers_above_threshold():
    key_facts = "\n".join(
        f"- Fact {idx}: something important about item {idx}."
        for idx in range(1, 35)
    )
    wm = _wm(key_facts=key_facts)

    reminders = Session._build_wm_section_reminders(wm)

    assert "<section_size_warnings>" in reminders
    assert "Key Facts" in reminders
    assert "34 bullets" in reminders
    assert "MUST be consolidated" in reminders


def test_build_wm_section_reminders_skips_append_only_sections():
    """Errors & Corrections should never get consolidation warnings
    even if oversized, because it's append-only."""
    errors = "\n".join(
        f"- Error {idx}: something went wrong in round {idx}."
        for idx in range(1, 40)
    )
    wm = _wm(errors=errors)

    reminders = Session._build_wm_section_reminders(wm)

    assert "Errors" not in reminders


def test_build_wm_section_reminders_silent_below_threshold():
    key_facts = "\n".join(
        f"- Fact {idx}: something about item {idx}."
        for idx in range(1, 10)
    )
    wm = _wm(key_facts=key_facts)

    reminders = Session._build_wm_section_reminders(wm)

    assert reminders == ""


# =====================================================================
# Wiring test: _generate_archive_summary_async passes reminders
# =====================================================================

def test_wm_section_reminders_consistent_with_prompt_template():
    """Verify _build_wm_section_reminders output matches the format expected
    by ov_wm_v2_update.yaml (XML tags that the Jinja2 template injects)."""
    big_key_facts = "\n".join(
        f"- Fact {idx}: person_{idx} decided thing_{idx} on 2024-01-{idx:02d}."
        for idx in range(1, 35)
    )
    prior_wm = _wm(key_facts=big_key_facts)

    reminders = Session._build_wm_section_reminders(prior_wm)

    assert reminders.startswith("<section_size_warnings>")
    assert reminders.endswith("</section_size_warnings>")
    assert "Key Facts & Decisions" in reminders
    assert "34 bullets" in reminders
    assert "MUST be consolidated" in reminders
    assert f"<={Session._WM_SECTION_BULLET_THRESHOLD} bullets" in reminders
    assert f"<={Session._WM_SECTION_TOKEN_THRESHOLD} tokens" in reminders


# =====================================================================
# Aggregate document size test (adapted for new guard behavior)
# =====================================================================

def test_working_memory_document_size_keeps_increasing_across_rounds():
    merged = _wm(
        key_facts="- Decision 0: keep the current WM shape.",
        files_context="- src/file_0.py - initial reference.",
        errors="- Round 0: initial mismatch between prompt and merge logic.",
    )
    initial_size = len(merged)

    for idx in range(1, 61):
        ops = _keep_all()
        ops["Key Facts & Decisions"] = {
            "op": "APPEND",
            "items": [f"Decision {idx}: keep module_{idx}.py because contract {idx} is stable."],
        }
        ops["Files & Context"] = {
            "op": "APPEND",
            "items": [f"src/file_{idx}.py - referenced during round {idx}."],
        }
        ops["Errors & Corrections"] = {
            "op": "APPEND",
            "items": [f"Round {idx}: corrected assumption {idx} after verification."],
        }
        merged = Session._merge_wm_sections(merged, ops)

    assert len(merged) > initial_size
    kf_items = _section_items(merged, "Key Facts & Decisions")
    assert len(kf_items) == 52, (
        f"Key Facts should stop at 52 (51 facts + 1 consolidation sentinel); "
        f"got {len(kf_items)}"
    )
    assert any("CONSOLIDATION REQUIRED" in it for it in kf_items), (
        "Emergency sentinel must be present when Key Facts exceeds 2x threshold"
    )
    assert len(_section_items(merged, "Files & Context")) == 61
    assert len(_section_items(merged, "Errors & Corrections")) == 61


# =====================================================================
# Anti-bloat guard: focused tests for each path
# =====================================================================

def test_antibloat_normal_append_passes_under_threshold():
    """APPEND with fewer than threshold bullets should pass without throttling."""
    facts = "\n".join(f"- Fact {i}" for i in range(10))
    merged = _wm(key_facts=facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": ["New fact A", "New fact B"],
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert len(items) == 12
    assert "New fact A" in items
    assert "New fact B" in items


def test_antibloat_throttled_append_caps_at_5():
    """APPEND beyond threshold but under 2x should accept at most 5 new items."""
    facts = "\n".join(f"- Fact {i}" for i in range(30))
    merged = _wm(key_facts=facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": [f"Extra {i}" for i in range(10)],
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert len(items) == 35, (
        f"Expected 30 + 5 (capped) = 35; got {len(items)}"
    )


def test_antibloat_throttled_dedup_filters_existing():
    """Throttled APPEND should skip items already present in old content."""
    facts = "\n".join(f"- Fact {i}" for i in range(30))
    merged = _wm(key_facts=facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": ["Fact 0", "Fact 1", "Brand new insight"],
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert "Brand new insight" in items
    assert items.count("Fact 0") == 1


def test_antibloat_emergency_inserts_sentinel_and_stops():
    """APPEND beyond 2x threshold should insert sentinel once, then hard stop."""
    facts = "\n".join(f"- Fact {i}" for i in range(55))
    merged = _wm(key_facts=facts)

    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": ["Emergency fact A"],
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert len(items) == 56, f"Expected 55 + 1 sentinel = 56; got {len(items)}"
    assert any("CONSOLIDATION REQUIRED" in it for it in items)
    assert "Emergency fact A" not in items

    ops2 = _keep_all()
    ops2["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": ["Emergency fact B"],
    }
    merged = Session._merge_wm_sections(merged, ops2)
    items2 = _section_items(merged, "Key Facts & Decisions")
    assert len(items2) == 56, (
        f"Sentinel already present — should stay at 56; got {len(items2)}"
    )
    assert "Emergency fact B" not in items2


def test_antibloat_salvage_bypass_blocked_at_emergency():
    """UPDATE rejected by Layer1/2 → salvage APPEND should be suppressed
    when Key Facts is at emergency level."""
    facts = "\n".join(
        f"- Person_{i} decided thing_{i} on 2024-01-{i % 28 + 1:02d}."
        for i in range(55)
    )
    merged = _wm(key_facts=facts)

    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "UPDATE",
        "content": "- Only one bullet.",
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert len(items) == 55, (
        f"Salvage APPEND should be suppressed at emergency level; got {len(items)}"
    )


def test_antibloat_nonstring_items_handled():
    """Non-string items in APPEND should not crash the guard."""
    facts = "\n".join(f"- Fact {i}" for i in range(30))
    merged = _wm(key_facts=facts)
    ops = _keep_all()
    ops["Key Facts & Decisions"] = {
        "op": "APPEND",
        "items": [123, None, "Valid new fact", True],
    }
    merged = Session._merge_wm_sections(merged, ops)
    items = _section_items(merged, "Key Facts & Decisions")
    assert "Valid new fact" in items
