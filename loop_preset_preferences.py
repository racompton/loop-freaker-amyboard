"""Persistent, channel-specific external MIDI preset randomization choices."""
import os

try:
    import ujson as json
except ImportError:
    import json

import loop_random as random


# Number of available programs in each zero-based external MIDI bank.
PROGRAM_COUNTS = {
    'lead': (26, 51, 40, 17),
    'bass': (101,),
    'chords': (128, 72),
    'drum': (128,),
}
PREFERRED_WEIGHT = 16
DEFAULT_PREFIX = '/user/preset_preferences'


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def valid_preset(role, bank, program):
    counts = PROGRAM_COUNTS.get(role)
    return (counts is not None and _integer(bank, 0, len(counts) - 1) and
            _integer(program, 0, counts[bank] - 1))


def move_preset(role, bank, program, delta):
    counts = PROGRAM_COUNTS[role]
    if not valid_preset(role, bank, program):
        raise ValueError('invalid bank or program')
    position = (sum(counts[:bank]) + program + delta) % sum(counts)
    for next_bank, count in enumerate(counts):
        if position < count:
            return next_bank, position
        position -= count


def _valid_entries(entries):
    if not isinstance(entries, dict):
        return False
    for key, entry in entries.items():
        if not isinstance(key, str) or ':' not in key or not isinstance(entry, dict):
            return False
        role, channel = key.split(':', 1)
        if role not in PROGRAM_COUNTS or not channel.isdigit() or not 1 <= int(channel) <= 16:
            return False
        if set(entry) != {'preferred', 'deleted'}:
            return False
        counts = PROGRAM_COUNTS[role]
        limit_program = 52 if role == 'lead' else max(counts) - 1
        legacy_total = len(counts) * (limit_program + 1)
        seen = set()
        for status in ('preferred', 'deleted'):
            values = entry[status]
            if not isinstance(values, list) or len(values) > legacy_total:
                return False
            for pair in values:
                if not isinstance(pair, list) or len(pair) != 2 or not _integer(pair[0], 0, len(counts) - 1) or not _integer(pair[1], 0, limit_program):
                    return False
                item = (pair[0], pair[1])
                if item in seen:
                    return False
                seen.add(item)
        if len(entry['deleted']) >= legacy_total:
            return False
    return True


def _sanitize_entries(entries):
    cleaned = {}
    for key, entry in entries.items():
        role = key.split(':', 1)[0]
        valid = {status: [pair for pair in entry[status]
                          if valid_preset(role, pair[0], pair[1])]
                 for status in ('preferred', 'deleted')}
        if len(valid['deleted']) >= sum(PROGRAM_COUNTS[role]):
            valid['deleted'] = []
        if valid['preferred'] or valid['deleted']:
            cleaned[key] = valid
    return cleaned


class PresetPreferences:
    def __init__(self, prefix=DEFAULT_PREFIX):
        self.prefix = prefix
        self.entries = {}
        self.revision = 0
        self.active = None
        for slot in ('a', 'b'):
            try:
                with open(self._path(slot), 'r') as handle:
                    data = json.loads(handle.read())
                if (not isinstance(data, dict) or data.get('version') != 1 or
                        not _integer(data.get('revision'), 1, 0x7fffffff) or
                        not _valid_entries(data.get('entries'))):
                    continue
                if data['revision'] > self.revision:
                    self.entries = _sanitize_entries(data['entries'])
                    self.revision = data['revision']
                    self.active = slot
            except (OSError, ValueError, TypeError):
                continue

    def _path(self, slot):
        return self.prefix + '.' + slot + '.json'

    def _key(self, role, channel):
        if role not in PROGRAM_COUNTS or not _integer(channel, 1, 16):
            raise ValueError('invalid role or MIDI channel')
        return role + ':' + str(channel)

    def _pair(self, role, bank, program):
        if not valid_preset(role, bank, program):
            raise ValueError('invalid bank or program')
        return [bank, program]

    def status(self, role, channel, bank, program):
        key = self._key(role, channel)
        pair = self._pair(role, bank, program)
        entry = self.entries.get(key, {})
        if pair in entry.get('preferred', []):
            return 'PREFERRED'
        if pair in entry.get('deleted', []):
            return 'DELETED'
        return 'NORMAL'

    def set_status(self, role, channel, bank, program, status):
        if status not in ('PREFERRED', 'DELETED', 'NORMAL'):
            raise ValueError('invalid status')
        key = self._key(role, channel)
        pair = self._pair(role, bank, program)
        old = self.entries.get(key, {'preferred': [], 'deleted': []})
        entry = {name: [item[:] for item in old[name] if item != pair]
                 for name in ('preferred', 'deleted')}
        if status != 'NORMAL':
            name = 'preferred' if status == 'PREFERRED' else 'deleted'
            entry[name].append(pair)
        if len(entry['deleted']) >= sum(PROGRAM_COUNTS[role]):
            raise ValueError('cannot delete every preset')
        next_entries = self.entries.copy()
        if entry['preferred'] or entry['deleted']:
            next_entries[key] = entry
        else:
            next_entries.pop(key, None)
        if next_entries == self.entries:
            return
        self._save(next_entries)
        self.entries = next_entries

    def _save(self, entries):
        next_slot = 'b' if self.active == 'a' else 'a'
        destination = self._path(next_slot)
        temporary = self.prefix + '.tmp'
        data = {'version': 1, 'revision': self.revision + 1, 'entries': entries}
        try:
            with open(temporary, 'w') as handle:
                handle.write(json.dumps(data))
            try:
                os.remove(destination)
            except OSError:
                pass
            os.rename(temporary, destination)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        self.active = next_slot
        self.revision += 1

    def choose(self, role, channel):
        key = self._key(role, channel)
        counts = PROGRAM_COUNTS[role]
        entry = self.entries.get(key, {'preferred': [], 'deleted': []})
        preferred = entry['preferred']
        deleted = entry['deleted']
        total = 0
        for bank, count in enumerate(counts):
            for program in range(count):
                pair = [bank, program]
                if pair not in deleted:
                    total += PREFERRED_WEIGHT if pair in preferred else 1
        if not total:
            raise ValueError('no presets available')
        target = random.randint(0, total - 1)
        for bank, count in enumerate(counts):
            for program in range(count):
                pair = [bank, program]
                if pair not in deleted:
                    target -= PREFERRED_WEIGHT if pair in preferred else 1
                    if target < 0:
                        return bank, program
