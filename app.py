import os, re, json, secrets, sqlite3, smtplib, threading
from datetime import date, timedelta
from email.message import EmailMessage
from flask import Flask, request, jsonify, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash

def load_env(p=".env"):
    if os.path.exists(p):
        for l in open(p):
            if "=" in l and not l.startswith("#"):
                k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
load_env()
DB = os.getenv("DB_PATH", "barberflow.db")
STATUTS = ("Nouveau", "Confirmé", "Terminé", "Annulé")
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS barbers(id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT, email TEXT UNIQUE,
      password_hash TEXT, services TEXT);
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, barber_id INTEGER, nom TEXT, prenom TEXT, telephone TEXT,
      email TEXT, prestation TEXT, date TEXT, heure TEXT, commentaire TEXT, statut TEXT DEFAULT 'Nouveau',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
    if not c.execute("SELECT 1 FROM barbers").fetchone():
        svc = ["Coupe homme", "Barbe", "Coupe + Barbe", "Dégradé", "Enfant"]
        pw = generate_password_hash(os.getenv("DEMO_PASSWORD", "demo1234"))
        c.execute("INSERT INTO barbers(slug,name,email,password_hash,services) VALUES(?,?,?,?,?)",
                  ("atelier-nord", "Atelier Nord", "barbier@example.com", pw, json.dumps(svc)))
        d = lambda n: (date.today() + timedelta(days=n)).isoformat()
        for i, (n, p, s, dt, h, st) in enumerate([("Martin", "Lucas", "Coupe + Barbe", d(0), "10:00", "Confirmé"),
            ("Durand", "Yanis", "Dégradé", d(0), "14:30", "Nouveau"), ("Bernard", "Hugo", "Barbe", d(1), "11:15", "Nouveau"),
            ("Petit", "Adam", "Coupe homme", d(2), "17:00", "Nouveau"), ("Leroy", "Noé", "Enfant", d(-1), "16:00", "Terminé")]):
            c.execute("INSERT INTO requests(barber_id,nom,prenom,telephone,email,prestation,date,heure,commentaire,statut) VALUES(1,?,?,?,?,?,?,?,?,?)",
                      (n, p, f"06 00 00 00 0{i}", f"{p.lower()}@example.com", s, dt, h, "", st))
    c.commit(); c.close()

def send(to, subject, body):
    """Envoie un email (SMTP si configuré) sinon simule dans la console. Non bloquant."""
    def run():
        if not os.getenv("SMTP_HOST"):
            print(f"\n[EMAIL SIMULÉ] à {to} — {subject}\n{body}\n"); return
        try:
            m = EmailMessage(); m["From"] = os.getenv("MAIL_FROM", "BarberFlow"); m["To"] = to; m["Subject"] = subject; m.set_content(body)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", 587))) as s:
                s.starttls(); s.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", "")); s.send_message(m)
        except Exception as e: print("[EMAIL ERREUR]", e)
    threading.Thread(target=run, daemon=True).start()

# ---------- Pages ----------
@app.get("/")
def home(): return app.send_static_file("index.html")
@app.get("/dashboard")
def dash(): return app.send_static_file("dashboard.html")
@app.get("/login")
def login_page(): return app.send_static_file("login.html")
@app.get("/signup")
def signup_page(): return app.send_static_file("signup.html")
@app.get("/mentions-legales")
def legal(): return app.send_static_file("mentions-legales.html")
@app.get("/cgu")
def cgu(): return app.send_static_file("cgu.html")
@app.get("/confidentialite")
def priv(): return app.send_static_file("confidentialite.html")

# ---------- Auth barbier ----------
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

@app.post("/api/auth/signup")
def signup():
    j = request.get_json(silent=True) or {}
    name = str(j.get("name", "")).strip()[:100]
    email = str(j.get("email", "")).strip().lower()[:150]
    slug = str(j.get("slug", "")).strip().lower()[:60]
    pw = str(j.get("password", ""))
    err = []
    if not name: err.append("name")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): err.append("email")
    if not SLUG_RE.match(slug): err.append("slug")
    if len(pw) < 8: err.append("password")
    if err: return jsonify(error="invalid", fields=err), 400
    c = db()
    if c.execute("SELECT 1 FROM barbers WHERE slug=? OR email=?", (slug, email)).fetchone():
        return jsonify(error="taken", fields=["slug_or_email"]), 409
    svc = ["Coupe homme", "Barbe", "Coupe + Barbe", "Dégradé", "Enfant"]
    cur = c.execute("INSERT INTO barbers(slug,name,email,password_hash,services) VALUES(?,?,?,?,?)",
                    (slug, name, email, generate_password_hash(pw), json.dumps(svc)))
    c.commit()
    session["barber_id"] = cur.lastrowid
    return jsonify(ok=True, slug=slug), 201

