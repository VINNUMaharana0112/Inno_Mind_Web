from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
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

# --- SUBSCRIPTION LIMITS ---
SUBSCRIPTION_LIMITS = {
    "Free": {"mcq": 0, "descriptive": 0}, 
    "Silver": {"mcq": 10, "descriptive": 2}, 
    "Gold": {"mcq": 50, "descriptive": 20}, 
    "Diamond": {"mcq": 100, "descriptive": 50}, 
    "Admin": {"mcq": 9999, "descriptive": 9999}
}

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

# ================= LIVE EXAM ENGINE =================

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

    if not allowed_types:
        flash("Please select at least one section (e.g. MCQs).")
        return redirect(url_for('exam_config'))

    total_req_count = sum(int(c) for c in rule_counts if c.isdigit() and int(c) > 0)

    tier = session.get('subscription', 'Free')
    limits = SUBSCRIPTION_LIMITS.get(tier, {"mcq": 0, "descriptive": 0})
    
    user_doc = db.collection('users').document(session['user_email']).get()
    used = user_doc.to_dict().get('mcqs_taken_today', 0) if user_doc.exists else 0
    
    if used + total_req_count > limits['mcq']:
        flash(f"Upgrade Required! Your {tier} limit is {limits['mcq']} questions per day. You have already used {used}.")
        return redirect(url_for('exam_config'))

    seen_hashes = set()
    try:
        recent_tests = db.collection("student_results").where(filter=FieldFilter("student", "==", session['user_email'])).order_by("date", direction=firestore.Query.DESCENDING).limit(5).stream()
        for t in recent_tests: seen_hashes.update(t.to_dict().get('seen_qs', []))
    except: pass

    docs = db.collection("opsc_content").where('course_values', 'array_contains', enrolled_course).stream()
    master_qs = []
    
    for doc in docs:
        d = doc.to_dict()
        for q in d.get('questions', []):
            if q.get('type', 'MCQ') in allowed_types:
                q_fingerprint = hashlib.md5(q.get('q', '').encode('utf-8')).hexdigest()
                if q_fingerprint in seen_hashes: continue 
                
                smart_q = q.copy()
                smart_q['q_hash'] = q_fingerprint
                smart_q['meta'] = {'course': d.get('course', 'Uncategorized'), 'subject': d.get('subject', 'General'), 'block': d.get('block', 'Block'), 'unit': d.get('unit', 'Unit'), 'topic': d.get('topic', 'Topic')}
                
                # --- BULLETPROOF EXAM PDF INTEGRATION ---
                # Format Question Text
                raw_q = smart_q.get('q', '').replace('\n', '<br>')
                img_parsed_q = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-width:100%; border-radius:5px; margin:10px 0;">', raw_q)
                smart_q['q_html'] = format_smart_tags(img_parsed_q) 
                
                # Format Explanation Text
                raw_exp = smart_q.get('exp', 'No explanation provided.').replace('\n', '<br>')
                img_parsed_exp = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-height:100px; display:block; margin:10px 0;">', raw_exp)
                smart_q['exp_html'] = format_smart_tags(img_parsed_exp)
                
                # Format Multiple Choice Options
                if smart_q.get('type') == 'MCQ':
                    processed_opts = []
                    for opt in smart_q.get('opts', []):
                        opt_html = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-height:80px; vertical-align:middle;">', opt)
                        processed_opts.append(format_smart_tags(opt_html)) # Apply PDF engine to options too!
                    smart_q['opts_html'] = processed_opts
                    
                master_qs.append(smart_q)

    active_test_qs = []
    used_hashes_this_test = set() 

    for tag, count_str in zip(rule_tags, rule_counts):
        if not count_str.isdigit() or int(count_str) <= 0: continue
        count = int(count_str)
        tag_lower = tag.strip().lower()
        
        valid_pool = []
        for q in master_qs:
            if q['q_hash'] in used_hashes_this_test: continue
            if tag_lower:
                meta_values = str(q.get('meta', {})).lower()
                if tag_lower not in meta_values: continue
            valid_pool.append(q)
            
        selected = random.sample(valid_pool, min(count, len(valid_pool)))
        active_test_qs.extend(selected)
        for sq in selected: used_hashes_this_test.add(sq['q_hash'])

    if not active_test_qs:
        flash("We couldn't find enough unseen questions matching your specific blueprint. Try broadening your tags!")
        return redirect(url_for('exam_config'))
        
    session['active_test_qs'] = active_test_qs
    session['time_left'] = timer
    session['pos_mark'] = pos_mark
    session['neg_mark'] = neg_mark
    
    return render_template('live_exam.html', questions=active_test_qs, time_left=timer, pos_mark=pos_mark, neg_mark=neg_mark)

