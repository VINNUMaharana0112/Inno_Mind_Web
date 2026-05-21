from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify, g, abort, send_from_directory
import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import re
import random
import hashlib
from datetime import datetime, timedelta
import razorpay
import math

app = Flask(__name__)
app.secret_key = "inno_mind_super_secret_key" 

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")
CLOUD_BUCKET_NAME = "psc-smart-preparation-app"

# ================= SMART FORMATTING ENGINE =================
def format_smart_tags(text):
    if not text:
        return ""
    # SECURE PDF FIX: Route clicks to our new secure Canvas renderer
    pdf_pattern = r'\[📄 Attached PDF: (https?://[^\s\]]+)\]'
    pdf_html = r"""<br><button onclick="openFlipbookModal('\1')" class="inno-pdf-btn" style="background:#2c3e50; color:white; padding:10px 20px; border-radius:30px; border:none; cursor:pointer; display:inline-block; margin:10px 0; font-weight:bold; box-shadow: 0 4px 15px rgba(0,0,0,0.2); transition: 0.3s;"><i class="fas fa-book-open"></i> 📖 Open 3D Flipbook</button><br>"""
    return re.sub(pdf_pattern, pdf_html, text)

app.jinja_env.filters['format_smart'] = format_smart_tags

# ================= DATABASE CONNECTION =================
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(KEY_PATH)
        firebase_admin.initialize_app(cred, {
            'projectId': 'psc-smart-preparation-app',
            'storageBucket': CLOUD_BUCKET_NAME
        })
        print("✅ Firebase Cloud Connected Successfully!")
    except Exception as e:
        print(f"❌ Cloud Connection Failed: {e}")

db = firestore.client()
bucket = storage.bucket()

# ================= THE MULTI-TENANT GATEKEEPER =================
@app.before_request
def intercept_subdomain():
    host = request.host.split(':')[0].lower()
    
    # 1. Set the default Master Inno Mind branding
    g.institute = {
        "name": "Inno Mind Academy",
        "domain": "innomindacademy.in",
        "logo_url": "/static/images/innomind_logo.png",
        "theme_color": "#1E3A8A",
        "creator_id": "inno_mind_master" 
    }

    # 2. If you are testing locally on your computer, skip the subdomain check
    if host in ['127.0.0.1', 'localhost']:
        return

    # 3. Extract the subdomain if they are on the live server
    subdomain = None
    if host.endswith('.innomindacademy.in'):
        subdomain = host.replace('.innomindacademy.in', '')
        subdomain = subdomain.rstrip('.')

    # 4. If it's the main site or www, keep the Master branding
    if not subdomain or subdomain == 'www' or subdomain == '':
        return

    # 5. Look up the Teacher's custom institute data
    doc = db.collection('institutes').document(subdomain).get()
    
    if doc.exists:
        g.institute = doc.to_dict()
        g.institute['subdomain'] = subdomain
    else:
        abort(404, description="This Academy does not exist on the Inno Mind Network.")

@app.context_processor
def inject_institute_brand():
    return dict(institute=g.institute)

