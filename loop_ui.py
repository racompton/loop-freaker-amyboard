"""OLED menu state and debounced encoder gestures; no hardware imports."""
from loop_engine import ROLES, NAMES, PARTS, DIRECTIONS, clamp
from loop_preset_preferences import preset_number
import loop_patterns as patterns


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
        return 'P%d' % preset_number(role, track.bank, track.program)
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
        self.last_activity = now
        self.sleeping = False
        self.screensaver_enabled = False
        self.notice = ''
        self.notice_at = now
        self.bam_at = None
        self.dirty = True

    def rows(self):
        if self.page == 'main':
            return ['RANDOMIZE', 'SYNTH', 'BASS', 'CHORD', 'DRUMS', 'SAVE', 'LOAD', 'SETTINGS']
        if self.page in ROLES:
            track = self.engine.tracks[self.page]
            rows = ['RANDOMIZE', 'RANDOM NOTES', 'RANDOM PRESET',
                    'PRESET ' + preset_label(self.page, track),
                    'LOOP LENGTH']
            if self.page != 'drum':
                rows += ['DIRECTION', 'OCTAVE', '# of NOTES']
                if self.page == 'chords':
                    rows += ['CHORD SIZE']
            else:
                rows += ['GENRE', 'DRUM ROLL']
            rows += ['AUTO UNMUTED' if self.page == 'drum' and self.engine.auto_play
                     else 'UNMUTE' if self.engine.tracks[self.page].muted else 'MUTE']
            if self.page == 'drum':
                rows += ['DRUM PARTS']
            return rows
        if self.page == 'settings':
            return ['MIDI CHANNELS', 'CV SOURCE', 'TEMPO', 'MIX', 'AUTO PLAY',
                    'SCREENSAVER', 'PLAY / STOP']
        if self.page == 'channels':
            return [NAMES[r] for r in ROLES]
        if self.page == 'drum_roll':
            return ['TRIGGER NOW', 'AUTO EVERY']
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

    def back(self):
        self.editing = False
        self.genre_candidate = None
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
            if row.startswith('PRESET '):
                track = e.tracks[self.page]
                if e.preset_preferences:
                    return e.preset_preferences.status(self.page, track.channel,
                                                       track.bank, track.program)
                return 'NORMAL'
            if row == 'LOOP LENGTH':
                length = len(e.tracks[self.page].pattern)
                return '%d STEPS' % length
            if row == 'DIRECTION':
                return e.tracks[self.page].direction
            if row == 'OCTAVE':
                return '%+d' % e.tracks[self.page].octave
            if row == '# of NOTES':
                return str(sum(n is not None for n in e.tracks[self.page].pattern))
            if row == 'CHORD SIZE':
                return str(e.chord_size)
            if row == 'GENRE':
                genre = self.genre_candidate if self.editing else e.drum_genre
                return '%s %d' % (genre, patterns.DRUM_TEMPOS[genre] if self.editing else e.bpm)
        if self.page == 'channels':
            return str(e.tracks[ROLES[self.index]].channel)
        if self.page == 'drum_roll' and row == 'AUTO EVERY':
            return '%d BEATS' % e.drum_roll_beats if e.drum_roll_beats else 'OFF'
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
            if row == 'CV SOURCE':
                return NAMES.get(e.cv_role, 'OFF')
            if row == 'TEMPO':
                return '%d BPM' % e.bpm
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
        elif self.page in ROLES and row == 'DIRECTION':
            current = e.tracks[self.page].direction
            e.set_direction(self.page, DIRECTIONS[(DIRECTIONS.index(current) + delta) % len(DIRECTIONS)])
        elif self.page in ROLES and row == 'OCTAVE':
            e.set_octave(self.page, e.tracks[self.page].octave + delta)
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
        elif self.page == 'channels':
            role = ROLES[self.index]
            if not e.set_channel(role, e.tracks[role].channel + delta):
                self.show_notice('CHANNEL IN USE', now)
        elif self.page == 'settings' and row == 'CV SOURCE':
            sources = (None,) + ROLES
            e.set_cv_role(sources[(sources.index(e.cv_role) + delta) % len(sources)])
        elif self.page == 'settings' and row == 'TEMPO':
            e.set_bpm(e.bpm + delta)
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
            if self.page == 'drum' and row == 'GENRE' and self.genre_candidate:
                e.set_drum_genre(self.genre_candidate)
                self.show_notice(self.genre_candidate, now)
            self.editing = False
            self.genre_candidate = None
        elif self.page == 'main':
            if self.index == 0:
                e.randomize_all()
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
            else:
                self.enter(ROLES[self.index - 1])
        elif self.page in ROLES:
            if row == 'RANDOMIZE':
                e.randomize_track(self.page)
                self.bam_at = now
            elif row == 'RANDOM NOTES':
                e.randomize_notes(self.page)
                self.show_notice('NEW NOTES!', now)
            elif row == 'RANDOM PRESET':
                e.randomize_preset(self.page)
                self.show_notice('NEW PRESET!', now)
            elif row.startswith('PRESET '):
                self.enter('preset:' + self.page)
            elif row in ('LOOP LENGTH', 'DIRECTION', 'OCTAVE', '# of NOTES', 'CHORD SIZE', 'GENRE'):
                self.editing = True
                if row == 'GENRE':
                    self.genre_candidate = e.drum_genre
            elif row == 'DRUM ROLL':
                self.enter('drum_roll')
            elif row == 'DRUM PARTS':
                self.enter('parts')
            elif row == 'AUTO UNMUTED':
                self.show_notice('DRUMS STAY ON', now)
            else:
                e.toggle_mute(self.page)
        elif self.page == 'settings':
            if self.index == 0:
                self.enter('channels')
            elif row == 'PLAY / STOP':
                e.stop() if e.playing else e.start()
            else:
                self.editing = True
        elif self.page == 'channels':
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
