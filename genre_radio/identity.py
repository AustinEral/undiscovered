"""Shared song identity normalization for cached inputs and generation history."""

import hashlib
import unicodedata


def song_key(record):
    return tuple(' '.join(unicodedata.normalize('NFKC', record.get(field, '')).casefold().split()) for field in ('artist', 'title'))


def lyrics_hash(text):
    normalized = ' '.join(unicodedata.normalize('NFKC', text).casefold().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
