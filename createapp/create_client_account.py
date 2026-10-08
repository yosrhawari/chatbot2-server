#!/usr/bin/env python3
"""Application HAYETT - Gestion & Création de Comptes Clients.

Architecture Base de données PostgreSQL & Envoi Direct Gmail SMTP.
- PostgreSQL Database Storage (Tables: client, compte) avec hachage sécurisé
- Envoi automatique des accès au client via Gmail SMTP Sécurisé (smtp.gmail.com:587)
- Générateur de mot de passe sécurisé intégré
- Contrôles stricts sur les champs (CIN 8 chiffres, Téléphone 8 chiffres, Date JJ/MM/AAAA)
"""

import os
import sys
import json
import secrets
import string
import threading
import hashlib
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# --- Configuration & Chemins ---
APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "app_config.json"

DEFAULT_CONFIG = {
    "oracle_host": "localhost",
    "oracle_port": "1521",
    "oracle_service": "FREEPDB1",
    "oracle_user": "hayett_user",
    "oracle_password": "Test1234",
    "mode_simulation": False,
    "smtp_sender": "talel.saadani.pama@gmail.com",
    "smtp_password": "iuil ldtz lely djdj"
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"[WARN] Impossible de charger la configuration : {e}")
    return config


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Impossible de sauvegarder la configuration : {e}")


# --- Sécurité & Hash ---
def hash_password(password: str) -> str:
    """Hash le mot de passe en bcrypt si disponible, sinon SHA-256 avec sel."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except ImportError:
        salt = os.urandom(16).hex()
        digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return f"sha256${salt}${digest}"


def generate_secure_password(length: int = 12) -> str:
    """Génère un mot de passe temporaire robuste et mémorisable."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in "!@#$%&*" for c in pwd)):
            return pwd


# --- Service Email Client (Gmail SMTP Direct) ---
def send_client_email(client_data: dict, config: dict) -> tuple[bool, str]:
    """Envoie un email de bienvenue contenant les identifiants au client via Gmail SMTP."""
    sender_email = config.get("smtp_sender", "talel.saadani.pama@gmail.com").strip()
    app_password = config.get("smtp_password", "").strip().replace(" ", "")
    client_email = client_data.get("email", "").strip()
    client_name = f"{client_data.get('prenom', '')} {client_data.get('nom', '')}".strip()

    if not app_password:
        return False, "Mot de passe d'application Google non configuré."

    if not sender_email or "@" not in sender_email:
        return False, "Email expéditeur Gmail non configuré ou invalide."

    if not client_email or "@" not in client_email:
        return False, "Email client invalide."

    text_content = f"""Bonjour {client_name},

Votre compte client HAYETT a été créé avec succès.

Voici vos informations de connexion :
----------------------------------------
Identifiant (Email) : {client_email}
Mot de passe initial : {client_data.get('password')}
----------------------------------------

Pour votre sécurité, nous vous invitons à modifier votre mot de passe lors de votre première connexion.

Cordialement,
L'équipe HAYETT Assurances
"""

    html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
    <div style="background-color: #1e40af; padding: 15px; border-radius: 6px; text-align: center; color: white;">
        <h2 style="margin: 0;">🏢 HAYETT ASSURANCES</h2>
        <p style="margin: 5px 0 0 0; font-size: 14px;">Activation de votre compte client</p>
    </div>
    
    <div style="padding: 20px 0;">
        <p>Bonjour <strong>{client_name}</strong>,</p>
        <p>Votre compte client HAYETT a été créé avec succès par votre conseiller.</p>
        
        <div style="background-color: #f8fafc; border-left: 4px solid #2563eb; padding: 15px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0 0 8px 0;"><strong>Identifiant (Email) :</strong> <code style="color: #2563eb;">{client_email}</code></p>
            <p style="margin: 0;"><strong>Mot de passe initial :</strong> <code style="color: #2563eb; font-weight: bold;">{client_data.get('password')}</code></p>
        </div>
        
        <p style="color: #64748b; font-size: 13px;">🔒 Pour votre sécurité, nous vous recommandons de changer ce mot de passe dès votre première connexion.</p>
    </div>
    
    <div style="border-top: 1px solid #e2e8f0; padding-top: 15px; text-align: center; color: #94a3b8; font-size: 12px;">
        <p style="margin: 0;">L'équipe HAYETT Assurances • Service Clientèle</p>
    </div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Bienvenue chez HAYETT ! Vos identifiants d'accès"
        msg["From"] = f"HAYETT Assurances <{sender_email}>"
        msg["To"] = client_email

        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, [client_email], msg.as_string())
        server.quit()
        return True, "Email délivré avec succès via Gmail."
    except smtplib.SMTPAuthenticationError:
        return False, "Erreur d'authentification Gmail. Vérifiez votre mot de passe d'application (16 lettres)."
    except Exception as e:
        return False, f"Erreur Gmail SMTP : {e}"


