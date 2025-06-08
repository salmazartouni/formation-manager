import streamlit as st
import sqlite3
import pandas as pd
import time
import os
import base64
from datetime import date, datetime
from fpdf import FPDF
import requests
import altair as alt

# --- Configuration de la page ---
st.set_page_config(layout="wide", page_title="Formation Manager")

st.markdown(
    """
    <style>
      /* 1) Supprime le padding de tout le container principal */
      [data-testid="stAppViewContainer"] > .main {
        padding-top: 50px !important;
      }
      /* 2) Supprime le padding interne du bloc qui contient vos onglets + contenu */
      .block-container {
        padding-top: 0 !important;
      }
      /* 3) Écrase la marge haute des headers injectés via st.title / st.header */
      [data-testid="stMarkdownContainer"] h1,
      [data-testid="stMarkdownContainer"] h2 {
        margin-top: 3px !important;
      }
    </style>
    """,
    unsafe_allow_html=True
)

# --- Traduction dynamique ---
def t(fr, en, es):
    lang = st.session_state.get("lang", "Français")
    if lang == "English":
        return en
    if lang == "Español":
        return es
    return fr

# --- BDD Système pour paramètres ---
def get_conn_settings():
    conn = sqlite3.connect("system.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            param TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn

conn_sys = get_conn_settings()
cursor_sys = conn_sys.cursor()

def save_param(param, value):
    cursor_sys.execute("""
        INSERT INTO system_settings(param,value) VALUES(?,?)
        ON CONFLICT(param) DO UPDATE SET value=excluded.value
    """, (param, str(value)))
    conn_sys.commit()

def get_param(param, default=None):
    cursor_sys.execute("SELECT value FROM system_settings WHERE param=?", (param,))
    row = cursor_sys.fetchone()
    return row[0] if row else default

# --- Initialise la langue depuis la BDD ---
if "lang" not in st.session_state:
    st.session_state.lang = get_param("lang", "Français")

# --- BDD Utilisateurs ---
def get_conn_users():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS utilisateurs (
            email TEXT PRIMARY KEY,
            mot_de_passe TEXT NOT NULL,
            nom TEXT, prenom TEXT, fonction TEXT, genre TEXT, photo_path TEXT
        )
    """)
    conn.commit()
    return conn

conn_users = get_conn_users()
cur_users = conn_users.cursor()

# --- BDD Métiers ---
@st.cache_resource
def get_conn_employes():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT, prenom TEXT, fonction TEXT
        )
    """)
    conn.commit()
    return conn

@st.cache_resource
def get_conn_formations():
    conn = sqlite3.connect("formations.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS formations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL, date TEXT NOT NULL,
            duree INTEGER NOT NULL, formateur TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chapitres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            formation_id INTEGER, titre TEXT NOT NULL,
            type_contenu TEXT NOT NULL, contenu TEXT NOT NULL,
            ordre INTEGER NOT NULL,
            FOREIGN KEY(formation_id) REFERENCES formations(id)
        )
    """)
    conn.commit()
    return conn

@st.cache_resource
def get_conn_progress():
    conn = sqlite3.connect("progress.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            email TEXT, formation_id INTEGER,
            chapter_id INTEGER, timestamp TEXT,
            PRIMARY KEY(email,formation_id,chapter_id)
        )
    """)
    conn.commit()
    return conn

