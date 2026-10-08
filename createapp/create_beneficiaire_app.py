#!/usr/bin/env python3
"""Application HAYETT - Ajout de Bénéficiaire."""

import sys
import json
import threading
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

def fetch_contrats(cin, config):
    try:
        import oracledb
    except ImportError:
        raise RuntimeError("Le module oracledb n'est pas installé.")

    dsn = f"{config.get('oracle_host')}:{config.get('oracle_port')}/{config.get('oracle_service')}"
    conn = oracledb.connect(user=config.get("oracle_user"), password=config.get("oracle_password"), dsn=dsn)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT client_id FROM client WHERE cin = :1", (cin,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Aucun client trouvé avec le CIN {cin}")
        
        client_id = row[0]
        cursor.execute("SELECT contrat_id FROM contrat WHERE client_id = :1", (client_id,))
        rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        conn.close()
        raise e

def save_beneficiaire(contrat_id, nom, prenom, relation, config):
    try:
        import oracledb
    except ImportError:
        raise RuntimeError("Le module oracledb n'est pas installé.")

    dsn = f"{config.get('oracle_host')}:{config.get('oracle_port')}/{config.get('oracle_service')}"
    conn = oracledb.connect(user=config.get("oracle_user"), password=config.get("oracle_password"), dsn=dsn)

    try:
        cursor = conn.cursor()
        out_id = cursor.var(oracledb.NUMBER)
        cursor.execute("""
            INSERT INTO beneficiaire (beneficiaire_id, contrat_id, nom, prenom, relation)
            VALUES (seq_beneficiaire.NEXTVAL, :1, :2, :3, :4)
            RETURNING beneficiaire_id INTO :5
        """, (contrat_id, nom, prenom, relation, out_id))
        
        bid = int(out_id.getvalue()[0])
        conn.commit()
        cursor.close()
        conn.close()
        return bid
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e

class BeneficiaireAppGUI:
    PRIMARY = "#1E40AF"
    PRIMARY_HOVER = "#1D4ED8"
    ACCENT = "#2563EB"
    BG_APP = "#F8FAFC"
    CARD_BG = "#FFFFFF"
    BORDER_COLOR = "#E2E8F0"
    TEXT_MAIN = "#0F172A"
    TEXT_MUTED = "#64748B"
    SUCCESS_COLOR = "#16A34A"
    ERROR_COLOR = "#DC2626"
    WARNING_COLOR = "#D97706"

    def __init__(self, root):
        self.root = root
        self.root.title("HAYETT Assurances • Ajout de Bénéficiaire")
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
        tk.Label(brand_frame, text="Gestionnaire des Bénéficiaires", font=("Segoe UI", 9), fg=self.TEXT_MUTED, bg=self.CARD_BG).pack(anchor="w")

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

        tk.Label(card_content, text="👥 Nouveau Bénéficiaire", font=("Segoe UI", 11, "bold"), fg=self.PRIMARY, bg=self.CARD_BG).pack(anchor="w", pady=(0, 10))
        self.entries = {}

        # 1. Recherche Client
        search_box = tk.Frame(card_content, bg=self.CARD_BG)
        search_box.pack(fill="x", pady=3)
        f_cin, self.entries["cin"] = self._create_entry_field(search_box, "CIN du Client (8 chiffres)", True, validate_digits_max=8)
        f_cin.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        btn_search = tk.Button(search_box, text="🔍 Rechercher Contrats", command=self._on_search, font=("Segoe UI", 9, "bold"), bg="#F1F5F9", fg=self.PRIMARY, bd=0, padx=14, pady=6, cursor="hand2")
        btn_search.pack(side="left", pady=(15,0))
        
        tk.Frame(card_content, bg="#F1F5F9", height=1).pack(fill="x", pady=15)

        # 2. Informations Bénéficiaire
        row1 = tk.Frame(card_content, bg=self.CARD_BG)
        row1.pack(fill="x", pady=3)
        f_cid, self.combo_contrat = self._create_combo_field(row1, "Sélectionner un Contrat", [], True)
        f_cid.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_nom, self.entries["nom"] = self._create_entry_field(row1, "Nom du Bénéficiaire", True)
        f_nom.pack(side="left", fill="x", expand=True)
        
        row2 = tk.Frame(card_content, bg=self.CARD_BG)
        row2.pack(fill="x", pady=3)
        f_prenom, self.entries["prenom"] = self._create_entry_field(row2, "Prénom du Bénéficiaire", True)
        f_prenom.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_rel, self.entries["relation"] = self._create_combo_field(row2, "Lien de Parenté", ["Conjoint", "Enfant", "Parent", "Frère/Sœur", "Autre"], False)
        f_rel.pack(side="left", fill="x", expand=True)
        
        action_box = tk.Frame(card_content, bg=self.CARD_BG)
        action_box.pack(fill="x", pady=(25, 0))

        self.btn_submit = tk.Button(action_box, text="✨ Enregistrer le Bénéficiaire", command=self._on_submit, font=("Segoe UI", 11, "bold"), bg=self.PRIMARY, fg="#FFFFFF", bd=0, padx=20, pady=12, cursor="hand2")
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

    def _on_search(self):
        cin = self.entries["cin"].get().strip()
        if not cin:
            messagebox.showerror("Erreur", "Veuillez entrer un CIN.")
            return

        self._set_loading(True, "⏳ Recherche des contrats...")
        
        def _worker():
            try:
                contrats = fetch_contrats(cin, self.config)
                self.root.after(0, lambda: self._handle_search_success(contrats))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._handle_error(f"Erreur : {err}"))

        threading.Thread(target=_worker, daemon=True).start()
        
    def _handle_search_success(self, contrats):
        self._set_loading(False)
        self.combo_contrat['values'] = contrats
        if contrats:
            self.combo_contrat.current(0)
            self.status_lbl.configure(text=f"✅ {len(contrats)} contrat(s) trouvé(s)", fg=self.SUCCESS_COLOR)
        else:
            self.status_lbl.configure(text="⚠️ Ce client n'a aucun contrat.", fg=self.WARNING_COLOR)

    def _on_submit(self):
        contrat_id = self.combo_contrat.get().strip()
        nom = self.entries["nom"].get().strip()
        prenom = self.entries["prenom"].get().strip()
        relation = self.entries["relation"].get().strip()

        if not contrat_id or not nom or not prenom:
            messagebox.showerror("Erreur", "Sélectionnez un contrat et indiquez le nom/prénom du bénéficiaire.")
            return

        self._set_loading(True, "⏳ Enregistrement du bénéficiaire...")
        
        def _worker():
            try:
                bid = save_beneficiaire(contrat_id, nom, prenom, relation, self.config)
                self.root.after(0, lambda: self._handle_success(bid, contrat_id, nom, prenom))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._handle_error(f"Erreur : {err}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_error(self, message: str):
        self._set_loading(False)
        self.status_lbl.configure(text=f"❌ {message}", fg=self.ERROR_COLOR)
        messagebox.showerror("Erreur", message)

    def _handle_success(self, bid, contrat_id, nom, prenom):
        self._set_loading(False)
        self.status_lbl.configure(text="✅ Bénéficiaire enregistré", fg=self.SUCCESS_COLOR)
        messagebox.showinfo("Succès", f"Bénéficiaire #{bid} ({prenom} {nom}) enregistré pour le contrat {contrat_id}")
        self.entries["nom"].delete(0, tk.END)
        self.entries["prenom"].delete(0, tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = BeneficiaireAppGUI(root)
    root.mainloop()
