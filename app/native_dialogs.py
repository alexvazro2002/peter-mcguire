"""User-invoked native file pickers for the local browser workspace."""
import argparse
import tkinter as tk
from tkinter import filedialog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["directory", "file", "save"])
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        value = (filedialog.askdirectory(parent=root) if args.mode == "directory" else
                 filedialog.asksaveasfilename(parent=root, initialfile=args.name) if args.mode == "save" else
                 filedialog.askopenfilename(parent=root))
        print(value or "", flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
