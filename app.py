from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
from werkzeug.security import generate_password_hash, check_password_hash
import os
import io
import re
import random
import uuid
import hashlib
from datetime import datetime
import razorpay

app = Flask(__name__)
app.secret_key = "inno_mind_super_secret_key" 

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")
CLOUD_BUCKET_NAME = "psc-smart-preparation-app"

# ================= SMART FORMATTING ENGINE =================
def format_smart_tags(text):
    if not text:
        return ""
    # Find the exact PDF text tag
    pdf_pattern = r'\[📄 Attached PDF: (https?://[^\s\]]+)\]'
    
    # NEW: Using triple-quotes so the JavaScript single-quotes render perfectly!
    pdf_html = r"""<br><a href="javascript:void(0);" onclick="openSecurePDF('\1')" class="inno-pdf-btn"><i class="fas fa-file-pdf"></i> 📄 View PDF Document</a><br>"""
    
    return re.sub(pdf_pattern, pdf_html, text)

# Register it with Flask for any stray HTML templates
app.jinja_env.filters['format_smart'] = format_smart_tags


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

@app.context_processor
def inject_institute_brand():
    host_url = request.host.replace('www.', '').split(':')[0]
    doc_ref = db.collection('institutions').document(host_url)
    doc = doc_ref.get()
    
    if doc.exists:
        institute_data = doc.to_dict()
    else:
        institute_data = {
            "name": "Inno Mind Academy",
            "domain": "innomindacademy.in",
            "logo_url": "/static/images/innomind_logo.png", 
            "theme_color": "#1E3A8A"
        }
    return dict(institute=institute_data)

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
        if enrolled_course == "None" or enrolled_course == "": 
            enrolled_course = None
            
        subscription_tier = user_data.get('subscription', 'Free')
        session['enrolled_course'] = enrolled_course 
        session['subscription'] = subscription_tier
        
    available_courses = {}
    if not enrolled_course:
        try:
            docs = db.collection('courses').where('status', '==', 'active').stream()
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

    acc = round((net_score / total_mcq * 100), 1) if total_mcq > 0 else 0

    return render_template('dashboard.html', 
                            user_name=session.get('user_name'), 
                            acc=acc, net=net_score, total=total_mcq,
                            test_history=test_history,
                            analytics_data=aggregated_stats,
                            enrolled_course=enrolled_course,
                            available_courses=available_courses,
                            subscription=subscription_tier)

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
        docs = db.collection('courses').where('institute_id', '==', host_url).where('status', '==', 'active').stream()
        for doc in docs:
            c_data = doc.to_dict()
            if "course_name" in c_data and "course_value" in c_data:
                available_courses[c_data["course_name"]] = c_data["course_value"]
    except Exception as e: print(f"Error fetching active courses: {e}")

    return render_template('register.html', courses=available_courses)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        new_password = request.form.get('new_password', '').strip()
        
        if not email or not phone or not new_password:
            flash("All fields are required to reset your password.")
            return redirect(url_for('forgot_password'))
            
        user_ref = db.collection("users").document(email)
        user_doc = user_ref.get()
        
        if user_doc.exists:
            user_data = user_doc.to_dict()
            
            if user_data.get('phone') == phone:
                hashed_password = generate_password_hash(new_password)
                user_ref.update({"password": hashed_password})
                
                flash("Password successfully reset! You can now log in securely.")
                return redirect(url_for('login'))
            else:
                flash("Verification failed: The phone number does not match our records.")
        else:
            flash("Error: That email address is not registered in our system.")
            
        return redirect(url_for('forgot_password'))
        
    return render_template('forgot_password.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/set_course', methods=['POST'])
def set_course():
    if 'user_email' not in session: return redirect(url_for('login'))
    selected_course = request.form.get('selected_course')
    if selected_course:
        db.collection("users").document(session['user_email']).update({"enrolled_course": selected_course})
        session['enrolled_course'] = selected_course
        flash("Course successfully linked!")
    else:
        flash("Please select a valid course.")
    return redirect(url_for('home'))

