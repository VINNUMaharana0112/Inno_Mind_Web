import tkinter as tk
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

class PricingManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Inno Mind - Cloud Pricing Manager")
        self.root.geometry("450x500")
        self.root.configure(bg="#2c3e50")
        
        self.live_pricing = {}
        
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
        tk.Entry(frame, textvariable=self.course_var, width=20).grid(row=0, column=1, padx=10)
        
        tk.Label(frame, text="Gold Price (₹):", bg="#2c3e50", fg="white").grid(row=1, column=0, sticky="w", pady=5)
        self.gold_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.gold_var, width=20).grid(row=1, column=1, padx=10)
        
        tk.Label(frame, text="Diamond Price (₹):", bg="#2c3e50", fg="white").grid(row=2, column=0, sticky="w", pady=5)
        self.diamond_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.diamond_var, width=20).grid(row=2, column=1, padx=10)
        
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
        for course, tiers in self.live_pricing.items():
            self.listbox.insert(tk.END, f"{course} (Gold: ₹{tiers['Gold']} | Diamond: ₹{tiers['Diamond']})")

    def load_selected(self, event):
        selection = self.listbox.curselection()
        if selection:
            course_text = self.listbox.get(selection[0])
            course_name = course_text.split(" (")[0]
            self.course_var.set(course_name)
            self.gold_var.set(self.live_pricing[course_name]["Gold"])
            self.diamond_var.set(self.live_pricing[course_name]["Diamond"])

    def push_to_cloud(self):
        db.collection("admin_settings").document("platform_pricing").update({"courses": self.live_pricing})
        messagebox.showinfo("Success", "Live pricing updated on the Web App instantly!")
        self.fetch_from_cloud()

    def update_course(self):
        c = self.course_var.get().strip()
        g = self.gold_var.get().strip()
        d = self.diamond_var.get().strip()
        
        if not c or not g or not d:
            messagebox.showerror("Error", "All fields are required.")
            return
            
        try:
            self.live_pricing[c] = {"Gold": int(g), "Diamond": int(d)}
            self.push_to_cloud()
            self.course_var.set(""); self.gold_var.set(""); self.diamond_var.set("")
        except ValueError:
            messagebox.showerror("Error", "Prices must be numbers.")

    def delete_course(self):
        c = self.course_var.get().strip()
        if c in self.live_pricing:
            if messagebox.askyesno("Confirm", f"Are you sure you want to remove {c} from the web app?"):
                del self.live_pricing[c]
                self.push_to_cloud()
                self.course_var.set(""); self.gold_var.set(""); self.diamond_var.set("")
        else:
            messagebox.showerror("Error", "Course not found.")

if __name__ == "__main__":
    root = tk.Tk()
    app = PricingManagerApp(root)
    root.mainloop()