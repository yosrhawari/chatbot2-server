#!/usr/bin/env python3
"""Creation atomique CONTRAT + EPARGNE pour un client existant (HAYETT Admin CLI + GUI)."""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import oracledb

from config import ORACLE_DSN, ORACLE_PASSWORD, ORACLE_USER


def _connect():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def _find_client_by_cin(cur, cin: str):
    cur.execute("SELECT client_id, nom, prenom FROM client WHERE cin = :cin", {"cin": cin or ""})
    rows = cur.fetchall()
    if not rows:
        raise ValueError(f"Client introuvable avec CIN : {cin}")
    if len(rows) > 1:
        raise ValueError(f"Plusieurs clients correspondent a CIN {cin} : {[(r[0], r[1], r[2]) for r in rows]}. Arret.")
    return rows[0]


def _list_contrats(cur, client_id):
    cur.execute("SELECT contrat_id, periodicite, montant_prime, statut FROM contrat WHERE client_id = :client_id ORDER BY contrat_id", {"client_id": client_id})
    return cur.fetchall()


def _next_contrat_id(cur) -> str:
    cur.execute("SELECT hayett_user.contrat_seq.NEXTVAL FROM dual")
    seq_val = cur.fetchone()[0]
    return f"C{seq_val:03d}"


def _next_epargne_id(cur) -> int:
    cur.execute("SELECT hayett_user.epargne_seq.NEXTVAL FROM dual")
    return cur.fetchone()[0]


def _input_required(prompt: str) -> str:
    while True:
        val = input(prompt).strip()
        if val:
            return val
        print("[ERROR] Champ obligatoire.")


def _input_optional(prompt: str):
    val = input(prompt).strip()
    return val if val else None


def _input_int(prompt: str, required: bool = True):
    while True:
        val = _input_optional(prompt) if not required else _input_required(prompt)
        if val is None:
            return None
        try:
            return int(val)
        except ValueError:
            print("[ERROR] Entier attendu.")


def _input_float(prompt: str, required: bool = True):
    while True:
        val = _input_optional(prompt) if not required else _input_required(prompt)
        if val is None:
            return None
        try:
            return float(val)
        except ValueError:
            print("[ERROR] Nombre attendu.")


def _input_date(prompt: str, required: bool = True):
    while True:
        val = _input_optional(prompt) if not required else _input_required(prompt)
        if val is None:
            return None
        if len(val) == 10 and val[4] == '-' and val[7] == '-':
            return val
        print("[ERROR] Format date attendu : YYYY-MM-DD")


def main() -> int:
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[ERROR] Oracle non configure dans .env (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)")
        return 1
    print("=== Creation CONTRAT + EPARGNE (HAYETT Admin) ===\n")
    cin = _input_required("CIN du client : ")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            client_id, client_nom, client_prenom = _find_client_by_cin(cur, cin)
        print(f"\n[OK] Client trouve\n   client_id = {client_id}\n   nom       = {client_nom}\n   prenom    = {client_prenom}")
        print("\n=== CONTRAT ===")
        date_souscription = _input_required("Date souscription (YYYY-MM-DD) : ")
        duree = _input_int("Duree (annees) : ")
        periodicite = _input_optional("Periodicite (ex: MENSUELLE, TRIMESTRIELLE) : ")
        montant_prime = _input_float("Montant prime : ")
        statut = _input_required("Statut (ex: ACTIF, RESILIE) : ")
        print("\n=== EPARGNE ===")
        date_calcul = _input_required("Date calcul (YYYY-MM-DD) : ")
        montant_epargne = _input_float("Montant epargne : ")
        participation_benefices = _input_float("Participation benefices : ", required=False)
        with conn.cursor() as cur:
            contrat_id = _next_contrat_id(cur)
            epargne_id = _next_epargne_id(cur)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO contrat (contrat_id, client_id, date_souscription, duree, periodicite, montant_prime, statut) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5, :6, :7)", [contrat_id, client_id, date_souscription, duree, periodicite, montant_prime, statut])
            cur.execute("INSERT INTO epargne (epargne_id, contrat_id, date_calcul, montant_epargne, participation_benefices) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)", [epargne_id, contrat_id, date_calcul, montant_epargne, participation_benefices])
        conn.commit()
    except oracledb.IntegrityError as e:
        conn.rollback()
        err = str(e)
        if "FK_CONTRAT_CLIENT" in err:
            print(f"[ERROR] Erreur reference client (FK) : {err}")
        elif "unique constraint" in err.lower() and "contrat" in err.lower():
            print(f"[ERROR] CONTRAT_ID deja existant (contrainte UNIQUE) : {err}")
        else:
            print(f"[ERROR] Erreur integrite Oracle : {err}")
        return 1
    except ValueError as e:
        conn.rollback()
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Erreur : {e}")
        return 1
    finally:
        conn.close()
    print("\n[OK] Creation reussie")
    print(f"   client_id      = {client_id}")
    print(f"   contrat_id     = {contrat_id}")
    print(f"   epargne_id     = {epargne_id}")
    return 0