# ================= CORE ROUTES =================
@app.route('/')
def home():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    user_email = session['user_email']
    user_doc = db.collection('users').document(user_email).get()
    
    enrolled_course = None 
    subscription_tier = "Free"
    
    if user_doc.exists:
        user_data = user_doc.to_dict()
        enrolled_course = user_data.get('enrolled_course')
        subscription_tier = user_data.get('subscription', 'Free')
        
        # Expiration logic
        end_date_str = user_data.get('sub_end_date')
        if end_date_str and subscription_tier != "Free":
            try:
                expiration_date = datetime.strptime(end_date_str, "%Y-%m-%d")
                if datetime.now() > expiration_date:
                    db.collection('users').document(user_email).update({
                        "subscription": "Free",
                        "enrolled_course": "",
                        "sub_end_date": ""
                    })
                    subscription_tier = "Free"
                    enrolled_course = None
                    flash("⚠️ Your subscription has expired. Please renew to access premium features.")
            except ValueError:
                pass 

        if enrolled_course in ["None", ""]: enrolled_course = None
            
        session['enrolled_course'] = enrolled_course 
        session['subscription'] = subscription_tier
        
    available_courses = {}
    if not enrolled_course:
        try:
            docs = db.collection('courses').where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).where(filter=FieldFilter('status', '==', 'active')).stream()
            for doc in docs:
                c_data = doc.to_dict()
                if "course_name" in c_data and "course_value" in c_data:
                    available_courses[c_data["course_name"]] = c_data["course_value"]
        except Exception as e:
            print(f"Error fetching active courses: {e}")

    results_stream = db.collection("student_results").where(filter=FieldFilter("student", "==", user_email)).stream()
    all_results = [r.to_dict() for r in results_stream]
    all_results.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    total_mcq = 0; net_score = 0.0
    test_history = []
    aggregated_stats = {"course": {}, "subject": {}, "block": {}, "unit": {}, "topic": {}}
    
    for d in all_results:
        q_total = int(d.get('mcq_total_raw', 0))
        q_score = float(d.get('mcq_score_raw', 0))
        total_mcq += q_total
        net_score += q_score
        
        test_history.append({
            "date": d.get("date", "Unknown Date").split(" ")[0],
            "score": q_score, "total": q_total,
            "pct": round((q_score / q_total * 100), 1) if q_total > 0 else 0
        })
        
        breakdown = d.get('breakdown', {})
        for level in ['course', 'subject', 'block', 'unit', 'topic']:
            level_data = breakdown.get(level, {})
            for item_name, stats in level_data.items():
                if item_name not in aggregated_stats[level]:
                    aggregated_stats[level][item_name] = {'score': 0.0, 'total': 0}
                aggregated_stats[level][item_name]['score'] += stats['score']
                aggregated_stats[level][item_name]['total'] += stats['total']

    acc = round((net_score / (total_mcq * 4) * 100), 1) if total_mcq > 0 else 0

    packages_ref = db.collection('subscription_packages').where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).stream()
    available_packages = []
    for pkg in packages_ref:
        pkg_data = pkg.to_dict()
        pkg_data['id'] = pkg.id 
        available_packages.append(pkg_data)

    return render_template('dashboard.html', 
                           user_name=session.get('user_name'), 
                           acc=acc, 
                           net=net_score, 
                           total=total_mcq,
                           test_history=test_history,
                           analytics_data=aggregated_stats,
                           enrolled_course=enrolled_course,
                           available_courses=available_courses,
                           subscription=subscription_tier, 
                           available_packages=available_packages)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()
        
        if not email or not password:
            flash("Please enter both email and password.")
            return render_template('login.html')
            
        user_doc = db.collection("users").document(email).get()
        
        if user_doc.exists:
            user_data = user_doc.to_dict()
            stored_password_hash = user_data.get("password", "")
            
            if check_password_hash(stored_password_hash, password):
                session['user_email'] = email
                session['user_name'] = user_data.get("name")
                session['user_role'] = user_data.get("role")
                session['subscription'] = user_data.get("subscription", "Free")
                session['enrolled_course'] = user_data.get("enrolled_course")
                return redirect(url_for('home'))
            else:
                flash("Invalid email or password.")
        else:
            flash("Invalid email or password.")
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip().lower()
        address = request.form.get('address', '').strip()
        password = request.form.get('password', '').strip()
        enrolled_course = request.form.get('enrolled_course', '')
        
        if not email or not password:
            flash("Error: Email and Password are required fields!")
            return redirect(url_for('register'))
            
        if db.collection("users").document(email).get().exists:
            flash("Email already registered! Try logging in.")
            return redirect(url_for('register'))
        
        hashed_password = generate_password_hash(password)
        
        user_data = {
            "name": name, "phone": phone, "email": email, "address": address,
            "password": hashed_password, "role": "Student", "subscription": "Free",
            "enrolled_course": enrolled_course,
            "join_date": datetime.now().strftime("%Y-%m-%d"),
            "mcqs_taken_today": 0, "last_active": datetime.now().strftime("%Y-%m-%d")
        }
        db.collection("users").document(email).set(user_data)
        flash("Registration successful! Please log in with your new credentials.")
        return redirect(url_for('login'))
        
    available_courses = {}
    host_url = request.host.replace('www.', '').split(':')[0]
    if host_url in ['127.0.0.1', 'localhost']: host_url = 'innomindacademy.in'
    
    try:
        docs = db.collection('courses').where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).where(filter=FieldFilter('status', '==', 'active')).stream()
        for doc in docs:
            c_data = doc.to_dict()
            if "course_name" in c_data and "course_value" in c_data:
                available_courses[c_data["course_name"]] = c_data["course_value"]
    except Exception as e: print(f"Error fetching active courses: {e}")

    return render_template('register.html', courses=available_courses)

