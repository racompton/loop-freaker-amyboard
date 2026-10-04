"""Musical generators ported from racompton/midi-generator/midi_looper.py.
Source blob: 0fa04f2d690f1e444bb839adbad824cc09a43158 (main, 2026-10-03).
Desktop I/O removed; random helpers support MicroPython's smaller random module.
"""
import loop_random as random

STEPS_PER_BAR = 16


CONFIG = {
    "midi": {"bpm": 124, "step_resolution": 4, "lead_octave_shift_start": 0},
    "chords": {
        "loop_bars_choices": [2, 4, 8],
        "loop_bars_weights": [0.3, 0.5, 0.2],
        "register_low": 48,   # C3
        "register_high": 76,  # E5
        "allow_arps": False,  # chords as block voicings only
        "allow_rests": True,
        "rest_prob": 0.12,
        "anticipation_prob": 0.15,
        "extensions_prob": {"p7": 0.45, "add9": 0.35, "sus": 0.15},
        "rhythm_presets": [[16], [8, 8], [4, 4, 4, 4], [3, 1, 4, 8]],
        "rhythm_weights": [0.2, 0.3, 0.35, 0.15],
    },
    "bass": {
        "loop_bars_choices": [1, 2, 4],
        "loop_bars_weights": [0.5, 0.35, 0.15],
        "follow_chords": True,
        "register_low": 36,
        "register_high": 55,
        "eighth_bounce_prob": 0.25,
        "passing_prob_half": 0.7,
        "passing_prob_last": 0.6,
        "octave_hop_prob": 0.25,
    },
    "lead": {
        "loop_len_choices": [16, 8, 4, 32],  # 16ths
        "loop_len_weights": [0.5, 0.25, 0.125, 0.125],
        "note_prob": 0.3,
        "edit_count": 1,  # one occupied step per encoder detent
    },
    "drums": {
        "ghost_kick_prob": 0.25,
        "rimshot_prob_offbeat": 0.4,
        "hi_hat_density_closed": 0.2,
        "hi_hat_density_open": 0.2,
        "tom_prob_low": 0.10,
        "tom_prob_mid": 0.08,
        "tom_prob_high": 0.08,
        "crash_prob": 0.07,
        "ride_prob": 0.07,
    },
    "reharm": {
        "enable": True,
        "borrowed_iv_prob": 0.25,
        "sec_dom_before_V_prob": 0.20,
        "minor_IV_lift_prob": 0.20,
    }
}

DRUM_PARTS = {
    'kick': [36], 'snare': [38], 'clap': [39], 'rimshot': [37],
    'hats': [42, 46], 'toms': [41, 45, 50], 'cymbals': [49, 51],
}

SCALE_PRESETS = {
    'C Major': [60, 62, 64, 65, 67, 69, 71],
    'A Minor': [57, 59, 60, 62, 64, 65, 67],
    'C Minor': [60, 62, 63, 65, 67, 68, 70],
    'E Phrygian': [64, 65, 67, 69, 71, 72, 74],
    'G Pentatonic': [67, 69, 71, 74, 76],
    'E Blues': [64, 67, 69, 70, 71, 74],
    'D Dorian': [62, 64, 65, 67, 69, 71, 72],
    'F Lydian': [65, 67, 69, 71, 72, 74, 76],
    'B Locrian': [71, 72, 74, 76, 77, 79, 81],
    'G Mixolydian': [67, 69, 71, 72, 74, 76, 77],
    'A Harmonic Minor': [57, 59, 60, 62, 64, 65, 69],
    'C Melodic Minor': [60, 62, 63, 65, 67, 69, 71],
    'D Egyptian': [62, 65, 67, 69, 72],
    'F Hungarian Minor': [65, 67, 68, 72, 73, 74, 77],
    'G Whole Tone': [67, 69, 71, 73, 75, 77],
    'A Japanese': [57, 59, 60, 64, 65]
}

# Mixed In Key's Camelot order, 1 through 12. B is major; A is relative minor.
CAMELOT_MAJOR = ('B Major', 'F# Major', 'Db Major', 'Ab Major',
                 'Eb Major', 'Bb Major', 'F Major', 'C Major',
                 'G Major', 'D Major', 'A Major', 'E Major')
CAMELOT_MINOR = ('G# Minor', 'D# Minor', 'Bb Minor', 'F Minor',
                 'C Minor', 'G Minor', 'D Minor', 'A Minor',
                 'E Minor', 'B Minor', 'F# Minor', 'C# Minor')
