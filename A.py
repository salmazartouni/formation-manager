import sqlite3

# الاتصال بقاعدة users.db
conn = sqlite3.connect('users.db')
cur = conn.cursor()

# تأكد أن admin مازال ما تضافش
cur.execute("SELECT * FROM utilisateurs WHERE email=?", ("admin@email.com",))
if not cur.fetchone():
    # إضافة admin
    cur.execute("""
        INSERT INTO utilisateurs (email, mot_de_passe, nom, prenom, fonction, genre)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("admin@email.com", "admin123", "Admin", "Admin", "Admin", "Homme"))
    conn.commit()
    print("✅ Admin ajouté à la base de données.")
else:
    print("ℹ️ Admin déjà existant.")

conn.close()
