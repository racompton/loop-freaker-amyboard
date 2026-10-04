"""Render the actual menu/animation code with Pillow, without an AMYboard.

Requires Pillow. The preview uses an approximation of the OLED's 8x8 font.
Run from the repository root. This script is not copied to the board.
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loop_engine import Engine
from loop_ui import Menu, NoteGalaxy
import loop_patterns


class Canvas:
    def __init__(self):
        self.image = Image.new('RGB', (128, 128), '#060a13')
        self.draw = ImageDraw.Draw(self.image)
        try:
            self.font = ImageFont.truetype('/System/Library/Fonts/Monaco.ttf', 9)
        except OSError:
            self.font = ImageFont.load_default()

    def color(self, value):
        return '#c0ecff' if value else '#060a13'

    def fill(self, value):
        self.draw.rectangle((0, 0, 127, 127), fill=self.color(value))

    def text(self, text, x, y, color):
        for index, char in enumerate(text):
            self.draw.text((x + index * 8, y-2), char, font=self.font, fill=self.color(color))

    def fill_rect(self, x, y, width, height, color):
        if width > 0 and height > 0:
            self.draw.rectangle((x, y, x+width-1, y+height-1), fill=self.color(color))

    def rect(self, x, y, width, height, color):
        self.draw.rectangle((x, y, x+width-1, y+height-1), outline=self.color(color))

    def hline(self, x, y, width, color):
        self.line(x, y, x+width-1, y, color)

    def line(self, x, y, x2, y2, color):
        self.draw.line((x, y, x2, y2), fill=self.color(color))

    def pixel(self, x, y, color):
        self.draw.point((x, y), fill=self.color(color))


class SilentOutputs:
    def __getattr__(self, _):
        return lambda *args: None


def main():
    random.seed(81)
    engine = Engine(SilentOutputs())
    galaxy = NoteGalaxy()
    engine.on_note = galaxy.note
    engine.start()
    for t in engine.tracks.values():
        t.muted = False
    menu = Menu(engine)
    canvas = Canvas()
    menu.render(canvas)
    images = [canvas.image.copy()]
    menu.enter('lead')
    menu.index = 5
    menu.editing = True
    menu.render(canvas)
    images.append(canvas.image.copy())
    menu.back()
    menu.enter('settings')
    menu.index = 2
    menu.render(canvas)
    images.append(canvas.image.copy())
    frames = []
    for frame in range(160):
        if frame % 2 == 0:
            engine.advance()
        else:
            engine.release_drums()
        galaxy.render(canvas, engine)
        frames.append(canvas.image.resize((384, 384), Image.Resampling.NEAREST))
        if frame == 32:
            images.append(canvas.image.copy())
    sheet = Image.new('RGB', (1152, 340), '#111722')
    draw = ImageDraw.Draw(sheet)
    for i, (image, label) in enumerate(zip(images, ['MAIN MENU', 'SYNTH / NOTE COUNT', 'SETTINGS', 'NOTE GALAXY'])):
        x = 24 + i * 284
        draw.text((x, 18), label, fill='#c0ecff')
        sheet.paste(image.resize((256, 256), Image.Resampling.NEAREST), (x, 44))
    draw.text((24, 316), 'Simulated 128 x 128 OLED layouts; physical OLED font may differ.', fill='#8ca0b7')
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    sheet.save(assets / 'looper-preview.png')
    menu.back()
    menu.index = 0
    menu.click(0)
    menu.render_bam(canvas, 270)
    canvas.image.resize((512, 512), Image.Resampling.NEAREST).save(assets / 'looper-bam.png')
    class PreviewSets:
        def list_names(self):
            return ['FUCKING DUCK', 'COSMIC GOBLIN']
        def load(self, name):
            return {'scale_name': 'C Major' if name == 'FUCKING DUCK' else 'C Minor'}
    engine.scale_name = 'C Major'
    engine.scale = loop_patterns.SCALE_PRESETS['C Major'][:]
    load_menu = Menu(engine, set_store=PreviewSets())
    load_menu.enter('load')
    load_menu.index = 1
    load_menu.render(canvas)
    canvas.image.resize((512, 512), Image.Resampling.NEAREST).save(assets / 'looper-load.png')
    roll_menu = Menu(engine)
    roll_menu.enter('drum')
    roll_menu.index = roll_menu.rows().index('DRUM ROLL')
    roll_menu.click(0)
    roll_menu.index = 1
    roll_menu.editing = True
    roll_menu.render(canvas)
    canvas.image.resize((512, 512), Image.Resampling.NEAREST).save(assets / 'looper-drum-roll.png')
    frames[0].save(assets / 'looper-screensaver.gif', save_all=True, append_images=frames[1:],
                   duration=80, loop=0, optimize=True)
    print(assets / 'looper-preview.png')


if __name__ == '__main__':
    main()
