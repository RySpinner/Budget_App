"""Generic merchant-name cleanup (SPEC section 5, step 4).

This makes a readable display name from the bank's raw description. It
doesn't need to be perfect: rules can set an exact merchant_name later
(Phase 4), and raw_description is always kept unchanged.

    "FCP EURO 8603889001 CT null XXXXXXXXXXXX7720"  -> "FCP EURO"
    "SQ *BLUE BOTTLE COFFEE"                        -> "BLUE BOTTLE COFFEE"
    "TST* PIZZA PLACE #0123 SPRINGFIELD IL"         -> "PIZZA PLACE SPRINGFIELD"
    "FAKE CAFE SPRINGFIELD IL REF #900000000000002" -> "FAKE CAFE SPRINGFIELD"
"""

import re

# Payment-processor prefixes such as "SQ *", "TST*", "PAYPAL *".
# A trailing reference number, e.g. " REF #22222222222222222" (credit union).
REFERENCE_SUFFIX = re.compile(r"\s+REF\s*#?\s*\d+\s*$", re.I)

PROCESSOR_PREFIX = re.compile(r"^(SQ|TST|SP|PAYPAL|PP|PY|DD|IC|GOOGLE|AMZN MKTP|AMZ)\s*\*\s*", re.I)

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def _is_noise(token: str) -> bool:
    """Tokens that carry no meaning at the end of a description."""
    return (
        token.lower() == "null"
        or re.fullmatch(r"X{4,}\d{0,4}", token, re.I) is not None   # masked card number
        or re.fullmatch(r"#?\d[\d-]*", token) is not None             # store / phone / reference numbers
        or token.upper() in US_STATES
    )


def clean_merchant(raw_description: str) -> str:
    text = " ".join(raw_description.split())          # collapse whitespace
    text = REFERENCE_SUFFIX.sub("", text)
    text = PROCESSOR_PREFIX.sub("", text)
    tokens = text.split(" ")

    # Strip noise from the end, e.g. "... 8603889001 CT null XXXX7720".
    while len(tokens) > 1 and _is_noise(tokens[-1]):
        tokens.pop()
    # Drop store numbers like "#0123" anywhere in the middle too.
    tokens = [t for t in tokens if not re.fullmatch(r"#\d+", t)] or tokens

    return " ".join(tokens).strip() or raw_description.strip()