# ================= ADMIN MAGIC LINK ENGINE =================
@app.route('/magic_login/<token>')
def magic_login(token):
    doc_ref = db.collection("magic_tokens").document(token)
    doc = doc_ref.get()
    
    if doc.exists:
        data = doc.to_dict()
        session['user_email'] = data.get('email', 'admin@innomind.in')
        session['user_role'] = data.get('role', 'Admin')
        session['user_name'] = "Master Admin"
        doc_ref.delete()
        next_url = request.args.get('next', '/home')
        return redirect(next_url)
        
    return "Security Error: Invalid or Expired Magic Link.", 403
# ===========================================================

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/pricing')
def pricing():
    student_email = session.get('user_email')
    if not student_email: return redirect(url_for('login'))

    current_plan = "default_free"
    packages = []
    
    try:
        user_doc = db.collection("users").document(student_email).get()
        if user_doc.exists:
            current_plan = user_doc.to_dict().get('subscription', 'default_free')

        pkg_stream = db.collection("subscription_packages").where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).where(filter=FieldFilter("status", "==", "active")).stream()
        for doc in pkg_stream:
            pkg_data = doc.to_dict()
            pkg_data['id'] = doc.id 
            packages.append(pkg_data)
            
        packages.sort(key=lambda x: x.get('price', 0))
        
    except Exception as e:
        print(f"Pricing Sync Error: {e}")

    return render_template('pricing.html', packages=packages, current_plan=current_plan)

# ================= STUDY VAULT =================
@app.route('/study')
def study():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    enrolled_course = session.get('enrolled_course')
    if not enrolled_course:
        flash("You are not enrolled in a course yet.")
        return redirect(url_for('home'))
        
    docs = db.collection("opsc_content").where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).where(filter=FieldFilter('course_values', 'array_contains', enrolled_course)).stream()
    
    tree = {}; content_dict = {}
    for doc in docs:
        d = doc.to_dict()
        c = enrolled_course 
        s = d.get('subject', 'General')
        b = d.get('block', 'Block')
        u = d.get('unit', 'Unit')
        t = d.get('topic', 'Topic')
        st = d.get('subtopic', 'Subtopic')
        
        if c not in tree: tree[c] = {}
        if s not in tree[c]: tree[c][s] = {}
        if b not in tree[c][s]: tree[c][s][b] = {}
        if u not in tree[c][s][b]: tree[c][s][b][u] = {}
        if t not in tree[c][s][b][u]: tree[c][s][b][u][t] = []
        
        tree[c][s][b][u][t].append({'id': doc.id, 'subtopic': st})
        
        raw_body = d.get('body', '').replace('\n', '<br>')
        html_body = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-width:100%; border-radius:8px; margin:15px 0;">', raw_body)
        html_body = format_smart_tags(html_body)
        
        content_dict[doc.id] = {'breadcrumbs': f"{c} &rarr; {s} &rarr; {b} &rarr; {u} &rarr; {t}", 'title': st, 'body': html_body}

    def generate_tree_html(node, depth=0, prefix="tier"):
        html = ""
        if isinstance(node, list):
            for item in node:
                html += f'<button class="leaf-btn" onclick="loadContent(\'{item["id"]}\')">📄 {item["subtopic"]}</button>\n'
            return html
        icons = ["🎓", "📚", "📦", "🔖", "📂"]
        icon = icons[depth] if depth < len(icons) else "📁"
        i = 0
        for key, child in node.items():
            current_id = f"{prefix}_{depth}_{i}"
            html += f'<button class="folder-btn" onclick="toggleTier(\'{current_id}\')">{icon} {key}</button>\n'
            html += f'<div id="{current_id}" class="tier" style="margin-left:15px;">\n'
            html += generate_tree_html(child, depth + 1, current_id)
            html += f'</div>\n'
            i += 1
        return html

    finished_tree_html = generate_tree_html(tree)
    return render_template('study.html', tree_html=finished_tree_html, content_dict=content_dict)

