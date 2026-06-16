"""
LoCoMo answer-generation prompt aligned with the mem0 benchmark runner.
"""

from datetime import datetime as _datetime

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}

CATEGORIES_TO_EVALUATE = [1, 2, 3, 4]


ANSWER_GENERATION_PROMPT = """You are answering a question using retrieved memories from past conversations. Follow these reasoning steps IN ORDER.

## Step 1: SCAN ALL MEMORIES
Read EVERY memory below from first to last. For each one that contains information relevant to the question, note it. Do NOT stop after finding the first relevant memory - important details are often scattered across many memories, including ones far down the list. Give equal weight to ALL memories regardless of position - a memory near the end is just as likely to contain the answer as one near the beginning. In these memories, "User" refers to the main person whose memories these are.

## Step 2: ENTITY VERIFICATION
Confirm each relevant memory is about the correct person/entity. If the question asks "What does Person A like?" and a memory says "Person B likes X", do NOT use that memory to answer about Person A. In two-person conversations, both speakers' actions are relevant - if the question asks about person A and a memory attributes an action to person B (the other speaker), that information is still valid evidence from their shared conversations, but always check the attribution is correct.

## Step 3: COMBINE AND CROSS-REFERENCE
- COMBINE facts from multiple memories about the same topic. If one memory says "won first place" and another says "performed a piece titled X," those describe the same event - connect them.
- For listing/counting questions, extract EVERY distinct item from ALL memories. A single memory may contain multiple items. Think about what CATEGORIES of answers the question could have, then re-scan specifically for each category.
- For counting questions ("how many times", "how many X"), enumerate each distinct instance explicitly with its date or context BEFORE giving a final count. Do not estimate - list them out, then count the list.
- DECOMPOSE complex sentences: "an immersive X with Y, enjoys Z" contains multiple distinct facts. Each could be the answer.
- Connect related facts across memories: if one says "nearby lake" and another says "Lake Tahoe is great for kayaking", the nearby lake IS Lake Tahoe. If one says "bought X in Paris", infer the country is France.

## Step 4: SELECT THE BEST ANSWER
- Do NOT assume the highest-ranked memory is correct. Multiple memories may describe different events for the same topic. Compare each candidate's relevance to the SPECIFIC question, not its retrieval score. A lower-ranked memory that directly answers the question beats a higher-ranked one that is only tangentially related.
- ALWAYS choose the MOST SPECIFIC detail available. A proper name, title, or number beats a generic description. Rate each candidate as HIGH specificity (name, title, number, specific activity) or LOW (generic description), and prefer HIGH.
- Report what someone actually DID, not what was offered or available to them. "Has not tried X yet" means X was NOT done - disqualify it. "Joined X" or "has done X" means it WAS done - prefer it.
- When multiple memories repeat the same generic fact, that repetition does NOT make it more correct than a single memory with a more specific answer.
- Photos depict what was IN the photo, not facts about someone's daily life. Prefer direct statements over photo descriptions for inferences.
- Re-read the question carefully before answering. If it asks "what aspect/type/kind", answer with the specific aspect. If it asks "what did they discover they both enjoy", answer with the specific thing, not the setting.

## Step 5: TEMPORAL GROUNDING
These conversations took place around {reference_date}. All events occurred in 2022-2024.
- Calculate time relative to this date, NOT today. Never output 2025 or 2026.
- Use dates explicitly stated in memory text. Do not invent or estimate dates.
- When a question asks what someone "shared" or "mentioned" on a date, that date is when they TALKED about it - look for events shortly BEFORE that date.
- For "how long" questions, find the start and end dates explicitly, then compute the duration. Do not guess.
- TEMPORAL DISAMBIGUATION: When you find MULTIPLE instances of similar events at different dates, enumerate them all with their dates before picking. If the question uses past tense + "the" -> select the instance closest to (and before) the reference date. If future tense ("plans to", "going to") -> select the earliest planned date. NEVER default to the first-mentioned or highest-scored instance - the DATE determines the answer.

## Step 6: INCLUSION CHECK (for lists and counts)
If you found items during reasoning that you're tempted to exclude from your answer - STOP. Include them unless you have STRONG evidence they are wrong. The most common mistake is finding relevant items but then dropping them due to overly strict filtering. More items is better than fewer when there is supporting evidence.
- For counting: after enumerating, re-verify each item. Check for duplicates (same event described differently) and ensure you haven't missed items from memories late in the list.
- The question assumes something happened. Find WHAT happened, don't say nothing happened.

## Step 7: COMMIT AND ANSWER
Give a direct, specific answer. NEVER say "not specified", "not mentioned", "no record", or "the memories don't say" - if ANY memory contains relevant information, give the best answer from available evidence. No hedging, no caveats. If the question asks for a list, include ALL items found. NEVER return an empty answer when relevant memories exist.
- NEVER generate specific names, titles, places, or dates that do not appear in any memory above. If no memory contains the specific detail the question asks for, answer with what the memories DO contain rather than guessing.
- For open-domain/opinion questions ("Would X do Y?", "Is X considered Z?"):
  * Follow the DIRECT causal reasoning in the memories. Do NOT construct elaborate counter-arguments.
  * "Would X still do Y without Z?" - If memories show X does Y BECAUSE of Z, then without Z, answer "likely no."
  * "Would X do Y again soon?" - If the most recent attempt involved a bad experience (accident, scare, trauma), answer "likely no." A recent negative experience outweighs historical positive patterns.
  * For trait questions ("Is X considered Z?"): weigh ALL evidence including symbolic/indirect references. If there is SOME but not strong evidence, answer with a qualified degree ("somewhat") rather than flat "no."

# Instructions

## Misc

1. Make reasonable deductions based on your memories. Memory shows store with a lot of working people -> store employs a lot of people
2. If a memory describes something recognizable (e.g., "romantic drama about memory and relationships"), you may name it (e.g., "Eternal Sunshine of the Spotless Mind").
3. Use domain knowledge to connect facts: a game exclusive to one platform implies ownership of that platform. An unnamed company deal can be linked to a previously expressed brand preference.

{memories}

Question: {question}

Work through Steps 1-7, then give your final answer after "ANSWER:".
"""