@app.route('/pricing')
def pricing():
    # 1. Secure Session Check
    student_email = session.get('user_email') or session.get('user') or session.get('email') or session.get('student')
    if not student_email:
        return redirect(url_for('login'))
        
    if isinstance(student_email, dict):
        student_email = student_email.get('email', '')

    current_plan = "default_free"
    packages = []
    
    try:
        # 2. Get the student's current active plan
        user_doc = db.collection("users").document(student_email).get()
        if user_doc.exists:
            current_plan = user_doc.to_dict().get('subscription', 'default_free')

        # 3. Fetch all ACTIVE subscription packages from the database
        pkg_stream = db.collection("subscription_packages")\
            .where(filter=FieldFilter("status", "==", "active"))\
            .stream()
            
        for doc in pkg_stream:
            pkg_data = doc.to_dict()
            pkg_data['id'] = doc.id # Save the document ID so we know which one they are clicking
            packages.append(pkg_data)
            
        # 4. Sort the packages by price (Lowest to Highest)
        packages.sort(key=lambda x: x.get('price', 0))
        
    except Exception as e:
        print(f"Pricing Sync Error: {e}")

    return render_template('pricing.html', packages=packages, current_plan=current_plan)

@app.route('/whiteboard/')
@app.route('/whiteboard')
def whiteboard():
    # Only allow logged-in students to access the premium whiteboard
    student_email = session.get('user_email') or session.get('user') or session.get('email') or session.get('student')
    if not student_email:
        return redirect(url_for('login'))
        
    return render_template('whiteboard.html')

# ================= STUDY VAULT =================

@app.route('/study')
def study():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    enrolled_course = session.get('enrolled_course')
    if not enrolled_course:
        flash("You are not enrolled in a course yet. Please contact your administrator.")
        return redirect(url_for('home'))
        
    docs = db.collection("opsc_content").where('course_values', 'array_contains', enrolled_course).stream()
    
    tree = {}; content_dict = {}
    for doc in docs:
        d = doc.to_dict()
        c = d.get('course', 'Uncategorized'); s = d.get('subject', 'General'); b = d.get('block', 'Block'); u = d.get('unit', 'Unit'); t = d.get('topic', 'Topic'); st = d.get('subtopic', 'Subtopic')
        
        if c not in tree: tree[c] = {}
        if s not in tree[c]: tree[c][s] = {}
        if b not in tree[c][s]: tree[c][s][b] = {}
        if u not in tree[c][s][b]: tree[c][s][b][u] = {}
        if t not in tree[c][s][b][u]: tree[c][s][b][u][t] = []
        
        tree[c][s][b][u][t].append({'id': doc.id, 'subtopic': st})
        
        # --- BULLETPROOF PDF INTEGRATION ---
        # 1. Convert newlines to HTML breaks
        raw_body = d.get('body', '').replace('\n', '<br>')
        # 2. Convert standard Images
        html_body = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-width:100%; border-radius:8px; margin:15px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">', raw_body)
        # 3. Convert PDFs using our Python engine before sending to the frontend!
        html_body = format_smart_tags(html_body)
        
        content_dict[doc.id] = {'breadcrumbs': f"{c} &rarr; {s} &rarr; {t}", 'title': st, 'body': html_body}

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
            html += f'<div id="{current_id}" class="tier">\n'
            html += generate_tree_html(child, depth + 1, current_id)
            html += f'</div>\n'
            i += 1
        return html

    finished_tree_html = generate_tree_html(tree)
    return render_template('study.html', tree_html=finished_tree_html, content_dict=content_dict)

from google.cloud.firestore_v1.base_query import FieldFilter

