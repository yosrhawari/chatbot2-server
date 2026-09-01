#!/usr/bin/env python3
"""Creation d'un VERSEMENT pour un contrat existant (HAYETT Admin CLI + GUI)."""
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


def _next_versement_id(cur) -> int:
    cur.execute("SELECT hayett_user.versement_seq.NEXTVAL FROM dual")
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


def _input_choice(prompt: str, max_choice: int) -> int:
    while True:
        val = _input_required(prompt)
        try:
            choice = int(val)
            if 1 <= choice <= max_choice:
                return choice
            print(f"[ERROR] Choix entre 1 et {max_choice}.")
        except ValueError:
            print("[ERROR] Entier attendu.")


def main() -> int:
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[ERROR] Oracle non configure dans .env (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)")
        return 1
    print("=== Creation VERSEMENT (HAYETT Admin) ===\n")
    cin = _input_required("CIN du client : ")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            client_id, client_nom, client_prenom = _find_client_by_cin(cur, cin)
        print(f"\n[OK] Client trouve\n   client_id = {client_id}\n   nom       = {client_nom}\n   prenom    = {client_prenom}")
        with conn.cursor() as cur:
            contrats = _list_contrats(cur, client_id)
        if not contrats:
            print("[ERROR] Aucun contrat trouve pour ce client.")
            return 1
        print("\n=== CONTRATS DU CLIENT ===")
        for i, (cid, periodicite, montant_prime, statut) in enumerate(contrats, 1):
            print(f"  {i}. {cid} | {periodicite or 'N/A'} | {montant_prime or 'N/A'} | {statut or 'N/A'}")
        choix = _input_choice("\nChoisir le contrat (numero) : ", len(contrats))
        contrat_id = contrats[choix - 1][0]
        print("\n=== VERSEMENT ===")
        date_versement = _input_required("Date versement (YYYY-MM-DD) : ")
        montant = _input_float("Montant : ")
        type_versement = _input_optional("Type versement (ex: PRIME, RACHAT, AUTRE) : ")
        with conn.cursor() as cur:
            versement_id = _next_versement_id(cur)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO versement (versement_id, contrat_id, date_versement, montant, type_versement) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)", [versement_id, contrat_id, date_versement, montant, type_versement])
        conn.commit()
    except oracledb.IntegrityError as e:
        conn.rollback()
        err = str(e)
        if "FK_VERSEMENT_CONTRAT" in err:
            print(f"[ERROR] Contrat invalide (FK) : {err}")
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
    print(f"   versement_id = {versement_id}")
    print(f"   contrat_id   = {contrat_id}")
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

    root = make_root("HAYETT — Création Versement", width=560, height=620)
    canvas = tk.Canvas(root, bg="#f7f9fc", highlightthickness=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg="#f7f9fc")
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True, padx=12, pady=8)
    scrollbar.pack(side="right", fill="y")

    tk.Label(scroll_frame, text="Création Versement", font=("Segoe UI", 14, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(pady=(6, 12))
    cin_frame, cin_entry = labeled_entry(scroll_frame, "CIN du client *")
    cin_frame.configure(bg="#f7f9fc"); cin_frame.pack(fill="x", padx=6)
    client_label = tk.Label(scroll_frame, text="", bg="#f7f9fc", fg="#2457c9", font=("Segoe UI", 9, "bold"))
    client_label.pack(pady=4)
    contrat_var = tk.StringVar()
    contrat_combo = ttk.Combobox(scroll_frame, textvariable=contrat_var, state="readonly", width=50)
    contrat_label = tk.Label(scroll_frame, text="Contrat (sélectionnez après recherche)", bg="#f7f9fc", fg="#5b6779")
    contrats_data = {"list": []}

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
                client_label.config(text=f"Client : {nom} {prenom} (ID {cid})")
                with conn.cursor() as cur:
                    contrats = _list_contrats(cur, cid)
                contrats_data["list"] = contrats
                contrats_data["client_id"] = cid
                if not contrats:
                    messagebox.showerror("Contrat", "Aucun contrat trouvé pour ce client.")
                    contrat_combo["values"] = []
                    return
                vals = [f"{c[0]} | {c[1] or 'N/A'} | {c[2] or 'N/A'} | {c[3] or 'N/A'}" for c in contrats]
                contrat_combo["values"] = vals
                if vals:
                    contrat_combo.current(0)
            finally:
                conn.close()
        except ValueError as e:
            messagebox.showerror("Client", "Aucun client ne correspond à ce CIN.")
        except Exception as e:
            messagebox.showerror("Erreur Oracle", f"Impossible de se connecter à la base Oracle.\n\n{e}")

    tk.Button(scroll_frame, text="Rechercher client et contrats", bg="#2457c9", fg="white", relief="flat", padx=12, pady=6, command=on_search).pack(pady=6)
    contrat_label.pack(anchor="w", padx=6, pady=(8, 0))
    contrat_combo.pack(fill="x", padx=6, pady=(2, 8))

    tk.Label(scroll_frame, text="Versement", font=("Segoe UI", 10, "bold"), bg="#f7f9fc", fg="#0b1a33").pack(anchor="w", padx=6, pady=(6, 0))
    entries = {}
    for label, key in [("Date versement (YYYY-MM-DD) *", "date_versement"), ("Montant *", "montant"), ("Type versement", "type_versement")]:
        f, e = labeled_entry(scroll_frame, label)
        f.configure(bg="#f7f9fc"); f.pack(fill="x", padx=6); entries[key] = e

    def on_create():
        if not contrats_data["list"]:
            messagebox.showerror("Contrat", "Veuillez d'abord rechercher un client et sélectionner un contrat.")
            return
        sel = contrat_combo.current()
        if sel < 0:
            messagebox.showerror("Contrat", "Veuillez sélectionner un contrat.")
            return
        contrat_id = contrats_data["list"][sel][0]
        vals = {k: e.get().strip() for k, e in entries.items()}
        if not vals["date_versement"] or not vals["montant"]:
            messagebox.showerror("Champs requis", "Date et Montant sont obligatoires.")
            return
        if len(vals["date_versement"]) != 10 or vals["date_versement"][4] != '-' or vals["date_versement"][7] != '-':
            messagebox.showerror("Date", "Format date attendu : YYYY-MM-DD")
            return
        try:
            montant = float(vals["montant"])
        except ValueError:
            messagebox.showerror("Montant", "Montant invalide.")
            return
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    versement_id = _next_versement_id(cur)
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO versement (versement_id, contrat_id, date_versement, montant, type_versement) VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)", [versement_id, contrat_id, vals["date_versement"], montant, vals["type_versement"] or None])
                conn.commit()
                messagebox.showinfo("Succès", f"Versement créé avec succès.\n\nVersement ID : {versement_id}\nContrat ID : {contrat_id}")
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()
        except Exception as e:
            messagebox.showerror("Erreur", f"Erreur : {e}")

    btn_frame = tk.Frame(scroll_frame, bg="#f7f9fc")
    btn_frame.pack(fill="x", pady=12, padx=6)
    tk.Button(btn_frame, text="Créer versement", bg="#2457c9", fg="white", relief="flat", padx=16, pady=8, command=on_create).pack(side="left", expand=True, fill="x", padx=(0, 6))
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
