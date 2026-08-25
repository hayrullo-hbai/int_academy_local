import enum


class UserSource(str, enum.Enum):
    """Where a user's credentials live / where they came from."""

    LOCAL = "local"
    HSTAFF = "hstaff"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    OFFBOARDED = "offboarded"
    VACATION = "vacation"


class OfficeLocation(str, enum.Enum):
    NAMANGAN = "namangan_uz"
    TASHKENT = "tashkent_uz"
    INCHEON = "incheon_sk"


OFFICE_LOCATION_VALUES = {o.value for o in OfficeLocation}
USER_STATUS_VALUES = {s.value for s in UserStatus}

# Well-known role names (mirror hstaff; see rbac_catalog).
SUPERADMIN = "superadmin"
TALENT = "talent"
SCHOOL = "school"  # entry talent tier granted when local onboarding completes

# "Learner" isn't a role of its own — it's the set of hstaff talent-journey
# tiers. Anyone holding one of these is treated as a learner.
LEARNER_ROLES = {"talent", "softlanding", "foundation", "school"}