# ================= SECURE PDF VIEWER =================
@app.route('/secure_viewer')
def secure_viewer():
    if 'user_email' not in session: return redirect(url_for('login'))
    doc_url = request.args.get('doc_url')
    if not doc_url: return "Document Missing", 404
    return render_template('secure_pdf.html', pdf_url=doc_url)

# ================= EXAM ENGINE =================
@app.route('/exam_config')
def exam_config():
    if 'user_email' not in session: return redirect(url_for('login'))
    return render_template('exam_config.html')

@app.route('/take_exam', methods=['POST'])
def take_exam():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    enrolled_course = session.get('enrolled_course')
    if not enrolled_course:
        flash("You must be enrolled in a course to take an exam.")
        return redirect(url_for('home'))

    rule_tags = request.form.getlist('rule_tag[]')
    rule_counts = request.form.getlist('rule_count[]')
    timer = int(request.form.get('timer', 15)) * 60
    pos_mark = float(request.form.get('pos_mark', 4.0))
    neg_mark = float(request.form.get('neg_mark', 1.0))
    
    allowed_types = request.form.getlist('q_types')
    if not allowed_types: allowed_types = ['MCQ', 'Short Answer', 'Long Answer']

    master_qs = []
    safe_enrolled = str(enrolled_course).lower()

    docs = db.collection("questions").where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).stream()
    for doc in docs:
        d_clean = {k.lower(): v for k, v in doc.to_dict().items()}
        
        db_course = str(d_clean.get('target_course', '')).lower()
        if safe_enrolled not in db_course and db_course not in safe_enrolled: continue
        
        q_type = str(d_clean.get('question_type', 'mcq')).upper()
        if q_type == 'SHORT': q_type = 'Short Answer'
        if q_type == 'LONG': q_type = 'Long Answer'
        if q_type not in [t.upper() for t in allowed_types]: continue

        q_text = str(d_clean.get('question_text', ''))
        if not q_text: continue
        
        q_fingerprint = hashlib.md5(q_text.encode('utf-8')).hexdigest()
        
        smart_q = {
            'type': q_type,
            'q_hash': q_fingerprint,
            'meta': {
                'subject': str(d_clean.get('subject', 'General')),
                'block': str(d_clean.get('block', 'Misc')),
                'unit': str(d_clean.get('unit', 'Misc')),
                'topic': str(d_clean.get('topic', 'Misc')),
                'subtopic': str(d_clean.get('subtopic', 'Misc'))
            },
            'q_html': q_text.replace('\n', '<br>')
        }
        
        if q_type == 'MCQ':
            smart_q['opts_html'] = [
                str(d_clean.get('option_a', '')), str(d_clean.get('option_b', '')),
                str(d_clean.get('option_c', '')), str(d_clean.get('option_d', ''))
            ]
            smart_q['ans'] = str(d_clean.get('correct_answer', ''))

        master_qs.append(smart_q)

    active_test_qs = []
    used_hashes_this_test = set() 

    for tag, count_str in zip(rule_tags, rule_counts):
        if not count_str.isdigit() or int(count_str) <= 0: continue
        count = int(count_str)
        tag_l = tag.strip().lower()
        
        valid_pool = [q for q in master_qs if q['q_hash'] not in used_hashes_this_test and (not tag_l or tag_l in str(q['meta']).lower())]
        
        random.shuffle(valid_pool)
        selected = valid_pool[:count] 
        
        active_test_qs.extend(selected)
        for sq in selected: used_hashes_this_test.add(sq['q_hash'])

    if not active_test_qs:
        flash("❌ No questions found matching those specific topics or subtopics in your database.")
        return redirect(url_for('exam_config'))
        
    session['active_test_qs'] = active_test_qs
    session['pos_mark'] = pos_mark
    session['neg_mark'] = neg_mark
    
    return render_template('live_exam.html', questions=active_test_qs, time_left=timer)

