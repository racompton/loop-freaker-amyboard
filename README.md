# ~L00P FR34K3R~

A four-track generative MIDI looper for [AMYboard](https://github.com/shorepine/tulipcc). Turn one encoder to reshape SYNTH, BASS, CHORD, and DRUMS; watch the menu on a 128×128 OLED; send all four tracks to external MIDI gear. CV1 can follow one track's pitch and CV2 its gate.

![OLED menu preview](assets/looper-preview.png)

**Current version:** [v0.4.0](VERSION) · [Download releases](https://github.com/racompton/loop-freaker-amyboard/releases)

## What it does

- Starts with a fresh randomized set on every boot. The main **RANDOMIZE** action changes to a Camelot-compatible key and generates new notes, presets, and mutes; each instrument also has its own RANDOMIZE action.
- Lets you change loop length from 1–64 sixteenth-note steps, note count, octave, playback direction, chord size, MIDI channel, preset, and mute state from the encoder. Shortening and re-expanding a loop restores the same hidden notes.
- CHORD shows the Korg minilogue's P1–P200 as one continuous range, sending Bank Select LSB 0/1 and Program Change 0–99 to access both hundreds.
- Saves sets under unique two-word names and highlights sets compatible with the current key. Preset preferences are specific to the instrument and MIDI channel.
- With **SETTINGS → MIX** on by default, LOAD brings in BASS on the next beat, SYNTH 32 beats later, CHORD another 32 beats later, and DRUMS at the next automatic roll. If automatic rolls are off, DRUMS enter at the next kick break. The OLED announces each handoff and `MIX COMPLETE!`. Turn MIX off for the original all-at-once load.
- Optional **SETTINGS → AUTO PLAY** creates fresh Camelot-compatible loops and presets, then brings the four instruments in at 32-beat intervals in a random order, beginning 64 beats after a drum roll. Rolls repeat every 128 beats; snare, clap, and two other random drum parts drop out for 32 beats before each roll. DRUMS stays unmuted and at least two melodic instruments remain audible. The OLED shows the old/new keys during transitions and a countdown to the next instrument switch. AUTO PLAY is off by default; when off, the bottom line shows the drum-roll countdown and instrument mute states.
- Groups saved sets by genre, with the oldest first and newest last within each group. A persistent order index keeps that arrangement after reboot. Deleting a set no longer leaves the LOAD cursor past the end of the list.
- Generates HOUSE, TECHNO, BREAKS, ELECTRO, and DRUM'N'BASS patterns, with genre tempo transitions over 64 beats. Drums can add ghost notes, fills, and a recurring kick breakdown.
- Sends external MIDI notes and 24-PPQ clock. CV defaults to 1 V/octave with C4 at 0 V and a 5 V gate. Internal AMY sound playback is disabled.
- Offers an optional note visualizer screensaver, off by default. OLED updates are coalesced, and Adafruit seesaw encoder reads are split into short steps so the menu stays responsive at faster genre tempos.

See [SEQUENCER.md](SEQUENCER.md) for the full menu map and behavior.

## Hardware

- AMYboard running recent Tulip/AMYboard MicroPython firmware with `amyboard.encoder()`, `amyboard.cv_out()`, and `sequencer.TulipSequence`.
- A supported 128×128 SSD1327 or SH1107 OLED and rotary encoder on the board's **front I²C accessory port**.
- External MIDI instrument or interface for sound; optional CV destination.

The OLED and encoder are detected by AMYboard's built-in drivers. See the [AMYboard accessory guide](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/accessories.md) for wiring and compatible controls.

## Install

Download the latest release archive, extract it, and copy these nine `.py` files to `/user/current/` on your AMYboard: `loop_random.py`, `loop_patterns.py`, `loop_engine.py`, `loop_sets.py`, `loop_preset_preferences.py`, `loop_ui.py`, `loop_hardware.py`, `sequencer_app.py`, and `sketch.py`. **Copy `sketch.py` last.** Back up `/user/current/`, `/user/sets/`, and the preset preference files first if you are upgrading.

For example, with [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) and a board connected over USB:

```bash
mpremote connect /dev/cu.usbmodem1101 resume fs cp \
  loop_random.py loop_patterns.py loop_engine.py loop_sets.py \
  loop_preset_preferences.py loop_ui.py loop_hardware.py \
  sequencer_app.py :/user/current/
mpremote connect /dev/cu.usbmodem1101 resume fs cp sketch.py :/user/current/
```

Replace the example serial port with your board's actual port. Reset the board after copying. During the four-second startup prompt, **hold the encoder button for SAFE MODE** to skip the looper and regain the REPL if an update needs repair.

The main screen starts at RANDOMIZE. Turn the knob to move, click to select or edit, and long-press to go back. Defaults are 124 BPM; MIDI channels SYNTH 4, BASS 3, CHORD 6, DRUMS 10; CHORD starts muted. TECHNO targets 130 BPM and BREAKS 135 BPM. The board begins playing after it draws the first menu screen.

## Development and releases

Run the software tests on desktop Python:

```bash
python3 -m unittest discover -s tests -q
python3 tools/package.py
```

[`VERSION`](VERSION) is the single source of the release number. From a checkout with push access, run `python3 tools/publish.py patch` (or `minor`/`major`) after making changes. It tests the code, bumps `VERSION`, commits the changes, and pushes the branch and matching `vX.Y.Z` tag. [GitHub Actions](.github/workflows/release.yml) then tests that tag and automatically uploads a versioned ZIP to GitHub Releases. Ordinary code commits also upload to the public repository when pushed; releases are created only for version tags. This workflow needs Git credentials with repository push access.

OLED rendering and MIDI callbacks still share MicroPython execution time, so the UI throttling improves timing but does not promise hard real-time MIDI. Test your full hardware setup before relying on a performance-critical clock.

## Credits and license

The musical generators were adapted from [racompton/midi-generator](https://github.com/racompton/midi-generator). The hardware and MicroPython APIs come from the [Tulip/AMYboard project](https://github.com/shorepine/tulipcc). This project is released under the [MIT License](LICENSE).
