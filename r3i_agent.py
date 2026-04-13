# r3i_agent.py
import os, json, uuid
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

CONTEXT_ENHANCER_PROMPT = """
You are a message enhancer for a college complaint system.
You are given the recent conversation history between a student and an admin,
followed by the student's new reply.

YOUR ONLY JOB:
- Rewrite the student's NEW reply in clear, formal English.
- Use the conversation history ONLY to understand the context — do NOT repeat or summarise it.
- Fix grammar, spelling, and clarity of the new reply.
- Keep the original meaning exactly. Do not add new information.
- Output ONLY the enhanced version of the new reply as plain text. Nothing else.
- Maximum 3 sentences.

FORMAT OF INPUT YOU WILL RECEIVE:
---CONVERSATION HISTORY (last 5 messages, oldest first)---
[Student]: <message>
[Admin]: <message>
... (up to 5 messages)
---NEW STUDENT REPLY---
<raw reply>

EXAMPLE:
---CONVERSATION HISTORY---
[Student]: The WiFi has not been working for the past two weeks.
[Admin]: Please share the specific floor and block where you are experiencing this issue.
---NEW STUDENT REPLY---
bro its 3rd floor block c whole area no wifi
OUTPUT:
The issue is on the third floor of Block C, where the entire area is without WiFi connectivity.
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
    """
    Returns routing info for the given category.
    If adminEmail is missing from category_routing, falls back to looking
    up the admin's email directly from the users collection.
    """
    doc = db.collection("category_routing").document(category).get()
    if not doc.exists:
        print(f"[ROUTING] No routing doc found for category={category!r}")
        return {"adminId": "unassigned", "adminEmail": ""}

    data = doc.to_dict()
    admin_id    = data.get("adminId", "")
    admin_email = data.get("adminEmail", "").strip()

    # ── Fallback: email missing in routing → look it up from users collection ──
    if not admin_email and admin_id and admin_id != "unassigned":
        print(f"[ROUTING] adminEmail missing in category_routing for {category!r}. "
              f"Falling back to users/{admin_id}")
        user_doc = db.collection("users").document(admin_id).get()
        if user_doc.exists:
            admin_email = (user_doc.to_dict() or {}).get("email", "").strip()
            print(f"[ROUTING] Fallback email resolved → {admin_email!r}")
        else:
            print(f"[ROUTING] users/{admin_id} not found either — email will be empty")

    return {"adminId": admin_id, "adminEmail": admin_email}


def get_admin_info(admin_id: str) -> dict:
    doc = db.collection("users").document(admin_id).get()
    return doc.to_dict() if doc.exists else {"displayName": "Admin", "email": ""}


def generate_tracking_id() -> str:
    return "#" + str(uuid.uuid4())[:6].upper()


def enhance_message(raw_message: str) -> str:
    """Simple enhancer — no context. Used for the first student message."""
    return call_ai(ENHANCER_PROMPT, raw_message).strip()


def fetch_last_n_messages(complaint_id: str, n: int = 5) -> list:
    """
    Fetches the last N messages from the complaint's messages subcollection,
    ordered by createdAt ascending so the oldest of the batch comes first.
    Returns a list of dicts.
    """
    try:
        docs = (
            db.collection("complaints")
            .document(complaint_id)
            .collection("messages")
            .order_by("createdAt")
            .stream()
        )
        all_msgs = [d.to_dict() for d in docs]
        return all_msgs[-n:]
    except Exception as e:
        print(f"[CONTEXT FETCH ERROR] {e}")
        return []


def enhance_message_with_context(raw_message: str, context_messages: list) -> str:
    """
    Context-aware enhancer. Builds a prompt that includes the last N messages
    so the LLM understands the thread before rewriting the student's new reply.
    """
    if not context_messages:
        return enhance_message(raw_message)

    history_lines = []
    for msg in context_messages:
        msg_type = msg.get("type", "")
        if msg_type == "student":
            text = msg.get("enhanced") or msg.get("raw", "")
            history_lines.append(f"[Student]: {text}")
        elif msg_type == "admin":
            text = msg.get("response", "")
            history_lines.append(f"[Admin]: {text}")

    history_str = "\n".join(history_lines)

    user_input = (
        f"---CONVERSATION HISTORY (last {len(context_messages)} messages, oldest first)---\n"
        f"{history_str}\n"
        f"---NEW STUDENT REPLY---\n"
        f"{raw_message}"
    )

    return call_ai(CONTEXT_ENHANCER_PROMPT, user_input).strip()


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL HELPERS
# These are plain functions — no threading. FastAPI BackgroundTasks (in main.py)
# handles running them off the critical path while staying ASGI-lifecycle-safe.
# ─────────────────────────────────────────────────────────────────────────────

def send_new_complaint_email(
    admin_email: str,
    admin_name: str,
    student: dict,
    tracking_id: str,
    category_data: dict,
    enhanced_initial: str,
):
    """Send 'new complaint assigned' email to admin."""
    try:
        email_admin_new_complaint(
            admin_email=admin_email,
            admin_name=admin_name,
            student_name=student.get("displayName", "Unknown"),
            tracking_id=tracking_id,
            category=category_data.get("category", "General"),
            short_title=category_data.get("short_title", ""),
            enhanced_message=enhanced_initial,
            room_number=student.get("roomNumber", "N/A"),
            contact_number=student.get("contactNumber", "N/A"),
            roll_number=student.get("rollNumber", "N/A"),
        )
    except Exception as e:
        print(f"[EMAIL BG ERROR] send_new_complaint_email → {e}")


def send_admin_replied_email(
    student_email: str,
    student_name: str,
    tracking_id: str,
    short_title: str,
    response: str,
    resolved: bool,
):
    """Send 'admin replied / resolved' email to student."""
    try:
        if resolved:
            email_student_resolved(
                student_email=student_email,
                student_name=student_name,
                tracking_id=tracking_id,
                short_title=short_title,
                admin_response=response,
            )
        else:
            email_student_admin_replied(
                student_email=student_email,
                student_name=student_name,
                tracking_id=tracking_id,
                short_title=short_title,
                admin_response=response,
            )
    except Exception as e:
        print(f"[EMAIL BG ERROR] send_admin_replied_email → {e}")


def send_student_replied_email(
    admin_email: str,
    admin_name: str,
    student_name: str,
    tracking_id: str,
    short_title: str,
    enhanced_reply: str,
):
    """Send 'student replied' email to admin."""
    try:
        email_admin_student_replied(
            admin_email=admin_email,
            admin_name=admin_name,
            student_name=student_name,
            tracking_id=tracking_id,
            short_title=short_title,
            enhanced_reply=enhanced_reply,
        )
    except Exception as e:
        print(f"[EMAIL BG ERROR] send_student_replied_email → {e}")


# ─────────────────────────────────────────────────────────────────────────────
# AGENT FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

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
                f"Just be alright, I can notify the {category} department "
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


def register_complaint(student_id: str, category_data: dict, raw_message: str) -> tuple[dict, dict | None]:
    """
    Returns (response_dict, email_kwargs_or_None).
    The caller (main.py route) schedules the email as a BackgroundTask.
    """
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

    # ── Prepare email kwargs (sent via BackgroundTask in main.py) ─────────
    admin_id    = routing.get("adminId", "")
    admin_email = routing.get("adminEmail", "").strip()

    print(f"[EMAIL DEBUG] register_complaint: admin_id={admin_id!r}  admin_email={admin_email!r}")

    email_kwargs = None
    if admin_email and admin_id != "unassigned":
        admin_info   = get_admin_info(admin_id)
        email_kwargs = {
            "admin_email":      admin_email,
            "admin_name":       admin_info.get("displayName", "Admin"),
            "student":          student,
            "tracking_id":      tracking_id,
            "category_data":    category_data,
            "enhanced_initial": enhanced_initial,
        }
    else:
        print(f"[EMAIL DEBUG] Skipped — admin_email empty or admin unassigned "
              f"(admin_id={admin_id!r}, admin_email={admin_email!r})")

    category = category_data.get("category", "General")
    response = {
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
    return response, email_kwargs


def cancel_registration() -> dict:
    return {
        "message": "No problem! Let me know if you'd like to report anything else.",
        "buttons": []
    }


def admin_send_message(complaint_id: str, response: str, status_update: str) -> tuple[dict, dict | None]:
    """
    Returns (response_dict, email_kwargs_or_None).
    The caller schedules the email as a BackgroundTask.
    """
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

    student_email = complaint_data.get("email", "").strip()
    print(f"[EMAIL DEBUG] admin_send_message: student_email={student_email!r}  status={status_update!r}")

    email_kwargs = None
    if student_email:
        email_kwargs = {
            "student_email": student_email,
            "student_name":  complaint_data.get("studentName", "Student"),
            "tracking_id":   complaint_data.get("trackingId", ""),
            "short_title":   complaint_data.get("shortTitle", ""),
            "response":      response,
            "resolved":      status_update == "resolved",
        }
    else:
        print("[EMAIL DEBUG] Skipped — student email missing from complaint document")

    return {"success": True}, email_kwargs


def student_reply(complaint_id: str, raw_message: str) -> tuple[dict, dict | None]:
    """
    Returns (response_dict, email_kwargs_or_None).
    The caller schedules the email as a BackgroundTask.
    """
    context_messages = fetch_last_n_messages(complaint_id, n=5)
    print(f"[CONTEXT] Fetched {len(context_messages)} messages for complaint {complaint_id}")

    enhanced = enhance_message_with_context(raw_message, context_messages)

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

    admin_id = complaint_data.get("assignedAdminId", "")
    print(f"[EMAIL DEBUG] student_reply: admin_id={admin_id!r}")

    email_kwargs = None
    if admin_id and admin_id != "unassigned":
        admin_info  = get_admin_info(admin_id)
        admin_email = admin_info.get("email", "").strip()
        print(f"[EMAIL DEBUG] student_reply: admin_email={admin_email!r}")
        if admin_email:
            email_kwargs = {
                "admin_email":    admin_email,
                "admin_name":     admin_info.get("displayName", "Admin"),
                "student_name":   complaint_data.get("studentName", "Student"),
                "tracking_id":    complaint_data.get("trackingId", ""),
                "short_title":    complaint_data.get("shortTitle", ""),
                "enhanced_reply": enhanced,
            }
        else:
            print("[EMAIL DEBUG] Skipped — admin email missing from users document")
    else:
        print(f"[EMAIL DEBUG] Skipped — no assigned admin (admin_id={admin_id!r})")

    return {
        "enhanced_message": enhanced,
        "studentFlag":      "yellow",
        "adminFlag":        "red"
    }, email_kwargs
