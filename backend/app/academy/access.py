"""Who may view/use vs. author Academy content (problems / exams / Data Lab).

Access is restricted to the talent-journey learner tiers (school, softlanding,
foundation, talent — see identity.enums.LEARNER_ROLES) plus the local
"examiner" role, which grades/administers content without being a learner.
Academy admins (academy-manager, superadmin) always have access too, since
they author and need to preview the content.

Authoring (create/update/delete problems, exams, datasets) is open to academy
admins AND examiners — an examiner's whole job is building/grading this content.
"""

from app.identity.enums import LEARNER_ROLES

ADMIN_ROLES = {"academy-manager", "superadmin"}
EXAMINER_ROLES = {"examiner"}
CONTENT_ADMIN_ROLES = ADMIN_ROLES | EXAMINER_ROLES
ACCESS_ROLES = LEARNER_ROLES | EXAMINER_ROLES | ADMIN_ROLES


def _roles(user) -> set[str]:
    return set(user.role_names) | ({user.role_name} if user.role_name else set())


def is_academy_admin(user) -> bool:
    """Superadmin, academy-manager, or examiner — anyone who may author content."""
    return bool(user) and (
        user.is_superuser or bool(_roles(user) & CONTENT_ADMIN_ROLES)
    )


def is_examiner(user) -> bool:
    return bool(user) and bool(_roles(user) & EXAMINER_ROLES)


def can_access_academy(user) -> bool:
    return bool(user) and (user.is_superuser or bool(_roles(user) & ACCESS_ROLES))
