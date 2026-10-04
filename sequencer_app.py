"""Nonblocking AMYboard looper entry point. start() returns to the REPL."""
import time
import amyboard
import tulip
import sequencer
from loop_engine import Engine
from loop_ui import Controls, Menu, NoteGalaxy
from loop_hardware import Outputs
from loop_sets import Sets
from loop_preset_preferences import PresetPreferences

_app = None


class App:
    def __init__(self):
        self.running = False
        self.sequence = None
        self.sink = Outputs()
        self.engine = Engine(self.sink, generate=False)
        self.engine.preset_preferences = PresetPreferences()
        # Make the first set exactly as RANDOMIZE would, before MIDI Start.
        self.engine.randomize_all()
        if amyboard.display is None:
            amyboard.init_display()
        self.display = amyboard.display
        self.encoder = amyboard.encoder()
        now = time.ticks_ms()
        self.controls = Controls(now, self.encoder.read(), time.ticks_diff)
        self.set_store = Sets()
        self.menu = Menu(self.engine, now, time.ticks_diff, self.set_store)
        self.menu.io_request = self._queue_io
        self.io_job = None
        self.last_clock_us = None
        self.micros = time.ticks_us if hasattr(time, 'ticks_us') else lambda: time.ticks_ms() * 1000
        self.engine.on_load = lambda name: self.menu.show_notice('LOADED ' + name, time.ticks_ms())
        self.galaxy = NoteGalaxy()
        self.engine.on_note = lambda role, note, velocity: (
            self.galaxy.note(role, note, velocity) if self.menu.screensaver_enabled else None)
        self.last_frame = now
        self.tempo = self.engine.bpm
        self.last_pulse = -1
        self.origin = None
        self.was_playing = False
        self.last_error = None

    def start(self):
        self.running = True
        self.menu.render(self.display)
        amyboard.display_refresh()
        self.last_frame = time.ticks_ms()
        sequencer.tempo(self.tempo)
        self.engine.start()
        # 96 divisions/bar = 24 MIDI clock pulses/quarter. Driven by AMY's
        # clock rather than accumulating sleep() and OLED transfer durations.
        self.sequence = sequencer.TulipSequence(96, self._tick)
        if hasattr(tulip, 'sequencer_start'):
            tulip.sequencer_start()
        tulip.defer(self._ui, None, 20)

    def _clock_slack(self, reserve_us):
        """Keep UI work away from the next 24-PPQ MIDI pulse."""
        if not self.engine.playing:
            return True
        if self.last_clock_us is None:
            return False
        period_us = 60000000 // (self.engine.bpm * 24)
        age = time.ticks_diff(self.micros(), self.last_clock_us)
        return 0 <= age < max(0, period_us - reserve_us)

    def _tick(self, tick):
        if not self.running:
            return
        try:
            if not self.engine.playing:
                self.was_playing = False
                return
            if not self.was_playing:
                self.origin = tick
                self.last_pulse = -1
                self.was_playing = True
            pulse = (tick - self.origin) // max(1, sequencer.PPQ // 24)
            if pulse <= self.last_pulse:
                return
            self.sink.clock()
            step = pulse // 6
            if step != self.last_pulse // 6:
                self.engine.step = step
                self.engine.advance()
                if self.engine.bpm != self.tempo:
                    self.tempo = self.engine.bpm
                    sequencer.tempo(self.tempo)
                    if self.menu.page == 'main' and self.menu.bam_at is None:
                        self.menu.dirty = True
            if pulse % 6 >= 3:
                self.engine.release_drums()
            self.last_pulse = pulse
            self.last_clock_us = self.micros()
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
        if self.engine.playing:
            if self.last_clock_us is None:
                return
            period_us = 60000000 // (self.engine.bpm * 24)
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
                    self.menu.show_notice('DELETED ' + job['name'], now)
            except Exception as exc:
                print('Loop storage failed:', exc)
                self.menu.show_notice(job['kind'].upper() + ' FAILED', now)

    def _deliver_input(self, now, delta, click, held, activity):
        self.menu.handle(now, delta, click, held, activity)

    def _ui(self, _):
        if not self.running:
            return
        try:
            if not self._clock_slack(5500):
                tulip.defer(self._ui, None, 3)
                return
            now = time.ticks_ms()
            self.sink.service(now)
            asleep = self.menu.sleeping
            position, pressed = self.encoder.read(), self.encoder.button()
            wake = asleep and (pressed or position != self.controls.position)
            delta, click, held, activity = self.controls.poll(now, position, pressed, consume=wake)
            self._deliver_input(now, delta, click, held, activity or wake)
            if self.engine.bpm != self.tempo:
                self.tempo = self.engine.bpm
                sequencer.tempo(self.tempo)
            self._service_io(now)
            # Coalesce fast encoder turns. Never build another OLED frame while
            # the previous background I2C transfer is still on the bus.
            frame_ms = (500 if self.menu.sleeping else
                        120 if hasattr(tulip, 'i2c_bg_write') else 250)
            animated = self.menu.sleeping
            panel_busy = (hasattr(tulip, 'i2c_bg_pending') and
                          tulip.i2c_bg_pending() > 0)
            if (self.io_job is None and not panel_busy and
                    self._clock_slack(9000) and
                    time.ticks_diff(now, self.last_frame) >= frame_ms and
                    (wake or animated or self.menu.dirty)):
                if self.menu.bam_active(now):
                    self.menu.render_bam(self.display, now)
                elif self.menu.sleeping:
                    self.galaxy.render(self.display, self.engine)
                else:
                    self.menu.render(self.display)
                amyboard.display_refresh()
                self.last_frame = now
            tulip.defer(self._ui, None, 5 if self.io_job is not None else 20)
        except Exception as exc:
            self.fail(exc)

    def stop(self):
        self.running = False
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
