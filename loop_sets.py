"""Named, atomic loop-set storage for MicroPython and CPython."""
import os

try:
    import ujson as json
except ImportError:
    import json

import loop_random as random
import loop_patterns as patterns
from loop_engine import ROLES, PARTS, DIRECTIONS

SET_DIR = '/user/sets'
ADJECTIVES = (
    'FUCKING', 'SHITTY', 'BATSHIT', 'CURSED', 'GNARLY', 'TWISTED',
    'FERAL', 'WICKED', 'BIZARRE', 'UNHOLY', 'RANCID', 'COSMIC',
    'LOOPY', 'NASTY', 'WOBBLY', 'BONKERS', 'SQUISHY', 'PSYCHIC',
    'FILTHY', 'HAUNTED', 'LUMPY', 'SPOOKY', 'CHAOTIC', 'GROSS',
    'GLITCHY', 'MUTANT', 'ELECTRIC', 'WILD', 'CRUSTY', 'DIZZY',
)
NOUNS = (
    'DUCK', 'GOBLIN', 'BASTARD', 'WIZARD', 'FART', 'MOTH',
    'VOID', 'GREMLIN', 'SNAIL', 'TOASTER', 'JELLY', 'RAT',
    'RITUAL', 'BANANA', 'MUPPET', 'PLONKER', 'ZOMBIE', 'WORM',
    'PICKLE', 'MONSTER', 'NOODLE', 'TURD', 'FERRET', 'ALIEN',
    'LOBSTER', 'OCTOPUS', 'MAGGOT', 'CLOWN', 'BADGER', 'BLOB',
)
ADJECTIVE_SET = set(ADJECTIVES)
NOUN_SET = set(NOUNS)
MAX_CACHED_SETS = 32
GENRE_PREFIX = {'HOUSE': 'H', "DRUM'N'BASS": 'D', 'TECHNO': 'T',
                'BREAKS': 'B', 'ELECTRO': 'E'}
PREFIX_GENRE = {letter: genre for genre, letter in GENRE_PREFIX.items()}


