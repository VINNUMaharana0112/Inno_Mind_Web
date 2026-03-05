from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
import os
import io
import re
import random
import uuid
from datetime import datetime
import razorpay

app = Flask(__name__)
app.secret_key = "inno_mind_super_secret_key" 

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")
CLOUD_BUCKET_NAME = "psc-smart-preparation-app"

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

# --- WEB ROUTES ---

@app.route('/')
def home():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    # THE FIX: Only ask Firebase to filter (Lightning fast). 
    # We removed the cloud 'order_by' command.
    results_stream = db.collection("student_results").where(filter=FieldFilter("student", "==", session['user_email'])).stream()
    
    # Extract all the data into a local Python list
    all_results = [r.to_dict() for r in results_stream]
    
    # THE FIX: Use Python to sort the dates. (Instantaneous).
    all_results.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    total_mcq = 0; net_score = 0.0
    test_history = []
    aggregated_stats = {"course": {}, "subject": {}, "block": {}, "unit": {}, "topic": {}}
    
    for d in all_results:
        q_total = int(d.get('mcq_total_raw', 0))
        q_score = float(d.get('mcq_score_raw', 0))
        
        total_mcq += q_total
        net_score += q_score
        
        # 1. Build the Individual Test History List
        test_history.append({
            "date": d.get("date", "Unknown Date").split(" ")[0],
            "score": q_score,
            "total": q_total,
            "pct": round((q_score / q_total * 100), 1) if q_total > 0 else 0
        })
        
        # 2. Aggregate Data for the Dynamic Charts
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
                           analytics_data=aggregated_stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password').strip()
        
        doc = db.collection("users").document(email).get()
        if doc.exists and doc.to_dict().get("password") == password:
            user_data = doc.to_dict()
            session['user_email'] = user_data['email']
            session['user_name'] = user_data['name']
            session['subscription'] = user_data['subscription']
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password. Please try again.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/study')
def study():
    if 'user_email' not in session: return redirect(url_for('login'))
    docs = db.collection("opsc_content").stream()
    tree = {}; content_dict = {}
    for doc in docs:
        d = doc.to_dict()
        c = d.get('course', 'Uncategorized'); s = d.get('subject', 'General')
        b = d.get('block', 'Block 1'); u = d.get('unit', 'Unit 1')
        t = d.get('topic', 'Topic 1'); st = d.get('subtopic', 'Subtopic 1')
        
        if c not in tree: tree[c] = {}
        if s not in tree[c]: tree[c][s] = {}
        if b not in tree[c][s]: tree[c][s][b] = {}
        if u not in tree[c][s][b]: tree[c][s][b][u] = {}
        if t not in tree[c][s][b][u]: tree[c][s][b][u][t] = []
        
        tree[c][s][b][u][t].append({'id': doc.id, 'subtopic': st})
        
        raw_body = d.get('body', '').replace('\n', '<br>')
        html_body = re.sub(
            r'\{\{IMG:(.*?)\}\}', 
            r'<img src="/asset/\1" style="max-width:100%; border-radius:8px; margin:15px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">', 
            raw_body
        )
        content_dict[doc.id] = {
            'breadcrumbs': f"{c} &rarr; {s} &rarr; {t}",
            'title': st,
            'body': html_body
        }

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
    
    course = request.form.get('course', '').strip().lower()
    subject = request.form.get('subject', '').strip().lower()
    count = int(request.form.get('count', 10))
    timer = int(request.form.get('timer', 15))
    pos_mark = float(request.form.get('pos_mark', 4.0)) 
    neg_mark = float(request.form.get('neg_mark', 1.0)) 
    allowed_types = request.form.getlist('q_types')
    
    if not allowed_types:
        flash("Please select at least one question type.")
        return redirect(url_for('exam_config'))
        
    pool = []
    docs = db.collection("opsc_content").stream()
    for doc in docs:
        d = doc.to_dict()
        c_tag = d.get('course', 'Uncategorized')
        s_tag = d.get('subject', 'General')
        
        if course and course not in c_tag.lower(): continue
        if subject and subject not in s_tag.lower(): continue
        
        for q in d.get('questions', []):
            if q.get('type', 'MCQ') in allowed_types:
                smart_q = q.copy()
                smart_q['meta'] = {'course': c_tag, 'subject': s_tag, 'block': d.get('block', 'Block'), 'unit': d.get('unit', 'Unit'), 'topic': d.get('topic', 'Topic')}
                
                raw_q = smart_q.get('q', '').replace('\n', '<br>')
                smart_q['q_html'] = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-width:100%; border-radius:5px; margin:10px 0;">', raw_q)
                
                # Format explanations for review page
                raw_exp = smart_q.get('exp', 'No explanation provided.').replace('\n', '<br>')
                smart_q['exp_html'] = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-height:100px; display:block; margin:10px 0;">', raw_exp)
                
                if smart_q.get('type') == 'MCQ':
                    processed_opts = []
                    for opt in smart_q.get('opts', []):
                        opt_html = re.sub(r'\{\{IMG:(.*?)\}\}', r'<img src="/asset/\1" style="max-height:80px; vertical-align:middle;">', opt)
                        processed_opts.append(opt_html)
                    smart_q['opts_html'] = processed_opts
                
                pool.append(smart_q)

    if not pool:
        flash("No questions matched your filters.")
        return redirect(url_for('exam_config'))
        
    active_test_qs = random.sample(pool, min(count, len(pool)))
    
    # NEW ARCHITECTURE: Save active test to Firebase temporarily to prevent browser crashes
    test_id = str(uuid.uuid4())
    db.collection("active_tests").document(test_id).set({"qs": active_test_qs})
    
    exam_html = ""
    for i, q in enumerate(active_test_qs):
        q_type = q.get('type', 'MCQ')
        exam_html += f'<div class="question-card">\n'
        exam_html += f'<div class="q-header">Question {i+1} [{q_type}]</div>\n'
        exam_html += f'<div class="q-text">{q.get("q_html", "")}</div>\n'
        
        if q_type == 'MCQ':
            letters = ['A', 'B', 'C', 'D']
            for j, opt_html in enumerate(q.get('opts_html', [])):
                exam_html += f'<label class="option-row">\n'
                exam_html += f'<input type="radio" name="q_{i}" value="{letters[j]}">\n'
                exam_html += f'<span><strong>{letters[j]}:</strong> {opt_html}</span>\n'
                exam_html += f'</label>\n'
        else:
            exam_html += f'<div style="font-size: 12px; color: #95a5a6; margin-bottom: 5px;">Target: 50-100 words (Short) | 200-250 words (Long)</div>\n'
            exam_html += f'<textarea name="desc_{i}" id="desc_{i}" placeholder="Type your answer here..." oninput="updateWordCount({i})"></textarea>\n'
            exam_html += f'<div class="word-counter" id="count_{i}">Words: 0</div>\n'
            
        exam_html += f'</div>\n'
        
    session['current_test_id'] = test_id
    session['pos_mark'] = pos_mark
    session['neg_mark'] = neg_mark
    
    return render_template('exam_live.html', exam_html=exam_html, timer=timer)

