"""Who may act on onboarding stages.

Onboarding is owned exclusively by the Academy Manager and HR roles (plus the
superadmin). No other role may access or edit any part of the onboarding process.
"""

STAFF_ROLES = {"academy-manager", "hr", "superadmin"}
MANAGEMENT_ROLES = {"academy-manager", "hr", "superadmin"}
# Only the academy manager may assign interviewers in-app (HR may not); the
# superadmin passes via is_superuser.
ASSIGNER_ROLES = {"academy-manager", "superadmin"}


def _roles(user) -> set[str]:
    return set(user.role_names) | ({user.role_name} if user.role_name else set())


def is_staff_member(user) -> bool:
    return bool(user) and (user.is_superuser or bool(_roles(user) & STAFF_ROLES))


def is_management(user) -> bool:
    return bool(user) and (user.is_superuser or bool(_roles(user) & MANAGEMENT_ROLES))


def can_assign(user) -> bool:
    return bool(user) and (user.is_superuser or bool(_roles(user) & ASSIGNER_ROLES))


# Which roles may conduct which interview. The call and culture-fit are HR's;
# the tech interview is the academy manager's. (Superadmin may always be assigned.)
INTERVIEW_ROLE_CAPS = {
    "intro_call": {"hr", "superadmin"},
    "culture_fit": {"hr", "superadmin"},
    "tech_interview": {"academy-manager", "superadmin"},
}


def can_conduct(user, key: str) -> bool:
    if not user:
        return False
    if user.is_superuser:
        return True
    return bool(_roles(user) & INTERVIEW_ROLE_CAPS.get(key, set()))