CAMELOT_CODES = {}
_NOTE_PC = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
            'E': 4, 'F': 5, 'F#': 6, 'G': 7, 'G#': 8, 'Ab': 8,
            'A': 9, 'Bb': 10, 'B': 11}
for _number in range(1, 13):
    for _letter, _name, _intervals in (
            ('B', CAMELOT_MAJOR[_number - 1], (0, 2, 4, 5, 7, 9, 11)),
            ('A', CAMELOT_MINOR[_number - 1], (0, 2, 3, 5, 7, 8, 10))):
        CAMELOT_CODES[_name] = (_number, _letter)
        _root = 60 + _NOTE_PC[_name.split(' ')[0]]
        if _root > 68:
            _root -= 12
        if _name not in SCALE_PRESETS:
            SCALE_PRESETS[_name] = [_root + _step for _step in _intervals]

# Legacy nonstandard modes are not named on the Camelot wheel. Compare their
# notes with nearby standard keys; if no close match exists, keep that mode.
CAMELOT_PARENTS = {
    'E Phrygian': 'C Major', 'G Pentatonic': 'G Major',
    'E Blues': 'E Minor', 'D Dorian': 'C Major',
    'F Lydian': 'C Major', 'B Locrian': 'C Major',
    'G Mixolydian': 'C Major', 'A Harmonic Minor': 'A Minor',
    'C Melodic Minor': 'C Major', 'D Egyptian': 'D Minor',
    'F Hungarian Minor': 'F Minor', 'G Whole Tone': 'G Major',
    'A Japanese': 'A Minor',
}


def camelot_compatible_keys(current):
    candidates = [name for name in CAMELOT_CODES
                  if camelot_compatible(current, name)]
    return candidates or [current]


def camelot_compatible(current, candidate):
    if current == candidate:
        return True
    source_code = CAMELOT_CODES.get(CAMELOT_PARENTS.get(current, current))
    target_code = CAMELOT_CODES.get(CAMELOT_PARENTS.get(candidate, candidate))
    if source_code is None or target_code is None:
        return False
    number, letter = source_code
    allowed = ((number, letter), ((number - 2) % 12 + 1, letter),
               (number % 12 + 1, letter),
               (number, 'A' if letter == 'B' else 'B'))
    if target_code not in allowed:
        return False
    if current in CAMELOT_PARENTS or candidate in CAMELOT_PARENTS:
        source = set(scale_pitch_classes(SCALE_PRESETS[current]))
        target = set(scale_pitch_classes(SCALE_PRESETS[candidate]))
        return len(source.intersection(target)) >= min(len(source), len(target)) - 1
    return True

ARRANGER = {"chord_roots_16ths": []}

def clamp_note(n):
    return max(0, min(127, int(n)))

def is_prob(p): return random.random() < p

def scale_pitch_classes(scale):
    pcs = []
    for n in scale:
        pc = n % 12
        if pc not in pcs:
            pcs.append(pc)
    return pcs

def diatonic_triad_pcs(scale_pcs, degree):
    L = len(scale_pcs)
    root = scale_pcs[degree % L]
    third = scale_pcs[(degree + 2) % L]
    fifth = scale_pcs[(degree + 4) % L]
    triad = [root, third, fifth]
    p7 = CONFIG["chords"]["extensions_prob"]["p7"]
    p9 = CONFIG["chords"]["extensions_prob"]["add9"]
    ps = CONFIG["chords"]["extensions_prob"]["sus"]
    if is_prob(p7) and L >= 4: triad.append(scale_pcs[(degree + 6) % L])
    if is_prob(p9) and L >= 5: triad.append(scale_pcs[(degree + 1) % L])
    if is_prob(ps):
        triad[1] = scale_pcs[(degree + (1 if is_prob(0.5) else 3)) % L]
    out = []
    for pc in triad:
        if pc not in out: out.append(pc)
    return out


def chord_pcs_for_size(pcs, scale_pcs, degree, size):
    """Keep the generated chord's character, filling/trimming to exact size."""
    result = []
    for pc in pcs:
        if pc not in result:
            result.append(pc)
    for offset in (6, 1, 3, 5, 0, 2, 4):
        pc = scale_pcs[(degree + offset) % len(scale_pcs)]
        if pc not in result:
            result.append(pc)
        if len(result) >= size:
            break
    return result[:size]


