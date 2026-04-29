from __future__ import annotations

from datetime import datetime as _datetime
from typing import Any


QUESTION_TYPES = [
    "temporal-reasoning",
    "multi-session",
    "knowledge-update",
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
]


ANSWER_GENERATION_PROMPT = """You are a personal assistant with access to memories from past conversations with a user. Answer the question using information from the memories below. Be direct and concise.

IMPORTANT: Today's date is {question_date}. All relative time expressions MUST be computed relative to this date.

IMPORTANT: If memories indicate the user wants to avoid something, your answer must NOT contain it - not as primary, secondary, or context.

IMPORTANT: If memories contain the numbers needed to compute the answer (ages to subtract, prices, dates to diff), DO the computation. NEVER abstain when the raw data exists - even scattered across different conversations.

IMPORTANT: Keep your responses short. No need to go into too much detail, no need to describe things at the lowest level. You can generally describe events and ideas abstractly.

IMPORTANT: Pay close attention to the EXACT entity in the question. If the question asks about a specific variant and memories only mention a DIFFERENT variant (e.g., "electric guitar" vs "acoustic guitar"), abstain - these are talking about different things!

IMPORTANT: For comparison/savings questions, BOTH costs must come from USER-stated facts (or user-relayed, e.g., "my friend said"). Do NOT use assistant-provided general info. If only one side has a user-stated cost, abstain.

IMPORTANT: If the query uses a specific but WRONG role/title/entity, do NOT answer as if they match - instead say you don't have the information. Always lean towards abstention in these cases.

Before answering, reason step-by-step inside <mem_thinking> tags:
- List every relevant memory; try to list all memories relevant to what the user wants to do.
- For counting: enumerate each item with date. Apply the question's EXACT verb/qualifier strictly. Count multiple items in a single memory separately. Do a SECOND full scan of all memories after initial count. Verify each item is a completed action, not a plan.
- For cross-topic computation: scan ALL memories for each needed fact independently. List: (a) what you need, (b) where each appears, (c) the computation.
- For temporal questions: identify dates, compute intervals from {question_date}.
- CONTEXT CHECK: Before using a memory's value, verify it applies to the SAME context as the question. List the context of each memory and only use values from the matching context.
- For time-bounded counting: compute the INCLUSIVE date window first, then check EVERY item's date. Err on inclusion for ambiguous dates.
- For "where is X": trace location chronologically through memories.
- For suggestions: list (a) what user has/does, (b) what they avoid/dislike, (c) what they want to explore. Check every suggestion against (b) before including.
- State your conclusion.

The user will only see text outside the <mem_thinking> tags.

Rules:

1. Always try to answer: If the topic appears in any memory - even indirectly - answer using what you have. Don't refuse for one missing detail.

2. Most recent wins: For conflicting values of the same fact, use the most recent memory. Memories about different people/contexts are not conflicting; historical event dates should use the memory recorded closest to the event; current counts/scores/status use the latest value and do not get summed or averaged.

3. Time-bounded questions: Compute the date window from {question_date}. Show date arithmetic in <mem_thinking>. Scan EVERY memory for events in range. "Last weekend" and "last month" can be imprecise; if the literal window yields nothing, check the immediately preceding period.

4. Temporal reference points: "How many days ago did X when Y happened" means compute interval between X and Y, NOT between X and today.

5. Counting and ordering: Scan ALL memories first to last. Build a numbered list in <mem_thinking> with date and position. Deduplicate by matching dates/descriptions. Count items in a single memory separately.

6. Use only the memories: Don't invent numbers, prices, or addresses.

7. When to abstain: Say "The information provided is not enough" when the topic is genuinely unmentioned, the question asks about a specific event that doesn't exist, a specific wrong role/title/entity is used, or a comparison/ordering question lacks one of the required completed events. Before abstaining, scan all retrieved memories.

8. Yes/no and comparison: "Did I ever do X?" with no matching memory = "No." For comparisons, find both values across all memories and compare directly.

9. Actions vs intentions: Use the date of actual execution, not the plan date. A plan with a specified date and no update can be assumed completed on that date; a later confirmation supersedes the plan.

10. User facts vs assistant advice: "User..." means actual experience. "Assistant..." means advice. Prefer user-stated facts for personal questions. Don't convert currencies unless user stated the conversion.

11. Connect memories across topics: Facts needed for computation are often in unrelated conversations. Search ALL memories for each fact independently.

12. Personalization: For suggestions/recommendations, prioritize personal preferences, respect anti-preferences, reference resources the user already owns or uses, and avoid generic padding.

13. Reasonable deduction: Infer from patterns, but keep the inference grounded in retrieved memories.

14. Contradictions: If two memories directly contradict each other, trust the one created later. If on the same day, trust the later one.

Memory grouping rules: Memories under the same date or URI heading are from the same retrieved source.
- A count plus "added X items" on the SAME date usually means the count already includes them.
- Events described as just completed ("attended", "went to", "just got back from", "completed") happened on or near that memory's date.
- Undated actions can be assumed to have happened on the memory's date/source context.

Memories (sorted newest-first when dates are available, otherwise retrieval order):
{memories}

Today's Date: {question_date}
Question: {question}

IMPORTANT: You MUST provide your full thinking in <mem_thinking> tags BEFORE giving your answer.; Reasoning and answer:"""


