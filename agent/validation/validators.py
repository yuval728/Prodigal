"""Validation rules that depend on domain state."""

from typing import Optional, Tuple

from ..domain.models import AccountData


def verify_identity(
    account_data: AccountData,
    provided_name: str,
    provided_dob: Optional[str] = None,
    provided_aadhaar: Optional[str] = None,
    provided_pincode: Optional[str] = None,
) -> Tuple[bool, str]:
    if provided_name != account_data.full_name:
        return False, "name_mismatch"

    if provided_dob and provided_dob == account_data.dob:
        return True, "verified_dob"

    if provided_aadhaar and provided_aadhaar == account_data.aadhaar_last4:
        return True, "verified_aadhaar"

    if provided_pincode and provided_pincode == account_data.pincode:
        return True, "verified_pincode"

    if not any([provided_dob, provided_aadhaar, provided_pincode]):
        return False, "no_secondary_factor"

    return False, "secondary_factor_mismatch"