# --- Gestion Base de Données Oracle 26ai (Schéma exact: client / compte) ---
def save_to_oracle(nom, prenom, cin, date_naissance, telephone, email, password, config):
    """Enregistre le client et son compte selon le schéma SQL exact."""
    host = config.get("oracle_host", "localhost")
    port = config.get("oracle_port", "1521")
    service = config.get("oracle_service", "FREEPDB1")
    user = config.get("oracle_user", "hayett_user")
    pwd = config.get("oracle_password", "Test1234")

    if not pwd:
        return None, None

    try:
        import oracledb
    except ImportError:
        raise RuntimeError("Le module oracledb n'est pas installé.")

    dsn = f"{host}:{port}/{service}"
    conn = oracledb.connect(
        user=user,
        password=pwd,
        dsn=dsn
    )

    try:
        cursor = conn.cursor()
        
        # Les tables sont gérées par le DDL externe.
        
        # Conversion date_naissance JJ/MM/AAAA -> objet date pour le type DATE SQL
        date_naissance_db = None
        if date_naissance:
            try:
                date_naissance_db = datetime.datetime.strptime(date_naissance, "%d/%m/%Y").date()
            except Exception:
                date_naissance_db = None

        # 3. Insertion dans la table client en utilisant la séquence
        out_client_id = cursor.var(oracledb.NUMBER)
        cursor.execute("""
            INSERT INTO client (client_id, nom, prenom, date_naissance, telephone, cin)
            VALUES (seq_client.NEXTVAL, :1, :2, :3, :4, :5)
            RETURNING client_id INTO :6
        """, (nom, prenom, date_naissance_db, telephone, cin, out_client_id))
        
        client_id = int(out_client_id.getvalue()[0])
        
        # 4. Insertion dans la table compte en utilisant la séquence
        pwd_hash = hash_password(password)
        out_compte_id = cursor.var(oracledb.NUMBER)
        cursor.execute("""
            INSERT INTO compte (compte_id, email, password_hash, client_id)
            VALUES (seq_compte.NEXTVAL, :1, :2, :3)
            RETURNING compte_id INTO :4
        """, (email, pwd_hash, client_id, out_compte_id))
        
        compte_id = int(out_compte_id.getvalue()[0])
        
        conn.commit()
        cursor.close()
        conn.close()
        return client_id, compte_id
    except Exception as e:
        conn.rollback()
        conn.close()
        raise e