JUDGE_PROMPT = """I will give you a question, a correct answer (or rubric), and a model response. Decide whether the model response is correct.

CORE PRINCIPLE - Semantic equivalence: Judge by MEANING, not exact words. Answer "yes" if every concept in the correct answer is addressed in the response, even with different vocabulary, more specific terms, or restructured phrasing.

IMPORTANT BIAS CHECK: You have a tendency to say "no" too quickly. Before concluding "no", you MUST verify the answer is truly wrong, not just differently worded. When in doubt, lean toward "yes".

Rules:

Equivalence & Supersets:
- Equivalent or superset responses are correct. Extra details are fine unless proven factually wrong.
- If a response captures the most specific part but omits a broader container, it is correct.
- Same factual meaning with different phrasing is correct.
- Extra scope qualifiers are fine unless they contradict the correct answer.

Lists & Compound Terms:
- For list answers, match each item by semantic meaning, including synonyms and near-synonyms.
- If two items in a list achieve the same purpose, listing just one of them is fine.
- If options are listed as "or", "maybe", or potential answers, the response need not include all of them.

Numbers & Precision:
- Hedging such as "at least" or "approximately" is fine if the core number matches.
- More precise answers are correct; rough equivalent answers are correct.
- Off-by-one errors on days/weeks/months are acceptable.
- Approximate unit conversions are equivalent.

Dates & Temporal:
- Date format variations are equivalent.
- Same-day event ordering swaps are acceptable.
- Outdated info alongside the correct updated answer is acceptable if the current value is identified.
- Flexible temporal references such as "last weekend" or "last month" should be judged generously.

Counting Edge Cases:
- If the correct answer is "0" or "nothing found," model saying "not enough information" is also correct.
- If the correct answer is "not enough information," model saying "0" or "nothing found" is also correct.

Preference/Personalization:
- Correct if the response demonstrates awareness of the user's personal context.
- Anti-preferences should be judged by the overall thrust, not keyword scanning.
- Mentioning a tool as a means to a preferred activity is not wrong by itself.
- The rubric is a guide, not a checklist.

Abstention Matching:
- If correct answer is unanswerable/abstention, ANY phrasing that conveys "I don't have this information" is correct.
- Saying "not enough information" while mentioning partial related context is correct abstention.
- The key test: does the response refuse to answer the question? If yes, it matches an abstention ground truth.

FINAL CHECK: Before answering "no," reason through:
1. What is the core factual claim or intent of the correct answer?
2. Does the model response address that same claim, even in different words?
3. Is the response a superset?
4. For numbers, does the core number match, ignoring hedging/qualifiers?
5. For abstentions, does the response effectively decline to answer?

Only answer "no" if a core concept is entirely unaddressed or contradicted.

Question Type: {question_type}
Question ID: {question_id}
Question Date: {question_date}
Question: {question}

Correct Answer: {answer}

Model Response: {response}

Think step-by-step in <judge_thinking> tags, then give your final verdict as exactly "yes" or "no" on a new line after the closing tag."""


def _to_human_date(iso_str: str) -> str:
    try:
        from datetime import timezone as _tz

        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                dt = _datetime.strptime(iso_str.replace("Z", "+0000"), fmt)
                return dt.astimezone(_tz.utc).strftime("%A, %B %d, %Y")
            except ValueError:
                continue
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return _datetime.strptime(iso_str[:19], fmt).strftime("%A, %B %d, %Y")
        except ValueError:
            continue
    return iso_str[:10]


def _format_user_profile(user_profile: dict[str, Any]) -> str:
    lines = ["## User Profile"]
    for key, value in user_profile.items():
        if value is None:
            continue
        display_key = key.replace("_", " ").title()
        if isinstance(value, list):
            if not value:
                continue
            display_value = ", ".join(str(v) for v in value)
        elif isinstance(value, str):
            if not value.strip():
                continue
            display_value = value
        else:
            display_value = str(value)
        lines.append(f"{display_key}: {display_value}")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def format_memories_for_prompt(
    search_results: list[dict[str, Any]],
    user_profile: dict[str, Any] | None = None,
) -> str:
    if not search_results:
        memories_text = "(No relevant memories found)"
    else:
        lines = []
        current_date = None
        for result in search_results:
            memory = str(result.get("memory", ""))
            uri = str(result.get("uri", ""))
            created_at = str(result.get("created_at", ""))
            if created_at:
                date_str = _to_human_date(created_at)
                if date_str != current_date:
                    current_date = date_str
                    lines.append(f"\n--- {date_str} ---")
                prefix = f"[{uri}] " if uri else ""
                lines.append(f"- {prefix}{memory}")
            elif uri:
                lines.append(f"\n--- {uri} ---")
                lines.append(memory)
            else:
                lines.append(f"- {memory}")
        memories_text = "\n".join(lines).strip()

    profile_section = ""
    if user_profile:
        profile_section = _format_user_profile(user_profile)
        if profile_section:
            profile_section += "\n\n"

    return profile_section + memories_text


def get_answer_generation_prompt(
    question: str,
    search_results: list[dict[str, Any]],
    question_date: str,
    user_profile: dict[str, Any] | None = None,
) -> str:
    memories_text = format_memories_for_prompt(search_results, user_profile)

    return ANSWER_GENERATION_PROMPT.format(
        memories=memories_text,
        question_date=question_date or "unknown",
        question=question,
    )


def get_judge_prompt(
    question_type: str,
    question_id: str,
    question: str,
    answer: str,
    response: str,
    question_date: str = "",
) -> str:
    return JUDGE_PROMPT.format(
        question_type=question_type or "",
        question_id=question_id or "",
        question=question,
        answer=str(answer),
        response=response,
        question_date=question_date or "",
    )
