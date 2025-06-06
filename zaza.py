import  streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import cv2
from datetime import date, datetime, timedelta
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image
import torch
import io
import altair as alt
import os

# ─── Config page ─────────────────────────────────────────────────────────
st.set_page_config(page_title="Gestion des Absences", layout="wide")

# ─── FaceNet & MTCNN Models ───────────────────────────────────────────────
@st.cache_resource
def load_face_models():
    mtcnn = MTCNN(image_size=160, margin=0, keep_all=True)
    resnet = InceptionResnetV1(pretrained='vggface2').eval()
    return mtcnn, resnet

mtcnn, resnet = load_face_models()

@st.cache_data
def get_embedding(img: Image.Image):
    face_tensors = mtcnn(img)  # Tensor (n,3,160,160) ou None
    if face_tensors is None:
        return None
    if face_tensors.ndim == 3:
        face_tensors = face_tensors.unsqueeze(0)
    with torch.no_grad():
        return resnet(face_tensors).cpu().numpy()

# ─── Database setup & migrations ──────────────────────────────────────────
DB_PATH = "absences.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()

# 1) Création des tables si besoin
c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        pwd TEXT,
        role TEXT,
        name TEXT,
        photo BLOB
    )
""")
c.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        emb BLOB,
        first_name TEXT,
        last_name TEXT,
        cne TEXT,
        filiere TEXT,
        niveau TEXT,
        photo BLOB,
        FOREIGN KEY(email) REFERENCES users(email)
    )
""")
c.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        FOREIGN KEY(email) REFERENCES users(email)
    )
""")
c.execute("""
    CREATE TABLE IF NOT EXISTS absences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        date TEXT,
        hour TEXT,
        status TEXT,
        justified INTEGER DEFAULT 0,
        justificatif TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id)
    )
""")
conn.commit()

c.execute("""
    CREATE TABLE IF NOT EXISTS timetables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filiere TEXT,
        niveau TEXT,
        day TEXT,
        slot TEXT,
        subject TEXT,
        teacher TEXT
    )
""")
conn.commit()

c.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (DATE('now')),
        viewed INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(student_id) REFERENCES students(id)
    )
""")
conn.commit()

# 2) Migrations éventuelles pour les colonnes manquantes
student_cols = [r[1] for r in conn.execute("PRAGMA table_info(students)").fetchall()]
if "first_name" not in student_cols:
    for col in ["first_name TEXT", "last_name TEXT", "cne TEXT", "filiere TEXT", "niveau TEXT", "photo BLOB"]:
        conn.execute(f"ALTER TABLE students ADD COLUMN {col}")
    conn.commit()

absence_cols = [r[1] for r in conn.execute("PRAGMA table_info(absences)").fetchall()]
if "hour" not in absence_cols:
    conn.execute("ALTER TABLE absences ADD COLUMN hour TEXT")
if "justificatif" not in absence_cols:
    conn.execute("ALTER TABLE absences ADD COLUMN justificatif TEXT")
if "justified" not in absence_cols:
    conn.execute("ALTER TABLE absences ADD COLUMN justified INTEGER DEFAULT 0")
conn.commit()

# 3) Comptes par défaut si la table users est vide
c.execute("SELECT COUNT(*) FROM users")
if c.fetchone()[0] == 0:
    # Admin
    c.execute("INSERT INTO users(email,pwd,role,name) VALUES(?,?,?,?)",
              ("admin@ocp.com","admin123","Admin","Administrateur"))
    # Enseignant
    c.execute("INSERT INTO users(email,pwd,role,name) VALUES(?,?,?,?)",
              ("prof1@ocp.com","prof123","Enseignant","Professeur Un"))
    c.execute("INSERT OR IGNORE INTO teachers(email) VALUES(?)",("prof1@ocp.com",))
    # Étudiant exemple
    c.execute("INSERT INTO users(email,pwd,role,name) VALUES(?,?,?,?)",
              ("etu1@ocp.com","etu123","Étudiant","Étudiant Un"))
    c.execute("""
        INSERT OR IGNORE INTO students(email,emb,first_name,last_name,cne,filiere,niveau,photo)
        VALUES(?,?,?,?,?,?,?,?)
    """, ("etu1@ocp.com", None, "Étudiant","Un","CNE123","Génie","1ère année", None))
    conn.commit()

# ─── Génération AUTOMATIQUE des alertes ────────────────────────────────────
# On considère l'année scolaire allant du 1er septembre de l'année en cours
# jusqu'au 30 juin de l'année suivante.

today = date.today()
if today.month >= 9:
    annee_debut = today.year
    annee_fin = today.year + 1
else:
    annee_debut = today.year - 1
    annee_fin = today.year

start_annee = date(annee_debut, 9, 1).isoformat()
end_annee   = date(annee_fin, 6, 30).isoformat()

# ─ 3+ absences dans la même matière (hors dimanche, créneaux 8–18)
query_matieres = """
    SELECT
      a.student_id,
      t.subject AS matiere,
      COUNT(*) AS nb_abs
    FROM absences a
    JOIN students s ON a.student_id = s.id
    JOIN timetables t
      ON t.filiere = s.filiere
     AND t.niveau  = s.niveau
     AND t.slot    = a.hour
     AND t.day    = CASE strftime('%w', a.date)
                      WHEN '1' THEN 'Lundi'
                      WHEN '2' THEN 'Mardi'
                      WHEN '3' THEN 'Mercredi'
                      WHEN '4' THEN 'Jeudi'
                      WHEN '5' THEN 'Vendredi'
                      WHEN '6' THEN 'Samedi'
                    END
    WHERE
      a.status = 'absent'
      AND a.justified = 0
      AND a.date BETWEEN ? AND ?
      AND strftime('%w', a.date) BETWEEN '1' AND '6'
      AND a.hour IN ('8–10','10–12','12–14','14–16','16–18')
    GROUP BY a.student_id, t.subject
    HAVING COUNT(*) >= 3
"""
rows_matieres = conn.execute(query_matieres, (start_annee, end_annee)).fetchall()
for student_id, matiere, nb_abs in rows_matieres:
    msg = f"🚨 Vous avez déjà été absent·e {nb_abs} fois en {matiere} depuis le début de l'année scolaire."
    existe = conn.execute(
        "SELECT 1 FROM alerts WHERE student_id=? AND message=? AND viewed=0",
        (student_id, msg)
    ).fetchone()
    if not existe:
        conn.execute(
            "INSERT INTO alerts(student_id, message) VALUES(?, ?)",
            (student_id, msg)
        )

# ─ Absence sur les 5 créneaux (8–10,10–12,12–14,14–16,16–18) d’un même lundi–samedi
query_full_day = """
    SELECT
      a.student_id,
      a.date,
      COUNT(DISTINCT a.hour) AS cnt_slots
    FROM absences a
    JOIN students s ON a.student_id = s.id
    WHERE
      a.status = 'absent'
      AND a.justified = 0
      AND a.date BETWEEN ? AND ?
      AND strftime('%w', a.date) BETWEEN '1' AND '6'
      AND a.hour IN ('8–10','10–12','12–14','14–16','16–18')
    GROUP BY a.student_id, a.date
    HAVING cnt_slots = 5
"""
rows_full_day = conn.execute(query_full_day, (start_annee, end_annee)).fetchall()
for student_id, day_str, cnt in rows_full_day:
    msg2 = f"⚠️ Vous avez été absent·e toute la journée (5 créneaux) le {day_str}."
    existe2 = conn.execute(
        "SELECT 1 FROM alerts WHERE student_id=? AND message=? AND viewed=0",
        (student_id, msg2)
    ).fetchone()
    if not existe2:
        conn.execute(
            "INSERT INTO alerts(student_id, message) VALUES(?, ?)",
            (student_id, msg2)
        )

conn.commit()
# ───────────────────────────────────────────────────────────────────────────


