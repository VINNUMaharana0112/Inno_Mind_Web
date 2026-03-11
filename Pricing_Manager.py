import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- Cloud Connection ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(SCRIPT_DIR, "PSC_Data_Vault", "firebase_key.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(KEY_PATH)
    firebase_admin.initialize_app(cred, {'projectId': 'psc-smart-preparation-app'})
db = firestore.client()

def fetch_live_courses(institute_domain="innomindacademy.in"):
    """Grabs the live list of courses from Firebase."""
    live_courses = {}
    try:
        docs = db.collection('courses')\
                 .where('institute_id', '==', institute_domain)\
                 .where('status', '==', 'active')\
                 .get()
        
        for doc in docs:
            data = doc.to_dict()
            name = data.get("course_name", "Unnamed Course")
            val = data.get("course_value", "")
            if name and val:
                live_courses[name] = val
                
        return live_courses
    except Exception as e:
        print(f"Error fetching from Firebase: {e}")
        return {}

class PricingManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Inno Mind - Cloud Pricing Manager")
        self.root.geometry("450x500")
        self.root.configure(bg="#2c3e50")
        
        self.live_pricing = {}
        
        # Fetch Master Course List on Boot
        self.master_courses = fetch_live_courses() 
        self.course_display_names = list(self.master_courses.keys())
        
        # UI Setup
        tk.Label(root, text="☁️ Live Cloud Pricing", font=("Segoe UI", 18, "bold"), bg="#2c3e50", fg="white").pack(pady=15)
        
        self.listbox = tk.Listbox(root, font=("Segoe UI", 12), width=40, height=8)
        self.listbox.pack(pady=10)
        self.listbox.bind('<<ListboxSelect>>', self.load_selected)
        
        # Inputs
        frame = tk.Frame(root, bg="#2c3e50")
        frame.pack(pady=10)
        
        tk.Label(frame, text="Course Name:", bg="#2c3e50", fg="white").grid(row=0, column=0, sticky="w", pady=5)
        self.course_var = tk.StringVar()
        
        # --- THE FIX: Changed state="readonly" to state="normal" so you can type! ---
        self.course_combo = ttk.Combobox(frame, textvariable=self.course_var, values=self.course_display_names, state="normal", width=18)
        self.course_combo.grid(row=0, column=1, padx=10)
        if self.course_display_names:
            self.course_combo.set("Choose a course...")
        else:
            self.course_combo.set("No courses found")
        # ----------------------------------------------------------------------------
        
        tk.Label(frame, text="Gold Price (₹):", bg="#2c3e50", fg="white").grid(row=1, column=0, sticky="w", pady=5)
        self.gold_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.gold_var, width=21).grid(row=1, column=1, padx=10)
        
        tk.Label(frame, text="Diamond Price (₹):", bg="#2c3e50", fg="white").grid(row=2, column=0, sticky="w", pady=5)
        self.diamond_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.diamond_var, width=21).grid(row=2, column=1, padx=10)
        
        # Buttons
        btn_frame = tk.Frame(root, bg="#2c3e50")
        btn_frame.pack(pady=20)
        
        tk.Button(btn_frame, text="Add / Update Course", bg="#2ecc71", fg="white", font=("bold"), command=self.update_course).grid(row=0, column=0, padx=10)
        tk.Button(btn_frame, text="Delete Course", bg="#e74c3c", fg="white", font=("bold"), command=self.delete_course).grid(row=0, column=1, padx=10)
        
        # Boot up
        self.fetch_from_cloud()

    def fetch_from_cloud(self):
        doc = db.collection("admin_settings").document("platform_pricing").get()
        if doc.exists:
            self.live_pricing = doc.to_dict().get("courses", {})
            self.refresh_listbox()
        else:
            messagebox.showwarning("Warning", "Pricing document not found in cloud. Try opening the web app first to generate it.")

    def refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        for backend_id, tiers in self.live_pricing.items():
            self.listbox.insert(tk.END, f"{backend_id} (Gold: ₹{tiers['Gold']} | Diamond: ₹{tiers['Diamond']})")

    def load_selected(self, event):
        selection = self.listbox.curselection()
        if selection:
            course_text = self.listbox.get(selection[0])
            backend_id = course_text.split(" (")[0]
            
            # Find the readable name for the dropdown based on the backend_id
            readable_name = next((name for name, val in self.master_courses.items() if val == backend_id), backend_id)
            
            self.course_var.set(readable_name)
            self.gold_var.set(self.live_pricing[backend_id]["Gold"])
            self.diamond_var.set(self.live_pricing[backend_id]["Diamond"])

    def push_to_cloud(self):
        db.collection("admin_settings").document("platform_pricing").update({"courses": self.live_pricing})
        messagebox.showinfo("Success", "Live pricing updated on the Web App instantly!")
        self.fetch_from_cloud()

    def update_course(self):
        selected_name = self.course_var.get().strip()
        g = self.gold_var.get().strip()
        d = self.diamond_var.get().strip()
        
        if not selected_name or selected_name == "Choose a course..." or not g or not d:
            messagebox.showerror("Error", "Please select a valid course and fill all prices.")
            return
            
        # --- THE FIX: Create Master Course Record if it doesn't exist ---
        if selected_name in self.master_courses:
            backend_id = self.master_courses[selected_name]
        else:
            # BRAND NEW COURSE DETECTED!
            # 1. Create a safe backend ID (e.g., "IPMAT 2026" -> "ipmat_2026")
            backend_id = selected_name.lower().replace(" ", "_")
            
            # 2. Save it to the Master 'courses' collection in Firebase
            db.collection("courses").document(backend_id).set({
                "course_name": selected_name,
                "course_value": backend_id,
                "institute_id": "innomindacademy.in",
                "status": "active"
            })
            
            # 3. Add it to our local dictionary so we don't have to restart the app
            self.master_courses[selected_name] = backend_id
            
            # 4. Update the dropdown list dynamically
            self.course_combo['values'] = list(self.master_courses.keys())
        # ----------------------------------------------------------------
            
        try:
            self.live_pricing[backend_id] = {"Gold": int(g), "Diamond": int(d)}
            self.push_to_cloud()
            self.course_var.set("Choose a course..."); self.gold_var.set(""); self.diamond_var.set("")
        except ValueError:
            messagebox.showerror("Error", "Prices must be numbers.")

    def delete_course(self):
        selected_name = self.course_var.get().strip()
        
        if selected_name in self.master_courses:
            backend_id = self.master_courses[selected_name]
        else:
            backend_id = selected_name
            
        if backend_id in self.live_pricing:
            if messagebox.askyesno("Confirm", f"Are you sure you want to remove pricing for {selected_name}?"):
                del self.live_pricing[backend_id]
                self.push_to_cloud()
                self.course_var.set("Choose a course..."); self.gold_var.set(""); self.diamond_var.set("")
        else:
            messagebox.showerror("Error", "Course pricing not found.")

if __name__ == "__main__":
    root = tk.Tk()
    app = PricingManagerApp(root)
    root.mainloop()