"""Semantic checks for a small, versioned corpus, not a general language validator.

Production validation proves shape and verbatim provenance. These annotations
add repeatable checks for known deliverables, qualifier binding and restrictions;
passing this corpus does not establish coverage of arbitrary requests.
These checks measure semantic adequacy, not compliance with every soft prompt
instruction about quote granularity. Kind, strength and scope remain mandatory.
"""
import json
from pathlib import Path
import re

from backend.research_requirements import validate_saved_assessment


CORPUS_PATH = Path(__file__).with_name("fixtures") / "research_requirements_quality.json"
# These vocabularies intentionally cover only the versioned fixtures. They detect
# broader spans that accidentally include the other requested qualifier too.
_QUALIFIER_ATOMS = {
    "period": re.compile(r"\bFY\d{4}\b"),
    "currency": re.compile(r"\b(?:USD|SEK|DKK|EUR|kr)\b"),
    "unit": re.compile(r"\b(?:millions|billions)\b"),
}


def load_cases(path=None):
    corpus = json.loads(Path(path or CORPUS_PATH).read_text(encoding="utf-8"))
    if corpus.get("version") != 1:
        raise AssertionError("Unsupported requirements quality corpus version.")
    cases = corpus["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise AssertionError("Quality cases need nonempty, unique IDs.")
    return cases


def _contains(value, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", value))


def _qualifier_matches(value, expected, field, partial):
    if value is None:
        return partial or expected is None
    if expected is None or not _contains(value, expected):
        return False
    atoms = _QUALIFIER_ATOMS[field]
    return set(atoms.findall(value)) <= set(atoms.findall(expected))


def _assignment(candidates):
    """Match each row to a distinct candidate, including overlapping spans."""
    def visit(index, used, chosen):
        if index == len(candidates):
            return chosen
        for candidate in candidates[index]:
            if candidate not in used:
                result = visit(index + 1, used | {candidate}, [*chosen, candidate])
                if result is not None:
                    return result
        return None

    return visit(0, set(), [])


def check_assessment(case, assessment):
    """Assert canonical validity and the known case's semantic expectations.

    IDs and quote granularity are unconstrained: metric/qualifier/restriction
    phrases may sit inside larger original spans. Ready cases require exactly the
    annotated deliverables. Clarification may retain any subset, with unresolved
    references or null qualifiers, while still asking the required questions.
    Other material questions need an explicit optional kind/target annotation;
    arbitrary extra questions must not receive a semantic-quality pass.
    A case may explicitly permit clarification of one ambiguous subject instead
    of ready, without relaxing any restriction or allowing unrelated questions.
    """
    label = case["id"]

    def require(condition, message):
        if not condition:
            raise AssertionError(f"{label}: {message}")

    try:
        value = validate_saved_assessment(assessment, case["request"])
    except (ValueError, TypeError, KeyError) as error:
        raise AssertionError(f"{label}: invalid canonical assessment: {error}") from error
    require(value["completion_criterion"] == case["request"], "original completion criterion changed")
    require(all(item["mandatory"] is True for item in value["items"]), "every item must remain mandatory")
    allowed_subject = case.get("allow_subject_clarification")
    subject_alternative = (value["decision"] == "clarify" and case["decision"] == "ready"
                           and bool(allowed_subject))
    require(value["decision"] == case["decision"] or subject_alternative,
            f"expected decision {case['decision']!r}, got {value['decision']!r}")
    if subject_alternative:
        require(all(question["kind"] == "subject" and _contains(question["quote"], allowed_subject)
                    for question in value["clarifications"]),
                f"only subject clarification of {allowed_subject!r} is permitted as an alternative to ready")

    expected_items, actual_items = case["items"], value["items"]
    partial = value["decision"] == "clarify"
    require(len(actual_items) <= len(expected_items) if partial else len(actual_items) == len(expected_items),
            f"expected {'at most ' if partial else ''}{len(expected_items)} deliverables, got {len(actual_items)}")
    subjects = {subject["id"]: subject for subject in value["subjects"]}
    names = {item["subject"] for item in expected_items}

    def subject_matches(subject, expected):
        return (_contains(subject["name"], expected)
                and not any(_contains(subject["name"], other) for other in names - {expected}))

    for subject in subjects.values():
        require(any(subject_matches(subject, name) for name in names),
                f"unexpected subject {subject['name']!r}")

    def item_matches(actual, expected):
        references = actual["subject_ids"]
        return (
            _contains(actual["criterion"], expected["criterion"])
            and (bool(references) or partial)
            and all(subject_matches(subjects[identity], expected["subject"]) for identity in references)
            and all(_qualifier_matches(actual[field], expected.get(field), field, partial)
                    for field in _QUALIFIER_ATOMS)
            and actual["freshness"] is None
        )

    if partial:
        candidates = [[index for index, expected in enumerate(expected_items) if item_matches(actual, expected)]
                      for actual in actual_items]
        assignment = _assignment(candidates)
        require(assignment is not None,
                f"partial items do not preserve the requested subject/metric/qualifiers; candidates={candidates}")
        item_ids = {expected_index: actual_items[index]["id"] for index, expected_index in enumerate(assignment)}
    else:
        candidates = [[index for index, actual in enumerate(actual_items) if item_matches(actual, expected)]
                      for expected in expected_items]
        assignment = _assignment(candidates)
        require(assignment is not None,
                f"no distinct item assignment preserves subject/metric/qualifiers for {expected_items!r}; "
                f"candidates={candidates}")
        item_ids = {index: actual_items[actual_index]["id"] for index, actual_index in enumerate(assignment)}

    actual_constraints, expected_constraints = value["constraints"], case["constraints"]
    require(len(actual_constraints) == len(expected_constraints),
            f"expected {len(expected_constraints)} separate constraints, got {len(actual_constraints)}")
    all_item_ids = {item["id"] for item in actual_items}

    def constraint_matches(actual, expected):
        expected_scope = {item_ids[index] for index in expected["applies_to"] if index in item_ids}
        return (
            actual["kind"] == expected["kind"] and actual["strength"] == expected["strength"]
            and _contains(actual["criterion"], expected["quote"])
            and (set(actual["item_ids"]) if actual["item_ids"] else all_item_ids) == expected_scope
        )

    candidates = [[index for index, actual in enumerate(actual_constraints) if constraint_matches(actual, expected)]
                  for expected in expected_constraints]
    require(_assignment(candidates) is not None,
            f"constraint kind/strength/quote/scope mismatch for {expected_constraints!r}; candidates={candidates}")

    questions = value["clarifications"]

    def question_matches(actual, expected):
        return actual["kind"] == expected["kind"] and _contains(actual["quote"], expected["quote"])

    permitted_questions = [*case["clarifications"], *case.get("optional_clarifications", [])]
    if subject_alternative:
        permitted_questions.append({"kind": "subject", "quote": allowed_subject})
    require(all(any(question_matches(actual, expected) for expected in permitted_questions)
                for actual in questions),
            f"unannotated clarification kind/target; permitted={permitted_questions!r}")
    candidates = [[index for index, actual in enumerate(questions) if question_matches(actual, expected)]
                  for expected in case["clarifications"]]
    require(_assignment(candidates) is not None,
            f"missing required material clarification {case['clarifications']!r}; candidates={candidates}")
