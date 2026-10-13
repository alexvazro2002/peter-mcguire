import time
import threading
import tkinter as tk
from tkinter import ttk
from desktop_input import keyboard, require_x11
try:
    require_x11()
except RuntimeError as error:
    print("ERROR: " + str(error))
    raise SystemExit(1)
import pyautogui
import sys
from pynput import mouse

# ==========================================
# CONFIGURE THESE COORDINATES
# ==========================================

FIRST_CLICK = (150, 200)
FIRST_CLICK2 = (130, 260)
SECOND_CLICK = (130, 650)

# ==========================================

pyautogui.FAILSAFE = True

paused = False
terminate = False

max_iterations = None
iterations = 0

# Text to type (selected at startup)
text_to_type = "Leisure ind"

# Auto-select mode variables
auto_select_mode = False
auto_y_distance = None
auto_start_y = None
auto_start_x = None

time.sleep(2)

# ==========================================
# STARTUP WINDOW
# ==========================================

selection_done = threading.Event()
point_selection_done = threading.Event()
captured_points = []


def capture_points_with_ctrl():
    """Capture two points with Ctrl+Click"""
    global captured_points
    captured_points = []

    print("\n" + "="*60)
    print("POINT SELECTION MODE")
    print("="*60)
    print("Ctrl+Left Click on your FIRST reservation")
    print("Then Ctrl+Left Click on your SECOND reservation")
    print("="*60 + "\n")

    def on_click(x, y, button, pressed):
        if pressed and button == mouse.Button.left:
            # Check if Ctrl is held
            if keyboard.is_pressed('ctrl'):
                captured_points.append((x, y))
                print(f"✓ Point {len(captured_points)} captured: ({x}, {y})")

                if len(captured_points) == 2:
                    point_selection_done.set()
                    return False  # Stop listener

    # Start mouse listener
    with mouse.Listener(on_click=on_click) as listener:
        listener.join()


def choose_iterations():
    global max_iterations, text_to_type, auto_select_mode, auto_y_distance, auto_start_y, auto_start_x

    root = tk.Tk()
    root.title("Mews Automation Settings")
    root.geometry("400x420")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg="#f0f0f0")

    # Header
    header = tk.Frame(root, bg="#4a90e2", height=60)
    header.pack(fill="x")
    header.pack_propagate(False)

    tk.Label(
        header,
        text="⚙ Mews Segment Updater",
        font=("Segoe UI", 16, "bold"),
        bg="#4a90e2",
        fg="white"
    ).pack(pady=15)

    # Main container
    main_frame = tk.Frame(root, bg="#f0f0f0")
    main_frame.pack(fill="both", expand=True, padx=20, pady=15)

    # Iterations section
    tk.Label(
        main_frame,
        text="Number of Reservations:",
        font=("Segoe UI", 11, "bold"),
        bg="#f0f0f0"
    ).pack(anchor="w", pady=(0, 5))

    iter_frame = tk.Frame(main_frame, bg="#f0f0f0")
    iter_frame.pack(fill="x", pady=(0, 15))

    iter_entry = tk.Entry(
        iter_frame,
        font=("Segoe UI", 11),
        width=10
    )
    iter_entry.pack(side="left")
    iter_entry.insert(0, "50")

    tk.Label(
        iter_frame,
        text="  (leave blank for unlimited)",
        font=("Segoe UI", 9),
        bg="#f0f0f0",
        fg="#666"
    ).pack(side="left")

    # Text selection section
    tk.Label(
        main_frame,
        text="Change Segment To:",
        font=("Segoe UI", 11, "bold"),
        bg="#f0f0f0"
    ).pack(anchor="w", pady=(10, 5))

    text_choice = tk.StringVar(value="Leisure ind")

    radio_frame = tk.Frame(main_frame, bg="#f0f0f0")
    radio_frame.pack(fill="x")

    tk.Radiobutton(
        radio_frame,
        text="🏖 Leisure Individual",
        variable=text_choice,
        value="Leisure ind",
        bg="#f0f0f0",
        font=("Segoe UI", 10),
        selectcolor="#ffffff"
    ).pack(side="left", padx=20)

    tk.Radiobutton(
        radio_frame,
        text="💼 Business Individual",
        variable=text_choice,
        value="Business ind",
        bg="#f0f0f0",
        font=("Segoe UI", 10),
        selectcolor="#ffffff"
    ).pack(side="left")

    # Info text
    info_text = tk.Text(
        main_frame,
        height=3,
        font=("Segoe UI", 9),
        bg="#ffffcc",
        relief="flat",
        padx=10,
        pady=10,
        wrap=tk.WORD
    )
    info_text.pack(fill="x", pady=(15, 0))
    info_text.insert("1.0",
        "The automation will:\n"
        "• Update segment for each reservation\n"
        "• Process the number of iterations you specified"
    )
    info_text.config(state="disabled")

    def select_points():
        """Handle Select Points button"""
        global auto_select_mode, auto_y_distance, auto_start_y, auto_start_x, max_iterations, text_to_type

        text_to_type = text_choice.get()
        auto_select_mode = True
        max_iterations = 7  # 2 manual clicks + 5 auto clicks = 7 total

        root.destroy()

        # Capture the two points
        capture_points_with_ctrl()
        point_selection_done.wait()

        # Calculate Y distance and store X coordinate from second click
        auto_start_x = captured_points[1][0]
        auto_start_y = captured_points[1][1]
        auto_y_distance = captured_points[1][1] - captured_points[0][1]

        print(f"\n✓ You clicked 2 reservations manually")
        print(f"✓ Starting auto-clicks from position: ({auto_start_x}, {auto_start_y})")
        print(f"✓ Y-distance: {auto_y_distance} pixels")
        print(f"✓ Will auto-click 5 more reservations (7 total)\n")

        selection_done.set()

    def start_automation():
        global max_iterations, text_to_type, auto_select_mode

        text_to_type = text_choice.get()
        auto_select_mode = False

        iter_val = iter_entry.get().strip()
        if iter_val:
            try:
                max_iterations = int(iter_val)
            except ValueError:
                max_iterations = None
        else:
            max_iterations = None

        selection_done.set()
        root.destroy()

    # Buttons frame
    button_frame = tk.Frame(main_frame, bg="#f0f0f0")
    button_frame.pack(pady=(15, 0))

    # Select Points button
    tk.Button(
        button_frame,
        text="🎯 Select Points",
        command=select_points,
        bg="#e67e22",
        fg="white",
        font=("Segoe UI", 11, "bold"),
        relief="flat",
        padx=20,
        pady=10,
        cursor="hand2"
    ).pack(side="left", padx=5)

    # Start button
    tk.Button(
        button_frame,
        text="Start Automation",
        command=start_automation,
        bg="#27ae60",
        fg="white",
        font=("Segoe UI", 11, "bold"),
        relief="flat",
        padx=20,
        pady=10,
        cursor="hand2"
    ).pack(side="left", padx=5)

    root.mainloop()