# ─── Authentication ───────────────────────────────────────────────────────
if "auth" not in st.session_state:
    st.session_state.auth = False

def login_page():
    st.title("🔐 Connexion")
    email = st.text_input("Email", key="login_email")
    pwd   = st.text_input("Mot de passe", type="password", key="login_pwd")
    if st.button("Se connecter"):
        row = conn.execute(
            "SELECT role,name FROM users WHERE email=? AND pwd=?",
            (email, pwd)
        ).fetchone()
        if row:
            st.session_state.auth  = True
            st.session_state.email = email
            st.session_state.role  = row[0]
            st.session_state.name  = row[1]
            st.experimental_rerun()
        else:
            st.error("Identifiants invalides")

if not st.session_state.auth:
    login_page()
    st.stop()

# ─── Main Application ────────────────────────────────────────────────────
role  = st.session_state.role
email = st.session_state.email
name  = st.session_state.name

st.sidebar.markdown(f"**Connecté** : {name} — *{role}*")
if st.sidebar.button("Déconnexion"):
    for k in ["auth","email","role","name"]:
        st.session_state.pop(k, None)
    st.experimental_rerun()

# Définition des onglets selon rôle
if role == "Admin":
    tabs = st.tabs([
        "Dashboard",
        "Gestion Étudiants",
        "Gestion Enseignants",
        "Validation Justificatifs",
        "Gestion des emplois de temps",
        "Paramètres"
    ])
elif role == "Enseignant":
    tabs = st.tabs([
        "Dashboard",
        "Appel manuel",
        "Reconnaissance Faciale",
        "Mes classes"
    ])
else:  # Étudiant
    tabs = st.tabs([
        "Mes absences",
        "Justificatifs",
        "Emploi du temps"
    ])


