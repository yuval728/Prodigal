"""Normalization helpers for user input."""

import re
from datetime import date
from typing import Optional, Tuple


def normalize_account_id(raw: str) -> Optional[str]:
    cleaned = raw.upper().replace(" ", "").replace("-", "").replace(":", "")
    match = re.search(r"ACC\d{4}", cleaned)
    return match.group(0) if match else None


def normalize_dob(raw: str) -> Tuple[Optional[str], Optional[str]]:
    raw = raw.lower()
    for phrase in ["dob is", "date of birth is", "dob:", "born on", "i was born on",
                   "my dob is", "my date of birth is", "birthday is"]:
        raw = raw.replace(phrase, "")
    raw = raw.strip()

    month_names = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    numeric_patterns = [
        (r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", "ymd"),
        (r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", "dmy"),
        (r"(\d{1,2})[-/](\d{1,2})[-/](\d{2})$", "dmy2"),
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
                else:
                    d, m = int(g[0]), int(g[1])
                    y = 2000 + int(g[2]) if int(g[2]) < 30 else 1900 + int(g[2])
                return _validate_date(y, m, d)
            except (ValueError, TypeError):
                continue

    raw_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw)

    for month_str, month_num in month_names.items():
        if month_str in raw_clean:
            digits = re.findall(r"\d+", raw_clean.replace(month_str, ""))
            if len(digits) == 2:
                a, b = int(digits[0]), int(digits[1])
                if a > 31:
                    y, d = a, b
                elif b > 31:
                    y, d = b, a
                elif b < 30:
                    y = 2000 + b if b < 30 else 1900 + b
                    d = a
                else:
                    continue
                if y < 100:
                    y = 2000 + y if y < 30 else 1900 + y
                return _validate_date(y, month_num, d)

    return None, "I couldn't parse that date. Please provide your date of birth as DD/MM/YYYY or 'Month DD, YYYY' (e.g., 14 May 1990)."


def _validate_date(year: int, month: int, day: int) -> Tuple[Optional[str], Optional[str]]:
    try:
        parsed = date(year, month, day)
    except ValueError as e:
        if "day is out of range" in str(e) and month == 2 and day == 29:
            return None, f"{year} is not a leap year. February only has 28 days in {year}. Please check your date of birth."
        return None, "That doesn't appear to be a valid date. Please try again with format DD/MM/YYYY."

    today = date.today()
    if parsed >= today:
        return None, "Date of birth must be in the past."
    if (today - parsed).days > 120 * 365:
        return None, "That date seems too far in the past. Please check and try again."

    return parsed.strftime("%Y-%m-%d"), None


def normalize_aadhaar_last4(raw: str) -> Optional[str]:
    raw = raw.lower()
    for phrase in ["last four of my aadhaar is", "aadhaar ends with", "aadhaar last 4 is",
                   "last 4 digits", "last four digits", "aadhaar:", "aadhaar is"]:
        raw = raw.replace(phrase, "")
    raw = raw.strip()

    matches = re.findall(r"\b\d{4}\b", raw)
    if matches:
        return matches[0]

    all_digits = re.sub(r"\D", "", raw)
    if len(all_digits) == 4:
        return all_digits

    return None


def normalize_pincode(raw: str) -> Optional[str]:
    raw = raw.lower()
    for phrase in ["pincode is", "pin code is", "pincode:", "pin:", "my pincode is"]:
        raw = raw.replace(phrase, "")

    spaced_match = re.search(r"(\d\s){5}\d", raw)
    if spaced_match:
        return spaced_match.group(0).replace(" ", "")

    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) == 6:
        return digits_only

    match = re.search(r"\b\d{6}\b", raw)
    return match.group(0) if match else None


def normalize_amount(raw: str, balance: float) -> Tuple[Optional[float], Optional[str]]:
    raw_lower = raw.lower().strip()

    full_phrases = ["full amount", "clear the full", "pay all", "clear all",
                    "total amount", "pay it all", "everything", "whole amount",
                    "entire amount", "clear everything"]
    if any(phrase in raw_lower for phrase in full_phrases):
        return balance, None

    word_numbers = {
        "zero": 0, "hundred": 100, "thousand": 1000, "ten thousand": 10000,
        "lakh": 100000, "one hundred": 100, "one thousand": 1000,
        "five hundred": 500, "two thousand": 2000, "three thousand": 3000,
    }
    for word, value in word_numbers.items():
        if word in raw_lower:
            return _validate_amount(value, balance)

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
    if amount <= 0:
        return None, "Payment amount must be greater than zero."

    amount = round(amount, 2)

    if amount > balance:
        return None, f"Amount ₹{amount:,.2f} exceeds your outstanding balance of ₹{balance:,.2f}. Please enter an amount up to ₹{balance:,.2f}."

    return amount, None


def normalize_card_number(raw: str) -> Tuple[Optional[str], Optional[str]]:
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None, "I couldn't find a card number in that. Please provide your 16-digit card number."

    if len(digits) not in (15, 16):
        return None, f"Card number should be 15 or 16 digits. I found {len(digits)} digits. Please try again."

    if not _luhn_check(digits):
        return None, "That card number doesn't appear to be valid. Please double-check and try again."

    return digits, None


def _luhn_check(card_number: str) -> bool:
    digits = [int(d) for d in card_number]
    for i in range(len(digits) - 2, -1, -2):
        digits[i] *= 2
        if digits[i] > 9:
            digits[i] -= 9
    return sum(digits) % 10 == 0


def normalize_cvv(raw: str, card_number: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None, "Please provide your card's CVV (the 3 or 4 digit security code)."

    is_amex = card_number and card_number[:2] in ("34", "37")
    expected_length = 4 if is_amex else 3

    if len(digits) != expected_length:
        card_type = "American Express" if is_amex else "standard"
        return None, f"CVV for a {card_type} card should be {expected_length} digits. Please try again."

    return digits, None


def normalize_expiry(raw: str) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    raw_lower = raw.lower()

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

    for name, num in month_names.items():
        if name in raw_lower:
            month = num
            digits = re.findall(r"\d+", raw_lower.replace(name, ""))
            if digits:
                y = int(digits[0])
                year = 2000 + y if y < 100 else y
            break

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

    today = date.today()
    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    expiry_date = date(year, month, last_day)

    if expiry_date < today:
        return None, f"This card expired in {month:02d}/{year}. Please use a different card."

    return (month, year), None