@app.route('/submit_exam', methods=['POST'])
def submit_exam():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    active_test_qs = session.get('active_test_qs', [])
    if not active_test_qs: return redirect(url_for('home'))
        
    pos_val = float(session.get('pos_mark', 4.0)) 
    neg_val = float(session.get('neg_mark', 1.0)) 
    
    mcq_total = 0; mcq_correct = 0; mcq_wrong = 0
    typed_descriptive = {}
    breakdown_dict = {"course": {}, "subject": {}, "block": {}, "unit": {}, "topic": {}}
    
    for i, q in enumerate(active_test_qs):
        meta = q.get('meta', {})
        q_type = q.get('type', 'MCQ')
        
        if q_type == 'MCQ':
            mcq_total += 1
            user_ans = request.form.get(f'q_{i}', '').strip().lower()
            correct_ans = str(q.get('ans', '')).strip().lower()
            
            is_correct = (user_ans == correct_ans)
            is_wrong = (user_ans != "" and not is_correct)
            score_val = pos_val if is_correct else (-neg_val if is_wrong else 0)
            
            if is_correct: mcq_correct += 1
            elif is_wrong: mcq_wrong += 1
            
            for level in ['subject', 'block', 'unit', 'topic']:
                tag_name = meta.get(level, 'Unknown')
                if tag_name not in breakdown_dict[level]: breakdown_dict[level][tag_name] = {'score': 0.0, 'total': 0}
                breakdown_dict[level][tag_name]['score'] += score_val
                breakdown_dict[level][tag_name]['total'] += 1 
        else:
            ans_text = request.form.get(f'desc_{i}', '').strip()
            if ans_text: typed_descriptive[str(i+1)] = ans_text

    final_mcq_score = (mcq_correct * pos_val) - (mcq_wrong * neg_val)
            
    result_data = {
        "student": session['user_email'],
        "mcq_score_raw": final_mcq_score, "mcq_total_raw": mcq_total,
        "breakdown": breakdown_dict, "typed_descriptive": typed_descriptive,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    db.collection("student_results").add(result_data)
    session.pop('active_test_qs', None)
    
    flash(f"🎉 Exam Submitted! You scored {final_mcq_score} marks.")
    return redirect(url_for('home'))

# ================= LIVE BOARD & CALENDAR =================
@app.route('/calendar')
def campus_calendar():
    student_email = session.get('user_email')
    if not student_email: return redirect(url_for('login'))
    
    enrolled_course = session.get('enrolled_course', '')
    events = []
    try:
        if enrolled_course:
            events_stream = db.collection("calendar_events").where(filter=FieldFilter("target_courses", "array_contains", enrolled_course)).stream()
            for doc in events_stream: events.append(doc.to_dict())
            events.sort(key=lambda x: x.get('date', '9999-12-31'))
    except Exception as e: print(f"Calendar Sync Error: {e}")
        
    return render_template('calendar.html', user={'enrolled_course': enrolled_course}, events=events)

@app.route('/whiteboard')
def whiteboard():
    if 'user_email' not in session: return redirect(url_for('login'))
    return render_template('whiteboard.html')

@app.route('/get_live_board')
def get_live_board():
    doc = db.collection("live_sessions").document("current_solution").get()
    if doc.exists: return jsonify(doc.to_dict())
    return jsonify({"image": None})

@app.route('/update_live_board', methods=['POST'])
def update_live_board():
    db.collection("live_sessions").document("current_solution").set({
        "image": request.json.get('image'), "timestamp": firestore.SERVER_TIMESTAMP
    })
    return jsonify({"success": True})

# ================= REAL PAYMENT GATEWAY (RAZORPAY) =================
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_live_Sqo4je7UJiSlnH') 
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'EwWbZOKLTS42U36u72yQrLvr')
rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

@app.route('/terms')
def terms(): return render_template('terms.html')
@app.route('/privacy')
def privacy(): return render_template('privacy.html')
@app.route('/refund')
def refund(): return render_template('refund.html')
@app.route('/contact')
def contact(): return render_template('contact.html')
@app.route('/cancellation')
def cancellation(): return render_template('cancellation.html')