@app.route('/submit_exam', methods=['POST'])
def submit_exam():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    active_test_qs = session.get('active_test_qs', [])
    if not active_test_qs:
        flash("Error: Test session expired or already submitted.")
        return redirect(url_for('home'))
        
    pos_val = float(session.get('pos_mark', 4.0)) 
    neg_val = float(session.get('neg_mark', 1.0)) 
    
    mcq_total = 0; mcq_correct = 0; mcq_wrong = 0
    typed_descriptive = {}
    breakdown_dict = {"course": {}, "subject": {}, "block": {}, "unit": {}, "topic": {}}
    review_data = []
    seen_qs_payload = []
    
    for i, q in enumerate(active_test_qs):
        meta = q.get('meta', {})
        q_type = q.get('type', 'MCQ')
        
        fingerprint = q.get('q_hash')
        if not fingerprint: fingerprint = hashlib.md5(q.get('q', '').encode('utf-8')).hexdigest()
        seen_qs_payload.append(fingerprint)
        
        if q_type == 'MCQ':
            mcq_total += 1
            user_ans = request.form.get(f'q_{i}', '')
            is_correct = (user_ans == q.get('ans'))
            is_wrong = (user_ans != "" and not is_correct)
            score_val = pos_val if is_correct else (-neg_val if is_wrong else 0)
            
            if is_correct: mcq_correct += 1
            elif is_wrong: mcq_wrong += 1
            
            for level in ['course', 'subject', 'block', 'unit', 'topic']:
                tag_name = meta.get(level, 'Unknown')
                if tag_name not in breakdown_dict[level]: breakdown_dict[level][tag_name] = {'score': 0.0, 'total': 0}
                breakdown_dict[level][tag_name]['score'] += score_val
                breakdown_dict[level][tag_name]['total'] += 1 
                
            review_data.append({
                'q_html': q.get('q_html'), 'type': q_type, 'user_ans': user_ans if user_ans else 'Skipped',
                'correct_ans': q.get('ans'), 'exp': q.get('exp_html', 'No explanation provided.'),
                'is_correct': is_correct, 'is_skipped': user_ans == ""
            })
        else:
            ans_text = request.form.get(f'desc_{i}', '').strip()
            if ans_text: typed_descriptive[str(i+1)] = ans_text
            review_data.append({
                'q_html': q.get('q_html'), 'type': q_type, 'user_ans': ans_text if ans_text else 'Skipped',
                'correct_ans': 'Manual Evaluation Required', 'exp': q.get('exp_html', 'No explanation provided.'),
                'is_correct': None, 'is_skipped': ans_text == ""
            })

    final_mcq_score = (mcq_correct * pos_val) - (mcq_wrong * neg_val)
    
    mains_attachment_name = None
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file.filename != '': 
            ext = os.path.splitext(file.filename)[1] or ".png"
            safe_name = session['user_name'].replace(' ', '_')
            mains_attachment_name = f"{safe_name}_{uuid.uuid4().hex[:6]}{ext}"
            blob = bucket.blob(f"student_answers/{mains_attachment_name}")
            blob.upload_from_file(file, content_type=file.content_type)
            
    user_email = session['user_email']
    user_ref = db.collection("users").document(user_email)
    user_doc = user_ref.get()
    if user_doc.exists:
        current_usage = user_doc.to_dict().get("mcqs_taken_today", 0)
        user_ref.update({"mcqs_taken_today": current_usage + len(active_test_qs)})
        
    result_data = {
        "student": session['user_email'],
        "mcq_score_raw": final_mcq_score, "mcq_total_raw": mcq_total,
        "breakdown": breakdown_dict, "typed_descriptive": typed_descriptive,
        "seen_qs": seen_qs_payload, 
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    db.collection("student_results").add(result_data)
    
    session.pop('active_test_qs', None)
    
    return render_template('exam_review.html', score=final_mcq_score, correct=mcq_correct, wrong=mcq_wrong, review_data=review_data)

# ================= PRICING & PAYMENT SIMULATOR =================

@app.route('/pricing')
def pricing():
    if 'user_email' not in session: return redirect(url_for('login'))
    enrolled_course = session.get('enrolled_course')
    if not enrolled_course:
        flash("You need to be enrolled in a course to view pricing.")
        return redirect(url_for('home'))

    pricing_doc = db.collection("admin_settings").document("platform_pricing").get()
    course_pricing = {"Gold": "Contact Admin", "Diamond": "Contact Admin"} 
    
    if pricing_doc.exists:
        all_pricing = pricing_doc.to_dict().get("courses", {})
        target = str(enrolled_course).strip().lower().replace("_", "").replace(" ", "")
        for key, prices in all_pricing.items():
            db_key = str(key).strip().lower().replace("_", "").replace(" ", "")
            if db_key == target:
                course_pricing = prices
                break

    display_name = str(enrolled_course).replace("_", " ").title()
    return render_template('pricing.html', course_pricing=course_pricing, course_name=display_name)

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

# ⚠️ WHEN READY FOR LAUNCH: Swap these test keys for your Live Razorpay Keys!
RAZORPAY_KEY_ID = "rzp_live_SVMJshjiBfFfkr"
RAZORPAY_KEY_SECRET = "v4PooXzferJIdvStE20ZOULg"
rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

@app.route('/create_order', methods=['POST'])
def create_order():
    if 'user_email' not in session: return {"error": "Unauthorized"}, 401
    
    data = request.json
    tier = data.get('tier')
    enrolled_course = session.get('enrolled_course')
    
    pricing_doc = db.collection("admin_settings").document("platform_pricing").get()
    amount_inr = 0
    
    if pricing_doc.exists:
        all_pricing = pricing_doc.to_dict().get("courses", {})
        target = str(enrolled_course).strip().lower().replace("_", "").replace(" ", "")
        for key, prices in all_pricing.items():
            db_key = str(key).strip().lower().replace("_", "").replace(" ", "")
            if db_key == target:
                amount_inr = int(prices.get(tier, 0))
                break
                
    if amount_inr <= 0: return {"error": "Invalid price configuration."}, 400
    
    amount_paise = amount_inr * 100 
    
    order_data = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": f"rcpt_{session['user_email'].split('@')[0][:10]}_{tier[:3]}"
    }
    
    try:
        order = rzp_client.order.create(data=order_data)
        return {"order_id": order['id'], "amount": amount_paise, "key_id": RAZORPAY_KEY_ID, "tier": tier}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    if 'user_email' not in session: return {"error": "Unauthorized"}, 401
    
    data = request.json
    try:
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': data['razorpay_order_id'],
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_signature': data['razorpay_signature']
        })
        
        new_tier = data['tier']
        db.collection("users").document(session['user_email']).update({"subscription": new_tier})
        session['subscription'] = new_tier 
        
        flash(f"Payment Successful! Welcome to the {new_tier} tier.")
        return {"status": "Success"}
        
    except razorpay.errors.SignatureVerificationError:
        return {"error": "Payment verification failed. Signature mismatch."}, 400
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)