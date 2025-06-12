import sqlite3

# 1. Connexion à la BDD
conn = sqlite3.connect("users.db", check_same_thread=False)
cur  = conn.cursor()

# 2. Exécution de la requête pour lister email + mot_de_passe des Admin
cur.execute("""
    SELECT email, mot_de_passe 
    FROM utilisateurs 
    WHERE fonction = 'Admin'
""")

# 3. Affichage
admins = cur.fetchall()
if not admins:
    print("Aucun compte Admin trouvé.")
else:
    for email, pwd in admins:
        print(f"Admin → email: {email} | mot_de_passe: {pwd}")
