"""Unit tests for student-end heuristic (signaled_end_of_session).

Pure-function coverage of the closing-signal regex set used by the session
loop to terminate on student satisfaction (issue #132).
"""

import pytest

from server.core.student_sim import signaled_end_of_session

# Phrases the student persona uses when satisfied — these MUST terminate.
# Includes the canonical D02 case ("I'm good to stop here for now") plus the
# explicit examples from the issue body.
_POSITIVE_PHRASES = [
    "I'm good to stop here for now.",
    "Thanks, I'm good to stop here for now!",
    "Good to stop here, thanks for the help.",
    "I think I'm good.",
    "I think we're done here.",
    "I'm done.",
    "I'm done with this for now.",
    "Thanks, that's all I needed!",
    "That's all, thanks.",
    "No more questions, thanks for the walkthrough.",
    "OK, I'm all set!",
    "Let's call it a day.",
    "Let me try this on my own.",
    "I'll go run this myself.",
]

# Phrases that mention "stop"/"done"/"good" in non-closing contexts — must
# NOT terminate. Covers the two negatives called out in issue #132 plus a
# few common false-positive shapes.
_NEGATIVE_PHRASES = [
    "Stop and let me think a moment — why does the rolling window matter?",
    "Before we stop, one quick question: what about edge cases?",
    "I'm good with the rolling window, but stuck on the resampling.",
    # Codex P1 finding: "I think I'm good with X" must NOT terminate
    "I think I'm good with the rolling window, but stuck on the resampling.",
    "I think I'm fine with that part, but the resampling still confuses me.",
    # Issue #137 Codex P2 finding: broad "all set" shouldn't match mid-clause
    "I'm all set with the rolling window, but stuck on the resampling.",
    "I think I'm all set with the basics but the slow window still confuses me.",
    "I think I'm done loading the data, anything else I should check.",
    "I'm done loading the data. What's next?",
    "Let me try this on the actual dataset and see what happens.",
    "I'm good at math but new to pandas, so this is helpful.",
]


@pytest.mark.parametrize("text", _POSITIVE_PHRASES)
def test_positive_phrases_terminate(text):
    assert signaled_end_of_session(text), f"Expected terminate: {text!r}"


@pytest.mark.parametrize("text", _NEGATIVE_PHRASES)
def test_negative_phrases_do_not_terminate(text):
    assert not signaled_end_of_session(text), f"Expected continue: {text!r}"


def test_empty_input_returns_false():
    assert not signaled_end_of_session("")
    assert not signaled_end_of_session("   ")
    assert not signaled_end_of_session(None)  # type: ignore[arg-type]


def test_trailing_question_disqualifies_otherwise_matching_text():
    # Without "?": terminates. With trailing "?": does not.
    assert signaled_end_of_session("I think I'm good.")
    assert not signaled_end_of_session(
        "I think I'm good — but one more question, what about Y?"
    )


# Regression for #137: student persona emits curly apostrophes (U+2019)
# in contractions; the regex must still terminate. Verified against the
# real A02 transcript that timed out at 10 student turns instead of
# terminating at the third clear goodbye.
_CURLY_GOODBYES = [
    "Thanks — I’m good for now. I was stressed at first.",
    "Sounds good — thanks, I’m all set for now.",
    "Thanks, I appreciate it — I’m done for now.",
    "Thanks — I’m all set now.",
    "I think I’m done now though, so I’m going to stop here.",
]


@pytest.mark.parametrize("text", _CURLY_GOODBYES)
def test_curly_apostrophe_variants_terminate(text):
    assert signaled_end_of_session(text), f"Expected terminate: {text!r}"