@st.cache_resource
def get_conn_tests():
    conn = sqlite3.connect("tests.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tests (
            email TEXT, formation_id INTEGER, passed INTEGER,
            PRIMARY KEY(email,formation_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            formation_id INTEGER, question_text TEXT NOT NULL,
            allow_multiple INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER, option_text TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            FOREIGN KEY(question_id) REFERENCES questions(id)
        )
    """)
    conn.commit()
    return conn

conn_emp = get_conn_employes()
cur_emp = conn_emp.cursor()
conn_form = get_conn_formations()
cur_form = conn_form.cursor()
conn_prog = get_conn_progress()
cur_prog = conn_prog.cursor()
conn_test = get_conn_tests()
cur_test = conn_test.cursor()

# --- Mapping fonctions OCP (nécessaire pour la gestion employés) ---
fonctions_ocp = {
    "Opérateur de production": "operateur_production",
    "Technicien de maintenance": "technicien_maintenance",
    "Ingénieur procédés": "ingenieur_procedes",
    "Responsable HSE": "responsable_hse",
    "Chef d’équipe": "chef_equipe",
    "Formateur": "formateur",
    "Responsable RH": "responsable_rh",
    "Responsable planification": "responsable_planification",
    "Développeur SI / Analyste": "developpeur_si",
    "Administrateur réseau / système": "admin_reseau",
    "Chef de projet": "chef_projet"
}

# --- État de session initial ---
for key in ["authenticated", "email", "login_email", "login_password"]:
    if key not in st.session_state:
        st.session_state[key] = False if key == "authenticated" else ""

# --- Page de connexion ---
def login_page():
    st.markdown("""
    <style>
    html, body, .stApp {
        background-color: #ffffff !important;
        background-image: url('https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/OCP_Group.svg/1606px-OCP_Group.svg.png') !important;
        background-repeat: no-repeat;
        background-position: center center;
        background-size: contain;
        height: 100vh;
    }
    h1 {
        text-align: center !important;
        font-size: 48px !important;
        margin-bottom: 20px !important;
        color: black !important;
    }
    .stTextInput>div, .stPasswordInput>div {
        max-width: 300px;
        margin: 0 auto 10px;
    }
    .stTextInput label, .stPasswordInput label {
        display: block !important;
        text-align: center !important;
        margin-bottom: 5px !important;
        color: black !important;
    }
    .stTextInput input, .stPasswordInput input {
        color: black !important;
    }
    .stButton>button {
        display: block !important;
        margin: 20px auto !important;
        background-color: #e47157 !important;
        color: white !important;
        border: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
       <div style='text-align: center; margin-top: 40px; margin-bottom: 10px;'>
         <p style='font-size: 40px; font-weight: 700; color: #000000; margin-bottom: 0;'>ForManager</p>
         <p style='font-size: 29px; font-weight: 700; color: #000000; margin-top: 4px;'>connexion</p>
       </div>
       """, unsafe_allow_html=True)

    email = st.text_input(t("Email","Email","Correo"), key="login_email")
    pwd = st.text_input(t("Mot de passe","Password","Contraseña"), type="password", key="login_password")

    if st.button(t("Se connecter","Log in","Iniciar sesión")):
        cur_users.execute("SELECT mot_de_passe FROM utilisateurs WHERE email=?", (email,))
        row = cur_users.fetchone()
        if row and row[0] == pwd:
            st.session_state.authenticated = True
            st.session_state.email = email
            st.success(t("Connexion réussie !","Login successful!","¡Inicio de sesión exitoso!"))
            time.sleep(1)
            st.rerun()
        else:
            st.error(t("Identifiants incorrects.","Incorrect credentials.","Credenciales incorrectas."))

# URL du logo
LOGO_URL = "https://start-up-bucket.s3.eu-west-3.amazonaws.com/wp-content/uploads/2025/04/15175542/OCP-Group-l-Start-Up-1-1-1-1-1-1-1-300x300.png"

# Fonction pour télécharger le logo temporairement
def download_logo(path="logo_temp.png"):
    try:
        r = requests.get(LOGO_URL, timeout=5)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    except Exception as e:
        print(f"Erreur téléchargement logo : {e}")
        return None

# Génération de certificat PDF avec logo
def creer_certificat(nom, formation, date_certif):
    logo_path = download_logo()

    pdf = FPDF()
    pdf.add_page()

    # Insérer le logo s'il a été téléchargé, centré horizontalement
    if logo_path and os.path.exists(logo_path):
        logo_w = 50  # largeur du logo en mm
        x_center = (pdf.w - logo_w) / 2
        y_logo = 18
        pdf.image(logo_path, x=x_center, y=y_logo, w=logo_w)
        os.remove(logo_path)

    # Titre principal
    pdf.set_font("Arial", "B", 26)
    pdf.set_text_color(44, 110, 73)
    pdf.ln(40)
    pdf.cell(
        0,
        18,
        t("CERTIFICAT DE FORMATION", "TRAINING CERTIFICATE", "CERTIFICADO DE FORMACIÓN"),
        ln=1,
        align="C"
    )
    pdf.ln(5)

    # Cadre autour de la page
    pdf.set_draw_color(44, 110, 73)
    pdf.set_line_width(1)
    pdf.rect(10, 30, 190, 240)

    # Texte central
    pdf.set_xy(20, 60)
    pdf.set_font("Arial", "", 14)
    pdf.set_text_color(0, 0, 0)

    # Notez l’utilisation de f''' pour pouvoir mettre librement des '
    texte = f'''
{t("Ce certificat est décerné à :",
   "This certificate is awarded to:",
   "Este certificado se otorga a:")}

{nom}

{t("Pour avoir suivi avec succès la formation :",
   "For successfully completing the training:",
   "Por haber completado con éxito la formación:")}

"{formation}"

{t("Délivré le :",
   "Issued on:",
   "Emitido el:")} {date_certif.strftime("%d/%m/%Y")}

{t("Ce certificat atteste de la participation active, de l'assiduité et de l'engagement",
   "This certificate certifies active participation, regular attendance, and commitment",
   "Este certificado certifica la participación activa, la asistencia regular y el compromiso")}

{t("dans le cadre d'un programme de développement professionnel.",
   "as part of a professional development program.",
   "como parte de un programa de desarrollo profesional.")}
'''

    pdf.multi_cell(0, 10, texte, align="C")

    # Signature RH
    pdf.set_xy(120, 220)
    pdf.set_font("Arial", "I", 12)
    pdf.cell(0, 10, t("Signature RH","HR Signature","Firma RRHH"), ln=1)
    pdf.set_xy(120, 230)
    pdf.set_font("Arial", "", 16)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, t("/ abdelkebir RH /","/ abdelkebir HR /","/ abdelkebir RRHH /"), ln=1)

    filename = f"Certificat_{nom.replace(' ', '_')}.pdf"
    pdf.output(filename)
    return filename

# --- Application principale ---
def main():
    st.markdown("""
        <style>
        [data-testid="stAppViewContainer"] .block-container {
            background: url("https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTzhCewsH_PSebAXk5yOFJF6wp_Qoq-Y_fHbg&s")
                        center/cover no-repeat fixed !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # on rétablit un peu d’espace vertical avant le bouton Logout
    st.markdown("<div style='height:30px;'></div>", unsafe_allow_html=True)

    # Bouton déconnexion
    if st.button(t("🔓 Se déconnecter","🔓 Log out","🔓 Cerrar sesión")):
        for k in ["authenticated","email","login_email","login_password"]:
            st.session_state[k] = False if k == "authenticated" else ""
        st.rerun()

    user_email = st.session_state.email

    # Récupérer le rôle dans la table utilisateurs
    cur_users.execute("SELECT fonction FROM utilisateurs WHERE email=?", (user_email,))
    row = cur_users.fetchone()
    if row is None:
        st.error("Utilisateur introuvable – déconnexion en cours.")
        st.session_state.authenticated = False
        st.rerun()
    role = row[0]
# Création des onglets
    if role == "Admin":
        tabs = st.tabs([
            t(" Gestion Formations"," Training Mgmt"," Gestión Formaciones"),
            t(" Gestion Employés"," Employee Mgmt"," Gestión Empleados"),
            t(" Chapitres"," Chapters"," Capítulos"),
            t(" Utilisateurs"," Users"," Usuarios"),
            t("⚙️Paramètres","⚙️Settings","⚙️Configuración"),
            t("📈dashbord"," 📈dashbord"," 📈dashbord"),
          
        ])
    else:
        tabs = st.tabs([
            t(" Parcourir Formation"," Browse Training"," Navegar Formación"),
            t(" Passer le test"," Take Test"," Realizar Prueba"),
            t(" Mes certificats"," My Certificates"," Mis Certificados"),
            t('⚙️ Paramètres','⚙️ Settings','⚙️ Ajustes'),
            t("📈 Mon Dashboard", "📈 My Dashboard","📈 Mi Panel")
        ])

    # ------------------------------------------------------------------------------------------------
    # 1️⃣ Admin / RH : Gestion Formations, Employés, Chapitres, Utilisateurs, Paramètres, Dashboard Admin
    # ------------------------------------------------------------------------------------------------
    if role in ["Admin"]:
        # --- 1) Gestion Formations ---
        with tabs[0]:
            st.markdown(
                f"<h1 style='text-align:center;font-size:28px; margin:0px;padding:0px'>{t('📘 Gestion des formations','📘 Training Management','📘 Gestión de Formaciones')}</h1>",
                unsafe_allow_html=True
            )
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(
                    f"<h2 style='text-align:center;font-size:18px; margin:0px 0;'>{t('➕ Ajouter une formation','➕ Add Training','➕ Agregar Formación')}</h2>",
                    unsafe_allow_html=True
                )
                titre = st.text_input(t("Titre","Title","Título"), key="add_titre")
                date_f = st.date_input(t("Date","Date","Fecha"), value=date.today(), key="add_date")
                duree = st.number_input(
                    t("Durée (h)","Duration (h)","Duración (h)"),
                    min_value=1, step=1, key="add_duree"
                )
                formateur = st.text_input(t("Formateur","Trainer","Formador"), key="add_formateur")
                if st.button(t("Ajouter","Add","Agregar"), key="add_form_btn"):
                    if titre and formateur:
                        cur_form.execute(
                            "INSERT INTO formations(titre,date,duree,formateur) VALUES(?,?,?,?)",
                            (titre, date_f.strftime("%Y-%m-%d"), duree, formateur)
                        )
                        conn_form.commit()
                        st.success(t("Formation ajoutée ✅","Training added ✅","Formación agregada ✅"))
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning(t("Veuillez remplir tous les champs.","Please fill all fields.","Por favor complete todos los campos."))
            with col2:
                st.markdown(
                    f"<h2 style='text-align:center;font-size:18px; margin:0px 0;'>{t('🛠 Modifier / Supprimer','🛠 Edit / Delete','🛠 Editar / Eliminar')}</h2>",
                    unsafe_allow_html=True
                )
                cur_form.execute("SELECT id, titre, date, duree, formateur FROM formations ORDER BY date DESC")
                data = cur_form.fetchall()
                if data:
                    choix = [f"{row[1]} — {row[2]}" for row in data]
                    sel = st.selectbox(t("Sélection formation","Select Training","Seleccione Formación"), choix, key="mod_form_select")
                    idx = choix.index(sel)
                    fid, old_t, old_d, old_du, old_fr = data[idx]
                    new_t = st.text_input(t("Titre","Title","Título"), old_t, key="mod_titre")
                    new_d = st.date_input(
                        t("Date","Date","Fecha"), value=datetime.fromisoformat(old_d), key="mod_date"
                    )
                    new_du = st.number_input(
                        t("Durée (h)","Duration (h)","Duración (h)"),
                        value=old_du, min_value=1, step=1, key="mod_duree"
                    )
                    new_fr = st.text_input(t("Formateur","Trainer","Formador"), old_fr, key="mod_formateur")
                    c_mod, c_del = st.columns(2)
                    with c_mod:
                        if st.button(t("Modifier","Edit","Editar"), key="mod_form_btn"):
                            cur_form.execute(
                                "UPDATE formations SET titre=?, date=?, duree=?, formateur=? WHERE id=?",
                                (new_t, new_d.strftime("%Y-%m-%d"), new_du, new_fr, fid)
                            )
                            conn_form.commit()
                            # Réinitialiser les progressions et tests pour cette formation
                            cur_prog.execute("DELETE FROM progress WHERE formation_id=?", (fid,))
                            conn_prog.commit()
                            cur_test.execute("DELETE FROM tests WHERE formation_id=?", (fid,))
                            conn_test.commit()
                            st.success(t("Formation modifiée ✏️ — indicateurs réinitialisés","Training updated ✏️ — metrics reset","Formación actualizada ✏️ — indicadores reiniciados"))
                            time.sleep(1)
                            st.rerun()
                    with c_del:
                        if st.button(t("Supprimer","Delete","Eliminar"), key="del_form_btn"):
                            cur_form.execute("DELETE FROM formations WHERE id=?", (fid,))
                            conn_form.commit()
                            # Supprimer chapitres, progressions et tests associés
                            cur_form.execute("DELETE FROM chapitres WHERE formation_id=?", (fid,))
                            conn_form.commit()
                            cur_prog.execute("DELETE FROM progress WHERE formation_id=?", (fid,))
                            conn_prog.commit()
                            cur_test.execute("DELETE FROM tests WHERE formation_id=?", (fid,))
                            conn_test.commit()
                            st.warning(t("Formation supprimée 🗑️ — indicateurs supprimés","Training deleted 🗑️ — metrics removed","Formación eliminada 🗑️ — indicadores eliminados"))
                            time.sleep(1)
                            st.rerun()
                else:
                    st.info(t("Aucune formation disponible.","No training available.","No hay formación disponible."))
            st.subheader(t("📋 Liste des formations","📋 Training List","📋 Lista de Formación"))
            cur_form.execute("SELECT titre, date, duree, formateur FROM formations ORDER BY date DESC")
            df_forms = pd.DataFrame(
                cur_form.fetchall(),
                columns=[
                    t("Titre","Title","Título"),
                    t("Date","Date","Fecha"),
                    t("Durée (h)","Duration (h)","Duración (h)"),
                    t("Formateur","Trainer","Formador")
                ]
            )
            if not df_forms.empty:
                st.dataframe(df_forms, use_container_width=True, hide_index=True)
            else:
                st.info(t("Aucune formation enregistrée.","No trainings recorded.","No hay formaciones registradas."))

        # --- 2) Gestion Employés ---
        with tabs[1]:
            st.markdown(
                f"<h1 style='text-align:center;font-size:28px; margin:0px;padding:0px'>{t('👥 Gestion des employés','👥 Employee Management','👥 Gestión de Empleados')}</h1>",
                unsafe_allow_html=True
            )
            col1, col2 = st.columns(2)
            with col1:
                st.subheader(t("➕ Ajouter un employé","➕ Add Employee","➕ Agregar Empleado"))
                nom = st.text_input(t("Nom","Last Name","Apellido"), key="add_nom")
                prenom = st.text_input(t("Prénom","First Name","Nombre"), key="add_prenom")
                func_disp = st.selectbox(t("Fonction","Role","Rol"), list(fonctions_ocp.keys()), key="add_fonct")
                func_val = fonctions_ocp[func_disp]
                if st.button(t("Ajouter","Add","Agregar"), key="add_emp_btn"):
                    if nom and prenom:
                        cur_emp.execute("INSERT INTO employes(nom,prenom,fonction) VALUES(?,?,?)", (nom, prenom, func_val))
                        conn_emp.commit()
                        st.success(t("Employé ajouté ✅","Employee added ✅","Empleado agregado ✅"))
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning(t("Veuillez remplir tous les champs.","Please fill all fields.","Por favor complete todos los campos."))
            with col2:
                st.subheader(t("🛠 Modifier / Supprimer","🛠 Edit / Delete","🛠 Editar / Eliminar"))
                cur_emp.execute("SELECT id, nom, prenom, fonction FROM employes ORDER BY nom")
                emp_data = cur_emp.fetchall()
                if emp_data:
                    opts = [f"{e[1]} {e[2]} — {e[3].replace('_',' ').title()}" for e in emp_data]
                    sel2 = st.selectbox(t("Sélection employé","Select Employee","Seleccione Empleado"), opts, key="mod_emp_select")
                    i2 = opts.index(sel2)
                    eid, old_n, old_p, old_f = emp_data[i2]
                    n_n = st.text_input(t("Nom","Last Name","Apellido"), old_n, key="mod_nom_emp")
                    n_p = st.text_input(t("Prénom","First Name","Nombre"), old_p, key="mod_prenom_emp")
                    n_f_disp = st.selectbox(
                        t("Fonction","Role","Rol"),
                        list(fonctions_ocp.keys()),
                        index=list(fonctions_ocp.values()).index(old_f),
                        key="mod_fonct_emp"
                    )
                    n_f = fonctions_ocp[n_f_disp]
                    c_mod2, c_del2 = st.columns(2)
                    with c_mod2:
                        if st.button(t("Modifier","Edit","Editar"), key="mod_emp_btn"):
                            cur_emp.execute("UPDATE employes SET nom=?, prenom=?, fonction=? WHERE id=?", (n_n, n_p, n_f, eid))
                            conn_emp.commit()
                            st.success(t("Employé modifié ✏️","Employee updated ✏️","Empleado actualizado ✏️"))
                            time.sleep(1)
                            st.rerun()
                    with c_del2:
                        if st.button(t("Supprimer","Delete","Eliminar"), key="del_emp_btn"):
                            cur_emp.execute("DELETE FROM employes WHERE id=?", (eid,))
                            conn_emp.commit()
                            st.warning(t("Employé supprimé 🗑️","Employee deleted 🗑️","Empleado eliminado 🗑️"))
                            time.sleep(1)
                            st.rerun()
                else:
                    st.info(t("Aucun employé enregistré.","No employees recorded.","No hay empleados registrados."))
            st.subheader(t("📋 Liste des employés","📋 Employee List","📋 Lista de Empleados"))
            cur_emp.execute("SELECT nom, prenom, fonction FROM employes ORDER BY nom")
            df_emp = pd.DataFrame(
                cur_emp.fetchall(),
                columns=[t("Nom","Last Name","Apellido"), t("Prénom","First Name","Nombre"), t("Fonction","Role","Rol")]
            )
            df_emp[t("Fonction","Role","Rol")] = df_emp[t("Fonction","Role","Rol")].apply(lambda x: x.replace("_"," ").title())
            if not df_emp.empty:
                st.dataframe(df_emp, use_container_width=True, hide_index=True)
            else:
                st.info(t("Aucun employé enregistré.","No employees recorded.","No hay empleados registrados."))

        # --- 3) Administration des chapitres et tests ---
        with tabs[2]:
            st.markdown(
                f"<h1 style='text-align:center;font-size:28px; margin:0px;padding:0px'>{t('🛠 Administration des chapitres','🛠 Chapters Administration','🛠 Administración Capítulos')}</h1>",
                unsafe_allow_html=True
            )
            mode = st.radio(
                t("Action à effectuer","Action","Acción"),
                [t("Ajouter Chapitre","Add Chapter","Agregar Capítulo"),
                 t("Ajouter Test","Add Test","Agregar Prueba")]
            )

            if mode == t("Ajouter Chapitre","Add Chapter","Agregar Capítulo"):
                fms2 = cur_form.execute("SELECT id, titre FROM formations ORDER BY date DESC").fetchall()
                if not fms2:
                    st.info(t(
                        "Créez d'abord une formation avant d'ajouter un chapitre.",
                        "Please create a training first.",
                        "Por favor cree una formación primero."
                    ))
                else:
                    mapping_formations = {titre: fid for fid, titre in fms2}
                    sel2 = st.selectbox(
                        t("Formation à gérer","Select Training","Selección Formación"),
                        list(mapping_formations.keys()),
                        key="admin_f2"
                    )
                    fid2 = mapping_formations[sel2]
                    st.subheader(t("➕ Ajouter un chapitre","➕ Add Chapter","➕ Agregar Capítulo"))
                    ch_title = st.text_input(t("Titre","Title","Título"), key="add2_ch_title")
                    ch_order = st.number_input(
                        t("Ordre","Order","Orden"), min_value=1, step=1, key="add2_ch_order"
                    )
                    ch_type = st.selectbox(
                        t("Type de contenu","Content Type","Tipo de contenido"),
                        ["texte","pdf","video","ppt"],
                        key="add2_ch_type"
                    )
                    ch_content = None
                    if ch_type == "texte":
                        ch_content = st.text_area(t("Contenu texte","Text content","Contenido de texto"), key="add2_ch_content")
                    else:
                        up = st.file_uploader(
                            t("Fichier","File","Archivo"),
                            type={"pdf":["pdf"], "video":["mp4"], "ppt":["ppt","pptx"]}[ch_type],
                            key="add2_ch_file"
                        )
                        if up:
                            os.makedirs("uploads", exist_ok=True)
                            path = os.path.join("uploads", up.name)
                            with open(path, "wb") as f:
                                f.write(up.read())
                            ch_content = path
                    if st.button(t("Ajouter","Add","Agregar"), key="add2_ch_btn"):
                        if ch_title and ch_content:
                            cur_form.execute(
                                "SELECT COUNT(*) FROM chapitres WHERE formation_id=? AND titre=?",
                                (fid2, ch_title)
                            )
                            if cur_form.fetchone()[0] > 0:
                                st.error(t("Chapitre déjà existant.","Chapter already exists.","Capítulo ya existe."))
                            else:
                                cur_form.execute(
                                    "INSERT INTO chapitres(formation_id,titre,type_contenu,contenu,ordre) VALUES(?,?,?,?,?)",
                                    (fid2, ch_title, ch_type, ch_content, ch_order)
                                )
                                conn_form.commit()
                                # À chaque ajout de chapitre, on réinitialise indicateurs de cette formation
                                cur_prog.execute("DELETE FROM progress WHERE formation_id=?", (fid2,))
                                conn_prog.commit()
                                cur_test.execute("DELETE FROM tests WHERE formation_id=?", (fid2,))
                                conn_test.commit()
                                st.success(t("Chapitre ajouté ✅ — indicateurs réinitialisés","Chapter added ✅ — metrics reset","Capítulo agregado ✅ — indicadores reiniciados"))
                                time.sleep(1)
                                st.rerun()
                        else:
                            st.warning(t("Remplissez tous les champs.","Fill all fields.","Complete todos los campos."))
                    st.markdown("---")
                    st.subheader(t("✏️ Modifier / 🗑️ Supprimer un chapitre","✏️ Edit / 🗑️ Delete Chapter","✏️ Editar / 🗑️ Eliminar Capítulo"))
                    cur_form.execute("SELECT id, titre, type_contenu, contenu, ordre FROM chapitres WHERE formation_id=? ORDER BY ordre", (fid2,))
                    chap_list = cur_form.fetchall()
                    if chap_list:
                        opts = [f"{ordr} – {tit}" for (_, tit, _, _, ordr) in chap_list]
                        sel3 = st.selectbox(t("Chapitre","Chapter","Capítulo"), opts, key="mod2_ch_select")
                        cid3, old_t3, old_type3, old_cont3, old_ord3 = chap_list[opts.index(sel3)]
                        new_t3 = st.text_input(t("Titre","Title","Título"), old_cont3, key="mod2_ch_title")
                        new_ord3 = st.number_input(
                            t("Ordre","Order","Orden"), min_value=1, value=old_ord3, step=1, key="mod2_ch_order"
                        )
                        new_type3 = st.selectbox(
                            t("Type de contenu","Content Type","Tipo de contenido"),
                            ["texte","pdf","video","ppt"],
                            index=["texte","pdf","video","ppt"].index(old_type3),
                            key="mod2_ch_type"
                        )
                        if new_type3 == "texte":
                            new_cont3 = st.text_area(t("Contenu texte","Text content","Contenido de texto"), old_cont3, key="mod2_ch_content")
                        else:
                            nf = st.file_uploader(
                                t("Fichier","File","Archivo"),
                                type={"pdf":["pdf"], "video":["mp4"], "ppt":["ppt","pptx"]}[new_type3],
                                key="mod2_ch_file"
                            )
                            if nf:
                                os.makedirs("uploads", exist_ok=True)
                                np = os.path.join("uploads", nf.name)
                                with open(np, "wb") as f:
                                    f.write(nf.read())
                                new_cont3 = np
                            else:
                                new_cont3 = old_cont3
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button(t("Modifier","Edit","Editar"), key="mod2_ch_btn"):
                                cur_form.execute(
                                    "UPDATE chapitres SET titre=?, type_contenu=?, contenu=?, ordre=? WHERE id=?",
                                    (new_t3, new_type3, new_cont3, new_ord3, cid3)
                                )
                                conn_form.commit()
                                # À chaque modification de chapitre, on réinitialise indicateurs de cette formation
                                cur_prog.execute("DELETE FROM progress WHERE formation_id=?", (fid2,))
                                conn_prog.commit()
                                cur_test.execute("DELETE FROM tests WHERE formation_id=?", (fid2,))
                                conn_test.commit()
                                st.success(t("Chapitre modifié ✅ — indicateurs réinitialisés","Chapter updated ✅ — metrics reset","Capítulo actualizado ✅ — indicadores reiniciados"))
                                time.sleep(1)
                                st.rerun()
                        with c2:
                            if st.button(t("Supprimer","Delete","Eliminar"), key="del2_ch_btn"):
                                cur_form.execute("DELETE FROM chapitres WHERE id=?", (cid3,))
                                conn_form.commit()
                                # À chaque suppression de chapitre, on réinitialise indicateurs de cette formation
                                cur_prog.execute("DELETE FROM progress WHERE formation_id=?", (fid2,))
                                conn_prog.commit()
                                cur_test.execute("DELETE FROM tests WHERE formation_id=?", (fid2,))
                                conn_test.commit()
                                st.warning(t("Chapitre supprimé 🗑️ — indicateurs réinitialisés","Chapter deleted 🗑️ — metrics reset","Capítulo eliminado 🗑️ — indicadores reiniciados"))
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.info(t("Aucun chapitre à modifier.","No chapter to modify.","Ningún capítulo para modificar."))
            else:
                # --- Ajouter une question de test ---
                st.subheader(t("➕ Ajouter une question de test","➕ Add Test Question","➕ Agregar Pregunta de Prueba"))
                fms = cur_form.execute("SELECT id, titre FROM formations").fetchall()
                if not fms:
                    st.info(t("Créez d'abord une formation.","Please create a training first.","Por favor cree una formación primero."))
                else:
                    mapping = {titre: fid for fid, titre in fms}
                    sel = st.selectbox(t("Formation pour le test","Training for test","Formación para prueba"), list(mapping.keys()), key="test_form")
                    fid_test = mapping[sel]
                    q_text = st.text_input(t("Question","Question","Pregunta"), key="q_text")
                    allow_multi = st.checkbox(t("Choix multiples","Multiple choice","Selección múltiple"), key="q_multi")
                    num_opts = st.number_input(
                        t("Nb options","# options","# opciones"),
                        min_value=2, max_value=6, value=4, step=1, key="q_num_opts"
                    )
                    opts = []
                    corrs = []
                    for i in range(num_opts):
                        t_opt = st.text_input(f"{t('Option','Option','Opción')} {i+1}", key=f"opt_txt_{i}")
                        c_opt = st.checkbox(t("Correct?","Correct?","¿Correcta?"), key=f"opt_corr_{i}")
                        opts.append(t_opt)
                        corrs.append(c_opt)
                    if st.button(t("Ajouter","Add","Agregar"), key="add_q_btn"):
                        cur_test.execute(
                            "INSERT INTO questions(formation_id, question_text, allow_multiple) VALUES(?,?,?)",
                            (fid_test, q_text, int(allow_multi))
                        )
                        qid = cur_test.lastrowid
                        for t_opt, c_opt in zip(opts, corrs):
                            cur_test.execute(
                                "INSERT INTO options(question_id, option_text, is_correct) VALUES(?,?,?)",
                                (qid, t_opt, int(c_opt))
                            )
                        conn_test.commit()
                        st.success(t("Question ajoutée ✅","Question added ✅","Pregunta agregada ✅"))

        # --- 4) Gestion Utilisateur ---
        with tabs[3]:
            st.markdown(
                f"<h1 style='text-align:center;font-size:28px; margin:0px;padding:0px'>{t('👤 Gestion Utilisateur','👤 User Management','👤 Gestión Usuarios')}</h1>",
                unsafe_allow_html=True
            )
            col1, col2 = st.columns([2.2, 1.3])
            with col1:
                nom = st.text_input(t("Nom","Last Name","Apellido"), "")
                prenom = st.text_input(t("Prénom","First Name","Nombre"), "")
                email_input = st.text_input("Email")
                mot_de_passe = st.text_input(t("Mot de passe","Password","Contraseña"), type="password")
                role_input = st.selectbox(
                    t("Rôle","Role","Rol"),
                    [t("Admin","Admin","Admin"), t("Employé","User","Usuario")]
                )
                photo = st.file_uploader(t("Changer la photo de profil","Change profile photo","Cambiar foto de perfil"), type=["png","jpg","jpeg"])
            with col2:
                genre = st.selectbox(
                    t("Genre","Gender","Género"),
                    [t("Homme","Male","Hombre"), t("Femme","Female","Mujer")],
                    key="update_genre"
                )
                if photo:
                    st.image(photo, width=200)
                else:
                    img_url = (
                        "https://img.freepik.com/vecteurs-libre/illustration-homme-affaires_53876-5856.jpg?w=740"
                        if genre == t("Homme","Male","Hombre")
                        else "https://img.freepik.com/vecteurs-libre/illustration-femme-affaires_53876-5857.jpg?w=740"
                    )
                    st.image(img_url, width=200)

                if st.button(t("Mettre à jour","Update","Actualizar"), key="update_user"):
                    if not all([
                        nom.strip(),
                        prenom.strip(),
                        email_input.strip(),
                        mot_de_passe.strip(),
                        role_input.strip(),
                        genre.strip()
                    ]) or photo is None:
                        st.warning(t(
                            "Veuillez remplir tous les champs et ajouter une photo.",
                            "Please fill all fields and upload a photo.",
                            "Por favor complete todos los campos y suba una foto."
                        ))
                    else:
                        photo_path = None
                        if photo:
                            os.makedirs("user_photos", exist_ok=True)
                            path = os.path.join("user_photos", photo.name)
                            with open(path, "wb") as f:
                                f.write(photo.read())
                            photo_path = path

                        cur_users.execute("""
                            INSERT INTO utilisateurs(
                                email, mot_de_passe, nom, prenom, fonction, genre, photo_path
                            ) VALUES(?,?,?,?,?,?,?)
                            ON CONFLICT(email) DO UPDATE SET
                                mot_de_passe=excluded.mot_de_passe,
                                nom=excluded.nom,
                                prenom=excluded.prenom,
                                fonction=excluded.fonction,
                                genre=excluded.genre,
                                photo_path=excluded.photo_path
                        """, (
                            email_input,
                            mot_de_passe,
                            nom,
                            prenom,
                            role_input,
                            genre,
                            photo_path
                        ))
                        conn_users.commit()
                        st.success(t("Profil mis à jour ✅","Profile updated ✅","Perfil actualizado ✅"))
                        st.rerun()

            # — Tableau & suppression —
            cur_users.execute(
                "SELECT email, nom, prenom, fonction, genre, mot_de_passe, photo_path FROM utilisateurs ORDER BY email"
            )
            df_users = pd.DataFrame(
                cur_users.fetchall(),
                columns=[
                            "Email",
                            t("Nom","Last Name","Apellido"),
                            t("Prénom","First Name","Nombre"),
                            t("Fonction","Role","Rol"),
                            t("Genre","Gender","Género"),
                            t("Mot de passe","Password","Contraseña"),
                            "Photo"
                        ]
                                    )

            if not df_users.empty:
                st.subheader(t("📋 Liste des utilisateurs","📋 User List","📋 Lista Usuarios"))
                col_table, col_delete = st.columns([3, 1])
                with col_table:
                    st.dataframe(df_users, use_container_width=True, hide_index=True)
                with col_delete:
                    st.subheader(t(" Supprimer un utilisateur"," Delete a user"," Eliminar usuario"))
                    email_to_delete = st.selectbox(
                        t("Sélectionnez un utilisateur","Select a user","Seleccione usuario"),
                        df_users["Email"].tolist(),
                        key="del_user_select"
                    )
                    if st.button(t("Supprimer","Delete","Eliminar"), key="del_user_btn"):
                        cur_users.execute(
                            "DELETE FROM utilisateurs WHERE email=?",
                            (email_to_delete,)
                        )
                        conn_users.commit()
                        st.success(t(
                            f"Utilisateur {email_to_delete} supprimé ✅",
                            f"User {email_to_delete} deleted ✅",
                            f"Usuario {email_to_delete} eliminado ✅"
                        ))
                        st.rerun()
            else:
                st.info(t("Aucun utilisateur enregistré.","No users recorded.","No hay usuarios registrados."))

        # --- 5) Paramètres ---
        with tabs[4]:
            with st.sidebar:
                st.title(t("⚙️ Réglages","⚙️ Settings","⚙️ Ajustes"))
                st.markdown("### " + t("Profil","Profile","Perfil"))
                st.text_input(t("Utilisateur","User","Usuario"), value=user_email, disabled=True)
                st.markdown("### " + t("Sécurité & vie privée","Security & Privacy","Seguridad & Privacidad"))
                ancien = st.text_input(t("Ancien mot de passe","Old password","Contraseña antigua"), type="password", key="old_pwd")
                nouveau = st.text_input(t("Nouveau mot de passe","New password","Contraseña nueva"), type="password", key="new_pwd")
                lang = st.selectbox(
                    t("Langue","Language","Idioma"),
                    ["Français","English","Español"],
                    index=["Français","English","Español"].index(st.session_state.lang)
                )
                search = st.text_input(t("🔍 Recherche","🔍 Search","🔍 Buscar"), key="search_param")
                if search.strip():
                    q = search.lower()
                    if "formation" in q:
                        st.info(t("Onglet Parcourir Formation","Browse Training tab","Pestaña Navegar Formación"))
                    elif "test" in q:
                        st.info(t("Onglet Passer le test","Take Test tab","Pestaña Realizar Prueba"))
                    elif "certif" in q:
                        st.info(t("Onglet Mes certificats","My Certificates tab","Pestaña Mis Certificados"))
                    else:
                        st.warning(t("Aucun résultat.","No result.","Ningún resultado."))

                st.markdown("### " + t("Notifications","Notifications","Notificaciones"))
                notif_form = st.checkbox(t("Formations","Trainings","Formaciones"), value=(get_param("notif_form","True")=="True"))
                notif_test = st.checkbox(t("Tests","Tests","Pruebas"), value=(get_param("notif_test","True")=="True"))
                notif_cert = st.checkbox(t("Certificats","Certificates","Certificados"), value=(get_param("notif_cert","True")=="True"))

                if st.button(t("💾 Sauvegarder","💾 Save","💾 Guardar")):
                    if ancien and nouveau:
                        cur_users.execute("SELECT mot_de_passe FROM utilisateurs WHERE email=?", (user_email,))
                        if cur_users.fetchone()[0] == ancien:
                            cur_users.execute("UPDATE utilisateurs SET mot_de_passe=? WHERE email=?", (nouveau, user_email))
                            conn_users.commit()
                            st.success(t("Mot de passe mis à jour.","Password updated.","Contraseña actualizada."))
                        else:
                            st.error(t("Ancien mot de passe incorrect.","Old password incorrect.","Contraseña antigua incorrecta."))
                    save_param("notif_form", notif_form)
                    save_param("notif_test", notif_test)
                    save_param("notif_cert", notif_cert)
                    if lang != st.session_state.lang:
                        st.session_state.lang = lang
                        save_param("lang", lang)
                    st.success(t("Paramètres sauvegardés!","Settings saved!","¡Ajustes guardados!"))

            st.title(t("❔ À propos & Aide","❔ About & Help","❔ Acerca & Ayuda"))
            st.subheader(t("Version & Changelog","Version & Changelog","Versión & Cambios"))
            st.write(f"- {t('Version','Version','Versión')}: 1.3.2")
            st.write(f"- {t('Build','Build','Compilación')}: {datetime.now().strftime('%Y-%m-%d')}")
            st.subheader(t("FAQ & Support","FAQ & Support","FAQ & Soporte"))
            st.write(t("Q: Comment créer un compte ?","Q: How to create an account?","P: ¿Cómo crear una cuenta?"))
            st.write(t("R: Dans l’onglet “👤 Utilisateurs”","A: In the “👤 Users” tab","R: En la pestaña “👤 Usuarios”"))
            st.markdown("---")
            st.write("©️ 2025 OCP Group — " + t("Tous droits réservés.","All rights reserved.","Todos los derechos reservados."))
        # --- 6) Tableau de bord Admin ---
        with tabs[5]:
            st.markdown("""
            <style>
            #dashboard { padding: 0px; }
            #dashboard .kpi-card {
                background: white;
                border-radius: 8px;
                padding: 0px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                margin: 0px;
                text-align: center !important;
            }
            #dashboard .kpi-title { font-size: 14px; color: #555; margin-bottom:4px;text-align: center !important; }
            #dashboard .kpi-value { font-size: 24px; font-weight: bold; color: #2E4053; margin-bottom:8px; }
            #dashboard .chart-title {
                font-size: 16px;
                font-weight: 600;
                text-align: center !important;
                margin-top: 0px;
                margin-bottom: 0px;
            }
            </style>
            <div id="dashboard">
            """, unsafe_allow_html=True)

            st.markdown(
                f"<h1 style='text-align:center;font-size:28px;margin:0px;padding:0px'>{t('📊 Tableau de bord','📊 Dashboard','📊 Tablero')}</h1>",
                unsafe_allow_html=True
            )

            # KPI calculations
            total_form = cur_form.execute("SELECT COUNT(*) FROM formations").fetchone()[0]
            total_emp = cur_emp.execute("SELECT COUNT(*) FROM employes").fetchone()[0]
            total_usr = cur_users.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0]
            total_chap = cur_form.execute("SELECT COUNT(*) FROM chapitres").fetchone()[0]
            total_prog = cur_prog.execute("SELECT COUNT(*) FROM progress").fetchone()[0]
            total_tests = cur_test.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
            passed_tests = cur_test.execute("SELECT COUNT(*) FROM tests WHERE passed=1").fetchone()[0]
            failed_tests = total_tests - passed_tests
            global_rate = int(passed_tests / total_tests * 100) if total_tests > 0 else 0
            active_emp = cur_prog.execute("SELECT COUNT(DISTINCT email) FROM progress").fetchone()[0]
            active_rate = int(active_emp / total_emp * 100) if total_emp > 0 else 0
            passed_emp = cur_test.execute("SELECT COUNT(DISTINCT email) FROM tests WHERE passed=1").fetchone()[0]
            passed_emp_rate = int(passed_emp / total_emp * 100) if total_emp > 0 else 0

            df_monthly = pd.DataFrame(
                cur_form.execute(
                    "SELECT substr(date,1,7) AS mois, COUNT(*) AS n FROM formations GROUP BY mois ORDER BY mois"
                ).fetchall(),
                columns=["mois","n"]
            )
            df_by_role = pd.DataFrame(
                cur_emp.execute("SELECT fonction, COUNT(*) AS n FROM employes GROUP BY fonction").fetchall(),
                columns=["fonction","n"]
            )
            df_test_rate = pd.DataFrame([
                { "cat": t("Passés","Passed","Aprobados"), "n": passed_tests },
                { "cat": t("Échoués","Failed","Fallidos"), "n": failed_tests }
            ])
            df_active = pd.DataFrame([
                { "cat": t("Actifs","Active","Activos"), "n": active_emp },
                { "cat": t("Inactifs","Inactive","Inactivos"), "n": total_emp - active_emp }
            ])
            df_passed_emp = pd.DataFrame([
                { "cat": t("Ont testé","Tested","Probados"), "n": passed_emp },
                { "cat": t("Non testés","Untested","Sin probar"), "n": total_emp - passed_emp }
            ])

            items = [
                {
                    "title": t("Formations","Trainings","Formaciones"),
                    "value": total_form,
                    "chart_title": t("Formations mensuelles","Monthly trainings","Form mens."),
                    "chart": alt.Chart(df_monthly)
                                .mark_line(color="#2E4053", interpolate="monotone", strokeWidth=3)
                                .encode(x="mois:T", y="n:Q")
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Employés","Employees","Empleados"),
                    "value": total_emp,
                    "chart_title": t("Employés par rôle","Employees by role","Empleados por rol"),
                    "chart": alt.Chart(df_by_role)
                                .mark_bar(color="#2E4053", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                                .encode(x=alt.X("fonction:N", sort="-y"), y="n:Q")
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Utilisateurs","Users","Usuarios"),
                    "value": total_usr,
                    "chart_title": t("Évolution utilisateurs","User growth","Crecimiento usr"),
                    "chart": alt.Chart(df_monthly)
                                .mark_area(opacity=0.3, color="#2E4053")
                                .encode(x="mois:T", y="n:Q")
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Chapitres","Chapters","Capítulos"),
                    "value": total_chap,
                    "chart_title": t("Chapitres par rôle","Chapters by role","Capítulos por rol"),
                    "chart": alt.Chart(df_by_role)
                                .mark_circle(size=100, color="#2E4053")
                                .encode(x="fonction:N", y="n:Q")
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Progressions","Progress","Progresos"),
                    "value": total_prog,
                    "chart_title": t("Progression mensuelle","Monthly progress","Progreso mens."),
                    "chart": alt.Chart(df_monthly)
                                .mark_bar(opacity=0.5, color="#2E4053")
                                .encode(x="mois:T", y="n:Q")
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Succès tests","Test success","Éxito pruebas"),
                    "value": f"{global_rate}%",
                    "chart_title": t("Réussite vs échec","Success vs Failure","Éxito vs Falla"),
                    "chart": alt.Chart(df_test_rate)
                                .mark_arc(innerRadius=50, outerRadius=100)
                                .encode(theta="n:Q", color=alt.Color("cat:N", legend=None))
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Taux actifs","Active rate","Tasa activos"),
                    "value": f"{active_rate}%",
                    "chart_title": t("Actifs vs inactifs","Active vs Inactive","Activos vs Inact."),
                    "chart": alt.Chart(df_active)
                                .mark_arc(innerRadius=50, outerRadius=100)
                                .encode(theta="n:Q", color=alt.Color("cat:N", legend=None))
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Taux test-passed","Passed rate","Tasa aprobados"),
                    "value": f"{passed_emp_rate}%",
                    "chart_title": t("Passé vs non-passé","Passed vs Untested","Aprob. vs Sin"),
                    "chart": alt.Chart(df_passed_emp)
                                .mark_arc(innerRadius=50, outerRadius=100)
                                .encode(theta="n:Q", color=alt.Color("cat:N", legend=None))
                                .properties(width=250, height=250)
                },
                {
                    "title": t("Tests totaux","Total tests","Total pruebas"),
                    "value": total_tests,
                    "chart_title": t("Répartition tests","Test breakdown","Desglose pruebas"),
                    "chart": alt.Chart(df_test_rate)
                                .mark_bar(color="#2E4053")
                                .encode(x=alt.X("cat:N", title=None), y="n:Q")
                                .properties(width=250, height=250)
                },
            ]

            for i in range(0, 9, 3):
                cols = st.columns(3, gap="large")
                for item, col in zip(items[i:i+3], cols):
                    with col:
                        st.markdown(f"""
                        <div class="kpi-card">
                            <div class="kpi-title">{item['title']}</div>
                            <div class="kpi-value">{item['value']}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        st.markdown(f"<div class='chart-title'>{item['chart_title']}</div>", unsafe_allow_html=True)
                        st.altair_chart(item["chart"], use_container_width=False)

            st.markdown("</div>", unsafe_allow_html=True)

    # ------------------------------------------------------------------------------------------------
    # 2️⃣ Utilisateur standard : Parcourir Formation, Passer le test, Mes certificats, Paramètres, Dashboard
    # ------------------------------------------------------------------------------------------------
    else:
        # --- Parcourir Formation ---
        with tabs[0]:
            st.header(t("🎓 Parcourir Formation","🎓 Browse Training","🎓 Navegar Formación"))

            # Récupérer toutes les formations
            cur_form.execute("SELECT id, titre FROM formations ORDER BY date DESC")
            forms = cur_form.fetchall()
            if not forms:
                st.info(t("Aucune formation disponible.","No training available.","No hay formación disponible."))
                st.stop()

            choix = [titre for (_fid, titre) in forms]
            sel = st.selectbox(t("Choisissez une formation","Select a training","Seleccione una formación"), choix, key="view_form")
            fid = [fid for (fid, titre) in forms if titre == sel][0]

            # Charger les chapitres pour cette formation
            cur_form.execute(
                "SELECT id, titre, type_contenu, contenu FROM chapitres WHERE formation_id = ? ORDER BY ordre",
                (fid,)
            )
            chs = cur_form.fetchall()
            total = len(chs)

            if total == 0:
                st.info(t("Pas de chapitres disponibles.","No chapters available.","No hay capítulos disponibles."))
                st.stop()

            # Initialisation / réinitialisation de l’index de chapitre si changement de formation
            if "last_fid" not in st.session_state or st.session_state.get("last_fid") != fid:
                st.session_state.ch_idx = 0
                st.session_state.last_fid = fid

            idx = st.session_state.get("ch_idx", 0)
            if idx < 0:
                idx = 0
                st.session_state.ch_idx = 0
            if idx >= total:
                idx = total - 1
                st.session_state.ch_idx = idx

            # Récupérer le chapitre courant
            cid, titre_chap, type_c, cont = chs[idx]

            # Marquer le chapitre comme lu (INSERT OR IGNORE)
            cur_prog.execute(
                "INSERT OR IGNORE INTO progress(email, formation_id, chapter_id, timestamp) VALUES(?,?,?,?)",
                (user_email, fid, cid, datetime.now().isoformat())
            )
            conn_prog.commit()

            # Si on est sur le dernier chapitre → message de fin
            if idx == total - 1:
                st.success(
                    t(
                        "🎉 Vous avez terminé la formation ! Vous pouvez passer le test.",
                        "🎉 You have finished the training! You can now take the test.",
                        "🎉 ¡Has terminado la formación! Ahora puedes realizar la prueba."
                    )
                )
            else:
                # Affichage du “stepper” (= petit cercle coloré) pour chaque chapitre
                cols = st.columns(total)
                for i in range(total):
                    if i < idx:
                        couleur = "#2c6e49"  # vert = chapitres déjà lus
                    elif i == idx:
                        couleur = "#e47157"  # orange = chapitre courant
                    else:
                        couleur = "#cfcfcf"  # gris = chapitres non lus
                    cols[i].markdown(
                        f"""
                        <div style="
                            width:36px;
                            height:36px;
                            border-radius:50%;
                            background-color:{couleur};
                            display:flex;
                            align-items:center;
                            justify-content:center;
                            color:white;
                        ">{i+1}</div>
                        """,
                        unsafe_allow_html=True
                    )

                # Affichage du contenu du chapitre courant
                if type_c == "texte":
                    st.markdown(cont)
                elif type_c == "pdf":
                    b64 = base64.b64encode(open(cont, "rb").read()).decode()
                    st.markdown(
                        f"<embed src='data:application/pdf;base64,{b64}' width='100%' height='400px'/>",
                        unsafe_allow_html=True
                    )
                elif type_c == "video":
                    st.video(cont)
                else:  # ppt
                    st.download_button(
                        t("Télécharger PPT","Download PPT","Descargar PPT"),
                        open(cont, "rb"),
                        file_name=os.path.basename(cont)
                    )

                # Boutons de navigation “Précédent” / “Suivant”
                prev_col, _, next_col = st.columns([1, 6, 1])
                with prev_col:
                    if st.button("◀️", key="nav_prev") and idx > 0:
                        st.session_state.ch_idx = idx - 1
                        st.rerun()
                with next_col:
                    if st.button("▶️", key="nav_next") and idx < total - 1:
                        st.session_state.ch_idx = idx + 1
                        st.rerun()

        # --- Passer le test ---
        with tabs[1]:
            st.header(t("📝 Passer le test","📝 Take Test","📝 Realizar Prueba"))
            # Récupérer toutes les formations
            cur_form.execute("SELECT id, titre FROM formations")
            forms = cur_form.fetchall()
            dispo = []
            for fid, ft in forms:
                # Vérifier si l’utilisateur a déjà passé et réussi le test
                cur_test.execute("SELECT passed FROM tests WHERE email = ? AND formation_id = ?", (user_email, fid))
                tp = cur_test.fetchone()
                if tp and tp[0] == 1:
                    continue
                # Nombre total de chapitres
                cur_form.execute("SELECT COUNT(*) FROM chapitres WHERE formation_id = ?", (fid,))
                tot = cur_form.fetchone()[0]
                # Nombre de chapitres lus
                cur_prog.execute("SELECT COUNT(*) FROM progress WHERE email = ? AND formation_id = ?", (user_email, fid))
                lus = cur_prog.fetchone()[0]
                if tot > 0 and lus >= tot:
                    dispo.append((fid, ft))
            if not dispo:
                st.info(t("Aucune formation éligible.","No eligible training.","No hay formación elegible."))
            else:
                titres = [t for _, t in dispo]
                sel_t = st.selectbox(t("Formation","Training","Formación"), titres, key="test_sel")
                fidt = [f for f, t in dispo if t == sel_t][0]
                # Charger les questions pour cette formation
                cur_test.execute("SELECT id, question_text, allow_multiple FROM questions WHERE formation_id = ?", (fidt,))
                qs = cur_test.fetchall()
                if not qs:
                    st.info(t("Aucun test disponible.","No test available.","No hay prueba disponible."))
                else:
                    reps = {}
                    for qid, qt, allow in qs:
                        cur_test.execute("SELECT option_text FROM options WHERE question_id = ?", (qid,))
                        opts = [o[0] for o in cur_test.fetchall()]
                        if allow:
                            reps[qid] = st.multiselect(qt, opts, key=f"rep_{qid}")
                        else:
                            reps[qid] = [st.radio(qt, opts, key=f"rep_{qid}")]
                    if st.button(t("Valider le test","Submit Test","Enviar Prueba")):
                        corr = 0
                        for qid, ans in reps.items():
                            cur_test.execute(
                                "SELECT option_text FROM options WHERE question_id = ? AND is_correct = 1",
                                (qid,)
                            )
                            bonnes = [o[0] for o in cur_test.fetchall()]
                            if set(ans) == set(bonnes):
                                corr += 1
                        score = corr / len(qs)
                        st.write(f"{corr}/{len(qs)} ({score*100:.0f}%)")
                        if score >= 0.8:
                            st.success(t("🎉 Test validé !","🎉 Test passed!","🎉 Prueba aprobada!"))
                            cur_test.execute(
                                "INSERT OR REPLACE INTO tests(email, formation_id, passed) VALUES(?, ?, 1)",
                                (user_email, fidt)
                            )
                            conn_test.commit()
                        else:
                            st.error(t(
                                "❌ Test non validé—vous devez relire la formation avant de repasser le test.",
                                "❌ Test not passed—you must reread the training before retaking the test.",
                                "❌ Prueba no aprobada; debes repasar la formación antes de volver a hacer la prueba."
                            ))
                            # Supprimer tous les chapitres lus pour forcer à tout relire
                            cur_prog.execute(
                                "DELETE FROM progress WHERE email = ? AND formation_id = ?",
                                (user_email, fidt)
                            )
                            conn_prog.commit()
                            # Réinitialiser le chapitre courant à 0 pour que l'utilisateur relise depuis le début
                            st.session_state.ch_idx = 0
                            st.success(t("Vous pouvez maintenant relire la formation depuis le début.","You can now reread the training from the beginning.","Ahora puedes repasar la formación desde el principio."))
                            st.rerun()

        # --- Mes certificats ---
        with tabs[2]:
            st.header(t("🏆 Mes certificats","🏆 My Certificates","🏆 Mis Certificados"))
            cur_test.execute("SELECT formation_id FROM tests WHERE email = ? AND passed = 1", (user_email,))
            passed = [r[0] for r in cur_test.fetchall()]
            if not passed:
                st.info(t("Aucun certificat obtenu.","No certificates earned.","No hay certificados obtenidos."))
            else:
                for fidc in passed:
                    cur_form.execute("SELECT titre FROM formations WHERE id = ?", (fidc,))
                    row = cur_form.fetchone()
                    if row is not None:
                        tit = row[0]
                    else:
                        tit = t("Formation inconnue","Unknown training","Formación desconocida")
                    obt = date.today().strftime("%d/%m/%Y")
                    st.write(f"**{tit}** — {t('obtenu le','earned on','obtenido el')} {obt}")
                    if st.button(t("Télécharger","Download","Descargar"), key=f"cert_{fidc}"):
                        fn = creer_certificat(user_email, tit, date.today())
                        with open(fn, "rb") as f:
                            st.download_button("⬇️ PDF", f, file_name=fn)

        # --- Paramètres utilisateur simple ---
        with tabs[3]:
            st.markdown(f"<h1 style='text-align:center;'>{t('⚙️ Paramètres','⚙️ Settings','⚙️ Ajustes')}</h1>", unsafe_allow_html=True)
            # Profil
            st.subheader(t("👤 Profil","👤 Profile","👤 Perfil"))
            col1, col2 = st.columns([3,1])
            with col1:
                st.text_input(t("Utilisateur","User","Usuario"), value=user_email, disabled=True)
                nom = st.text_input(t("Nom","Last Name","Apellido"), st.session_state.get("nom",""), key="param_nom")
                prenom = st.text_input(t("Prénom","First Name","Nombre"), st.session_state.get("prenom",""), key="param_prenom")
            with col2:
                photo = st.file_uploader(t("Photo de profil","Profile photo","Foto de perfil"), type=["png","jpg","jpeg"], key="param_photo")
                if photo:
                    st.image(photo, width=120)

            st.markdown("---")
            # Sécurité & vie privée
            st.subheader(t("🔒 Sécurité & vie privée","🔒 Security & Privacy","🔒 Seguridad & Privacidad"))
            ancien = st.text_input(t("Ancien mot de passe","Old password","Contraseña antigua"), type="password", key="param_old_pwd")
            nouveau = st.text_input(t("Nouveau mot de passe","New password","Contraseña nueva"), type="password", key="param_new_pwd")

            st.markdown("---")
            # Langue & Notifications
            st.subheader(t("🌐 Langue & Notifications","🌐 Language & Notifications","🌐 Idioma & Notificaciones"))
            lang = st.selectbox(
                t("Langue de l’interface","Interface language","Idioma de la interfaz"),
                ["Français","English","Español"],
                index=["Français","English","Español"].index(st.session_state.lang),
                key="param_lang"
            )
            notif_form = st.checkbox(t("Formations","Trainings","Formaciones"), value=(get_param("notif_form","True")=="True"), key="param_notif_form")
            notif_test = st.checkbox(t("Tests","Tests","Pruebas"), value=(get_param("notif_test","True")=="True"), key="param_notif_test")
            notif_cert = st.checkbox(t("Certificats","Certificates","Certificados"), value=(get_param("notif_cert","True")=="True"), key="param_notif_cert")

            if st.button(t("💾 Sauvegarder","💾 Save","💾 Guardar")):
                if ancien and nouveau:
                    cur_users.execute("SELECT mot_de_passe FROM utilisateurs WHERE email=?", (user_email,))
                    if cur_users.fetchone()[0] == ancien:
                        cur_users.execute("UPDATE utilisateurs SET mot_de_passe=? WHERE email=?", (nouveau, user_email))
                        conn_users.commit()
                        st.success(t("🔑 Mot de passe mis à jour","🔑 Password updated","🔑 Contraseña actualizada"))
                    else:
                        st.error(t("❌ Ancien mot de passe incorrect","❌ Old password incorrect","❌ Contraseña antigua incorrecta"))
                save_param("lang", lang)
                save_param("notif_form", notif_form)
                save_param("notif_test", notif_test)
                save_param("notif_cert", notif_cert)
                st.session_state.lang = lang
                st.success(t("✅ Paramètres sauvegardés","✅ Settings saved","✅ Ajustes guardados"))

            st.markdown("---")
            # — À propos & FAQ —
            st.subheader(t("❔ À propos & Aide","❔ About & Help","❔ Acerca & Ayuda"))
            st.write(f"- **{t('Version','Version','Versión')}** : 1.3.2   •   **{t('Build','Build','Compilación')}** : {datetime.now().strftime('%Y-%m-%d')}")
            with st.expander(t("❓ Comment créer un compte ?","❓ How to create an account?","❓ ¿Cómo crear una cuenta?")):
                st.write(t(
    "→ Tu dois contacter l’administrateur : c’est lui qui va créer un compte pour toi.",
    "→ You must contact the administrator: they will create an account for you.",
    "→ Debes contactar al administrador: él te creará una cuenta."
))
            with st.expander(t("❓ Où télécharger mon certificat ?","❓ Where to download my certificate?","❓ ¿Dónde descargar mi certificado?")):
                st.write(t("→ Dans “ Mes certificats”, cliquez sur “Télécharger”.","→ In “ My Certificates”, click “Download”.","→ En “ Mis Certificados”, haz clic en “Descargar”."))
            with st.expander(t("❓ Comment changer ma photo de profil ?","❓ How to change my profile photo?","❓ ¿Cómo cambiar mi foto de perfil?")):
                st.write(t(
    "→ Dans cette interface, cliquez sur “Changer la photo de profil”, choisissez un fichier et sauvegardez.",
    "→ In this interface, click “Change profile photo”, choose a file and save.",
    "→ En esta interfaz, haz clic en “Cambiar foto de perfil”, elige un archivo y guarda."
))
            with st.expander(t("❓ Qui contacter en cas de problème ?","❓ Who to contact in case of issues?","❓ ¿A quién contactar en caso de problemas?")):
                st.write(t(
                    "Envoyez un email à support@ocpgroup.com ou appelez le +212 5 36 00 00 00.",
                    "Send an email to support@ocpgroup.com or call +212 5 36 00 00 00.",
                    "Envía un correo a support@ocpgroup.com o llama al +212 5 36 00 00 00."
                ))
            st.write(f"© 2025 OCP Group — {t('Tous droits réservés.','All rights reserved.','Todos los derechos reservados.')}")

        with tabs[4]:
            
            st.markdown(
                f"<h1 style='text-align:center'>{t('📊 Mes indicateurs','📊 My Metrics','📊 Mis Indicadores')}</h1>",
                unsafe_allow_html=True
            )
        # --- 5) Mon Dashboard (Utilisateur) ---
        with tabs[4]:
            st.markdown(f"<h1 style='text-align:center'>{t('📊 Mes indicateurs','📊 My Metrics','📊 Mis Indicadores')}</h1>", unsafe_allow_html=True)

            total_chap = cur_form.execute("SELECT COUNT(*) FROM chapitres").fetchone()[0]
            chap_lus = cur_prog.execute("SELECT COUNT(*) FROM progress WHERE email=?", (user_email,)).fetchone()[0]
            total_tests_user = cur_test.execute("SELECT COUNT(*) FROM tests WHERE email=?", (user_email,)).fetchone()[0]
            passed_tests = cur_test.execute("SELECT COUNT(*) FROM tests WHERE email=? AND passed=1", (user_email,)).fetchone()[0]
            total_forms = cur_form.execute("SELECT COUNT(*) FROM formations").fetchone()[0]
            form_started = cur_prog.execute("SELECT COUNT(DISTINCT formation_id) FROM progress WHERE email=?", (user_email,)).fetchone()[0]
            form_completed = passed_tests

            has_activity = (total_chap > 0 or total_tests_user > 0 or form_started > 0)

            if has_activity:
                pct_prog = int(chap_lus / total_chap * 100) if total_chap > 0 else 0
                pct_tests = int(passed_tests / total_tests_user * 100) if total_tests_user > 0 else 0
                pct_forms = int(form_completed / total_forms * 100) if total_forms > 0 else 0

                c1, c2, c3 = st.columns(3, gap="large")
                with c1:
                    st.metric(
                        label=t("Chapitres lus","Chapters read","Capítulos leídos"),
                        value=f"{chap_lus}/{total_chap}",
                        delta=f"{pct_prog}%"
                    )
                with c2:
                    st.metric(
                        label=t("Tests réussis","Tests passed","Pruebas aprobadas"),
                        value=f"{passed_tests}/{total_tests_user}",
                        delta=f"{pct_tests}%"
                    )
                with c3:
                    st.metric(
                        label=t("Formations complétées","Trainings done","Formaciones completadas"),
                        value=f"{form_completed}/{total_forms}",
                        delta=f"{pct_forms}%"
                    )

                st.markdown("---")

                # Répartition par type de contenu
                chap_ids = [r[0] for r in cur_prog.execute("SELECT chapter_id FROM progress WHERE email=?", (user_email,)).fetchall()]
                if chap_ids:
                    placeholder = ",".join("?" for _ in chap_ids)
                    q = f"SELECT type_contenu FROM chapitres WHERE id IN ({placeholder})"
                    types = [r[0] for r in cur_form.execute(q, chap_ids).fetchall()]
                    s = pd.Series(types)
                    counts = s.value_counts()
                    pct = (counts / counts.sum() * 100).round(1)
                    df_fmt = pd.DataFrame({
                        "format": pct.index.tolist(),
                        "pct": pct.values.tolist(),
                    })
                else:
                    df_fmt = pd.DataFrame(columns=["format","pct"])

                # Chapitres lus par formation
                data = cur_prog.execute(
                    "SELECT formation_id, COUNT(*) FROM progress WHERE email=? GROUP BY formation_id",
                    (user_email,)
                ).fetchall()
                if data:
                    forms_cp = []
                    for fid_cp, cnt in data:
                        row_f = cur_form.execute("SELECT titre FROM formations WHERE id=?", (fid_cp,)).fetchone()
                        titre_cp = row_f[0] if row_f else t("Formation inconnue","Unknown training","Formación desconocida")
                        forms_cp.append({"titre": titre_cp, "lus": cnt})
                    df_cp = pd.DataFrame(forms_cp) if forms_cp else pd.DataFrame(columns=["titre","lus"])
                else:
                    df_cp = pd.DataFrame(columns=["titre","lus"])

                # Tests passés vs échecs
                df_testrate = pd.DataFrame([
                    {"cat": t("Passés","Passed","Aprobados"), "n": passed_tests},
                    {"cat": t("Échoués","Failed","Fallidos"), "n": total_tests_user - passed_tests},
                ])

                # Formations terminées vs non
                df_formrate = pd.DataFrame([
                    {"cat": t("Terminées","Done","Completadas"), "n": form_completed},
                    {"cat": t("Non terminées","Undone","No completadas"), "n": total_forms - form_completed},
                ])

                r1c1, r1c2 = st.columns(2, gap="large")
                with r1c1:
                    st.subheader(t("Par format","By format","Por formato"))
                    ch1 = (
                        alt.Chart(df_fmt)
                        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                        .encode(
                            x=alt.X("format:N", title=t("Format","Type","Formato")),
                            y=alt.Y("pct:Q", title="%"),
                            tooltip=["format","pct"]
                        )
                        .properties(height=300)
                    )
                    st.altair_chart(ch1, use_container_width=True)

                with r1c2:
                    st.subheader(t("Chapitres par formation","Chapters per training","Capítulos por formación"))
                    ch2 = (
                        alt.Chart(df_cp)
                        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                        .encode(
                            x=alt.X("titre:N", title=t("Formation","Training","Formación"), sort="-y"),
                            y=alt.Y("lus:Q", title=t("Chap. lus","Chaps read","Capítulos leídos")),
                            tooltip=["titre","lus"]
                        )
                        .properties(height=300)
                    )
                    st.altair_chart(ch2, use_container_width=True)

                r2c1, r2c2 = st.columns(2, gap="large")
                with r2c1:
                    st.subheader(t("Tests passés vs échecs","Tests passed vs failed","Pruebas aprobadas vs fallidas"))
                    ch3 = (
                        alt.Chart(df_testrate)
                        .mark_arc(innerRadius=50, outerRadius=100)
                        .encode(theta="n:Q", color=alt.Color("cat:N", legend=None))
                        .properties(height=300)
                    )
                    st.altair_chart(ch3, use_container_width=True)

                with r2c2:
                    st.subheader(t("Formations terminées vs non","Trainings done vs undone","Form completadas vs no"))
                    ch4 = (
                        alt.Chart(df_formrate)
                        .mark_arc(innerRadius=50, outerRadius=100)
                        .encode(theta="n:Q", color=alt.Color("cat:N", legend=None))
                        .properties(height=300)
                    )
                    st.altair_chart(ch4, use_container_width=True)
            else:
                st.info(t("Pas encore d’activité sur votre compte.","No activity yet.","Sin actividad aún."))

    # Footer commun
    footer_html = """
    <style>
    .footer {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        background-color: rgba(255,255,255,0.8);
        text-align: center;
        padding: 8px 0;
        font-size: 12px;
        z-index: 1000;
    }
    .footer img {
        height: 20px;
        vertical-align: middle;
        margin-right: 6px;
    }
    </style>
    <div class="footer">
      <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/OCP_Group.svg/1606px-OCP_Group.svg.png" alt="Logo" />
      © 2025 OCP Group. Tous droits réservés.
    </div>
    """
    st.markdown(footer_html, unsafe_allow_html=True)

# Lancement
if not st.session_state.authenticated:
    login_page()
    st.stop()
else:
    main()
