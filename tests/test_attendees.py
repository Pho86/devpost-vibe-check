from vibe_check.attendees import (
    AttendeeInfo,
    attach_attendees,
    load_attendee_csv,
    merge_attendee_indexes,
)
from vibe_check.models import Project


def test_load_luma_csv(tmp_csv):
    path = tmp_csv(
        "luma.csv",
        "email,approval_status\n"
        "alice@school.edu,approved\n"
        "bob@school.edu,pending\n"
        "cara@school.edu,going\n",
    )
    idx = load_attendee_csv(path, source_label="luma", preset="luma")
    assert idx["alice@school.edu"].accepted is True
    assert idx["cara@school.edu"].accepted is True
    assert idx["bob@school.edu"].accepted is False


def test_load_portal_with_checkin(tmp_csv):
    path = tmp_csv(
        "portal.csv",
        "Email,Current Status,StormHacks 2026 Check In\n"
        "alice@school.edu,Accepted,yes\n"
        "bob@school.edu,Accepted,\n"
        "dan@school.edu,Rejected,yes\n",
    )
    idx = load_attendee_csv(
        path,
        source_label="portal",
        preset="portal",
        checkin_column="StormHacks 2026 Check In",
    )
    assert idx["alice@school.edu"].accepted is True
    assert idx["alice@school.edu"].checked_in is True
    assert idx["bob@school.edu"].accepted is True
    assert idx["bob@school.edu"].checked_in is False
    assert idx["bob@school.edu"].checkin_tracked is True
    assert idx["dan@school.edu"].accepted is False


def test_registrants_presence_means_accepted(tmp_csv):
    path = tmp_csv(
        "reg.csv",
        "Email,Submitted Project?\n"
        "eve@school.edu,Yes\n"
        "frank@school.edu,No\n",
    )
    idx = load_attendee_csv(path, source_label="reg", preset="registrants")
    assert idx["eve@school.edu"].accepted is True
    assert idx["frank@school.edu"].accepted is True


def test_merge_and_attach():
    a = {"alice@x.com": AttendeeInfo(email="alice@x.com", accepted=True)}
    b = {
        "alice@x.com": AttendeeInfo(
            email="alice@x.com",
            checked_in=True,
            checkin_tracked=True,
        ),
        "bob@x.com": AttendeeInfo(email="bob@x.com", accepted=False, checkin_tracked=True),
    }
    merged = merge_attendee_indexes([a, b])
    assert merged["alice@x.com"].accepted is True
    assert merged["alice@x.com"].checked_in is True

    p = Project(
        title="Demo",
        url="https://example.devpost.com/submissions/demo",
        slug="demo",
        team_emails=["alice@x.com", "bob@x.com", "missing@x.com"],
    )
    attach_attendees([p], merged)
    assert p.accepted_members == 1
    assert "bob@x.com" in p.not_accepted_emails
    assert "missing@x.com" in p.accepted_unmatched_emails
    assert p.checked_in_members == 1
    assert "bob@x.com" in p.unchecked_emails
