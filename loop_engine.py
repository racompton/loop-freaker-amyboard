"""Four-track sequencer, independent of the OLED and board drivers."""
import loop_random as random
import loop_patterns as patterns
from loop_preset_preferences import PROGRAM_COUNTS, move_preset, valid_preset

ROLES = ('lead', 'bass', 'chords', 'drum')
DIRECTIONS = ('FORWARD', 'REVERSE', 'PING PONG', 'RANDOM')
NAMES = {'lead': 'SYNTH', 'bass': 'BASS', 'chords': 'CHORD', 'drum': 'DRUMS'}
CHANNELS = {'lead': 4, 'bass': 3, 'chords': 6, 'drum': 10}  # human, 1..16
PARTS = ('kick', 'snare', 'toms', 'rimshot', 'clap', 'hats', 'cymbals')
DRUM_FILL_STEPS = 16  # one bar at sixteenth-note resolution
DRUM_BREAK_BEATS = 64
AUTO_WAIT_STEPS = 128 * 4
AUTO_HANDOFF_STEPS = 32 * 4


def clamp(value, low, high):
    return max(low, min(high, value))


def gcd(a, b):
    while b:
        a, b = b, a % b
    return a


class Track:
    def __init__(self, role):
        self.role = role
        self.channel = CHANNELS[role]
        self.pattern = []
        self.stash = []  # hidden tail retained when a loop is shortened
        self.lengths = []  # melodic note duration in sixteenth-note steps
        self.lengths_stash = []
        self.direction = 'FORWARD'
        self.random_cycle = None
        self.random_order = []
        self.active = []  # exact notes sent, including transpose
        self.muted = role == 'chords'
        self.octave = 0
        self.transpose = 0
        self.bank = 0
        self.program = 0


class _SilentSink:
    def note_off(self, track, note):
        pass

    def cv(self, volts, channel):
        pass

    def preset(self, track):
        pass


