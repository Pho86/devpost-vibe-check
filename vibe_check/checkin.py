"""Back-compat re-exports — prefer vibe_check.attendees."""

from .attendees import attach_checkin, load_checkin_index

__all__ = ["attach_checkin", "load_checkin_index"]
