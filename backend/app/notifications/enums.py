"""Vocabulary for the in-app notification feed (the bell-icon dropdown).

Kept small and generic on purpose: this module doesn't know or care what
"profile" or "academy" mean, only that something happened and a user should
see it. New categories get added here as new features start creating
notifications — they don't need their own delivery mechanism.
"""

import enum


class NotificationCategory(str, enum.Enum):
    GENERAL = "general"
    PROFILE = "profile"


NOTIFICATION_CATEGORY_VALUES = {c.value for c in NotificationCategory}