@app.route('/checkout/<package_id>')
def checkout_page(package_id):
    if 'user_email' not in session: return redirect(url_for('login'))
    pkg_doc = db.collection('subscription_packages').document(package_id).get()
    if not pkg_doc.exists: return "<h1>Error</h1><p>Course not found.</p>", 404
        
    package_data = pkg_doc.to_dict()
    package_data['id'] = pkg_doc.id
    return render_template('checkout.html', package=package_data)

@app.route('/create_order', methods=['POST'])
def create_order():
    package_id = request.form.get('package_id')
    pkg_doc = db.collection("subscription_packages").document(package_id).get()
    
    if not pkg_doc.exists:
        return jsonify({"status": "error", "message": "Package not found"}), 404
        
    pkg_data = pkg_doc.to_dict()
    total_price_in_rupees = float(pkg_data.get('price', 0))
    total_amount_paisa = int(total_price_in_rupees * 100) 

    creator_id = g.institute.get('creator_id', 'inno_mind_master')
    linked_account = g.institute.get('razorpay_linked_account', '')

    order_payload = {
        "amount": total_amount_paisa,
        "currency": "INR",
        "receipt": f"rcpt_{uuid.uuid4().hex[:8]}"
    }

    if creator_id != 'inno_mind_master' and linked_account != '':
        teacher_share_paisa = int(math.floor(total_amount_paisa * 0.60))
        order_payload["transfers"] = [
            {
                "account": linked_account,       
                "amount": teacher_share_paisa,   
                "currency": "INR",
                "notes": {
                    "info": f"Course purchase split for {g.institute.get('name')}"
                },
                "on_hold": False 
            }
        ]
    
    try:
        razorpay_order = razorpay_client.order.create(data=order_payload)
        return jsonify(razorpay_order)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    student_email = session.get('user_email')
    data = request.json
    try:
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': data.get('razorpay_order_id'),
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_signature': data.get('razorpay_signature')
        })
        
        item_type = data.get('type')

        if item_type == 'subscription':
            package_id = data.get('package_id')
            pkg_doc = db.collection("subscription_packages").document(package_id).get()
            pkg_data = pkg_doc.to_dict()
            
            target_course = pkg_data.get('target_course', '')
            validity_days = int(pkg_data.get('validity_days', 365)) 
            start_date = datetime.now()
            end_date = start_date + timedelta(days=validity_days)
            
            db.collection("users").document(student_email).update({
                "subscription": package_id, 
                "enrolled_course": target_course,
                "sub_start_date": start_date.strftime("%Y-%m-%d"),
                "sub_end_date": end_date.strftime("%Y-%m-%d")
            })
            return jsonify({"status": "success", "message": "Account Upgraded Successfully!"})

        elif item_type == 'mentorship':
            slot_id = data.get('slot_id')
            subject = data.get('subject')
            topic_desc = data.get('topic_desc')
            actual_price = MENTORSHIP_RATE_CARD.get(subject)

            db.collection("mentorship_slots").document(slot_id).update({
                "status": "booked",
                "booked_by": student_email,
                "subject": subject,
                "topic": topic_desc,
                "price": actual_price
            })
            return jsonify({"status": "success", "message": "Mentorship Slot Confirmed!"})

    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500

# ================= MENTORSHIP & DOUBT CLEARANCE =================
MENTORSHIP_RATE_CARD = {
    "Arithmetic / Basic Math": 1499,
    "Reasoning / Logical Aptitude": 1799,
    "History / General Studies": 1499,
    "Physics / Higher Math": 1899,
    "Career Counseling and Consultation": 1499
}

@app.route('/mentorship')
def mentorship_hub():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    slots_ref = db.collection("mentorship_slots").where(filter=FieldFilter('creator_id', '==', g.institute['creator_id'])).where(filter=FieldFilter("status", "==", "available")).stream()
    available_slots = [{"id": doc.id, **doc.to_dict()} for doc in slots_ref]
    available_slots.sort(key=lambda x: (x.get('date', ''), x.get('start_time', '')))
    
    my_slots_ref = db.collection("mentorship_slots").where(filter=FieldFilter("booked_by", "==", session['user_email'])).stream()
    my_booked_slots = [{"id": doc.id, **doc.to_dict()} for doc in my_slots_ref]
    my_booked_slots.sort(key=lambda x: (x.get('date', ''), x.get('start_time', '')))
    
    return render_template('mentorship.html', slots=available_slots, my_slots=my_booked_slots)

