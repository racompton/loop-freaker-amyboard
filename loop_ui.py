"""OLED menu state and debounced encoder gestures; no hardware imports."""
from loop_engine import (ROLES, NAMES, PARTS, DIRECTIONS, LEAD_STYLES, BASS_STYLES,
                         CHORD_STYLES, DRUM_STYLES, clamp)
from loop_preset_preferences import preset_number
from loop_settings import CLOCK_SOURCES
import loop_patterns as patterns

# Updated by tools/release_looper.py when a release is prepared.
LOOPER_VERSION = '0.9.0'


_SCALE_LABELS = {'Major': 'MAJ', 'Minor': 'MIN', 'Phrygian': 'PHR',
                 'Pentatonic': 'PENT', 'Blues': 'BLU', 'Dorian': 'DOR',
                 'Lydian': 'LYD', 'Locrian': 'LOC', 'Mixolydian': 'MIX',
                 'Harmonic Minor': 'HMIN', 'Melodic Minor': 'MMIN',
                 'Egyptian': 'EGY', 'Hungarian Minor': 'HMIN',
                 'Whole Tone': 'WHOLE', 'Japanese': 'JPN'}


def compact_key(name):
    root, mode = name.split(' ', 1)
    return (root + ' ' + _SCALE_LABELS.get(mode, mode[:4].upper()))[:7]


def preset_label(role, track):
    if role == 'chords':
        return 'P%d' % preset_number(role, track.bank, track.program,
                                     {role: track.program_counts})
    return 'B%d P%d' % (track.bank + 1, track.program + 1)


class Controls:
    def __init__(self, now=0, position=0, ticks_diff=None):
        self.diff = ticks_diff or (lambda a, b: a - b)
        self.position = position
        self.raw = False
        self.down = False
        self.changed = now
        self.started = now
        self.long_sent = False
        self.suppressed = False

    def poll(self, now, position, pressed, consume=False):
        delta = position - self.position
        self.position = position
        if pressed != self.raw:
            self.raw = pressed
            self.changed = now
        activity = bool(delta or pressed or self.down)
        if consume:
            self.suppressed = True
        click = long_press = False
        if self.diff(now, self.changed) >= 25 and self.down != self.raw:
            self.down = self.raw
            if self.down:
                self.started = now
                self.long_sent = False
            else:
                click = not self.long_sent and not self.suppressed
        if self.down and not self.long_sent and self.diff(now, self.started) >= 650:
            self.long_sent = True
            long_press = not self.suppressed
        if self.suppressed:
            delta = 0
            click = long_press = False
            if not self.raw and not self.down:
                self.suppressed = False
        return delta, click, long_press, activity