@app.route('/calendar')
def campus_calendar():
    # 1. DEBUG TOOL: This will print the actual session contents to your Render logs!
    print("CURRENT SESSION DATA:", dict(session))
    
    # 2. The Catch-All Check: Look for every possible key your login function might have used
    student_email = session.get('user') or session.get('email') or session.get('student') or session.get('user_email')
    
    # If absolutely none of them exist, the user actually isn't logged in
    if not student_email:
        print("DEBUG: No valid session key found! Redirecting to login.")
        return redirect(url_for('login'))
        
    # 3. Extract the email string if the session stored a dictionary by mistake
    if isinstance(student_email, dict):
        student_email = student_email.get('email', '')

    enrolled_course = ""
    events = []
    
    try:
        # 4. Fetch the student's exact enrolled course directly from Firestore
        user_doc = db.collection("users").document(student_email).get()
        if user_doc.exists:
            enrolled_course = user_doc.to_dict().get('enrolled_course', '')

        # 5. Fetch only events tagged for this student's specific course
        if enrolled_course:
            events_stream = db.collection("calendar_events")\
                .where(filter=FieldFilter("target_courses", "array_contains", enrolled_course))\
                .stream()
                
            for doc in events_stream:
                events.append(doc.to_dict())
                
            # Sort the events chronologically by date
            events.sort(key=lambda x: x.get('date', '9999-12-31'))
            
    except Exception as e:
        print(f"Calendar Sync Error: {e}")
        
    # 6. Pass the data explicitly to the HTML template
    # We pass 'user' as a dictionary so {{ user.enrolled_course }} works in the HTML
    return render_template('calendar.html', user={'enrolled_course': enrolled_course}, events=events)

@app.route('/asset/<filename>')
def serve_asset(filename):
    if 'user_email' not in session: return "Unauthorized", 401
    try:
        blob = bucket.blob(f"assets/{filename}")
        if not blob.exists(): return "Image missing", 404
        file_bytes = blob.download_as_bytes()
        return send_file(io.BytesIO(file_bytes), mimetype='image/png')
    except Exception as e:
        return str(e), 500