@app.post("/api/auth/login")
def login():
    j = request.get_json(silent=True) or {}
    email = str(j.get("email", "")).strip().lower()
    pw = str(j.get("password", ""))
    b = db().execute("SELECT * FROM barbers WHERE email=?", (email,)).fetchone()
    if not b or not check_password_hash(b["password_hash"], pw):
        return jsonify(error="invalid_credentials"), 401
    session["barber_id"] = b["id"]
    return jsonify(ok=True, slug=b["slug"])

@app.post("/api/auth/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@app.get("/api/auth/me")
def me():
    b = current_barber()
    if not b: return jsonify(authenticated=False)
    return jsonify(authenticated=True, name=b["name"], slug=b["slug"])

def current_barber():
    bid = session.get("barber_id")
    if not bid: return None
    return db().execute("SELECT * FROM barbers WHERE id=?", (bid,)).fetchone()

def require_barber():
    b = current_barber()
    if not b: abort(401)
    return b

# ---------- API publique (formulaire client) ----------
@app.get("/api/barbers/<slug>")
def barber(slug):
    b = db().execute("SELECT name,services FROM barbers WHERE slug=?", (slug,)).fetchone() or abort(404)
    return jsonify(name=b["name"], services=json.loads(b["services"]))

@app.post("/api/barbers/<slug>/requests")
def create(slug):
    c = db(); b = c.execute("SELECT * FROM barbers WHERE slug=?", (slug,)).fetchone() or abort(404)
    j = request.get_json(silent=True) or {}
    # Honeypot anti-spam : champ invisible pour un humain, souvent rempli par les robots
    if str(j.get("site_web", "")).strip():
        return jsonify(id=0), 201  # on fait croire que ça a marché, sans rien enregistrer
    f = {k: str(j.get(k, "")).strip()[:200] for k in ("nom", "prenom", "telephone", "email", "prestation", "date", "heure", "commentaire")}
    err = [k for k in f if k != "commentaire" and not f[k]]
    if f["email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", f["email"]): err.append("email")
    if f["prestation"] not in json.loads(b["services"]): err.append("prestation")
    if f["date"] and f["date"] < date.today().isoformat(): err.append("date")
    if err: return jsonify(error="invalid", fields=sorted(set(err))), 400
    taken = c.execute("SELECT 1 FROM requests WHERE barber_id=? AND date=? AND heure=? AND statut IN ('Nouveau','Confirmé')",
                      (b["id"], f["date"], f["heure"])).fetchone()
    if taken: return jsonify(error="slot_taken"), 409
    cur = c.execute("INSERT INTO requests(barber_id,nom,prenom,telephone,email,prestation,date,heure,commentaire) VALUES(?,?,?,?,?,?,?,?,?)",
                    (b["id"], *[f[k] for k in ("nom", "prenom", "telephone", "email", "prestation", "date", "heure", "commentaire")]))
    c.commit()
    send(b["email"], f"Nouvelle demande — {f['prenom']} {f['nom']}", f"{f['prestation']} le {f['date']} à {f['heure']}\nTél : {f['telephone']}\n{f['commentaire']}")
    send(f["email"], f"Demande reçue — {b['name']}", f"Bonjour {f['prenom']},\nVotre demande ({f['prestation']}, {f['date']} à {f['heure']}) a bien été envoyée à {b['name']}. Vous recevrez une réponse rapidement.")
    return jsonify(id=cur.lastrowid), 201

# ---------- Dashboard (protégé par session) ----------
@app.get("/api/dashboard/requests")
def lst():
    b = require_barber()
    rows = db().execute("SELECT * FROM requests WHERE barber_id=? ORDER BY date,heure", (b["id"],)).fetchall()
    return jsonify(barber=b["name"], requests=[dict(r) for r in rows])

@app.patch("/api/dashboard/requests/<int:rid>")
def upd(rid):
    b = require_barber(); st = (request.get_json(silent=True) or {}).get("statut")
    if st not in STATUTS: abort(400)
    c = db(); r = c.execute("SELECT * FROM requests WHERE id=? AND barber_id=?", (rid, b["id"])).fetchone() or abort(404)
    c.execute("UPDATE requests SET statut=? WHERE id=?", (st, rid)); c.commit()
    msgs = {"Confirmé": "est confirmé ✅", "Annulé": "a dû être annulé. N'hésitez pas à proposer un autre créneau."}
    if st in msgs and r["statut"] != st:
        send(r["email"], f"Votre rendez-vous — {b['name']}", f"Bonjour {r['prenom']},\nvotre rendez-vous ({r['prestation']}, {r['date']} à {r['heure']}) {msgs[st]}")
    return jsonify(ok=True, statut=st)

init()
if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)), debug=False)
