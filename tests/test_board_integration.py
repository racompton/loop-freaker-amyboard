"""Board API contract tests; these do not claim physical timing validation."""
import importlib
import sys
import time
import tempfile
import types
import unittest
from unittest.mock import patch
import loop_patterns


class Display:
    def __init__(self):
        self.texts = []

    def fill(self, color):
        self.texts = []

    def text(self, text, x, y, color):
        self.texts.append((text, x, y, color))

    def __getattr__(self, _):
        return lambda *args: None


class Encoder:
    def __init__(self):
        self.position = 0
        self.pressed = False

    def read(self):
        return self.position

    def button(self):
        return self.pressed


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.midi = []
        self.amy_messages = []
        self.cv = []
        self.deferred = []
        self.display = Display()
        self.encoder = Encoder()
        self.sequences = []
        self.refreshes = 0
        self.fail_internal = False
        self.next_pulse = 0

        def send(**kwargs):
            if self.fail_internal:
                raise RuntimeError('AMY unavailable')
            self.amy_messages.append(kwargs)

        def make_sequence(divider, fn):
            sequence = types.SimpleNamespace(divider=divider, fn=fn, clear=lambda: None)
            self.sequences.append(sequence)
            return sequence

        def refresh():
            self.refreshes += 1

        self.board = types.SimpleNamespace(
            display=self.display, encoder=lambda: self.encoder,
            init_display=lambda *a, **k: None, display_refresh=refresh,
            cv_out=lambda volts, channel: self.cv.append((channel, volts)),
            set_cv_out=lambda *a: None, init_gp8413=lambda: None)
        modules = {
            'amyboard': self.board,
            'amy': types.SimpleNamespace(send=send, FILTER_LPF=1, FILTER_HPF=2, FILTER_BPF=3),
            'tulip': types.SimpleNamespace(midi_out=lambda b: self.midi.append(bytes(b)),
                                          defer=lambda fn, arg, ms: self.deferred.append((fn, arg, ms)),
                                          sequencer_start=lambda: None, i2c_bg_write=lambda *a: None),
            'sequencer': types.SimpleNamespace(PPQ=48, TulipSequence=make_sequence, tempo=lambda bpm: None),
        }
        self.patchers = [patch.dict(sys.modules, modules),
                         patch.object(time, 'ticks_ms', lambda: self.now, create=True),
                         patch.object(time, 'ticks_diff', lambda a, b: a-b, create=True),
                         patch.object(time, 'ticks_add', lambda a, b: a+b, create=True)]
        for p in self.patchers:
            p.start()
        for module in ('sequencer_app', 'loop_hardware', 'loop_amy_settings', 'menu'):
            sys.modules.pop(module, None)
        self.module = importlib.import_module('sequencer_app')
        self.set_dir = tempfile.TemporaryDirectory()
        store_type = self.module.Sets
        self.module.Sets = lambda: store_type(self.set_dir.name)
        self.app = self.module.start()

    def tearDown(self):
        self.fail_internal = False
        self.app.stop()
        for module in ('sequencer_app', 'loop_hardware', 'loop_amy_settings', 'menu'):
            sys.modules.pop(module, None)
        for p in reversed(self.patchers):
            p.stop()
        self.set_dir.cleanup()

    def tick(self, pulse):
        self.now = pulse * 60000 // (120 * 24)
        self.app._tick(pulse * 2)
        self.app._ui(None)

    def finish_job(self):
        for _ in range(400):
            pulse = self.next_pulse
            self.next_pulse += 1
            self.tick(pulse)
            self.deferred.clear()
            if self.app.io_job is None:
                return
        self.fail('background job did not finish')

    def test_startup_returns_with_menu_first_and_no_internal_audio(self):
        self.assertTrue(self.app.running)
        self.assertFalse(hasattr(self.app.sink, 'internal_role'))
        self.assertEqual(96, self.sequences[0].divider)
        self.assertTrue(any(t[0] == '>RANDOMIZE' for t in self.display.texts))
        self.assertIn(bytes((0xFA,)), self.midi)
        self.assertTrue(self.deferred)
        self.assertEqual(124, self.app.engine.bpm)
        self.assertFalse(self.app.menu.screensaver_enabled)

    def test_boot_randomizes_once_before_transport_starts(self):
        original = self.module.Engine.randomize_all
        calls = []

        def track_randomize(engine):
            calls.append((engine.playing, bool(engine.tracks['lead'].pattern)))
            return original(engine)

        with patch.object(self.module.Engine, 'randomize_all', track_randomize):
            app = self.module.App()
        self.assertEqual([(False, False)], calls)
        self.assertTrue(app.engine.tracks['lead'].pattern)
        self.assertFalse(app.engine.playing)
        app.sink.close()

    def test_scrolling_coalesces_oled_and_waits_for_i2c_queue(self):
        self.tick(0)
        initial = self.refreshes
        self.module.tulip.i2c_bg_pending = lambda: 1
        for pulse in range(1, 25):
            self.encoder.position += 1
            self.tick(pulse)
        self.assertEqual(initial, self.refreshes)
        self.assertEqual(25, self.midi.count(bytes((0xF8,))))
        self.module.tulip.i2c_bg_pending = lambda: 0
        self.now += 130
        self.app._ui(None)
        self.assertTrue(self.app.menu.dirty)
        self.app._tick(50)
        self.app._ui(None)
        self.assertFalse(self.app.menu.dirty)
        self.assertEqual(initial + 1, self.refreshes)

    def test_clock_notes_midi_and_cv_run_together(self):
        for t in self.app.engine.tracks.values():
            t.pattern = [60, None]
            t.muted = False
        for pulse in range(96):
            self.tick(pulse)
        self.assertEqual(96, self.midi.count(bytes((0xF8,))))
        self.assertEqual(16, self.app.engine.step)
        self.assertIn(bytes((0x93, 60, 100)), self.midi)
        self.assertIn(bytes((0x92, 60, 100)), self.midi)
        self.assertIn((1, 5), self.cv)
        self.assertIn((1, 0), self.cv)
        self.assertEqual([], self.amy_messages)

    def test_idle_menu_does_not_redraw_oled_or_start_screensaver(self):
        initial = self.refreshes
        for _ in range(100):
            self.now += 20
            self.app._ui(None)
        self.assertEqual(initial, self.refreshes)
        self.now = 60001
        self.app._ui(None)
        self.assertFalse(self.app.menu.sleeping)
        self.assertEqual(initial, self.refreshes)

    def test_bam_draws_one_still_frame_then_restores_menu(self):
        initial = self.refreshes
        self.app.menu.handle(0, click=True)
        for pulse in range(1, 30):
            self.tick(pulse)
        self.assertEqual(initial + 1, self.refreshes)
        self.assertTrue(any(item[0] == 'BAM!' for item in self.display.texts))
        for pulse in range(30, 38):
            self.tick(pulse)
        self.assertEqual(initial + 2, self.refreshes)
        self.assertFalse(self.app.menu.bam_active(self.now))

    def test_randomize_before_loop_ends_with_leading_chord_rest(self):
        original = loop_patterns.generate_pattern

        def chord_rest(scale, role, scale_name=None, chord_size=4):
            pattern = original(scale, role, scale_name, chord_size)
            if role == 'chords':
                pattern[0] = None
            return pattern

        for pulse in range(15):
            self.tick(pulse)
        self.assertTrue(self.app.engine.playing)
        # Match the board's one-argument next() API. The old code crashed here
        # only when the random chord phrase began with a rest.
        with patch('loop_engine.next', lambda iterator: next(iterator), create=True), \
                patch.object(loop_patterns, 'generate_pattern', side_effect=chord_rest):
            self.app.menu.handle(self.now + 1, click=True)
        self.assertTrue(self.app.running)
        self.assertTrue(self.app.engine.playing)
        self.assertIsNone(self.app.last_error)
        for pulse in range(15, 30):
            self.tick(pulse)
        self.assertTrue(self.app.running)
        self.assertIsNone(self.app.last_error)

    def test_save_and_load_jobs_keep_clock_and_notes_running(self):
        menu = self.app.menu
        menu.index = menu.rows().index('SAVE')
        menu.handle(1, click=True)
        self.assertTrue(menu.busy)
        self.assertEqual([], self.app.set_store.list_names())
        for pulse in range(180):
            self.tick(pulse)
            self.deferred.clear()
            if not menu.busy:
                break
        self.assertFalse(menu.busy)
        names = self.app.set_store.list_names()
        self.assertEqual(1, len(names))
        self.assertTrue(self.app.running)
        self.assertEqual(pulse + 1, self.midi.count(bytes((0xF8,))))
        menu.index = menu.rows().index('LOAD')
        menu.handle(self.now + 1, click=True)
        self.assertEqual('load', menu.page)
        menu.handle(self.now + 2, click=True)
        self.assertTrue(menu.busy)
        for next_pulse in range(pulse + 1, pulse + 30):
            self.tick(next_pulse)
            self.deferred.clear()
            if not menu.busy:
                break
        self.assertFalse(menu.busy)
        self.assertIsNotNone(self.app.engine.pending_load)
        self.assertEqual(next_pulse + 1, self.midi.count(bytes((0xF8,))))
        self.assertTrue(self.app.running)
        self.assertIsNone(self.app.last_error)

    def test_deleting_last_set_keeps_load_cursor_valid_and_playback_running(self):
        name = self.app.set_store.save(self.app.engine.snapshot())
        menu = self.app.menu
        menu.enter('load')
        menu.index = menu.rows().index('DELETE...')
        menu.click(1)
        menu.index = menu.rows().index(name)
        menu.click(2)
        menu.index = menu.rows().index('DELETE SET')
        menu.click(3)
        self.assertTrue(menu.busy)
        self.finish_job()
        self.assertEqual('load', menu.page)
        self.assertEqual(['(NO SAVES)'], menu.rows())
        self.assertEqual(0, menu.index)
        self.app._ui(None)
        self.assertTrue(self.app.running)
        self.assertIsNone(self.app.last_error)

    def test_click_applies_immediately_without_undo_snapshot(self):
        self.app.menu.enter('lead')
        self.app.menu.index = self.app.menu.rows().index('RANDOM NOTES')
        before = self.app.engine.snapshot()
        self.app._tick(0)
        with patch.object(self.app.controls, 'poll', return_value=(0, True, False, True)):
            self.app._ui(None)
        self.assertNotEqual(before['tracks']['lead']['pattern'],
                            self.app.engine.snapshot()['tracks']['lead']['pattern'])
        self.assertIsNone(self.app.io_job)
        self.assertTrue(self.app.running)

    def test_preset_preference_change_remains_persistent(self):
        from loop_preset_preferences import PresetPreferences
        self.app.engine.preset_preferences = PresetPreferences(
            self.set_dir.name + '/preset_preferences')
        self.app.menu.enter('lead')
        self.app.menu.index = next(i for i, row in enumerate(self.app.menu.rows())
                                   if row.startswith('PRESET B'))
        self.app.menu.click(1)
        self.app.menu.index = 1
        self.app.menu.click(2)
        self.finish_job()
        track = self.app.engine.tracks['lead']
        prefs = self.app.engine.preset_preferences
        self.assertEqual('PREFERRED', prefs.status('lead', track.channel,
                                                   track.bank, track.program))
        self.assertEqual('PREFERRED', self.module.PresetPreferences(
            self.set_dir.name + '/preset_preferences').status(
                'lead', track.channel, track.bank, track.program))

    def test_settings_navigation_saver_and_release_wake(self):
        menu = self.app.menu
        menu.screensaver_enabled = True
        menu.enter('settings')
        menu.index = menu.rows().index('TEMPO')
        menu.click(0)
        self.now = 60000
        self.app._tick(0)
        self.app._ui(None)
        self.assertTrue(menu.sleeping)
        self.encoder.pressed = True
        self.now += 1
        self.app._ui(None)
        self.assertFalse(menu.sleeping)
        for pulse, delta in enumerate((30, 700), 1):
            self.now += delta
            self.app._tick(pulse * 2)
            self.app._ui(None)
        self.encoder.pressed = False
        self.now += 1
        self.app._tick(6)
        self.app._ui(None)
        self.now += 30
        self.app._tick(8)
        self.app._ui(None)
        self.assertEqual('settings', menu.page)
        self.assertTrue(menu.editing)
        self.assertTrue(self.app.running)

    def test_no_internal_audio_calls_during_external_midi(self):
        for t in self.app.engine.tracks.values():
            t.pattern = [60]
            t.muted = False
        self.amy_messages.clear()
        self.tick(0)
        self.assertIn(bytes((0x93, 60, 100)), self.midi)
        self.assertEqual([], self.amy_messages)
        self.tick(6)
        self.assertTrue(self.app.running)
        self.assertEqual([], self.amy_messages)
        self.assertIn(bytes((0xF8,)), self.midi)

    def test_drum_bank_delay_canceled_on_channel_change(self):
        self.app.engine.randomize_preset('drum')
        self.assertIn(10, self.app.sink.pending_programs)
        self.app.engine.set_channel('drum', 11)
        self.assertNotIn(10, self.app.sink.pending_programs)
        self.midi.clear()
        self.now = 100
        self.app.sink.service(self.now)
        self.assertTrue(any(m[0] == 0xCA for m in self.midi))
        self.assertFalse(any(m[0] == 0xC9 for m in self.midi))

    def test_long_simulated_run_keeps_memory_bounded_and_enters_saver(self):
        self.app.menu.screensaver_enabled = True
        for pulse in range(3000):
            self.tick(pulse)
            self.deferred.clear()  # emulate firmware consuming callback slots
        self.assertTrue(self.app.menu.sleeping)
        self.assertTrue(self.app.running)
        self.assertIsNone(self.app.last_error)
        self.assertLessEqual(len(self.app.galaxy.particles), 40)
        self.assertLessEqual(len(self.app.galaxy.rings), 5)

if __name__ == '__main__':
    unittest.main()