def resize_chord_voicing(voicing, root_pc, scale, size):
    scale_pcs = scale_pitch_classes(scale)
    degree = scale_pcs.index(root_pc) if root_pc in scale_pcs else 0
    pcs = [root_pc] + [note % 12 for note in voicing if note % 12 != root_pc]
    pcs = chord_pcs_for_size(pcs, scale_pcs, degree, size)
    return voice_lead_to_register(voicing, pcs)

def voice_lead_to_register(prev_voicing, pcs, target_low=None, target_high=None):
    if not pcs: return []
    if target_low is None:  target_low = CONFIG["chords"]["register_low"]
    if target_high is None: target_high = CONFIG["chords"]["register_high"]
    center = round(sum(prev_voicing) / len(prev_voicing)) if prev_voicing else (target_low + target_high) // 2
    voiced = []
    for pc in pcs:
        k = round((center - pc) / 12); n = pc + 12 * k
        while n < target_low:  n += 12
        while n > target_high: n -= 12
        voiced.append(n)
    voiced.sort()
    while voiced and (max(voiced) - min(voiced)) > 16:
        if abs(max(voiced) - center) > abs(min(voiced) - center):
            i = voiced.index(max(voiced)); voiced[i] -= 12
        else:
            i = voiced.index(min(voiced)); voiced[i] += 12
        voiced.sort()
    return voiced

def guess_mode_from_name(name):
    n = (name or "").lower()
    if any(k in n for k in ['minor','phrygian','locrian','dorian','hungarian','egyptian']):
        return 'minorish'
    return 'majorish'

PROGRESSIONS_MAJOR = [[0,4,5,3],[0,5,3,4],[0,1,4,0],[0,3,4,0],[0,4,0,5]]

PROGRESSIONS_MINOR = [[0,5,3,4],[0,4,5,0],[0,6,5,4],[0,3,4,0]]

MARKOV_NEXT = {0:[4,3,5,2],3:[4,0,5],4:[0,3,5],5:[3,4,0],2:[4,0],6:[5,0],1:[4,0]}

def evolve_progression(seq, bars_wanted):
    out = []; i = 0; last = seq[0]
    while len(out) < bars_wanted:
        d = seq[i] if i < len(seq) else random.choice(MARKOV_NEXT.get(last,[0,4,3]))
        if is_prob(0.2) and out: out[-1], d = d, out[-1]
        out.append(d); last = d; i += 1
    return out

def drum_remove_part_at_step(step_list, part_notes):
    if not step_list: return step_list
    new = []
    for item in step_list:
        note_val = item[0] if isinstance(item, tuple) else item
        if note_val not in part_notes:
            new.append(item)
    return new if new else None

def drum_add_kick(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        step = i % STEPS_PER_BAR
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['kick'])
        if step in [0,4,8,12]:
            pattern[i] = (pattern[i] or []); pattern[i].append((36, random.randint(100,120)))
    ghost_step = random.choice([13,14])
    if random.random() < CONFIG["drums"]["ghost_kick_prob"]:
        i = ghost_step; pattern[i] = (pattern[i] or []); pattern[i].append((36, random.randint(50,70)))

def drum_add_snare(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        step = i % STEPS_PER_BAR
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['snare'])
        if step in [4,12]:
            pattern[i] = (pattern[i] or []); pattern[i].append((38, random.randint(90,120)))

def drum_add_clap(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        step = i % STEPS_PER_BAR
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['clap'])
        if step in [4,12]:
            pattern[i] = (pattern[i] or []); pattern[i].append((39, random.randint(90,120)))

