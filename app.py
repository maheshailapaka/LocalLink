from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify)
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3, os, secrets, json, random
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = 'locallink-secret-key-2024-production'

# Flask-SocketIO — async_mode='threading' works with the built-in dev server.
# For production swap to: async_mode='eventlet'  (pip install eventlet)
#                      or async_mode='gevent'     (pip install gevent gevent-websocket)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

UPLOAD_FOLDER      = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER']        = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH']   = 5 * 1024 * 1024

# Email config
EMAIL_HOST     = 'smtp.gmail.com'
EMAIL_PORT     = 587
EMAIL_USER     = ''
EMAIL_PASSWORD = ''
EMAIL_FROM     = 'Local Link <noreply@locallink.com>'

os.makedirs(os.path.join(UPLOAD_FOLDER, 'providers'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'services'),  exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'kyc'),       exist_ok=True)

# ── SocketIO rooms handle real-time (no queues needed) ──────────────────────

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def allowed_file(f):
    return '.' in f and f.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_otp():
    return str(secrets.randbelow(900000) + 100000)

def send_otp_email(to_email: str, otp: str, name: str = 'User') -> bool:
    if not EMAIL_USER or not EMAIL_PASSWORD: return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Your Local Link Verification Code'
        msg['From']    = EMAIL_FROM
        msg['To']      = to_email
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:30px;
                    background:#f8f9fa;border-radius:12px;">
          <h2 style="color:#667eea;">Local Link 📍</h2>
          <p>Hi {name},</p>
          <p>Your verification code is:</p>
          <div style="font-size:40px;font-weight:900;letter-spacing:10px;color:#667eea;
                      text-align:center;background:white;padding:20px;border-radius:8px;margin:20px 0;">
            {otp}
          </div>
          <p>Expires in <strong>10 minutes</strong>.</p>
        </div>"""
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASSWORD)
            s.sendmail(EMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}"); return False

def save_search_history(user_id, data):
    if not user_id: return
    conn = get_db()
    conn.execute('INSERT INTO search_history(user_id,service_type,city,locality) VALUES(?,?,?,?)',
                 (user_id, data.get('service_type'), data.get('city'), data.get('locality')))
    conn.commit(); conn.close()

def track_provider_view(provider_id, user_id):
    if not user_id: return
    conn = get_db()
    conn.execute('INSERT INTO provider_views(provider_id,user_id) VALUES(?,?)', (provider_id, user_id))
    conn.commit(); conn.close()

def calculate_trust_score(provider_id):
    conn  = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM bookings WHERE provider_id=?',(provider_id,)).fetchone()['c']
    done  = conn.execute("SELECT COUNT(*) as c FROM bookings WHERE provider_id=? AND status='Completed'",(provider_id,)).fetchone()['c']
    avg_r = conn.execute('SELECT COALESCE(AVG(rating),0) as a FROM reviews WHERE provider_id=?',(provider_id,)).fetchone()['a']
    rev_c = conn.execute('SELECT COUNT(*) as c FROM reviews WHERE provider_id=?',(provider_id,)).fetchone()['c']
    conn.close()
    cr    = (done/total) if total > 0 else 0
    trust = (cr*4.0) + (avg_r/5.0*5.0) + (min(rev_c/10.0,1.0)*1.0)
    return round(min(trust, 10.0), 1)

def get_ai_recommendations(user_id):
    conn     = get_db()
    searches = conn.execute('SELECT service_type,city FROM search_history WHERE user_id=? ORDER BY created_at DESC LIMIT 10',(user_id,)).fetchall()
    bookings = conn.execute('SELECT sp.service_type,sp.city FROM bookings b JOIN service_providers sp ON b.provider_id=sp.id WHERE b.user_id=?',(user_id,)).fetchall()
    if not searches and not bookings:
        providers = conn.execute('''SELECT sp.*,COUNT(DISTINCT b.id) as booking_count,
                                   COALESCE(AVG(r.rating),0) as avg_rating
                                   FROM service_providers sp
                                   LEFT JOIN bookings b ON sp.id=b.provider_id
                                   LEFT JOIN reviews r ON sp.id=r.provider_id
                                   WHERE sp.approved=1 GROUP BY sp.id ORDER BY booking_count DESC LIMIT 5''').fetchall()
        conn.close(); return providers
    svc_list  = [s['service_type'] for s in searches] + [b['service_type'] for b in bookings]
    city_list = [s['city'] for s in searches] + [b['city'] for b in bookings]
    if svc_list:
        top_svc  = max(set(svc_list),  key=svc_list.count)
        top_city = max(set(city_list), key=city_list.count) if city_list else None
        providers = conn.execute('''SELECT sp.*,COALESCE(AVG(r.rating),0) as avg_rating,
                                   COUNT(DISTINCT r.id) as review_count
                                   FROM service_providers sp LEFT JOIN reviews r ON sp.id=r.provider_id
                                   WHERE sp.approved=1 AND sp.service_type=? AND (sp.city=? OR ? IS NULL)
                                   GROUP BY sp.id ORDER BY avg_rating DESC LIMIT 5''',
                                  (top_svc, top_city, top_city)).fetchall()
        conn.close(); return providers
    conn.close(); return []

def get_provider_analytics(provider_id):
    conn  = get_db()
    views = conn.execute('SELECT COUNT(*) as c FROM provider_views WHERE provider_id=?',(provider_id,)).fetchone()['c']
    uv    = conn.execute('SELECT COUNT(DISTINCT user_id) as c FROM provider_views WHERE provider_id=?',(provider_id,)).fetchone()['c']
    tb    = conn.execute('SELECT COUNT(*) as c FROM bookings WHERE provider_id=?',(provider_id,)).fetchone()['c']
    comp  = conn.execute("SELECT COUNT(*) as c FROM bookings WHERE provider_id=? AND status='Completed'",(provider_id,)).fetchone()['c']
    rev   = conn.execute("SELECT COALESCE(SUM(payment_amount),0) as t FROM bookings WHERE provider_id=? AND payment_status='Completed'",(provider_id,)).fetchone()['t']
    avg_r = conn.execute('SELECT COALESCE(AVG(rating),0) as a FROM reviews WHERE provider_id=?',(provider_id,)).fetchone()['a']
    rdist = {i: conn.execute('SELECT COUNT(*) as c FROM reviews WHERE provider_id=? AND rating=?',(provider_id,i)).fetchone()['c'] for i in range(1,6)}
    vtl   = conn.execute("""SELECT DATE(viewed_at) as date,COUNT(*) as views FROM provider_views
                             WHERE provider_id=? AND viewed_at>=date('now','-30 days')
                             GROUP BY DATE(viewed_at) ORDER BY date""",(provider_id,)).fetchall()
    # Phase 3: wallet earnings
    wallet_bal = conn.execute('SELECT COALESCE(balance,0) as b FROM wallets WHERE user_id=(SELECT user_id FROM service_providers WHERE id=?)',(provider_id,)).fetchone()
    conn.close()
    return {'views':views,'unique_viewers':uv,'total_bookings':tb,'completed_bookings':comp,
            'conversion_rate':(tb/views*100) if views>0 else 0,'revenue':rev,
            'avg_rating':round(avg_r,2),'rating_distribution':rdist,'views_timeline':vtl,
            'wallet_balance': wallet_bal['b'] if wallet_bal else 0}

# ── Phase 2: AI Pricing Suggestion ───────────────────────────────────────────
def get_ai_price_suggestion(service_type, city):
    """Returns suggested min/max price based on market data in DB."""
    conn = get_db()
    stats = conn.execute('''SELECT MIN(s.price) as min_p, MAX(s.price) as max_p,
                            AVG(s.price) as avg_p, COUNT(s.id) as cnt
                            FROM services s JOIN service_providers sp ON s.provider_id=sp.id
                            WHERE sp.service_type LIKE ? AND sp.city LIKE ? AND sp.approved=1''',
                         (f'%{service_type}%', f'%{city}%')).fetchone()
    conn.close()
    if stats and stats['cnt'] and stats['cnt'] > 0:
        avg  = round(stats['avg_p'] or 0)
        low  = round((stats['min_p'] or avg) * 0.9)
        high = round((stats['max_p'] or avg) * 1.1)
        return {'suggested': avg, 'range_low': low, 'range_high': high,
                'sample_size': stats['cnt'], 'found': True}
    return {'suggested': 500, 'range_low': 300, 'range_high': 800,
            'sample_size': 0, 'found': False}

# ── Phase 3: Wallet helpers ───────────────────────────────────────────────────
def get_or_create_wallet(user_id):
    conn = get_db()
    w = conn.execute('SELECT * FROM wallets WHERE user_id=?',(user_id,)).fetchone()
    if not w:
        conn.execute('INSERT INTO wallets(user_id,balance) VALUES(?,0)',(user_id,))
        conn.commit()
        w = conn.execute('SELECT * FROM wallets WHERE user_id=?',(user_id,)).fetchone()
    conn.close()
    return w

def wallet_credit(user_id, amount, description):
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,0)',(user_id,))
    conn.execute('UPDATE wallets SET balance=balance+? WHERE user_id=?',(amount,user_id))
    conn.execute('INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)',
                 (user_id, amount, 'credit', description))
    conn.commit(); conn.close()

def wallet_debit(user_id, amount, description):
    conn = get_db()
    w = conn.execute('SELECT balance FROM wallets WHERE user_id=?',(user_id,)).fetchone()
    if not w or w['balance'] < amount:
        conn.close(); return False
    conn.execute('UPDATE wallets SET balance=balance-? WHERE user_id=?',(amount,user_id))
    conn.execute('INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)',
                 (user_id, amount, 'debit', description))
    conn.commit(); conn.close()
    return True

def notify(user_id, title, body, ntype='info', link=None):
    """Insert a notification for user_id — called from routes, never blocks."""
    try:
        conn = get_db()
        conn.execute(
            'INSERT INTO notifications(user_id,title,body,type,link) VALUES(?,?,?,?,?)',
            (user_id, title, body, ntype, link))
        conn.commit(); conn.close()
    except Exception as e:
        print(f'[NOTIFY ERROR] {e}')

# ── DB Init ───────────────────────────────────────────────────────────────────
def init_db():
    conn = get_db(); c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'customer',
            city TEXT, locality TEXT, phone TEXT,
            email_verified BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS otp_verifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, otp_code TEXT NOT NULL,
            contact TEXT NOT NULL, expires_at TIMESTAMP NOT NULL,
            verified BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS service_providers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, name TEXT NOT NULL, service_type TEXT NOT NULL,
            phone TEXT NOT NULL, city TEXT NOT NULL, locality TEXT NOT NULL,
            experience INTEGER DEFAULT 0, latitude REAL, longitude REAL,
            approved BOOLEAN DEFAULT 0, is_emergency INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0, is_featured INTEGER DEFAULT 0,
            trust_score REAL DEFAULT 5.0, description TEXT,
            is_available INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id));

        CREATE TABLE IF NOT EXISTS services(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, service_name TEXT NOT NULL,
            price REAL, description TEXT,
            FOREIGN KEY(provider_id) REFERENCES service_providers(id));

        CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, provider_id INTEGER, service_id INTEGER,
            booking_date TEXT NOT NULL, address TEXT, notes TEXT,
            status TEXT DEFAULT 'Pending', payment_status TEXT DEFAULT 'Pending',
            payment_amount REAL, payment_id TEXT, order_id TEXT,
            is_emergency INTEGER DEFAULT 0, commission REAL DEFAULT 0,
            coupon_code TEXT, discount_amount REAL DEFAULT 0,
            provider_lat REAL, provider_lng REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(provider_id) REFERENCES service_providers(id),
            FOREIGN KEY(service_id) REFERENCES services(id));

        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, provider_id INTEGER,
            rating INTEGER CHECK(rating>=1 AND rating<=5),
            comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(provider_id) REFERENCES service_providers(id));

        CREATE TABLE IF NOT EXISTS chat_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER, receiver_id INTEGER, booking_id INTEGER,
            message TEXT NOT NULL, is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS provider_photos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, photo_url TEXT NOT NULL,
            caption TEXT, is_profile INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS provider_views(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER, user_id INTEGER,
            viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS search_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, search_query TEXT,
            service_type TEXT, city TEXT, locality TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS local_feed(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, content TEXT NOT NULL,
            category TEXT DEFAULT 'general', city TEXT,
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id));

        CREATE TABLE IF NOT EXISTS emergency_contacts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, phone TEXT NOT NULL,
            type TEXT, city TEXT);

        -- ── PHASE 1: KYC ──────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS kyc_documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            doc_number TEXT,
            file_url TEXT,
            status TEXT DEFAULT 'pending',
            admin_note TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            FOREIGN KEY(provider_id) REFERENCES service_providers(id));

        -- ── PHASE 1: COUPONS ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coupons(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_type TEXT DEFAULT 'percentage',
            discount_value REAL NOT NULL,
            min_order REAL DEFAULT 0,
            max_uses INTEGER DEFAULT 100,
            used_count INTEGER DEFAULT 0,
            valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            valid_until TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS coupon_uses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coupon_id INTEGER, user_id INTEGER, booking_id INTEGER,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        -- ── PHASE 2: SUBSCRIPTIONS ────────────────────────────────────
        CREATE TABLE IF NOT EXISTS subscription_plans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            duration_days INTEGER NOT NULL,
            features TEXT,
            is_active INTEGER DEFAULT 1);

        CREATE TABLE IF NOT EXISTS user_subscriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ends_at TIMESTAMP NOT NULL,
            payment_status TEXT DEFAULT 'active',
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(plan_id) REFERENCES subscription_plans(id));

        -- ── PHASE 3: WALLET ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS wallets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            balance REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id));

        CREATE TABLE IF NOT EXISTS wallet_transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id));

        -- ── PHASE 3: ENTERPRISE ───────────────────────────────────────
        CREATE TABLE IF NOT EXISTS enterprise_accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_person TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            city TEXT,
            gst_number TEXT,
            credit_limit REAL DEFAULT 50000,
            credit_used REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

        -- ── NOTIFICATIONS ────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            type TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id));

        -- ── PASSWORD RESET ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS password_resets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    ''')

    # ── Seed admin ─────────────────────────────────────────────────────────
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        c.execute("INSERT INTO users(name,email,password,role,city,email_verified) VALUES(?,?,?,?,?,?)",
                  ('Admin','admin@locallink.com',generate_password_hash('admin123'),'admin','Hyderabad',1))

    if not c.execute("SELECT 1 FROM users WHERE email='demo@locallink.com'").fetchone():
        c.execute("INSERT INTO users(name,email,password,role,city,locality,email_verified) VALUES(?,?,?,?,?,?,?)",
                  ('Demo User','demo@locallink.com',generate_password_hash('demo123'),'customer','Hyderabad','Banjara Hills',1))

    if not c.execute("SELECT 1 FROM users WHERE email='provider@locallink.com'").fetchone():
        c.execute("INSERT INTO users(name,email,password,role,city,email_verified) VALUES(?,?,?,?,?,?)",
                  ('Ravi Kumar','provider@locallink.com',generate_password_hash('provider123'),'provider','Hyderabad',1))

    if not c.execute("SELECT 1 FROM service_providers").fetchone():
        prow = c.execute("SELECT id FROM users WHERE email='provider@locallink.com'").fetchone()
        pid  = prow[0] if prow else None
        providers = [
            (pid,'Ravi Kumar Plumbing','Plumbing','9876543210','Hyderabad','MG Road',10,17.3850,78.4867,1,1,1,1,9.2,'Expert plumber 10 yrs. Available 24/7 for emergencies.',1),
            (None,'Suresh Electric Works','Electrical','9876543211','Hyderabad','Banjara Hills',8,17.4146,78.4490,1,1,0,1,8.7,'Certified electrician for wiring, fitting and repairs.',1),
            (None,'Mahesh Carpentry','Carpentry','9876543212','Hyderabad','Jubilee Hills',12,17.4319,78.4073,1,0,0,1,8.1,'Custom furniture, wardrobes and wood work.',1),
            (None,'CleanPro Services','House Cleaning','9876543213','Hyderabad','Kondapur',5,17.4700,78.3500,1,1,1,0,9.5,'Professional home and office deep cleaning.',1),
            (None,'ColorMaster Painters','Painting','9876543214','Hyderabad','Gachibowli',7,17.4400,78.3489,1,0,0,1,7.8,'Interior and exterior painting experts.',1),
            (None,'SpeedFix Plumbing','Plumbing','9876543215','Hyderabad','Madhapur',6,17.4500,78.3900,1,1,1,1,8.9,'24/7 emergency plumbing. 30-min response guaranteed.',1),
            (None,'TechCool AC Repair','AC Repair','9876543216','Hyderabad','Hitech City',9,17.4480,78.3800,1,1,0,1,8.3,'AC installation, servicing and repair all brands.',1),
            (None,'GreenThumb Gardening','Gardening','9876543217','Hyderabad','Puppalaguda',4,17.3900,78.3700,1,0,0,0,7.5,'Garden design, maintenance and landscaping.',1),
        ]
        for p in providers:
            c.execute('''INSERT INTO service_providers
                        (user_id,name,service_type,phone,city,locality,experience,
                         latitude,longitude,approved,is_emergency,is_featured,is_verified,
                         trust_score,description,is_available) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', p)
        sp_ids = [r[0] for r in c.execute("SELECT id FROM service_providers").fetchall()]
        svcs = [
            (sp_ids[0],'Pipe Leak Repair',350,'Fix leaking pipes, joints and taps'),
            (sp_ids[0],'Bathroom Fitting',1200,'Complete bathroom fitting and fixtures'),
            (sp_ids[0],'Emergency Plumbing',800,'24/7 emergency plumbing service'),
            (sp_ids[1],'Wiring & Fitting',500,'New wiring and electrical fitting'),
            (sp_ids[1],'MCB/Fuse Repair',250,'Safety switch and fuse box repairs'),
            (sp_ids[2],'Furniture Repair',400,'All types of furniture repair and polish'),
            (sp_ids[3],'Home Deep Clean',1500,'Full home deep cleaning service'),
            (sp_ids[3],'Office Cleaning',2000,'Professional office cleaning'),
            (sp_ids[4],'Interior Painting',3000,'Interior wall painting per room'),
            (sp_ids[5],'Emergency Plumbing',900,'30-min guaranteed response'),
            (sp_ids[6],'AC Service',800,'Annual maintenance and gas refill'),
            (sp_ids[7],'Garden Maintenance',600,'Monthly garden maintenance'),
        ]
        c.executemany('INSERT INTO services(provider_id,service_name,price,description) VALUES(?,?,?,?)', svcs)
        cust = c.execute("SELECT id FROM users WHERE email='demo@locallink.com'").fetchone()
        if cust:
            cid = cust[0]
            c.executemany('INSERT INTO reviews(user_id,provider_id,rating,comment) VALUES(?,?,?,?)',[
                (cid,sp_ids[0],5,'Excellent! Fixed the leak quickly and professionally.'),
                (cid,sp_ids[0],4,'Good service, came on time.'),
                (cid,sp_ids[1],5,'Very knowledgeable, solved complex wiring issue.'),
                (cid,sp_ids[3],5,'Amazing cleaning! House looks brand new.'),
                (cid,sp_ids[5],5,'Emergency response in 25 minutes. Life saver!'),
                (cid,sp_ids[6],4,'Good AC service, reasonably priced.'),
            ])

    if not c.execute("SELECT 1 FROM emergency_contacts").fetchone():
        c.executemany('INSERT INTO emergency_contacts(name,phone,type,city) VALUES(?,?,?,?)',[
            ('Police','100','police','All Cities'),('Fire Station','101','fire','All Cities'),
            ('Ambulance','108','medical','All Cities'),('Women Helpline','1091','helpline','All Cities'),
            ('Child Helpline','1098','helpline','All Cities'),('Disaster Mgmt','1077','disaster','Hyderabad'),
        ])

    if not c.execute("SELECT 1 FROM local_feed").fetchone():
        admin = c.execute("SELECT id FROM users WHERE role='admin'").fetchone()
        if admin:
            c.executemany('INSERT INTO local_feed(user_id,content,category,city,likes) VALUES(?,?,?,?,?)',[
                (admin[0],'Looking for a good tiffin service near Banjara Hills. Any recommendations? 🍱','question','Hyderabad',3),
                (admin[0],'Ravi Kumar Plumbing fixed my pipe burst in 20 mins. Highly recommend! 🔧','recommendation','Hyderabad',7),
                (admin[0],'Second-hand fridge available in Kondapur. ₹4500. DM for details 🧊','sell','Hyderabad',2),
            ])

    # ── PHASE 1: Seed coupons ──────────────────────────────────────────────
    if not c.execute("SELECT 1 FROM coupons").fetchone():
        c.executemany('INSERT INTO coupons(code,discount_type,discount_value,min_order,max_uses,valid_until) VALUES(?,?,?,?,?,?)',[
            ('WELCOME20','percentage',20,200,500,(datetime.now()+timedelta(days=365)).strftime('%Y-%m-%d')),
            ('FLAT100','fixed',100,500,200,(datetime.now()+timedelta(days=180)).strftime('%Y-%m-%d')),
            ('EMERGENCY10','percentage',10,0,1000,(datetime.now()+timedelta(days=365)).strftime('%Y-%m-%d')),
        ])

    # ── PHASE 2: Seed subscription plans ──────────────────────────────────
    if not c.execute("SELECT 1 FROM subscription_plans").fetchone():
        c.executemany('INSERT INTO subscription_plans(name,price,duration_days,features) VALUES(?,?,?,?)',[
            ('Basic',99,30,'Priority listing,5% discount on bookings'),
            ('Pro',299,90,'Featured badge,10% discount,AI pricing,Analytics'),
            ('Business',699,180,'All Pro features,Dedicated support,Custom reports,Multi-city'),
        ])

    conn.commit(); conn.close()

init_db()

# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name     = request.form['name']
        email    = request.form['email']
        password = generate_password_hash(request.form['password'])
        role     = request.form.get('role','customer')
        city     = request.form.get('city','')
        locality = request.form.get('locality','')
        phone    = request.form.get('phone','')
        try:
            conn = get_db()
            conn.execute('INSERT INTO users(name,email,password,role,city,locality,phone,email_verified) VALUES(?,?,?,?,?,?,?,0)',
                         (name,email,password,role,city,locality,phone))
            conn.commit()
            user_id = conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()['id']
            conn.close()
            otp = generate_otp()
            exp = datetime.now() + timedelta(minutes=10)
            conn2 = get_db()
            conn2.execute('INSERT INTO otp_verifications(user_id,otp_code,contact,expires_at) VALUES(?,?,?,?)',
                          (user_id, otp, email, exp))
            conn2.commit(); conn2.close()
            email_sent = send_otp_email(email, otp, name)
            session['pending_verification_user_id'] = user_id
            session['pending_verification_email']   = email
            session['demo_otp'] = otp
            if email_sent: flash(f'Account created! Check {email} for your verification code.', 'success')
            else: flash('Account created! Since email is in demo mode, your OTP is shown below.', 'info')
            return redirect(url_for('verify_email'))
        except sqlite3.IntegrityError:
            flash('Email already registered!', 'danger')
    return render_template('register.html')

@app.route('/verify-email', methods=['GET','POST'])
def verify_email():
    if 'pending_verification_user_id' not in session:
        flash('No pending verification.', 'warning')
        return redirect(url_for('register'))
    demo_otp = session.get('demo_otp')
    if request.method == 'POST':
        entered = request.form['otp'].strip()
        user_id = session['pending_verification_user_id']
        conn    = get_db()
        rec     = conn.execute('''SELECT * FROM otp_verifications
                                  WHERE user_id=? AND otp_code=? AND verified=0
                                  AND expires_at > ? ORDER BY created_at DESC LIMIT 1''',
                               (user_id, entered, datetime.now())).fetchone()
        if rec:
            conn.execute('UPDATE otp_verifications SET verified=1 WHERE id=?',(rec['id'],))
            conn.execute('UPDATE users SET email_verified=1 WHERE id=?',(user_id,))
            conn.commit(); conn.close()
            session.pop('pending_verification_user_id', None)
            session.pop('pending_verification_email', None)
            session.pop('demo_otp', None)
            flash('Email verified! You can now login. ✅', 'success')
            return redirect(url_for('login'))
        conn.close()
        flash('Invalid or expired OTP. Please try again.', 'danger')
    return render_template('verify_otp.html',
                           email=session.get('pending_verification_email'),
                           demo_otp=demo_otp)

@app.route('/resend-otp')
def resend_otp():
    uid   = session.get('pending_verification_user_id')
    email = session.get('pending_verification_email')
    if not uid: return redirect(url_for('register'))
    conn = get_db()
    name = conn.execute('SELECT name FROM users WHERE id=?',(uid,)).fetchone()['name']
    conn.close()
    otp = generate_otp()
    exp = datetime.now() + timedelta(minutes=10)
    conn2 = get_db()
    conn2.execute('INSERT INTO otp_verifications(user_id,otp_code,contact,expires_at) VALUES(?,?,?,?)',
                  (uid, otp, email, exp))
    conn2.commit(); conn2.close()
    session['demo_otp'] = otp
    sent = send_otp_email(email, otp, name)
    flash('OTP resent! ' + ('Check your email.' if sent else f'Demo OTP: {otp}'), 'info')
    return redirect(url_for('verify_email'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        conn  = get_db()
        user  = conn.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], request.form['password']):
            if not user['email_verified']:
                otp = generate_otp()
                exp = datetime.now() + timedelta(minutes=10)
                conn2 = get_db()
                conn2.execute('INSERT INTO otp_verifications(user_id,otp_code,contact,expires_at) VALUES(?,?,?,?)',
                              (user['id'], otp, email, exp))
                conn2.commit(); conn2.close()
                session['pending_verification_user_id'] = user['id']
                session['pending_verification_email']   = email
                session['demo_otp'] = otp
                send_otp_email(email, otp, user['name'])
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('verify_email'))
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            session['city']      = user['city']
            flash(f'Welcome back, {user["name"]}! 👋', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()

    if session['user_role'] == 'customer':
        bookings    = conn.execute('''SELECT b.*,sp.name as provider_name,sp.phone,
                                     s.service_name FROM bookings b
                                     JOIN service_providers sp ON b.provider_id=sp.id
                                     LEFT JOIN services s ON b.service_id=s.id
                                     WHERE b.user_id=? ORDER BY b.created_at DESC''',
                                   (session['user_id'],)).fetchall()
        recommended = get_ai_recommendations(session['user_id'])
        feed_posts  = conn.execute('''SELECT f.*,u.name as user_name FROM local_feed f
                                      JOIN users u ON f.user_id=u.id
                                      ORDER BY f.created_at DESC LIMIT 3''').fetchall()
        emergency   = conn.execute('SELECT * FROM emergency_contacts LIMIT 5').fetchall()
        wallet      = get_or_create_wallet(session['user_id'])
        # active subscription
        sub         = conn.execute('''SELECT us.*,sp.name as plan_name FROM user_subscriptions us
                                     JOIN subscription_plans sp ON us.plan_id=sp.id
                                     WHERE us.user_id=? AND us.ends_at > ? AND us.payment_status='active'
                                     ORDER BY us.ends_at DESC LIMIT 1''',
                                   (session['user_id'], datetime.now())).fetchone()
        conn.close()
        return render_template('dashboard.html', bookings=bookings, recommended=recommended,
                               feed_posts=feed_posts, emergency=emergency,
                               wallet=wallet, subscription=sub)

    elif session['user_role'] == 'provider':
        provider = conn.execute('SELECT * FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
        if provider:
            services = conn.execute('SELECT * FROM services WHERE provider_id=?',(provider['id'],)).fetchall()
            bookings = conn.execute('''SELECT b.*,u.name as customer_name,u.email,
                                       u.phone as customer_phone,s.service_name FROM bookings b
                                       JOIN users u ON b.user_id=u.id
                                       LEFT JOIN services s ON b.service_id=s.id
                                       WHERE b.provider_id=? ORDER BY b.created_at DESC''',
                                    (provider['id'],)).fetchall()
            reviews  = conn.execute('''SELECT r.*,u.name as customer_name FROM reviews r
                                       JOIN users u ON r.user_id=u.id
                                       WHERE r.provider_id=? ORDER BY r.created_at DESC''',
                                    (provider['id'],)).fetchall()
            photos   = conn.execute('SELECT * FROM provider_photos WHERE provider_id=? ORDER BY is_profile DESC',
                                    (provider['id'],)).fetchall()
            kyc      = conn.execute('SELECT * FROM kyc_documents WHERE provider_id=? ORDER BY submitted_at DESC',
                                    (provider['id'],)).fetchall()
            wallet   = get_or_create_wallet(session['user_id'])
            ts       = calculate_trust_score(provider['id'])
            conn.execute('UPDATE service_providers SET trust_score=? WHERE id=?',(ts, provider['id']))
            conn.commit(); conn.close()
            return render_template('provider_dashboard.html', provider=provider,
                                   services=services, bookings=bookings, reviews=reviews,
                                   photos=photos, kyc_docs=kyc, wallet=wallet)
        conn.close()
        return redirect(url_for('provider_setup'))

    elif session['user_role'] == 'admin':
        pending   = conn.execute('''SELECT sp.*,u.email as user_email FROM service_providers sp
                                    LEFT JOIN users u ON sp.user_id=u.id WHERE sp.approved=0''').fetchall()
        kyc_pend  = conn.execute('''SELECT kd.*,sp.name as provider_name FROM kyc_documents kd
                                    JOIN service_providers sp ON kd.provider_id=sp.id
                                    WHERE kd.status='pending' ORDER BY kd.submitted_at''').fetchall()
        tu  = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        tp  = conn.execute("SELECT COUNT(*) as c FROM service_providers WHERE approved=1").fetchone()['c']
        tb  = conn.execute('SELECT COUNT(*) as c FROM bookings').fetchone()['c']
        tr  = conn.execute("SELECT COALESCE(SUM(payment_amount),0) as t FROM bookings WHERE payment_status='Completed'").fetchone()['t']
        recent   = conn.execute('''SELECT b.*,u.name as customer_name,sp.name as provider_name,
                                   s.service_name FROM bookings b
                                   JOIN users u ON b.user_id=u.id
                                   JOIN service_providers sp ON b.provider_id=sp.id
                                   LEFT JOIN services s ON b.service_id=s.id
                                   ORDER BY b.created_at DESC LIMIT 10''').fetchall()
        all_prov = conn.execute('''SELECT sp.*,COALESCE(AVG(r.rating),0) as avg_rating,
                                   COUNT(DISTINCT b.id) as booking_count FROM service_providers sp
                                   LEFT JOIN reviews r ON sp.id=r.provider_id
                                   LEFT JOIN bookings b ON sp.id=b.provider_id
                                   WHERE sp.approved=1 GROUP BY sp.id ORDER BY booking_count DESC''').fetchall()
        coupons  = conn.execute('SELECT * FROM coupons ORDER BY created_at DESC').fetchall()
        conn.close()
        return render_template('admin_dashboard.html', pending_providers=pending,
                               kyc_pending=kyc_pend, total_users=tu, total_providers=tp,
                               total_bookings=tb, total_revenue=tr,
                               recent_bookings=recent, all_providers=all_prov, coupons=coupons)
    return redirect(url_for('index'))

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — KYC VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/provider/kyc', methods=['GET','POST'])
def provider_kyc():
    if 'user_id' not in session or session['user_role'] != 'provider':
        return redirect(url_for('index'))
    conn     = get_db()
    provider = conn.execute('SELECT id FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
    if not provider:
        conn.close(); flash('Set up your provider profile first.','warning')
        return redirect(url_for('provider_setup'))
    kyc_docs = conn.execute('SELECT * FROM kyc_documents WHERE provider_id=?',(provider['id'],)).fetchall()
    conn.close()
    if request.method == 'POST':
        doc_type = request.form.get('doc_type','')
        doc_num  = request.form.get('doc_number','')
        file     = request.files.get('doc_file')
        file_url = None
        if file and allowed_file(file.filename):
            fname    = secure_filename(file.filename)
            uname    = f"kyc_{provider['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{fname}"
            fpath    = os.path.join(UPLOAD_FOLDER, 'kyc', uname)
            file.save(fpath)
            file_url = f'kyc/{uname}'
        conn2 = get_db()
        conn2.execute('INSERT INTO kyc_documents(provider_id,doc_type,doc_number,file_url) VALUES(?,?,?,?)',
                      (provider['id'], doc_type, doc_num, file_url))
        conn2.commit(); conn2.close()
        flash('KYC document submitted! Pending admin review. 📋','success')
        return redirect(url_for('dashboard'))
    return render_template('kyc.html', kyc_docs=kyc_docs)

@app.route('/admin/kyc/review/<int:doc_id>/<action>', methods=['POST'])
def admin_kyc_review(doc_id, action):
    if 'user_id' not in session or session['user_role'] != 'admin':
        return redirect(url_for('index'))
    status = 'approved' if action == 'approve' else 'rejected'
    note   = request.form.get('note','')
    conn   = get_db()
    conn.execute('UPDATE kyc_documents SET status=?,admin_note=?,reviewed_at=? WHERE id=?',
                 (status, note, datetime.now(), doc_id))
    if status == 'approved':
        provider_id = conn.execute('SELECT provider_id FROM kyc_documents WHERE id=?',(doc_id,)).fetchone()['provider_id']
        conn.execute('UPDATE service_providers SET is_verified=1 WHERE id=?',(provider_id,))
    conn.commit(); conn.close()
    flash(f'KYC document {status}.','success')
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — COMMISSION SYSTEM (enhanced in bookings + analytics)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/commission')
def admin_commission():
    if 'user_id' not in session or session['user_role'] != 'admin':
        return redirect(url_for('index'))
    conn = get_db()
    data = conn.execute('''SELECT sp.name as provider_name, sp.service_type,
                           COUNT(b.id) as total_bookings,
                           SUM(b.payment_amount) as gross_revenue,
                           SUM(b.commission) as total_commission,
                           SUM(CASE WHEN b.is_emergency=1 THEN b.commission ELSE 0 END) as emergency_commission
                           FROM bookings b
                           JOIN service_providers sp ON b.provider_id=sp.id
                           WHERE b.payment_status='Completed'
                           GROUP BY sp.id ORDER BY total_commission DESC''').fetchall()
    summary = conn.execute('''SELECT SUM(payment_amount) as gross, SUM(commission) as total_comm
                              FROM bookings WHERE payment_status='Completed' ''').fetchone()
    conn.close()
    return render_template('commission.html', commission_data=data, summary=summary)

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — COUPONS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/coupon/validate', methods=['POST'])
def validate_coupon():
    if 'user_id' not in session:
        return jsonify({'ok': False, 'error': 'Not logged in'}), 401
    code   = request.form.get('code','').upper().strip()
    amount = request.form.get('amount', 0, type=float)
    conn   = get_db()
    coupon = conn.execute('''SELECT * FROM coupons WHERE code=? AND is_active=1
                             AND (valid_until IS NULL OR valid_until >= ?)
                             AND used_count < max_uses''',
                          (code, datetime.now().strftime('%Y-%m-%d'))).fetchone()
    if not coupon:
        conn.close()
        return jsonify({'ok': False, 'error': 'Invalid or expired coupon'})
    if amount < coupon['min_order']:
        conn.close()
        return jsonify({'ok': False, 'error': f'Minimum order ₹{coupon["min_order"]:.0f} required'})
    # Check user hasn't already used this coupon
    already = conn.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=? AND user_id=?',
                           (coupon['id'], session['user_id'])).fetchone()
    conn.close()
    if already:
        return jsonify({'ok': False, 'error': 'You have already used this coupon'})
    if coupon['discount_type'] == 'percentage':
        discount = round(amount * coupon['discount_value'] / 100, 2)
    else:
        discount = min(coupon['discount_value'], amount)
    return jsonify({'ok': True, 'discount': discount, 'final': amount - discount,
                    'message': f'Coupon applied! You save ₹{discount:.0f}'})

@app.route('/admin/coupon/add', methods=['POST'])
def admin_add_coupon():
    if 'user_id' not in session or session['user_role'] != 'admin':
        return redirect(url_for('index'))
    conn = get_db()
    try:
        conn.execute('''INSERT INTO coupons(code,discount_type,discount_value,min_order,max_uses,valid_until)
                        VALUES(?,?,?,?,?,?)''',
                     (request.form['code'].upper(), request.form['discount_type'],
                      float(request.form['discount_value']), float(request.form.get('min_order',0)),
                      int(request.form.get('max_uses',100)), request.form.get('valid_until')))
        conn.commit()
        flash('Coupon created! 🎟️','success')
    except sqlite3.IntegrityError:
        flash('Coupon code already exists.','danger')
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/admin/coupon/toggle/<int:coupon_id>')
def toggle_coupon(coupon_id):
    if 'user_id' not in session or session['user_role'] != 'admin':
        return redirect(url_for('index'))
    conn = get_db()
    cur  = conn.execute('SELECT is_active FROM coupons WHERE id=?',(coupon_id,)).fetchone()
    if cur: conn.execute('UPDATE coupons SET is_active=? WHERE id=?',(0 if cur['is_active'] else 1, coupon_id))
    conn.commit(); conn.close()
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — PROVIDER AVAILABILITY TOGGLE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/provider/toggle_availability', methods=['POST'])
def toggle_availability():
    if 'user_id' not in session or session['user_role'] != 'provider':
        return jsonify({'ok': False}), 403
    conn     = get_db()
    provider = conn.execute('SELECT id,is_available FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
    if not provider:
        conn.close(); return jsonify({'ok': False, 'error': 'Profile not found'}), 404
    new_val  = 0 if provider['is_available'] else 1
    conn.execute('UPDATE service_providers SET is_available=? WHERE id=?',(new_val, provider['id']))
    conn.commit(); conn.close()
    status_text = 'Online' if new_val else 'Offline'
    return jsonify({'ok': True, 'is_available': new_val, 'status': status_text})

# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER SETUP & MANAGEMENT (existing, unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/provider/setup', methods=['GET','POST'])
def provider_setup():
    if 'user_id' not in session or session['user_role'] != 'provider':
        return redirect(url_for('index'))
    if request.method == 'POST':
        conn = get_db()
        conn.execute('''INSERT INTO service_providers
                        (user_id,name,service_type,phone,city,locality,experience,
                         latitude,longitude,description) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                     (session['user_id'],request.form['name'],request.form['service_type'],
                      request.form['phone'],request.form['city'],request.form['locality'],
                      request.form.get('experience',0),request.form.get('latitude'),
                      request.form.get('longitude'),request.form.get('description','')))
        conn.commit(); conn.close()
        flash('Profile submitted! Waiting for admin approval.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('provider_setup.html')

@app.route('/provider/add_service', methods=['POST'])
def add_service():
    if 'user_id' not in session or session['user_role'] != 'provider':
        return redirect(url_for('index'))
    conn     = get_db()
    provider = conn.execute('SELECT id FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
    if provider:
        conn.execute('INSERT INTO services(provider_id,service_name,price,description) VALUES(?,?,?,?)',
                     (provider['id'],request.form['service_name'],request.form['price'],request.form['description']))
        conn.commit()
        flash('Service added!','success')
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/provider/upload_photo', methods=['POST'])
def upload_provider_photo():
    if 'user_id' not in session or session['user_role'] != 'provider':
        return jsonify({'success':False,'error':'Unauthorized'}),401
    if 'photo' not in request.files:
        return jsonify({'success':False,'error':'No file'}),400
    file = request.files['photo']
    if file and allowed_file(file.filename):
        conn     = get_db()
        provider = conn.execute('SELECT id FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
        if not provider:
            conn.close(); return jsonify({'success':False,'error':'Provider not found'}),404
        fname = secure_filename(file.filename)
        uname = f"{provider['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{fname}"
        fpath = os.path.join(UPLOAD_FOLDER, 'providers', uname)
        try:
            file.save(fpath)
            conn.execute('INSERT INTO provider_photos(provider_id,photo_url,caption,is_profile) VALUES(?,?,?,?)',
                         (provider['id'],f'providers/{uname}',request.form.get('caption',''),
                          1 if request.form.get('is_profile')=='1' else 0))
            conn.commit(); conn.close()
            flash('Photo uploaded!','success')
            return jsonify({'success':True})
        except Exception as e:
            conn.close(); return jsonify({'success':False,'error':str(e)}),500
    return jsonify({'success':False,'error':'Invalid file type'}),400

@app.route('/provider/delete_photo/<int:photo_id>', methods=['POST'])
def delete_provider_photo(photo_id):
    if 'user_id' not in session or session['user_role'] != 'provider':
        return redirect(url_for('index'))
    conn     = get_db()
    provider = conn.execute('SELECT id FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
    if provider:
        photo = conn.execute('SELECT * FROM provider_photos WHERE id=? AND provider_id=?',
                             (photo_id, provider['id'])).fetchone()
        if photo:
            fp = os.path.join(UPLOAD_FOLDER, photo['photo_url'])
            if os.path.exists(fp): os.remove(fp)
            conn.execute('DELETE FROM provider_photos WHERE id=?',(photo_id,))
            conn.commit()
            flash('Photo deleted.','success')
    conn.close()
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH & DETAIL
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/search', methods=['GET','POST'])
def search():
    if 'user_id' not in session: return redirect(url_for('login'))
    providers   = []
    user_city   = session.get('city', '')
    searched    = False
    if request.method == 'POST':
        city         = request.form.get('city', user_city).strip()
        locality     = request.form.get('locality','').strip()
        service_type = request.form.get('service_type','').strip()
        min_rating   = request.form.get('min_rating', 0, type=float)
        emergency_only = request.form.get('emergency_only') == '1'
        verified_only  = request.form.get('verified_only')  == '1'
        searched = True
        save_search_history(session['user_id'],{'city':city,'locality':locality,'service_type':service_type})
        conn   = get_db()
        q      = '''SELECT sp.*,COALESCE(AVG(r.rating),0) as avg_rating,
                    COUNT(DISTINCT r.id) as review_count
                    FROM service_providers sp LEFT JOIN reviews r ON sp.id=r.provider_id
                    WHERE sp.approved=1 AND sp.is_available=1'''
        params = []
        if city:           q += ' AND sp.city LIKE ?';          params.append(f'%{city}%')
        if locality:       q += ' AND sp.locality LIKE ?';      params.append(f'%{locality}%')
        if service_type:   q += ' AND sp.service_type LIKE ?';  params.append(f'%{service_type}%')
        if emergency_only: q += ' AND sp.is_emergency=1'
        if verified_only:  q += ' AND sp.is_verified=1'
        q += ' GROUP BY sp.id'
        if min_rating > 0: q += f' HAVING avg_rating >= {min_rating}'
        q += ' ORDER BY sp.trust_score DESC,avg_rating DESC'
        providers = conn.execute(q, params).fetchall()
        conn.close()
    return render_template('search.html', providers=providers, user_city=user_city, searched=searched)

@app.route('/provider/<int:provider_id>')
def provider_detail(provider_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    track_provider_view(provider_id, session['user_id'])
    conn     = get_db()
    provider = conn.execute('''SELECT sp.*,COALESCE(AVG(r.rating),0) as avg_rating,
                               COUNT(DISTINCT r.id) as review_count
                               FROM service_providers sp LEFT JOIN reviews r ON sp.id=r.provider_id
                               WHERE sp.id=? GROUP BY sp.id''',(provider_id,)).fetchone()
    services = conn.execute('SELECT * FROM services WHERE provider_id=?',(provider_id,)).fetchall()
    reviews  = conn.execute('''SELECT r.*,u.name as customer_name FROM reviews r
                               JOIN users u ON r.user_id=u.id WHERE r.provider_id=?
                               ORDER BY r.created_at DESC''',(provider_id,)).fetchall()
    photos     = conn.execute('SELECT * FROM provider_photos WHERE provider_id=? ORDER BY is_profile DESC',(provider_id,)).fetchall()
    suggestion = get_ai_price_suggestion(provider['service_type'], provider['city']) if provider else None
    # Can this user review? Only if they have a completed booking with this provider
    can_review = False
    user_review = None
    if 'user_id' in session and session.get('user_role') == 'customer':
        completed = conn.execute(
            "SELECT id FROM bookings WHERE user_id=? AND provider_id=? AND status='Completed' LIMIT 1",
            (session['user_id'], provider_id)
        ).fetchone()
        can_review = completed is not None
        user_review = conn.execute(
            'SELECT * FROM reviews WHERE user_id=? AND provider_id=?',
            (session['user_id'], provider_id)
        ).fetchone()
    conn.close()
    return render_template('provider_detail.html', provider=provider, services=services,
                           reviews=reviews, photos=photos, price_suggestion=suggestion,
                           can_review=can_review, user_review=user_review)

@app.route('/review/<int:provider_id>', methods=['POST'])
def add_review(provider_id):
    if 'user_id' not in session or session['user_role'] != 'customer':
        return redirect(url_for('index'))
    conn = get_db()
    # Only allow review if user has a completed booking with this provider
    completed = conn.execute(
        "SELECT id FROM bookings WHERE user_id=? AND provider_id=? AND status='Completed' LIMIT 1",
        (session['user_id'], provider_id)
    ).fetchone()
    if not completed:
        conn.close()
        flash('You can only review providers after a completed booking.','warning')
        return redirect(url_for('provider_detail', provider_id=provider_id))
    # Prevent duplicate review per booking
    already = conn.execute(
        'SELECT id FROM reviews WHERE user_id=? AND provider_id=?',
        (session['user_id'], provider_id)
    ).fetchone()
    if already:
        conn.execute('UPDATE reviews SET rating=?,comment=? WHERE user_id=? AND provider_id=?',
                     (request.form['rating'],request.form['comment'],session['user_id'],provider_id))
    else:
        conn.execute('INSERT INTO reviews(user_id,provider_id,rating,comment) VALUES(?,?,?,?)',
                     (session['user_id'],provider_id,request.form['rating'],request.form['comment']))
    conn.commit()
    ts = calculate_trust_score(provider_id)
    conn.execute('UPDATE service_providers SET trust_score=? WHERE id=?',(ts,provider_id))
    # Notify provider of new review
    sp = conn.execute('SELECT user_id,name FROM service_providers WHERE id=?',(provider_id,)).fetchone()
    conn.commit(); conn.close()
    if sp:
        notify(sp['user_id'], '⭐ New Review',
               f'{session["user_name"]} left a {request.form["rating"]}-star review on your profile.',
               'review', url_for('provider_detail', provider_id=provider_id))
    flash('Review submitted! ⭐','success')
    return redirect(url_for('provider_detail', provider_id=provider_id))

# ═══════════════════════════════════════════════════════════════════════════════
# BOOKINGS (with coupon + wallet support)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/book/<int:service_id>', methods=['POST'])
def book_service(service_id):
    if 'user_id' not in session or session['user_role'] != 'customer':
        return redirect(url_for('index'))
    conn = get_db()
    svc  = conn.execute('''SELECT s.*,sp.name as provider_name FROM services s
                           JOIN service_providers sp ON s.provider_id=sp.id WHERE s.id=?''',(service_id,)).fetchone()
    if svc:
        is_emg   = 1 if request.form.get('is_emergency') else 0
        amt      = svc['price'] or 0
        # Apply coupon if any
        coupon_code   = request.form.get('coupon_code','').upper().strip()
        discount_amt  = 0
        if coupon_code:
            coupon = conn.execute('''SELECT * FROM coupons WHERE code=? AND is_active=1
                                     AND (valid_until IS NULL OR valid_until >= ?)
                                     AND used_count < max_uses''',
                                  (coupon_code, datetime.now().strftime('%Y-%m-%d'))).fetchone()
            if coupon and amt >= coupon['min_order']:
                already = conn.execute('SELECT 1 FROM coupon_uses WHERE coupon_id=? AND user_id=?',
                                       (coupon['id'], session['user_id'])).fetchone()
                if not already:
                    discount_amt = round(amt * coupon['discount_value']/100, 2) if coupon['discount_type']=='percentage' else min(coupon['discount_value'], amt)
                    conn.execute('UPDATE coupons SET used_count=used_count+1 WHERE id=?',(coupon['id'],))
        final_amt = max(amt - discount_amt, 0)
        comm      = final_amt * (0.20 if is_emg else 0.15)
        # Use wallet if requested
        use_wallet  = request.form.get('use_wallet') == '1'
        wallet_used = 0
        if use_wallet:
            wallet = conn.execute('SELECT balance FROM wallets WHERE user_id=?',(session['user_id'],)).fetchone()
            if wallet and wallet['balance'] > 0:
                wallet_used = min(wallet['balance'], final_amt)
                final_amt   = max(final_amt - wallet_used, 0)
        cur = conn.execute('''INSERT INTO bookings
                              (user_id,provider_id,service_id,booking_date,address,notes,
                               payment_amount,is_emergency,commission,payment_status,coupon_code,discount_amount)
                              VALUES(?,?,?,?,?,?,?,?,?,'Pending',?,?)''',
                           (session['user_id'],svc['provider_id'],service_id,
                            request.form['booking_date'],request.form.get('address',''),
                            request.form.get('notes',''),final_amt,is_emg,comm,
                            coupon_code or None, discount_amt))
        bid = cur.lastrowid
        if coupon_code and discount_amt > 0:
            coupon_row = conn.execute('SELECT id FROM coupons WHERE code=?',(coupon_code,)).fetchone()
            if coupon_row:
                conn.execute('INSERT INTO coupon_uses(coupon_id,user_id,booking_id) VALUES(?,?,?)',
                             (coupon_row['id'], session['user_id'], bid))
        if wallet_used > 0:
            conn.execute('UPDATE wallets SET balance=balance-? WHERE user_id=?',(wallet_used, session['user_id']))
            conn.execute('INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)',
                         (session['user_id'], wallet_used, 'debit', f'Booking #{bid} wallet payment'))
        conn.commit(); conn.close()
        flash(f'Booking created!{" Coupon applied!" if discount_amt>0 else ""} Please complete payment.','info')
        return redirect(url_for('payment_page', booking_id=bid))
    conn.close()
    flash('Service not found!','danger')
    return redirect(url_for('search'))

@app.route('/payment/<int:booking_id>')
def payment_page(booking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn    = get_db()
    booking = conn.execute('''SELECT b.*,s.service_name,s.price,sp.name as provider_name,
                               u.name as customer_name,u.email FROM bookings b
                               JOIN service_providers sp ON b.provider_id=sp.id
                               LEFT JOIN services s ON b.service_id=s.id
                               JOIN users u ON b.user_id=u.id
                               WHERE b.id=? AND b.user_id=?''',(booking_id,session['user_id'])).fetchone()
    wallet  = get_or_create_wallet(session['user_id'])
    conn.close()
    if not booking:
        flash('Booking not found!','danger'); return redirect(url_for('dashboard'))
    if booking['payment_status'] == 'Completed':
        flash('Payment already completed!','info'); return redirect(url_for('dashboard'))
    return render_template('payment.html', booking=booking, wallet=wallet)

@app.route('/payment/verify', methods=['POST'])
def verify_payment():
    if 'user_id' not in session: return redirect(url_for('login'))
    bid  = request.form.get('booking_id')
    conn = get_db()
    conn.execute("UPDATE bookings SET payment_status='Completed',payment_id=?,order_id=?,status='Pending' WHERE id=?",
                 (request.form.get('payment_id'),request.form.get('order_id'),bid))
    # Credit provider wallet with their share (payment - commission)
    booking = conn.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone()
    if booking:
        provider = conn.execute('SELECT user_id FROM service_providers WHERE id=?',(booking['provider_id'],)).fetchone()
        if provider:
            provider_earning = (booking['payment_amount'] or 0) - (booking['commission'] or 0)
            if provider_earning > 0:
                conn.execute('INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,0)',(provider['user_id'],))
                conn.execute('UPDATE wallets SET balance=balance+? WHERE user_id=?',(provider_earning, provider['user_id']))
                conn.execute('INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)',
                             (provider['user_id'], provider_earning, 'credit', f'Booking #{bid} payment received'))
    conn.commit()
    # Notify provider of new payment
    if booking:
        provider_row = conn.execute('SELECT user_id FROM service_providers WHERE id=?',(booking['provider_id'],)).fetchone()
        if provider_row:
            notify(provider_row['user_id'],
                   '💰 Payment Received',
                   f'Payment confirmed for booking #{bid}. Check your wallet.',
                   'payment', url_for('wallet_page'))
        # Notify customer
        notify(booking['user_id'],
               '✅ Payment Successful',
               f'Your booking #{bid} is confirmed and payment processed.',
               'payment', url_for('dashboard'))
    conn.close()
    flash('Payment successful! Booking confirmed. ✅','success')
    return redirect(url_for('dashboard'))

@app.route('/booking/update/<int:booking_id>/<status>')
def update_booking(booking_id, status):
    if 'user_id' not in session or session['user_role'] not in ['provider','admin']:
        return redirect(url_for('index'))
    conn = get_db()
    conn.execute('UPDATE bookings SET status=? WHERE id=?',(status,booking_id))
    # Notify the customer
    booking = conn.execute(
        'SELECT b.user_id, s.service_name, sp.name as pname FROM bookings b '
        'LEFT JOIN services s ON b.service_id=s.id '
        'JOIN service_providers sp ON b.provider_id=sp.id WHERE b.id=?', (booking_id,)
    ).fetchone()
    conn.commit(); conn.close()
    if booking:
        icons = {'Accepted':'✅','Completed':'🎉','Rejected':'❌','Cancelled':'🚫'}
        icon  = icons.get(status,'ℹ️')
        notify(booking['user_id'],
               f'{icon} Booking {status}',
               f'{booking["pname"]} has {status.lower()} your {booking["service_name"] or "service"} booking.',
               'booking', url_for('dashboard'))
    flash(f'Booking marked as {status}.','success')
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/approve/<int:provider_id>')
def approve_provider(provider_id):
    if 'user_id' not in session or session['user_role'] != 'admin': return redirect(url_for('index'))
    conn = get_db()
    conn.execute('UPDATE service_providers SET approved=1 WHERE id=?',(provider_id,))
    conn.commit(); conn.close()
    flash('Provider approved! ✅','success')
    return redirect(url_for('dashboard'))

@app.route('/admin/reject/<int:provider_id>')
def reject_provider(provider_id):
    if 'user_id' not in session or session['user_role'] != 'admin': return redirect(url_for('index'))
    conn = get_db()
    conn.execute('DELETE FROM service_providers WHERE id=? AND approved=0',(provider_id,))
    conn.commit(); conn.close()
    flash('Provider rejected.','warning')
    return redirect(url_for('dashboard'))

@app.route('/admin/toggle_featured/<int:provider_id>')
def toggle_featured(provider_id):
    if 'user_id' not in session or session['user_role'] != 'admin': return redirect(url_for('index'))
    conn = get_db()
    cur  = conn.execute('SELECT is_featured FROM service_providers WHERE id=?',(provider_id,)).fetchone()
    if cur: conn.execute('UPDATE service_providers SET is_featured=? WHERE id=?',(0 if cur['is_featured'] else 1, provider_id))
    conn.commit(); conn.close()
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — REAL-TIME TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/tracking/<int:booking_id>')
def tracking_page(booking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn    = get_db()
    booking = conn.execute('''SELECT b.*,sp.name as provider_name,sp.latitude as sp_lat,
                               sp.longitude as sp_lng,s.service_name
                               FROM bookings b JOIN service_providers sp ON b.provider_id=sp.id
                               LEFT JOIN services s ON b.service_id=s.id
                               WHERE b.id=? AND (b.user_id=? OR sp.user_id=?)''',
                           (booking_id, session['user_id'], session['user_id'])).fetchone()
    conn.close()
    if not booking: flash('Booking not found!','danger'); return redirect(url_for('dashboard'))
    return render_template('tracking.html', booking=booking)



# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — SUBSCRIPTION SERVICES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/subscriptions')
def subscriptions_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn   = get_db()
    plans  = conn.execute('SELECT * FROM subscription_plans WHERE is_active=1').fetchall()
    active = conn.execute('''SELECT us.*,sp.name as plan_name,sp.features FROM user_subscriptions us
                             JOIN subscription_plans sp ON us.plan_id=sp.id
                             WHERE us.user_id=? AND us.ends_at > ? AND us.payment_status='active'
                             ORDER BY us.ends_at DESC LIMIT 1''',
                          (session['user_id'], datetime.now())).fetchone()
    conn.close()
    return render_template('subscriptions.html', plans=plans, active_subscription=active)

@app.route('/subscriptions/subscribe/<int:plan_id>', methods=['POST'])
def subscribe(plan_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    plan = conn.execute('SELECT * FROM subscription_plans WHERE id=? AND is_active=1',(plan_id,)).fetchone()
    if not plan: conn.close(); flash('Plan not found.','danger'); return redirect(url_for('subscriptions_page'))
    ends_at = datetime.now() + timedelta(days=plan['duration_days'])
    conn.execute('INSERT INTO user_subscriptions(user_id,plan_id,ends_at) VALUES(?,?,?)',
                 (session['user_id'], plan_id, ends_at))
    conn.commit(); conn.close()
    flash(f'Subscribed to {plan["name"]} plan! Valid until {ends_at.strftime("%d %b %Y")} 🎉','success')
    return redirect(url_for('dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — WALLET
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/wallet')
def wallet_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn   = get_db()
    wallet = get_or_create_wallet(session['user_id'])
    txns   = conn.execute('''SELECT * FROM wallet_transactions WHERE user_id=?
                             ORDER BY created_at DESC LIMIT 30''',(session['user_id'],)).fetchall()
    conn.close()
    return render_template('wallet.html', wallet=wallet, transactions=txns)

@app.route('/wallet/add', methods=['POST'])
def wallet_add_funds():
    if 'user_id' not in session: return redirect(url_for('login'))
    amount = request.form.get('amount', 0, type=float)
    if amount <= 0: flash('Invalid amount.','danger'); return redirect(url_for('wallet_page'))
    wallet_credit(session['user_id'], amount, 'Wallet top-up')
    flash(f'₹{amount:.0f} added to your wallet! 💰','success')
    return redirect(url_for('wallet_page'))

@app.route('/wallet/balance')
def wallet_balance():
    if 'user_id' not in session: return jsonify({'ok':False}),401
    wallet = get_or_create_wallet(session['user_id'])
    return jsonify({'ok':True,'balance': wallet['balance']})

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — ENTERPRISE ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/enterprise')
def enterprise_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn     = get_db()
    accounts = conn.execute('SELECT * FROM enterprise_accounts ORDER BY created_at DESC').fetchall() if session['user_role']=='admin' else []
    conn.close()
    return render_template('enterprise.html', accounts=accounts)

@app.route('/enterprise/register', methods=['POST'])
def enterprise_register():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    try:
        conn.execute('''INSERT INTO enterprise_accounts(company_name,contact_person,email,phone,city,gst_number)
                        VALUES(?,?,?,?,?,?)''',
                     (request.form['company_name'],request.form['contact_person'],
                      request.form['email'],request.form.get('phone',''),
                      request.form.get('city',''),request.form.get('gst_number','')))
        conn.commit()
        flash('Enterprise account registered! Our team will contact you within 24 hours. 🏢','success')
    except sqlite3.IntegrityError:
        flash('This email is already registered as an enterprise account.','danger')
    conn.close()
    return redirect(url_for('enterprise_page'))

@app.route('/admin/enterprise/update/<int:account_id>', methods=['POST'])
def update_enterprise(account_id):
    if 'user_id' not in session or session['user_role'] != 'admin': return redirect(url_for('index'))
    conn = get_db()
    conn.execute('UPDATE enterprise_accounts SET status=?,credit_limit=? WHERE id=?',
                 (request.form.get('status','active'),request.form.get('credit_limit',50000),account_id))
    conn.commit(); conn.close()
    flash('Enterprise account updated.','success')
    return redirect(url_for('enterprise_page'))

# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL FEED
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/feed', methods=['GET','POST'])
def feed():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    if request.method == 'POST':
        conn.execute('INSERT INTO local_feed(user_id,content,category,city) VALUES(?,?,?,?)',
                     (session['user_id'],request.form['content'],
                      request.form.get('category','general'),session.get('city','')))
        conn.commit()
        flash('Post shared! 🎉','success')
    posts = conn.execute('''SELECT f.*,u.name as user_name FROM local_feed f
                            JOIN users u ON f.user_id=u.id ORDER BY f.created_at DESC''').fetchall()
    conn.close()
    return render_template('feed.html', posts=posts)

@app.route('/feed/like/<int:post_id>')
def like_post(post_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    conn.execute('UPDATE local_feed SET likes=likes+1 WHERE id=?',(post_id,))
    conn.commit(); conn.close()
    return redirect(url_for('feed'))

@app.route('/feed/delete/<int:post_id>')
def delete_post(post_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    post = conn.execute('SELECT * FROM local_feed WHERE id=?',(post_id,)).fetchone()
    if post and (post['user_id']==session['user_id'] or session['user_role']=='admin'):
        conn.execute('DELETE FROM local_feed WHERE id=?',(post_id,))
        conn.commit()
        flash('Post deleted.','info')
    conn.close()
    return redirect(url_for('feed'))

# ═══════════════════════════════════════════════════════════════════════════════
# ONBOARDING / RECOMMENDATIONS / ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/onboarding')
def onboarding():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn      = get_db()
    emergency = conn.execute('SELECT * FROM emergency_contacts').fetchall()
    cleaning  = conn.execute("SELECT * FROM service_providers WHERE service_type LIKE '%Clean%' AND approved=1 LIMIT 3").fetchall()
    conn.close()
    return render_template('onboarding.html', emergency=emergency, cleaning=cleaning)

@app.route('/recommendations')
def recommendations():
    if 'user_id' not in session: return redirect(url_for('login'))
    return render_template('recommendations.html', providers=get_ai_recommendations(session['user_id']))

@app.route('/provider/analytics')
def provider_analytics():
    if 'user_id' not in session or session['user_role'] != 'provider': return redirect(url_for('index'))
    conn     = get_db()
    provider = conn.execute('SELECT id FROM service_providers WHERE user_id=?',(session['user_id'],)).fetchone()
    conn.close()
    if provider:
        return render_template('analytics.html', analytics=get_provider_analytics(provider['id']))
    flash('Provider profile not found!','danger')
    return redirect(url_for('dashboard'))

# ── PHASE 2: AI Pricing API endpoint ─────────────────────────────────────────
@app.route('/api/pricing_suggestion')
def api_pricing_suggestion():
    if 'user_id' not in session: return jsonify({'ok':False}),401
    svc  = request.args.get('service_type','')
    city = request.args.get('city', session.get('city',''))
    return jsonify({'ok':True, **get_ai_price_suggestion(svc, city)})

# ═══════════════════════════════════════════════════════════════════════════════
# REAL-TIME CHAT (SSE + HTTP + Polling fallback)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/chat/<int:booking_id>')
def chat(booking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn    = get_db()
    booking = conn.execute('''SELECT b.*,u1.name as customer_name,u2.name as provider_name,
                               sp.user_id as provider_user_id,b.user_id as customer_user_id
                               FROM bookings b JOIN users u1 ON b.user_id=u1.id
                               JOIN service_providers sp ON b.provider_id=sp.id
                               JOIN users u2 ON sp.user_id=u2.id WHERE b.id=?''',(booking_id,)).fetchone()
    if not booking:
        flash('Booking not found!','danger'); conn.close(); return redirect(url_for('dashboard'))
    messages = conn.execute('''SELECT cm.*,u.name as sender_name FROM chat_messages cm
                               JOIN users u ON cm.sender_id=u.id WHERE cm.booking_id=?
                               ORDER BY cm.created_at ASC''',(booking_id,)).fetchall()
    conn.execute('UPDATE chat_messages SET is_read=1 WHERE booking_id=? AND receiver_id=?',
                 (booking_id,session['user_id']))
    conn.commit(); conn.close()
    other = (booking['provider_name'] if session['user_id']==booking['customer_user_id']
             else booking['customer_name'])
    return render_template('chat.html', booking=booking, messages=messages,
                           other_user_name=other, booking_id=booking_id)

# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/notifications")
def notifications_page():
    if "user_id" not in session: return redirect(url_for("login"))
    conn   = get_db()
    notifs = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (session["user_id"],)
    ).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session["user_id"],))
    conn.commit(); conn.close()
    return render_template("notifications.html", notifications=notifs)

@app.route("/notifications/count")
def notifications_count():
    if "user_id" not in session: return jsonify({"count":0})
    conn  = get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0",
        (session["user_id"],)
    ).fetchone()["c"]
    conn.close()
    return jsonify({"count": count})

@app.route("/notifications/clear", methods=["POST"])
def notifications_clear():
    if "user_id" not in session: return redirect(url_for("login"))
    conn = get_db()
    conn.execute("DELETE FROM notifications WHERE user_id=?", (session["user_id"],))
    conn.commit(); conn.close()
    flash("All notifications cleared.", "info")
    return redirect(url_for("notifications_page"))

# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE EDIT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/profile", methods=["GET","POST"])
def edit_profile():
    if "user_id" not in session: return redirect(url_for("login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        name     = request.form.get("name","").strip()
        phone    = request.form.get("phone","").strip()
        city     = request.form.get("city","").strip()
        locality = request.form.get("locality","").strip()
        conn.execute("UPDATE users SET name=?,phone=?,city=?,locality=? WHERE id=?",
                     (name, phone, city, locality, session["user_id"]))
        conn.commit()
        session["user_name"] = name
        session["city"]      = city
        new_pw = request.form.get("new_password","").strip()
        if new_pw:
            if len(new_pw) < 6:
                conn.close()
                flash("Password must be at least 6 characters.", "danger")
                return redirect(url_for("edit_profile"))
            if not check_password_hash(user["password"], request.form.get("current_password","")):
                conn.close()
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("edit_profile"))
            conn2 = get_db()
            conn2.execute("UPDATE users SET password=? WHERE id=?",
                          (generate_password_hash(new_pw), session["user_id"]))
            conn2.commit(); conn2.close()
            flash("Password updated successfully.", "success")
        conn.close()
        flash("Profile updated! ✅", "success")
        return redirect(url_for("edit_profile"))
    conn.close()
    return render_template("edit_profile.html", user=user)

# ═══════════════════════════════════════════════════════════════════════════════
# FORGOT / RESET PASSWORD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        conn  = get_db()
        user  = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            token   = secrets.token_urlsafe(32)
            expires = datetime.now() + timedelta(hours=1)
            conn.execute("INSERT INTO password_resets(user_id,token,expires_at) VALUES(?,?,?)",
                         (user["id"], token, expires))
            conn.commit()
            reset_url = url_for("reset_password", token=token, _external=True)
            sent = False
            if EMAIL_USER and EMAIL_PASSWORD:
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = "Local Link Password Reset"
                    msg["From"]    = EMAIL_FROM
                    msg["To"]      = email
                    body = f"Hi {user['name']}, reset your password: {reset_url} (expires in 1 hour)"
                    msg.attach(MIMEText(body, "plain"))
                    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
                        s.starttls(); s.login(EMAIL_USER, EMAIL_PASSWORD)
                        s.sendmail(EMAIL_USER, email, msg.as_string())
                    sent = True
                except Exception as e:
                    print(f"[RESET EMAIL ERROR] {e}")
            conn.close()
            if sent:
                flash("Password reset link sent to your email.", "success")
            else:
                flash(f"Demo mode — reset link: {reset_url}", "info")
        else:
            conn.close()
            flash("If that email exists, a reset link will be sent.", "info")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")

@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    conn = get_db()
    rec  = conn.execute(
        "SELECT * FROM password_resets WHERE token=? AND used=0 AND expires_at > ?",
        (token, datetime.now())
    ).fetchone()
    if not rec:
        conn.close()
        flash("Reset link is invalid or has expired.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password","").strip()
        if len(pw) < 6:
            conn.close()
            flash("Password must be at least 6 characters.", "danger")
            return render_template("reset_password.html", token=token)
        conn.execute("UPDATE users SET password=? WHERE id=?",
                     (generate_password_hash(pw), rec["user_id"]))
        conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (rec["id"],))
        conn.commit(); conn.close()
        flash("Password reset successfully! Please log in.", "success")
        return redirect(url_for("login"))
    conn.close()
    return render_template("reset_password.html", token=token)

# ═══════════════════════════════════════════════════════════════════════════════
# BOOKING CANCELLATION (customer)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/booking/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    if "user_id" not in session: return redirect(url_for("login"))
    conn    = get_db()
    booking = conn.execute(
        "SELECT b.*,sp.user_id as provider_uid,sp.name as pname,s.service_name "
        "FROM bookings b JOIN service_providers sp ON b.provider_id=sp.id "
        "LEFT JOIN services s ON b.service_id=s.id WHERE b.id=? AND b.user_id=?",
        (booking_id, session["user_id"])
    ).fetchone()
    if not booking:
        conn.close(); flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))
    if booking["status"] in ("Completed","Cancelled"):
        conn.close(); flash(f"Cannot cancel a {booking['status'].lower()} booking.", "warning")
        return redirect(url_for("dashboard"))
    conn.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (booking_id,))
    if booking["payment_status"] == "Completed" and booking["payment_amount"]:
        conn.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,0)", (session["user_id"],))
        conn.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?",
                     (booking["payment_amount"], session["user_id"]))
        conn.execute("INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)",
                     (session["user_id"], booking["payment_amount"], "credit",
                      f"Refund for cancelled booking #{booking_id}"))
        provider_earning = (booking["payment_amount"] or 0) - (booking["commission"] or 0)
        conn.execute("UPDATE wallets SET balance=MAX(0,balance-?) WHERE user_id=?",
                     (provider_earning, booking["provider_uid"]))
        conn.execute("INSERT INTO wallet_transactions(user_id,amount,type,description) VALUES(?,?,?,?)",
                     (booking["provider_uid"], provider_earning, "debit",
                      f"Booking #{booking_id} cancelled by customer"))
    conn.commit()
    notify(booking["provider_uid"], "Booking Cancelled",
           f"{session['user_name']} cancelled their {booking['service_name'] or 'service'} booking.",
           "booking", url_for("dashboard"))
    conn.close()
    flash("Booking cancelled." + (" Amount refunded to your wallet." if booking["payment_status"]=="Completed" else ""), "info")
    return redirect(url_for("dashboard"))

# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE EDIT & DELETE (provider)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/provider/edit_service/<int:service_id>", methods=["POST"])
def edit_service(service_id):
    if "user_id" not in session or session["user_role"] != "provider":
        return redirect(url_for("index"))
    conn     = get_db()
    provider = conn.execute("SELECT id FROM service_providers WHERE user_id=?", (session["user_id"],)).fetchone()
    if provider:
        conn.execute(
            "UPDATE services SET service_name=?,price=?,description=? WHERE id=? AND provider_id=?",
            (request.form["service_name"], request.form["price"],
             request.form["description"], service_id, provider["id"])
        )
        conn.commit()
        flash("Service updated!", "success")
    conn.close()
    return redirect(url_for("dashboard"))

@app.route("/provider/delete_service/<int:service_id>", methods=["POST"])
def delete_service(service_id):
    if "user_id" not in session or session["user_role"] != "provider":
        return redirect(url_for("index"))
    conn     = get_db()
    provider = conn.execute("SELECT id FROM service_providers WHERE user_id=?", (session["user_id"],)).fetchone()
    if provider:
        in_use = conn.execute("SELECT 1 FROM bookings WHERE service_id=? LIMIT 1", (service_id,)).fetchone()
        if in_use:
            conn.close()
            flash("Cannot delete a service that has existing bookings.", "warning")
            return redirect(url_for("dashboard"))
        conn.execute("DELETE FROM services WHERE id=? AND provider_id=?", (service_id, provider["id"]))
        conn.commit()
        flash("Service deleted.", "info")
    conn.close()
    return redirect(url_for("dashboard"))

# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET EVENTS — Chat
# ═══════════════════════════════════════════════════════════════════════════════

@socketio.on('join_chat')
def ws_join_chat(data):
    """Client joins the room for a booking's chat."""
    booking_id = data.get('booking_id')
    if 'user_id' not in session or not booking_id:
        return
    room = f'chat_{booking_id}'
    join_room(room)
    emit('status', {'msg': 'joined', 'room': room})

