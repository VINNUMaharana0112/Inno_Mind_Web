import firebase_admin
from firebase_admin import credentials, firestore
import csv
import os

# 1. Connect to your Live Firebase Database using the exact path from app.py
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIAL_FILE = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")

print("Connecting to Firebase...")
try:
    cred = credentials.Certificate(CREDENTIAL_FILE)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Connected Successfully!")
except Exception as e:
    print(f"❌ Connection Failed. Check your key path. Error: {e}")
    exit()

def bulk_upload(csv_filename):
    print(f"Opening {csv_filename}...")
    
    with open(csv_filename, mode='r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        count = 0
        
        for row in reader:
            if not row.get('question_text') or row.get('question_text').strip() == "":
                continue
                
            # Build the question document with the deep hierarchy
            question_data = {
                "target_course": row.get('target_course', '').strip(),
                "subject": row.get('subject', '').strip(),
                "block": row.get('block', '').strip(),
                "unit": row.get('unit', '').strip(),
                "topic": row.get('topic', '').strip(),
                "subtopic": row.get('subtopic', '').strip(),
                
                # The crucial separator: mcq, short, or long
                "question_type": row.get('question_type', 'mcq').strip().lower(), 
                
                "question_text": row.get('question_text', '').strip(),
                "option_a": row.get('option_a', '').strip(),
                "option_b": row.get('option_b', '').strip(),
                "option_c": row.get('option_c', '').strip(),
                "option_d": row.get('option_d', '').strip(),
                "correct_answer": row.get('correct_answer', '').strip(),
                "explanation": row.get('explanation', '').strip(),
                "difficulty": row.get('difficulty', 'medium').strip()
            }
            
            # PUSH STRICTLY TO THE QUESTIONS COLLECTION
            db.collection("questions").add(question_data)
            count += 1
            print(f"✅ Uploaded [{question_data['question_type'].upper()}] to {question_data['topic']}: {question_data['question_text'][:25]}...")
            
    print(f"\n🎉 Success! {count} questions uploaded to the standalone 'questions' database.")

if __name__ == "__main__":
    # Guarantee Python looks in the exact same folder as this script
    csv_path = os.path.join(SCRIPT_DIR, "deep_hierarchy_questions.csv")
    
    if not os.path.exists(csv_path):
        print(f"❌ ERROR: Still can't find {csv_path}")
        print("💡 WINDOWS TIP: Make sure your file isn't accidentally named 'deep_hierarchy_questions.csv.csv'!")
    else:
        bulk_upload(csv_path)