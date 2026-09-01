"""Helpers Tkinter communs aux outils HAYETT (sans dépendance externe)."""
import tkinter as tk
from tkinter import ttk, messagebox

HAYETT_NAVY = "#0b1a33"
HAYETT_BLUE = "#2457c9"
HAYETT_BG = "#f7f9fc"


def setup_style():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("HAYETT.TButton", background=HAYETT_BLUE, foreground="white", padding=8)
    style.configure("HAYETT.TLabel", background=HAYETT_BG, foreground="#101828")
    style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), foreground=HAYETT_NAVY, background=HAYETT_BG)
    return style


def show_error(title: str, msg: str):
    messagebox.showerror(title, msg)


def show_info(title: str, msg: str):
    messagebox.showinfo(title, msg)


def show_warning(title: str, msg: str):
    messagebox.showwarning(title, msg)


def make_root(title: str, width: int = 520, height: int = 620):
    root = tk.Tk()
    root.title(title)
    root.configure(bg=HAYETT_BG)
    root.geometry(f"{width}x{height}")
    root.resizable(False, False)
    # Centrer
    root.update_idletasks()
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")
    setup_style()
    return root


def labeled_entry(parent, label: str, show: str = None, width: int = 42):
    """Retourne (frame, entry). Frame est un tk.Frame pour supporter bg."""
    frame = tk.Frame(parent, bg=HAYETT_BG)
    lbl = tk.Label(frame, text=label, bg=HAYETT_BG, fg="#101828", font=("Segoe UI", 9))
    lbl.pack(anchor="w")
    ent = ttk.Entry(frame, width=width, show=show)
    ent.pack(fill="x", pady=(2, 6))
    return frame, ent


def labeled_combo(parent, label: str, values, width: int = 40):
    frame = tk.Frame(parent, bg=HAYETT_BG)
    lbl = tk.Label(frame, text=label, bg=HAYETT_BG, fg="#101828", font=("Segoe UI", 9))
    lbl.pack(anchor="w")
    combo = ttk.Combobox(frame, values=values, width=width, state="readonly")
    combo.pack(fill="x", pady=(2, 6))
    return frame, combo