def gui_main():
    import tkinter as tk
    from tkinter import ttk, messagebox
    try:
        from tools.gui_common import make_root, labeled_entry
    except ImportError:
        from gui_common import make_root, labeled_entry

    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        messagebox.showerror("Configuration", "Impossible de se connecter à la base Oracle. Vérifiez la configuration et que la base est démarrée.")
        return

    root = make_root("HAYETT — Création Contrat", width=580, height=720)
    # Scrollable
    canvas = tk.Canvas(root, bg="#f7f9fc", highlightthickness=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg="#f7f9fc")
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True, padx=12, pady=8)
    scrollbar.pack(side="right", fill="y")

    tk.Label(scroll_frame, text="Création Contrat + Épargne", font=("Segoe UI", 14, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(pady=(6, 12))
    # CIN
    tk.Label(scroll_frame, text="Identification client par CIN", font=("Segoe UI", 10, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(anchor="w", padx=6)
    cin_frame, cin_entry = labeled_entry(scroll_frame, "CIN du client *")
    cin_frame.configure(bg="#f7f9fc")
    cin_frame.pack(fill="x", padx=6)
    client_label = tk.Label(scroll_frame, text="", bg="#f7f9fc", fg="#2457c9", font=("Segoe UI", 9, "bold"))
    client_label.pack(pady=4)
    client_ctx = {"client_id": None}

    def on_search():
        cin = cin_entry.get().strip()
        if not cin:
            messagebox.showerror("CIN", "CIN requis.")
            return
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cid, nom, prenom = _find_client_by_cin(cur, cin)
                client_ctx["client_id"] = cid
                client_label.config(text=f"Client trouvé : {nom} {prenom} (ID {cid})")
            finally:
                conn.close()
        except ValueError as e:
            messagebox.showerror("Client", "Aucun client ne correspond à ce CIN.")
            client_ctx["client_id"] = None
            client_label.config(text="")
        except Exception as e:
            messagebox.showerror("Erreur Oracle", f"Impossible de se connecter à la base Oracle.\n\n{e}")
            client_ctx["client_id"] = None

    tk.Button(scroll_frame, text="Rechercher client", bg="#2457c9", fg="white", relief="flat", padx=12, pady=6, command=on_search).pack(pady=6)

    # Contrat fields
    tk.Label(scroll_frame, text="Contrat", font=("Segoe UI", 10, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(anchor="w", padx=6, pady=(10, 0))
    entries = {}
    for label, key in [("Date souscription (YYYY-MM-DD) *", "date_souscription"), ("Durée (années)", "duree"), ("Périodicité", "periodicite"), ("Montant prime", "montant_prime"), ("Statut *", "statut")]:
        f, e = labeled_entry(scroll_frame, label)
        f.configure(bg="#f7f9fc"); f.pack(fill="x", padx=6); entries[key] = e
    tk.Label(scroll_frame, text="Épargne", font=("Segoe UI", 10, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(anchor="w", padx=6, pady=(10, 0))
    for label, key in [("Date calcul (YYYY-MM-DD) *", "date_calcul"), ("Montant épargne", "montant_epargne"), ("Participation bénéfices", "participation_benefices")]:
        f, e = labeled_entry(scroll_frame, label)
        f.configure(bg="#f7f9fc"); f.pack(fill="x", padx=6); entries[key] = e

    def on_create():
        if client_ctx["client_id"] is None:
            messagebox.showerror("Client", "Veuillez d'abord rechercher un client par CIN.")
            return
        client_id = client_ctx["client_id"]
        vals = {k: e.get().strip() for k, e in entries.items()}
        if not vals["date_souscription"] or not vals["statut"] or not vals["date_calcul"]:
            messagebox.showerror("Champs requis", "Date souscription, Statut et Date calcul sont obligatoires.")
            return
        # Validate numeric
        try:
            duree = int(vals["duree"]) if vals["duree"] else None
            montant_prime = float(vals["montant_prime"]) if vals["montant_prime"] else None
            montant_epargne = float(vals["montant_epargne"]) if vals["montant_epargne"] else None
            participation = float(vals["participation_benefices"]) if vals["participation_benefices"] else None
        except ValueError:
            messagebox.showerror("Format", "Vérifiez les montants / durée (nombres attendus).")
            return
        if len(vals["date_souscription"]) != 10 or vals["date_souscription"][4] != '-' or vals["date_souscription"][7] != '-':
            messagebox.showerror("Date", "Format date attendu : YYYY-MM-DD")
            return
        if len(vals["date_calcul"]) != 10 or vals["date_calcul"][4] != '-' or vals["date_calcul"][7] != '-':
            messagebox.showerror("Date", "Format date attendu : YYYY-MM-DD")
            return
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    contrat_id = _next_contrat_id(cur)
                    epargne_id = _next_epargne_id(cur)
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO contrat (contrat_id, client_id, date_souscription, duree, periodicite, montant_prime, statut) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5, :6, :7)", [contrat_id, client_id, vals["date_souscription"], duree, vals["periodicite"] or None, montant_prime, vals["statut"]])
                    cur.execute("INSERT INTO epargne (epargne_id, contrat_id, date_calcul, montant_epargne, participation_benefices) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)", [epargne_id, contrat_id, vals["date_calcul"], montant_epargne, participation])
                conn.commit()
                messagebox.showinfo("Succès", f"Contrat créé avec succès.\n\nClient ID : {client_id}\nContrat ID : {contrat_id} (format C001)\nÉpargne ID : {epargne_id}")
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()
        except Exception as e:
            messagebox.showerror("Erreur", f"Erreur : {e}")

    btn_frame = tk.Frame(scroll_frame, bg="#f7f9fc")
    btn_frame.pack(fill="x", pady=12, padx=6)
    tk.Button(btn_frame, text="Créer contrat", bg="#2457c9", fg="white", relief="flat", padx=16, pady=8, command=on_create).pack(side="left", expand=True, fill="x", padx=(0, 6))
    tk.Button(btn_frame, text="Quitter", bg="#e2e7f0", fg="#0b1a33", relief="flat", padx=16, pady=8, command=root.destroy).pack(side="left", padx=(6, 0))
    root.mainloop()


if __name__ == "__main__":
    if "--gui" in sys.argv:
        gui_main()
    elif "--cli" in sys.argv:
        raise SystemExit(main())
    elif getattr(sys, 'frozen', False):
        gui_main()
    else:
        try:
            if not sys.stdin.isatty():
                gui_main()
            else:
                raise SystemExit(main())
        except Exception:
            try:
                gui_main()
            except Exception:
                raise SystemExit(main())