# --- Composants Graphiques Modernes ---
class ClientAppGUI:
    PRIMARY = "#1E40AF"        # Bleu Corporate Profond
    PRIMARY_HOVER = "#1D4ED8"  # Bleu Vif
    ACCENT = "#2563EB"         # Bleu Accent
    BG_APP = "#F8FAFC"         # Arrière-plan doux (Slate 50)
    CARD_BG = "#FFFFFF"        # Fond de carte pur
    BORDER_COLOR = "#E2E8F0"   # Bordure subtile (Slate 200)
    TEXT_MAIN = "#0F172A"      # Texte sombre lisible (Slate 900)
    TEXT_MUTED = "#64748B"     # Texte secondaire (Slate 500)
    SUCCESS_COLOR = "#16A34A"   # Vert succès
    WARNING_COLOR = "#D97706"   # Orange alerte
    ERROR_COLOR = "#DC2626"     # Rouge erreur

    def __init__(self, root):
        self.root = root
        self.root.title("HAYETT Assurances • Gestionnaire de Comptes Clients")
        self.root.geometry("760x800")
        self.root.minsize(680, 720)
        self.root.configure(bg=self.BG_APP)
        
        self.config = load_config()
        self.show_passwords = tk.BooleanVar(value=False)
        self._init_theme()
        self._build_ui()

    def _init_theme(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

    def _create_entry_field(self, parent, label_text: str, is_required: bool = False, show_char: str = None, validate_digits_max: int = None):
        """Crée un champ avec label soigné, bordure interactive et focus coloré."""
        container = tk.Frame(parent, bg=self.CARD_BG)
        
        lbl_box = tk.Frame(container, bg=self.CARD_BG)
        lbl_box.pack(fill="x", anchor="w")
        
        lbl = tk.Label(
            lbl_box,
            text=label_text,
            font=("Segoe UI", 9, "bold"),
            fg="#334155",
            bg=self.CARD_BG
        )
        lbl.pack(side="left")
        
        if is_required:
            req_lbl = tk.Label(
                lbl_box,
                text=" *",
                font=("Segoe UI", 9, "bold"),
                fg=self.ERROR_COLOR,
                bg=self.CARD_BG
            )
            req_lbl.pack(side="left")

        border_frame = tk.Frame(container, bg=self.BORDER_COLOR, bd=1)
        border_frame.pack(fill="x", pady=(3, 0))

        entry = tk.Entry(
            border_frame,
            font=("Segoe UI", 10),
            bg="#F8FAFC",
            fg=self.TEXT_MAIN,
            bd=0,
            relief="flat",
            insertbackground=self.ACCENT,
            show=show_char if show_char else ""
        )
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

    def _build_ui(self):
        # 1. Header
        header = tk.Frame(self.root, bg=self.CARD_BG, height=75, bd=0)
        header.pack(fill="x", side="top")

        header_inner = tk.Frame(header, bg=self.CARD_BG)
        header_inner.pack(fill="both", expand=True, padx=30, pady=14)

        brand_frame = tk.Frame(header_inner, bg=self.CARD_BG)
        brand_frame.pack(side="left")

        tk.Label(
            brand_frame,
            text="🏢 HAYETT ASSURANCES",
            font=("Segoe UI", 14, "bold"),
            fg=self.TEXT_MAIN,
            bg=self.CARD_BG
        ).pack(anchor="w")

        tk.Label(
            brand_frame,
            text="Portail d'enregistrement et d'activation des comptes clients",
            font=("Segoe UI", 9),
            fg=self.TEXT_MUTED,
            bg=self.CARD_BG
        ).pack(anchor="w")

        btn_settings = tk.Button(
            header_inner,
            text="⚙ Paramètres",
            command=self._open_settings,
            font=("Segoe UI", 9, "bold"),
            bg="#F1F5F9",
            fg="#334155",
            activebackground="#E2E8F0",
            activeforeground="#0F172A",
            bd=1,
            relief="solid",
            padx=14,
            pady=6,
            cursor="hand2"
        )
        btn_settings.pack(side="right", pady=2)

        tk.Frame(self.root, bg=self.BORDER_COLOR, height=1).pack(fill="x")

        # 2. Formulaire Principal
        content_frame = tk.Frame(self.root, bg=self.BG_APP)
        content_frame.pack(fill="both", expand=True, padx=30, pady=20)

        main_card = tk.Frame(content_frame, bg=self.CARD_BG, bd=1, relief="solid")
        main_card.configure(highlightbackground=self.BORDER_COLOR, highlightthickness=1)
        main_card.pack(fill="both", expand=True)

        card_content = tk.Frame(main_card, bg=self.CARD_BG)
        card_content.pack(fill="both", expand=True, padx=35, pady=25)

        # Section 1: Informations Personnelles
        sec1_title = tk.Frame(card_content, bg=self.CARD_BG)
        sec1_title.pack(fill="x", pady=(0, 10))
        tk.Label(
            sec1_title,
            text="👤 1. Informations Personnelles",
            font=("Segoe UI", 11, "bold"),
            fg=self.PRIMARY,
            bg=self.CARD_BG
        ).pack(side="left")

        self.entries = {}

        # Grille 2 colonnes pour Nom & Prénom
        row1 = tk.Frame(card_content, bg=self.CARD_BG)
        row1.pack(fill="x", pady=3)
        f_nom, self.entries["nom"] = self._create_entry_field(row1, "Nom de famille", True)
        f_nom.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_prenom, self.entries["prenom"] = self._create_entry_field(row1, "Prénom", True)
        f_prenom.pack(side="left", fill="x", expand=True)

        # Grille 2 colonnes pour CIN & Téléphone (Limités à 8 chiffres)
        row2 = tk.Frame(card_content, bg=self.CARD_BG)
        row2.pack(fill="x", pady=3)
        f_cin, self.entries["cin"] = self._create_entry_field(row2, "Numéro CIN (8 chiffres)", True, validate_digits_max=8)
        f_cin.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_tel, self.entries["telephone"] = self._create_entry_field(row2, "Numéro de Téléphone (8 chiffres)", False, validate_digits_max=8)
        f_tel.pack(side="left", fill="x", expand=True)

        # Grille pour Date de Naissance
        row3 = tk.Frame(card_content, bg=self.CARD_BG)
        row3.pack(fill="x", pady=3)
        f_dob, self.entries["date_naissance"] = self._create_entry_field(row3, "Date de Naissance (JJ/MM/AAAA)", False)
        f_dob.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        empty_placeholder = tk.Frame(row3, bg=self.CARD_BG)
        empty_placeholder.pack(side="left", fill="x", expand=True)

        tk.Frame(card_content, bg="#F1F5F9", height=1).pack(fill="x", pady=15)

        # Section 2: Identifiants & Accès
        sec2_title = tk.Frame(card_content, bg=self.CARD_BG)
        sec2_title.pack(fill="x", pady=(0, 10))
        tk.Label(
            sec2_title,
            text="🔐 2. Identifiants & Accès Client",
            font=("Segoe UI", 11, "bold"),
            fg=self.PRIMARY,
            bg=self.CARD_BG
        ).pack(side="left")

        row4 = tk.Frame(card_content, bg=self.CARD_BG)
        row4.pack(fill="x", pady=3)
        f_email, self.entries["email"] = self._create_entry_field(row4, "Adresse Email Client (Identifiant d'accès)", True)
        f_email.pack(fill="x")

        row5 = tk.Frame(card_content, bg=self.CARD_BG)
        row5.pack(fill="x", pady=3)
        f_pwd, self.entries["password"] = self._create_entry_field(row5, "Mot de passe initial", True, "*")
        f_pwd.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_pwd_conf, self.entries["password_confirm"] = self._create_entry_field(row5, "Confirmer le mot de passe", True, "*")
        f_pwd_conf.pack(side="left", fill="x", expand=True)

        # Outils mot de passe
        pwd_tools = tk.Frame(card_content, bg=self.CARD_BG)
        pwd_tools.pack(fill="x", pady=(6, 0))

        btn_gen = tk.Button(
            pwd_tools,
            text="🎲 Générer un mot de passe sécurisé",
            command=self._generate_pwd,
            font=("Segoe UI", 8, "bold"),
            bg="#F1F5F9",
            fg=self.PRIMARY,
            activebackground="#E2E8F0",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2"
        )
        btn_gen.pack(side="left")

        chk_show = tk.Checkbutton(
            pwd_tools,
            text="Afficher le mot de passe",
            variable=self.show_passwords,
            command=self._toggle_show_passwords,
            font=("Segoe UI", 8),
            bg=self.CARD_BG,
            fg=self.TEXT_MUTED,
            activebackground=self.CARD_BG,
            bd=0
        )
        chk_show.pack(side="right")

        # Bouton Principal
        action_box = tk.Frame(card_content, bg=self.CARD_BG)
        action_box.pack(fill="x", pady=(25, 0))

        self.btn_submit = tk.Button(
            action_box,
            text="✨ Créer le Compte & Notifier le Client",
            command=self._on_submit,
            font=("Segoe UI", 11, "bold"),
            bg=self.PRIMARY,
            fg="#FFFFFF",
            activebackground=self.PRIMARY_HOVER,
            activeforeground="#FFFFFF",
            bd=0,
            relief="flat",
            padx=20,
            pady=12,
            cursor="hand2"
        )
        self.btn_submit.pack(fill="x")

        self.progress_bar = ttk.Progressbar(card_content, mode="indeterminate")

        self.status_lbl = tk.Label(
            card_content,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg=self.CARD_BG,
            fg=self.TEXT_MUTED
        )
        self.status_lbl.pack(fill="x", pady=(8, 0))

    def _generate_pwd(self):
        new_pwd = generate_secure_password(12)
        self.entries["password"].delete(0, tk.END)
        self.entries["password"].insert(0, new_pwd)
        self.entries["password_confirm"].delete(0, tk.END)
        self.entries["password_confirm"].insert(0, new_pwd)
        self.show_passwords.set(True)
        self._toggle_show_passwords()

    def _toggle_show_passwords(self):
        show_char = "" if self.show_passwords.get() else "*"
        self.entries["password"].configure(show=show_char)
        self.entries["password_confirm"].configure(show=show_char)

    def _open_settings(self):
        """Ouvre la boîte de dialogue des paramètres."""
        win = tk.Toplevel(self.root)
        win.title("Paramètres - Base de Données & Gmail SMTP")
        win.geometry("540x680")
        win.minsize(500, 620)
        win.resizable(True, True)
        win.configure(bg=self.CARD_BG)
        win.transient(self.root)
        win.grab_set()

        # En-tête
        p_header = tk.Frame(win, bg="#F8FAFC", height=55, bd=0)
        p_header.pack(fill="x", side="top")
        
        tk.Label(
            p_header,
            text="⚙ Paramètres d'Intégration",
            font=("Segoe UI", 12, "bold"),
            fg=self.TEXT_MAIN,
            bg="#F8FAFC"
        ).pack(anchor="w", padx=25, pady=12)

        tk.Frame(win, bg=self.BORDER_COLOR, height=1).pack(fill="x", side="top")

        # Sticky Footer
        footer = tk.Frame(win, bg="#F8FAFC", bd=0)
        footer.pack(side="bottom", fill="x")
        tk.Frame(win, bg=self.BORDER_COLOR, height=1).pack(side="bottom", fill="x")

        def _save():
            self.config["oracle_host"] = oracle_host_entry.get().strip()
            self.config["oracle_port"] = oracle_port_entry.get().strip()
            self.config["oracle_service"] = oracle_service_entry.get().strip()
            self.config["oracle_user"] = oracle_user_entry.get().strip()
            self.config["oracle_password"] = oracle_pwd_entry.get().strip()
            
            self.config["smtp_sender"] = smtp_sender_entry.get().strip()
            self.config["smtp_password"] = smtp_pwd_entry.get().strip()
            
            save_config(self.config)
            messagebox.showinfo("Succès", "Configuration enregistrée avec succès !", parent=win)
            win.destroy()

        btn_save = tk.Button(
            footer,
            text="💾 Enregistrer la Configuration",
            bg=self.PRIMARY,
            fg="#FFFFFF",
            activebackground=self.PRIMARY_HOVER,
            activeforeground="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=15,
            pady=10,
            cursor="hand2",
            command=_save
        )
        btn_save.pack(fill="x", padx=25, pady=12)

        # Corps
        body = tk.Frame(win, bg=self.CARD_BG)
        body.pack(fill="both", expand=True, padx=25, pady=12)

        # 1. Oracle 26ai
        tk.Label(body, text="💾 1. Base de Données Oracle 26ai", font=("Segoe UI", 10, "bold"), fg=self.PRIMARY, bg=self.CARD_BG).pack(anchor="w", pady=(0, 2))

        oracle_row = tk.Frame(body, bg=self.CARD_BG)
        oracle_row.pack(fill="x", pady=1)
        f_h, oracle_host_entry = self._create_entry_field(oracle_row, "Hôte:")
        f_h.pack(side="left", fill="x", expand=True, padx=(0, 8))
        oracle_host_entry.insert(0, self.config.get("oracle_host", "localhost"))

        f_p, oracle_port_entry = self._create_entry_field(oracle_row, "Port:")
        f_p.pack(side="left", fill="x", expand=True)
        oracle_port_entry.insert(0, self.config.get("oracle_port", "1521"))

        f_svc, oracle_service_entry = self._create_entry_field(body, "Service Name (PDB):")
        f_svc.pack(fill="x", pady=1)
        oracle_service_entry.insert(0, self.config.get("oracle_service", "FREEPDB1"))

        oracle_row2 = tk.Frame(body, bg=self.CARD_BG)
        oracle_row2.pack(fill="x", pady=1)
        f_u, oracle_user_entry = self._create_entry_field(oracle_row2, "Utilisateur DB:")
        f_u.pack(side="left", fill="x", expand=True, padx=(0, 8))
        oracle_user_entry.insert(0, self.config.get("oracle_user", "hayett_user"))

        f_pwd, oracle_pwd_entry = self._create_entry_field(oracle_row2, "Mot de passe DB:", False, "*")
        f_pwd.pack(side="left", fill="x", expand=True)
        oracle_pwd_entry.insert(0, self.config.get("oracle_password", "Test1234"))

        # 2. Gmail SMTP
        tk.Label(body, text="📧 2. Service Email Gmail (Direct & Sans Limite)", font=("Segoe UI", 10, "bold"), fg=self.PRIMARY, bg=self.CARD_BG).pack(anchor="w", pady=(12, 2))

        f_snd, smtp_sender_entry = self._create_entry_field(body, "Email Expéditeur Gmail:")
        f_snd.pack(fill="x", pady=1)
        smtp_sender_entry.insert(0, self.config.get("smtp_sender", "talel.saadani.pama@gmail.com"))

        f_spwd, smtp_pwd_entry = self._create_entry_field(body, "Mot de passe d'application Google (16 lettres):", False, "*")
        f_spwd.pack(fill="x", pady=1)
        smtp_pwd_entry.insert(0, self.config.get("smtp_password", "iuil ldtz lely djdj"))

    def _reset_form(self):
        for entry in self.entries.values():
            entry.delete(0, tk.END)
        self.status_lbl.configure(text="")

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
        nom = self.entries["nom"].get().strip()
        prenom = self.entries["prenom"].get().strip()
        cin = self.entries["cin"].get().strip()
        date_naissance = self.entries["date_naissance"].get().strip()
        telephone = self.entries["telephone"].get().strip()
        email = self.entries["email"].get().strip().lower()
        password = self.entries["password"].get()
        password_confirm = self.entries["password_confirm"].get()

        # 1. Vérification des champs obligatoires
        if not nom or not prenom or not cin or not email:
            messagebox.showerror("Champs obligatoires", "Veuillez renseigner au minimum le Nom, Prénom, CIN et Email.")
            return

        # 2. Contrôle du CIN (Exactement 8 chiffres)
        if not (cin.isdigit() and len(cin) == 8):
            messagebox.showerror("CIN Invalide", "Le numéro CIN doit comporter exactement 8 chiffres (ex: 08123456).")
            return

        # 3. Contrôle du Téléphone (Exactement 8 chiffres si renseigné)
        if telephone and not (telephone.isdigit() and len(telephone) == 8):
            messagebox.showerror("Téléphone Invalide", "Le numéro de téléphone doit comporter exactement 8 chiffres (ex: 98123456).")
            return

        # 4. Contrôle de la Date de Naissance (Format JJ/MM/AAAA)
        if date_naissance:
            try:
                dob = datetime.datetime.strptime(date_naissance, "%d/%m/%Y").date()
                today = datetime.date.today()
                if dob > today:
                    messagebox.showerror("Date Invalide", "La date de naissance ne peut pas être dans le futur.")
                    return
                if dob.year < 1900:
                    messagebox.showerror("Date Invalide", "Veuillez saisir une année de naissance valide (après 1900).")
                    return
            except ValueError:
                messagebox.showerror(
                    "Format de Date Invalide",
                    "Veuillez saisir la date de naissance au format JJ/MM/AAAA (ex: 15/04/1995)."
                )
                return

        # 5. Contrôle de l'Email
        import re
        if not re.fullmatch(r"^[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}$", email):
            messagebox.showerror("Email Invalide", "Veuillez entrer une adresse email valide (ex: client@gmail.com).")
            return

        # 6. Contrôle des mots de passe
        if not password:
            messagebox.showerror("Mot de passe requis", "Veuillez renseigner ou générer un mot de passe initial.")
            return

        if len(password) < 6:
            messagebox.showerror("Mot de passe trop court", "Le mot de passe initial doit comporter au moins 6 caractères.")
            return

        if password != password_confirm:
            messagebox.showerror("Mots de passe non identiques", "La confirmation du mot de passe ne correspond pas.")
            return

        self._set_loading(True, "⏳ Création du compte et notification...")

        def _worker():
            client_id, compte_id = None, None
            db_status = "Mode local / Base non configurée"
            
            # 1. Base Oracle 26ai (Schéma exact: client / compte)
            if self.config.get("oracle_user") and self.config.get("oracle_password"):
                try:
                    client_id, compte_id = save_to_oracle(
                        nom, prenom, cin, date_naissance, telephone, email, password, self.config
                    )
                    db_status = f"Enregistré dans Oracle (Client #{client_id}, Compte #{compte_id})"
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self._handle_error(f"Erreur Oracle : {err}"))
                    return

            import datetime as dt
            client_data = {
                "nom": nom,
                "prenom": prenom,
                "cin": cin,
                "date_naissance": date_naissance,
                "telephone": telephone,
                "email": email,
                "password": password,
                "client_id": client_id if client_id else "Non connecté",
                "compte_id": compte_id if compte_id else "Non connecté",
                "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # 2. Gmail SMTP Client
            email_success, email_msg = send_client_email(client_data, self.config)
            
            self.root.after(0, lambda: self._handle_success(email_success, email_msg, client_data, db_status))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_error(self, message: str):
        self._set_loading(False)
        self.status_lbl.configure(text=f"❌ {message}", fg=self.ERROR_COLOR)
        messagebox.showerror("Erreur", message)

    def _handle_success(self, email_success: bool, email_msg: str, client_data: dict, db_status: str):
        self._set_loading(False)
        
        if email_success:
            self.status_lbl.configure(text="✅ Opération terminée avec succès", fg=self.SUCCESS_COLOR)
        else:
            self.status_lbl.configure(text="⚠️ Opération terminée avec avertissements", fg=self.WARNING_COLOR)
            
        email_status_text = "✅ Identifiants envoyés par email au client (Gmail)" if email_success else f"❌ Échec notification client: {email_msg}"

        msg_details = (
            f"🎉 Le compte client a été créé avec succès !\n\n"
            f"• Client : {client_data['prenom']} {client_data['nom']}\n"
            f"• CIN : {client_data['cin']}\n"
            f"• Email Client : {client_data['email']}\n"
            f"• Statut Base de données : {db_status}\n\n"
            f"📧 Envoi au client : {email_status_text}\n"
        )
        messagebox.showinfo("Opération Réussie", msg_details)
        self._reset_form()


def main():
    root = tk.Tk()
    app = ClientAppGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