class Engine:
    """sink implements note_on/off, preset, cv, transport and clock.

    Called from a single event loop. No desktop threads, mido or blocking sleeps.
    Chord voicings tie across steps; every drum hit retriggers, including repeats.
    """
    def __init__(self, sink, generate=True):
        self.sink = sink
        self.tracks = {role: Track(role) for role in ROLES}
        self.drum_muted = {part: part in ('toms', 'rimshot', 'cymbals') for part in PARTS}
        self.scale_name = 'C Major'
        self.scale = patterns.SCALE_PRESETS[self.scale_name][:]
        self.chord_size = 4
        self.bpm = 124
        self.tempo_start_bpm = 124
        self.tempo_target_bpm = 124
        self.tempo_start_step = None
        self.step = 0
        self.playing = False
        self.cv_role = 'lead'
        self.cv_zero = 60
        self.cv_gate = 5
        self.drum_genre = 'HOUSE'
        self.drum_roll_beats = 128
        self.break_duration_beats = random.randint(2, 16)
        self.roll_due_step = self.drum_roll_beats * 4
        self.break_due_step = (DRUM_BREAK_BEATS - self.break_duration_beats) * 4
        self.roll_remaining = 0
        self.break_remaining = 0
        self.chord_roots_stash = []
        self.phase_origin = 0
        self.track_origins = {role: 0 for role in ROLES}
        self.mix_enabled = True
        self.mix_state = None
        self.pending_load = None
        self.auto_play = False
        self.auto_due_step = None
        self.auto_plan = None
        self.auto_state = None
        self.on_load = None
        self.on_mix = None
        self.on_auto = None
        self._cv_notes = []
        self._cv_gate_high = False
        self.on_note = None
        self.preset_preferences = None
        if generate:
            self.generate_all()

    def generate_all(self):
        # Chords FIRST: the bass generator consumes the newly generated roots.
        for role in ('chords', 'bass', 'lead', 'drum'):
            generated = (
                patterns.generate_drums(self.drum_genre, 16) if role == 'drum'
                else self._generate_pattern(role))
            self.tracks[role].pattern = generated[:64]
            self.tracks[role].stash = []
            self.tracks[role].random_cycle = None
            if role == 'chords':
                patterns.ARRANGER['chord_roots_16ths'] = patterns.ARRANGER['chord_roots_16ths'][:64]
                self.chord_roots_stash = []
            if role != 'drum':
                self._ensure_start_note(role)
            self.tracks[role].lengths = [1 if cell is not None else 0
                                        for cell in self.tracks[role].pattern]
            self.tracks[role].lengths_stash = []
            self._fill_hidden_tail(role)

    def _fill_hidden_tail(self, role):
        """Choose all hidden steps once; resizing only reveals or hides them."""
        track = self.tracks[role]
        missing = 64 - len(track.pattern) - len(track.stash)
        if missing <= 0:
            return
        extra, roots = self._extra_notes(role, missing)
        track.stash.extend(extra)
        track.lengths_stash.extend(1 if cell is not None else 0 for cell in extra)
        if role == 'chords':
            self.chord_roots_stash.extend(roots)

    def _sync_lengths(self, role):
        track = self.tracks[role]
        if len(track.lengths) != len(track.pattern):
            track.lengths = [1 if cell is not None else 0 for cell in track.pattern]
        else:
            for index, cell in enumerate(track.pattern):
                if cell is None:
                    track.lengths[index] = 0
                elif track.lengths[index] < 1:
                    track.lengths[index] = 1

    def _maybe_randomize_lengths(self, role):
        if role == 'drum':
            return
        track = self.tracks[role]
        self._sync_lengths(role)
        if random.randint(1, 3) != 1:
            return
        bank = track.pattern + track.stash
        lengths = track.lengths + track.lengths_stash
        positions = [i for i, cell in enumerate(bank) if cell is not None]
        for offset, position in enumerate(positions):
            following = positions[(offset + 1) % len(positions)]
            if following <= position:
                following += len(bank)
            lengths[position] = random.randint(1, following - position)
        track.lengths = lengths[:len(track.pattern)]
        track.lengths_stash = lengths[len(track.pattern):]

    def _ensure_start_note(self, role):
        track = self.tracks[role]
        if not track.pattern or track.pattern[0] is not None:
            return
        if role == 'lead':
            track.pattern[0] = random.choice(self.scale)
        elif role == 'bass':
            low = patterns.CONFIG['bass']['register_low']
            high = patterns.CONFIG['bass']['register_high']
            pcs = patterns.scale_pitch_classes(self.scale)
            candidates = [n for n in range(low, high + 1) if n % 12 in pcs]
            track.pattern[0] = random.choice(candidates)
        else:
            # MicroPython's next() only accepts the iterator argument.
            first = None
            for cell in track.pattern:
                if cell:
                    first = cell
                    break
            track.pattern[0] = (first[:] if first else patterns.resize_chord_voicing(
                [self.scale[0]], self.scale[0] % 12, self.scale, self.chord_size))
            roots = patterns.ARRANGER['chord_roots_16ths']
            if not roots:
                roots.extend([None] * len(track.pattern))
            if roots[0] is None:
                roots[0] = clamp(self.scale[0] - 24, 0, 127)

    def _generate_pattern(self, role):
        return patterns.generate_pattern(self.scale, role, self.scale_name,
                                         self.chord_size if role == 'chords' else 4)

    def set_chord_size(self, size):
        size = clamp(size, 1, 5)
        if size == self.chord_size:
            return
        self.silence('chords')
        self.chord_size = size
        pattern = self.tracks['chords'].pattern
        roots = patterns.ARRANGER['chord_roots_16ths']
        for index, voicing in enumerate(pattern):
            if voicing:
                root = roots[index] if index < len(roots) else None
                root_pc = root % 12 if root is not None else voicing[0] % 12
                pattern[index] = patterns.resize_chord_voicing(
                    voicing, root_pc, self.scale, size)
        for index, voicing in enumerate(self.tracks['chords'].stash):
            if voicing:
                root = self.chord_roots_stash[index] if index < len(self.chord_roots_stash) else None
                root_pc = root % 12 if root is not None else voicing[0] % 12
                self.tracks['chords'].stash[index] = patterns.resize_chord_voicing(
                    voicing, root_pc, self.scale, size)

    def start(self):
        if not self.playing:
            self.step = 0
            self.phase_origin = 0
            self.track_origins = {role: 0 for role in ROLES}
            self.roll_due_step = self.drum_roll_beats * 4 if self.drum_roll_beats else None
            self.break_due_step = (DRUM_BREAK_BEATS - self.break_duration_beats) * 4
            self.roll_remaining = self.break_remaining = 0
            self.playing = True
            if self.auto_play:
                self.auto_due_step = AUTO_WAIT_STEPS
            self.sink.transport(True)

    def stop(self):
        self.playing = False
        self.pending_load = None
        self.mix_state = None
        self.auto_plan = None
        self.auto_state = None
        self.auto_due_step = None
        self.roll_remaining = self.break_remaining = 0
        for role in ROLES:
            self.silence(role)
        self._gate(False)
        self.sink.transport(False)

    def silence(self, role):
        track = self.tracks[role]
        for note, _ in track.active:
            self.sink.note_off(track, note)
        track.active = []
        if self.cv_role == role:
            self._cv_notes = []
            self._gate(False)

    def _gate(self, high):
        if high != self._cv_gate_high:
            self.sink.cv(self.cv_gate if high else 0, 1)
            self._cv_gate_high = high

    def _mirror(self, role, notes):
        if role != self.cv_role:
            return
        pitches = [n for n, _ in notes]
        if pitches:
            if pitches != self._cv_notes:
                self.sink.cv((min(pitches) - self.cv_zero) / 12.0, 0)
            self._gate(True)
        else:
            self._gate(False)
        self._cv_notes = pitches

    def set_cv_role(self, role):
        if role is not None and role not in ROLES:
            raise ValueError('unknown CV source')
        self._gate(False)
        self._cv_notes = []
        self.cv_role = role
        if role is not None:
            self._mirror(role, self.tracks[role].active)

    def set_channel(self, role, channel):
        channel = clamp(channel, 1, 16)
        track = self.tracks[role]
        if channel == track.channel:
            return True
        # Sharing channels lets one track's note-offs/program changes damage another.
        if any(t.channel == channel for r, t in self.tracks.items() if r != role):
            return False
        self.silence(role)  # send offs to the OLD channel
        if hasattr(self.sink, 'release_channel'):
            self.sink.release_channel(track.channel)
        track.channel = channel
        self.sink.preset(track)
        return True

    def set_transpose(self, role, value):
        self.silence(role)
        self.tracks[role].transpose = clamp(value, -48, 48)

    def set_octave(self, role, value=None):
        if value is None:
            role, value = 'lead', role  # compatibility with the original SYNTH control
        if role not in ('lead', 'bass', 'chords'):
            return
        self.silence(role)
        self.tracks[role].octave = clamp(value, -5, 5)

    def edit_notes(self, role, delta=None):
        if delta is None:
            role, delta = 'lead', role
        if role not in ('lead', 'bass', 'chords') or delta == 0:
            return
        track = self.tracks[role]
        occupied = sum(cell is not None for cell in track.pattern)
        count = (min(abs(delta), len(track.pattern) - occupied) if delta > 0
                 else min(abs(delta), max(0, occupied - 1)))
        if not count:
            return
        self.silence(role)
        if role == 'lead' and delta > 0:
            patterns.lead_add_notes_inplace(track.pattern, self.scale, count)
            self._sync_lengths(role)
            return
        if role == 'lead':
            patterns.lead_remove_notes_inplace(track.pattern, count)
            self._sync_lengths(role)
            return
        indexes = [i for i, value in enumerate(track.pattern)
                   if (value is None) == (delta > 0)]
        for i in random.sample(indexes, min(count, len(indexes))):
            if delta < 0:
                track.pattern[i] = None
            elif role == 'bass':
                low = patterns.CONFIG['bass']['register_low']
                high = patterns.CONFIG['bass']['register_high']
                candidates = [n for n in range(low, high + 1)
                              if n % 12 in patterns.scale_pitch_classes(self.scale)]
                track.pattern[i] = random.choice(candidates)
            else:
                # Use a voicing already in this phrase; if the loop is empty,
                # draw a new voicing from the original chord generator.
                existing = [track.pattern[(i - distance) % len(track.pattern)]
                            for distance in range(1, len(track.pattern) + 1)
                            if track.pattern[(i - distance) % len(track.pattern)] is not None]
                if existing:
                    track.pattern[i] = existing[0][:]
                else:
                    roots = patterns.ARRANGER['chord_roots_16ths'][:]
                    source = self._generate_pattern('chords')
                    choice = None
                    for chord in source:
                        if chord:
                            choice = chord[:]
                            break
                    track.pattern[i] = choice if choice else patterns.resize_chord_voicing(
                        [self.scale[0]], self.scale[0] % 12,
                        self.scale, self.chord_size)
                    patterns.ARRANGER['chord_roots_16ths'] = roots
        self._sync_lengths(role)

    def _extra_notes(self, role, length):
        if role == 'drum':
            return patterns.generate_drums(self.drum_genre, length), []
        roots = patterns.ARRANGER['chord_roots_16ths'][:]
        out, new_roots = [], []
        while len(out) < length:
            segment = self._generate_pattern(role)
            out.extend(segment)
            if role == 'chords':
                new_roots.extend(patterns.ARRANGER['chord_roots_16ths'])
        patterns.ARRANGER['chord_roots_16ths'] = roots
        return out[:length], new_roots[:length]

    def set_loop_length(self, role, delta):
        if delta == 0:
            return
        track = self.tracks[role]
        self._sync_lengths(role)
        old_length = len(track.pattern)
        new_length = old_length
        for _ in range(min(abs(delta), 8)):
            new_length = min(64, new_length * 2) if delta > 0 else max(1, new_length // 2)
        if new_length == old_length:
            return
        self.silence(role)
        track.random_cycle = None
        if new_length < old_length:
            track.stash = track.pattern[new_length:] + track.stash
            track.pattern = track.pattern[:new_length]
            track.lengths_stash = track.lengths[new_length:] + track.lengths_stash
            track.lengths = track.lengths[:new_length]
            if role == 'chords':
                roots = patterns.ARRANGER['chord_roots_16ths'][:old_length]
                if len(roots) < old_length:
                    roots.extend([None] * (old_length - len(roots)))
                self.chord_roots_stash = roots[new_length:] + self.chord_roots_stash
                patterns.ARRANGER['chord_roots_16ths'] = roots[:new_length]
        else:
            needed = new_length - old_length
            self._fill_hidden_tail(role)
            kept = min(needed, len(track.stash))
            track.pattern.extend(track.stash[:kept])
            track.stash = track.stash[kept:]
            track.lengths.extend(track.lengths_stash[:kept])
            track.lengths_stash = track.lengths_stash[kept:]
            if role == 'chords':
                patterns.ARRANGER['chord_roots_16ths'].extend(self.chord_roots_stash[:kept])
                self.chord_roots_stash = self.chord_roots_stash[kept:]

    def set_direction(self, role, direction):
        if role not in ('lead', 'bass', 'chords') or direction not in DIRECTIONS:
            raise ValueError('invalid direction')
        track = self.tracks[role]
        if track.direction != direction:
            self.silence(role)
            track.direction = direction
            track.random_cycle = None
            track.random_order = []

    def set_drum_genre(self, genre):
        if genre not in patterns.DRUM_GENRES:
            raise ValueError('unknown drum genre')
        self.silence('drum')
        self.drum_genre = genre
        self.tracks['drum'].pattern = patterns.generate_drums(
            genre, len(self.tracks['drum'].pattern))
        self.tracks['drum'].stash = []
        self.tracks['drum'].lengths = [1 if cell is not None else 0
                                       for cell in self.tracks['drum'].pattern]
        self.tracks['drum'].lengths_stash = []
        self._fill_hidden_tail('drum')
        self.tempo_start_bpm = self.bpm
        self.tempo_target_bpm = patterns.DRUM_TEMPOS[genre]
        if self.playing and self.tempo_target_bpm != self.bpm:
            self.tempo_start_step = self.step
        else:
            self.bpm = self.tempo_target_bpm
            self.tempo_start_step = None

    def set_bpm(self, bpm):
        self.bpm = clamp(bpm, 30, 240)
        self.tempo_start_bpm = self.bpm
        self.tempo_target_bpm = self.bpm
        self.tempo_start_step = None

    def _advance_tempo(self):
        if self.tempo_start_step is None:
            return
        progress = min(256, max(0, self.step - self.tempo_start_step + 1))
        delta = self.tempo_target_bpm - self.tempo_start_bpm
        offset = (abs(delta) * progress + 128) // 256
        self.bpm = self.tempo_start_bpm + (offset if delta >= 0 else -offset)
        if progress == 256:
            self.bpm = self.tempo_target_bpm
            self.tempo_start_step = None

    def set_drum_roll_beats(self, beats):
        self.drum_roll_beats = clamp((beats // 32) * 32, 0, 1024)
        self.roll_due_step = (self.step + self.drum_roll_beats * 4
                              if self.drum_roll_beats else None)

    def trigger_drum_roll(self):
        if not self.playing:
            return False
        self.roll_remaining = DRUM_FILL_STEPS
        self.roll_due_step = (self.step + self.drum_roll_beats * 4
                              if self.drum_roll_beats else None)
        return True

    def randomize_notes(self, role):
        self.silence(role)
        length = len(self.tracks[role].pattern)
        if role == 'drum':
            self.tracks[role].pattern = patterns.generate_drums(self.drum_genre, length)
        else:
            new, roots = self._extra_notes(role, length)
            self.tracks[role].pattern = new
            if role == 'chords':
                patterns.ARRANGER['chord_roots_16ths'] = roots
                self.chord_roots_stash = []
            self._ensure_start_note(role)
        self.tracks[role].stash = []
        self.tracks[role].lengths = [1 if cell is not None else 0
                                    for cell in self.tracks[role].pattern]
        self.tracks[role].lengths_stash = []
        self.tracks[role].random_cycle = None
        self._fill_hidden_tail(role)
        self._maybe_randomize_lengths(role)
        if role == 'drum':
            self.drum_muted['kick'] = False

    def randomize_preset(self, role):
        self.silence(role)
        track = self.tracks[role]
        if self.preset_preferences is None:
            track.bank, track.program = move_preset(
                role, 0, 0, random.randint(0, sum(PROGRAM_COUNTS[role]) - 1))
        else:
            track.bank, track.program = self.preset_preferences.choose(role, track.channel)
        self.sink.preset(track)

    def step_preset(self, role, delta):
        if role not in ROLES or delta == 0:
            return
        track = self.tracks[role]
        bank, program = move_preset(role, track.bank, track.program, delta)
        if (bank, program) != (track.bank, track.program):
            self.silence(role)
            track.bank, track.program = bank, program
            self.sink.preset(track)

    def randomize_all(self):
        self.pending_load = None
        self.mix_state = None
        self.auto_plan = None
        self.auto_state = None
        self.auto_due_step = self.step + AUTO_WAIT_STEPS if self.auto_play else None
        self.phase_origin = self.step
        self.track_origins = {role: self.step for role in ROLES}
        for role in ROLES:
            self.silence(role)
        # Main RANDOMIZE starts a fresh countdown for both drum effects.
        # Keep the set's chosen breakdown duration unchanged.
        self.roll_remaining = 0
        self.break_remaining = 0
        self.roll_due_step = (self.step + self.drum_roll_beats * 4
                              if self.drum_roll_beats else None)
        self.break_due_step = self.step + (DRUM_BREAK_BEATS - self.break_duration_beats) * 4
        self.scale_name = random.choice(patterns.camelot_compatible_keys(self.scale_name))
        self.scale = patterns.SCALE_PRESETS[self.scale_name][:]
        self.generate_all()
        for role in ('lead', 'bass', 'chords'):
            self._maybe_randomize_lengths(role)
        for role in ROLES:
            self.randomize_preset(role)
        for role in ('lead', 'bass', 'chords'):
            self.tracks[role].muted = random.choice((True, False))
        for part in PARTS:
            self.drum_muted[part] = random.choice((True, False))
        self.drum_muted['kick'] = False

    def set_auto_play(self, enabled):
        enabled = bool(enabled)
        if self.auto_play == enabled:
            return
        self.auto_play = enabled
        if enabled:
            self.auto_due_step = self.step + AUTO_WAIT_STEPS if self.playing else None
        else:
            self.auto_plan = None
            self.auto_due_step = None
        # An in-progress transition finishes so its four loops share one key.

    def auto_signature(self):
        return (self.scale_name, self.drum_genre, self.chord_size,
                tuple((len(self.tracks[role].pattern), self.tracks[role].channel,
                       self.tracks[role].octave, self.tracks[role].transpose,
                       self.tracks[role].direction) for role in ROLES))

    def prepare_auto_steps(self, result):
        """Build a compatible set off the MIDI callback, one track per slice."""
        signature = self.auto_signature()
        source_key = self.scale_name
        keys = [name for name in patterns.camelot_compatible_keys(source_key)
                if name != source_key]
        target_key = random.choice(keys or [source_key])
        staged = Engine(_SilentSink(), generate=False)
        staged.scale_name = target_key
        staged.scale = patterns.SCALE_PRESETS[target_key][:]
        staged.drum_genre = self.drum_genre
        staged.bpm = self.bpm
        staged.chord_size = self.chord_size
        staged.cv_role = self.cv_role
        staged.preset_preferences = self.preset_preferences
        roots = []
        for role in ('chords', 'bass', 'lead', 'drum'):
            current = self.tracks[role]
            target = staged.tracks[role]
            target.channel = current.channel
            target.octave = current.octave
            target.transpose = current.transpose
            target.direction = current.direction
            target.pattern = [None] * len(current.pattern)
            live_roots = patterns.ARRANGER['chord_roots_16ths']
            patterns.ARRANGER['chord_roots_16ths'] = roots
            try:
                staged.randomize_notes(role)
                roots = patterns.ARRANGER['chord_roots_16ths']
            finally:
                patterns.ARRANGER['chord_roots_16ths'] = live_roots
            staged.randomize_preset(role)
            target.muted = random.choice((True, False))
            if role == 'drum':
                for part in PARTS:
                    staged.drum_muted[part] = random.choice((True, False))
                staged.drum_muted['kick'] = False
            yield
        snapshot = {}
        steps = staged.snapshot_steps(snapshot)
        while True:
            live_roots = patterns.ARRANGER['chord_roots_16ths']
            patterns.ARRANGER['chord_roots_16ths'] = roots
            done = False
            try:
                next(steps)
            except StopIteration:
                done = True
            finally:
                roots = patterns.ARRANGER['chord_roots_16ths']
                patterns.ARRANGER['chord_roots_16ths'] = live_roots
            if done:
                break
            yield
        result['value'] = {'signature': signature, 'old_key': source_key,
                           'new_key': target_key, 'saved': snapshot['value'],
                           'order': random.sample(ROLES, len(ROLES))}

    def _auto_handoff(self, role, saved):
        self._restore_track(role, saved['tracks'][role], keep_channel=True)
        if role == 'chords':
            patterns.ARRANGER['chord_roots_16ths'] = saved['chord_roots'][:len(self.tracks[role].pattern)]
            self.chord_roots_stash = saved.get('chord_roots_stash', [])[:len(self.tracks[role].stash)]
        elif role == 'drum':
            self.drum_muted = saved['drum_muted'].copy()
        if self.on_auto:
            self.on_auto(role)

    def _advance_auto(self, roll_started, break_started):
        if self.pending_load or self.mix_state:
            return
        state = self.auto_state
        if state is None and self.auto_play and self.auto_due_step is not None and self.step >= self.auto_due_step:
            plan = self.auto_plan
            if plan is not None and plan['signature'] == self.auto_signature():
                self.auto_plan = None
                state = {'old_key': plan['old_key'], 'new_key': plan['new_key'],
                         'saved': plan['saved'], 'order': plan['order'],
                         'index': 0, 'next_at': self.step}
                self.auto_state = state
                if self.on_auto:
                    self.on_auto('start')
            elif plan is not None:
                self.auto_plan = None
        if state is None or self.step < state['next_at']:
            return
        role = state['order'][state['index']]
        if role == 'drum' and not (roll_started if self.drum_roll_beats else break_started):
            return
        self._auto_handoff(role, state['saved'])
        state['index'] += 1
        if state['index'] == len(state['order']):
            self.scale_name = state['new_key']
            self.scale = patterns.SCALE_PRESETS[self.scale_name][:]
            self.auto_state = None
            self.auto_due_step = self.step + AUTO_WAIT_STEPS if self.auto_play else None
            if self.on_auto:
                self.on_auto('complete')
        else:
            state['next_at'] = self.step + AUTO_HANDOFF_STEPS

    def randomize_track(self, role):
        """Randomize this track's notes and preset, then make it audible."""
        if role not in ROLES:
            raise ValueError('unknown track')
        self.randomize_notes(role)
        self.randomize_preset(role)
        self.tracks[role].muted = False
        if role == 'drum':
            for part in PARTS:
                self.drum_muted[part] = random.choice((True, False))
            self.drum_muted['kick'] = False

    def toggle_mute(self, role):
        self.silence(role)
        self.tracks[role].muted = not self.tracks[role].muted

    def toggle_part(self, part):
        self.drum_muted[part] = not self.drum_muted[part]
        self.silence('drum')

    def randomize_part(self, part):
        self.silence('drum')
        track = self.tracks['drum']
        for start in range(0, len(track.pattern), 16):
            bar = track.pattern[start:start+16]
            while len(bar) < 16:
                bar.append(None)
            patterns.drum_randomize_part_free(bar, part)
            patterns.drum_cleanup(bar)
            track.pattern[start:start+min(16, len(track.pattern)-start)] = bar[:min(16, len(track.pattern)-start)]
        self._sync_lengths('drum')
        self.drum_muted['kick'] = False

    def snapshot_steps(self, result):
        def serial_cells(cells):
            return [None if cell is None else
                    [list(item) if isinstance(item, tuple) else item for item in cell]
                    if isinstance(cell, list) else cell for cell in cells]
        tracks = {}
        for role in ROLES:
            t = self.tracks[role]
            self._sync_lengths(role)
            tracks[role] = {
                'channel': t.channel, 'muted': t.muted, 'octave': t.octave,
                'transpose': t.transpose, 'bank': t.bank, 'program': t.program,
                'direction': t.direction,
                'pattern': serial_cells(t.pattern),
                'stash': serial_cells(t.stash),
                'lengths': t.lengths[:],
                'lengths_stash': t.lengths_stash[:],
            }
            yield
        result['value'] = {
            'format': 'amyboard-loop-set', 'version': 1,
            'scale_name': self.scale_name, 'bpm': self.bpm,
            'chord_size': self.chord_size,
            'drum_roll_beats': self.drum_roll_beats,
            'break_duration_beats': self.break_duration_beats,
            'drum_genre': self.drum_genre,
            'tracks': tracks, 'drum_muted': self.drum_muted.copy(),
            'cv_role': self.cv_role,
            'chord_roots': patterns.ARRANGER['chord_roots_16ths'][:],
            'chord_roots_stash': self.chord_roots_stash[:],
        }

    def snapshot(self):
        result = {}
        for _ in self.snapshot_steps(result):
            pass
        return result['value']

    def queue_load(self, name, saved, mix=None):
        self.auto_plan = None
        self.auto_state = None
        self.auto_due_step = None
        self.mix_state = None
        if not self.playing:
            self._apply_saved(name, saved)
            return self.step
        if mix is None:
            mix = self.mix_enabled
        if mix:
            # Start the incoming bass on a beat; later handoffs are beat-based.
            boundary = ((self.step + 3) // 4) * 4
            self.pending_load = (boundary, name, saved, True)
            return boundary
        period = 1
        for role in ROLES:
            length = len(self.tracks[role].pattern)
            period = period * length // gcd(period, length)
        elapsed = self.step - self.phase_origin
        boundary = self.phase_origin + max(period, ((elapsed + period - 1) // period) * period)
        self.pending_load = (boundary, name, saved, False)
        return boundary

    def _restore_track(self, role, source, keep_channel=False):
        t = self.tracks[role]
        self.silence(role)
        if hasattr(self.sink, 'release_channel'):
            self.sink.release_channel(t.channel)
        channel = t.channel
        for field in ('channel', 'muted', 'octave', 'transpose', 'bank', 'program'):
            setattr(t, field, source[field])
        if keep_channel:
            # Existing routing stays collision-free while old/new tracks coexist.
            t.channel = channel
        if not valid_preset(role, t.bank, t.program):
            counts = PROGRAM_COUNTS[role]
            t.bank = clamp(t.bank, 0, len(counts) - 1)
            t.program = clamp(t.program, 0, counts[t.bank] - 1)
        t.direction = source.get('direction', 'FORWARD')
        t.random_cycle = None
        t.random_order = []
        def restored_cells(cells):
            return [None if cell is None else
                    [tuple(item) for item in cell] if role == 'drum'
                    else cell[:] if isinstance(cell, list) else cell
                    for cell in cells]
        t.pattern = restored_cells(source['pattern'][:64])
        t.stash = restored_cells(source.get('stash', [])[:max(0, 64 - len(t.pattern))])
        t.lengths = source.get('lengths', [1 if cell is not None else 0
                                            for cell in t.pattern])[:len(t.pattern)]
        t.lengths_stash = source.get('lengths_stash', [1 if cell is not None else 0
                                                        for cell in t.stash])[:len(t.stash)]
        self.track_origins[role] = self.step
        self.sink.preset(t)

    def _apply_saved(self, name, saved):
        for role in ROLES:
            self.silence(role)
        for role in ROLES:
            self._restore_track(role, saved['tracks'][role])
        self.scale_name = saved['scale_name']
        self.scale = patterns.SCALE_PRESETS[self.scale_name][:]
        self.chord_size = saved.get('chord_size', 4)
        self.bpm = saved['bpm']
        self.tempo_start_bpm = self.bpm
        self.tempo_target_bpm = self.bpm
        self.tempo_start_step = None
        self.drum_genre = saved['drum_genre']
        self.drum_roll_beats = saved.get('drum_roll_beats', 128)
        self.break_duration_beats = saved.get('break_duration_beats', self.break_duration_beats)
        self.drum_muted = saved['drum_muted'].copy()
        patterns.ARRANGER['chord_roots_16ths'] = saved['chord_roots'][:len(self.tracks['chords'].pattern)]
        self.chord_roots_stash = saved.get('chord_roots_stash', [])[:len(self.tracks['chords'].stash)]
        self.set_cv_role(saved['cv_role'])
        self.phase_origin = self.step
        self.roll_due_step = (self.step + self.drum_roll_beats * 4
                              if self.drum_roll_beats else None)
        self.break_due_step = self.step + (DRUM_BREAK_BEATS - self.break_duration_beats) * 4
        self.roll_remaining = self.break_remaining = 0
        self.pending_load = None
        self.mix_state = None
        self.auto_due_step = self.step + AUTO_WAIT_STEPS if self.auto_play else None
        if self.on_load:
            self.on_load(name)

    def _mix_in(self, role, saved):
        self._restore_track(role, saved['tracks'][role], keep_channel=True)
        if role == 'chords':
            patterns.ARRANGER['chord_roots_16ths'] = saved['chord_roots'][:len(self.tracks['chords'].pattern)]
            self.chord_roots_stash = saved.get('chord_roots_stash', [])[:len(self.tracks['chords'].stash)]
        if self.on_mix:
            self.on_mix('MIXING IN ' + NAMES[role])

    def _finish_mix(self, saved):
        self.scale_name = saved['scale_name']
        self.scale = patterns.SCALE_PRESETS[self.scale_name][:]
        self.chord_size = saved.get('chord_size', 4)
        self.bpm = saved['bpm']
        self.tempo_start_bpm = self.tempo_target_bpm = self.bpm
        self.tempo_start_step = None
        self.drum_genre = saved['drum_genre']
        self.drum_roll_beats = saved.get('drum_roll_beats', 128)
        self.break_duration_beats = saved.get('break_duration_beats', self.break_duration_beats)
        self.drum_muted = saved['drum_muted'].copy()
        self.set_cv_role(saved['cv_role'])
        # Change saved MIDI channels together after every old track is gone.
        # Releasing them first avoids note-offs on a newly reassigned channel.
        changed = [role for role in ROLES if
                   self.tracks[role].channel != saved['tracks'][role]['channel']]
        for role in changed:
            self.silence(role)
            if hasattr(self.sink, 'release_channel'):
                self.sink.release_channel(self.tracks[role].channel)
        for role in changed:
            self.tracks[role].channel = saved['tracks'][role]['channel']
            self.sink.preset(self.tracks[role])
        self.phase_origin = self.step
        self.roll_due_step = (self.step + self.drum_roll_beats * 4
                              if self.drum_roll_beats else None)
        self.break_due_step = self.step + (DRUM_BREAK_BEATS - self.break_duration_beats) * 4
        self.auto_due_step = self.step + AUTO_WAIT_STEPS if self.auto_play else None

    def _position_at(self, track, step):
        length = len(track.pattern)
        direction = track.direction
        if direction == 'REVERSE':
            return length - 1 - step % length
        if direction == 'PING PONG':
            period = max(1, length * 2 - 2)
            position = step % period
            return position if position < length else period - position
        if direction == 'RANDOM':
            cycle, position = divmod(step, length)
            if track.random_cycle != cycle or len(track.random_order) != length:
                order = list(range(length))
                for index in range(length - 1, 0, -1):
                    other = random.randint(0, index)
                    order[index], order[other] = order[other], order[index]
                track.random_order = order
                track.random_cycle = cycle
            return track.random_order[position]
        return step % length

    def notes_at(self, track, step):
        if track.muted or not track.pattern:
            return []
        position = self._position_at(track, step)
        cell = track.pattern[position]
        if cell is None and track.role != 'drum' and len(track.lengths) == len(track.pattern):
            distance_limit = (step % len(track.pattern) if track.direction == 'RANDOM'
                              else len(track.pattern) * 2 - 2 if track.direction == 'PING PONG'
                              else len(track.pattern) - 1)
            for distance in range(1, distance_limit + 1):
                previous = self._position_at(track, step - distance)
                prior = track.pattern[previous]
                if prior is not None:
                    if track.lengths[previous] > distance:
                        cell = prior
                    break
        if cell is None:
            return []
        items = cell if isinstance(cell, list) else [cell]
        result = []
        seen = set()
        shift = track.transpose + track.octave * 12
        for item in items:
            note, velocity = item if isinstance(item, tuple) else (item, 100)
            if track.role == 'drum' and any(
                    self.drum_muted[part] and note in patterns.DRUM_PARTS[part] for part in PARTS):
                continue
            note = clamp(note + shift, 0, 127)
            if note not in seen:
                result.append((note, velocity))
                seen.add(note)
        return result

    def _with_ghost_note(self, notes, step):
        """Occasional quiet hits on the eighth-note offbeat, not saved in loops."""
        if step % 4 != 2 or self.tracks['drum'].muted or random.random() >= 0.35:
            return notes
        sounding = {note for note, _ in notes}
        candidates = [note for note, part in ((38, 'snare'), (37, 'rimshot'),
                      (39, 'clap'), (41, 'toms'), (45, 'toms'), (42, 'hats'))
                      if not self.drum_muted[part] and note not in sounding]
        if candidates:
            notes.append((random.choice(candidates), random.randint(24, 55)))
        return notes

    def _available_drum_notes(self, sounding):
        return [note for note, part in ((38, 'snare'), (37, 'rimshot'),
                (39, 'clap'), (41, 'toms'), (45, 'toms'), (50, 'toms'),
                (42, 'hats'), (46, 'hats'), (49, 'cymbals'))
                if not self.drum_muted[part] and note not in sounding]

    def _kick_break_notes(self, base, index):
        notes = [(note, velocity) for note, velocity in base if note != 36]
        if index % 2 == 0:
            candidates = self._available_drum_notes({note for note, _ in notes})
            if candidates:
                notes.append((random.choice(candidates), random.randint(45, 95)))
        return notes

    def _roll_notes(self, index):
        if not self.tracks['drum'].muted:
            candidates = self._available_drum_notes(set())
            if candidates:
                if 39 in candidates and random.random() < 0.25:
                    note = 39
                else:
                    others = [candidate for candidate in candidates if candidate != 39]
                    note = (38 if 38 in others and index % 4 != 3 else
                            random.choice(others or candidates))
                return [(note, min(127, 55 + index * 4))]
        return []

    def advance(self):
        if not self.playing:
            return
        if self.pending_load and self.step >= self.pending_load[0]:
            _, name, saved, mix = self.pending_load
            self.pending_load = None
            if mix:
                self._mix_in('bass', saved)
                self.mix_state = {'name': name, 'saved': saved, 'stage': 'lead',
                                  'lead_at': self.step + 32 * 4,
                                  'chords_at': self.step + 64 * 4}
            else:
                self._apply_saved(name, saved)
        self._advance_tempo()
        break_started = False
        if self.step >= self.break_due_step:
            self.break_remaining = self.break_duration_beats * 4
            self.break_due_step = self.step + DRUM_BREAK_BEATS * 4
            break_started = True
        roll_started = self.roll_remaining == DRUM_FILL_STEPS
        if self.roll_due_step is not None and self.step >= self.roll_due_step:
            self.roll_remaining = DRUM_FILL_STEPS
            self.roll_due_step = self.step + self.drum_roll_beats * 4
            roll_started = True
        mix = self.mix_state
        if mix is not None:
            stage = mix['stage']
            if stage == 'lead' and self.step >= mix['lead_at']:
                self._mix_in('lead', mix['saved'])
                mix['stage'] = 'chords'
            elif stage == 'chords' and self.step >= mix['chords_at']:
                self._mix_in('chords', mix['saved'])
                mix['stage'] = 'drum'
            elif stage == 'drum' and (roll_started if self.drum_roll_beats
                                       else break_started):
                self._mix_in('drum', mix['saved'])
                self._finish_mix(mix['saved'])
                mix['stage'] = 'complete'
                mix['complete_at'] = self.step + (DRUM_FILL_STEPS if roll_started
                                                  else self.break_remaining)
            elif stage == 'complete' and self.step >= mix['complete_at']:
                self.mix_state = None
                if self.on_mix:
                    self.on_mix('MIX COMPLETE!')
        self._advance_auto(roll_started, break_started)
        for role in ROLES:
            track = self.tracks[role]
            notes = self.notes_at(track, self.step - self.track_origins[role])
            if role == 'drum':
                if self.roll_remaining:
                    notes = self._roll_notes(DRUM_FILL_STEPS - self.roll_remaining)
                elif self.break_remaining and not track.muted:
                    notes = self._kick_break_notes(notes, self.break_duration_beats * 4 - self.break_remaining)
                else:
                    notes = self._with_ghost_note(notes, self.step - self.track_origins[role])
            if notes != track.active or role == 'drum':
                self.silence(role)
                # Pitch must settle before gate rises.
                self._mirror(role, notes)
                for note, velocity in notes:
                    self.sink.note_on(track, note, velocity)
                    if self.on_note:
                        self.on_note(role, note, velocity)
                track.active = notes
        if self.roll_remaining:
            self.roll_remaining -= 1
        if self.break_remaining:
            self.break_remaining -= 1
        self.step += 1

    def release_drums(self):
        # Half-step percussion gates give repeated hits a real low interval.
        self.silence('drum')
