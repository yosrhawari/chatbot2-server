#!/usr/bin/env python3
"""Creation atomique CLIENT + COMPTE par HAYETT (admin CLI + GUI).

- getpass()/input() en mode CLI
- Tkinter en mode GUI (double-clic exe windowed)
- hash_password() -> bcrypt hash uniquement
- CIN obligatoire
- Transaction Oracle unique COMMIT/ROLLBACK
- Sequences Oracle pour IDs (client_seq.NEXTVAL, compte_seq.NEXTVAL)

Usage CLI:
    python -m backend.tools.create_client_account
    python -m tools.create_client_account
    python -m tools.create_client_account --cli  (force CLI)
    python -m tools.create_client_account --gui  (force GUI)
"""
import getpass
import sys
from pathlib import Path

# Ensure backend package is importable when run from project root or backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import oracledb

from config import ORACLE_DSN, ORACLE_PASSWORD, ORACLE_USER
from security import hash_password


def _connect():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def _email_exists(conn, email: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM compte WHERE LOWER(email) = LOWER(:email)",
            {"email": email},
        )
        return cur.fetchone() is not None


def _diagnose_email(conn, email: str) -> None:
    """READ-ONLY diagnostic: affiche contexte Oracle et recherche exacte.
    Aucune écriture, aucune séquence consommée."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM dual")
            schema = cur.fetchone()[0]
            cur.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME') FROM dual")
            service = cur.fetchone()[0]
            cur.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_USER') FROM dual")
            current_user = cur.fetchone()[0]
            print(f"[DIAG] CURRENT_USER={current_user} CURRENT_SCHEMA={schema} SERVICE_NAME={service}")
            print(f"[DIAG] ORACLE_USER={ORACLE_USER} ORACLE_DSN={ORACLE_DSN}")
            try:
                cur.execute("SELECT COUNT(*) FROM hayett_user.compte")
                cnt_hayett = cur.fetchone()[0]
                print(f"[DIAG] hayett_user.compte COUNT(*)={cnt_hayett}")
            except Exception as e:
                print(f"[DIAG] hayett_user.compte COUNT erreur: {e}")
            try:
                cur.execute("SELECT COUNT(*) FROM compte")
                cnt = cur.fetchone()[0]
                print(f"[DIAG] compte (sans schema) COUNT(*)={cnt}")
            except Exception as e:
                print(f"[DIAG] compte COUNT erreur: {e}")
            for qualified in ("hayett_user.compte", "compte"):
                try:
                    cur.execute(
                        f"SELECT compte_id, email, client_id FROM {qualified} "
                        "WHERE LOWER(TRIM(email)) = LOWER(TRIM(:email))",
                        {"email": email},
                    )
                    rows = cur.fetchall()
                    print(f"[DIAG] {qualified} WHERE LOWER(TRIM(email))=... -> {len(rows)} ligne(s)")
                    for r in rows:
                        print(f"[DIAG]   compte_id={r[0]} email={r[1]} client_id={r[2]}")
                except Exception as e:
                    print(f"[DIAG] {qualified} recherche erreur: {e}")
    except Exception as e:
        print(f"[DIAG] diagnostic erreur: {e}")


def _cin_exists(conn, cin: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM client WHERE cin = :cin",
            {"cin": cin},
        )
        return cur.fetchone() is not None


def _next_client_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT hayett_user.client_seq.NEXTVAL FROM dual")
        return cur.fetchone()[0]


def _next_compte_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT hayett_user.compte_seq.NEXTVAL FROM dual")
        return cur.fetchone()[0]


def _do_create(nom, prenom, cin, date_naissance, telephone, email, password):
    """Logique métier partagée CLI/GUI. Retourne (client_id, compte_id)."""
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        raise RuntimeError("Oracle non configure dans .env (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)")
    conn = _connect()
    try:
        if _cin_exists(conn, cin):
            raise ValueError(f"CIN deja existant : {cin}")
        if _email_exists(conn, email):
            try:
                _diagnose_email(conn, email)
            except Exception:
                pass
            raise ValueError(f"Email deja existant : {email}")
        client_id = _next_client_id(conn)
        compte_id = _next_compte_id(conn)
        password_hash = hash_password(password)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO client (client_id, cin, nom, prenom, date_naissance, telephone)
                VALUES (:1, :2, :3, :4, TO_DATE(:5, 'YYYY-MM-DD'), :6)
                """,
                [client_id, cin, nom, prenom, date_naissance, telephone],
            )
            cur.execute(
                """
                INSERT INTO compte (compte_id, email, password_hash, client_id)
                VALUES (:1, :2, :3, :4)
                """,
                [compte_id, email, password_hash, client_id],
            )
        conn.commit()
        return client_id, compte_id
    except oracledb.IntegrityError as e:
        conn.rollback()
        err = str(e)
        if "UQ_COMPTE_EMAIL" in err or ("unique constraint" in err.lower() and "email" in err.lower()):
            try:
                _diagnose_email(conn, email)
            except Exception:
                pass
            raise ValueError(f"Cette adresse email est deja associee a un compte. ({email})")
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[ERROR] Oracle non configure dans .env (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)")
        return 1

    print("=== Creation CLIENT + COMPTE (HAYETT Admin) ===\n")

    nom = input("Nom : ").strip()
    prenom = input("Prenom : ").strip()
    cin = input("CIN : ").strip()
    date_naissance = input("Date naissance (YYYY-MM-DD) : ").strip()
    telephone = input("Telephone : ").strip()
    email = input("Email : ").strip().lower()

    if not nom or not prenom or not cin or not email:
        print("[ERROR] Nom, prenom, CIN et email sont obligatoires")
        return 1

    password = getpass.getpass("Mot de passe initial : ")
    password_confirm = getpass.getpass("Confirmer mot de passe : ")

    if not password:
        print("[ERROR] Mot de passe requis")
        return 1

    if password != password_confirm:
        print("[ERROR] Mots de passe differents")
        return 1

    try:
        client_id, compte_id = _do_create(nom, prenom, cin, date_naissance, telephone, email, password)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        print(f"[ERROR] Erreur : {e}")
        return 1

    print("\n[OK] CLIENT/COMPTE crees")
    print(f"   client_id = {client_id}")
    print(f"   cin       = {cin}")
    print(f"   compte_id = {compte_id}")
    print(f"   email     = {email}")
    print(f"   nom       = {nom} {prenom}")

    # --- Envoi email (apres COMMIT uniquement) ---
    try:
        from email_service import is_email_configured, send_welcome_email

        if not is_email_configured():
            print("\n[WARN] Email non configure (SMTP_HOST/SMTP_FROM vides dans .env).")
            print("       Le compte est operationnel (login fonctionne).")
            print(f"       Pour envoyer l'email plus tard : python -m tools.resend_credentials --email {email}")
            print("       Le mot de passe initial n'a pas ete envoye.")
            return 2

        send_welcome_email(email, prenom, nom, password)
        print(f"\n[OK] Email de bienvenue envoye a {email}")
        print("     Le mot de passe initial a ete hashe (bcrypt) et n'est plus en memoire.")
    except Exception as e:
        print(f"\n[WARN] Compte cree (client_id={client_id}, email={email}) MAIS l'envoi d'email a echoue: {e}")
        print("       Le compte est operationnel (login fonctionne).")
        print(f"       Pour reessayer : python -m tools.resend_credentials --email {email}")
        return 2
    finally:
        try:
            del password
            del password_confirm
        except NameError:
            pass

    return 0


