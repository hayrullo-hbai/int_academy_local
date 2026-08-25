import pytest

from app.profile.mentorship import extract_mentee_emails


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            {"mentees": [{"email": "a@x.io"}, {"user_email": "b@x.io"}]},
            ["a@x.io", "b@x.io"],
        ),
        (
            [{"mentee_email": "c@x.io"}, {"email": "A@X.io"}, {"email": "a@x.io"}],
            ["c@x.io", "a@x.io"],
        ),
        (
            {"results": [{"talent": {"email": "d@x.io"}}]},
            ["d@x.io"],
        ),
        (
            {"mentees": [{"email": "a@x.io"}], "data": [{"email": "b@x.io"}]},
            ["a@x.io"],
        ),
        ({"data": []}, []),
        ({"mentees": [{"first_name": "No Email"}]}, []),
        (None, []),
    ],
)
def test_extract_mentee_emails(payload, expected):
    assert extract_mentee_emails(payload) == expected