# ─── ADMIN: DASHBOARD ─────────────────────────────────────────────────────
if role == "Admin":

    st.markdown("""
        <style>
        
        /* 2) Injecter l'image de fond sur le conteneur principal */
        [data-testid="stAppViewContainer"] .block-container {
            background: url("https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRl7i7W7SWXLAB5V3U83r7qGgbmUDFhNwjQJA&s")
                        center/cover no-repeat fixed !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <style>
        /* Centre tout texte mis en header (st.header → <h2>) */
        h2 {
            text-align: center !important;
        }
        </style>
    """, unsafe_allow_html=True)  

    with tabs[0]:
        st.header("📊 Dashboard d’Absences")

        # 1) Sélection de la période (pour les indicateurs visuels uniquement)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            dr = st.date_input(
                "Période",
                value=(date.today().replace(day=1), date.today()),
                key="dash_date"
            )
            start_date = None
            end_date   = None
            if hasattr(dr, "__len__"):
                try:
                    length = len(dr)
                except Exception:
                    length = 0
                if length >= 2:
                    start_date, end_date = dr[0], dr[1]
                else:
                    start_date = None
                    end_date   = None
            else:
                start_date = None
                end_date   = None

            if start_date is None or end_date is None:
                st.info("✅ Veuillez sélectionner deux dates pour définir la période complète.")
                st.stop()

        with col2:
            filieres = (
                pd.read_sql("SELECT DISTINCT filiere FROM students", conn)
                  ["filiere"]
                  .fillna("Non renseignée")
                  .tolist()
            )
            sel_fil = st.multiselect("Filière", sorted(set(filieres)), default=filieres)

        with col3:
            niveaux = ["1ère année", "2ème année"]
            sel_niv = st.multiselect("Niveau", niveaux, default=niveaux)

        with col4:
            filter_hors_creneau = st.checkbox(
                "Exclure absences hors créneaux 8–18 ou dimanche",
                value=True
            )

        # 2) Chargement & filtrage
        df_abs = pd.read_sql(
            """
            SELECT a.student_id, a.date, a.hour, a.status, a.justified,
                   s.first_name || ' ' || s.last_name AS student,
                   COALESCE(s.filiere,'Non renseignée') AS filiere,
                   COALESCE(s.niveau,'Inconnu')      AS niveau
            FROM absences a
            JOIN students s ON a.student_id=s.id
            WHERE a.date BETWEEN ? AND ?
            """,
            conn,
            params=(start_date, end_date)
        )

        df_abs = df_abs[
            df_abs["filiere"].isin(sel_fil) &
            df_abs["niveau"].isin(sel_niv)
        ]

        if filter_hors_creneau:
            df_abs = df_abs[
                (df_abs["hour"].isin(['8–10','10–12','12–14','14–16','16–18'])) &
                (pd.to_datetime(df_abs["date"]).dt.weekday <= 5)
            ]

        # 3) KPI globaux (Total / Justifiées / Non justifiées)
        total = len(df_abs)
        just  = int(df_abs["justified"].sum())
        nonj  = total - just
        col1, col2, col3 = st.columns(3)
        col1.metric("Total absences", total)
        col2.metric("Absences justifiées", just, f"{just/total: .1%}" if total else "—")
        col3.metric("Absences non justifiées", nonj, f"{nonj/total: .1%}" if total else "—")

        # 4) Évolution dans le temps (compte toutes absences)
        ts = (
            df_abs
            .groupby("date")
            .size()
            .reset_index(name="count")
        )
        if not ts.empty:
            st.subheader("Évolution des absences")
            st.line_chart(ts.rename(columns={"date": "index"}).set_index("index"))

        # 5) Par filière et niveau
        st.subheader("Absences par filière")
        grp_f = df_abs.groupby("filiere").size().reset_index(name="count")
        chart_f = alt.Chart(grp_f).mark_bar().encode(
            x=alt.X("count:Q", title="Nombre"),
            y=alt.Y("filiere:N", sort="-x", title=None),
            tooltip=["filiere", "count"]
        )
        st.altair_chart(chart_f, use_container_width=True)

        st.subheader("Absences par niveau")
        grp_n = df_abs.groupby("niveau").size().reset_index(name="count")
        chart_n = alt.Chart(grp_n).mark_bar().encode(
            x="count:Q",
            y=alt.Y("niveau:N", sort="-x"),
            tooltip=["niveau", "count"]
        )
        st.altair_chart(chart_n, use_container_width=True)

        # 6) Top étudiants absents
        st.subheader("Étudiants les plus absents")
        top = (
            df_abs
            .groupby(["student_id", "student"])
            .size()
            .reset_index(name="absences")
            .sort_values("absences", ascending=False)
            .head(10)
            .rename(columns={"student": "Étudiant"})
        )
        st.table(top[["Étudiant", "absences"]])

        # 7) Taux de présence par créneau (heatmap)
        if "hour" in df_abs.columns and not df_abs.empty:
            st.subheader("Absences par créneau horaire")
            df_h = (
                df_abs
                .groupby(["hour"])
                .size()
                .reset_index(name="count")
            )
            heat = alt.Chart(df_h).mark_bar().encode(
                x=alt.X("hour:N", title="Créneau"),
                y=alt.Y("count:Q", title="Absences"),
                tooltip=["hour", "count"]
            )
            st.altair_chart(heat, use_container_width=True)

        # 8) Afficher un tableau global des absences entre start_date / end_date
        #    (avec une colonne “Justifiée”)
        st.markdown("---")
        st.subheader("📋 Liste détaillée des absences (justifiées ou non)")

        if not df_abs.empty:
            df_tableau = df_abs.copy()
            df_tableau["Justifiée"] = df_tableau["justified"].apply(lambda x: "Oui" if x == 1 else "Non")
            df_tableau = df_tableau.rename(columns={
                "date": "Date",
                "hour": "Séance",
                "status": "Statut",
                "student": "Étudiant",
                "filiere": "Filière",
                "niveau": "Niveau"
            })
            # On affiche toutes les colonnes d’un coup
            st.dataframe(
                df_tableau[["Date","Séance","Étudiant","Filière","Niveau","Statut","Justifiée"]],
                use_container_width=True
            )
        else:
            st.info("Aucune absence dans cette période.")

        # 9) Génération des alertes (Admin) : 
        #    seules les absences non justifiées sont prises en compte
        today = date.today()
        if today.month >= 9:
            start_annee = date(today.year, 9, 1)
            end_annee   = date(today.year + 1, 6, 30)
        else:
            start_annee = date(today.year - 1, 9, 1)
            end_annee   = date(today.year, 6, 30)

        st.markdown("---")
        st.subheader("⚠️ Génération des alertes pour étudiants problématiques")

        filieres_sel = sel_fil if sel_fil else pd.read_sql("SELECT DISTINCT filiere FROM students", conn)["filiere"].fillna("Non renseignée").tolist()
        niveaux_sel  = sel_niv if sel_niv else ["1ère année","2ème année"]

        placeholders_fil  = ",".join("?" for _ in filieres_sel)
        placeholders_niv  = ",".join("?" for _ in niveaux_sel)

        # a) Alerte “>= 3 absences non justifiées dans la même matière”
        query_matieres = f"""
            SELECT
            a.student_id,
            t.subject            AS matière,
            COUNT(*)             AS nb_absences
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t
            ON t.filiere = s.filiere
            AND t.niveau  = s.niveau
            AND t.slot    = a.hour
            AND t.day     = CASE strftime('%w', a.date)
                            WHEN '1' THEN 'Lundi'
                            WHEN '2' THEN 'Mardi'
                            WHEN '3' THEN 'Mercredi'
                            WHEN '4' THEN 'Jeudi'
                            WHEN '5' THEN 'Vendredi'
                            WHEN '6' THEN 'Samedi'
                            END
            WHERE
            a.status    = 'absent'
            AND a.justified = 0
            AND s.filiere IN ({placeholders_fil})
            AND s.niveau  IN ({placeholders_niv})
            AND a.date   BETWEEN ? AND ?
            AND strftime('%w', a.date) BETWEEN '1' AND '6'
            AND a.hour   IN ('8–10','10–12','12–14','14–16','16–18')
            GROUP BY
            a.student_id, t.subject
            HAVING
            COUNT(*) >= 3
        """
        params_mat = filieres_sel + niveaux_sel + [start_date, end_date]
        matieres_problematiques = conn.execute(query_matieres, params_mat).fetchall()

        for (stud_id, matiere, nb_abs) in matieres_problematiques:
            message = f"🚨 Vous avez manqué {nb_abs} séances en {matiere} entre {start_date} et {end_date} (non justifiées)."
            déjà = conn.execute(
                "SELECT 1 FROM alerts WHERE student_id = ? AND message = ? AND viewed = 0",
                (stud_id, message)
            ).fetchone()
            if not déjà:
                conn.execute(
                    "INSERT INTO alerts(student_id, message) VALUES(?, ?)",
                    (stud_id, message)
                )

        # b) Alerte “absent·e toute la journée” (5 créneaux différents, non justifiées)
        query_full_day = f"""
            SELECT
            a.student_id,
            a.date,
            COUNT(DISTINCT a.hour) AS cnt_slots
            FROM absences a
            JOIN students s ON a.student_id = s.id
            WHERE
            a.status    = 'absent'
            AND a.justified = 0
            AND s.filiere IN ({placeholders_fil})
            AND s.niveau  IN ({placeholders_niv})
            AND a.date  BETWEEN ? AND ?
            AND strftime('%w', a.date) BETWEEN '1' AND '6'
            AND a.hour  IN ('8–10','10–12','12–14','14–16','16–18')
            GROUP BY
            a.student_id, a.date
            HAVING cnt_slots = 5
        """
        params_full = filieres_sel + niveaux_sel + [start_date, end_date]
        full_day_abs = conn.execute(query_full_day, params_full).fetchall()

        for (stud_id, jour_str, cnt) in full_day_abs:
            message2 = f"⚠️ Vous avez été absent·e toute la journée (5 créneaux) le {jour_str}."
            déjà2 = conn.execute(
                "SELECT 1 FROM alerts WHERE student_id = ? AND message = ? AND viewed = 0",
                (stud_id, message2)
            ).fetchone()
            if not déjà2:
                conn.execute(
                    "INSERT INTO alerts(student_id, message) VALUES(?, ?)",
                    (stud_id, message2)
                )

        conn.commit()

        nb_nouvelles_alertes = len(matieres_problematiques) + len(full_day_abs)
        if nb_nouvelles_alertes:
            st.success(f"{nb_nouvelles_alertes} alerte(s) générée(s) pour la période sélectionnée.")
        else:
            st.info("Aucune alerte à générer pour la période sélectionnée.")

        # 10) Compteur total d’alertes non lues depuis le début de l’année scolaire
        today = date.today()
        if today.month >= 9:
            debut_annee = date(today.year, 9, 1)
            fin_annee   = date(today.year + 1, 6, 30)
        else:
            debut_annee = date(today.year - 1, 9, 1)
            fin_annee   = date(today.year, 6, 30)

        df_alertes_admin = pd.read_sql(
            """
            SELECT COUNT(*) AS nb_alertes
            FROM alerts
            WHERE created_at BETWEEN ? AND ? AND viewed = 0
            """,
            conn,
            params=(debut_annee.isoformat(), fin_annee.isoformat())
        )
        nb_alertes_admin = int(df_alertes_admin["nb_alertes"].iloc[0])
        st.metric("Alertes actives (non lues) depuis le 1er sept", nb_alertes_admin)


    # ───  GESTION des ÉTUDIANTS (Admin) ─────────────────────────────────────
    with tabs[1]:
        st.header("👥 Gestion des Étudiants")
        st.subheader("➕ Ajouter un étudiant")

        col1, col2 = st.columns(2)
        with col1:
            fn = st.text_input("Prénom", key="std_fn")
            ln = st.text_input("Nom",     key="std_ln")
            em = st.text_input("Email",   key="std_em")
            cne = st.text_input("CNE",    key="std_cne")
        with col2:
            fil = st.text_input("Filière", key="std_fil")
            niv = st.text_input("Niveau",  key="std_niv")
            photo_file = st.file_uploader("Photo JPG/PNG", type=["jpg","jpeg","png"], key="std_ph")

        if st.button("Ajouter un étudiant"):
            if not all([fn, ln, em, cne, fil, niv, photo_file]):
                st.warning("Merci de remplir tous les champs et d’uploader une photo.")
            else:
                img = Image.open(io.BytesIO(photo_file.getvalue())).convert("RGB")
                embs = get_embedding(img)
                if embs is None or len(embs) == 0:
                    st.error("Impossible de détecter un visage sur la photo.")
                else:
                    emb_blob   = embs[0].tobytes()
                    photo_blob = photo_file.getvalue()
                    default_pwd = cne
                    conn.execute("""
                        INSERT OR REPLACE INTO users
                        (email, pwd, role, name, photo)
                        VALUES (?,      ?,   ?,    ?,    ?)
                    """, (
                        em,
                        default_pwd,
                        "Étudiant",
                        f"{fn} {ln}",
                        photo_blob
                    ))
                    conn.execute("""
                        INSERT OR REPLACE INTO students
                        (email, emb,         first_name, last_name,
                            cne,   filiere,     niveau,     photo)
                        VALUES (?,     ?,       ?,          ?,
                                ?,     ?,       ?,          ?)
                    """, (
                        em,
                        emb_blob,
                        fn,
                        ln,
                        cne,
                        fil,
                        niv,
                        photo_blob
                    ))
                    conn.commit()
                    st.success(
                        f"Étudiant **{fn} {ln}** ajouté avec succès !\n\n"
                        f"• Identifiant : `{em}`\n"
                        f"• Mot de passe initial : `{default_pwd}`\n\n"
                        "Demandez à l’étudiant de changer son mot de passe lors de sa première connexion."
                    )
                    st.experimental_rerun()

        st.markdown("---")
        st.markdown("### 👩‍🎓 Liste des étudiants par filière et niveau")

        df_students = pd.read_sql("""
            SELECT first_name, last_name, email, cne, filiere, niveau
            FROM students
        """, conn)

        if df_students.empty:
            st.info("Aucun étudiant enregistré.")
        else:
            df_students["filiere"] = df_students["filiere"].fillna("Non renseignée")
            df_students["niveau"]  = df_students["niveau"].fillna("Inconnu")
            niveaux_fixés = ["1ère année", "2ème année"]
            for filière in sorted(df_students["filiere"].unique()):
                st.subheader(f"🎓 Filière : {filière}")
                df_fil = df_students[df_students["filiere"] == filière]
                for niv in niveaux_fixés:
                    df_classe = df_fil[df_fil["niveau"] == niv]
                    if df_classe.empty:
                        st.markdown(f"*{niv} : (vide)*")
                    else:
                        st.markdown(f"**{niv}**")
                        st.table(df_classe[["first_name","last_name","email","cne"]])
                df_autres = df_fil[~df_fil["niveau"].isin(niveaux_fixés)]
                if not df_autres.empty:
                    st.markdown("**Autres niveaux**")
                    st.dataframe(
                        df_autres[["first_name","last_name","email","cne","niveau"]],
                        use_container_width=True,
                        hide_index=True
                    )
                st.markdown("---")


    # ───  GESTION des ENSEIGNANTS (Admin) ───────────────────────────────────
    with tabs[2]:
        st.header("👤 Gestion des Enseignants")
        st.subheader("➕ Ajouter un enseignant")
        tfn = st.text_input("Prénom", key="t_fn")
        tln = st.text_input("Nom",     key="t_ln")
        tem = st.text_input("Email",   key="t_em")
        if st.button("Ajouter enseignant"):
            if not all([tfn, tln, tem]):
                st.warning("Merci de remplir tous les champs.")
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO users(email,pwd,role,name) VALUES(?,?,?,?)",
                    (tem, "prof123", "Enseignant", f"{tfn} {tln}")
                )
                conn.execute("INSERT OR REPLACE INTO teachers(email) VALUES(?)", (tem,))
                conn.commit()
                st.success(f"{tfn} {tln} ajouté ✅ (pwd : prof123)")
                st.experimental_rerun()
        st.markdown("---")
        df_t = pd.read_sql(
            "SELECT u.name, u.email FROM users u JOIN teachers t ON u.email=t.email",
            conn
        )
        st.dataframe(df_t, use_container_width=True)


    # ───  VALIDATION des JUSTIFICATIFS ──────────────────────────────────────
    with tabs[3]:
        st.header("📑 Validation des justificatifs & justification manuelle")

        # 1) Partie existante : Justificatifs en attente
        dfj = pd.read_sql("""
            SELECT
              a.id,
              a.date,
              a.hour,
              a.justificatif,
              a.justified,
              s.first_name,
              s.last_name,
              s.filiere,
              s.niveau
            FROM absences a
            JOIN students s ON a.student_id = s.id
            WHERE a.justificatif IS NOT NULL
            ORDER BY a.justified, a.date DESC, a.hour
        """, conn)

        pending = dfj[dfj["justified"] == 0]
        done    = dfj[dfj["justified"] == 1]

        if not pending.empty:
            st.subheader("🕒 Justificatifs en attente")
            for _, r in pending.iterrows():
                st.markdown(
                    f"**{r.first_name} {r.last_name}** — *{r.filiere} / {r.niveau}* — {r.date} ・ {r.hour}"
                )
                st.write(f"[Voir justificatif]({r.justificatif})")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Valider justificatif", key=f"val_doc_{r.id}"):
                        conn.execute("UPDATE absences SET justified = 1 WHERE id = ?", (r.id,))
                        conn.commit()
                        st.experimental_rerun()
                with c2:
                    if st.button("Rejeter justificatif", key=f"rej_doc_{r.id}"):
                        conn.execute("UPDATE absences SET justificatif = NULL WHERE id = ?", (r.id,))
                        conn.commit()
                        st.experimental_rerun()
        else:
            st.info("Aucun justificatif en attente.")

        if not done.empty:
            st.subheader("✅ Justificatifs validés")
            for _, r in done.iterrows():
                st.markdown(
                    f"**{r.first_name} {r.last_name}** — *{r.filiere} / {r.niveau}* — {r.date} ・ {r.hour} — ✅ Validé"
                )
                st.write(f"[Voir justificatif]({r.justificatif})")
        else:
            st.info("Aucun justificatif déjà validé.")

        st.markdown("---")

        # 2) Partie “Justifier MANUELLEMENT” — tout dans un seul tableau
        st.subheader("✍️ Justifier MANUELLEMENT une absence (sans fichier)")

        df_nonjust = pd.read_sql("""
            SELECT
              a.id,
              a.student_id,
              s.first_name || ' ' || s.last_name AS Étudiant,
              s.filiere,
              s.niveau,
              a.date AS Date,
              a.hour AS Séance,
              CASE WHEN a.justificatif IS NOT NULL THEN 'Oui' ELSE 'Non' END AS "A un justificatif"
            FROM absences a
            JOIN students s ON a.student_id = s.id
            WHERE a.justified = 0
            ORDER BY a.date DESC, a.hour
        """, conn)

        if df_nonjust.empty:
            st.info("Aucune absence non justifiée à ce jour.")
            st.stop()

        df_nonjust["Justifier ✅"] = False
        df_affiche = df_nonjust[[
            "id",
            "Étudiant",
            "filiere",
            "niveau",
            "Date",
            "Séance",
            "A un justificatif",
            "Justifier ✅"
        ]].copy()
        df_affiche.set_index("id", inplace=True)

        edited = st.experimental_data_editor(
            df_affiche,
            use_container_width=True,
            num_rows="fixed"
        )

        if st.button("Enregistrer les justifications manuelles"):
            to_justifier = [abs_id for abs_id, row in edited.iterrows() if row["Justifier ✅"]]
            if to_justifier:
                for abs_id in to_justifier:
                    conn.execute("UPDATE absences SET justified = 1 WHERE id = ?", (abs_id,))
                conn.commit()
                st.success(f"{len(to_justifier)} absence(s) justifiée(s) avec succès !")
                st.experimental_rerun()
            else:
                st.info("Aucune case cochée. Rien à enregistrer.")

        st.markdown(
            """
            > 🔍 **Conseil** : Vous pouvez filtrer directement dans le tableau !  
            > · Cliquez sur l’icône d’entonnoir dans l’en-tête de la colonne **filiere**,  
            >   **niveau**, ou **Étudiant**, pour n’afficher que les lignes souhaitées.  
            > · Cochez la colonne **Justifier ✅** pour chaque absence à justifier,  
            >   puis cliquez sur **Enregistrer les justifications manuelles**.
            """
        )

    # ───  GESTION des EMPLOIS DE TEMPS (Admin) ─────────────────────────────
    with tabs[4]:
        st.header("🗓️ Gestion des emplois du temps")
        st.markdown("""
        <style>
        [data-testid="column"] {
            border: 1px solid #ddd !important;
            padding: 8px !important;
        }
        [data-testid="column"] > div {
            border: none !important;
        }
        </style>
        """, unsafe_allow_html=True)

        df_profs = pd.read_sql(
            "SELECT u.name FROM users u JOIN teachers t ON u.email=t.email",
            conn
        )
        profs = df_profs["name"].tolist()
        if not profs:
            st.warning("Aucun enseignant enregistré. Merci d’ajouter des professeurs avant de créer un emploi du temps.")
            st.stop()

        filieres = [f or "Non renseignée"
                    for f in pd.read_sql("SELECT DISTINCT filiere FROM students", conn)["filiere"]]
        niveaux  = ["1ère année","2ème année"]
        sel_fil  = st.selectbox("Filière", sorted(set(filieres)))
        sel_niv  = st.selectbox("Niveau", niveaux)

        days  = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi"]
        slots = ["8–10","10–12","12–14","14–16","16–18"]

        df_exist = pd.read_sql(
            "SELECT day, slot, subject, teacher FROM timetables WHERE filiere=? AND niveau=?",
            conn, params=(sel_fil, sel_niv)
        )
        prepop = {(r["day"], r["slot"]): (r["subject"], r["teacher"])
                  for _, r in df_exist.iterrows()}

        st.markdown("**Double-cliquez sur chaque case pour taper :**  \n"
                    "`Matière` puis `Professeur` (séparés par un saut de ligne)`")

        header_cols = st.columns(len(slots) + 1)
        header_cols[0].markdown("**Jour\\Créneau**")
        for i, slot in enumerate(slots, start=1):
            header_cols[i].markdown(f"**{slot}**")

        subj_vals = {}
        prof_vals = {}

        for day in days:
            row_cols = st.columns(len(slots) + 1)
            row_cols[0].markdown(f"**{day}**")
            for j, slot in enumerate(slots, start=1):
                key_subj = f"tt_{sel_fil}_{sel_niv}_{day}_{slot}_subj"
                key_prof = f"tt_{sel_fil}_{sel_niv}_{day}_{slot}_prof"
                init_subj, init_prof = prepop.get((day, slot), ("", ""))

                with row_cols[j]:
                    subj_vals[(day, slot)] = st.text_input(
                        label="", value=init_subj, key=key_subj,
                        placeholder="Matière", label_visibility="collapsed"
                    )
                    prof_vals[(day,slot)] = st.selectbox(
                        "",
                        options=[""] + profs,
                        index=0,
                        key=key_prof,
                        label_visibility="collapsed"
                    )

        if st.button("Enregistrer l’emploi du temps"):
            conn.execute("DELETE FROM timetables WHERE filiere=? AND niveau=?", (sel_fil, sel_niv))
            for day in days:
                for slot in slots:
                    subj = subj_vals[(day, slot)].strip()
                    prof = prof_vals[(day, slot)].strip()
                    if subj or prof:
                        conn.execute("""
                            INSERT INTO timetables
                            (filiere, niveau, day, slot, subject, teacher)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (sel_fil, sel_niv, day, slot, subj, prof))
            conn.commit()
            st.success("Emploi du temps sauvegardé ✅")


    # ───  PARAMÈTRES (Admin) ─────────────────────────────────────────────────
    with tabs[5]:
        st.header("⚙️ Paramètres")
        st.subheader("🔑 Réinitialiser mot de passe")
        df_users = pd.read_sql("SELECT email, role FROM users", conn)
        sel_user = st.selectbox("Utilisateur", df_users['email'], key='pwd_user')
        new_pwd  = st.text_input("Nouveau mot de passe", type="password", key='new_pwd')
        if st.button("Mettre à jour le mot de passe"):
            if new_pwd:
                conn.execute("UPDATE users SET pwd=? WHERE email=?", (new_pwd, sel_user))
                conn.commit()
                st.success(f"Mot de passe mis à jour pour {sel_user}")
            else:
                st.warning("Entrez un nouveau mot de passe.")

        st.markdown("---")
        st.subheader("🗑️ Supprimer un utilisateur")
        df_del = pd.read_sql("SELECT email, role FROM users WHERE role!='Admin'", conn)
        sel_del = st.selectbox("Sélectionnez l'utilisateur à supprimer", df_del['email'], key='del_user')
        if st.button("Supprimer l'utilisateur"):
            role_del = df_del[df_del["email"]==sel_del]['role'].iloc[0]
            if role_del == 'Étudiant':
                conn.execute("DELETE FROM students WHERE email=?", (sel_del,))
            elif role_del == 'Enseignant':
                conn.execute("DELETE FROM teachers WHERE email=?", (sel_del,))
            conn.execute("DELETE FROM users WHERE email=?", (sel_del,))
            conn.commit()
            st.success(f"Utilisateur {sel_del} supprimé.")
            st.experimental_rerun()

        st.markdown("---")
        st.subheader("✏️ Modifier un étudiant")
        df_std = pd.read_sql("SELECT email, first_name, last_name, cne, filiere, niveau FROM students", conn)
        if not df_std.empty:
            sel_std = st.selectbox("Choisir un étudiant", df_std['email'], key='mod_std')
            row = df_std[df_std["email"]==sel_std].iloc[0]
            fn2 = st.text_input("Prénom", row['first_name'], key='mod_fn')
            ln2 = st.text_input("Nom", row['last_name'], key='mod_ln')
            cne2= st.text_input("CNE", row['cne'], key='mod_cne')
            fil2= st.text_input("Filière", row['filiere'], key='mod_fil')
            niv2= st.text_input("Niveau", row['niveau'], key='mod_niv')
            if st.button("Modifier l'étudiant"):
                conn.execute(
                    """
                    UPDATE students SET first_name=?, last_name=?, cne=?, filiere=?, niveau=?
                    WHERE email=?
                    """, (fn2, ln2, cne2, fil2, niv2, sel_std))
                conn.commit()
                st.success("Étudiant modifié avec succès.")
                st.experimental_rerun()
        else:
            st.info("Aucun étudiant à modifier.")

        st.markdown("---")
        st.subheader("✏️ Modifier un enseignant")
        df_te = pd.read_sql(
            "SELECT t.email, u.name FROM teachers t JOIN users u ON t.email=u.email",
            conn
        )
        if not df_te.empty:
            sel_te = st.selectbox("Choisir un enseignant", df_te['email'], key='mod_te')
            name_te = st.text_input("Nom complet", df_te[df_te["email"]==sel_te]['name'].iloc[0], key='mod_tname')
            if st.button("Modifier l'enseignant"):
                conn.execute("UPDATE users SET name=? WHERE email=?", (name_te, sel_te))
                conn.commit()
                st.success("Enseignant modifié avec succès.")
                st.experimental_rerun()
        else:
            st.info("Aucun enseignant à modifier.")


# ─── ENSEIGNANT ─────────────────────────────────────────────────────────
elif role == "Enseignant":
    st.markdown("""
        <style>
        
        /* 2) Injecter l'image de fond sur le conteneur principal */
        [data-testid="stAppViewContainer"] .block-container {
            background: url("https://img.freepik.com/premium-photo/blue-sky-white-cloud-abstract-watercolor-background_91515-336.jpg?w=360")
                        center/cover no-repeat fixed !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <style>
        /* Centre tout texte mis en header (st.header → <h2>) */
        h2 {
            text-align: center !important;
        }
        </style>
    """, unsafe_allow_html=True)  

    teacher_name = st.session_state.name

    # Onglet Dashboard Enseignant
    with tabs[0]:
        st.header("📊 Mon Dashboard Enseignant")

        # 1) Récupérer les (filière, niveau) qu'il enseigne
        df_pairs = pd.read_sql(
            """
            SELECT DISTINCT t.filiere, t.niveau
            FROM timetables t
            WHERE t.teacher = ?
            """,
            conn,
            params=(teacher_name,)
        )

        if df_pairs.empty:
            st.info("Vous n’avez aucun cours planifié pour le moment.")
            st.stop()

        # 2) Sélecteurs Filière / Niveau parmi ceux qu’il enseigne
        df_classes_filieres = sorted(df_pairs["filiere"].dropna().unique())
        sel_filiere = st.selectbox("Filière à visualiser", df_classes_filieres)

        niveaux_dispo = (
            df_pairs[df_pairs["filiere"] == sel_filiere]["niveau"]
            .dropna()
            .unique()
            .tolist()
        )
        sel_niveau = st.selectbox("Niveau à visualiser", niveaux_dispo)

        # 3) Choix de la période
        period = st.selectbox("Période", ["Cette semaine", "Ce mois", "Année scolaire"])
        today = date.today()
        end_date = today.isoformat()
        if period == "Cette semaine":
            debut_semaine = today - timedelta(days=today.weekday())
            start_date = debut_semaine.isoformat()
        elif period == "Ce mois":
            start_30j = today - timedelta(days=30)
            start_date = start_30j.isoformat()
        else:  # “Année scolaire” → 1er sept. → 30 juin
            if today.month >= 9:
                annee_debut = today.year
                fin_annee   = date(today.year + 1, 6, 30)
            else:
                annee_debut = today.year - 1
                fin_annee   = date(today.year, 6, 30)
            start_date = date(annee_debut, 9, 1).isoformat()
            end_date   = fin_annee.isoformat()

        st.markdown(f"**Période sélectionnée :** du {start_date} au {end_date}")

        # ─── Statistiques d’absence (uniquement pour les matières de ce prof) ──
        st.subheader(f"📈 Statistiques – {sel_filiere} / {sel_niveau}")

        # a) Total absences (toutes confondues)
        df_stats_total = pd.read_sql(
            """
            SELECT COUNT(*) AS total_absences
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t 
              ON t.filiere = s.filiere
             AND t.niveau  = s.niveau
             AND t.slot    = a.hour
             AND t.day     = CASE strftime('%w', a.date)
                               WHEN '1' THEN 'Lundi'
                               WHEN '2' THEN 'Mardi'
                               WHEN '3' THEN 'Mercredi'
                               WHEN '4' THEN 'Jeudi'
                               WHEN '5' THEN 'Vendredi'
                               WHEN '6' THEN 'Samedi'
                             END
             AND t.teacher = ?
            WHERE
              a.status = 'absent'
              AND s.filiere = ?
              AND s.niveau = ?
              AND a.date BETWEEN ? AND ?
            """,
            conn,
            params=(teacher_name, sel_filiere, sel_niveau, start_date, end_date)
        )
        total_abs = int(df_stats_total["total_absences"].iloc[0])

        # b) Absences justifiées pour ce prof
        df_stats_just = pd.read_sql(
            """
            SELECT COUNT(*) AS justifiees
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t 
              ON t.filiere = s.filiere
             AND t.niveau  = s.niveau
             AND t.slot    = a.hour
             AND t.day     = CASE strftime('%w', a.date)
                               WHEN '1' THEN 'Lundi'
                               WHEN '2' THEN 'Mardi'
                               WHEN '3' THEN 'Mercredi'
                               WHEN '4' THEN 'Jeudi'
                               WHEN '5' THEN 'Vendredi'
                               WHEN '6' THEN 'Samedi'
                             END
             AND t.teacher = ?
            WHERE
              a.status = 'absent'
              AND a.justified = 1
              AND s.filiere = ?
              AND s.niveau = ?
              AND a.date BETWEEN ? AND ?
            """,
            conn,
            params=(teacher_name, sel_filiere, sel_niveau, start_date, end_date)
        )
        justifiees = int(df_stats_just["justifiees"].iloc[0])

        # c) Absences non justifiées pour ce prof
        non_justifiees = total_abs - justifiees

        df_students = pd.read_sql(
            """
            SELECT COUNT(*) AS total_eleves
            FROM students
            WHERE filiere = ? AND niveau = ?
            """,
            conn,
            params=(sel_filiere, sel_niveau)
        )
        total_eleves = int(df_students["total_eleves"].iloc[0]) or 1
        taux = total_abs / total_eleves

        col1, col2, col3 = st.columns(3)
        col1.metric("Total absences", total_abs)
        col2.metric("Absences justifiées", justifiees, f"{justifiees/total_abs: .1%}" if total_abs else "—")
        col3.metric("Absences non justifiées", non_justifiees, f"{non_justifiees/total_abs: .1%}" if total_abs else "—")

        # ─── Évolution des absences (jour par jour) ─────────────────────────
        st.subheader("Évolution des absences (jour par jour)")
        df_trend = pd.read_sql(
            """
            SELECT a.date AS Date, COUNT(*) AS count
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t 
              ON t.filiere = s.filiere
             AND t.niveau  = s.niveau
             AND t.slot    = a.hour
             AND t.day     = CASE strftime('%w', a.date)
                               WHEN '1' THEN 'Lundi'
                               WHEN '2' THEN 'Mardi'
                               WHEN '3' THEN 'Mercredi'
                               WHEN '4' THEN 'Jeudi'
                               WHEN '5' THEN 'Vendredi'
                               WHEN '6' THEN 'Samedi'
                             END
             AND t.teacher = ?
            WHERE
              a.status = 'absent'
              AND s.filiere = ?
              AND s.niveau = ?
              AND a.date BETWEEN ? AND ?
            GROUP BY a.date
            ORDER BY a.date
            """,
            conn,
            params=(teacher_name, sel_filiere, sel_niveau, start_date, end_date)
        )
        if not df_trend.empty:
            st.line_chart(df_trend.set_index("Date")["count"])
        else:
            st.info("Aucune donnée d’absence pour cette période.")

        # ─── Liste des absences récentes (limité à 20) ───────────────────────
        st.subheader("📋 Absences récentes")
        df_recent = pd.read_sql(
            """
            SELECT
              a.date   AS Date,
              a.hour   AS Séance,
              s.first_name || ' ' || s.last_name AS Étudiant,
              CASE a.justified WHEN 1 THEN 'Oui' ELSE 'Non' END AS Justifiée,
              a.justificatif AS LienJustificatif
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t 
              ON t.filiere = s.filiere
             AND t.niveau  = s.niveau
             AND t.slot    = a.hour
             AND t.day     = CASE strftime('%w', a.date)
                               WHEN '1' THEN 'Lundi'
                               WHEN '2' THEN 'Mardi'
                               WHEN '3' THEN 'Mercredi'
                               WHEN '4' THEN 'Jeudi'
                               WHEN '5' THEN 'Vendredi'
                               WHEN '6' THEN 'Samedi'
                             END
             AND t.teacher = ?
            WHERE
              a.status = 'absent'
              AND s.filiere = ?
              AND s.niveau = ?
              AND a.date BETWEEN ? AND ?
            ORDER BY a.date DESC, a.hour
            LIMIT 20
            """,
            conn,
            params=(teacher_name, sel_filiere, sel_niveau, start_date, end_date)
        )
        if not df_recent.empty:
            st.dataframe(df_recent, use_container_width=True)
        else:
            st.info("Pas d’absences récentes pour vos matières.")

    # ─── ENSEIGNANT: APPEL MANUEL ─────────────────────────────────────────
    with tabs[1]:
        st.header("✏️ Appel Manuel par Heure")
        call_date = st.date_input("Date de l'appel", value=date.today(), key="call_date")

        filieres = (
            pd.read_sql("SELECT DISTINCT filiere FROM students", conn)["filiere"]
            .fillna("Non renseignée")
            .tolist()
        )
        niveaux = ["1ère année", "2ème année"]
        sel_fil = st.selectbox("Filière", sorted(filieres), key="call_fil")
        sel_niv = st.selectbox("Niveau", niveaux, key="call_niv")

        df_students = pd.read_sql(
            """
            SELECT id, first_name || ' ' || last_name AS name
            FROM students
            WHERE filiere=? AND niveau=?
            ORDER BY last_name
            """,
            conn,
            params=(sel_fil, sel_niv)
        )
        if df_students.empty:
            st.warning("Aucun étudiant dans cette filière / ce niveau.")
            st.stop()

        slots = ["8–10", "10–12", "12–14", "14–16", "16–18"]
        pivot = pd.DataFrame(False, index=df_students["id"], columns=slots)
        pivot.insert(0, "Nom", df_students["name"].values)

        st.markdown("**Cochez les cases pour marquer une absence**")
        edited = st.experimental_data_editor(
            pivot,
            num_rows="fixed",
            use_container_width=True
        )

        if st.button("Enregistrer les absences"):
            for sid, row in edited.iterrows():
                for slot in slots:
                    if row[slot]:
                        exists = conn.execute(
                            "SELECT 1 FROM absences WHERE student_id=? AND date=? AND hour=?",
                            (sid, call_date.isoformat(), slot)
                        ).fetchone()
                        if not exists:
                            conn.execute(
                                """
                                INSERT INTO absences
                                (student_id, date, hour, status)
                                VALUES (?, ?, ?, ?)
                                """,
                                (sid, call_date.isoformat(), slot, "absent")
                            )
            conn.commit()
            st.success("✅ Absences enregistrées (sans doublons) pour les cases cochées !")


    # ─── ENSEIGNANT: RECONNAISSANCE FACIALE ─────────────────────────────────
    with tabs[2]:
        st.header("📷 Appel automatique par Reconnaissance Faciale")

        filieres = (
            pd.read_sql("SELECT DISTINCT filiere FROM students", conn)["filiere"]
            .fillna("Non renseignée")
            .tolist()
        )
        niveaux = ["1ère année", "2ème année"]
        sel_fil = st.selectbox("Filière", sorted(filieres), key="auto_fil")
        sel_niv = st.selectbox("Niveau", niveaux, key="auto_niv")

        now = datetime.now()
        date_str = now.date().isoformat()
        h, m = now.hour, now.minute
        if 8 <= h < 10:
            slot = "8–10"
        elif 10 <= h < 12:
            slot = "10–12"
        elif 12 <= h < 14:
            slot = "12–14"
        elif 14 <= h < 16:
            slot = "14–16"
        elif 16 <= h < 18:
            slot = "16–18"
        else:
            slot = f"{h:02d}h{m:02d}"
            st.warning("Créneau hors plage officielle (8–18). Les absences ne seront PAS enregistrées en base.")

        st.markdown(f"**Date** : {date_str} &nbsp;&nbsp; **Créneau (estimé)** : {slot}")

        df_studs = pd.read_sql(
            """
            SELECT id, first_name, last_name
            FROM students
            WHERE filiere=? AND niveau=?
            """,
            conn,
            params=(sel_fil, sel_niv)
        )
        df_studs["name"] = df_studs["first_name"] + " " + df_studs["last_name"]

        if df_studs.empty:
            st.warning("Aucun étudiant pour cette filière/niveau.")
            st.stop()

        status_map = {row["id"]: "absent" for _, row in df_studs.iterrows()}

        df_emb = pd.read_sql(
            "SELECT id, emb, first_name, last_name FROM students WHERE filiere=? AND niveau=?",
            conn,
            params=(sel_fil, sel_niv)
        )
        df_emb["arr"] = df_emb["emb"].apply(
            lambda b: np.frombuffer(b, dtype=np.float32) if b else None
        )
        valid = df_emb[df_emb["arr"].notnull()]
        known_ids = valid["id"].tolist()
        known_embs = valid["arr"].tolist()
        names = (valid["first_name"] + " " + valid["last_name"]).tolist()

        if not known_embs:
            st.warning("Aucun embedding disponible pour cette classe.")
        else:
            if "cam_active" not in st.session_state:
                st.session_state.cam_active = False
            if "last_capture" not in st.session_state:
                st.session_state.last_capture = None

            if st.button("📷 Activer la caméra"):
                st.session_state.cam_active = True
                st.session_state.last_capture = None
                st.experimental_rerun()

            if st.session_state.cam_active:
                img_file = st.camera_input("Positionnez-vous et prenez la photo", key="auto_cam")
                if img_file:
                    st.session_state.last_capture = img_file.getvalue()
                    st.session_state.cam_active = False
                    st.experimental_rerun()

            if st.session_state.last_capture is not None:
                img = Image.open(io.BytesIO(st.session_state.last_capture)).convert("RGB")
                frame = np.array(img)

                boxes, _ = mtcnn.detect(img)
                faces = mtcnn(img)
                if boxes is None or faces is None:
                    st.error("Aucun visage détecté.")
                else:
                    if faces.ndim == 3:
                        faces = faces.unsqueeze(0)

                    for box, face_tensor in zip(boxes, faces):
                        x1, y1, x2, y2 = box.astype(int)
                        with torch.no_grad():
                            emb = resnet(face_tensor.unsqueeze(0)).cpu().numpy()[0]
                        dists = [np.linalg.norm(emb - ke) for ke in known_embs]
                        idx = int(np.argmin(dists))
                        dist = dists[idx]
                        match = dist < 1.2

                        student_id = known_ids[idx]
                        color = (0, 255, 0) if match else (255, 0, 0)

                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                        if match:
                            label = f"{names[idx]} ({dist:.2f})"
                            cv2.putText(
                                frame,
                                label,
                                (x1, max(y1 - 10, 0)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                color,
                                2
                            )
                            status_map[student_id] = "present"

                    st.image(frame, channels="RGB")

                    # Vérification “créneau valide”
                    jour_valide = now.weekday() <= 5       # 0..5 => Lundi..Samedi
                    slot_valide = slot in ["8–10","10–12","12–14","14–16","16–18"]

                    if not (jour_valide and slot_valide):
                        st.warning(
                            "Créneau ou jour non valide (hors Lundi–Samedi 8–18). "
                            "Le bilan est affiché, mais les absences NE SONT PAS enregistrées en base."
                        )
                    else:
                        # Insert en base uniquement si le créneau/jour est valide
                        for sid, stat in status_map.items():
                            exists = conn.execute(
                                "SELECT 1 FROM absences WHERE student_id=? AND date=? AND hour=?",
                                (sid, date_str, slot)
                            ).fetchone()
                            if not exists:
                                conn.execute(
                                    """
                                    INSERT INTO absences
                                    (student_id, date, hour, status)
                                    VALUES (?,?,?,?)
                                    """,
                                    (sid, date_str, slot, stat)
                                )
                        conn.commit()
                        n_pres = sum(1 for s in status_map.values() if s == "present")
                        n_abs = sum(1 for s in status_map.values() if s == "absent")
                        st.success(f"Appel enregistré ✔️  Présents : {n_pres} | Absents : {n_abs}")

                    # Toujours afficher le bilan
                    report = [
                        {
                            "Nom": row["first_name"] + " " + row["last_name"],
                            "Statut": status_map[row["id"]]
                        }
                        for _, row in df_studs.iterrows()
                    ]
                    df_report = pd.DataFrame(report)
                    st.markdown("### 📋 Bilan de l’appel")
                    st.dataframe(df_report, use_container_width=True)


    # ─── ENSEIGNANT: MES CLASSES ────────────────────────────────────────────
    with tabs[3]:
        st.header("📚 Mes classes")
        teacher_name = st.session_state.name

        st.markdown("Mon emploi du temps")
        df_tt = pd.read_sql(
            """
            SELECT day, slot, subject, filiere, niveau
            FROM timetables
            WHERE teacher = ?
            """,
            conn,
            params=(teacher_name,)
        )
        if df_tt.empty:
            st.info(f"Aucun cours trouvé pour {teacher_name}.")
        else:
            df_tt["cell"] = df_tt.apply(
                lambda r: f"**{r.subject}**<br><small>{r.filiere} – {r.niveau}</small>",
                axis=1
            )
            pivot = (
                df_tt
                .pivot(index="day", columns="slot", values="cell")
                .reindex(
                    index=["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
                    columns=["8–10", "10–12", "12–14", "14–16", "16–18"]
                )
                .fillna("")
            )
            st.markdown("""
                <style>
                table {
                  width: 100%;
                  border-collapse: collapse;
                }
                th, td {
                  border: 1px solid #ddd;
                  padding: 8px;
                  vertical-align: top;
                }
                th {
                  background-color: #f5f5f5;
                  text-align: center;
                }
                td {
                  white-space: normal;
                }
                </style>
            """, unsafe_allow_html=True)
            html = pivot.to_html(escape=False)
            st.markdown(html, unsafe_allow_html=True)

        st.markdown("Liste des étudiants")
        df_classes = pd.read_sql(
            """
            SELECT DISTINCT filiere, niveau
            FROM timetables
            WHERE teacher = ?
            """,
            conn,
            params=(teacher_name,)
        )
        if df_classes.empty:
            st.info(f"Aucun cours trouvé pour {teacher_name}.")
            st.stop()

        filieres = sorted(df_classes["filiere"].fillna("Non renseignée").unique())
        sel_fil = st.selectbox("Choisissez la filière", filieres)

        niveaux = (
            df_classes[df_classes["filiere"] == sel_fil]["niveau"]
            .fillna("Inconnu")
            .unique()
            .tolist()
        )
        sel_niv = st.selectbox("Choisissez le niveau", niveaux)

        df_cls = pd.read_sql(
            """
            SELECT id,
                   first_name || ' ' || last_name AS Nom,
                   cne AS CNE
            FROM students
            WHERE filiere = ? AND niveau = ?
            ORDER BY last_name, first_name
            """,
            conn,
            params=(sel_fil, sel_niv)
        )
        st.subheader(f"Liste des étudiant·e·s — {sel_fil} / {sel_niv}")
        if df_cls.empty:
            st.write("_Aucun·e étudiant·e enregistré·e dans cette classe._")
        else:
            st.dataframe(
                df_cls.set_index("id"),
                use_container_width=True
            )

        # ─── Historique des absences COMPLET pour ce prof (toutes celles de ses matières) ───
        st.markdown("---")
        st.subheader("📜 Historique COMPLET des absences pour mes matières")

        df_history = pd.read_sql(
            """
            SELECT
              a.date   AS Date,
              a.hour   AS Séance,
              s.first_name || ' ' || s.last_name AS Étudiant,
              CASE a.justified WHEN 1 THEN 'Oui' ELSE 'Non' END AS Justifiée
            FROM absences a
            JOIN students s ON a.student_id = s.id
            JOIN timetables t 
              ON t.filiere = s.filiere
             AND t.niveau  = s.niveau
             AND t.slot    = a.hour
             AND t.day     = CASE strftime('%w', a.date)
                               WHEN '1' THEN 'Lundi'
                               WHEN '2' THEN 'Mardi'
                               WHEN '3' THEN 'Mercredi'
                               WHEN '4' THEN 'Jeudi'
                               WHEN '5' THEN 'Vendredi'
                               WHEN '6' THEN 'Samedi'
                             END
             AND t.teacher = ?
            WHERE
              s.filiere = ?
              AND s.niveau = ?
              AND a.status = 'absent'
            ORDER BY a.date DESC, a.hour
            """,
            conn,
            params=(teacher_name, sel_fil, sel_niv)
        )
        if not df_history.empty:
            st.dataframe(df_history, use_container_width=True)
        else:
            st.info("Aucun historique d’absences pour vos matières.")


# ─── ÉTUDIANT ───────────────────────────────────────────────────────────
else:

    st.markdown("""
        <style>
        
        /* 2) Injecter l'image de fond sur le conteneur principal */
        [data-testid="stAppViewContainer"] .block-container {
            background: url("https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcR6rK8O7cs-okrcvbETSMcaP-_HLT2gUAYsuw&s")
                        center/cover no-repeat fixed !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <style>
        /* Centre tout texte mis en header (st.header → <h2>) */
        h2 {
            text-align: center !important;
        }
        </style>
    """, unsafe_allow_html=True)   

    with tabs[0]:
        st.header("📝 Mes absences")

        # 1) Récupérer l’ID de l’étudiant connecté
        row = conn.execute(
            "SELECT id FROM students WHERE email = ?",
            (email,)
        ).fetchone()
        if row is None:
            st.error("Profil introuvable ou incomplet.")
            st.stop()
        student_id = row[0]

        # 2) Afficher les alertes non lues pour cet étudiant
        df_alerts = pd.read_sql(
            """
            SELECT
                id,
                created_at,
                message
            FROM alerts
            WHERE student_id = ? AND viewed = 0
            ORDER BY created_at DESC
            """,
            conn,
            params=(student_id,)
        )

        if not df_alerts.empty:
            st.subheader("🔔 Mes alertes")
            for r in df_alerts.itertuples():
                st.markdown(f"- **{r.created_at}** : {r.message}")
                if st.button(f"Marquer l’alerte #{r.id} comme lue", key=f"ack_{r.id}"):
                    conn.execute("UPDATE alerts SET viewed = 1 WHERE id = ?", (r.id,))
                    conn.commit()
                    st.experimental_rerun()
        else:
            st.info("Aucune alerte en cours.")

        # 3) Ensuite, on affiche le tableau « Mes absences » complet
        df_a = pd.read_sql(
            """
            SELECT
                date   AS Date,
                hour   AS Séance,
                CASE WHEN justified = 1 THEN 'Justifiée' ELSE 'Non justifiée' END AS Statut,
                CASE WHEN justified = 1 THEN '✅' ELSE '' END AS "Justifiée"
            FROM absences
            WHERE student_id = ? AND status = 'absent'
            ORDER BY date DESC, hour
            """,
            conn,
            params=(student_id,)
        )
        if df_a.empty:
            st.info("Vous n’avez aucune absence enregistrée.")
        else:
            st.dataframe(df_a, use_container_width=True)

    with tabs[1]:
        st.header("📂 Soumettre justificatif")
        d0 = st.date_input("Date absence", value=date.today())
        file = st.file_uploader("Justificatif (jpg/png/pdf)", type=["jpg","png","pdf"])
        if st.button("Soumettre"):
            if not file:
                st.warning("Uploader un justificatif.")
            else:
                os.makedirs("justifs", exist_ok=True)
                path = f"justifs/{email}_{d0}.pdf"
                with open(path, "wb") as f:
                    f.write(file.getbuffer())
                conn.execute("""
                    INSERT OR IGNORE INTO absences(student_id,date,status,justificatif,hour)
                    VALUES((SELECT id FROM students WHERE email=?),?,?,?,NULL)
                """, (email, d0.isoformat(), "absent", path))
                conn.commit()
                st.success("Justificatif soumis.")

    with tabs[2]:
        st.header("📅 Mon emploi du temps")

        row = conn.execute(
            "SELECT filiere, niveau FROM students WHERE email=?",
            (email,)
        ).fetchone()
        if not row or not row[0] or not row[1]:
            st.error("Votre profil est incomplet : filière ou niveau manquant.")
            st.stop()
        etu_fil, etu_niv = row

        df_tt = pd.read_sql(
            """
            SELECT day, slot, subject, teacher
            FROM timetables
            WHERE filiere=? AND niveau=?
            """,
            conn, params=(etu_fil, etu_niv)
        )

        if df_tt.empty:
            st.info("Votre emploi du temps n'est pas encore défini.")
        else:
            df_tt["cell"] = df_tt.apply(
                lambda r: f"{r.subject}\n{r.teacher}".strip(), axis=1
            )
            pivot = (
                df_tt
                .pivot(index="day", columns="slot", values="cell")
                .reindex(index=["Lundi","Mardi","Mercredi","Jeudi","Vendredi"],
                         columns=["8–10","10–12","12–14","14–16","16–18"])
            )
            st.dataframe(
                pivot.fillna(""),
                use_container_width=True,
                hide_index=False
            )
