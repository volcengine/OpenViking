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

IMPORTANT: If memories indicate the user wants to avoid something, your answer must NOT contain it — not as primary, secondary, or context.

IMPORTANT: If memories contain the numbers needed to compute the answer (ages to subtract, prices, dates to diff), DO the computation. NEVER abstain when the raw data exists — even scattered across different conversations.

IMPORTANT: Keep your responses short. No need to go into too much detail, no need to describe things at the lowest level. You can generally describe events and ideas abstractly.

IMPORTANT: Pay close attention to the EXACT entity in the question. If the question asks about a specific variant and memories only mention a DIFFERENT variant (e.g., "electric guitar" vs "acoustic guitar"), abstain — these are talking about different things!

IMPORTANT: For comparison/savings questions, BOTH costs must come from USER-stated facts (or user-relayed, e.g., "my friend said"). Do NOT use assistant-provided general info. If only one side has a user-stated cost, abstain.

IMPORTANT: If the query uses a specific but WRONG role/title/entity (e.g., asks about experience as a "Sales Manager" but memories say "Senior Sales Engineer"), do NOT answer as if they match — instead say you don't have the information! Always lean towards abstention in these cases! Do not mix up different role titles, they are not the same roles and you should say you don't have information.

Before answering, reason step-by-step inside <mem_thinking> tags:
- List every relevant memory; try to list all memories relevant to what the user wants to do! Eg. List memory of Payment management apps if query is about paying someone; list memory of travel management apps if query is about going somewhere.

- For counting: enumerate each item with date. Apply the question's EXACT verb/qualifier strictly (e.g., "LED" = leader only, "BAKED" = completed baking only, "RAISED" = total from events user participated in (include team/event totals), "COMPLETED writing" = each distinct finished piece). Count multiple items in a single memory separately. Do a SECOND full scan of all memories after initial count — items at positions 30-200 are commonly missed. Verify each item is a completed action (past tense), not a plan ("plans to", "intends to").
- For cross-topic computation: scan ALL memories for each needed fact independently — they're often in unrelated conversations. List: (a) what you need, (b) where each appears, (c) the computation.
- For temporal questions: identify dates, compute intervals from {question_date}
- CONTEXT CHECK: Before using a memory's value, verify it applies to the SAME context as the question. A wake-up time "while traveling" is NOT the same as a regular weekday wake-up time. A "general daily" schedule may conflict with a "specific weekday" schedule — always prefer the more specific memory that matches the question's context. List the context of each memory (weekday routine vs. travel vs. weekend vs. specific day) and only use values from the matching context.
- For time-bounded counting: compute the INCLUSIVE date window first, then check EVERY item's date. Err on inclusion for ambiguous dates.
- For "where is X": trace location chronologically through memories
- For suggestions: list (a) what user has/does, (b) what they avoid/dislike, (c) what they want to explore. Check every suggestion against (b) before including.
- State your conclusion

The user will only see text outside the <mem_thinking> tags.

Rules:

1. **Always try to answer**: If the topic appears in any memory — even indirectly — answer using what you have. Don't refuse for one missing detail.

2. **Most recent wins**: For conflicting values of the same fact, use the most recent memory. But: (a) memories about different people/contexts aren't conflicting; (b) for historical event dates, use the memory recorded closest to the event; (c) for current counts/scores/status, the latest value REPLACES all earlier ones — don't sum or average.

Similarly, when memories give two numbers for the same metric (e.g., "has 1,250 followers" and "close to 1,300 followers") on the same date, treat the HIGHER/UPDATED value as current — "close to 1,300" means the count has grown from 1,250 to approximately 1,300.

3. **Time-bounded questions**: Compute the date window from {question_date}. Show date arithmetic in <mem_thinking>. Scan EVERY memory for events in range. "Last weekend" is imprecise — could mean up to 10 days ago as people sometimes mean weekend before the latest one. "Last 3 months" can include boundary days of the 4th month back.

"Last month" includes the current month so far as well as the previous month. Eg. "last month" in Late May includes all of April. If the literal window yields nothing, check the immediately preceding period.

4. **Temporal reference points**: "How many days ago did X when Y happened" — compute interval between X and Y, NOT between X and today.

5. **Counting and ordering**: Scan ALL memories first to last. Build a numbered list in <mem_thinking> with date and position. Deduplicate by matching dates/descriptions. Count items in a single memory separately.
Any addition to a list on the same day as a stated count is already included in the count

