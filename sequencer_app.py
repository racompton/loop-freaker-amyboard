"""Nonblocking AMYboard looper entry point. start() returns to the REPL."""
import time
import struct
import amyboard
import tulip
import sequencer
try:
    import midi
except ImportError:
    midi = None
from loop_engine import Engine
from loop_ui import Controls, Menu, NoteGalaxy
from loop_hardware import Outputs
from loop_sets import Sets
from loop_preset_preferences import PresetPreferences
from loop_settings import DeviceSettings, CLOCK_SOURCES

_app = None


class EncoderSampler:
    """Poll a seesaw encoder without its two blocking 8 ms register sleeps."""
    def __init__(self, encoder, position):
        self.encoder = encoder
        self.position = position
        self.pressed = False
        self.phase = 0
        self.sent_at = 0
        self.i2c = None
        try:
            dial, dial_index = encoder._map[0]
            button, button_index = encoder._button_map[0]
            if dial['seesaw'] and button['addr'] == dial['addr']:
                self.i2c = amyboard.get_i2c()
                self.addr = dial['addr']
                self.dial_index = dial_index
                self.button_pin = button['button_pins'][button_index]
                self.offset = encoder._offset[0]
                self.invert = encoder._invert[0]
        except (AttributeError, IndexError, KeyError, TypeError):
            pass

    def poll(self, now):
        if self.i2c is None:
            return self.encoder.read(), self.encoder.button()
        try:
            if self.phase == 0:
                self.i2c.writeto(self.addr, bytes((0x11, 0x30 + self.dial_index)))
                self.sent_at, self.phase = now, 1
            elif self.phase == 1 and time.ticks_diff(now, self.sent_at) >= 8:
                raw = struct.unpack('>i', self.i2c.readfrom(self.addr, 4))[0]
                self.position = self.offset - raw if self.invert else raw - self.offset
                self.phase = 2
            elif self.phase == 2:
                self.i2c.writeto(self.addr, b'\x01\x04')
                self.sent_at, self.phase = now, 3
            elif self.phase == 3 and time.ticks_diff(now, self.sent_at) >= 8:
                mask = struct.unpack('>I', self.i2c.readfrom(self.addr, 4))[0]
                self.pressed = not bool(mask & (1 << self.button_pin))
                self.phase = 0
        except OSError:
            self.phase = 0
        return self.position, self.pressed


