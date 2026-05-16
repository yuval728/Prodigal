"""
validators.py — Pure-Python validation functions.

All validation is deterministic and happens client-side before any API call.
No LLM involvement — these are hard rules, not heuristics.

Design note: catching errors here means fewer wasted API calls and
better user-facing error messages (we know *why* the input is invalid).
"""

import re
from datetime import date, datetime
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Account ID
# ---------------------------------------------------------------------------

def normalize_account_id(raw: str) -> Optional[str]:
    """
    Extract and normalize an account ID from raw text.
    Handles: 'ACC1001', 'ACC 1001', 'acc1001', 'account id: acc1001'
    Returns uppercase normalized form or None if not found.
    """
    # Remove common noise words and normalize spacing
    cleaned = raw.upper().replace(" ", "").replace("-", "").replace(":", "")
    match = re.search(r"ACC\d{4}", cleaned)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Date of Birth
# ---------------------------------------------------------------------------

def normalize_dob(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a date of birth from messy natural language input.
    Returns (normalized_iso_string, error_message).
    Normalized form is YYYY-MM-DD.

    Handles:
    - '1990-05-14'
    - '14th May 1990', '14 May 1990'
    - 'May 14, 90', 'May 14, 1990'
    - '14-05-1990', '14/05/1990'
    - 'DOB is May 14, 90'
    """
    # Strip noise phrases
    raw = raw.lower()
    for phrase in ["dob is", "date of birth is", "dob:", "born on", "i was born on",
                   "my dob is", "my date of birth is", "birthday is"]:
        raw = raw.replace(phrase, "")
    raw = raw.strip()

    # Month name mapping
    month_names = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    # Try standard numeric formats first: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY
    numeric_patterns = [
        (r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", "ymd"),  # YYYY-MM-DD
        (r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", "dmy"),  # DD-MM-YYYY
        (r"(\d{1,2})[-/](\d{1,2})[-/](\d{2})$", "dmy2"),  # DD-MM-YY
    ]

    for pattern, fmt in numeric_patterns:
        match = re.search(pattern, raw)
        if match:
            g = match.groups()
            try:
                if fmt == "ymd":
                    y, m, d = int(g[0]), int(g[1]), int(g[2])
                elif fmt == "dmy":
                    d, m, y = int(g[0]), int(g[1]), int(g[2])
                elif fmt == "dmy2":
                    d, m = int(g[0]), int(g[1])
                    y = 2000 + int(g[2]) if int(g[2]) < 30 else 1900 + int(g[2])
                return _validate_date(y, m, d)
            except (ValueError, TypeError):
                continue

    # Try formats with month names: "14th May 1990", "May 14, 1990", "May 14, 90"
    # Remove ordinal suffixes
    raw_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw)

    for month_str, month_num in month_names.items():
        if month_str in raw_clean:
            # Extract digits
            digits = re.findall(r"\d+", raw_clean.replace(month_str, ""))
            if len(digits) == 2:
                # Figure out which is day and which is year
                a, b = int(digits[0]), int(digits[1])
                if a > 31:           # a is year
                    y, d = a, b
                elif b > 31:         # b is year (4-digit)
                    y, d = b, a
                elif b < 30:         # b is 2-digit year
                    y = 2000 + b if b < 30 else 1900 + b
                    d = a
                else:
                    continue
                # Expand 2-digit year
                if y < 100:
                    y = 2000 + y if y < 30 else 1900 + y
                return _validate_date(y, month_num, d)

    return None, "I couldn't parse that date. Please provide your date of birth as DD/MM/YYYY or 'Month DD, YYYY' (e.g., 14 May 1990)."


def _validate_date(year: int, month: int, day: int) -> Tuple[Optional[str], Optional[str]]:
    """Validate a parsed year/month/day and return ISO string or error."""
    try:
        # datetime.date will raise ValueError for invalid dates (e.g., Feb 30)
        # Correctly handles leap years: 1988-02-29 is valid, 1990-02-29 is not
        parsed = date(year, month, day)
    except ValueError as e:
        # Give a specific error for common mistakes (e.g., Feb 29 on non-leap year)
        if "day is out of range" in str(e) and month == 2 and day == 29:
            return None, f"{year} is not a leap year. February only has 28 days in {year}. Please check your date of birth."
        return None, f"That doesn't appear to be a valid date. Please try again with format DD/MM/YYYY."

    # Sanity check: DOB should be in the past and not more than 120 years ago
    today = date.today()
    if parsed >= today:
        return None, "Date of birth must be in the past."
    if (today - parsed).days > 120 * 365:
        return None, "That date seems too far in the past. Please check and try again."

    return parsed.strftime("%Y-%m-%d"), None


# ---------------------------------------------------------------------------
# Aadhaar last 4
# ---------------------------------------------------------------------------

def normalize_aadhaar_last4(raw: str) -> Optional[str]:
    """
    Extract the last 4 digits of an Aadhaar number from messy input.
    Handles: '4321', 'last four is 4321', 'Aadhaar ends with 9876'
    Returns 4-digit string or None.
    """
    # Remove noise
    raw = raw.lower()
    for phrase in ["last four of my aadhaar is", "aadhaar ends with", "aadhaar last 4 is",
                   "last 4 digits", "last four digits", "aadhaar:", "aadhaar is"]:
        raw = raw.replace(phrase, "")
    raw = raw.strip()

    # Find exactly 4 consecutive digits
    matches = re.findall(r"\b\d{4}\b", raw)
    if matches:
        return matches[0]  # Take the first 4-digit block found

    # Try to find any 4 digits if no word-boundary match
    all_digits = re.sub(r"\D", "", raw)
    if len(all_digits) == 4:
        return all_digits

    return None


# ---------------------------------------------------------------------------
# Pincode
# ---------------------------------------------------------------------------

def normalize_pincode(raw: str) -> Optional[str]:
    """
    Extract a 6-digit Indian PIN code from messy input.
    Handles: '400001', 'pincode is 400 001', '4 0 0 0 0 1'
    Returns 6-digit string or None.
    """
    # Remove common noise, then collapse spaces between digits
    raw = raw.lower()
    for phrase in ["pincode is", "pin code is", "pincode:", "pin:", "my pincode is"]:
        raw = raw.replace(phrase, "")

    # Handle space-separated digits: "4 0 0 0 0 1" → "400001"
    spaced_match = re.search(r"(\d\s){5}\d", raw)
    if spaced_match:
        return spaced_match.group(0).replace(" ", "")

    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) == 6:
        return digits_only

    # Find a 6-digit block
    match = re.search(r"\b\d{6}\b", raw)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Payment amount
# ---------------------------------------------------------------------------

def normalize_amount(raw: str, balance: float) -> Tuple[Optional[float], Optional[str]]:
    """
    Parse a payment amount from natural language.
    Handles: '1000', 'a thousand rupees', '₹500', 'full amount', 'clear everything'
    Returns (amount_float, error_message).
    """
    raw_lower = raw.lower().strip()

    # "full amount", "clear everything", "pay all", "total amount", "pay it all"
    full_phrases = ["full amount", "clear the full", "pay all", "clear all",
                    "total amount", "pay it all", "everything", "whole amount",
                    "entire amount", "clear everything"]
    if any(phrase in raw_lower for phrase in full_phrases):
        return balance, None

    # Word-to-number for common values
    word_numbers = {
        "zero": 0, "hundred": 100, "thousand": 1000, "ten thousand": 10000,
        "lakh": 100000, "one hundred": 100, "one thousand": 1000,
        "five hundred": 500, "two thousand": 2000, "three thousand": 3000,
    }
    for word, value in word_numbers.items():
        if word in raw_lower:
            return _validate_amount(value, balance)

    # Extract numeric value — strip currency symbols and commas
    cleaned = re.sub(r"[₹$,\s]", "", raw)
    match = re.search(r"\d+(\.\d+)?", cleaned)
    if match:
        try:
            amount = float(match.group(0))
            return _validate_amount(amount, balance)
        except ValueError:
            pass

    return None, "I couldn't understand that amount. Please specify how much you'd like to pay (e.g., ₹500 or 'full amount')."


def _validate_amount(amount: float, balance: float) -> Tuple[Optional[float], Optional[str]]:
    """Validate a parsed amount against business rules."""
    if amount <= 0:
        return None, "Payment amount must be greater than zero."

    # Round to 2 decimal places — API rejects more than 2 decimal places
    amount = round(amount, 2)

    if amount > balance:
        return None, f"Amount ₹{amount:,.2f} exceeds your outstanding balance of ₹{balance:,.2f}. Please enter an amount up to ₹{balance:,.2f}."

    return amount, None


# ---------------------------------------------------------------------------
# Card number (with Luhn check)
# ---------------------------------------------------------------------------

def normalize_card_number(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract and validate a card number from messy input.
    Handles: '4532 0151 1283 0366', '4532015112830366'
    Returns (digits_only_string, error_message).
    """
    # Strip everything except digits
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None, "I couldn't find a card number in that. Please provide your 16-digit card number."

    if len(digits) not in (15, 16):  # 15 for Amex, 16 for most cards
        return None, f"Card number should be 15 or 16 digits. I found {len(digits)} digits. Please try again."

    if not _luhn_check(digits):
        return None, "That card number doesn't appear to be valid. Please double-check and try again."

    return digits, None


def _luhn_check(card_number: str) -> bool:
    """
    Luhn algorithm — validates card number checksum.
    This catches most typos and fake numbers before hitting the API.

    Algorithm:
    1. From right to left, double every second digit
    2. If doubled value > 9, subtract 9
    3. Sum all digits
    4. Valid if sum % 10 == 0
    """
    digits = [int(d) for d in card_number]
    # Process from right to left — every second digit (0-indexed from right)
    for i in range(len(digits) - 2, -1, -2):
        digits[i] *= 2
        if digits[i] > 9:
            digits[i] -= 9
    return sum(digits) % 10 == 0


# ---------------------------------------------------------------------------
# CVV
# ---------------------------------------------------------------------------

def normalize_cvv(raw: str, card_number: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract and validate CVV.
    Standard cards: 3 digits. Amex (starts with 34 or 37): 4 digits.
    Returns (cvv_string, error_message).
    """
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None, "Please provide your card's CVV (the 3 or 4 digit security code)."

    # Determine expected CVV length based on card type
    is_amex = card_number and card_number[:2] in ("34", "37")
    expected_length = 4 if is_amex else 3

    if len(digits) != expected_length:
        card_type = "American Express" if is_amex else "standard"
        return None, f"CVV for a {card_type} card should be {expected_length} digits. Please try again."

    return digits, None


# ---------------------------------------------------------------------------
# Card expiry
# ---------------------------------------------------------------------------

def normalize_expiry(raw: str) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """
    Parse card expiry from messy input.
    Handles: '12/2027', '12/27', 'December 2027', 'expires Dec 27'
    Returns ((month, year), error_message) — year is always 4-digit.
    """
    raw_lower = raw.lower()

    # Strip noise
    for phrase in ["expires", "expiry", "expiry date", "valid till", "valid through", "exp"]:
        raw_lower = raw_lower.replace(phrase, "")
    raw_lower = raw_lower.strip()

    month_names = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
        "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    month, year = None, None

    # Try month name + year: "December 2027", "Dec 27"
    for name, num in month_names.items():
        if name in raw_lower:
            month = num
            digits = re.findall(r"\d+", raw_lower.replace(name, ""))
            if digits:
                y = int(digits[0])
                year = 2000 + y if y < 100 else y
            break

    # Try MM/YY or MM/YYYY
    if month is None:
        match = re.search(r"(\d{1,2})[/\-](\d{2,4})", raw_lower)
        if match:
            month = int(match.group(1))
            y = int(match.group(2))
            year = 2000 + y if y < 100 else y

    if month is None or year is None:
        return None, "I couldn't parse that expiry date. Please provide it as MM/YYYY (e.g., 12/2027)."

    if month < 1 or month > 12:
        return None, f"Month {month} is invalid. Please provide a valid expiry date (MM/YYYY)."

    # Check if card is expired
    today = date.today()
    # Card is valid through the last day of the expiry month
    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    expiry_date = date(year, month, last_day)

    if expiry_date < today:
        return None, f"This card expired in {month:02d}/{year}. Please use a different card."

    return (month, year), None


# ---------------------------------------------------------------------------
# Verification logic (deterministic — no LLM)
# ---------------------------------------------------------------------------

def verify_identity(
    account_data,  # AccountData
    provided_name: str,
    provided_dob: Optional[str] = None,
    provided_aadhaar: Optional[str] = None,
    provided_pincode: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Strict identity verification.

    Rules (from spec):
    - Full name must match EXACTLY (case-sensitive, no fuzzy matching)
    - At least one secondary factor must match exactly:
      - Date of birth (YYYY-MM-DD)
      - Aadhaar last 4 digits
      - Pincode

    Returns (is_verified, reason_string).

    IMPORTANT: This function never returns the actual account values
    in the reason string — only confirms match/mismatch status.
    This prevents data exposure even in error messages.
    """
    # Step 1: Name must match exactly
    if provided_name != account_data.full_name:
        return False, "name_mismatch"

    # Step 2: At least one secondary factor must match
    if provided_dob and provided_dob == account_data.dob:
        return True, "verified_dob"

    if provided_aadhaar and provided_aadhaar == account_data.aadhaar_last4:
        return True, "verified_aadhaar"

    if provided_pincode and provided_pincode == account_data.pincode:
        return True, "verified_pincode"

    # Name matched but no secondary factor matched
    if not any([provided_dob, provided_aadhaar, provided_pincode]):
        return False, "no_secondary_factor"

    return False, "secondary_factor_mismatch"
