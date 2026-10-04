"""AMYboard external MIDI and pitch/gate CV outputs."""
import time
import amyboard
import tulip


class Outputs:
    def __init__(self):
        self.pending_programs = {}
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
        tulip.midi_out(bytes((0xF8,)))

    def transport(self, playing):
        tulip.midi_out(bytes((0xFA if playing else 0xFC,)))

    def note_on(self, track, note, velocity):
        # Wait out the drum bank's 50ms settle time without blocking sequencing.
        if track.channel not in self.pending_programs:
            tulip.midi_out(bytes((0x90 | (track.channel - 1), note, velocity)))

    def note_off(self, track, note):
        tulip.midi_out(bytes((0x80 | (track.channel - 1), note, 0)))

    def preset(self, track):
        channel = track.channel - 1
        tulip.midi_out(bytes((0xB0 | channel, 0, track.bank)))
        if track.role == 'drum':
            self.pending_programs[track.channel] = (
                time.ticks_add(time.ticks_ms(), 50), track.program)
        else:
            tulip.midi_out(bytes((0xC0 | channel, track.program)))

    def release_channel(self, channel):
        self.pending_programs.pop(channel, None)

    def service(self, now):
        for channel in list(self.pending_programs):
            due, program = self.pending_programs[channel]
            if time.ticks_diff(now, due) >= 0:
                tulip.midi_out(bytes((0xC0 | (channel - 1), program)))
                del self.pending_programs[channel]

    def close(self):
        self.pending_programs.clear()
        self.cv(0, 1)
