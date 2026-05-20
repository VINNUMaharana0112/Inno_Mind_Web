import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- 1. CONNECT TO FIREBASE ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")

print("🔌 Connecting to Firebase...")
cred = credentials.Certificate(KEY_PATH)
firebase_admin.initialize_app(cred, {'projectId': 'psc-smart-preparation-app'})
db = firestore.client()

# Your Master ID
MASTER_ID = "inno_mind_master"

# The collections we need to update
COLLECTIONS_TO_MIGRATE = [
    "courses", 
    "opsc_content", 
    "questions", 
    "subscription_packages", 
    "mentorship_slots",
    "users",
    "student_results"
]

print(f"🚀 Starting Migration. Claiming all data for: {MASTER_ID}\n")

total_updated = 0

for collection_name in COLLECTIONS_TO_MIGRATE:
    print(f"Scanning [{collection_name}]...")
    docs = db.collection(collection_name).stream()
    
    count = 0
    for doc in docs:
        data = doc.to_dict()
        
        # Only update if they don't already have a creator_id
        if "creator_id" not in data:
            db.collection(collection_name).document(doc.id).update({
                "creator_id": MASTER_ID
            })
            count += 1
            
    print(f"   ✅ Updated {count} documents in {collection_name}.")
    total_updated += count

print(f"\n🎉 Migration Complete! Successfully secured {total_updated} total documents.")