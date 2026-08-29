"""Gate fixtures for the alias-matching core (R1).

`POSITIVES` must merge, `NEGATIVES` must stay distinct. Precision and recall over
these lists are asserted in CI, so resolution quality is a number that can
regress a test rather than something eyeballed in the browser.
"""

# (left, right, why)
POSITIVES: list[tuple[str, str, str]] = [
    # Nickname variance
    ("Bob Smith", "Robert Smith", "nickname"),
    ("Bill Gates", "William Gates", "nickname"),
    ("Kate Chen", "Katherine Chen", "nickname"),
    ("Tony Russo", "Anthony Russo", "nickname"),
    ("Dan Wu", "Daniel Wu", "nickname"),
    # Legal suffix and spacing variance
    ("TechCorp", "Tech Corp Inc.", "legal suffix + spacing"),
    ("Acme Ltd", "ACME", "legal suffix + case"),
    ("Globex Corporation", "Globex", "legal suffix"),
    ("Initech, LLC", "Initech", "legal suffix + punctuation"),
    ("Umbrella Holdings", "Umbrella", "legal suffix"),
    # Punctuation, case and accents
    ("O'Brien Systems", "OBrien Systems", "punctuation"),
    ("Zoë Martin", "Zoe Martin", "accents"),
    ("billing-service", "Billing Service", "hyphenation"),
    ("Auth Library", "auth-library", "hyphenation"),
    # Titles and generational suffixes
    ("Dr. Alice Johnson", "Alice Johnson", "title"),
    ("John Smith Jr.", "John Smith", "generational suffix"),
    # Acronyms and tickers
    ("IBM", "International Business Machines", "initialism"),
    ("MSFT", "Microsoft", "ticker abbreviation"),
    ("AWS", "Amazon Web Services", "initialism"),
]

NEGATIVES: list[tuple[str, str, str]] = [
    # The canonical trap: ~0.97 Jaro-Winkler, different companies.
    ("Tech Corp", "Tech Corps Ltd", "different company, one letter"),
    ("Acme Systems", "Acme Solutions", "shared brand, different entity"),
    ("John Smith", "Jane Smith", "different given name"),
    ("Robert Smith", "Robert Smyth", "different surname spelling"),
    ("Alice Johnson", "Alice Johnston", "different surname"),
    ("billing-service", "billing-worker", "sibling repositories"),
    ("auth-library", "auth-service", "sibling components"),
    ("Platform Team", "Payments Team", "different teams"),
    ("Globex", "Globe X Media", "unrelated"),
    ("Initech", "Initrode", "unrelated"),
    ("Dan Wu", "Dana Wu", "nickname vs distinct name"),
    ("IBM", "IBN Systems", "near-miss acronym"),
]