def gui_main():
    import tkinter as tk
    from tkinter import messagebox

    try:
        from tools.gui_common import make_root, labeled_entry, show_error, show_info, show_warning
    except ImportError:
        from gui_common import make_root, labeled_entry, show_error, show_info, show_warning

    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        show_error("Configuration", "Impossible de se connecter à la base Oracle. Vérifiez la configuration et que la base est démarrée.\n(.env : ORACLE_USER / ORACLE_PASSWORD / ORACLE_DSN manquants)")
        return

    root = make_root("HAYETT — Création Client", width=560, height=680)
    canvas = tk.Canvas(root, bg="#f7f9fc", highlightthickness=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg="#f7f9fc")
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True, padx=12, pady=8)
    scrollbar.pack(side="right", fill="y")

    tk.Label(scroll_frame, text="Création Client + Compte", font=("Segoe UI", 14, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(pady=(6, 4))
    tk.Label(scroll_frame, text="Saisissez les informations du nouveau client", bg="#f7f9fc", fg="#5b6779").pack(pady=(0, 12))

    entries = {}
    fields = [
        ("Nom *", "nom", None),
        ("Prénom *", "prenom", None),
        ("CIN *", "cin", None),
        ("Date naissance (YYYY-MM-DD)", "date_naissance", None),
        ("Téléphone", "telephone", None),
        ("Email *", "email", None),
        ("Mot de passe initial *", "password", "*"),
        ("Confirmation mot de passe *", "password_confirm", "*"),
    ]
    for label, key, show in fields:
        f, e = labeled_entry(scroll_frame, label, show=show, width=48)
        f.configure(bg="#f7f9fc")
        f.pack(fill="x", padx=6)
        entries[key] = e

    def on_create():
        nom = entries["nom"].get().strip()
        prenom = entries["prenom"].get().strip()
        cin = entries["cin"].get().strip()
        date_naissance = entries["date_naissance"].get().strip()
        telephone = entries["telephone"].get().strip()
        email = entries["email"].get().strip().lower()
        password = entries["password"].get()
        password_confirm = entries["password_confirm"].get()

        if not nom or not prenom or not cin or not email:
            show_error("Champs requis", "Nom, prénom, CIN et email sont obligatoires.")
            return
        if not password:
            show_error("Mot de passe", "Mot de passe requis.")
            return
        if password != password_confirm:
            show_error("Mot de passe", "Les deux mots de passe sont différents.")
            return
        # Oracle check + insert
        try:
            client_id, compte_id = _do_create(nom, prenom, cin, date_naissance, telephone, email, password)
        except ValueError as e:
            msg = str(e)
            if "CIN deja existant" in msg:
                show_error("CIN existant", f"Aucune création : {msg}")
            elif "Email deja existant" in msg or "deja associee" in msg:
                show_error("Email existant", "Cette adresse email est déjà associée à un compte.")
            else:
                show_error("Erreur", msg)
            return
        except Exception as e:
            show_error("Erreur Oracle", f"Impossible de se connecter à la base Oracle. Vérifiez la configuration et que la base est démarrée.\n\nDétail : {e}")
            return

        # Email (après COMMIT)
        email_status = ""
        try:
            from email_service import is_email_configured, send_welcome_email
            if not is_email_configured():
                email_status = "\n\n[Email non configuré] Le compte est opérationnel (login fonctionne).\nRenseignez SMTP dans le .env à côté de l'exe."
                show_warning("Compte créé — email non envoyé",
                             f"Client créé avec succès.\n\nClient ID : {client_id}\nCompte ID : {compte_id}\nCIN : {cin}\nNom : {nom} {prenom}\nEmail : {email}{email_status}")
            else:
                send_welcome_email(email, prenom, nom, password)
                email_status = "\n\nUn email contenant les informations de connexion a été envoyé au client."
                show_info("Succès",
                          f"Client créé avec succès.\n\nClient ID : {client_id}\nCompte ID : {compte_id}\nCIN : {cin}\nNom : {nom} {prenom}\nEmail : {email}{email_status}")
        except Exception as e:
            show_warning("Compte créé — email échoué",
                         f"Client créé avec succès (ID {client_id} / {compte_id}) mais l'email n'a pas pu être envoyé.\n\nDétail : {e}\n\nLe compte est opérationnel. Relancez l'envoi via l'outil de renvoi.")
        finally:
            try:
                del password
                del password_confirm
            except Exception:
                pass
        # Clear password fields
        entries["password"].delete(0, tk.END)
        entries["password_confirm"].delete(0, tk.END)

    btn_frame = tk.Frame(scroll_frame, bg="#f7f9fc")
    btn_frame.pack(fill="x", pady=12, padx=6)
    tk.Button(btn_frame, text="Créer le client", bg="#2457c9", fg="white", activebackground="#1c46a6", relief="flat", padx=16, pady=8, command=on_create).pack(side="left", expand=True, fill="x", padx=(0, 6))
    tk.Button(btn_frame, text="Quitter", bg="#e2e7f0", fg="#0b1a33", relief="flat", padx=16, pady=8, command=root.destroy).pack(side="left", padx=(6, 0))

    root.mainloop()


if __name__ == "__main__":
    # Double-clic exe (frozen windowed) -> GUI. CLI reste disponible via --cli ou terminal.
    if "--gui" in sys.argv:
        gui_main()
    elif "--cli" in sys.argv:
        raise SystemExit(main())
    elif getattr(sys, 'frozen', False):
        gui_main()
    else:
        # Si lancé en double-clic sans console (stdin non tty), ouvrir GUI
        try:
            if not sys.stdin.isatty():
                gui_main()
            else:
                raise SystemExit(main())
        except Exception:
            # Fallback GUI si isatty échoue (windowed)
            try:
                gui_main()
            except Exception:
                raise SystemExit(main())