# ==========================================
# MAIN AUTOMATION
# ==========================================

def process_single_reservation():
    """Process one reservation"""
    global iterations

    if terminate or paused:
        return

    iterations += 1

    # First click (segment dropdown)
    pyautogui.click(*FIRST_CLICK)
    time.sleep(0.75)

    # Click 2
    pyautogui.click(*FIRST_CLICK2)
    time.sleep(0.5)

    # Press space
    pyautogui.press('space')
    time.sleep(0.3)

    # Click 3 (opens segment field)
    pyautogui.click(*SECOND_CLICK)
    time.sleep(0.5)

    # Type the segment text
    pyautogui.typewrite(text_to_type, interval=0.05)
    time.sleep(0.3)

    # Press Enter twice
    pyautogui.press('enter')
    time.sleep(0.3)
    pyautogui.press('enter')
    time.sleep(1.5)

    # Close tab (Ctrl+W)
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(0.7)


def run_auto_select_mode():
    """Auto-select mode: Click through reservations automatically"""
    global paused, terminate, auto_y_distance, auto_start_y, auto_start_x

    print("\n" + "="*60)
    print("AUTO-SELECT MODE")
    print("="*60)
    print(f"Starting from position: ({auto_start_x}, {auto_start_y})")
    print(f"Y Distance: {auto_y_distance} pixels")
    print(f"Will Ctrl+Click 5 more reservations (7 total with your 2)")
    print("="*60 + "\n")

    print("Controls:")
    print("  Press 'p' to pause/resume")
    print("  Press 'q' to quit at ANY time")
    print("\nStarting in 3 seconds...")
    time.sleep(3)

    cycle = 1
    first_cycle = True

    while not terminate:
        print(f"\n{'='*60}")
        print(f"CYCLE {cycle}")
        print(f"{'='*60}")

        # Determine how many reservations to select and process
        if first_cycle:
            reservations_count = 7
            ctrl_clicks_needed = 5  # 2 manual + 5 auto = 7
            start_index = 1  # Start from position 3 (after your 2 manual clicks)
        else:
            reservations_count = 5
            ctrl_clicks_needed = 5  # Use stored positions, click all 5 automatically
            start_index = 0  # Start from position 1 (first stored click position)

        # STEP 1: Ctrl+Click reservations
        print(f"\n📋 STEP 1: Auto-selecting {ctrl_clicks_needed} reservations...")
        for i in range(ctrl_clicks_needed):
            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

            # Wait if paused
            while paused and not terminate:
                time.sleep(0.5)

            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

            # Calculate current Y position (X stays the same)
            # For first cycle: start from captured_points[1] + distance
            # For later cycles: start from captured_points[0]
            if first_cycle:
                current_y = auto_start_y + ((i + 1) * auto_y_distance)
            else:
                current_y = captured_points[0][1] + (i * auto_y_distance)

            print(f"  [{i + 1}/{ctrl_clicks_needed}] Ctrl+Clicking at ({auto_start_x}, {current_y})")

            # Ctrl+Click on the reservation at calculated position
            pyautogui.keyDown('ctrl')
            try:
                time.sleep(0.1)  # Small delay while holding ctrl
                pyautogui.click(auto_start_x, current_y)
                time.sleep(0.1)  # Small delay before releasing ctrl
            finally:
                pyautogui.keyUp('ctrl')
            time.sleep(0.65)  # Longer delay to prevent accidental opening

        if terminate:
            print("\n⏹ Stopping due to 'q' press...")
            return

        print(f"\n✓ All {reservations_count} reservations selected!")
        time.sleep(2)

        # STEP 2: Process reservations
        print(f"\n⚙️ STEP 2: Processing {reservations_count} reservations...")
        for i in range(reservations_count):
            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

            # Wait if paused
            while paused and not terminate:
                time.sleep(0.5)

            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

            print(f"\n  Processing reservation {i + 1}/{reservations_count}...")

            # Click to continue (330, 10) to open next reservation
            print(f"    Clicking continue button to open reservation...")
            pyautogui.click(330, 10)
            time.sleep(0.5)

            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

            # Process this reservation
            process_single_reservation()

            if terminate:
                print("\n⏹ Stopping due to 'q' press...")
                return

        if terminate:
            print("\n⏹ Stopping due to 'q' press...")
            return

        # After all reservations, click final button and repeat
        print(f"\n✓ Completed {reservations_count} reservations in this cycle")
        print("Clicking final button at (1850, 1030)...")
        pyautogui.click(1850, 1030)
        time.sleep(3.5)

        if terminate:
            print("\n⏹ Stopping due to 'q' press...")
            return

        # Mark first cycle as complete
        first_cycle = False

        print("\n🔄 Starting next cycle...")
        cycle += 1


