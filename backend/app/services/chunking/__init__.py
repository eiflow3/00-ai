"""Chunking — the ways one document can be cut into embeddable segments.

One module per strategy, one registry that names them, and one catalog that
describes them for a person choosing between them.  Everything here is pure:
given the same text and geometry a strategy returns the same segments, with no
I/O, no framework and no clock — which is what makes re-indexing idempotent and
makes two strategies genuinely comparable.

Identity (which vector id a segment becomes) belongs to app.services.chunker,
and storage to app.services.ingestion.  Nothing in this package knows a file
exists.

Deliberately empty of re-exports.  `schemas.chunking` reads its defaults from
`chunking.tokens`, and a package that imported its own strategies here would
drag the schema module into its own import — a cycle, and one that only shows
up at startup.  Import the submodule you want.
"""
