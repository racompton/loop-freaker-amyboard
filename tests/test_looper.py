import random
import tempfile
import os
import json
import unittest
from unittest.mock import patch

from loop_engine import Engine, ROLES
from loop_ui import Menu, Controls, NoteGalaxy
from loop_sets import Sets, validate_set, ADJECTIVES, NOUNS
from loop_preset_preferences import PresetPreferences, PROGRAM_COUNTS, move_preset, valid_preset
import loop_patterns
import loop_random


class Sink:
    def __init__(self):
        self.events = []

    def note_on(self, track, note, velocity):
        self.events.append(('on', track.channel, note, velocity))

    def note_off(self, track, note):
        self.events.append(('off', track.channel, note))

    def preset(self, track):
        self.events.append(('preset', track.channel, track.bank, track.program))

    def cv(self, volts, channel):
        self.events.append(('cv', channel, volts))

    def transport(self, playing):
        self.events.append(('transport', playing))

    def set_internal(self, role, tracks):
        self.events.append(('internal', role))


class SequencerTests(unittest.TestCase):
    def setUp(self):
        random.seed(123)
        self.sink = Sink()
        self.e = Engine(self.sink)
        for track in self.e.tracks.values():
            track.pattern = [None] * 16
            track.muted = False
        self.e.start()
        self.sink.events.clear()

    def test_tied_chords_and_cv_lowest_pitch(self):
        self.e.set_cv_role('chords')
        self.e.tracks['chords'].pattern = [[64, 60, 67], [64, 60, 67], None]
        self.e.advance()
        self.assertIn(('cv', 0, 0.0), self.sink.events)
        self.assertIn(('cv', 1, 5), self.sink.events)
        self.sink.events.clear()
        self.e.advance()
        self.assertEqual([], self.sink.events)
        self.e.advance()
        self.assertEqual(3, len([x for x in self.sink.events if x[0] == 'off']))
        self.assertIn(('cv', 1, 0), self.sink.events)

    def test_zero_note_is_a_real_note(self):
        self.e.tracks['lead'].pattern = [0, None]
        self.e.advance()
        self.e.advance()
        self.assertIn(('on', 4, 0, 100), self.sink.events)
        self.assertIn(('off', 4, 0), self.sink.events)

    def test_channel_change_turns_off_old_channel_and_prevents_collision(self):
        self.e.tracks['lead'].pattern = [60]
        self.e.advance()
        self.assertFalse(self.e.set_channel('lead', 3))
        self.assertEqual(4, self.e.tracks['lead'].channel)
        self.assertTrue(self.e.set_channel('lead', 16))
        self.assertIn(('off', 4, 60), self.sink.events)
        self.assertIn(('cv', 1, 0), self.sink.events)
        self.e.advance()
        self.assertIn(('on', 16, 60, 100), self.sink.events)

    def test_octave_edit_releases_actual_transposed_pitch(self):
        self.e.tracks['lead'].pattern = [60]
        self.e.set_octave(1)
        self.e.advance()
        self.e.set_octave(-1)
        self.e.advance()
        self.assertIn(('off', 4, 72), self.sink.events)
        self.assertIn(('on', 4, 48, 100), self.sink.events)

    def test_mute_releases_and_unmute_restarts_same_note(self):
        self.e.tracks['lead'].pattern = [60]
        self.e.advance()
        self.e.toggle_mute('lead')
        self.assertIn(('off', 4, 60), self.sink.events)
        self.sink.events.clear()
        self.e.advance()
        self.assertFalse(any(x[0] == 'on' for x in self.sink.events))
        self.e.toggle_mute('lead')
        self.e.advance()
        self.assertIn(('on', 4, 60, 100), self.sink.events)

    def test_repeated_drums_retrigger_and_filtered_notes_stay_off(self):
        self.e.set_cv_role('drum')
        self.e.tracks['drum'].pattern = [[(36, 100), (41, 90)]]
        self.e.advance()
        self.e.release_drums()
        self.e.advance()
        self.assertEqual(2, self.sink.events.count(('on', 10, 36, 100)))
        self.assertNotIn(('on', 10, 41, 90), self.sink.events)
        self.assertEqual(2, self.sink.events.count(('cv', 1, 5)))
        self.assertIn(('cv', 1, 0), self.sink.events)

    def test_route_change_releases_gate_and_tracks_active_destination(self):
        self.e.tracks['lead'].pattern = [60]
        self.e.tracks['bass'].pattern = [36]
        self.e.advance()
        self.sink.events.clear()
        self.e.set_cv_role('bass')
        self.assertEqual([('cv', 1, 0), ('cv', 0, -2.0), ('cv', 1, 5)], self.sink.events)
        self.e.set_cv_role(None)
        self.assertEqual(('cv', 1, 0), self.sink.events[-1])

    def test_randomize_all_preserves_whole_drum_mute_and_generates_chords_first(self):
        self.e.tracks['drum'].muted = True
        with patch.object(loop_patterns, 'generate_pattern', wraps=loop_patterns.generate_pattern) as gen:
            self.e.randomize_all()
        generated_roles = [c.args[1] for c in gen.call_args_list]
        self.assertEqual(['chords', 'bass', 'lead', 'drum'],
                         list(dict.fromkeys(generated_roles)))
        self.assertTrue(self.e.tracks['drum'].muted)
        self.assertEqual(4, len([x for x in self.sink.events if x[0] == 'preset']))

    def test_global_randomize_restarts_drum_effect_countdowns(self):
        self.e.step = 173
        self.e.roll_remaining = 9
        self.e.break_remaining = 7
        duration = self.e.break_duration_beats
        self.e.randomize_all()
        self.assertEqual(0, self.e.roll_remaining)
        self.assertEqual(0, self.e.break_remaining)
        self.assertEqual(173 + self.e.drum_roll_beats * 4, self.e.roll_due_step)
        self.assertEqual(173 + (64 - duration) * 4, self.e.break_due_step)
        self.assertEqual(duration, self.e.break_duration_beats)

    def test_instrument_randomize_changes_notes_preset_and_unmutes(self):
        choose = loop_random.choice

        def chosen(values):
            return True if values == (True, False) else choose(values)

        with patch.object(loop_random, 'choice', side_effect=chosen):
            for role in ROLES:
                track = self.e.tracks[role]
                track.muted = True
                length, channel, octave = len(track.pattern), track.channel, track.octave
                self.sink.events.clear()
                self.e.randomize_track(role)
                self.assertFalse(track.muted)
                self.assertEqual((length, channel, octave),
                                 (len(track.pattern), track.channel, track.octave))
                self.assertTrue(any(event[0] == 'preset' and event[1] == channel
                                    for event in self.sink.events))
        self.assertFalse(self.e.drum_muted['kick'])
        self.assertTrue(all(self.e.drum_muted[part] for part in self.e.drum_muted if part != 'kick'))

    def test_randomize_only_uses_adjacent_camelot_keys(self):
        self.assertEqual({'C Major', 'F Major', 'G Major', 'A Minor'},
                         set(loop_patterns.camelot_compatible_keys('C Major')))
        self.assertEqual({'A Minor', 'D Minor', 'E Minor', 'C Major'},
                         set(loop_patterns.camelot_compatible_keys('A Minor')))
        for name in loop_patterns.CAMELOT_CODES:
            choices = loop_patterns.camelot_compatible_keys(name)
            self.assertEqual(4, len(choices))
            old_number, old_letter = loop_patterns.CAMELOT_CODES[name]
            for choice in choices:
                number, letter = loop_patterns.CAMELOT_CODES[choice]
                self.assertTrue((number == old_number and letter != old_letter) or
                                (letter == old_letter and
                                 (number - old_number) % 12 in (0, 1, 11)))
        self.assertEqual(['G Whole Tone'],
                         loop_patterns.camelot_compatible_keys('G Whole Tone'))
        for _ in range(100):
            before = self.e.scale_name
            self.e.randomize_all()
            self.assertIn(self.e.scale_name,
                          loop_patterns.camelot_compatible_keys(before))

    def test_chord_size_defaults_to_four_and_updates_existing_and_new_loops(self):
        self.e.randomize_all()
        self.assertEqual(4, self.e.chord_size)
        self.assertTrue(all(len(cell) == 4 for cell in self.e.tracks['chords'].pattern if cell))
        self.e.set_chord_size(2)
        self.assertTrue(all(len(cell) == 2 for cell in self.e.tracks['chords'].pattern if cell))
        self.e.randomize_notes('chords')
        self.assertTrue(all(len(cell) == 2 for cell in self.e.tracks['chords'].pattern if cell))
        self.e.set_chord_size(5)
        self.e.set_loop_length('chords', 1)
        self.assertTrue(all(len(cell) == 5 for cell in self.e.tracks['chords'].pattern if cell))
        saved = self.e.snapshot()
        self.assertEqual(5, saved['chord_size'])
        validate_set(saved)
        old = saved.copy()
        old.pop('chord_size')
        validate_set(old)

    def test_note_count_changes_one_step_and_never_removes_last_note(self):
        self.e.edit_notes(1)
        self.assertEqual(1, sum(n is not None for n in self.e.tracks['lead'].pattern))
        original = self.e.tracks['lead'].pattern[:]
        self.e.edit_notes(1)
        self.assertTrue(all(old is None or old == new
                            for old, new in zip(original, self.e.tracks['lead'].pattern)))
        self.e.edit_notes(100)
        self.assertEqual(16, sum(n is not None for n in self.e.tracks['lead'].pattern))
        self.e.edit_notes(-100)
        self.assertEqual(1, sum(n is not None for n in self.e.tracks['lead'].pattern))

    def test_random_note_lengths_sustain_only_until_next_melodic_note(self):
        track = self.e.tracks['lead']
        track.pattern = [60, None, None, 62, None, None, None, None]
        track.lengths = [1, 0, 0, 1, 0, 0, 0, 0]
        track.stash = []
        track.lengths_stash = []
        # Force the one-in-three branch and use each full available gap.
        with patch.object(loop_random, 'randint', side_effect=[1, 3, 5]):
            self.e._maybe_randomize_lengths('lead')
        self.assertEqual([3, 0, 0, 5, 0, 0, 0, 0], track.lengths)
        self.assertEqual([(60, 100)], self.e.notes_at(track, 2))
        self.assertEqual([(62, 100)], self.e.notes_at(track, 3))
        self.assertEqual([(62, 100)], self.e.notes_at(track, 7))

    def test_bass_and_chord_octave_and_density(self):
        for role in ('bass', 'chords'):
            self.e.tracks[role].pattern = [None] * 16
            with patch('loop_engine.next', lambda iterator: next(iterator), create=True):
                self.e.edit_notes(role, 1)
            self.assertEqual(1, sum(c is not None for c in self.e.tracks[role].pattern))
            self.e.set_octave(role, 2)
            self.assertEqual(2, self.e.tracks[role].octave)
            self.e.edit_notes(role, -1)
            self.assertEqual(1, sum(c is not None for c in self.e.tracks[role].pattern))

    def test_length_doubles_with_new_notes_and_halves_preserving_prefix(self):
        for role in ROLES:
            self.e.tracks[role].pattern = [None] * 16
            self.e.set_loop_length(role, 1)
            self.assertEqual(32, len(self.e.tracks[role].pattern))
            self.assertTrue(any(c is not None for c in self.e.tracks[role].pattern[16:]))
            self.assertEqual([None] * 16, self.e.tracks[role].pattern[:16])
            self.e.set_loop_length(role, -1)
            self.assertEqual([None] * 16, self.e.tracks[role].pattern)

    def test_shortening_to_one_and_reexpanding_to_sixty_four_restores_notes(self):
        originals = {
            'lead': [60 + i % 5 for i in range(16)],
            'bass': [36 + i % 5 for i in range(16)],
            'chords': [[60, 64, 67, 71] for _ in range(16)],
            'drum': [[(36, 100), (38, 80)] for _ in range(16)],
        }
        loop_patterns.ARRANGER['chord_roots_16ths'] = [36] * 16
        for role in ROLES:
            track = self.e.tracks[role]
            track.pattern = originals[role][:]
            self.e.set_loop_length(role, -4)
            self.assertEqual(1, len(track.pattern))
            self.e.set_loop_length(role, 6)
            self.assertEqual(64, len(track.pattern))
            self.assertEqual(originals[role], track.pattern[:16])
            full = self.e.snapshot()['tracks'][role]['pattern']
            self.e.set_loop_length(role, -6)
            self.e.set_loop_length(role, 6)
            self.assertEqual(full, self.e.snapshot()['tracks'][role]['pattern'])
            self.e.set_loop_length(role, 1)
            self.assertEqual(64, len(track.pattern))
        validate_set(self.e.snapshot())

    def test_length_changes_only_reveal_the_fixed_hidden_pattern(self):
        for role in ROLES:
            self.e.randomize_notes(role)
            track = self.e.tracks[role]
            original = self.e.snapshot()['tracks'][role]
            with patch.object(self.e, '_extra_notes', side_effect=AssertionError('regenerated')):
                self.e.set_loop_length(role, -6)
                self.e.set_loop_length(role, 6)
                self.e.set_loop_length(role, -6)
                self.e.set_loop_length(role, 6)
            self.assertEqual(original['pattern'] + original['stash'],
                             self.e.snapshot()['tracks'][role]['pattern'] +
                             self.e.snapshot()['tracks'][role]['stash'])

    def test_melodic_playback_directions_and_random_cycle(self):
        track = self.e.tracks['lead']
        track.pattern = [60, 61, 62, 63]
        track.lengths = [1] * 4
        cases = {
            'FORWARD': [60, 61, 62, 63, 60, 61],
            'REVERSE': [63, 62, 61, 60, 63, 62],
            'PING PONG': [60, 61, 62, 63, 62, 61],
        }
        for direction, expected in cases.items():
            self.e.set_direction('lead', direction)
            self.assertEqual(expected, [self.e.notes_at(track, step)[0][0]
                                        for step in range(6)])
        self.e.set_direction('lead', 'RANDOM')
        first = [self.e.notes_at(track, step)[0][0] for step in range(4)]
        self.assertEqual([60, 61, 62, 63], sorted(first))
        self.assertEqual(first, [self.e.notes_at(track, step)[0][0]
                                 for step in range(4)])

    def test_ghost_notes_only_on_offbeats_and_at_low_velocity(self):
        self.e.tracks['drum'].pattern = [None] * 16
        self.e.tracks['drum'].muted = False
        with patch.object(loop_random, 'random', return_value=0.0):
            for step in range(16):
                self.e.step = step
                self.sink.events.clear()
                self.e.advance()
                notes = [event for event in self.sink.events if event[0] == 'on' and event[1] == 10]
                self.assertEqual(1 if step % 4 == 2 else 0, len(notes))
                if notes:
                    self.assertLessEqual(notes[0][3], 55)

    def test_kick_break_ends_each_sixty_four_beat_cycle_with_fixed_duration(self):
        track = self.e.tracks['drum']
        track.pattern = [[(36, 110)]] * 16
        track.muted = False
        self.e.break_duration_beats = 4
        self.e.break_due_step = 240
        self.e.roll_due_step = 100000
        for offset in (0, 256, 512):
            self.e.step = 239 + offset
            self.sink.events.clear()
            self.e.advance()
            self.assertIn(('on', 10, 36, 110), self.sink.events)
            for step in range(240 + offset, 256 + offset):
                self.e.step = step
                self.sink.events.clear()
                self.e.advance()
                self.assertFalse(any(event[:3] == ('on', 10, 36) for event in self.sink.events))
            self.e.step = 256 + offset
            self.sink.events.clear()
            self.e.advance()
            self.assertIn(('on', 10, 36, 110), self.sink.events)
        self.assertEqual([[(36, 110)]] * 16, track.pattern)
        self.assertEqual(4, self.e.snapshot()['break_duration_beats'])

    def test_drum_roll_uses_claps_one_quarter_when_unmuted(self):
        self.e.drum_muted['clap'] = False
        with patch.object(loop_random, 'random', side_effect=[0.2, 0.3]):
            self.assertEqual(39, self.e._roll_notes(0)[0][0])
            self.assertNotEqual(39, self.e._roll_notes(1)[0][0])
        self.e.drum_muted['clap'] = True
        with patch.object(loop_random, 'random', return_value=0.0):
            self.assertNotEqual(39, self.e._roll_notes(0)[0][0])

    def test_auto_roll_for_one_bar_then_original_drums_return(self):
        track = self.e.tracks['drum']
        track.pattern = [[(36, 110)]] * 16
        track.muted = False
        self.assertEqual(128, self.e.drum_roll_beats)
        self.e.set_drum_roll_beats(32)
        self.assertEqual(128, self.e.roll_due_step)
        for step in range(128, 144):
            self.e.step = step
            self.sink.events.clear()
            self.e.advance()
            self.assertTrue(any(event[0] == 'on' and event[1] == 10 for event in self.sink.events))
            self.assertFalse(any(event[:3] == ('on', 10, 36) for event in self.sink.events))
        self.e.step = 144
        self.sink.events.clear()
        self.e.advance()
        self.assertIn(('on', 10, 36, 110), self.sink.events)
        self.assertEqual([[(36, 110)]] * 16, track.pattern)

    def test_genre_switch_replaces_drums_and_glides_tempo_for_sixty_four_beats(self):
        self.assertEqual(('HOUSE', 'TECHNO', 'BREAKS', 'ELECTRO', "DRUM'N'BASS"),
                         loop_patterns.DRUM_GENRES)
        self.assertEqual(140, loop_patterns.DRUM_TEMPOS['ELECTRO'])
        self.assertEqual(130, loop_patterns.DRUM_TEMPOS['TECHNO'])
        self.assertEqual(135, loop_patterns.DRUM_TEMPOS['BREAKS'])
        for genre, tempo in loop_patterns.DRUM_TEMPOS.items():
            before = self.e.bpm
            self.e.set_drum_genre(genre)
            self.assertEqual(genre, self.e.drum_genre)
            self.assertEqual(before, self.e.bpm)
            for _ in range(256):
                self.e.advance()
            self.assertEqual(tempo, self.e.bpm)
            self.assertEqual(16, len(self.e.tracks['drum'].pattern))
            self.assertTrue(any(c is not None for c in self.e.tracks['drum'].pattern))
        self.e.set_loop_length('drum', 1)
        self.e.randomize_part('kick')
        self.assertTrue(any(cell and any(hit[0] == 36 for hit in cell)
                            for cell in self.e.tracks['drum'].pattern[16:]))

    def test_queued_load_waits_for_common_boundary_and_restores_phase(self):
        self.e.tracks['lead'].pattern = [60] * 16
        self.e.tracks['bass'].pattern = [36] * 32
        self.e.tracks['chords'].pattern = [None] * 64
        self.e.tracks['drum'].pattern = [None] * 16
        saved = self.e.snapshot()
        saved['tracks']['lead']['pattern'] = [70] * 4 + [71] * 4
        saved['tracks']['lead']['program'] = 52
        saved['bpm'] = 174
        saved['drum_genre'] = "DRUM'N'BASS"
        self.e.step = 17
        self.assertEqual(64, self.e.queue_load('FUCKING DUCK', saved))
        for step in range(17, 64):
            self.e.step = step
            self.e.advance()
            self.assertEqual(60, self.e.tracks['lead'].pattern[0])
        self.e.step = 64
        self.e.advance()
        self.assertEqual(70, self.e.tracks['lead'].pattern[0])
        self.assertEqual(64, self.e.phase_origin)
        self.assertEqual(174, self.e.bpm)
        self.assertIn(('on', 4, 70, 100), self.sink.events)
        # A subsequent load uses the newly started set's own loop origin.
        self.e.step = 65
        self.assertEqual(128, self.e.queue_load('FUCKING DUCK', saved))

    def test_snapshot_is_independent_of_live_mutations(self):
        self.e.tracks['drum'].pattern = [[(36, 110)]] * 16
        saved = self.e.snapshot()
        self.e.tracks['drum'].pattern[0][0] = (38, 90)
        self.assertEqual([36, 110], saved['tracks']['drum']['pattern'][0][0])
        validate_set(saved)

    def test_stop_releases_every_note_and_cv_gate(self):
        for t in self.e.tracks.values():
            t.pattern = [60]
        self.e.advance()
        self.e.stop()
        self.assertTrue(all(not t.active for t in self.e.tracks.values()))
        self.assertEqual(4, len([x for x in self.sink.events if x[0] == 'off']))
        self.assertIn(('cv', 1, 0), self.sink.events)

    def test_preset_change_preserves_loop_and_releases_held_note(self):
        track = self.e.tracks['lead']
        track.pattern = [72]
        self.e.advance()
        self.e.randomize_preset('lead')
        self.assertEqual([72], track.pattern)
        self.assertIn(('off', 4, 72), self.sink.events)
        self.assertIn(('cv', 1, 0), self.sink.events)
        self.e.advance()
        self.assertEqual(2, self.sink.events.count(('on', 4, 72, 100)))

    def test_patterns_stress_all_scales(self):
        for name, scale in loop_patterns.SCALE_PRESETS.items():
            for _ in range(8):
                for role in ('chords', 'bass', 'lead', 'drum'):
                    t = self.e.tracks[role]
                    t.pattern = loop_patterns.generate_pattern(scale, role, name)
                    self.assertIn(len(t.pattern), (16, 32, 64, 128))
                    for step in range(len(t.pattern)):
                        for note, velocity in self.e.notes_at(t, step):
                            self.assertTrue(0 <= note <= 127)
                            self.assertTrue(0 < velocity <= 127)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine(Sink())
        self.m = Menu(self.e)

    def test_order_actions_and_long_press_from_edit_goes_up(self):
        self.assertEqual(['RANDOMIZE', 'SYNTH', 'BASS', 'CHORD', 'DRUMS', 'SAVE', 'LOAD', 'SETTINGS'], self.m.rows())
        self.m.handle(1, delta=1)
        self.m.handle(2, click=True)
        self.assertEqual(['RANDOMIZE', 'RANDOM NOTES', 'RANDOM PRESET', 'PRESET B1 P1', 'LOOP LENGTH', 'DIRECTION', 'OCTAVE', '# of NOTES', 'MUTE'], self.m.rows())
        self.m.handle(3, delta=6)
        self.m.handle(4, click=True)
        self.m.handle(5, delta=1)
        self.assertEqual(1, self.e.tracks['lead'].octave)
        self.m.handle(6, long_press=True)
        self.assertEqual(('main', 1, False), (self.m.page, self.m.index, self.m.editing))

    def test_every_instrument_menu_starts_with_randomize(self):
        for role in ROLES:
            self.m.enter(role)
            self.assertEqual('RANDOMIZE', self.m.rows()[0])
            self.m.handle(10, click=True)
            self.assertTrue(self.m.bam_active(10))
            self.m.back()

    def test_melodic_direction_menu_defaults_forward_and_cycles(self):
        for role in ('lead', 'bass', 'chords'):
            self.m.enter(role)
            self.m.index = self.m.rows().index('DIRECTION')
            self.assertEqual('FORWARD', self.m.selected_value())
            self.m.handle(1, click=True)
            for expected in ('REVERSE', 'PING PONG', 'RANDOM', 'FORWARD'):
                self.m.handle(2, delta=1)
                self.assertEqual(expected, self.e.tracks[role].direction)
            self.m.back()
        self.m.enter('drum')
        self.assertNotIn('DIRECTION', self.m.rows())

    def test_screensaver_boundary_wake_only_and_preserves_edit(self):
        self.m.screensaver_enabled = True
        self.m.enter('lead')
        self.m.index = 4
        self.m.editing = True
        self.m.handle(59999)
        self.assertFalse(self.m.sleeping)
        self.m.handle(60000)
        self.assertTrue(self.m.sleeping)
        self.m.handle(60001, delta=3, click=True)
        self.assertFalse(self.m.sleeping)
        self.assertEqual(('lead', 4, True), (self.m.page, self.m.index, self.m.editing))
        self.assertEqual(0, self.e.tracks['lead'].octave)

    def test_turns_and_holds_reset_idle(self):
        self.m.screensaver_enabled = True
        self.m.handle(50000, delta=1)
        self.m.handle(100000, activity=True)
        self.m.handle(159999)
        self.assertFalse(self.m.sleeping)
        self.m.handle(160000)
        self.assertTrue(self.m.sleeping)

    def test_screensaver_off_by_default_and_menu_toggle(self):
        self.m.handle(100000)
        self.assertFalse(self.m.sleeping)
        self.m.enter('settings')
        self.m.index = self.m.rows().index('SCREENSAVER')
        self.assertEqual('OFF', self.m.selected_value())
        self.m.click(100001)
        self.m.adjust(1, 100002)
        self.assertEqual('ON', self.m.selected_value())
        self.m.adjust(-1, 100003)
        self.assertEqual('OFF', self.m.selected_value())

    def test_drum_roll_menu_sets_interval_and_manual_roll(self):
        self.e.start()
        self.m.enter('drum')
        self.m.index = self.m.rows().index('DRUM ROLL')
        self.m.click(1)
        self.assertEqual('drum_roll', self.m.page)
        self.m.index = 1
        self.assertEqual('128 BEATS', self.m.selected_value())
        self.m.click(2)
        self.m.adjust(1, 3)
        self.assertEqual(160, self.e.drum_roll_beats)
        self.m.click(4)
        self.m.index = 0
        self.m.click(5)
        self.assertEqual(16, self.e.roll_remaining)

    def test_genre_turn_only_previews_until_button_confirms(self):
        self.m.enter('drum')
        self.m.index = self.m.rows().index('GENRE')
        self.m.click(1)
        self.m.adjust(1, 2)
        self.assertEqual('HOUSE', self.e.drum_genre)
        self.assertEqual('TECHNO 130', self.m.selected_value())
        self.m.click(3)
        self.assertEqual('TECHNO', self.e.drum_genre)
        self.assertFalse(self.m.editing)

    def test_nested_drum_menu_back(self):
        self.m.enter('drum')
        self.m.index = self.m.rows().index('DRUM PARTS')
        self.m.handle(1, click=True)
        self.m.handle(2, click=True)
        self.assertEqual('part:kick', self.m.page)
        self.m.handle(3, long_press=True)
        self.assertEqual('parts', self.m.page)
        self.m.handle(4, long_press=True)
        self.assertEqual(('drum', self.m.rows().index('DRUM PARTS')), (self.m.page, self.m.index))

    def test_load_list_and_separate_delete_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Sets(directory)
            name = store.save(self.e.snapshot())
            self.m = Menu(self.e, set_store=store)
            self.m.index = 6
            self.m.click(1)
            self.assertEqual([name, 'DELETE...'], self.m.rows())
            self.m.click(2)
            self.assertEqual('main', self.m.page)
            self.m.enter('load')
            self.m.index = 1
            self.m.click(3)
            self.assertEqual('delete', self.m.page)
            self.m.click(4)
            self.assertEqual('delete_confirm', self.m.page)
            self.assertEqual(['CANCEL', 'DELETE SET'], self.m.rows())
            self.m.click(5)
            self.assertEqual([name], store.list_names())
            self.m.click(6)
            self.m.index = 1
            self.m.click(7)
            self.assertEqual([], store.list_names())

    def test_load_highlights_only_camelot_compatible_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Sets(directory)
            matching = store.save(self.e.snapshot())
            self.e.scale_name = 'C Minor'
            self.e.scale = loop_patterns.SCALE_PRESETS['C Minor'][:]
            other = store.save(self.e.snapshot())
            self.e.scale_name = 'C Major'
            self.e.scale = loop_patterns.SCALE_PRESETS['C Major'][:]
            menu = Menu(self.e, set_store=store)
            menu.enter('load')
            menu.index = menu.rows().index(matching)
            self.assertEqual('KEY MATCH', menu.selected_value())
            menu.index = menu.rows().index(other)
            self.assertEqual('OTHER KEY', menu.selected_value())
            class Display:
                def __init__(self):
                    self.lines = []
                def hline(self, x, y, width, color):
                    self.lines.append((x, y, width, color))
                def __getattr__(self, _):
                    return lambda *args: None
            display = Display()
            menu.render(display)
            match_y = 18 + menu.rows().index(matching) * 13
            self.assertIn((0, match_y - 2, 128, 255), display.lines)
            self.e.scale_name = 'C Minor'
            self.assertEqual('KEY MATCH', menu.selected_value())
            menu.index = menu.rows().index(matching)
            self.assertEqual('OTHER KEY', menu.selected_value())
            self.assertTrue(loop_patterns.camelot_compatible('C Major', 'E Phrygian'))
            self.assertFalse(loop_patterns.camelot_compatible('C Major', 'G Whole Tone'))

    def test_main_title_fits_oled(self):
        self.assertLessEqual(len('~L00P FR34K3R~'), 16)
        class Display:
            def __init__(self):
                self.texts = []
                self.lines = 0
            def text(self, value, x, y, color):
                self.texts.append((value, x, y, color))
            def line(self, *args):
                self.lines += 1
            def __getattr__(self, _):
                return lambda *args: None
        display = Display()
        self.m.render(display)
        self.assertIn(('~L00P FR34K3R~', 8, 0, 255), display.texts)
        self.assertIn(('C MAJ', 0, 104, 255), display.texts)
        self.assertIn(('H', 60, 104, 255), display.texts)
        self.assertIn(('124', 104, 104, 255), display.texts)
        self.e.drum_genre = 'ELECTRO'
        self.e.bpm = 137
        self.m.render(display)
        self.assertIn(('E', 60, 104, 255), display.texts)
        self.assertIn(('137', 104, 104, 255), display.texts)
        self.m.click(10)
        self.assertTrue(self.m.bam_active(10))
        self.m.render_bam(display, 100)
        line_count = display.lines
        self.m.handle(200)
        self.assertFalse(self.m.dirty)
        self.assertEqual(line_count, display.lines)
        self.assertIn(('BAM!', 50, 57, 0), display.texts)
        self.assertGreater(display.lines, 30)
        self.assertFalse(self.m.bam_active(650))
        self.m.handle(650)
        self.assertTrue(self.m.dirty)

    def test_preset_submenu_actions_use_current_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs = PresetPreferences(os.path.join(directory, 'preferences'))
            self.e.preset_preferences = prefs
            self.m.enter('chords')
            self.m.index = self.m.rows().index('PRESET B1 P1')
            self.assertEqual('PRESET B1 P1', self.m.rows()[self.m.index])
            self.m.click(1)
            self.assertEqual('preset:chords', self.m.page)
            self.assertEqual(['SELECT PRESET', 'PREFER PRESET', 'DELETE PRESET', 'NORMAL PRESET'], self.m.rows())
            self.m.index = 1
            self.m.click(2)
            self.assertEqual('PREFERRED', prefs.status('chords', 6, 0, 0))
            self.assertTrue(self.e.set_channel('chords', 5))
            self.assertEqual('NORMAL', prefs.status('chords', 5, 0, 0))
            self.m.click(3)
            self.m.index = 2
            self.m.click(4)
            self.assertEqual('DELETED', prefs.status('chords', 5, 0, 0))
            self.assertEqual('PREFERRED', prefs.status('chords', 6, 0, 0))

    def test_preset_submenu_scrolls_valid_synth_banks(self):
        self.m.enter('lead')
        self.m.index = self.m.rows().index('PRESET B1 P1')
        self.m.click(1)
        self.assertEqual('SELECT PRESET', self.m.rows()[0])
        self.m.click(2)
        self.m.handle(3, delta=25)
        self.assertEqual((0, 25), (self.e.tracks['lead'].bank,
                                   self.e.tracks['lead'].program))
        self.assertEqual('B1 P26', self.m.selected_value())
        self.m.handle(4, delta=1)
        self.assertEqual((1, 0), (self.e.tracks['lead'].bank,
                                  self.e.tracks['lead'].program))
        self.m.handle(5, delta=51)
        self.assertEqual((2, 0), (self.e.tracks['lead'].bank,
                                  self.e.tracks['lead'].program))
        self.m.handle(6, delta=-1)
        self.assertEqual((1, 50), (self.e.tracks['lead'].bank,
                                   self.e.tracks['lead'].program))
        self.assertTrue(any(event[0] == 'preset' for event in self.e.sink.events))

    def test_chord_size_menu_changes_voicing_count(self):
        self.m.enter('chords')
        self.assertIn('CHORD SIZE', self.m.rows())
        self.m.index = self.m.rows().index('CHORD SIZE')
        self.assertEqual('4', self.m.selected_value())
        self.m.click(1)
        self.m.handle(2, delta=-2)
        self.assertEqual(2, self.e.chord_size)
        self.m.click(3)
        self.assertFalse(self.m.editing)

    def test_settings_has_no_internal_sound_editor(self):
        self.m.enter('settings')
        self.assertEqual(['MIDI CHANNELS', 'CV SOURCE', 'TEMPO',
                          'SCREENSAVER', 'PLAY / STOP'], self.m.rows())

    def test_tick_wrap_for_idle(self):
        modulus = 1 << 20
        diff = lambda a, b: ((a-b+modulus//2) % modulus) - modulus//2
        m = Menu(self.e, modulus-100, diff)
        m.screensaver_enabled = True
        m.handle(59900)
        self.assertTrue(m.sleeping)


class GestureTests(unittest.TestCase):
    def test_bounce_short_and_long_are_exclusive(self):
        c = Controls()
        c.poll(0, 0, True)
        c.poll(5, 0, False)
        c.poll(10, 0, True)
        self.assertFalse(c.poll(35, 0, True)[1])
        self.assertTrue(c.poll(685, 0, True)[2])
        self.assertFalse(c.poll(900, 0, True)[2])
        c.poll(910, 0, False)
        self.assertFalse(c.poll(940, 0, False)[1])
        c.poll(950, 0, True)
        c.poll(980, 0, True)
        c.poll(1000, 0, False)
        self.assertTrue(c.poll(1030, 0, False)[1])

    def test_wake_press_suppressed_through_release(self):
        c = Controls()
        c.poll(60000, 0, True, consume=True)
        c.poll(60030, 0, True)
        self.assertFalse(c.poll(61000, 0, True)[2])
        c.poll(61100, 0, False)
        self.assertFalse(c.poll(61130, 0, False)[1])
        self.assertEqual(1, c.poll(61200, 1, False)[0])

    def test_wake_turn_consumed_once(self):
        c = Controls()
        self.assertEqual(0, c.poll(1, 2, False, consume=True)[0])
        self.assertEqual(1, c.poll(2, 3, False)[0])


class GalaxyTests(unittest.TestCase):
    def test_bounded_particles_and_no_rng_interference(self):
        g = NoteGalaxy()
        before = random.getstate()
        for _ in range(100):
            g.note('drum', 36, 127)
        self.assertEqual(before, random.getstate())
        self.assertEqual(40, len(g.particles))
        self.assertEqual(5, len(g.rings))


class SavedSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Sets(self.temp.name)
        self.engine = Engine(Sink())

    def tearDown(self):
        self.temp.cleanup()

    def test_unique_uppercase_names_and_roundtrip_every_track(self):
        self.engine.set_drum_genre("DRUM'N'BASS")
        self.engine.tracks['bass'].muted = True
        self.engine.tracks['lead'].octave = 2
        self.engine.set_direction('lead', 'REVERSE')
        self.engine.drum_muted['kick'] = True
        self.engine.set_loop_length('drum', 1)
        before = self.engine.snapshot()
        names = [self.store.save(before) for _ in range(25)]
        self.assertEqual(25, len(set(names)))
        self.assertTrue(all(name == name.upper() and len(name) <= 16 and
                            name.split(' ')[0] == 'D' and
                            name.split(' ')[1] in ADJECTIVES and name.split(' ')[2] in NOUNS
                            for name in names))
        self.assertEqual(sorted(names), self.store.list_names())
        self.assertEqual(before, self.store.load(names[0]))
        self.assertFalse(any(name.endswith('.tmp') for name in os.listdir(self.temp.name)))

    def test_hidden_loop_tail_survives_save_and_load(self):
        original = [60 + i for i in range(16)]
        self.engine.tracks['lead'].pattern = original[:]
        self.engine.break_duration_beats = 7
        self.engine.set_direction('lead', 'PING PONG')
        self.engine.set_loop_length('lead', -4)
        self.assertEqual(1, len(self.engine.tracks['lead'].pattern))
        name = self.store.save(self.engine.snapshot())
        restored = Engine(Sink())
        restored.queue_load(name, self.store.load(name))
        self.assertEqual(7, restored.break_duration_beats)
        self.assertEqual('PING PONG', restored.tracks['lead'].direction)
        self.assertEqual((64 - 7) * 4, restored.break_due_step)
        restored.set_loop_length('lead', 4)
        self.assertEqual(original, restored.tracks['lead'].pattern)

    def test_older_sets_without_direction_load_forward(self):
        older = self.engine.snapshot()
        for track in older['tracks'].values():
            track.pop('direction')
        validate_set(older)
        restored = Engine(Sink())
        restored.queue_load('OLDER SET', older)
        self.assertTrue(all(track.direction == 'FORWARD'
                            for track in restored.tracks.values()))

    def test_older_internal_fields_are_ignored_and_synth_preset_is_clamped(self):
        older = self.engine.snapshot()
        older['internal_role'] = 'lead'
        older['tracks']['lead']['patch'] = 32
        older['tracks']['lead']['bank'] = 0
        older['tracks']['lead']['program'] = 52
        validate_set(older)
        restored = Engine(Sink())
        restored.queue_load('OLDER SET', older)
        self.assertEqual((0, 25), (restored.tracks['lead'].bank,
                                   restored.tracks['lead'].program))
        self.assertNotIn('internal_role', restored.snapshot())
        self.assertNotIn('patch', restored.snapshot()['tracks']['lead'])

    def test_corrupt_set_rejected_without_changing_engine(self):
        name = self.store.save(self.engine.snapshot())
        path = self.store._path(name)
        with open(path, 'r') as handle:
            invalid = json.load(handle)
        invalid['tracks']['bass']['pattern'] = [999] * 16
        with open(path, 'w') as handle:
            json.dump(invalid, handle)
        before = self.engine.snapshot()
        with self.assertRaises(ValueError):
            self.store.load(name)
        self.assertEqual(before, self.engine.snapshot())

    def test_delete_only_target_and_save_after_delete(self):
        first = self.store.save(self.engine.snapshot())
        second = self.store.save(self.engine.snapshot())
        self.store.delete(first)
        self.assertEqual([second], self.store.list_names())
        self.assertNotEqual(second, self.store.save(self.engine.snapshot()))


class PresetPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prefix = os.path.join(self.temp.name, 'preset_preferences')
        self.preferences = PresetPreferences(self.prefix)

    def tearDown(self):
        self.temp.cleanup()

    def test_preference_is_persistent_and_only_applies_to_role_and_channel(self):
        self.preferences.set_status('chords', 6, 0, 1, 'PREFERRED')
        reloaded = PresetPreferences(self.prefix)
        self.assertEqual('PREFERRED', reloaded.status('chords', 6, 0, 1))
        self.assertEqual('NORMAL', reloaded.status('chords', 5, 0, 1))
        self.assertEqual('NORMAL', reloaded.status('lead', 6, 0, 1))
        reloaded.set_status('chords', 6, 0, 1, 'DELETED')
        self.assertEqual('DELETED', PresetPreferences(self.prefix).status('chords', 6, 0, 1))
        reloaded.set_status('chords', 6, 0, 1, 'NORMAL')
        self.assertEqual('NORMAL', PresetPreferences(self.prefix).status('chords', 6, 0, 1))

    def test_preferred_is_weighted_and_deleted_never_selected(self):
        self.preferences.set_status('drum', 10, 0, 2, 'PREFERRED')
        random.seed(123)
        selected = [self.preferences.choose('drum', 10) for _ in range(600)]
        self.assertGreater(selected.count((0, 2)), 35)
        self.preferences.set_status('drum', 10, 0, 2, 'DELETED')
        self.assertTrue(all(self.preferences.choose('drum', 10) != (0, 2)
                            for _ in range(600)))
        # A channel switch ignores both the deleted and preferred history.
        random.seed(123)
        self.assertTrue(any(self.preferences.choose('drum', 11) == (0, 2)
                            for _ in range(600)))

    def test_engine_randomize_all_uses_channel_specific_preference(self):
        engine = Engine(Sink())
        engine.preset_preferences = self.preferences
        self.preferences.set_status('chords', 6, 0, 0, 'DELETED')
        for _ in range(100):
            engine.randomize_all()
            self.assertNotEqual((0, 0), (engine.tracks['chords'].bank,
                                         engine.tracks['chords'].program))

    def test_synth_bank_ranges_are_exact_for_random_and_manual_selection(self):
        self.assertEqual((26, 51, 40, 17), PROGRAM_COUNTS['lead'])
        self.assertEqual((3, 0), move_preset('lead', 0, 0, 117))
        self.assertEqual((0, 0), move_preset('lead', 3, 16, 1))
        engine = Engine(Sink())
        engine.preset_preferences = self.preferences
        for _ in range(400):
            engine.randomize_preset('lead')
            track = engine.tracks['lead']
            self.assertTrue(valid_preset('lead', track.bank, track.program))
        with self.assertRaises(ValueError):
            self.preferences.set_status('lead', 4, 0, 26, 'PREFERRED')

    def test_legacy_synth_preferences_outside_new_ranges_are_ignored(self):
        with open(self.prefix + '.a.json', 'w') as handle:
            json.dump({'version': 1, 'revision': 1, 'entries': {
                'lead:4': {'preferred': [[0, 52], [1, 50]], 'deleted': [[3, 30]]}
            }}, handle)
        loaded = PresetPreferences(self.prefix)
        self.assertEqual('PREFERRED', loaded.status('lead', 4, 1, 50))
        self.assertEqual([], loaded.entries['lead:4']['deleted'])
        self.assertTrue(all(valid_preset('lead', *loaded.choose('lead', 4))
                            for _ in range(100)))


if __name__ == '__main__':
    unittest.main()