@socketio.on('leave_chat')
def ws_leave_chat(data):
    booking_id = data.get('booking_id')
    if booking_id:
        leave_room(f'chat_{booking_id}')

@socketio.on('send_chat_message')
def ws_send_chat_message(data):
    """Receive a message from a client, persist it, broadcast to room."""
    if 'user_id' not in session:
        return
    booking_id = int(data.get('booking_id', 0))
    text       = (data.get('message') or '').strip()
    if not booking_id or not text:
        return

    conn    = get_db()
    booking = conn.execute(
        'SELECT b.user_id, sp.user_id as provider_user_id '
        'FROM bookings b JOIN service_providers sp ON b.provider_id=sp.id '
        'WHERE b.id=?', (booking_id,)
    ).fetchone()
    if not booking:
        conn.close(); return

    sid = session['user_id']
    rid = booking['provider_user_id'] if sid == booking['user_id'] else booking['user_id']

    cur = conn.execute(
        'INSERT INTO chat_messages(sender_id,receiver_id,booking_id,message) VALUES(?,?,?,?)',
        (sid, rid, booking_id, text)
    )
    msg_id = cur.lastrowid
    conn.commit(); conn.close()

    ts = datetime.now().strftime('%H:%M')
    payload = {
        'id':          msg_id,
        'sender_id':   sid,
        'sender_name': session['user_name'],
        'message':     text,
        'timestamp':   ts,
    }
    # Broadcast to everyone in the chat room (including sender for confirmation)
    emit('new_message', payload, room=f'chat_{booking_id}')