def valid_name(name):
    words = name.split(' ') if isinstance(name, str) else []
    if len(words) == 3 and words[0] in PREFIX_GENRE:
        words = words[1:]
    return len(words) == 2 and words[0] in ADJECTIVE_SET and words[1] in NOUN_SET


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_set_steps(data):
    """Reject broken/foreign sets before they can interrupt the playing loop."""
    if not isinstance(data, dict) or data.get('format') != 'amyboard-loop-set' or data.get('version') != 1:
        raise ValueError('unsupported set')
    if data.get('scale_name') not in patterns.SCALE_PRESETS:
        raise ValueError('invalid scale')
    if not _integer(data.get('bpm'), 30, 240):
        raise ValueError('invalid tempo')
    if 'chord_size' in data and not _integer(data['chord_size'], 1, 5):
        raise ValueError('invalid chord size')
    roll_beats = data.get('drum_roll_beats', 128)
    if not _integer(roll_beats, 32, 1024) or roll_beats % 32:
        raise ValueError('invalid drum roll interval')
    if 'break_duration_beats' in data and not _integer(data['break_duration_beats'], 2, 16):
        raise ValueError('invalid kick break duration')
    if data.get('drum_genre') not in patterns.DRUM_GENRES:
        raise ValueError('invalid genre')
    if data.get('cv_role') not in (None,) + ROLES:
        raise ValueError('invalid routing')
    yield
    tracks = data.get('tracks')
    if not isinstance(tracks, dict) or set(tracks) != set(ROLES):
        raise ValueError('invalid tracks')
    channels = []
    for role in ROLES:
        track = tracks[role]
        if not isinstance(track, dict):
            raise ValueError('invalid track')
        bounds = {'channel': (1, 16), 'octave': (-5, 5),
                  'transpose': (-48, 48), 'bank': (0, 3),
                  'program': (0, 127)}
        for field, (low, high) in bounds.items():
            if not _integer(track.get(field), low, high):
                raise ValueError('invalid ' + field)
        if type(track.get('muted')) is not bool:
            raise ValueError('invalid mute')
        if track.get('direction', 'FORWARD') not in DIRECTIONS or (role == 'drum' and
                track.get('direction', 'FORWARD') != 'FORWARD'):
            raise ValueError('invalid direction')
        channels.append(track['channel'])
        cells = track.get('pattern')
        if not isinstance(cells, list) or len(cells) not in (1, 2, 4, 8, 16, 32, 64, 128):
            raise ValueError('invalid loop length')
        stash = track.get('stash', [])
        if not isinstance(stash, list) or len(stash) > max(0, 64 - len(cells)):
            raise ValueError('invalid hidden loop')
        for notes, lengths, field in ((cells, track.get('lengths'), 'lengths'),
                                      (stash, track.get('lengths_stash'), 'hidden lengths')):
            if lengths is not None and (not isinstance(lengths, list) or
                    len(lengths) != len(notes) or any(
                        not _integer(length, 0 if cell is None else 1, 64) or
                        (cell is None and length != 0)
                        for cell, length in zip(notes, lengths))):
                raise ValueError('invalid ' + field)
        for cell in cells + stash:
            if cell is None:
                continue
            if role in ('lead', 'bass'):
                if not _integer(cell, 0, 127):
                    raise ValueError('invalid note')
            elif role == 'chords':
                if not isinstance(cell, list) or not 1 <= len(cell) <= 8 or any(
                        not _integer(n, 0, 127) for n in cell):
                    raise ValueError('invalid chord')
            elif not isinstance(cell, list) or not 1 <= len(cell) <= 16 or any(
                    not isinstance(hit, list) or len(hit) != 2 or
                    not _integer(hit[0], 0, 127) or not _integer(hit[1], 1, 127)
                    for hit in cell):
                raise ValueError('invalid drum hit')
        yield
    if len(set(channels)) != len(ROLES):
        raise ValueError('shared MIDI channel')
    mutes = data.get('drum_muted')
    if not isinstance(mutes, dict) or set(mutes) != set(PARTS) or any(
            type(value) is not bool for value in mutes.values()):
        raise ValueError('invalid drum mutes')
    roots = data.get('chord_roots')
    if not isinstance(roots, list) or len(roots) > 128 or any(
            root is not None and not _integer(root, 0, 127) for root in roots):
        raise ValueError('invalid roots')
    hidden_roots = data.get('chord_roots_stash', [])
    if not isinstance(hidden_roots, list) or len(hidden_roots) > 64 or any(
            root is not None and not _integer(root, 0, 127) for root in hidden_roots):
        raise ValueError('invalid hidden roots')


def validate_set(data):
    for _ in validate_set_steps(data):
        pass
    return data


