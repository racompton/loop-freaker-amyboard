"""Small device-wide settings, separate from saved musical sets."""
import os

try:
    import ujson as json
except ImportError:
    import json

from loop_preset_preferences import PROGRAM_COUNTS

DEFAULT_PATH = '/user/looper_settings.json'
CLOCK_SOURCES = ('INTERNAL', 'MIDI IN', 'CV1 IN', 'CV2 IN')


class DeviceSettings:
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self.ghost_percent = 35
        self.ghost_velocity = 70
        self.ghost_on_muted = False
        self.swing = 0
        self.clock_source = 'INTERNAL'
        self.program_counts = {role: tuple(counts) for role, counts in PROGRAM_COUNTS.items()}
        try:
            with open(path, 'r') as handle:
                data = json.loads(handle.read())
            if not isinstance(data, dict) or data.get('version') != 1:
                return
            ghost = data.get('ghost_percent')
            if type(ghost) is int and 0 <= ghost <= 100:
                self.ghost_percent = ghost
            velocity = data.get('ghost_velocity')
            if type(velocity) is int and 1 <= velocity <= 127:
                self.ghost_velocity = velocity
            if type(data.get('ghost_on_muted')) is bool:
                self.ghost_on_muted = data['ghost_on_muted']
            swing = data.get('swing')
            if type(swing) is int and 0 <= swing <= 3:
                self.swing = swing
            source = data.get('clock_source')
            if source in CLOCK_SOURCES:
                self.clock_source = source
            ranges = data.get('program_counts', {})
            for role in PROGRAM_COUNTS:
                counts = ranges.get(role) if isinstance(ranges, dict) else None
                if (isinstance(counts, (list, tuple)) and 1 <= len(counts) <= 16 and
                        all(type(count) is int and 1 <= count <= 128 for count in counts)):
                    self.program_counts[role] = tuple(counts)
        except (OSError, ValueError, TypeError):
            pass

    def save_steps(self):
        data = {'version': 1, 'ghost_percent': self.ghost_percent,
                'ghost_velocity': self.ghost_velocity,
                'ghost_on_muted': self.ghost_on_muted, 'swing': self.swing,
                'clock_source': self.clock_source,
                'program_counts': self.program_counts}
        encoded = json.dumps(data)
        yield
        temporary = self.path + '.tmp'
        try:
            with open(temporary, 'w') as handle:
                handle.write(encoded)
            yield
            os.rename(temporary, self.path)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise

    def save(self):
        for _ in self.save_steps():
            pass