@app.route('/take_exam', methods=['POST'])
def take_exam():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    enrolled_course = session.get('enrolled_course')
    if not enrolled_course:
        flash("You must be enrolled in a course to take an exam.")
        return redirect(url_for('home'))

    # --- 1. Get Form Data ---
    rule_tags = request.form.getlist('rule_tag[]')
    rule_counts = request.form.getlist('rule_count[]')
    timer = int(request.form.get('timer', 15)) * 60
    pos_mark = float(request.form.get('pos_mark', 4.0))
    neg_mark = float(request.form.get('neg_mark', 1.0))
    allowed_types = request.form.getlist('q_types')

    if not allowed_types:
        flash("Please select at least one section (e.g. MCQs).")
        return redirect(url_for('exam_config'))

    # --- 2. Subscription Limit Check ---
    total_req_count = sum(int(c) for c in rule_counts if c.isdigit() and int(c) > 0)
    tier = session.get('subscription', 'Free')
    limits = SUBSCRIPTION_LIMITS.get(tier, {"mcq": 10, "descriptive": 0}) # Default safety
    
    user_doc = db.collection('users').document(session['user_email']).get()
    used = user_doc.to_dict().get('mcqs_taken_today', 0) if user_doc.exists else 0
    
    if used + total_req_count > limits['mcq']:
        flash(f"Upgrade Required! Your {tier} limit is {limits['mcq']} questions/day. You've used {used}.")
        return redirect(url_for('exam_config'))

    # --- 3. Anti-Repeat Logic (Seen Questions) ---
    seen_hashes = set()
    try:
        recent_tests = db.collection("student_results").where("student", "==", session['user_email']).order_by("date", direction=firestore.Query.DESCENDING).limit(5).stream()
        for t in recent_tests: 
            seen_hashes.update(t.to_dict().get('seen_qs', []))
    except Exception as e: 
        print(f"DEBUG: Seen Qs Error: {e}")

    # --- 4. THE DATA FETCH (Smart Course Matching) ---
    master_qs = []
    collections_to_check = ["questions", "opsc_content"]
    
    # Force everything to lowercase for safe comparing
    safe_enrolled = enrolled_course.lower() if enrolled_course else ""

    for coll in collections_to_check:
        # Fetch all questions (we will filter them instantly in Python)
        docs = db.collection(coll).stream()
        
        for doc in docs:
            d = doc.to_dict()
            
            # Check if the DB course (e.g. 'ipmat') is inside the enrolled course (e.g. 'ipmat_2026')
            db_course = d.get('course', '').lower()
            if not db_course or db_course not in safe_enrolled:
                continue # Skip if it doesn't match

            # Handle both CSV format and Manual Bundle format
            q_list = [d] if 'question' in d else d.get('questions', [])

            for q in q_list:
                q_type = q.get('type', 'MCQ')
                if q_type in allowed_types:
                    q_text = q.get('question') or q.get('q', '')
                    if not q_text: continue
                    
                    q_fingerprint = hashlib.md5(q_text.encode('utf-8')).hexdigest()
                    if q_fingerprint in seen_hashes: continue 
                    
                    smart_q = q.copy()
                    smart_q['q_hash'] = q_fingerprint
                    
                    smart_q['meta'] = {
                        'course': d.get('course', enrolled_course),
                        'subject': d.get('subject', q.get('subject', 'General')),
                        'block': d.get('block', q.get('block', 'Misc')),
                        'unit': d.get('unit', q.get('unit', 'Misc')),
                        'topic': d.get('topic', q.get('topic', 'Misc'))
                    }
                    
                    raw_q = q_text.replace('\n', '<br>')
                    smart_q['q_html'] = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-width:100%;">', raw_q)
                    
                    if q_type == 'MCQ':
                        if 'opts' in q:
                            smart_q['opts_html'] = q['opts']
                        else:
                            smart_q['opts_html'] = [q.get('a',''), q.get('b',''), q.get('c',''), q.get('d','')]
                        smart_q['ans'] = q.get('answer') or q.get('ans')

                    master_qs.append(smart_q)

    # --- 5. THE BLUEPRINT SELECTION (Safety Gate 2: Avoid Sample Crash) ---
    active_test_qs = []
    used_hashes_this_test = set() 

    for tag, count_str in zip(rule_tags, rule_counts):
        if not count_str.isdigit() or int(count_str) <= 0: continue
        count = int(count_str)
        tag_l = tag.strip().lower()
        
        valid_pool = [q for q in master_qs if q['q_hash'] not in used_hashes_this_test and (not tag_l or tag_l in str(q['meta']).lower())]
        
        # SAFETY: Use random.shuffle instead of sample to prevent "Sample larger than population" crash
        random.shuffle(valid_pool)
        selected = valid_pool[:count] 
        
        active_test_qs.extend(selected)
        for sq in selected: used_hashes_this_test.add(sq['q_hash'])

    if not active_test_qs:
        flash("No questions found! Check if you uploaded questions for " + enrolled_course)
        return redirect(url_for('exam_config'))
        
    session['active_test_qs'] = active_test_qs
    session['pos_mark'] = pos_mark
    session['neg_mark'] = neg_mark
    
    return render_template('live_exam.html', questions=active_test_qs, time_left=timer)

# 1. THE CONFIGURATION PAGE
@app.route('/exam_config')
def exam_config():
    if 'user_email' not in session: 
        return redirect(url_for('login'))
    # Ensure the template name matches your file name exactly
    return render_template('exam_config.html')

# 2. THE LIVE BOARD SYNC (Fixes the other 404 in your log)
@app.route('/get_live_board')
def get_live_board():
    doc = db.collection("live_sessions").document("current_solution").get()
    if doc.exists:
        return jsonify(doc.to_dict())
    return jsonify({"image": None})