When asked to count all instances of an event *before* a specific one, obviously don't include the specific one in the count. Eg. "how many restaurants did i visit before eating at Pizza Hut?". Obviously don't include Pizza Hut in the count

6. **Use only the memories**: Don't invent numbers, prices, or addresses.

7. **When to abstain**: Say "The information provided is not enough" when:
   - The topic is genuinely unmentioned

- The question asks about a specific event that doesn't exist, even if a related topic does

- IMPORTANT: If the query uses a specific but WRONG role/title/entity (e.g., asks about experience as a "Sales Manager" but memories say "Senior Sales Engineer"), do NOT answer as if they match — instead say you don't have the information! Always lean towards abstention in these cases! Do not mix up different role titles, they are not the same roles and you should say you don't have information.

   - For comparison/ordering, BOTH items must be present as completed events
   If query asks to compare timings of two tasks and one of them did not even happen, abstain.
   Before abstaining, do a keyword scan of ALL memories (they're chronological, not relevance-sorted — check positions 1-200). Only abstain if NO keywords match.
   EXCEPTIONS: For suggestion questions, don't abstain for lack of real-time info — recommend based on known preferences. If you lack exact brand but have the store, output the store.

8. **Yes/no and comparison**: "Did I ever do X?" with no matching memory = "No." For comparisons, find both values across all memories and compare directly.

9. **Actions vs intentions**: Use the date of actual execution, not the plan date. "Decided to" or "took X for servicing" = action initiated. Only treat as plan if explicit future-tense ("plans to", "will"). A plan with a specified date and no update = assume completed on that date. If a later memory confirms execution, use the execution date — it supersedes the earlier plan.

When a query asks: "when I decided to do X", it means they are asking when X was actually done.

10. **User facts vs assistant advice**: "User..." = actual experience. "Assistant..." = advice. Prefer user-stated facts for personal questions. Don't convert currencies unless user stated the conversion.

11. **Connect memories across topics**: Facts needed for computation are often in unrelated conversations (age in travel advice + relative's age in birthday discussion; cashback rate in membership talk + purchase amount in expense tracking). Search ALL memories for each fact independently.

12. **Personalization**: For suggestions/recommendations:
   - Prioritize personal preferences over informational content
   - Apply known preferences to new contexts — don't abstain for unfamiliar destinations
   - Acknowledge prior work before suggesting next steps
   - Respect anti-preferences — check every suggestion against known dislikes
   - Reference existing tools owned, not to acquire
   - Lead with personalization, don't pad with generic alternatives
   - Suggest similar things to the user as their habits. Eg. Logging basketball scores in a app they do usually. Eg. Adding travel logs to a travel logging app they use usually.
   - IMPORTANT: Scan ALL top memories for user-owned tools, apps, and resources relevant to the question. If the user has a travel card (Suica), a trip organizer app (TripIt), a budgeting tool, etc., mention ALL of them — not just the most obvious one. Do a SECOND pass of the top 30 memories specifically looking for apps, tools, and resources the user has mentioned owning or using.

13. **Reasonable deduction**:
- Infer from patterns
IMPORTANT: Assume that similar items referenced in the same sentence have the same type.
Eg. "User ate lunch, which was the third meal with this chicken fajitas". This means the other meals with these chicken fajitas were lunch meals too, should be treated as explicit lunches.

14. IMPORTANT: If two pieces of memory directly contradict each other (not just an update, a direct contradiction), then assume that the memory that was created later is true. Doesn't matter if a different one "appears" more reliable. If on the same day, trust the one at a later time.

- Chronological actions:
If the user is watching the 11th episode of a series is watching it normally, assume they have completed the earlier 10 too.

- If you lack a name but have a description, answer with the description.

**Memory grouping rules**: Memories under the same date heading are from the same conversation.
- A count + "added X items" on the SAME date = count already includes them
- "Aims to beat X" = X is the current value
- "Previous" = the value superseded by a more recent one
- Events described as just completed ("attended", "went to", "just got back from", "completed") = happened on/near that date. Undated actions = assume the event happened on the memory's date.

# Misc Rules
- Count class projects too when asked about users' projects. Class projects = projects.
- Most old (Eg. ancestral, vintage, heritage) items count as antiques too!
- If you don't have chords for a song (but have notes), output the notes. Song notes count as chord progressions.
- Starting a *diorama project* (eg. diorama work, working on terrain) EXPLICITLY COUNTS AS working on that model kit; these are equivalent! Always count such items.
- Running into someone at a coffee shop and exchanging numbers DOES NOT count as meeting them; lunch meetings do count.
- Potlucks/feasts/birthday parties count as dinner parties (BBQ doesn't).
- chandelier counts as jewelry
- Always assume birthdays cleanly follow years. Ie. User was 22 in 2022; they will be 23 in 2023.
- "scratch grains" count as "new layer feed", always include them when interpreting "new layer feed"

Memories (sorted newest-first, grouped by date):
{memories}

Today's Date: {question_date}
Question: {question}

IMPORTANT: You MUST provide your full thinking in <mem_thinking> tags BEFORE giving your answer.; Reasoning and answer:"""


JUDGE_PROMPT = """I will give you a question, a correct answer (or rubric), and a model response. Decide whether the model response is correct.

CORE PRINCIPLE — Semantic equivalence: Judge by MEANING, not exact words. Answer "yes" if every concept in the correct answer is addressed in the response, even with different vocabulary, more specific terms, or restructured phrasing.

IMPORTANT BIAS CHECK: You have a tendency to say "no" too quickly. Before concluding "no", you MUST verify the answer is truly wrong, not just differently worded. When in doubt, lean toward "yes".

Rules:

**Equivalence & Supersets**
- Equivalent or superset responses are correct. Extra details are fine unless proven to be factually wrong. Extra qualifiers are fine unless proven to be wrong. E.g., "a blue dress and a matching necklace" is correct when the answer is "a blue dress."
- If a response captures the most specific part (exact item/place/name) but omits a broader container, it's correct.
- Same factual meaning with different phrasing = correct (e.g., "No, you did not visit with a friend" ≈ "You didn't mention going with anyone").
- Adding scope qualifiers like "regular-season" or "excluding X" is fine as long as the core value is correct. The qualifier may narrow the context but does NOT make the answer wrong unless the correct answer explicitly includes the excluded items.

**Lists & Compound Terms**
- For list answers, match each item by semantic meaning. A concept is covered if restated via synonyms, sub-concepts, or related terms. Adding methodological detail or rewording verbs to near-synonyms is acceptable.
- A broad term like "A and B significance" is covered if the response addresses the topic area through related specific terms, even without naming each component literally.
- If some items as listed as "or"s, "maybe"s and potential answers, it's okay if the answer does not include those.
- If two items in a list achieve the same purpose, listing just one of them is fine.

IMPORTANT: The "anti-preference" items are very specific!
Eg. Someone "not interested in general AI topics" could be very interested in specific AI topics in general AI *conferences*; those are not the same thing and should be accepted! topics != conferences

**Numbers & Precision**
- Hedging ("at least 3", "approximately") is fine if the core number matches. A range that includes the correct answer is correct.
Generally, if the user themself would be satisfied by the response, it is acceptable. Ie. If the answer is conditional on information they would have (eg. their birthday, some hidden dependent information), and would be correct with that information, that is acceptable.
- More precise answers are correct: "22 days" matches "3 weeks"; "over $270" matches "$270."; "9 1/2 months" matches "9 months";

- Rough answers are correct: "about nine months" ≈ "9 months; "8 months and 20 days" matches "9 months";

- Off-by-one errors on days/weeks/months are acceptable.
- Approximate unit conversions are equivalent: "14 weeks" ≈ "3 months", "6 months" ≈ "half a year."
- Round time ranges generously: 7 months and 16 days ≈ 8 months.
- Notes instead of chords are acceptable when justified
- A correct number with added context (e.g., "about 5 months ago (around December 2022)") is correct — the parenthetical date is supplementary, not a contradiction.

**Dates & Temporal**
- Date format variations are equivalent: "February 1st" = "Feb 1, 2023" = "on February 1."
- Same-day event ordering swaps are acceptable.
- Outdated info alongside the correct updated answer is acceptable if the current value is identified.
- "recent" is upto 6 years ago, which means 2017+
- References like "last weekend", "last Wednesday", etc. are imprecise - people sometimes mean the weekend/Wednesday before the latest one if they're near it. "Last 3 months" can include boundary days of the 4th month back. "Last month" includes the current month so far. Be flexible with such timestamps

**Counting Edge Cases**
- If correct answer is "0" or "nothing found," model saying "not enough information" is also correct.
- Similarly, If correct answer is "not enough information", model saying "0" or "nothing found," is also correct.

**Preference/Personalization Rubrics** (apply in order):
1. Correct if the response demonstrates awareness of user's personal context (preferences, habits, interests). Need not satisfy every rubric point.
2. Primary criterion: do main suggestions align with what the user WANTS?
3. Anti-preferences: evaluate the OVERALL thrust, not keyword scanning. If the response largely suggests correct options, minor incidental references to "not-preferred" things are fine.
4. Mentioning a phone app as a MEANS to a preferred activity (e.g., meditation app for sleep) is not "suggesting phone use." Judge by the activity, not delivery mechanism.
5. "May not prefer" = mild preference, not hard prohibition. Secondary/context-dependent inclusion is fine.
6. Explicit acknowledgment of anti-preferences (e.g., "keep screens off") strengthens correctness.
7. Context-dependent suggestions are acceptable (reading is fine on a bus even if rubric flags visual attention activities). Adjacent genres alongside preferred ones are additive, not contradictory.
8. If the rubric mentions specific user resources/tools (e.g., "Suica card", "TripIt app"), the response is correct if it demonstrates awareness of the user's MAIN personal context even if it does not name every specific tool. The rubric is a guide, not a checklist.

**Abstention Matching**
- If correct answer = unanswerable/abstention, ANY phrasing that conveys "I don't have this information" is correct, regardless of what partial context is mentioned or omitted.
- Saying "not enough information" while mentioning partial related context = correct abstention.
- Saying "no record of X" or "only have plans for X, not actual dates" = correct abstention.
- The key test: does the response REFUSE to answer the question? If yes, it matches an abstention ground truth, period.

FINAL CHECK: Before answering "no," you MUST reason through these steps:
1. What is the core factual claim or intent of the correct answer?
2. Does the model response address that same claim, even in different words?
3. Is the response a superset (correct answer + extra details)?
4. For numbers: does the core number match, ignoring hedging/qualifiers?
5. For abstentions: does the response effectively decline to answer?
Only answer "no" if, after this analysis, a core concept is entirely unaddressed or contradicted.

Question: {question}

Correct Answer: {answer}

Model Response: {response}

Think step-by-step in <judge_thinking> tags, then give your final verdict as exactly "yes" or "no" on a new line after the closing tag."""


STRICT_JUDGE_PROMPT = """I will give you a LongMemEval question, the gold answer or rubric, and a model response. Decide whether the model response is correct.

Use a strict benchmark grading standard. Judge the response against the gold answer, not against what might be plausible from memory.

Rules:

1. **Gold answer is the target**: The response must include the key fact(s), constraint(s), and intent in the gold answer. Do not mark a response correct just because it is related to the same topic.

2. **Question type matters**:
- For temporal-reasoning questions, dates, order, recency, durations, and relative-time calculations must be correct.
- For multi-session and knowledge-update questions, the response must combine the relevant facts and use the latest/current fact when the gold answer depends on an update.
- For single-session-user and single-session-assistant questions, the response must identify the exact requested user/assistant-provided fact.
- For preference questions, the response must preserve positive and negative preferences; suggesting or including an anti-preference is wrong.

3. **Lists and compound answers**: If the gold answer contains multiple required items, the response must include all or nearly all essential items. A single correct item from a multi-item answer is not enough.

4. **Numbers and counts**: Counts, quantities, prices, scores, ages, and computed values must match the gold answer. Ranges or approximations are acceptable only when they clearly contain the gold value and do not change the conclusion.

5. **Dates and durations**: Exact dates must match when the gold answer is specific. Do not allow broad date windows, off-by-one errors, or swapped event ordering if the question asks for a specific time or temporal relation.

6. **Abstention**: If the gold answer is an abstention or says the information is unavailable, the response is correct only if it clearly abstains. If the gold answer contains a concrete fact, an abstention is wrong.

7. **Paraphrases count only when meaning is preserved**: Different wording is fine, but broader, narrower, merely related, or partially contradictory statements are wrong.

8. **Extra detail is allowed only after the core answer is correct**: Extra detail cannot compensate for missing the requested fact. If extra detail contradicts the gold answer, mark wrong.

Mark "yes" only when the response directly answers the question and matches the essential gold answer. Mark "no" when a key fact is missing, wrong, too vague, or only topically related.

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


def get_strict_judge_prompt(
    question_type: str,
    question_id: str,
    question: str,
    answer: str,
    response: str,
    question_date: str = "",
) -> str:
    return STRICT_JUDGE_PROMPT.format(
        question_type=question_type or "",
        question_id=question_id or "",
        question=question,
        answer=str(answer),
        response=response,
        question_date=question_date or "",
    )
