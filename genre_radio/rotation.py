from genre_radio.identity import lyrics_hash, song_key


def select_cached_lyrics(records, tracks, upcoming):
    groups = []
    for record in records:
        names, hashes = {song_key(record)}, {lyrics_hash(record['text'])}
        members, separate = [record], []
        for group in groups:
            if names & group[1] or hashes & group[2]:
                members.extend(group[0])
                names.update(group[1])
                hashes.update(group[2])
            else:
                separate.append(group)
        groups = separate + [(members, names, hashes)]
    if not groups:
        return None

    def matches(track, group):
        return song_key(track) in group[1] or track.get('lyrics_hash') in group[2] or track.get('lyrics_id') in {record['id'] for record in group[0]}

    tail = upcoming[-1] if upcoming else None
    alternatives = [group for group in groups if not tail or not matches(tail, group)]
    candidates = alternatives or groups
    unqueued = [group for group in candidates if not any(matches(track, group) for track in upcoming)]
    candidates = unqueued or candidates
    selected = min(candidates, key=lambda group: (
        max((track.get('created_at', '') for track in tracks if matches(track, group)), default=''),
        min(record['id'] for record in group[0]),
    ))
    return min(selected[0], key=lambda record: record['id'])