class App:
    def __init__(self):
        self.running = False
        self.sequence = None
        self.settings = DeviceSettings()
        self.sink = Outputs()
        self.engine = Engine(self.sink, generate=False,
                             program_counts=self.settings.program_counts)
        self.engine.ghost_percent = self.settings.ghost_percent
        self.engine.ghost_velocity = self.settings.ghost_velocity
        self.engine.ghost_on_muted = self.settings.ghost_on_muted
        self.engine.swing = self.settings.swing
        self.engine.clock_source = self.settings.clock_source
        self.engine.preset_preferences = PresetPreferences(ranges=self.engine.program_counts)
        # Make the first set exactly as RANDOMIZE would, before MIDI Start.
        self.engine.randomize_all()
        if amyboard.display is None:
            amyboard.init_display()
        self.display = amyboard.display
        self.encoder = amyboard.encoder()
        now = time.ticks_ms()
        position = self.encoder.read()
        self.controls = Controls(now, position, time.ticks_diff)
        self.sampler = EncoderSampler(self.encoder, position)
        self.set_store = Sets()
        self.menu = Menu(self.engine, now, time.ticks_diff, self.set_store)
        self.menu.io_request = self._queue_io
        self.menu.settings_changed = self._settings_changed
        self.menu.clock_changed = self._set_clock_source
        self.io_job = None
        self.settings_job = None
        self.settings_dirty_at = None
        self.auto_job = None
        self.last_clock_us = None
        self.micros = time.ticks_us if hasattr(time, 'ticks_us') else lambda: time.ticks_ms() * 1000
        self.engine.on_load = lambda name: self.menu.show_notice('LOADED ' + name, time.ticks_ms())
        self.engine.on_mix = lambda message: self.menu.show_notice(message, time.ticks_ms())
        self.engine.on_auto = lambda event: setattr(self.menu, 'dirty', True)
        self.galaxy = NoteGalaxy()
        self.engine.on_note = lambda role, note, velocity: (
            self.galaxy.note(role, note, velocity) if self.menu.screensaver_enabled else None)
        self.last_frame = now
        self.tempo = self.engine.bpm
        self.last_pulse = -1
        self.last_note_step = -1
        self.drum_release_pulse = None
        self.origin = None
        self.was_playing = False
        self.last_error = None
        self.cv_high = False
        self.cv_edge_us = None
        self.cv_period_us = 60000000 // self.engine.bpm
        self.cv_next_us = None
        self.cv_pulse = 0
        self.cv_beat = -1
        self.midi_callback_added = False
        self.midi_beat_us = None

    def start(self):
        self.running = True
        self.menu.render(self.display)
        amyboard.display_refresh()
        self.last_frame = time.ticks_ms()
        sequencer.tempo(self.tempo)
        self.sequence = sequencer.TulipSequence(96, self._tick)
        if hasattr(tulip, 'sequencer_start'):
            tulip.sequencer_start()
        self._configure_clock(self.engine.clock_source)
        tulip.defer(self._ui, None, 20)

    def _settings_changed(self, now):
        self.settings.ghost_percent = self.engine.ghost_percent
        self.settings.ghost_velocity = self.engine.ghost_velocity
        self.settings.ghost_on_muted = self.engine.ghost_on_muted
        self.settings.swing = self.engine.swing
        self.settings.program_counts = {role: tuple(counts) for role, counts in
                                        self.engine.program_counts.items()}
        self.settings.clock_source = self.engine.clock_source
        self.settings_dirty_at = now

    def _configure_clock(self, source):
        if self.engine.playing:
            self.engine.stop()
        if self.midi_callback_added and midi is not None:
            midi.remove_callback(self._midi_input)
            self.midi_callback_added = False
        if hasattr(tulip, 'external_midi_sync'):
            tulip.external_midi_sync(source == 'MIDI IN')
        if source == 'MIDI IN' and midi is not None:
            midi.add_callback(self._midi_input)
            self.midi_callback_added = True
        self.cv_edge_us = None
        self.cv_next_us = None
        self.cv_high = False
        self.cv_beat = -1
        self.last_clock_us = None
        self.was_playing = False
        self.last_pulse = -1
        self.last_note_step = -1
        self.drum_release_pulse = None
        self.midi_beat_us = None
        if source == 'INTERNAL' and self.running:
            self.engine.start()
        if source.startswith('CV') and self.running:
            tulip.defer(self._cv_tick, None, 2)

    def _midi_input(self, message):
        if not self.running or self.engine.clock_source != 'MIDI IN' or not message:
            return
        if message[0] == 0xFA:
            self.engine.start()
            self.was_playing = False
            self.last_pulse = -1
            self.last_note_step = -1
            self.drum_release_pulse = None
        elif message[0] == 0xFC:
            self.engine.stop()
            self.was_playing = False

    def _set_clock_source(self, source, now):
        if source not in CLOCK_SOURCES:
            raise ValueError('unknown clock source')
        if source.startswith('CV') and not hasattr(amyboard, 'cv_in'):
            self.menu.show_notice('CV IN UNAVAILABLE', now)
            return
        if source == self.engine.clock_source:
            return
        self.engine.clock_source = source
        self._configure_clock(source)
        self._settings_changed(now)
        self.menu.show_notice(source, now)

    def _clock_slack(self, reserve_us):
        """Keep UI work away from the next Python note callback."""
        if not self.engine.playing:
            return True
        if self.last_clock_us is None:
            return self.engine.clock_source.startswith('CV')
        period_us = (self.cv_period_us // 24 if self.engine.clock_source.startswith('CV')
                     else 60000000 // (self.engine.bpm * 24))
        age = time.ticks_diff(self.micros(), self.last_clock_us)
        return 0 <= age < max(0, period_us - reserve_us)

    def _tick(self, tick):
        if not self.running:
            return
        if self.engine.clock_source.startswith('CV'):
            return
        try:
            if not self.engine.playing:
                self.was_playing = False
                return
            if not self.was_playing:
                self.origin = tick
                self.last_pulse = -1
                self.last_note_step = -1
                self.drum_release_pulse = None
                self.was_playing = True
            pulse = (tick - self.origin) // max(1, sequencer.PPQ // 24)
            self._process_pulse(pulse)
        except Exception as exc:
            self.fail(exc)

    def _process_pulse(self, pulse):
        if pulse <= self.last_pulse:
            return
        now_us = self.micros()
        if self.engine.clock_source == 'MIDI IN' and pulse % 24 == 0:
            if self.midi_beat_us is not None:
                period = time.ticks_diff(now_us, self.midi_beat_us)
                if 250000 <= period <= 2000000:
                    self.engine.set_bpm(round(60000000 / period))
                    if self.menu.page == 'main':
                        self.menu.dirty = True
            self.midi_beat_us = now_us
        self.sink.clock()
        step = pulse // 6
        if self.drum_release_pulse is not None and pulse >= self.drum_release_pulse:
            self.engine.release_drums()
            self.drum_release_pulse = None
        ready = step % 2 == 0 or pulse % 6 >= self.engine.swing
        if ready and step > self.last_note_step:
            self.engine.step = step
            self.engine.advance()
            self.last_note_step = step
            self.drum_release_pulse = pulse + 3
            if step % 4 == 0 and self.menu.bam_at is None:
                self.menu.status_dirty = True
            if self.engine.clock_source.startswith('CV'):
                old_bpm = self.engine.bpm
                self.engine.set_bpm(round(60000000 / self.cv_period_us))
                if old_bpm != self.engine.bpm and self.menu.page == 'main':
                    self.menu.dirty = True
            if self.engine.bpm != self.tempo and self.engine.clock_source == 'INTERNAL':
                self.tempo = self.engine.bpm
                sequencer.tempo(self.tempo)
                if self.menu.page == 'main' and self.menu.bam_at is None:
                    self.menu.dirty = True
        self.last_pulse = pulse
        self.last_clock_us = self.micros()

    def _cv_tick(self, _):
        if not self.running or not self.engine.clock_source.startswith('CV'):
            return
        try:
            now = self.micros()
            channel = 0 if self.engine.clock_source == 'CV1 IN' else 1
            volts = amyboard.cv_in(channel)
            rising = volts >= 1.5 and not self.cv_high
            if volts <= 0.8:
                self.cv_high = False
            elif volts >= 1.5:
                self.cv_high = True
            if rising:
                if self.cv_edge_us is not None:
                    period = time.ticks_diff(now, self.cv_edge_us)
                    if 250000 <= period <= 2000000:
                        self.cv_period_us = period
                self.cv_edge_us = now
                self.cv_beat += 1
                self.cv_pulse = self.cv_beat * 24
                self.cv_next_us = now
                if not self.engine.playing:
                    self.engine.start()
                    self.last_pulse = -1
                    self.last_note_step = -1
                    self.drum_release_pulse = None
                    self.cv_beat = 0
                    self.cv_pulse = 0
            if (self.cv_edge_us is not None and
                    time.ticks_diff(now, self.cv_edge_us) > self.cv_period_us * 2):
                if self.engine.playing:
                    self.engine.stop()
                self.cv_edge_us = None
                self.cv_next_us = None
            if (self.engine.playing and self.cv_next_us is not None and
                    time.ticks_diff(now, self.cv_next_us) >= 0):
                if self.cv_pulse <= self.cv_beat * 24 + 23:
                    self._process_pulse(self.cv_pulse)
                    self.cv_pulse += 1
                    self.cv_next_us = time.ticks_add(
                        self.cv_next_us, self.cv_period_us // 24)
            tulip.defer(self._cv_tick, None, 2)
        except Exception as exc:
            self.fail(exc)

    def _io_steps(self, job):
        result = job['result']
        kind, name = job['kind'], job['name']
        if kind == 'save':
            snapshot = {}
            for _ in self.engine.snapshot_steps(snapshot):
                yield
            for _ in self.set_store.save_steps(snapshot['value'], result):
                yield
        elif kind == 'load':
            for _ in self.set_store.load_steps(name, result):
                yield
        elif kind == 'delete':
            for _ in self.set_store.delete_steps(name, result):
                yield
        elif kind == 'preset':
            self.engine.preset_preferences.set_status(*name[:5])
            result['value'] = name
            yield

    def _queue_io(self, kind, name, now):
        if self.io_job is not None:
            self.menu.show_notice('STORAGE BUSY', now)
            return False
        job = {'kind': kind, 'name': name, 'result': {}}
        job['steps'] = self._io_steps(job)
        self.io_job = job
        self.menu.busy = True
        self.menu.show_notice({'save': 'SAVING...', 'load': 'LOADING...',
                               'delete': 'DELETING...', 'preset': 'UPDATING...'}[kind], now)
        return True

    def _service_io(self, now):
        job = self.io_job
        if job is None:
            return
        if self.engine.playing and self.last_clock_us is not None:
            if not self._clock_slack(8000):
                return
            period_us = (self.cv_period_us // 24 if self.engine.clock_source.startswith('CV')
                         else 60000000 // (self.engine.bpm * 24))
            age = time.ticks_diff(self.micros(), self.last_clock_us)
            if age < 0 or age > max(1500, period_us - 8000):
                return
            limit = 1
        else:
            limit = 4
        finished = False
        try:
            for _ in range(limit):
                next(job['steps'])
        except StopIteration:
            finished = True
        except Exception as exc:
            self.io_job = None
            self.menu.busy = False
            print('Loop storage failed:', exc)
            self.menu.show_notice(job['kind'].upper() + ' FAILED', now)
        if finished:
            self.io_job = None
            self.menu.busy = False
            try:
                result = job['result']['value']
                if job['kind'] == 'save':
                    self.menu.show_notice('SAVED ' + result, now)
                elif job['kind'] == 'load':
                    self.engine.queue_load(job['name'], result)
                    self.menu.show_notice(('QUEUED ' if self.engine.playing else 'LOADED ')
                                          + job['name'], now)
                elif job['kind'] == 'preset':
                    role, channel, bank, program, status = result
                    self.menu.show_notice(status + ' CH%d' % channel, now)
                else:
                    if self.engine.pending_load and self.engine.pending_load[1] == job['name']:
                        self.engine.pending_load = None
                    self.menu.saved_names = self.set_store.list_names()
                    self.menu.saved_keys.pop(job['name'], None)
                    self.menu.clamp_index()
                    self.menu.show_notice('DELETED ' + job['name'], now)
            except Exception as exc:
                print('Loop storage failed:', exc)
                self.menu.show_notice(job['kind'].upper() + ' FAILED', now)

    def _service_settings(self, now):
        if self.settings_dirty_at is None and self.settings_job is None:
            return
        if self.io_job is not None or not self._clock_slack(12000):
            return
        if self.settings_job is None:
            if time.ticks_diff(now, self.settings_dirty_at) < 800:
                return
            self.settings_job = (self.settings_dirty_at, self.settings.save_steps())
        stamp, steps = self.settings_job
        try:
            next(steps)
        except StopIteration:
            self.settings_job = None
            if self.settings_dirty_at == stamp:
                self.settings_dirty_at = None
        except Exception as exc:
            self.settings_job = None
            self.settings_dirty_at = None
            print('Looper settings save failed:', exc)
            self.menu.show_notice('SETTINGS FAILED', now)

    def _deliver_input(self, now, delta, click, held, activity):
        self.menu.handle(now, delta, click, held, activity)

    def _service_auto(self):
        engine = self.engine
        if (not engine.auto_play or not engine.playing or engine.pending_load or
                engine.mix_state or engine.auto_state):
            self.auto_job = None
            return
        signature = engine.auto_signature()
        if engine.auto_plan is not None:
            if engine.auto_plan['signature'] == signature:
                return
            engine.auto_plan = None
        if self.auto_job is not None and self.auto_job['signature'] != signature:
            self.auto_job = None
        if self.io_job is not None or not self._clock_slack(12000):
            return
        if self.auto_job is None:
            result = {}
            self.auto_job = {'signature': signature, 'result': result,
                             'steps': engine.prepare_auto_steps(result)}
        job = self.auto_job
        try:
            next(job['steps'])
        except StopIteration:
            if engine.auto_play and engine.auto_signature() == job['signature']:
                engine.auto_plan = job['result']['value']
            self.auto_job = None
        except Exception as exc:
            self.auto_job = None
            print('AUTO PLAY preparation failed:', exc)
            engine.set_auto_play(False)
            self.menu.show_notice('AUTO PLAY FAILED', time.ticks_ms())

    def _ui(self, _):
        if not self.running:
            return
        try:
            if not self._clock_slack(9000):
                tulip.defer(self._ui, None, 10)
                return
            now = time.ticks_ms()
            self.sink.service(now)
            panel_busy = (hasattr(tulip, 'i2c_bg_pending') and
                          tulip.i2c_bg_pending() > 0)
            asleep = self.menu.sleeping
            position, pressed = ((self.sampler.position, self.sampler.pressed)
                                 if panel_busy else self.sampler.poll(now))
            wake = asleep and (pressed or position != self.controls.position)
            delta, click, held, activity = self.controls.poll(now, position, pressed, consume=wake)
            self._deliver_input(now, delta, click, held, activity or wake)
            if self.engine.bpm != self.tempo and self.engine.clock_source == 'INTERNAL':
                self.tempo = self.engine.bpm
                sequencer.tempo(self.tempo)
            self._service_io(now)
            self._service_settings(now)
            self._service_auto()
            # Coalesce fast encoder turns. Never build another OLED frame while
            # the previous background I2C transfer is still on the bus.
            frame_ms = (500 if self.menu.sleeping else
                        120 if self.menu.dirty else 400)
            animated = self.menu.sleeping
            due = (self.io_job is None and not panel_busy and
                   time.ticks_diff(now, self.last_frame) >= frame_ms and
                   (wake or animated or self.menu.dirty or self.menu.status_dirty))
            retry = False
            if due:
                if self._clock_slack(12000):
                    if self.menu.bam_active(now):
                        self.menu.render_bam(self.display, now)
                    elif self.menu.sleeping:
                        self.galaxy.render(self.display, self.engine)
                    elif self.menu.dirty:
                        self.menu.render(self.display)
                    else:
                        self.menu.render_status(self.display)
                    amyboard.display_refresh()
                    self.last_frame = now
                else:
                    retry = True
            tulip.defer(self._ui, None, 10 if retry or self.io_job is not None or
                        self.sampler.i2c is not None else 20)
        except Exception as exc:
            self.fail(exc)

    def stop(self):
        self.running = False
        if self.midi_callback_added and midi is not None:
            midi.remove_callback(self._midi_input)
            self.midi_callback_added = False
        if hasattr(tulip, 'external_midi_sync'):
            tulip.external_midi_sync(False)
        if self.sequence is not None:
            self.sequence.clear()
            self.sequence = None
        try:
            self.engine.stop()
        finally:
            self.sink.close()

    def fail(self, exc):
        self.last_error = str(exc)
        print('AMY looper stopped:', exc)
        try:
            self.stop()
        finally:
            self.display.fill(0)
            self.display.text('LOOPER STOPPED', 0, 0, 255)
            self.display.text(self.last_error[:16], 0, 20, 255)
            self.display.text('REPL available', 0, 48, 255)
            amyboard.display_refresh()


def start():
    global _app
    if _app is not None:
        _app.stop()
    _app = App()
    try:
        _app.start()
    except Exception:
        _app.stop()
        raise
    return _app


def stop():
    if _app is not None:
        _app.stop()