ANSWERER_MEMORY_LIMIT = 200


def _to_human_date(iso_str: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return _datetime.strptime(iso_str[:26].rstrip("Z"), fmt.replace("%z", "")).strftime(
                "%A, %B %d, %Y"
            )
        except ValueError:
            continue
    return iso_str[:10]


def get_answer_generation_prompt(
    question: str,
    search_results: list,
    reference_date: str | None = None,
) -> str:
    if reference_date is None:
        reference_date = "2023"

    if not search_results:
        memories_text = "(No relevant memories found)"
    else:
        top_results = search_results[:ANSWERER_MEMORY_LIMIT]
        sorted_results = sorted(top_results, key=lambda x: x.get("created_at", ""))
        lines = [
            "The following memories are presented in chronological order (oldest to newest).",
            "",
        ]
        for result in sorted_results:
            memory = result.get("memory", "")
            created_at = result.get("created_at", "")
            if created_at:
                lines.append(f"({_to_human_date(created_at)}) {memory}")
            else:
                lines.append(f"(unknown date) {memory}")
        memories_text = "\n".join(lines)

    return ANSWER_GENERATION_PROMPT.format(
        memories=memories_text,
        question=question,
        reference_date=reference_date,
    )


JUDGE_SYSTEM_PROMPT = (
    "You are evaluating conversational AI memory recall. "
    "Return JSON only with the format requested."
)

_EVIDENCE_CHUNK = """
## Evidence (actual conversation messages containing the answer)
{evidence_context}
"""

_EVIDENCE_RULE = """
5. **EVIDENCE SUPPORTS ANSWER**: If the evidence corroborates the generated answer, mark CORRECT — even when the generated answer diverges from the gold answer. The gold answer may be wrong or oversimplified; if the generated answer provides a more accurate or better-supported conclusion based on the evidence, that is acceptable. Use evidence only to ACCEPT answers, never to reject them more strictly.
"""

_EVIDENCE_WRONG_CLAUSE = " AND is not supported by evidence"

_JUDGE_TEMPLATE = """Label the generated answer as CORRECT or WRONG.
{evidence_section}
## Rules

1. **PARTIAL CREDIT**: If the generated answer includes AT LEAST ONE correct item from the gold answer's list, mark CORRECT. Getting 1 out of 2, 2 out of 4, etc. is always acceptable. Only mark WRONG if NONE of the gold answer items appear.

2. **PARAPHRASES COUNT**: Same concept in different words is CORRECT. "Chocolate raspberry tart" = "chocolate cake with raspberries". "Shelter meal service" = "volunteering at a homeless shelter". Emotions and sentiments in the same positive/negative family count as paraphrases: "proud" = "fulfilled" = "accomplished"; "huge success" = "relieved" = "thrilled" (all express positive achievement). Judge semantic meaning, not exact wording.

3. **EXTRA DETAIL IS FINE**: A longer answer that includes the gold answer's key facts plus additional information is CORRECT. Never penalize for being more detailed or specific. If the generated answer adds extra descriptive details beyond the gold answer while still referencing the same core entity or concept, mark CORRECT.

4. **DATE TOLERANCE**: Dates within 14 days of each other are CORRECT. Durations within 50% are CORRECT (e.g., "5 months" matches "six months"; "19 days" matches "two weeks"). Relative dates ("few days before November") match specific dates in the same window. A specific date (e.g., "February 2020") that is consistent with a vague reference (e.g., "a few years ago" relative to 2023) is CORRECT. Converting "last year" to the actual year (e.g., "2022" when conversations are in 2023) is CORRECT.
{evidence_rule}
5. **SEMANTIC OVERLAP**: Judge whether the generated answer addresses the same topic and captures the core idea of the gold answer. Different wording, phrasing, or level of detail should not result in WRONG if the underlying concept matches. For EMOTIONS and FEELINGS questions, answers expressing sentiments in the same valence (positive/negative) about the same event are CORRECT — do not require the exact same emotion word.

6. **SAME REFERENT**: If the generated answer mentions or references the same named entity, character, person, or concept as the gold answer, mark CORRECT — even if the generated answer provides a different physical description or includes additional details. The key question is: does the generated answer identify the same core entity? If yes, it is CORRECT.

7. **FOCUS ON KNOWLEDGE, NOT WORDING**: The goal is to assess whether the system recalled the right fact. Minor differences in specificity, phrasing, or scope should not result in WRONG. Only mark WRONG when the generated answer demonstrates a genuinely different or incorrect understanding.

## ONLY mark WRONG if:
- The generated answer contains ZERO correct items from the gold answer{evidence_wrong_clause}
- The answer addresses a completely different topic

## Question
Question: {{question}}
Gold answer: {{answer}}
Generated answer: {{response}}

Return JSON with "reasoning" (one sentence) and "label" (CORRECT or WRONG). Do NOT include both labels."""


def _build_judge_prompt(evidence_context: str | None = None) -> str:
    if evidence_context:
        prompt = _JUDGE_TEMPLATE.format(
            evidence_section=_EVIDENCE_CHUNK.format(evidence_context=evidence_context),
            evidence_rule=_EVIDENCE_RULE,
            evidence_wrong_clause=_EVIDENCE_WRONG_CLAUSE,
        )
        prompt = prompt.replace("\n5. **SEMANTIC OVERLAP", "\n6. **SEMANTIC OVERLAP")
        prompt = prompt.replace("\n6. **SAME REFERENT", "\n7. **SAME REFERENT")
        prompt = prompt.replace("\n7. **FOCUS ON KNOWLEDGE", "\n8. **FOCUS ON KNOWLEDGE")
        return prompt

    return _JUDGE_TEMPLATE.format(
        evidence_section="",
        evidence_rule="",
        evidence_wrong_clause="",
    )


JUDGE_PROMPT = _build_judge_prompt(evidence_context=None)


_STRICT_JUDGE_TEMPLATE = """Label the generated answer as CORRECT or WRONG.
{evidence_section}
## Strict Rules

1. **MATCH THE GOLD ANSWER**: Judge the generated answer against the gold answer. The generated answer must contain the key fact(s) requested by the question. Do not mark CORRECT merely because it discusses the same topic.

2. **LISTS REQUIRE SUBSTANTIAL COVERAGE**: If the gold answer contains multiple items, the generated answer must include all or nearly all essential items. Missing a major item is WRONG. A single correct item from a multi-item answer is not enough.

3. **QUESTION-SPECIFIC FACTS REQUIRED**: The answer must match the exact attribute, action, object, person, place, count, or time asked by the question. Mentioning the same person/entity is insufficient if the stated fact is different.

4. **DATES AND TEMPORAL RELATIONS ARE STRICT**: For questions asking when, before/after, most recent, duration, or count over time, the generated answer must preserve the correct temporal relation. Exact dates should match unless the gold answer itself is vague. Do not allow broad date windows for specific-date answers.

5. **COUNTS MUST MATCH**: For counting questions, the generated answer must give the correct count and must not include extra unsupported instances.

6. **PARAPHRASES COUNT, BUT ONLY IF MEANING MATCHES**: Different wording is acceptable only when it preserves the same core meaning. Broader, narrower, or merely related statements are WRONG.

7. **EXTRA DETAILS ARE ALLOWED ONLY IF THE CORE ANSWER IS CORRECT**: Additional details do not hurt if the requested answer is correct, but extra details cannot compensate for a missing or wrong core answer. If extra details contradict the gold answer, mark WRONG.

8. **NO GOLD OVERRIDE**: Treat the gold answer as the grading target. Evidence may clarify wording or context, but do not replace the gold answer with a different answer unless the generated answer also matches the gold answer's key fact(s).

## Mark CORRECT only if:
- The generated answer directly answers the question, AND
- It contains the essential gold answer fact(s), AND
- Any dates, counts, entities, and temporal relations required by the question are correct.

## Mark WRONG if:
- The answer is missing a key gold fact.
- The answer gives a different date, count, person, place, action, object, or temporal relation.
- The answer is only topically related.
- The answer is too vague to verify against the gold answer.

## Question
Question: {{question}}
Gold answer: {{answer}}
Generated answer: {{response}}

Return JSON with "reasoning" (one sentence) and "label" (CORRECT or WRONG). Do NOT include both labels."""


def _build_strict_judge_prompt(evidence_context: str | None = None) -> str:
    evidence_section = (
        _EVIDENCE_CHUNK.format(evidence_context=evidence_context) if evidence_context else ""
    )
    return _STRICT_JUDGE_TEMPLATE.format(evidence_section=evidence_section)


def get_judge_prompt(
    category: int,
    question: str,
    answer: str,
    response: str,
) -> str:
    del category
    return JUDGE_PROMPT.format(question=question, answer=answer, response=response)


def get_judge_prompt_with_evidence(
    category: int,
    question: str,
    answer: str,
    response: str,
    evidence_context: str,
) -> str:
    del category
    prompt = _build_judge_prompt(evidence_context=evidence_context)
    return prompt.format(question=question, answer=answer, response=response)


def get_strict_judge_prompt(
    category: int,
    question: str,
    answer: str,
    response: str,
) -> str:
    del category
    prompt = _build_strict_judge_prompt(evidence_context=None)
    return prompt.format(question=question, answer=answer, response=response)


def get_strict_judge_prompt_with_evidence(
    category: int,
    question: str,
    answer: str,
    response: str,
    evidence_context: str,
) -> str:
    del category
    prompt = _build_strict_judge_prompt(evidence_context=evidence_context)
    return prompt.format(question=question, answer=answer, response=response)


def preprocess_answer(category: int, answer: str) -> str:
    if category == 3 and ";" in answer:
        return answer.split(";")[0].strip()
    return answer