def run_automation():
    """Main automation loop"""
    global paused, terminate

    print("\n" + "="*60)
    print("MEWS SEGMENT UPDATER")
    print("="*60)
    print(f"Iterations: {'Unlimited' if max_iterations is None else max_iterations}")
    print(f"Segment: {text_to_type}")
    print("="*60 + "\n")

    print("Controls:")
    print("  Press 'p' to pause/resume")
    print("  Press 'q' to quit")
    print("\nStarting in 3 seconds...")
    time.sleep(3)

    while not terminate:
        # Check for pause
        while paused and not terminate:
            time.sleep(0.5)

        if terminate:
            break

        # Check if we've reached max iterations
        if max_iterations is not None and iterations >= max_iterations:
            print(f"\n✓ Completed {iterations} iterations")
            break

        print(f"Processing reservation {iterations + 1}...")
        process_single_reservation()

    print("\n" + "="*60)
    print("✓ AUTOMATION COMPLETE!")
    print("="*60)
    print(f"Processed {iterations} reservations")


# ==========================================
# KEYBOARD CONTROLS
# ==========================================

def on_pause():
    global paused
    paused = not paused
    if paused:
        print("\n⏸ PAUSED")
    else:
        print("\n▶ RESUMED")


def on_quit():
    global terminate
    terminate = True
    print("\n⏹ STOPPING...")


keyboard.add_hotkey('p', on_pause)
keyboard.add_hotkey('q', on_quit)


# ==========================================
# MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    print("="*60)
    print("MEWS SEGMENT UPDATER")
    print("="*60)
    print("\nInitializing...")

    # Show settings window
    choose_iterations()

    # Wait for user to start
    selection_done.wait()

    # Check if auto-select mode was chosen
    if auto_select_mode:
        # Run auto-select automation
        automation_thread = threading.Thread(target=run_auto_select_mode, daemon=True)
        automation_thread.start()
    else:
        # Run normal automation
        automation_thread = threading.Thread(target=run_automation, daemon=True)
        automation_thread.start()

    # Keep main thread alive
    try:
        while automation_thread.is_alive():
            time.sleep(0.1)
    except KeyboardInterrupt:
        terminate = True
        print("\n⏹ Interrupted by user")

    finally:
        terminate = True
        pyautogui.keyUp('ctrl')
        keyboard.unhook_all_hotkeys()
