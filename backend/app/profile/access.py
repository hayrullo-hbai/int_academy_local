"""Who may read, edit and verify profile items.

Derived from the permissions matrix in the User Profile spec (§16) and the
field-ownership table (§6), not from the ticket summaries:

* Add/Edit skills, projects, external accounts → the owner, Academy Manager, Admin.
* Verify skills / projects / account ownership → Mentor, Academy Manager, Admin.
* Control public visibility → Academy Manager and Admin *only*. The spec is
  emphatic that no other role, including HR, may override this, and that the
  profile owner never controls public display (§10, §12).
"""

from app.profile.enums import Visibility  # , ReviewState

MENTOR_ROLES = {"mentor"}
HR_ROLES = {"hr"}
ACADEMY_MANAGER_ROLES = {"academy-manager"}
ADMIN_ROLES = {"superadmin"}

# Spec §16 "Verify skills" / "Verify projects": Mentor, Academy Manager, Admin.
VERIFIER_ROLES = MENTOR_ROLES | ACADEMY_MANAGER_ROLES | ADMIN_ROLES
# Spec §16 "Control public visibility (section-based)": Academy Manager, Admin.
CURATOR_ROLES = ACADEMY_MANAGER_ROLES | ADMIN_ROLES


def _roles(user) -> set[str]:
    if not user:
        return set()
    return set(user.role_names) | ({user.role_name} if user.role_name else set())


def _has(user, roles: set[str]) -> bool:
    return bool(user) and (user.is_superuser or bool(_roles(user) & roles))


def is_curator(user) -> bool:
    """Academy Manager / Admin — the only roles that may publish or curate."""
    return _has(user, CURATOR_ROLES)


def is_admin(user) -> bool:
    """Superadmin only — sees and can action every verification submission
    (the fallback reviewer when a mentor is away or offboarded)."""
    return _has(user, ADMIN_ROLES)


# def is_mentor(user) -> bool:
#     return _has(user, MENTOR_ROLES)


# def is_hr(user) -> bool:
#     return _has(user, HR_ROLES)


def can_verify(user) -> bool:
    return _has(user, VERIFIER_ROLES)


def is_owner(user, target_user_id) -> bool:
    return bool(user) and str(user.id) == str(target_user_id)


def has_any_role(user) -> bool:
    """Whether ``user`` holds at least one role, superusers always counting.

    Local accounts start roleless mid-onboarding (docs/roles.md: "school" is
    the entry tier, granted once HR/Academy Manager completes onboarding) —
    until then they shouldn't be treated as an active member who can edit
    their own profile.
    """
    return bool(user) and (user.is_superuser or bool(user.role_names))


def can_edit_items(user, target_user_id) -> bool:
    """Spec §6: the owner edits their own claims; the Academy Manager (and
    Admin) may override them. An owner with no role yet — still
    mid-onboarding — can't edit until a role is assigned."""
    if is_owner(user, target_user_id):
        return has_any_role(user)
    return is_curator(user)


def can_share_profile(user) -> bool:
    """Publish a profile / curate its sections — Academy Manager and Admin only
    (shareProfile.md §4). Explicitly not HR, and never the profile owner."""
    return is_curator(user)


def can_set_visibility(user, value: str) -> bool:
    """Audience is curated, not self-declared: only the Academy Manager / Admin
    sets an item's visibility, at every level.

    Owners used to be able to pick the in-platform levels themselves. They no
    longer can — a profile owner deciding their own audience made the published
    profile inconsistent, and the choice belongs with whoever curates it. Items
    are created Private (internal, logged-in viewers) and stay there until a
    curator moves them.
    """
    return is_curator(user)


# ---- read-side audience filtering -------------------------------------------
def can_view_section(viewer, share, section: str, target_user_id=None) -> bool:
    """Whether ``viewer`` may see a whole section of this profile.

    v2 visibility: every section carries an audience level (``ProfileShare.
    sections``). Anonymous visitors only when the Academy Manager has both
    published the profile and set the section to a public level. Logged-in
    users pass when the section's level admits their role — "internal by
    default" (private admits every logged-in user).
    """
    if viewer is None:
        return bool(share and share.shows(section))
    if share is None:
        return True  # no share row: nothing curated yet, internal by default
    level = share.section_visibility(section)
    return level in visible_levels_for(viewer, target_user_id)


def can_view_item(viewer, target_user_id, item) -> bool:
    """A non-owner may only see an item that has cleared review at least once —
    and only when its visibility level admits them.

    Spec §10's item lifecycle is ``Draft → (Mentor approval) → Private →
    (Academy Manager) → Public``: a Draft item is invisible to everyone but the
    owner (and the Academy Manager, who may override), regardless of the
    visibility level set on it. Once approved, the level set by the owner or
    overridden by an Academy Manager/Admin actually gates who sees it.

    ``section_unverified`` is the v2 curator choice to show never-approved
    items in a section (see ProfileShare.include_unverified). Rejected items
    still never leak: an explicit rejection outranks a section setting.
    """
    if is_owner(viewer, target_user_id) or is_curator(viewer):
        return True
    never_approved = item.approved_snapshot is None
    if never_approved:
        # if section_unverified and item.review_state != ReviewState.REJECTED.value:
        #     return True
        # Reviewers still need to see what they're asked to review.
        return can_verify(viewer) and item.is_pending_review
    # Approved items are gated by their visibility level.
    return item.visibility in visible_levels_for(viewer, target_user_id)


# Which visibility levels each audience may see. "anonymous" only ever sees
# items that reached a public level *and* belong to a published profile — the
# publication check itself lives in the parent share-profile feature.
def visible_levels_for(viewer, target_user_id) -> set[str]:
    if viewer is None:
        return {Visibility.PUBLIC.value}  # , Visibility.PUBLIC_SUMMARY.value}
    if is_owner(viewer, target_user_id) or is_curator(viewer):
        return {v.value for v in Visibility}
    # "Private" means logged-in-only, NOT owner-only — the spec defines it that
    # way in §4.3 ("Private profile — visible to logged-in users only") and in
    # the §10 lifecycle, where mentor approval moves an item to Private and
    # thereby makes it "visible within the platform (logged-in users)".
    # MENTOR_ONLY / HR_ONLY are the genuinely restricted levels.
    levels = {
        Visibility.PRIVATE.value,
        # Visibility.ACADEMY_ONLY.value,
        # Visibility.PUBLIC_SUMMARY.value,
        Visibility.PUBLIC.value,
    }
    # if is_mentor(viewer):
    #     levels.add(Visibility.MENTOR_ONLY.value)
    # if is_hr(viewer):
    #     levels.add(Visibility.HR_ONLY.value)
    return levels