@app.route('/submit_exam', methods=['POST'])
def submit_exam():
    if 'user_email' not in session: return redirect(url_for('login'))
    
    test_id = session.get('current_test_id')
    test_doc = db.collection("active_tests").document(test_id).get()
    
    if not test_doc.exists:
        flash("Error: Test session expired or already submitted.")
        return redirect(url_for('home'))
        
    active_test_qs = test_doc.to_dict().get('qs', [])
    
    pos_val = float(session.get('pos_mark', 4.0)) 
    neg_val = float(session.get('neg_mark', 1.0)) 
    
    mcq_total = 0; mcq_correct = 0; mcq_wrong = 0
    typed_descriptive = {}
    breakdown_dict = {"course": {}, "subject": {}, "block": {}, "unit": {}, "topic": {}}
    
    # Data to pass to the review page
    review_data = []
    
    for i, q in enumerate(active_test_qs):
        meta = q.get('meta', {})
        q_type = q.get('type', 'MCQ')
        
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
                'q_html': q.get('q_html'),
                'type': q_type,
                'user_ans': user_ans if user_ans else 'Skipped',
                'correct_ans': q.get('ans'),
                'exp': q.get('exp_html', 'No explanation provided.'),
                'is_correct': is_correct,
                'is_skipped': user_ans == ""
            })
        else:
            ans_text = request.form.get(f'desc_{i}', '').strip()
            if ans_text: typed_descriptive[str(i+1)] = ans_text
            
            review_data.append({
                'q_html': q.get('q_html'),
                'type': q_type,
                'user_ans': ans_text if ans_text else 'Skipped',
                'correct_ans': 'Manual Evaluation Required',
                'exp': q.get('exp_html', 'No explanation provided.'),
                'is_correct': None,
                'is_skipped': ans_text == ""
            })

    # Final Score Calculation
    final_mcq_score = (mcq_correct * pos_val) - (mcq_wrong * neg_val)
    
    # --- NEW: Catch and Upload the Handwritten Answer Sheet ---
    mains_attachment_name = None
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file.filename != '': # If the student actually selected a file
            ext = os.path.splitext(file.filename)[1] or ".png"
            # Create a unique filename using their name
            safe_name = session['user_name'].replace(' ', '_')
            mains_attachment_name = f"{safe_name}_{uuid.uuid4().hex[:6]}{ext}"
            
            # Upload directly to Firebase Storage
            blob = bucket.blob(f"student_answers/{mains_attachment_name}")
            blob.upload_from_file(file, content_type=file.content_type)
    # -----------------------------------------------------------
    
    user_email = session['user_email']
    user_ref = db.collection("users").document(user_email)
    user_doc = user_ref.get()
    if user_doc.exists:
        current_usage = user_doc.to_dict().get("mcqs_taken_today", 0)
        user_ref.update({"mcqs_taken_today": current_usage + len(active_test_qs)})
        
    result_data = {
        "student": user_email,
        "mcq_score_raw": final_mcq_score,
        "mcq_total_raw": mcq_total,
        "breakdown": breakdown_dict, 
        "typed_descriptive": typed_descriptive,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mains_attachment": mains_attachment_name, # Saved to the cloud!
        "feedback": ""
    }
    db.collection("student_results").add(result_data)
    
    db.collection("active_tests").document(test_id).delete()
    
    return render_template('exam_review.html', score=final_mcq_score, correct=mcq_correct, wrong=mcq_wrong, review_data=review_data)

