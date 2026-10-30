"""AMYboard external MIDI and pitch/gate CV outputs."""
import time
import amyboard
import tulip
from loop_preset_preferences import preset_number


class Outputs:
    def __init__(self):
        self.pending_programs = {}
        self.midi_uart = None
        # Firmware MIDI clock output can stall the AMYboard sequencer. Keep
        # transport in internal-clock mode and send F8 from our callback.
        self.native_clock = False
        if hasattr(tulip, 'external_midi_sync'):
            tulip.external_midi_sync(False)
        # On AMYboard's current firmware tulip.midi_out() can take a full
        # second per message when USB MIDI is not being consumed. Use the
        # physical TRS MIDI pin directly so notes never block the clock.
        if getattr(tulip, 'board', lambda: None)() == 'AMYBOARD':
            from machine import UART
            pin = 15 if getattr(amyboard, 'midi_type', lambda: 'A')() == 'B' else 14
            self.midi_uart = UART(2, baudrate=31250, tx=pin)
        # Clear waveform-to-CV routing before taking ownership of the DAC.
        if hasattr(amyboard, 'set_cv_out'):
            amyboard.set_cv_out(0, 0)
            amyboard.set_cv_out(1, 0)
        if hasattr(amyboard, 'init_gp8413'):
            amyboard.init_gp8413()
        self.cv(0, 0)
        self.cv(0, 1)

    def cv(self, volts, channel):
        amyboard.cv_out(volts, channel=channel)

    def clock(self):
        self._midi(bytes((0xF8,)))

    def _midi(self, message):
        if self.midi_uart is not None:
            self.midi_uart.write(message)
        else:
            tulip.midi_out(message)

    def transport(self, playing):
        self._midi(bytes((0xFA if playing else 0xFC,)))

    def note_on(self, track, note, velocity):
        # Wait out the drum bank's 50ms settle time without blocking sequencing.
        if track.channel not in self.pending_programs:
            self._midi(bytes((0x90 | (track.channel - 1), note, velocity)))

    def note_off(self, track, note):
        self._midi(bytes((0x80 | (track.channel - 1), note, 0)))

    def preset(self, track):
        channel = track.channel - 1
        if track.role == 'chords':
            # The original minilogue displays 1–200 but accepts PC 0–99.
            # CC0 is always 0; CC32 selects either hundred.
            position = preset_number('chords', track.bank, track.program) - 1
            self._midi(bytes((0xB0 | channel, 0, 0)))
            self._midi(bytes((0xB0 | channel, 32, position // 100)))
            self._midi(bytes((0xC0 | channel, position % 100)))
            return
        self._midi(bytes((0xB0 | channel, 0, track.bank)))
        if track.role == 'drum':
            self.pending_programs[track.channel] = (
                time.ticks_add(time.ticks_ms(), 50), track.program)
        else:
            self._midi(bytes((0xC0 | channel, track.program)))

    def release_channel(self, channel):
        self.pending_programs.pop(channel, None)

    def service(self, now):
        for channel in list(self.pending_programs):
            due, program = self.pending_programs[channel]
            if time.ticks_diff(now, due) >= 0:
                self._midi(bytes((0xC0 | (channel - 1), program)))
                del self.pending_programs[channel]

    def close(self):
        self.pending_programs.clear()
        self.cv(0, 1)
        if self.midi_uart is not None:
            self.midi_uart.deinit()
            self.midi_uart = None
            if hasattr(amyboard, 'set_midi_type'):
                amyboard.set_midi_type(amyboard.midi_type())
