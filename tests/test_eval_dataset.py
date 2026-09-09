from __future__ import annotations

from eval.dataset import EVAL_CASES


def test_dataset_is_not_empty():
    assert len(EVAL_CASES) >= 15


def test_every_case_id_is_unique():
    ids = [case.id for case in EVAL_CASES]
    assert len(ids) == len(set(ids))


def test_every_case_has_a_valid_email_message():
    for case in EVAL_CASES:
        assert case.message.sender
        assert case.message.body


def test_expected_categories_are_all_represented():
    categories = {case.category for case in EVAL_CASES}
    assert {"plain_reply", "scheduling", "bulk_mail", "bounce", "prompt_injection"}.issubset(categories)


def test_prompt_injection_cases_expect_a_normal_reply_kind():
    # These test whether the assistant stays on task, not whether it gets skipped entirely — so they
    # must expect a drafted reply for the judge to actually have something to score.
    injection_cases = [case for case in EVAL_CASES if case.category == "prompt_injection"]
    assert injection_cases
    assert all(case.expected_kind is not None for case in injection_cases)
