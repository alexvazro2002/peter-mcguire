"""Original activity sequence with interruptible waits and key release."""
import threading
import sys


def main():
    from desktop_input import keyboard, require_x11
    require_x11()
    import pyautogui
    stop = threading.Event()
    keyboard.add_hotkey("q", stop.set)
    def read_stop():
        for line in sys.stdin:
            if line.strip() == "STOP":
                stop.set()
                return
    threading.Thread(target=read_stop, daemon=True).start()
    try:
        print("Starting in 3 seconds. Press Q to stop; move mouse to a corner for failsafe.")
        if stop.wait(3):
            return
        while not stop.is_set():
            for key, duration in [("w", 10), ("s", 1), ("space", 1)]:
                keyboard.press(key)
                print(f"Holding {key}")
                stop.wait(duration)
                keyboard.release(key)
                if stop.wait(0.5):
                    break
            if not stop.is_set():
                pyautogui.click()
    finally:
        for key in ("w", "s", "space"):
            keyboard.release(key)
        keyboard.unhook_all_hotkeys()
        print("Activity script stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ImportError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
