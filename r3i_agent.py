# r3i_agent.py
import os, json, uuid, threading
from dotenv import load_dotenv
import requests
from firebase_client import db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from email_client import (
    email_admin_new_complaint,
    email_student_admin_replied,
    email_student_resolved,
    email_admin_student_replied,
)

load_dotenv()
API_KEY = os.getenv("API_KEY")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL   = "openai/gpt-4o-mini"

CATEGORIES = [
    "Bathroom & Hygiene",
    "Anti-Ragging & Safety",
    "Mess & Food Quality",
    "Academic Issues",
    "Infrastructure_Maintenance",
    "Rules and Discipline",
    "Other"
]


def call_ai(system_prompt: str, user_message: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message}
        ],
        "provider": {"zdr": True},
    }
    res = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type":  "application/json"
        },
        json=payload
    )
    return res.json()["choices"][0]["message"]["content"]


CATEGORIZATION_PROMPT = """
You are R3i, an AI assistant for a college campus complaint system.
A student has described a problem. Your ONLY job is to categorize it.

CATEGORIES (use EXACT string, pick one):
- "Bathroom & Hygiene"
- "Anti-Ragging & Safety"
- "Mess & Food Quality"
- "Academic Issues"
- "Infrastructure_Maintenance"
- "Rules and Discipline"
- "Other"

OUTPUT RULES:
1. Output ONLY valid JSON. No text outside JSON. No markdown fences.
2. short_title: 3-6 words max describing the issue.
3. confidence: float 0.0 to 1.0. Be honest — if genuinely unsure, give low confidence.
4. flag: always 1.

JSON SCHEMA:
{
  "category": string,
  "short_title": string,
  "confidence": float,
  "flag": 1
}

EXAMPLE:
Input: "WiFi not working from past 2 weeks"
{
  "category": "Infrastructure_Maintenance",
  "short_title": "WiFi Not Working",
  "confidence": 0.95,
  "flag": 1
}
"""

ENHANCER_PROMPT = """
You are a message enhancer for a college complaint system.
A student has typed a message about a complaint.

YOUR ONLY JOB:
- Rewrite in clear, formal English.
- Fix grammar, spelling, clarity.
- Keep the original meaning exactly. Do not add new information.
- Output ONLY the enhanced message as plain text. Nothing else.
- Maximum 3 sentences.

EXAMPLE:
Input:  "bro wifi not working from 2 week pls do smthing fast"
Output: "The WiFi has not been working for the past 2 weeks. Please take action at the earliest."
"""


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "category":    "Other",
            "short_title": "Campus Complaint",
            "confidence":  0.0,
            "flag":        1
        }

def get_assigned_admin(category: str) -> dict:
    doc = db.collection("category_routing").document(category).get()
    return doc.to_dict() if doc.exists else {"adminId": "unassigned", "adminEmail": ""}

def get_admin_info(admin_id: str) -> dict:
    doc = db.collection("users").document(admin_id).get()
    return doc.to_dict() if doc.exists else {"displayName": "Admin", "email": ""}

def generate_tracking_id() -> str:
    return "#" + str(uuid.uuid4())[:6].upper()

def enhance_message(raw_message: str) -> str:
    return call_ai(ENHANCER_PROMPT, raw_message).strip()


def _fire_email(target, kwargs):
    """Wrapper so any exception inside an email thread prints to Render logs."""
    try:
        target(**kwargs)
    except Exception as e:
        print(f"[EMAIL THREAD ERROR] {e}")


def categorize_complaint(student_message: str) -> dict:
    raw         = call_ai(CATEGORIZATION_PROMPT, student_message)
    data        = parse_json(raw)
    confidence  = data.get("confidence", 0.0)
    short_title = data.get("short_title", "Campus Complaint")

    if confidence >= 0.75:
        category = data.get("category")
        return {
            "low_confidence": False,
            "category":       category,
            "short_title":    short_title,
            "confidence":     confidence,
            "flag":           1,
            "message": (
                f"Chill! I can notify the {category} department "
                f"and register your complaint. Do you want to proceed?"
            ),
            "buttons": ["Yes", "No"]
        }
    else:
        return {
            "low_confidence": True,
            "category":       None,
            "short_title":    short_title,
            "confidence":     confidence,
            "flag":           0,
            "message": (
                "I could not categorize your problem right now. "
                "Please select the most relevant department from below:"
            ),
            "buttons": CATEGORIES
        }


def apply_manual_category(selected_category: str, short_title: str) -> dict:
    return {
        "low_confidence": False,
        "category":       selected_category,
        "short_title":    short_title,
        "confidence":     1.0,
        "flag":           1,
        "message": (
            f"Got it! I can notify the {selected_category} department "
            f"and register your complaint. Do you want to proceed?"
        ),
        "buttons": ["Yes", "No"]
    }