class Menu:
    def __init__(self, engine, now=0, ticks_diff=None, set_store=None):
        self.engine = engine
        self.set_store = set_store
        self.saved_names = []
        self.saved_keys = {}
        self.io_request = None
        self.busy = False
        self.delete_name = None
        self.diff = ticks_diff or (lambda a, b: a - b)
        self.page = 'main'
        self.index = 0
        self.stack = []
        self.editing = False
        self.genre_candidate = None
        self.direction_candidate = None
        self.channel_candidate = None
        self.clock_candidate = None
        self.settings_changed = None
        self.clock_changed = None
        self.random_request = None
        self.last_activity = now
        self.sleeping = False
        self.screensaver_enabled = False
        self.notice = ''
        self.notice_at = now
        self.bam_at = None
        self.dirty = True
        self.status_dirty = False

    def rows(self):
        if self.page == 'main':
            return ['RANDOMIZE', 'PERFORM', 'LOCKS', 'SYNTH', 'BASS', 'CHORD', 'DRUMS', 'SAVE', 'LOAD', 'SETTINGS', 'PLAY / STOP']
        if self.page in ROLES:
            track = self.engine.tracks[self.page]
            rows = ['RANDOMIZE', 'RANDOM NOTES', 'RANDOM PRESET',
                    'PRESET ' + preset_label(self.page, track),
                    'LOOP LENGTH']
            if self.page == 'lead':
                rows += ['VARIATION', 'STYLE', 'DENSITY']
            elif self.page == 'bass':
                rows += ['STYLE', 'DENSITY']
            elif self.page == 'chords':
                rows += ['STYLE', 'DENSITY']
            if self.page != 'drum':
                rows += ['DIRECTION', 'OCTAVE', '# of NOTES']
                if self.page == 'chords':
                    rows += ['CHORD SIZE']
            else:
                rows += ['GENRE', 'STYLE', 'DENSITY', 'GHOST NOTES', 'GHOST VELOCITY', 'GHOST ON MUTES', 'DRUM ROLL']
            rows += ['AUTO UNMUTED' if self.page == 'drum' and self.engine.auto_play
                     else 'UNMUTE' if self.engine.tracks[self.page].muted else 'MUTE']
            if self.page == 'drum':
                rows += ['DRUM PARTS']
            return rows
        if self.page == 'settings':
            return ['MIDI CHANNELS', 'PRESET BANKS', 'CLOCK SOURCE',
                    'CV OUTPUT FROM', 'TEMPO', 'SWING', 'MIX', 'AUTO PLAY',
                    'SCREENSAVER', 'VERSION ' + LOOPER_VERSION]
        if self.page == 'perform':
            rows = [('UNMUTE ' if self.engine.tracks[r].muted else 'MUTE ') + NAMES[r]
                    for r in ROLES]
            rows += [('UNMUTE KICK' if self.engine.drum_muted['kick'] else 'MUTE KICK'),
                     'VARIATION', 'DRUM ROLL', 'TEMPO', 'NEXT BAR']
            return rows
        if self.page == 'locks':
            return ['KEY'] + [NAMES[r] for r in ROLES] + ['P ' + NAMES[r] for r in ROLES]
        if self.page == 'channels':
            return [NAMES[r] for r in ROLES]
        if self.page == 'banks':
            return [NAMES[r] for r in ROLES]
        if self.page.startswith('banks:'):
            counts = self.engine.program_counts[self.page[6:]]
            return ['BANK COUNT'] + ['BANK %d PRESETS' % (i + 1)
                                     for i in range(len(counts))]
        if self.page == 'drum_roll':
            return ['TRIGGER NOW', 'AUTO EVERY', 'LENGTH']
        if self.page.startswith('preset:'):
            return ['SELECT PRESET', 'PREFER PRESET', 'DELETE PRESET', 'NORMAL PRESET']
        if self.page == 'load':
            return self.saved_names + ['DELETE...'] if self.saved_names else ['(NO SAVES)']
        if self.page == 'delete':
            return self.saved_names or ['(NO SAVES)']
        if self.page == 'delete_confirm':
            return ['CANCEL', 'DELETE SET']
        if self.page == 'parts':
            return [p.upper() for p in PARTS]
        if self.page.startswith('part:'):
            part = self.page[5:]
            return ['RANDOM NOTES', 'UNMUTE' if self.engine.drum_muted[part] else 'MUTE']
        return []

    def enter(self, page):
        self.stack.append((self.page, self.index))
        self.page, self.index, self.editing = page, 0, False
        self.genre_candidate = None
        self.direction_candidate = None
        self.channel_candidate = None
        self.clock_candidate = None
        if page in ('load', 'delete') and self.set_store:
            self.saved_names = self.set_store.list_names()
            if page == 'load':
                self.saved_keys = {}
        self.dirty = True

    def _saved_key(self, name):
        if name not in self.saved_keys:
            self.saved_keys[name] = (self.set_store.key_for(name)
                                     if hasattr(self.set_store, 'key_for') else None)
        return self.saved_keys[name]

    def _compatible_saved(self, name):
        key = self._saved_key(name)
        return key is not None and patterns.camelot_compatible(self.engine.scale_name, key)

    def _channel_in_use(self, role, channel):
        return any(track.channel == channel for other, track in self.engine.tracks.items()
                   if other != role)

    def back(self):
        self.editing = False
        self.genre_candidate = None
        self.direction_candidate = None
        self.channel_candidate = None
        self.clock_candidate = None
        if self.stack:
            self.page, self.index = self.stack.pop()
            self.index = min(self.index, len(self.rows()) - 1)
        self.dirty = True

    def show_notice(self, text, now):
        self.notice, self.notice_at = text, now
        self.dirty = True

    def clamp_index(self):
        rows = self.rows()
        self.index = max(0, min(self.index, len(rows) - 1)) if rows else 0
        return rows

    def selected_value(self):
        e = self.engine
        rows = self.clamp_index()
        if not rows:
            return None
        row = rows[self.index]
        if self.page in ROLES:
            if row == 'STYLE':
                return getattr(e, {'lead': 'lead_style', 'bass': 'bass_style',
                                   'chords': 'chord_style', 'drum': 'drum_style'}[self.page])
            if row == 'DENSITY':
                return '%d%%' % getattr(e, {'lead': 'lead_density', 'bass': 'bass_density',
                                            'chords': 'chord_density', 'drum': 'drum_density'}[self.page])
            if row.startswith('PRESET '):
                track = e.tracks[self.page]
                if e.preset_preferences:
                    return e.preset_preferences.status(self.page, track.channel,
                                                       track.bank, track.program)
                return 'NORMAL'
            if row == 'LOOP LENGTH':
                track = e.tracks[self.page]
                return 'HOLD' if self.page == 'chords' and track.chord_hold else '%d STEPS' % len(track.pattern)
            if row == 'DIRECTION':
                return self.direction_candidate if self.editing else e.tracks[self.page].direction
            if row == 'OCTAVE':
                return '%+d SEMITONES' % (e.tracks[self.page].octave * 12 + e.tracks[self.page].transpose)
            if row == '# of NOTES':
                return str(sum(n is not None for n in e.tracks[self.page].pattern))
            if row == 'CHORD SIZE':
                return str(e.chord_size)
            if row == 'GENRE':
                genre = self.genre_candidate if self.editing else e.drum_genre
                return '%s %d' % (genre, patterns.DRUM_TEMPOS[genre] if self.editing else e.bpm)
            if self.page == 'drum' and row == 'GHOST NOTES':
                return '%d%%' % e.ghost_percent
            if self.page == 'drum' and row == 'GHOST VELOCITY':
                return str(e.ghost_velocity)
            if self.page == 'drum' and row == 'GHOST ON MUTES':
                return 'ON' if e.ghost_on_muted else 'OFF'
        if self.page == 'channels':
            role = ROLES[self.index]
            channel = self.channel_candidate if self.editing else e.tracks[role].channel
            return 'CH%d IN USE' % channel if self.editing and self._channel_in_use(role, channel) else 'CH%d' % channel
        if self.page == 'locks':
            value = (e.key_locked if self.index == 0 else
                     e.track_locks[ROLES[self.index - 1]] if self.index <= 4 else
                     e.preset_locks[ROLES[self.index - 5]])
            return 'LOCKED' if value else 'UNLOCKED'
        if self.page == 'perform':
            if self.index < 4:
                return 'MUTED' if e.tracks[ROLES[self.index]].muted else 'ON'
            if self.index == 4:
                return 'MUTED' if e.drum_muted['kick'] else 'ON'
            if row == 'TEMPO':
                return '%d BPM' % e.bpm
            if row == 'NEXT BAR':
                return 'ON' if e.quantize_actions else 'OFF'
        if self.page.startswith('banks:'):
            counts = e.program_counts[self.page[6:]]
            return str(len(counts)) if self.index == 0 else str(counts[self.index - 1])
        if self.page == 'drum_roll' and row == 'AUTO EVERY':
            return '%d BEATS' % e.drum_roll_beats if e.drum_roll_beats else 'OFF'
        if self.page == 'drum_roll' and row == 'LENGTH':
            return '%d BEATS' % e.roll_length_beats
        if self.page == 'load' and row in self.saved_names:
            return 'KEY MATCH' if self._compatible_saved(row) else 'OTHER KEY'
        if self.page.startswith('preset:'):
            role = self.page[7:]
            track = e.tracks[role]
            if row == 'SELECT PRESET':
                return preset_label(role, track)
            status = (e.preset_preferences.status(role, track.channel, track.bank, track.program)
                      if e.preset_preferences else 'NORMAL')
            return 'CH%d %s' % (track.channel, status)
        if self.page == 'settings':
            if row == 'CLOCK SOURCE':
                return self.clock_candidate if self.editing else getattr(e, 'clock_source', 'INTERNAL')
            if row == 'CV OUTPUT FROM':
                return NAMES.get(e.cv_role, 'OFF')
            if row == 'TEMPO':
                return '%d BPM' % e.bpm
            if row == 'SWING':
                return ('50%', '58%', '67%', '75%')[e.swing]
            if row == 'MIX':
                return 'ON' if e.mix_enabled else 'OFF'
            if row == 'AUTO PLAY':
                return 'ON' if e.auto_play else 'OFF'
            if row == 'SCREENSAVER':
                return 'ON' if self.screensaver_enabled else 'OFF'
            if row == 'PLAY / STOP':
                return 'PLAYING' if e.playing else 'STOPPED'
        return None

    def status_line(self):
        e = self.engine
        if e.auto_play:
            if not e.playing:
                return 'AUTO PAUSED'
            if all(e.track_locks.values()):
                return 'AUTO LOCKED'
            due, role = e.auto_next_change()
            beats = max(0, (due - e.step + 3) // 4) if due is not None else 0
            return '%dB>%s' % (beats, NAMES[role] if role else 'PREP')
        due = e.roll_due_step
        beats = max(0, (due - e.step + 3) // 4) if due is not None else None
        flags = ' '.join(('*' if not e.tracks[role].muted else ' ') + initial
                         for role, initial in (('lead', 'S'), ('bass', 'B'),
                                               ('chords', 'C'), ('drum', 'D')))
        status = 'R%s %s' % (str(beats) if beats is not None else '--', flags)
        if len(status) > 16:
            status = 'R%s %s' % (str(beats), flags.replace(' ', ''))
        return status[:16]

    def adjust(self, delta, now):
        e = self.engine
        rows = self.clamp_index()
        if not rows:
            return
        row = rows[self.index]
        if self.page in ROLES and row == 'LOOP LENGTH':
            e.set_loop_length(self.page, delta)
        elif self.page in ROLES and row == 'STYLE':
            styles = {'lead': LEAD_STYLES, 'bass': BASS_STYLES,
                      'chords': CHORD_STYLES, 'drum': DRUM_STYLES}[self.page]
            field = {'lead': 'lead_style', 'bass': 'bass_style',
                     'chords': 'chord_style', 'drum': 'drum_style'}[self.page]
            current = getattr(e, field)
            value = styles[(styles.index(current) + delta) % len(styles)]
            if self.page == 'chords' and current != value:
                e.silence('chords')
            setattr(e, field, value)
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page in ROLES and row == 'DENSITY':
            field = {'lead': 'lead_density', 'bass': 'bass_density',
                     'chords': 'chord_density', 'drum': 'drum_density'}[self.page]
            setattr(e, field, clamp(getattr(e, field) + delta * 5, 10, 90))
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'perform' and row == 'TEMPO':
            e.set_bpm(e.bpm + delta)
        elif self.page in ROLES and row == 'DIRECTION':
            current = self.direction_candidate or e.tracks[self.page].direction
            self.direction_candidate = DIRECTIONS[(DIRECTIONS.index(current) + delta) % len(DIRECTIONS)]
        elif self.page in ROLES and row == 'OCTAVE':
            e.set_transpose(self.page, e.tracks[self.page].transpose + 3 * delta)
        elif self.page in ROLES and row == '# of NOTES':
            e.edit_notes(self.page, delta)
        elif self.page == 'chords' and row == 'CHORD SIZE':
            e.set_chord_size(e.chord_size + delta)
        elif self.page == 'drum' and row == 'GENRE':
            genres = patterns.DRUM_GENRES
            current = self.genre_candidate or e.drum_genre
            self.genre_candidate = genres[(genres.index(current) + delta) % len(genres)]
        elif self.page == 'drum_roll' and row == 'AUTO EVERY':
            if e.auto_play:
                self.show_notice('AUTO ROLL 128', now)
            else:
                e.set_drum_roll_beats(e.drum_roll_beats + delta * 32)
        elif self.page == 'drum_roll' and row == 'LENGTH':
            e.roll_length_beats = clamp(e.roll_length_beats + delta, 4, 32)
        elif self.page == 'channels':
            role = ROLES[self.index]
            current = self.channel_candidate if self.channel_candidate is not None else e.tracks[role].channel
            self.channel_candidate = (current - 1 + delta) % 16 + 1
            if self._channel_in_use(role, self.channel_candidate):
                self.show_notice('CHANNEL IN USE', now)
        elif self.page.startswith('banks:'):
            role = self.page[6:]
            counts = list(e.program_counts[role])
            if self.index == 0:
                target = clamp(len(counts) + delta, 1, 16)
                counts = (counts + [128] * (target - len(counts)))[:target]
            else:
                counts[self.index - 1] = clamp(counts[self.index - 1] + delta, 1, 128)
            e.set_program_counts(role, counts)
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'drum' and row == 'GHOST NOTES':
            e.set_ghost_percent(e.ghost_percent + delta * 5)
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'drum' and row == 'GHOST VELOCITY':
            e.ghost_velocity = clamp(e.ghost_velocity + delta, 1, 127)
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'drum' and row == 'GHOST ON MUTES':
            e.ghost_on_muted = delta > 0
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'settings' and row == 'CLOCK SOURCE':
            current = self.clock_candidate or getattr(e, 'clock_source', 'INTERNAL')
            self.clock_candidate = CLOCK_SOURCES[(CLOCK_SOURCES.index(current) + delta) % len(CLOCK_SOURCES)]
        elif self.page == 'settings' and row == 'CV OUTPUT FROM':
            sources = (None,) + ROLES
            e.set_cv_role(sources[(sources.index(e.cv_role) + delta) % len(sources)])
        elif self.page == 'settings' and row == 'TEMPO':
            e.set_bpm(e.bpm + delta)
        elif self.page == 'settings' and row == 'SWING':
            e.swing = clamp(e.swing + delta, 0, 3)
            if self.settings_changed:
                self.settings_changed(now)
        elif self.page == 'settings' and row == 'MIX':
            e.mix_enabled = delta > 0
        elif self.page == 'settings' and row == 'AUTO PLAY':
            e.set_auto_play(delta > 0)
        elif self.page == 'settings' and row == 'SCREENSAVER':
            self.screensaver_enabled = delta > 0
            if not self.screensaver_enabled:
                self.sleeping = False
        elif self.page.startswith('preset:') and row == 'SELECT PRESET':
            e.step_preset(self.page[7:], delta)

    def click(self, now):
        e = self.engine
        rows = self.clamp_index()
        if not rows:
            return
        row = rows[self.index]
        if self.editing:
            if self.page in ROLES and row == 'DIRECTION' and self.direction_candidate:
                e.set_direction(self.page, self.direction_candidate)
            if self.page == 'drum' and row == 'GENRE' and self.genre_candidate:
                e.set_drum_genre(self.genre_candidate)
                self.show_notice(self.genre_candidate, now)
            if self.page == 'channels':
                role = ROLES[self.index]
                channel = self.channel_candidate
                if channel is not None and self._channel_in_use(role, channel):
                    self.show_notice('CHANNEL IN USE', now)
                    return
                if channel is not None:
                    e.set_channel(role, channel)
            if self.page == 'settings' and row == 'CLOCK SOURCE' and self.clock_candidate:
                if self.clock_changed:
                    self.clock_changed(self.clock_candidate, now)
            self.editing = False
            self.genre_candidate = None
            self.direction_candidate = None
            self.channel_candidate = None
            self.clock_candidate = None
        elif self.page == 'main':
            if self.index == 0:
                if self.random_request is not None:
                    self.random_request(now)
                else:
                    e.randomize_all()
                    if self.settings_changed:
                        self.settings_changed(now)
                    self.bam_at = now
            elif row == 'SAVE':
                if self.set_store is None:
                    self.show_notice('NO STORAGE', now)
                elif self.io_request is not None:
                    self.io_request('save', None, now)
                else:
                    try:
                        name = self.set_store.save(e.snapshot())
                        self.show_notice('SAVED ' + name, now)
                    except Exception as exc:
                        print('Loop save failed:', exc)
                        self.show_notice('SAVE FAILED', now)
            elif row == 'LOAD':
                self.enter('load')
            elif row == 'SETTINGS':
                self.enter('settings')
            elif row == 'PERFORM':
                self.enter('perform')
            elif row == 'LOCKS':
                self.enter('locks')
            elif row == 'PLAY / STOP':
                e.stop() if e.playing else e.start()
            else:
                self.enter(next(role for role in ROLES if NAMES[role] == row))
        elif self.page in ROLES:
            if row == 'RANDOMIZE':
                if e.randomize_track(self.page):
                    self.bam_at = now
                    if self.settings_changed:
                        self.settings_changed(now)
                else:
                    self.show_notice('TRACK LOCKED', now)
            elif row == 'RANDOM NOTES':
                if e.track_locks[self.page]:
                    self.show_notice('TRACK LOCKED', now)
                else:
                    e.randomize_notes(self.page)
                    self.show_notice('NEW NOTES!', now)
            elif row == 'RANDOM PRESET':
                if e.track_locks[self.page] or e.preset_locks[self.page]:
                    self.show_notice('PRESET LOCKED', now)
                else:
                    e.randomize_preset(self.page)
                    self.show_notice('NEW PRESET!', now)
            elif row == 'VARIATION':
                self.show_notice(('QUEUED' if e.quantize_actions and e.playing else 'VARIATION')
                                 if e.perform('variation') else 'TRACK LOCKED', now)
            elif row.startswith('PRESET '):
                self.enter('preset:' + self.page)
            elif row in ('LOOP LENGTH', 'DIRECTION', 'OCTAVE', '# of NOTES', 'CHORD SIZE', 'GENRE', 'GHOST NOTES', 'GHOST VELOCITY', 'GHOST ON MUTES', 'STYLE', 'DENSITY'):
                self.editing = True
                if row == 'GENRE':
                    self.genre_candidate = e.drum_genre
                elif row == 'DIRECTION':
                    self.direction_candidate = e.tracks[self.page].direction
            elif row == 'DRUM ROLL':
                self.enter('drum_roll')
            elif row == 'DRUM PARTS':
                self.enter('parts')
            elif row == 'AUTO UNMUTED':
                self.show_notice('DRUMS STAY ON', now)
            else:
                e.toggle_mute(self.page)
        elif self.page == 'perform':
            if self.index < 4:
                ok = e.perform('mute', ROLES[self.index])
            elif self.index == 4:
                ok = e.perform('kick')
            elif row == 'VARIATION':
                ok = e.perform('variation')
            elif row == 'DRUM ROLL':
                ok = e.perform('roll')
            elif row == 'NEXT BAR':
                e.quantize_actions = not e.quantize_actions
                return
            else:
                self.editing = True
                return
            self.show_notice(('QUEUED' if e.quantize_actions and e.playing else 'DONE')
                             if ok else 'UNAVAILABLE', now)
        elif self.page == 'locks':
            if e.auto_state or e.mix_state:
                self.show_notice('MIX IN PROGRESS', now)
            elif self.index == 0:
                e.key_locked = not e.key_locked
            elif self.index <= 4:
                role = ROLES[self.index - 1]
                e.track_locks[role] = not e.track_locks[role]
            else:
                role = ROLES[self.index - 5]
                e.preset_locks[role] = not e.preset_locks[role]
            e.auto_plan = None
        elif self.page == 'settings':
            if row == 'MIDI CHANNELS':
                self.enter('channels')
            elif row == 'PRESET BANKS':
                self.enter('banks')
            elif row.startswith('VERSION '):
                pass
            else:
                self.editing = True
                if row == 'CLOCK SOURCE':
                    self.clock_candidate = getattr(e, 'clock_source', 'INTERNAL')
        elif self.page == 'channels':
            self.editing = True
            self.channel_candidate = e.tracks[ROLES[self.index]].channel
        elif self.page == 'banks':
            self.enter('banks:' + ROLES[self.index])
        elif self.page.startswith('banks:'):
            self.editing = True
        elif self.page == 'drum_roll':
            if row == 'TRIGGER NOW':
                self.show_notice('ROLLING!' if e.trigger_drum_roll() else 'START PLAYBACK', now)
            else:
                self.editing = True
        elif self.page.startswith('preset:'):
            if row == 'SELECT PRESET':
                self.editing = True
            elif e.preset_preferences is None:
                self.show_notice('NO STORAGE', now)
            else:
                role = self.page[7:]
                track = e.tracks[role]
                status = {'PREFER PRESET': 'PREFERRED', 'DELETE PRESET': 'DELETED',
                          'NORMAL PRESET': 'NORMAL'}[row]
                if self.io_request is not None:
                    self.io_request('preset', (role, track.channel, track.bank,
                                               track.program, status), now)
                    self.back()
                    return
                try:
                    e.preset_preferences.set_status(role, track.channel,
                                                    track.bank, track.program, status)
                    self.show_notice(status + ' CH%d' % track.channel, now)
                    self.back()
                except Exception as exc:
                    print('Preset preference failed:', exc)
                    self.show_notice('PRESET FAILED', now)
        elif self.page == 'load':
            if not self.saved_names:
                self.show_notice('NO SAVES YET', now)
            elif row == 'DELETE...':
                self.enter('delete')
            elif self.io_request is not None:
                self.io_request('load', row, now)
                self.back()
            else:
                try:
                    boundary = e.queue_load(row, self.set_store.load(row))
                    self.show_notice('QUEUED ' + row if e.playing else 'LOADED ' + row, now)
                    self.back()
                except Exception as exc:
                    print('Loop load failed:', exc)
                    self.show_notice('LOAD FAILED', now)
        elif self.page == 'delete':
            if self.saved_names:
                self.delete_name = row
                self.enter('delete_confirm')
        elif self.page == 'delete_confirm':
            if self.index == 0:
                self.back()
            else:
                if self.io_request is not None:
                    name = self.delete_name
                    self.back()
                    self.back()
                    self.io_request('delete', name, now)
                    return
                try:
                    name = self.delete_name
                    self.set_store.delete(name)
                    if self.engine.pending_load and self.engine.pending_load[1] == name:
                        self.engine.pending_load = None
                    self.saved_names = self.set_store.list_names()
                    self.saved_keys.pop(name, None)
                    self.back()
                    self.back()
                    self.show_notice('DELETED ' + name, now)
                except Exception as exc:
                    print('Loop delete failed:', exc)
                    self.show_notice('DELETE FAILED', now)
        elif self.page == 'parts':
            self.enter('part:' + PARTS[self.index])
        elif self.page.startswith('part:'):
            part = self.page[5:]
            if self.index == 0:
                e.randomize_part(part)
                self.show_notice('NEW HITS!', now)
            else:
                e.toggle_part(part)

    def handle(self, now, delta=0, click=False, long_press=False, activity=False):
        touched = bool(activity or delta or click or long_press)
        if touched:
            self.bam_at = None
        if touched:
            self.last_activity = now
            self.dirty = True
        if self.sleeping:
            if touched:
                self.sleeping = False
            return  # wake only; retain exact page, cursor and edit mode
        if self.busy:
            return  # finish short storage slices before accepting another edit
        if long_press:
            self.back()
        elif click:
            self.click(now)
        elif delta:
            if self.editing:
                self.adjust(delta, now)
            else:
                self.index = (self.index + delta) % len(self.rows())
        if self.notice and self.diff(now, self.notice_at) >= 1100:
            self.notice = ''
            self.dirty = True
        if self.bam_at is not None and self.diff(now, self.bam_at) >= 640:
            self.bam_at = None
            self.dirty = True
        if not touched and self.screensaver_enabled and self.diff(now, self.last_activity) >= 60000:
            self.sleeping = True
            self.dirty = True

    def render(self, d):
        d.fill(0)
        self.clamp_index()
        title = NAMES.get(self.page, self.page.upper())
        if self.page == 'main':
            title = '~L00P FR34K3R~'
        elif self.page.startswith('preset:'):
            track = self.engine.tracks[self.page[7:]]
            title = 'PRESET ' + preset_label(self.page[7:], track)
        elif self.page == 'delete_confirm':
            title = 'DELETE ' + self.delete_name.split(' ')[0]
        elif self.page == 'drum_roll':
            title = 'DRUM ROLL'
        elif self.page.startswith('part:'):
            title = self.page[5:].upper()
        title = title[:16]
        d.text(title, (128 - len(title) * 8) // 2 if self.page == 'main' else 0, 0, 255)
        d.hline(0, 12, 128, 255)
        rows = self.rows()
        start = max(0, min(self.index - 4, len(rows) - 6))
        for index in range(start, min(start + 6, len(rows))):
            y = 18 + (index - start) * 13
            selected = index == self.index
            if selected:
                d.fill_rect(0, y - 2, 128, 12, 255)
            if self.page == 'load' and rows[index] in self.saved_names and self._compatible_saved(rows[index]):
                color = 0 if selected else 255
                d.hline(0, y - 2, 128, color)
                d.hline(0, y + 9, 128, color)
            prefix = '*' if selected and self.editing else ('>' if selected else ' ')
            label = rows[index] if self.page in ('load', 'delete') and len(rows[index]) == 16 else prefix + rows[index]
            d.text(label[:16], 0, y, 0 if selected else 255)
        value = self.selected_value()
        if self.page == 'main' and self.engine.auto_state is not None:
            state = self.engine.auto_state
            keys = compact_key(state['old_key']) + '>' + compact_key(state['new_key'])
            d.text(keys[:16], max(0, (128 - len(keys) * 8) // 2), 104, 255)
            footer = None
        elif self.notice:
            footer = self.notice
        elif value is not None:
            footer = ('SET: ' if self.editing else 'VALUE: ') + value
        elif self.page in ROLES:
            t = self.engine.tracks[self.page]
            footer = 'CH%d %s %s' % (t.channel, preset_label(self.page, t),
                                     'MUTE' if t.muted else 'ON')
        elif self.page == 'delete_confirm':
            footer = self.delete_name
        elif self.page == 'main':
            # Three independent columns keep the genre centered as BPM grows.
            d.text(compact_key(self.engine.scale_name), 0, 104, 255)
            d.text(self.engine.drum_genre[0], 60, 104, 255)
            bpm = str(self.engine.bpm)
            d.text(bpm, 128 - 8 * len(bpm), 104, 255)
            footer = None
        elif self.engine.pending_load:
            footer = 'NEXT ' + self.engine.pending_load[1]
        else:
            footer = self.engine.scale_name
        if footer is not None:
            d.text(footer[:16], 0, 104, 255)
        d.text(self.status_line()[:16], 0, 118, 255)
        self.dirty = False
        self.status_dirty = False

    def render_status(self, d):
        """Change just the countdown row; keep the rest of the menu framebuffer."""
        d.fill_rect(0, 116, 128, 12, 0)
        d.text(self.status_line()[:16], 0, 118, 255)
        self.status_dirty = False

    def bam_active(self, now):
        return self.bam_at is not None and 0 <= self.diff(now, self.bam_at) < 640

    def render_bam(self, d, now):
        """One still frame; the menu redraws only after it expires."""
        radius = 43
        directions = ((0, -100), (38, -92), (71, -71), (92, -38),
                      (100, 0), (92, 38), (71, 71), (38, 92),
                      (0, 100), (-38, 92), (-71, 71), (-92, 38),
                      (-100, 0), (-92, -38), (-71, -71), (-38, -92))
        d.fill(0)
        for index, (dx, dy) in enumerate(directions):
            nx, ny = directions[(index + 1) % len(directions)]
            tip = radius + (5 if index % 3 == 0 else 0)
            x, y = 64 + dx * tip // 100, 61 + dy * tip // 100
            ix = 64 + (dx + nx) * 9 // 100
            iy = 61 + (dy + ny) * 9 // 100
            next_tip = radius + (5 if (index + 1) % 3 == 0 else 0)
            tx, ty = 64 + nx * next_tip // 100, 61 + ny * next_tip // 100
            d.line(x, y, ix, iy, 255)
            d.line(ix, iy, tx, ty, 255)
            if index % 2 == 0:
                d.line(64 + dx * (tip + 4) // 100, 61 + dy * (tip + 4) // 100,
                       64 + dx * (tip + 13) // 100, 61 + dy * (tip + 13) // 100, 255)
        d.fill_rect(43, 52, 46, 18, 255)
        d.text('BAM!', 50, 57, 0)
        self.dirty = False
        self.status_dirty = False


class NoteGalaxy:
    """Bounded note-powered comet trails, drum shockwaves and dancing lanes."""
    def __init__(self):
        self.particles = []
        self.rings = []
        self.frame = 0
        self.levels = [0, 0, 0, 0]

    def note(self, role, note, velocity):
        lane = ROLES.index(role)
        self.levels[lane] = velocity
        # Deterministic animation: never consume the music generator's RNG.
        self.particles.append([4 + note * 119 // 127, 108, (note % 5) - 2,
                               -2 - velocity // 32, 18 + velocity // 10, lane])
        if len(self.particles) > 40:
            self.particles.pop(0)
        if role == 'drum':
            self.rings.append([64, 60, 2])
            if len(self.rings) > 5:
                self.rings.pop(0)

    def render(self, d, engine):
        d.fill(0)
        self.frame += 1
        for i in range(22):
            x = (i * 47 + (self.frame // (1 + i % 3))) % 128
            y = (i * 31 + self.frame * (1 + i % 2)) % 112
            d.pixel(x, y, 255)
        alive = []
        for p in self.particles:
            x, y, vx, vy, life, lane = p
            d.line(x, y, x - vx * 3, y - vy * 3, 255)
            d.fill_rect(x - 1, y - 1, 3, 3, 255)
            p[0] += vx
            p[1] += vy
            p[4] -= 1
            if p[4] > 0 and 0 <= p[0] < 128 and 0 <= p[1] < 114:
                alive.append(p)
        self.particles = alive
        alive = []
        for ring in self.rings:
            x, y, r = ring
            # Expanding diamonds are crisp on both monochrome and grayscale OLEDs.
            d.line(x, y-r, x+r, y, 255)
            d.line(x+r, y, x, y+r, 255)
            d.line(x, y+r, x-r, y, 255)
            d.line(x-r, y, x, y-r, 255)
            ring[2] += 4
            if r < 56:
                alive.append(ring)
        self.rings = alive
        d.fill_rect(0, 112, 128, 16, 0)
        for lane, role in enumerate(ROLES):
            x = lane * 32
            height = self.levels[lane] // 7
            self.levels[lane] = max(0, self.levels[lane] - 9)
            d.fill_rect(x + 2, 111-height, 27, height, 255)
            d.text(('SYN', 'BAS', 'CHO', 'DRM')[lane], x + 2, 118, 255)
            # Sustained notes continue to glow after their attack particles fade.
            for note, _ in engine.tracks[role].active:
                d.rect(x + 2 + note % 23, 90 - note % 60, 4, 4, 255)
        d.fill_rect((engine.step % 16) * 8, 113, 7, 2, 255)
