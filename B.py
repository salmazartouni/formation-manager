import sqlite3

conn = sqlite3.connect('users.db')
cur = conn.cursor()

# إضافة بيانات وهمية باستعمال اسم العمود الصحيح
cur.execute("INSERT INTO utilisateurs (email, mot_de_passe) VALUES ('user1@email.com', 'pass1')")
cur.execute("INSERT INTO utilisateurs (email, mot_de_passe) VALUES ('user2@email.com', 'pass2')")
cur.execute("INSERT INTO utilisateurs (email, mot_de_passe) VALUES ('user3@email.com', 'pass3')")

conn.commit()
print("✅ تمت إضافة الإيميلات مع كلمات السر بنجاح.")

# عرض البيانات
cur.execute("SELECT email, mot_de_passe FROM utilisateurs")
users = cur.fetchall()
print("\n=== Utilisateurs avec mots de passe ===")
for user in users:
    print(f"Email: {user[0]} - Password: {user[1]}")

conn.close()
