"""A shared word-window splitter for format chunkers.

Both the markdown and PDF chunkers split long bodies into overlapping word
windows near a target size so each chunk embeds with good differentiation. The
algorithm lives here as a pure function so the two chunkers share one
implementation without importing each other — keeping chunker modules free of
sibling-module coupling.
"""


def split_windows(body: str, target_words: int, overlap_words: int) -> list[str]:
    """Split ``body`` into overlapping word windows.

    A body at or under ``target_words`` words is returned as a single window
    unchanged. Longer bodies are cut into windows of at most ``target_words``
    words that advance by ``target_words - overlap_words`` (at least one word)
    so successive windows overlap, and the walk stops once a window reaches the
    end of the body.
    """
    words = body.split()
    if len(words) <= target_words:
        return [body]
    step = max(1, target_words - overlap_words)
    windows = []
    for start in range(0, len(words), step):
        window = words[start : start + target_words]
        if window:
            windows.append(" ".join(window))
        if start + target_words >= len(words):
            break
    return windows
