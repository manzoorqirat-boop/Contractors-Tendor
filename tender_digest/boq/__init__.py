"""BOQ engine: master data, BOQ parsing, three-tier item matching, rate comparison and risk extraction.

Everything here is plain Python on top of the app's SQLite file. Claude is optional: with no ANTHROPIC_API_KEY the matcher
falls back to rule-based checks and never auto-matches an item it cannot justify.
"""
