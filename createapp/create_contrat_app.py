#!/usr/bin/env python3
"""Application HAYETT - Création de Contrat."""

import sys
import json
import threading
import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "app_config.json"

DEFAULT_CONFIG = {
    "oracle_host": "localhost",
    "oracle_port": "1521",
    "oracle_service": "FREEPDB1",
    "oracle_user": "hayett_user",
    "oracle_password": "Test1234",
}

def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception:
            pass
    return config

def save_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

def save_to_oracle(cin, contrat_id, date_souscription, duree, periodicite, montant_prime, statut, config):
    try:
        import oracledb
    except ImportError:
        raise RuntimeError("Le module oracledb n'est pas installé.")

    dsn = f"{config.get('oracle_host')}:{config.get('oracle_port')}/{config.get('oracle_service')}"
    conn = oracledb.connect(
        user=config.get("oracle_user"),
        password=config.get("oracle_password"),
        dsn=dsn
    )

    try:
        cursor = conn.cursor()
        
        # Trouver le client_id
        cursor.execute("SELECT client_id, nom, prenom FROM client WHERE cin = :1", (cin,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Aucun client trouvé avec le CIN {cin}")
        
        client_id, nom, prenom = row[0], row[1], row[2]
        
        date_sousc_db = datetime.datetime.strptime(date_souscription, "%d/%m/%Y").date()
        duree_val = int(duree) if duree else None
        montant_val = float(montant_prime) if montant_prime else None

        cursor.execute("""
            INSERT INTO contrat (contrat_id, client_id, date_souscription, duree, periodicite, montant_prime, statut)
            VALUES (:1, :2, :3, :4, :5, :6, :7)
        """, (contrat_id, client_id, date_sousc_db, duree_val, periodicite, montant_val, statut))
        
        conn.commit()
        cursor.close()
        conn.close()
        return client_id, nom, prenom
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e

class ContratAppGUI:
    PRIMARY = "#1E40AF"
    PRIMARY_HOVER = "#1D4ED8"
    ACCENT = "#2563EB"
    BG_APP = "#F8FAFC"
    CARD_BG = "#FFFFFF"
    BORDER_COLOR = "#E2E8F0"
    TEXT_MAIN = "#0F172A"
    TEXT_MUTED = "#64748B"
    SUCCESS_COLOR = "#16A34A"
    WARNING_COLOR = "#D97706"
    ERROR_COLOR = "#DC2626"

    def __init__(self, root):
        self.root = root
        self.root.title("HAYETT Assurances • Création de Contrat")
        self.root.geometry("760x700")
        self.root.minsize(680, 600)
        self.root.configure(bg=self.BG_APP)
        self.config = load_config()
        self._init_theme()
        self._build_ui()

    def _init_theme(self):
        style = ttk.Style()
        try: style.theme_use("clam")
        except: pass

    def _create_entry_field(self, parent, label_text: str, is_required: bool = False, validate_digits_max: int = None):
        container = tk.Frame(parent, bg=self.CARD_BG)
        lbl_box = tk.Frame(container, bg=self.CARD_BG)
        lbl_box.pack(fill="x", anchor="w")
        
        tk.Label(lbl_box, text=label_text, font=("Segoe UI", 9, "bold"), fg="#334155", bg=self.CARD_BG).pack(side="left")
        if is_required:
            tk.Label(lbl_box, text=" *", font=("Segoe UI", 9, "bold"), fg=self.ERROR_COLOR, bg=self.CARD_BG).pack(side="left")

        border_frame = tk.Frame(container, bg=self.BORDER_COLOR, bd=1)
        border_frame.pack(fill="x", pady=(3, 0))

        entry = tk.Entry(border_frame, font=("Segoe UI", 10), bg="#F8FAFC", fg=self.TEXT_MAIN, bd=0, relief="flat", insertbackground=self.ACCENT)
        entry.pack(fill="x", ipady=6, padx=8, pady=1)

        if validate_digits_max:
            vcmd = (self.root.register(lambda P: (P.isdigit() and len(P) <= validate_digits_max) or P == ""), "%P")
            entry.configure(validate="key", validatecommand=vcmd)

        def on_focus_in(event):
            border_frame.configure(bg=self.ACCENT)
            entry.configure(bg="#FFFFFF")

        def on_focus_out(event):
            border_frame.configure(bg=self.BORDER_COLOR)
            entry.configure(bg="#F8FAFC")

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        return container, entry
        
    def _create_combo_field(self, parent, label_text: str, values: list, is_required: bool = False):
        container = tk.Frame(parent, bg=self.CARD_BG)
        lbl_box = tk.Frame(container, bg=self.CARD_BG)
        lbl_box.pack(fill="x", anchor="w")
        tk.Label(lbl_box, text=label_text, font=("Segoe UI", 9, "bold"), fg="#334155", bg=self.CARD_BG).pack(side="left")
        if is_required:
            tk.Label(lbl_box, text=" *", font=("Segoe UI", 9, "bold"), fg=self.ERROR_COLOR, bg=self.CARD_BG).pack(side="left")
        
        combo = ttk.Combobox(container, values=values, font=("Segoe UI", 10), state="readonly")
        combo.pack(fill="x", pady=(3, 0), ipady=4)
        if values:
            combo.current(0)
        return container, combo

    def _build_ui(self):
        header = tk.Frame(self.root, bg=self.CARD_BG, height=75, bd=0)
        header.pack(fill="x", side="top")
        header_inner = tk.Frame(header, bg=self.CARD_BG)
        header_inner.pack(fill="both", expand=True, padx=30, pady=14)
        
        brand_frame = tk.Frame(header_inner, bg=self.CARD_BG)
        brand_frame.pack(side="left")
        tk.Label(brand_frame, text="🏢 HAYETT ASSURANCES", font=("Segoe UI", 14, "bold"), fg=self.TEXT_MAIN, bg=self.CARD_BG).pack(anchor="w")
        tk.Label(brand_frame, text="Gestionnaire de Contrats", font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(anchor="w")

        btn_settings = tk.Button(header_inner, text="⚙ Paramètres", command=self._open_settings, font=("Segoe UI", 9, "bold"), bg="#F1F5F9", fg="#334155", bd=1, relief="solid", padx=14, pady=6, cursor="hand2")
        btn_settings.pack(side="right", pady=2)
        tk.Frame(self.root, bg=self.BORDER_COLOR, height=1).pack(fill="x")

        content_frame = tk.Frame(self.root, bg=self.BG_APP)
        content_frame.pack(fill="both", expand=True, padx=30, pady=20)
        main_card = tk.Frame(content_frame, bg=self.CARD_BG, bd=1, relief="solid")
        main_card.configure(highlightbackground=self.BORDER_COLOR, highlightthickness=1)
        main_card.pack(fill="both", expand=True)

        card_content = tk.Frame(main_card, bg=self.CARD_BG)
        card_content.pack(fill="both", expand=True, padx=35, pady=25)

        tk.Label(card_content, text="📄 Nouveau Contrat", font=("Segoe UI", 11, "bold"), fg=self.PRIMARY, bg=self.CARD_BG).pack(anchor="w", pady=(0, 10))
        self.entries = {}

        row1 = tk.Frame(card_content, bg=self.CARD_BG)
        row1.pack(fill="x", pady=3)
        f_cin, self.entries["cin"] = self._create_entry_field(row1, "CIN du Client (8 chiffres)", True, validate_digits_max=8)
        f_cin.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_cid, self.entries["contrat_id"] = self._create_entry_field(row1, "ID Contrat (ex: C001)", True)
        f_cid.pack(side="left", fill="x", expand=True)
        self.entries["contrat_id"].insert(0, f"C-{datetime.datetime.now().strftime('%Y%m%d%H%M')}")

        row2 = tk.Frame(card_content, bg=self.CARD_BG)
        row2.pack(fill="x", pady=3)
        f_date, self.entries["date"] = self._create_entry_field(row2, "Date Souscription (JJ/MM/AAAA)", True)
        f_date.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_duree, self.entries["duree"] = self._create_entry_field(row2, "Durée (Mois)", False, validate_digits_max=4)
        f_duree.pack(side="left", fill="x", expand=True)
        self.entries["date"].insert(0, datetime.datetime.now().strftime('%d/%m/%Y'))
        
        row3 = tk.Frame(card_content, bg=self.CARD_BG)
        row3.pack(fill="x", pady=3)
        f_per, self.entries["periodicite"] = self._create_combo_field(row3, "Périodicité", ["Mensuelle", "Trimestrielle", "Semestrielle", "Annuelle"], False)
        f_per.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_mont, self.entries["montant"] = self._create_entry_field(row3, "Montant Prime (TND)", False)
        f_mont.pack(side="left", fill="x", expand=True)
        
        row4 = tk.Frame(card_content, bg=self.CARD_BG)
        row4.pack(fill="x", pady=3)
        f_statut, self.entries["statut"] = self._create_combo_field(row4, "Statut", ["Actif", "En attente", "Suspendu"], True)
        f_statut.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Frame(row4, bg=self.CARD_BG).pack(side="left", fill="x", expand=True)

        action_box = tk.Frame(card_content, bg=self.CARD_BG)
        action_box.pack(fill="x", pady=(25, 0))

        self.btn_submit = tk.Button(action_box, text="✨ Créer le Contrat", command=self._on_submit, font=("Segoe UI", 11, "bold"), bg=self.PRIMARY, fg="#FFFFFF", bd=0, padx=20, pady=12, cursor="hand2")
        self.btn_submit.pack(fill="x")
        self.progress_bar = ttk.Progressbar(card_content, mode="indeterminate")
        self.status_lbl = tk.Label(card_content, text="", font=("Segoe UI", 9, "bold"), bg=self.CARD_BG, fg=self.TEXT_MUTED)
        self.status_lbl.pack(fill="x", pady=(8, 0))

    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Paramètres - Base de Données")
        win.geometry("540x400")
        win.configure(bg=self.CARD_BG)
        win.grab_set()
        
        tk.Label(win, text="⚙ Paramètres Oracle", font=("Segoe UI", 12, "bold"), bg=self.CARD_BG).pack(anchor="w", padx=25, pady=12)
        body = tk.Frame(win, bg=self.CARD_BG)
        body.pack(fill="both", expand=True, padx=25)

        f_h, oracle_host_entry = self._create_entry_field(body, "Hôte:")
        f_h.pack(fill="x", pady=1)
        oracle_host_entry.insert(0, self.config.get("oracle_host", "localhost"))
        f_p, oracle_port_entry = self._create_entry_field(body, "Port:")
        f_p.pack(fill="x", pady=1)
        oracle_port_entry.insert(0, self.config.get("oracle_port", "1521"))
        f_svc, oracle_service_entry = self._create_entry_field(body, "Service Name:")
        f_svc.pack(fill="x", pady=1)
        oracle_service_entry.insert(0, self.config.get("oracle_service", "FREEPDB1"))
        f_u, oracle_user_entry = self._create_entry_field(body, "Utilisateur DB:")
        f_u.pack(fill="x", pady=1)
        oracle_user_entry.insert(0, self.config.get("oracle_user", "hayett_user"))
        f_pwd, oracle_pwd_entry = self._create_entry_field(body, "Mot de passe DB:", False, "*")
        f_pwd.pack(fill="x", pady=1)
        oracle_pwd_entry.insert(0, self.config.get("oracle_password", "Test1234"))

        def _save():
            self.config["oracle_host"] = oracle_host_entry.get().strip()
            self.config["oracle_port"] = oracle_port_entry.get().strip()
            self.config["oracle_service"] = oracle_service_entry.get().strip()
            self.config["oracle_user"] = oracle_user_entry.get().strip()
            self.config["oracle_password"] = oracle_pwd_entry.get().strip()
            save_config(self.config)
            win.destroy()

        tk.Button(win, text="💾 Enregistrer", bg=self.PRIMARY, fg="#FFFFFF", font=("Segoe UI", 10, "bold"), relief="flat", command=_save).pack(fill="x", padx=25, pady=12)

    def _set_loading(self, is_loading: bool, message=""):
        if is_loading:
            self.btn_submit.configure(state="disabled", bg="#94A3B8")
            self.progress_bar.pack(fill="x", pady=(10, 0))
            self.progress_bar.start(10)
            self.status_lbl.configure(text=message, fg=self.ACCENT)
        else:
            self.btn_submit.configure(state="normal", bg=self.PRIMARY)
            self.progress_bar.stop()
            self.progress_bar.pack_forget()

    def _on_submit(self):
        cin = self.entries["cin"].get().strip()
        contrat_id = self.entries["contrat_id"].get().strip()
        date_souscription = self.entries["date"].get().strip()
        duree = self.entries["duree"].get().strip()
        periodicite = self.entries["periodicite"].get().strip()
        montant_prime = self.entries["montant"].get().strip()
        statut = self.entries["statut"].get().strip()

        if not cin or not contrat_id or not date_souscription:
            messagebox.showerror("Erreur", "CIN, ID Contrat et Date de souscription sont obligatoires.")
            return

        self._set_loading(True, "⏳ Création du contrat...")
        
        def _worker():
            try:
                client_id, nom, prenom = save_to_oracle(cin, contrat_id, date_souscription, duree, periodicite, montant_prime, statut, self.config)
                self.root.after(0, lambda: self._handle_success(client_id, nom, prenom, contrat_id))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._handle_error(f"Erreur : {err}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_error(self, message: str):
        self._set_loading(False)
        self.status_lbl.configure(text=f"❌ {message}", fg=self.ERROR_COLOR)
        messagebox.showerror("Erreur", message)

    def _handle_success(self, client_id, nom, prenom, contrat_id):
        self._set_loading(False)
        self.status_lbl.configure(text="✅ Contrat créé", fg=self.SUCCESS_COLOR)
        messagebox.showinfo("Succès", f"Contrat {contrat_id} créé avec succès pour le client {prenom} {nom} (ID: {client_id})")
        for k, e in self.entries.items():
            if k not in ["date", "periodicite", "statut"]:
                if hasattr(e, 'delete'): e.delete(0, tk.END)
        self.entries["contrat_id"].insert(0, f"C-{datetime.datetime.now().strftime('%Y%m%d%H%M')}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ContratAppGUI(root)
    root.mainloop()