class Sets:
    def __init__(self, directory=SET_DIR):
        self.directory = directory
        self.keys = {}
        self.cached = {}
        try:
            os.mkdir(directory)
        except OSError:
            if not self._exists(directory):
                raise
        self.names = self._scan_names()
        # Build a bounded live-set cache before playback starts. Read just the
        # key for larger libraries so boot time and memory remain bounded.
        for name in self.names:
            try:
                if len(self.cached) < MAX_CACHED_SETS:
                    data = self.load(name)
                    self.keys[name] = data['scale_name']
                    self.cached[name] = data
                else:
                    key = self._peek_key(name)
                    if key is not None:
                        self.keys[name] = key
            except (OSError, ValueError, TypeError):
                pass

    def _peek_key(self, name):
        with open(self._path(name), 'r') as handle:
            header = handle.read(192)
        marker = '"scale_name"'
        colon = header.find(':', header.find(marker) + len(marker))
        if marker not in header or colon < 0:
            return None
        start = header.find('"', colon + 1)
        end = header.find('"', start + 1)
        if start < 0 or end < 0:
            return None
        key = header[start + 1:end]
        return key if key in patterns.SCALE_PRESETS else None

    def _exists(self, path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def _path(self, name):
        if not valid_name(name):
            raise ValueError('invalid set name')
        return self.directory + '/' + name.replace(' ', '_') + '.json'

    def _scan_names(self):
        names = []
        for filename in os.listdir(self.directory):
            if filename.endswith('.json'):
                name = filename[:-5].replace('_', ' ')
                if valid_name(name):
                    names.append(name)
        names.sort()
        return names

    def list_names(self):
        return self.names[:]

    def _fresh_name(self, genre):
        prefix = GENRE_PREFIX[genre] + ' '
        for _ in range(40):
            name = prefix + random.choice(ADJECTIVES) + ' ' + random.choice(NOUNS)
            if len(name) <= 16 and name not in self.names and not self._exists(self._path(name)):
                return name
        for adjective in ADJECTIVES:
            for noun in NOUNS:
                name = prefix + adjective + ' ' + noun
                if len(name) <= 16 and name not in self.names and not self._exists(self._path(name)):
                    return name
        raise OSError('set library full')

    def save(self, snapshot):
        validate_set(snapshot)
        name = self._fresh_name(snapshot['drum_genre'])
        destination = self._path(name)
        temporary = destination + '.tmp'
        try:
            with open(temporary, 'w') as handle:
                handle.write(json.dumps(snapshot))
            os.rename(temporary, destination)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        self.keys[name] = snapshot['scale_name']
        self.names.append(name)
        self.names.sort()
        if len(self.cached) < MAX_CACHED_SETS:
            self.cached[name] = snapshot
        return name

    def load(self, name):
        with open(self._path(name), 'r') as handle:
            return validate_set(json.loads(handle.read()))

    def cached_load(self, name):
        return self.cached.get(name)

    def load_steps(self, name, result):
        """Read one small block per call; validate away from the note callback."""
        cached = self.cached_load(name)
        if cached is not None:
            result['value'] = cached
            return
        chunks = []
        with open(self._path(name), 'r') as handle:
            while True:
                chunk = handle.read(256)
                if not chunk:
                    break
                chunks.append(chunk)
                yield
        data = json.loads(''.join(chunks))
        yield
        for _ in validate_set_steps(data):
            yield
        self.keys[name] = data['scale_name']
        if len(self.cached) < MAX_CACHED_SETS:
            self.cached[name] = data
        result['value'] = data

    def save_steps(self, snapshot, result):
        """Commit a set in bounded writes, leaving note scheduling first."""
        for _ in validate_set_steps(snapshot):
            yield
        name = self._fresh_name(snapshot['drum_genre'])
        destination = self._path(name)
        temporary = destination + '.tmp'
        yield
        payload = json.dumps(snapshot)
        yield
        try:
            with open(temporary, 'w') as handle:
                for start in range(0, len(payload), 256):
                    handle.write(payload[start:start + 256])
                    yield
            os.rename(temporary, destination)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        self.keys[name] = snapshot['scale_name']
        self.names.append(name)
        self.names.sort()
        if len(self.cached) < MAX_CACHED_SETS:
            self.cached[name] = snapshot
        result['value'] = name

    def delete_steps(self, name, result):
        self.delete(name)
        result['value'] = name
        yield

    def restore_steps(self, name, snapshot, result):
        """Undo DELETE by recreating the same named set atomically."""
        destination = self._path(name)
        if self._exists(destination):
            raise OSError('set name already exists')
        for _ in validate_set_steps(snapshot):
            yield
        payload = json.dumps(snapshot)
        yield
        temporary = destination + '.tmp'
        try:
            with open(temporary, 'w') as handle:
                for start in range(0, len(payload), 256):
                    handle.write(payload[start:start + 256])
                    yield
            os.rename(temporary, destination)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        self.names.append(name)
        self.names.sort()
        self.keys[name] = snapshot['scale_name']
        if len(self.cached) < MAX_CACHED_SETS:
            self.cached[name] = snapshot
        result['value'] = name

    def key_for(self, name):
        return self.keys.get(name)

    def delete(self, name):
        os.remove(self._path(name))
        if name in self.names:
            self.names.remove(name)
        self.keys.pop(name, None)
        self.cached.pop(name, None)