@app.route('/live_session/<slot_id>')
def live_session(slot_id):
    if 'user_email' not in session: return redirect(url_for('login'))
    slot_doc = db.collection("mentorship_slots").document(slot_id).get()
    if not slot_doc.exists: return "Session not found", 404
    
    slot_data = slot_doc.to_dict()
    user_email = session['user_email']
    user_role = session.get('user_role')
    
    if slot_data.get('booked_by') != user_email and user_role != 'Admin':
        return "Access Denied: You do not have booking credentials for this private room.", 403
        
    return render_template('live_session.html', slot_id=slot_id, user_name=session.get('user_name'), end_time=slot_data.get('end_datetime_str'))

@app.route('/review_session/<slot_id>', methods=['GET', 'POST'])
def review_session(slot_id):
    if 'user_email' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        rating = int(request.form.get('rating', 5))
        review_text = request.form.get('review_text', '')
        
        db.collection("mentorship_reviews").add({
            "slot_id": slot_id,
            "student_email": session['user_email'],
            "rating": rating,
            "review": review_text,
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        flash("Thank you for sharing your feedback with Inno Mind Academy!")
        return redirect(url_for('home'))
        
    return render_template('review_session.html', slot_id=slot_id)

# ================= CREATOR ONBOARDING MODULE =================
@app.route('/creator-join', methods=['GET', 'POST'])
def creator_join():
    if request.method == 'GET':
        return render_template('creator_join.html')
        
    name = request.form.get('name', '').strip()
    subdomain = request.form.get('subdomain', '').strip().lower()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    
    terms_accepted = request.form.get('terms_accepted')
    if terms_accepted != 'yes':
        return render_template('creator_join.html', error="You must accept the Terms and Conditions to proceed.")
    
    subdomain = re.sub(r'[^a-z0-9\-]', '', subdomain)
    if not all([name, subdomain, email, password]):
        return render_template('creator_join.html', error="All fields are mandatory.")
        
    if db.collection('institutes').document(subdomain).get().exists:
        return render_template('creator_join.html', error="This custom subdomain is already taken.")
        
    existing_creator = list(db.collection('institutes').where('creator_id', '==', email).stream())
    if len(existing_creator) > 0:
        error_msg = 'This email is already registered. <a href="/creator-login" style="color:#ff7675; text-decoration:underline; font-weight:bold;">Click here to Login & Download Studio.</a>'
        return render_template('creator_join.html', error=error_msg)

    hashed_password = generate_password_hash(password)
    tenant_payload = {
        "creator_id": email,
        "name": name,
        "theme_color": "#e74c3c", 
        "logo_url": "/static/images/innomind_logo.png",
        "password_hash": hashed_password,
        "subdomain": subdomain,
        "onboarding_date": firestore.SERVER_TIMESTAMP,
        "razorpay_linked_account": "" 
    }
    
    db.collection('institutes').document(subdomain).set(tenant_payload)
    return render_template('creator_success.html', subdomain=subdomain)

@app.route('/creator-login', methods=['GET', 'POST'])
def creator_login():
    if request.method == 'GET':
        return render_template('creator_login.html')
        
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    
    institutes = list(db.collection('institutes').where('creator_id', '==', email).stream())
    
    if not institutes:
        return render_template('creator_login.html', error="No creator account found with this email.")
        
    inst_data = institutes[0].to_dict()
    
    if check_password_hash(inst_data.get('password_hash', ''), password):
        return render_template('creator_success.html', subdomain=inst_data.get('subdomain'))
    else:
        return render_template('creator_login.html', error="Invalid password.")

@app.route('/download-studio')
def download_studio():
    return send_from_directory(directory='static', path='InnoMind_Studio_Bundle.zip', as_attachment=True)

@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    message = request.form.get('message')
    target_creator = g.institute.get('creator_id', 'inno_mind_master')
    
    feedback_data = {
        "student_email": session['user_email'],
        "student_name": session.get('user_name', 'Student'),
        "creator_id": target_creator,
        "message": message,
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    
    db.collection('feedbacks').add(feedback_data)
    return jsonify({"status": "success", "message": "Feedback sent to your instructor."})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)