# --- RAZORPAY SETUP ---
# Replace these with your actual TEST keys from the Razorpay Dashboard
RAZORPAY_KEY_ID = "rzp_test_SNVpSbsF9gb8ti"
RAZORPAY_KEY_SECRET = "mwTzPIeteaf8DjQQsbJJJVlY"

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Scalable Pricing Dictionary (Prices in INR)
# Razorpay calculates in paise (multiply by 100 in the code)
# --- CLOUD PRICING ENGINE ---
def get_cloud_pricing():
    # Fetches live pricing from the admin_settings folder in Firebase
    doc = db.collection("admin_settings").document("platform_pricing").get()
    if doc.exists:
        return doc.to_dict().get("courses", {})
    else:
        # If the file doesn't exist yet, create it with your default prices!
        default_pricing = {
            "IPMAT 2026": {"Gold": 2999, "Diamond": 4999},
            "CLAT 2026": {"Gold": 3499, "Diamond": 5999},
            "OPSC": {"Gold": 1999, "Diamond": 3999}
        }
        db.collection("admin_settings").document("platform_pricing").set({"courses": default_pricing})
        return default_pricing

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Fetch live courses from the cloud
    pricing_data = get_cloud_pricing()
    active_courses = list(pricing_data.keys())
    
    if request.method == 'POST':
        name = request.form.get('name').strip()
        email = request.form.get('email').strip().lower()
        phone = request.form.get('phone').strip()
        password = request.form.get('password').strip()
        course = request.form.get('course').strip()
        
        doc_ref = db.collection("users").document(email)
        if doc_ref.get().exists:
            flash("An account with this email already exists! Please log in.")
            return redirect(url_for('register'))
            
        user_data = {
            "name": name, "email": email, "phone": phone, "password": password,
            "primary_course": course, "role": "Student", "subscription": "Silver",
            "join_date": datetime.now().strftime("%Y-%m-%d"), "mcqs_taken_today": 0,
            "last_active": datetime.now().strftime("%Y-%m-%d")
        }
        doc_ref.set(user_data)
        flash("Registration successful! You can now log in.")
        return redirect(url_for('login'))
        
    # Pass the live courses to the HTML page
    return render_template('register.html', courses=active_courses)

@app.route('/upgrade')
def upgrade_page():
    if 'user_email' not in session: return redirect(url_for('login'))
    # Fetch live pricing
    live_pricing = get_cloud_pricing()
    return render_template('upgrade.html', pricing=live_pricing, key_id=RAZORPAY_KEY_ID)

@app.route('/create_order', methods=['POST'])
def create_order():
    if 'user_email' not in session: return {"error": "Unauthorized"}, 401
    
    course = request.json.get('course')
    tier = request.json.get('tier')
    
    # Verify the incoming request against the LIVE cloud prices, not hardcoded ones!
    live_pricing = get_cloud_pricing()
    
    if course not in live_pricing or tier not in live_pricing[course]:
        return {"error": "Invalid course or tier"}, 400
        
    amount_inr = live_pricing[course][tier]
    amount_paise = amount_inr * 100 
    
    order_data = {
        "amount": amount_paise, "currency": "INR",
        "receipt": f"receipt_{session['user_email'].split('@')[0]}_{tier}",
        "notes": {"email": session['user_email'], "course": course, "tier": tier}
    }
    
    try:
        order = rzp_client.order.create(data=order_data)
        return {"order_id": order['id'], "amount": amount_paise, "course": course, "tier": tier}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    if 'user_email' not in session: return {"error": "Unauthorized"}, 401
    
    data = request.json
    try:
        # Cryptographically verify the signature so students can't fake a payment
        rzp_client.utility.verify_payment_signature({
            'razorpay_order_id': data['razorpay_order_id'],
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_signature': data['razorpay_signature']
        })
        
        # Payment is legit! Upgrade the user in Firebase immediately
        user_email = session['user_email']
        new_tier = data['tier']
        course = data['course']
        
        user_ref = db.collection("users").document(user_email)
        user_ref.update({
            "subscription": new_tier, 
            # We log the specific course they bought for future scaling
            f"access_{course.replace(' ', '_')}": new_tier 
        })
        
        # Update current browser session
        session['subscription'] = new_tier
        
        return {"status": "Success", "message": f"Welcome to {new_tier}!"}
        
    except razorpay.errors.SignatureVerificationError:
        return {"error": "Payment verification failed"}, 400
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)