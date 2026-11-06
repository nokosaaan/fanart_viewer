"""Shared pieces of the bookmark/like scanners (twitter_gql_fetch,
pixiv_bookmarks_fetch, poipiku_bookmarks_fetch).

A scan walks the list newest-first, one page per request. A network or API
error on page N used to throw away everything pages 1..N-1 had found, and the
saved resume position stayed where the PREVIOUS successful call left it. The
scanners now raise ScanInterrupted instead, carrying what they found and the
position of the page that failed, so the caller can queue the candidates,
save that position, and then report the original error: the next call
restarts exactly at the page that failed.
"""


class ScanInterrupted(Exception):
    """A scan failed part-way. `cause` is the original exception,
    `candidates` the newest-first candidates found before the failure, and
    `resume` the platform's resume position (Twitter cursor / Pixiv offset /
    Poipiku URL) of the page that failed -- in the same form the scanner
    returns as its second value."""

    def __init__(self, cause, candidates, resume):
        super().__init__(str(cause))
        self.cause = cause
        self.candidates = candidates
        self.resume = resume
