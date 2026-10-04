import sys
sys.path.append("/user/current")

# --- Safe mode: hold encoder button at boot to skip menu ---
_SAFE_MODE = False
try:
    import time
    import amyboard
    _i2c = amyboard.get_i2c()
    # The board's driver auto-detects SSD1327 and SH1107 displays.
    amyboard.init_display()
    _d = amyboard.display
    _d.fill(0)
    _d.text("Hold btn: safe", 0, 0, 255)
    _d.text("mode in 4s...", 0, 16, 255)
    amyboard.display_refresh()

    # Check Adafruit seesaw button
    _SS_ADDR = None
    for _a in (0x36, 0x37, 0x49):
        if _a in _i2c.scan():
            _SS_ADDR = _a
            break

    _PIN = 24
    _MASK = 1 << _PIN
    if _SS_ADDR:
        _i2c.writeto(_SS_ADDR, bytes([0x01, 0x03]) + (int(_MASK)).to_bytes(4, "big"))
        _i2c.writeto(_SS_ADDR, bytes([0x01, 0x0B]) + (int(_MASK)).to_bytes(4, "big"))
        _i2c.writeto(_SS_ADDR, bytes([0x01, 0x05]) + (int(_MASK)).to_bytes(4, "big"))

    _deadline = time.ticks_add(time.ticks_ms(), 4000)
    while time.ticks_diff(_deadline, time.ticks_ms()) > 0:
        _held = False
        if _SS_ADDR:
            try:
                _i2c.writeto(_SS_ADDR, bytes([0x01, 0x04]))
                time.sleep_ms(2)
                _bulk = int.from_bytes(_i2c.readfrom(_SS_ADDR, 4), "big")
                _held = (_bulk & _MASK) == 0  # pull-up: LOW = pressed
            except Exception:
                pass
        if _held:
            _SAFE_MODE = True
            break
        time.sleep_ms(50)

    _d.fill(0)
    if _SAFE_MODE:
        _d.text("SAFE MODE", 0, 0, 255)
        _d.text("Thonny ready", 0, 16, 255)
    else:
        _d.text("Starting...", 0, 0, 255)
    amyboard.display_refresh()
    time.sleep_ms(500)

except Exception:
    pass  # If anything fails, just boot normally

if not _SAFE_MODE:
    try:
        # A sketch-only restart preserves sys.modules on AMYboard. Stop the old
        # app and reload its modules so uploaded edits take effect immediately.
        if "sequencer_app" in sys.modules:
            try:
                sys.modules["sequencer_app"].stop()
            except Exception:
                pass
            for _module in ("sequencer_app", "loop_sets", "loop_preset_preferences",
                            "loop_ui", "loop_engine",
                            "loop_patterns", "loop_random", "loop_hardware",
                            "loop_amy_settings", "menu"):
                if _module in sys.modules:
                    del sys.modules[_module]
            import gc
            gc.collect()
        import sequencer_app
        sequencer_app.start()
    except Exception as _error:
        # Keep the uploaded sketch and REPL available on older firmware or a
        # missing-file install, rather than triggering firmware self-recovery.
        print("AMY looper startup failed:", _error)
        try:
            amyboard.cv_out(0, channel=1)
            _d.fill(0)
            _d.text("LOOPER ERROR", 0, 0, 255)
            _d.text(str(_error)[:16], 0, 20, 255)
            _d.text("REPL available", 0, 48, 255)
            amyboard.display_refresh()
        except Exception:
            pass