def drum_add_rimshot(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        step = i % STEPS_PER_BAR
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['rimshot'])
        if step not in [0,4,8,12] and random.random() < CONFIG["drums"]["rimshot_prob_offbeat"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((37, random.randint(50,100)))

def drum_add_hats(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['hats'])
    hi_hat_loop = [None] * 4
    while all(step is None for step in hi_hat_loop):
        for i in range(4):
            r = random.random()
            if r < CONFIG["drums"]["hi_hat_density_closed"]:
                hi_hat_loop[i] = (42, random.randint(60,110))
            elif r < CONFIG["drums"]["hi_hat_density_closed"] + CONFIG["drums"]["hi_hat_density_open"]:
                hi_hat_loop[i] = (46, random.randint(80,120))
            else:
                hi_hat_loop[i] = None
    for i in range(LOOP_LENGTH):
        hit = hi_hat_loop[i % 4]
        if hit:
            pattern[i] = (pattern[i] or []); pattern[i].append(hit)

def drum_add_toms(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['toms'])
        r = random.random()
        if r < CONFIG["drums"]["tom_prob_low"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((41, random.randint(80,120)))
        elif r < CONFIG["drums"]["tom_prob_low"] + CONFIG["drums"]["tom_prob_mid"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((45, random.randint(80,120)))
        elif r < CONFIG["drums"]["tom_prob_low"] + CONFIG["drums"]["tom_prob_mid"] + CONFIG["drums"]["tom_prob_high"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((50, random.randint(80,120)))

def drum_add_cymbals(pattern):
    LOOP_LENGTH = STEPS_PER_BAR
    for i in range(LOOP_LENGTH):
        pattern[i] = drum_remove_part_at_step(pattern[i], DRUM_PARTS['cymbals'])
        r = random.random()
        if r < CONFIG["drums"]["crash_prob"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((49, random.randint(90,127)))
        elif r < CONFIG["drums"]["crash_prob"] + CONFIG["drums"]["ride_prob"]:
            pattern[i] = (pattern[i] or []); pattern[i].append((51, random.randint(90,127)))

def drum_cleanup(pattern):
    for i in range(len(pattern)):
        if isinstance(pattern[i], list) and len(pattern[i]) == 0:
            pattern[i] = None

def drum_randomize_part_free(pattern, part):
    """Wipe the given part and place hits randomly across the bar (16 steps)."""
    LOOP_LENGTH = STEPS_PER_BAR
    notes = DRUM_PARTS[part]
    # remove existing occurrences of this part
    for i in range(LOOP_LENGTH):
        pattern[i] = drum_remove_part_at_step(pattern[i], notes)

    # density selection per part
    if part == 'hats':
        min_hits, max_hits = 6, 14
    elif part == 'kick':
        min_hits, max_hits = 2, 8
    elif part in ('snare', 'clap', 'rimshot'):
        min_hits, max_hits = 2, 8
    elif part == 'toms':
        min_hits, max_hits = 1, 6
    elif part == 'cymbals':
        min_hits, max_hits = 1, 4
    else:
        min_hits, max_hits = 2, 8

    hits = random.randint(min_hits, max_hits)
    chosen_steps = random.sample(range(LOOP_LENGTH), k=hits)

    for step in chosen_steps:
        if pattern[step] is None: pattern[step] = []
        note = random.choice(notes)
        vel = random.randint(50, 127)
        pattern[step].append((note, vel))

    # small chance to add a second hit at a random chosen step (cluster)
    if random.random() < 0.25:
        step = random.choice(chosen_steps)
        if pattern[step] is None: pattern[step] = []
        note = random.choice(notes)
        vel = random.randint(40, 120)
        pattern[step].append((note, vel))

def lead_add_notes_inplace(pattern, scale, count=None):
    if pattern is None or not isinstance(pattern, list):
        return
    if count is None: count = CONFIG["lead"]["edit_count"]
    empty_idxs = [i for i, v in enumerate(pattern) if v is None]
    if not empty_idxs:
        return
    k = min(count, len(empty_idxs))
    for i in random.sample(empty_idxs, k=k):
        pattern[i] = random.choice(scale)

def lead_remove_notes_inplace(pattern, count=None):
    if pattern is None or not isinstance(pattern, list):
        return
    if count is None: count = CONFIG["lead"]["edit_count"]
    note_idxs = [i for i, v in enumerate(pattern) if v is not None]
    if not note_idxs:
        return
    k = min(count, len(note_idxs))
    for i in random.sample(note_idxs, k=k):
        pattern[i] = None

def generate_pattern(scale, role, scale_name=None, chord_size=4):
    if role == 'drum':
        LOOP_LENGTH = STEPS_PER_BAR
        pattern = [None for _ in range(LOOP_LENGTH)]
        # default starter drum kit (can be overwritten by Q..U free randomizers)
        drum_add_kick(pattern)
        drum_add_snare(pattern)
        drum_add_rimshot(pattern)
        drum_add_hats(pattern)
        drum_add_toms(pattern)
        drum_add_cymbals(pattern)
        drum_cleanup(pattern)
        return pattern

    elif role == 'bass':
        bass_bars = random.choices(CONFIG["bass"]["loop_bars_choices"], weights=CONFIG["bass"]["loop_bars_weights"])[0]
        LOOP_LENGTH = bass_bars * STEPS_PER_BAR
        pattern = [None for _ in range(LOOP_LENGTH)]
        roots = ARRANGER.get("chord_roots_16ths") or []
        have_roots = len(roots) > 0
        LOW = CONFIG["bass"]["register_low"]; HIGH = CONFIG["bass"]["register_high"]

        def pick_passing(a, b):
            if a is None or b is None: return None
            step = random.choice([2,2,2,3,1]); direction = 1 if (b >= a) else -1
            cand = a + direction * step
            scale_set = {n % 12 for n in scale}
            options = [cand + k for k in (-1,0,1,2,-2) if ((cand + k) % 12) in scale_set]
            x = (min(options, key=lambda n: abs(n - a)) if options else cand)
            while x < LOW: x += 12
            while x > HIGH: x -= 12
            return x

        for bar in range(bass_bars):
            base = bar * STEPS_PER_BAR
            if have_roots and len(roots) >= base + 1:
                r0 = roots[base] if base < len(roots) else None
                r8 = roots[base + 8] if (base + 8) < len(roots) else r0
            else:
                low_notes = [n - 24 for n in scale if LOW <= n - 24 <= HIGH]
                r0 = random.choice(low_notes) if low_notes else LOW + 4
                r8 = random.choice(low_notes) if low_notes else r0

            if random.random() < CONFIG["bass"]["octave_hop_prob"] and r8 is not None:
                r8 = r8 + random.choice([-12, 12])
                if r8 < LOW:  r8 += 12
                if r8 > HIGH: r8 -= 12

            pattern[base + 0] = r0
            pattern[base + 8] = r8
            if random.random() < CONFIG["bass"]["passing_prob_half"]:
                pattern[base + 4] = pick_passing(r0, r8)
            if random.random() < CONFIG["bass"]["passing_prob_last"]:
                pattern[base + 12] = pick_passing(r8, r0)

            if random.random() < CONFIG["bass"]["eighth_bounce_prob"]:
                for i in (2,6,10,14):
                    if random.random() < 0.5:
                        anchor = r0 if i < 8 else r8
                        if anchor is not None:
                            step = random.choice([-2,2])
                            bounce = anchor + step
                            while bounce < LOW:  bounce += 12
                            while bounce > HIGH: bounce -= 12
                            pattern[base + i] = bounce

        return pattern

    elif role == 'lead':
        LOOP_LENGTH = STEPS_PER_BAR
        pattern = [None for _ in range(LOOP_LENGTH)]
        lead_loop_len = random.choices(CONFIG["lead"]["loop_len_choices"], weights=CONFIG["lead"]["loop_len_weights"])[0]
        lead_loop_len = max(1, min(lead_loop_len, LOOP_LENGTH))
        for i in range(lead_loop_len):
            if random.random() < CONFIG["lead"]["note_prob"]:
                note = random.choice(scale)
                pattern[i] = note
        for i in range(lead_loop_len, LOOP_LENGTH):
            pattern[i] = pattern[i % lead_loop_len]
        return pattern

    elif role == 'chords':
        scale_pcs = scale_pitch_classes(scale)
        mode = guess_mode_from_name(scale_name or "")
        base = random.choice(PROGRESSIONS_MINOR if mode == 'minorish' else PROGRESSIONS_MAJOR)

        chord_bars = random.choices(CONFIG["chords"]["loop_bars_choices"], weights=CONFIG["chords"]["loop_bars_weights"])[0]
        chord_steps = chord_bars * STEPS_PER_BAR
        degrees = evolve_progression(base, chord_bars)

        if CONFIG["reharm"]["enable"]:
            for i, d in enumerate(degrees):
                if mode == 'majorish':
                    if d == 3 and random.random() < CONFIG["reharm"]["borrowed_iv_prob"]:
                        degrees[i] = 2
                    if d == 4 and i > 0 and random.random() < CONFIG["reharm"]["sec_dom_before_V_prob"]:
                        degrees[i-1] = 1
                else:
                    if d == 3 and random.random() < CONFIG["reharm"]["minor_IV_lift_prob"]:
                        degrees[i] = 4

        rp = CONFIG["chords"]["rhythm_presets"]; rw = CONFIG["chords"]["rhythm_weights"]
        cells = [rp[random.choices(range(len(rp)), weights=rw)[0]] for _ in range(chord_bars)]

        pattern = [None] * chord_steps
        ARRANGER["chord_roots_16ths"] = [None] * chord_steps
        prev_voicing = None

        step_ptr = 0
        for bar_idx, deg in enumerate(degrees):
            pcs = diatonic_triad_pcs(scale_pcs, deg)
            pcs = chord_pcs_for_size(pcs, scale_pcs, deg, chord_size)
            voicing = voice_lead_to_register(prev_voicing, pcs)
            prev_voicing = voicing[:]

            root_pc = pcs[0]
            bass_root = root_pc
            LOW = CONFIG["bass"]["register_low"]; HIGH = CONFIG["bass"]["register_high"]
            while bass_root < LOW:  bass_root += 12
            while bass_root > HIGH: bass_root -= 12

            raw = cells[bar_idx]
            segments = raw if sum(raw) == STEPS_PER_BAR else [4,4,4,4]

            idx_in_bar = 0
            for seg in segments:
                dur = max(1, seg)
                do_rest = (CONFIG["chords"]["allow_rests"] and random.random() < CONFIG["chords"]["rest_prob"])
                anticipate = (not do_rest) and random.random() < CONFIG["chords"]["anticipation_prob"] and dur > 2
                start_offset = -1 if anticipate else 0

                start_step = step_ptr + max(0, idx_in_bar + start_offset)
                end_step = min(step_ptr + idx_in_bar + dur, step_ptr + STEPS_PER_BAR)

                if not do_rest:
                    for s in range(start_step, end_step):
                        pattern[s] = voicing[:]
                    for s in range(start_step, end_step):
                        ARRANGER["chord_roots_16ths"][s] = bass_root

                idx_in_bar += dur
            step_ptr += STEPS_PER_BAR

        return pattern

    return [None] * STEPS_PER_BAR


# These are deliberate starting tempos within broad genre ranges, not hard rules.
DRUM_GENRES = ('HOUSE', 'TECHNO', 'BREAKS', 'ELECTRO', "DRUM'N'BASS")
DRUM_TEMPOS = {'HOUSE': 124, "DRUM'N'BASS": 174, 'TECHNO': 130,
               'BREAKS': 135, 'ELECTRO': 140}


def drum_genre_bar(genre):
    """Generate one 16-step bar using the original GM drum note map."""
    if genre == 'HOUSE':
        return generate_pattern(None, 'drum')
    if genre not in DRUM_GENRES:
        raise ValueError('unknown drum genre')
    bar = [None] * STEPS_PER_BAR

    def hit(step, note, low=70, high=115):
        if bar[step] is None:
            bar[step] = []
        bar[step].append((note, random.randint(low, high)))

    if genre == "DRUM'N'BASS":
        # Half-time-feeling break at a fast 174 BPM: syncopated kick, 2/4 snare.
        for step in (0, 10):
            hit(step, 36, 102, 124)
        if is_prob(0.45):
            hit(random.choice((6, 15)), 36, 50, 88)
        for step in (4, 12):
            hit(step, 38, 105, 127)
        for step in (2, 6, 8, 10, 14):
            hit(step, 42, 48, 95)
        for step in (3, 7, 11, 15):
            if is_prob(0.50):
                hit(step, 42, 35, 69)
        if is_prob(0.35):
            hit(15, 38, 38, 65)
    elif genre == 'TECHNO':
        for step in (0, 4, 8, 12):
            hit(step, 36, 105, 127)
        for step in (2, 6, 10, 14):
            hit(step, 46 if is_prob(0.25) else 42, 65, 106)
        for step in (4, 12):
            if is_prob(0.65):
                hit(step, 39, 60, 98)
        for step in range(16):
            if is_prob(0.13):
                hit(step, random.choice((37, 41, 45)), 40, 80)
    elif genre == 'BREAKS':
        for step in (0, 7, 10):
            hit(step, 36, 87, 120)
        for step in (4, 12):
            hit(step, 38, 92, 126)
        for step in (0, 2, 5, 7, 8, 10, 13, 15):
            hit(step, 42, 48, 103)
        for step in (3, 11, 15):
            if is_prob(0.45):
                hit(step, 38, 32, 67)
        if is_prob(0.45):
            hit(14, 46, 75, 110)
    else:  # ELECTRO: machine-funk syncopation and 808-style accents.
        for step in (0, 6, 10, 15):
            hit(step, 36, 89, 123)
        for step in (4, 12):
            hit(step, 38, 90, 118)
        for step in (2, 6, 10, 14):
            hit(step, 42, 50, 93)
        for step in (3, 11):
            hit(step, 37, 55, 94)
        if is_prob(0.50):
            hit(12, 39, 62, 105)
        if is_prob(0.35):
            hit(15, 46, 70, 105)
    return bar


def generate_drums(genre, length):
    out = []
    while len(out) < length:
        out.extend(drum_genre_bar(genre))
    return out[:length]
