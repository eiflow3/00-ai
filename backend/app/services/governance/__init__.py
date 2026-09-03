"""Governance stages: pluggable checkpoints a pipeline passes content
through, which can allow it, redact parts of it, tag it, or block it.

Call sites import `runner` and nothing deeper — a pipeline never names a
specific stage, so adding one (secrets, injection screening) is a new
sibling package under here, not an edit at any call site.
"""
