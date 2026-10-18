"""Social pipeline: NODO prepares posts for TikTok, Instagram and Facebook; Gaetano approves; then they ship.

Nothing is ever posted without an explicit «sì»: a draft creates a `ContentItem` plus a pending `Approval`.
On approval the post pack (caption, per-channel text, media notes) is exported to the files folder so it can be
posted by hand today — and real API publishers can be dropped into `publish.py` when the OAuth tokens exist.
"""
