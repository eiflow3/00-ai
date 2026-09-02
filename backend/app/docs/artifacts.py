"""OpenAPI documentation for the /artifacts endpoints.

Plain response models throughout — no streams — so this module is prose only.
It lives apart from the router so the handlers stay readable.
"""

ARTIFACTS_TAG = "artifacts"

ARTIFACTS_TAG_DESCRIPTION = """\
Stored by-products of extraction — today, a document's tables.

When a document with tables (a PDF) is indexed, each table is lifted out and
stored verbatim, and the text that gets embedded carries a prose description of
it ending in a link of the form `[label](table://{document_id}/{table_id})`.
These endpoints are what such a link resolves against: the client parses the
two ids out of the link and fetches the table here.

Artifacts are written by indexing and only ever read here. They survive
de-indexing (the file and its extraction are both still valid) and are deleted
with their source file on replace or delete.\
"""

LIST_TABLES_DESCRIPTION = """\
Every table one document currently has stored, in document order.

The ids here are what table links inside embedded chunks point at. A document
indexed before it had tables — or one whose format carries none — lists empty
rather than failing.\
"""

GET_TABLE_DESCRIPTION = """\
One stored table, verbatim, with its page and caption.

This is the target of a `table://{document_id}/{table_id}` link found in a
chunk or an answer: the markdown is the table exactly as extracted from the
source document.\
"""

LIST_TABLES_RESPONSES = {
    404: {"description": "No extraction is stored for this document."},
}

GET_TABLE_RESPONSES = {
    404: {"description": "The document has no stored table under this id."},
}