@app.route('/update_live_board', methods=['POST'])
def update_live_board():
    data = request.json
    db.collection("live_sessions").document("current_solution").set({
        "image": data.get('image'),
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    return jsonify({"success": True})

@app.route('/privacy-policy')
def privacy():
    return render_template('privacy.html')

@app.route('/refund-policy')
def refund():
    return render_template('refund.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

# ================= PRICING & PAYMENT SIMULATOR =================


@app.route('/checkout/<tier>')
def checkout(tier):
    if 'user_email' not in session: return redirect(url_for('login'))
    enrolled_course = session.get('enrolled_course')
    pricing_doc = db.collection("admin_settings").document("platform_pricing").get()
    price = "0"

    if pricing_doc.exists:
        all_pricing = pricing_doc.to_dict().get("courses", {})
        target = str(enrolled_course).strip().lower().replace("_", "").replace(" ", "")
        for key, prices in all_pricing.items():
            db_key = str(key).strip().lower().replace("_", "").replace(" ", "")
            if db_key == target:
                price = prices.get(tier, "0")
                break

    display_name = str(enrolled_course).replace("_", " ").title()
    return render_template('checkout.html', tier=tier, price=price, course_name=display_name)

# ================= REAL PAYMENT GATEWAY (RAZORPAY) =================
# Security Protocol: 
# os.environ.get() asks the Render server for the secret live keys.
# If it can't find them (because you are running it locally on your laptop), 
# it safely defaults to your Test Keys.

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_test_SVsOLwS887kkCL') 
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '0GX3Nce0ZaU06IvJfpiQJZgN')

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

@app.route('/create_order', methods=['POST'])
def create_order():
    student_email = session.get('user_email') or session.get('user') or session.get('email') or session.get('student')
    if not student_email: return jsonify({"error": "Not logged in"}), 401
    if isinstance(student_email, dict): student_email = student_email.get('email', '')

    data = request.json
    package_id = data.get('package_id')

    try:
        # Fetch the package from the database to ensure they didn't tamper with the price
        pkg_doc = db.collection("subscription_packages").document(package_id).get()
        if not pkg_doc.exists: return jsonify({"error": "Package not found"}), 404
        
        pkg_data = pkg_doc.to_dict()
        price_in_rupees = pkg_data.get('price', 0)
        
        # Razorpay expects the amount in PAISE (Multiply by 100)
        amount_in_paise = int(price_in_rupees * 100)

        # Create the order in Razorpay
        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": f"receipt_{package_id}_{student_email[:5]}",
            "notes": {
                "email": student_email,
                "package": package_id
            }
        }
        order = rzp_client.order.create(data=order_data)
        
        return jsonify({
            "order_id": order['id'], 
            "amount": amount_in_paise, 
            "key_id": RAZORPAY_KEY_ID,
            "package_name": pkg_data.get('name')
        })

    except Exception as e:
        print(f"Order Creation Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    student_email = session.get('user_email') or session.get('user') or session.get('email') or session.get('student')
    if isinstance(student_email, dict): student_email = student_email.get('email', '')

    data = request.json
    
    try:
        # 1. Verify the cryptographic signature from Razorpay
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': data.get('razorpay_order_id'),
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_signature': data.get('razorpay_signature')
        })
        
        # 2. If signature is valid, upgrade the student in Firestore!
        package_id = data.get('package_id')
        pkg_doc = db.collection("subscription_packages").document(package_id).get()
        target_course = pkg_doc.to_dict().get('target_course', '')

        db.collection("users").document(student_email).update({
            "subscription": package_id,
            "enrolled_course": target_course
        })

        return jsonify({"status": "success", "message": "Account Upgraded Successfully!"})

    except razorpay.errors.SignatureVerificationError:
        return jsonify({"status": "failed", "error": "Payment Verification Failed!"}), 400
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500

if __name__ == "__main__":
    # 0.0.0.0 is the magic number that lets mobile devices connect
    app.run(host='0.0.0.0', port=10000)