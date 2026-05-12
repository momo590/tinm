import json
import re


JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator. Given a set of expected facts and an "
    "agent's responses, you assess whether the agent CONFIDENTLY identified "
    "the correct values for the right topic. A response that hedges, asks for "
    "clarification, lists alternatives, or mixes in unrelated topics scores LOW "
    "even if the correct value appears somewhere in the text. "
    "Return JSON only — no commentary, no markdown fences."
)


_NUM_COMMA = re.compile(r"(\d),(\d)")
_WORD_RE = re.compile(r"\w+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "as", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "that", "these", "those",
}


def _normalize(text: str) -> str:
    # Lowercase + strip thousands separators ("120,000" -> "120000")
    return _NUM_COMMA.sub(r"\1\2", text.lower())


def _content_tokens(text: str) -> set[str]:
    """Significant-word token set: lowercased, comma-stripped, stopwords removed."""
    return set(_WORD_RE.findall(_normalize(text))) - _STOPWORDS


def fact_extraction_score(
    responses: list[str],
    facts: dict[str, str],
    fact_key_groups: list[list[str]] | None = None,
) -> dict[str, bool]:
    """Symbolic check: does each fact value appear in the response that targets it?

    If `fact_key_groups` is provided (one inner list per response naming the
    fact keys that response is meant to answer), we match each fact only
    against its designated response. This prevents over-matching on multi-hop
    tasks where the bridge entity's answer happens to appear in the response
    to an earlier subquestion.

    If `fact_key_groups` is None, fall back to searching the joined text.
    """
    def matches(value: str, text: str) -> bool:
        # Match if the (normalized) value appears as a substring,
        # OR all content tokens of the value are present in the text's content tokens.
        if _normalize(value) in _normalize(text):
            return True
        value_tokens = _content_tokens(value)
        if not value_tokens:
            return False
        text_tokens = _content_tokens(text)
        return value_tokens.issubset(text_tokens)

    if fact_key_groups is None:
        full_text = " ".join(responses)
        return {k: matches(v, full_text) for k, v in facts.items()}

    found = {k: False for k in facts}
    for response, keys in zip(responses, fact_key_groups):
        for key in keys:
            if key not in facts:
                continue
            found[key] = matches(facts[key], response)
    return found


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def llm_judge(
    client,
    model: str,
    task_goal: str,
    facts: dict[str, str],
    responses: list[str],
) -> tuple[float, dict[str, bool]]:
    """LLM-as-judge for factual coverage. Returns (quality, per-fact bools).

    Quality is the model's overall 0-1 score; the per-fact booleans give
    a finer-grained view useful for ablations.
    """
    facts_str = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    response_str = "\n---\n".join(responses)
    fact_keys = list(facts.keys())

    example_pairs = ", ".join(f'{json.dumps(k)}: true' for k in fact_keys)
    example_json = f'{{"facts_found": {{{example_pairs}}}, "quality": 0.85}}'

    user_msg = (
        f"Task goal: {task_goal}\n\n"
        f"Expected facts:\n{facts_str}\n\n"
        f"Agent responses (concatenated, separated by '---'):\n{response_str}\n\n"
        f"Apply STRICT criteria. For each expected fact, output:\n"
        f"  - true  if the agent confidently and unambiguously identified the "
        f"correct value AS belonging to this task's topic\n"
        f"  - false if the value is wrong, missing, hedged with alternatives, "
        f"or mixed with wrong-topic information that forces the user to disambiguate\n\n"
        f"Then assign an overall quality score in [0, 1]:\n"
        f"  - 1.0 = all facts confidently identified for the correct topic\n"
        f"  - around 0.5 = correct values present but confused with alternatives, "
        f"asks for clarification, or mentions unrelated topics\n"
        f"  - 0.0 = wrong or missing entirely\n\n"
        f"A response containing phrases like 'Could you clarify', 'There are "
        f"multiple possibilities', 'Project A is X and Project B is Y, which one "
        f"do you mean' should score LOW even if the correct value appears.\n\n"
        f"Return strict JSON, e.g.:\n{example_json}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=512,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    payload = _extract_json(text)

    quality = float(payload.get("quality", 0.0))
    raw_facts_found = payload.get("facts_found", {}) or {}
    facts_found = {k: bool(raw_facts_found.get(k, False)) for k in fact_keys}
    return quality, facts_found


def combined_quality(
    client,
    judge_model: str,
    task,
    responses: list[str],
) -> tuple[float, dict[str, bool], dict[str, bool], float]:
    """Hybrid: 50% symbolic fact coverage + 50% LLM-judge quality."""
    fact_key_groups = getattr(task, "subquestion_fact_keys", None)
    symbolic = fact_extraction_score(responses, task.facts_to_find, fact_key_groups)
    symbolic_score = sum(symbolic.values()) / max(len(symbolic), 1)

    judge_score, judge_found = llm_judge(
        client, judge_model, task.goal_text, task.facts_to_find, responses
    )
    combined = 0.5 * symbolic_score + 0.5 * judge_score
    return combined, symbolic, judge_found, judge_score
