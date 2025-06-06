import streamlit as st
import sqlite3

# دالة التحقق من البريد الإلكتروني وكلمة السر
def verifier_connexion(email, mot_de_passe):
    conn = sqlite3.connect('users.db')  # تأكد أنك كتستعمل users.db
    cur = conn.cursor()

    # تأكد تستعمل العمود الصحيح mot_de_passe
    cur.execute("SELECT * FROM utilisateurs WHERE email=? AND mot_de_passe=?", (email, mot_de_passe))
    user = cur.fetchone()

    conn.close()

    if user:
        return True
    else:
        return False

# واجهة Streamlit
st.set_page_config(page_title="Connexion", page_icon="🔒")
st.title("🔒 Connexion")
st.write("Bienvenue! Veuillez vous connecter pour accéder à l'application.")

# Inputs de l'utilisateur
email = st.text_input("📧 Email")
mot_de_passe = st.text_input("🔑 Mot de passe", type="password")

# Bouton de connexion
if st.button("Se connecter"):
    if verifier_connexion(email, mot_de_passe):
        st.success("✅ Connexion réussie! Bienvenue 👋")
    else:
        st.error("❌ Identifiants incorrects.")