def register_complaint(student_id: str, category_data: dict, raw_message: str) -> dict:
    tracking_id      = generate_tracking_id()
    student_doc      = db.collection("users").document(student_id).get()
    student          = student_doc.to_dict() if student_doc.exists else {}
    routing          = get_assigned_admin(category_data.get("category", "Other"))
    enhanced_initial = enhance_message(raw_message)

    complaint_ref = db.collection("complaints").document()
    complaint_ref.set({
        "studentId":       student_id,
        "studentName":     student.get("displayName", "Unknown"),
        "rollNumber":      student.get("rollNumber", ""),
        "roomNumber":      student.get("roomNumber", ""),
        "contactNumber":   student.get("contactNumber", ""),
        "email":           student.get("email", ""),
        "trackingId":      tracking_id,
        "shortTitle":      category_data.get("short_title"),
        "category":        category_data.get("category"),
        "confidence":      category_data.get("confidence"),
        "assignedAdminId": routing.get("adminId", "unassigned"),
        "status":          "submitted",
        "studentFlag":     "yellow",
        "adminFlag":       "red",
        "adminResponse":   "<none>",
        "createdAt":       SERVER_TIMESTAMP,
        "lastUpdated":     SERVER_TIMESTAMP,
    })

    complaint_ref.collection("messages").add({
        "type":      "student",
        "raw":       raw_message,
        "enhanced":  enhanced_initial,
        "createdAt": SERVER_TIMESTAMP,
    })

    # ── Fire-and-forget email ─────────────────────────────────────────────
    # daemon=False is critical — daemon threads are killed the moment the
    # HTTP response is sent; non-daemon threads finish even after the response.
    admin_id    = routing.get("adminId", "")
    admin_email = routing.get("adminEmail", "")
    print(f"[EMAIL DEBUG] admin_id={admin_id!r}  admin_email={admin_email!r}")
    if admin_email and admin_id != "unassigned":
        admin_info = get_admin_info(admin_id)
        threading.Thread(
            target=_fire_email,
            args=(email_admin_new_complaint, {
                "admin_email":      admin_email,
                "admin_name":       admin_info.get("displayName", "Admin"),
                "student_name":     student.get("displayName", "Unknown"),
                "tracking_id":      tracking_id,
                "category":         category_data.get("category", "General"),
                "short_title":      category_data.get("short_title", ""),
                "enhanced_message": enhanced_initial,
                "room_number":      student.get("roomNumber", "N/A"),
                "contact_number":   student.get("contactNumber", "N/A"),
                "roll_number":      student.get("rollNumber", "N/A"),
            }),
            daemon=False,   # ← non-daemon: thread outlives the HTTP response
        ).start()
    else:
        print("[EMAIL DEBUG] Skipped — no admin routed for this category or email missing")

    category = category_data.get("category", "General")
    return {
        "message": (
            f"Done! Your complaint has been registered under the "
            f"{category} category. "
            f"Your Tracking ID is {tracking_id}."
        ),
        "tracking_id":      tracking_id,
        "complaint_doc_id": complaint_ref.id,
        "studentFlag":      "yellow",
        "buttons":          []
    }


def cancel_registration() -> dict:
    return {
        "message": "No problem! Let me know if you'd like to report anything else.",
        "buttons": []
    }


def admin_send_message(complaint_id: str, response: str, status_update: str) -> dict:
    if status_update == "resolved":
        student_flag = "green"
        admin_flag   = "green"
        new_status   = "resolved"
    else:
        student_flag = "red"
        admin_flag   = "yellow"
        new_status   = "admin_responded"

    complaint_ref  = db.collection("complaints").document(complaint_id)
    complaint_data = complaint_ref.get().to_dict() or {}

    complaint_ref.collection("messages").add({
        "type":         "admin",
        "response":     response,
        "statusUpdate": status_update,
        "createdAt":    SERVER_TIMESTAMP,
    })

    complaint_ref.update({
        "adminResponse": response,
        "status":        new_status,
        "studentFlag":   student_flag,
        "adminFlag":     admin_flag,
        "lastUpdated":   SERVER_TIMESTAMP,
    })

    # ── Fire-and-forget email — daemon=False ──────────────────────────────
    student_email = complaint_data.get("email", "")
    if student_email:
        if status_update == "resolved":
            threading.Thread(
                target=_fire_email,
                args=(email_student_resolved, {
                    "student_email":  student_email,
                    "student_name":   complaint_data.get("studentName", "Student"),
                    "tracking_id":    complaint_data.get("trackingId", ""),
                    "short_title":    complaint_data.get("shortTitle", ""),
                    "admin_response": response,
                }),
                daemon=False,
            ).start()
        else:
            threading.Thread(
                target=_fire_email,
                args=(email_student_admin_replied, {
                    "student_email":  student_email,
                    "student_name":   complaint_data.get("studentName", "Student"),
                    "tracking_id":    complaint_data.get("trackingId", ""),
                    "short_title":    complaint_data.get("shortTitle", ""),
                    "admin_response": response,
                }),
                daemon=False,
            ).start()

    return {"success": True}


def student_reply(complaint_id: str, raw_message: str) -> dict:
    enhanced       = enhance_message(raw_message)
    complaint_ref  = db.collection("complaints").document(complaint_id)
    complaint_data = complaint_ref.get().to_dict() or {}

    complaint_ref.collection("messages").add({
        "type":      "student",
        "raw":       raw_message,
        "enhanced":  enhanced,
        "createdAt": SERVER_TIMESTAMP,
    })

    complaint_ref.update({
        "status":      "student_replied",
        "studentFlag": "yellow",
        "adminFlag":   "red",
        "lastUpdated": SERVER_TIMESTAMP,
    })

    # ── Fire-and-forget email — daemon=False ──────────────────────────────
    admin_id = complaint_data.get("assignedAdminId", "")
    if admin_id and admin_id != "unassigned":
        admin_info  = get_admin_info(admin_id)
        admin_email = admin_info.get("email", "")
        if admin_email:
            threading.Thread(
                target=_fire_email,
                args=(email_admin_student_replied, {
                    "admin_email":    admin_email,
                    "admin_name":     admin_info.get("displayName", "Admin"),
                    "student_name":   complaint_data.get("studentName", "Student"),
                    "tracking_id":    complaint_data.get("trackingId", ""),
                    "short_title":    complaint_data.get("shortTitle", ""),
                    "enhanced_reply": enhanced,
                }),
                daemon=False,
            ).start()

    return {
        "enhanced_message": enhanced,
        "studentFlag":      "yellow",
        "adminFlag":        "red"
    }