@socketio.on('typing')
def ws_typing(data):
    """Forward typing indicator to the other person in the room."""
    if 'user_id' not in session:
        return
    booking_id = data.get('booking_id')
    emit('typing', {'sender_id': session['user_id'], 'sender_name': session['user_name']},
         room=f'chat_{booking_id}', include_self=False)

# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET EVENTS — Live Tracking
# ═══════════════════════════════════════════════════════════════════════════════

@socketio.on('join_tracking')
def ws_join_tracking(data):
    """Customer joins the tracking room; provider joins to broadcast."""
    booking_id = data.get('booking_id')
    if 'user_id' not in session or not booking_id:
        return
    room = f'tracking_{booking_id}'
    join_room(room)
    # If customer just joined, send last known location immediately
    if session.get('user_role') == 'customer':
        conn    = get_db()
        booking = conn.execute(
            'SELECT provider_lat, provider_lng FROM bookings WHERE id=?', (booking_id,)
        ).fetchone()
        conn.close()
        if booking and booking['provider_lat']:
            emit('location_update', {
                'lat':       booking['provider_lat'],
                'lng':       booking['provider_lng'],
                'timestamp': 'last known',
            })

@socketio.on('update_location')
def ws_update_location(data):
    """Provider emits their GPS coords; server persists + broadcasts to room."""
    if 'user_id' not in session or session.get('user_role') != 'provider':
        return
    booking_id = int(data.get('booking_id', 0))
    lat        = data.get('lat')
    lng        = data.get('lng')
    if not booking_id or lat is None or lng is None:
        return

    conn = get_db()
    conn.execute('UPDATE bookings SET provider_lat=?,provider_lng=? WHERE id=?',
                 (lat, lng, booking_id))
    conn.commit(); conn.close()

    ts = datetime.now().strftime('%H:%M:%S')
    emit('location_update', {'lat': lat, 'lng': lng, 'timestamp': ts},
         room=f'tracking_{booking_id}', include_self=False)

# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # use_reloader=False — stops Flask watching site-packages (important!)
    socketio.run(app, debug=True, port=5000, use_reloader=False, allow_unsafe_werkzeug=True)
