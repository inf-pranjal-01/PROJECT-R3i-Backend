# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from r3i_agent import (
    CATEGORIES,
    categorize_complaint,
    apply_manual_category,
    register_complaint,
    cancel_registration,
    admin_send_message,
    student_reply
)
from firebase_client import db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Models ───────────────────────────────────────────────────────

class CategorizeRequest(BaseModel):
    message: str

class ManualCategoryRequest(BaseModel):
    selected_category: str
    short_title:       str

class RegisterRequest(BaseModel):
    student_id:  str
    raw_message: str
    category:    str
    short_title: str
    confidence:  float

class StudentReplyRequest(BaseModel):
    complaint_id: str
    message:      str

class AdminMessageRequest(BaseModel):
    complaint_id:  str
    response:      str
    status_update: str   # "action" | "resolved"

class OnboardRequest(BaseModel):
    uid:           str
    displayName:   str
    email:         str
    role:          str
    rollNumber:    Optional[str] = ""
    roomNumber:    Optional[str] = ""
    contactNumber: Optional[str] = ""
    category:      Optional[str] = ""

class TestEmailRequest(BaseModel):
    to: str   # send a test email to this address


# ── Routes ───────────────────────────────────────────────────────────────

@app.post("/chat/categorize")
def route_categorize(req: CategorizeRequest):
    try:
        return categorize_complaint(req.message)
    except Exception as e:
        return {"error": str(e)}


@app.post("/chat/select-category")
def route_select_category(req: ManualCategoryRequest):
    try:
        return apply_manual_category(req.selected_category, req.short_title)
    except Exception as e:
        return {"error": str(e)}


@app.post("/chat/register")
def route_register(req: RegisterRequest):
    try:
        category_data = {
            "category":    req.category,
            "short_title": req.short_title,
            "confidence":  req.confidence,
        }
        return register_complaint(req.student_id, category_data, req.raw_message)
    except Exception as e:
        return {"error": str(e)}


@app.post("/chat/cancel")
def route_cancel():
    return cancel_registration()


@app.post("/admin/message")
def route_admin_message(req: AdminMessageRequest):
    try:
        return admin_send_message(req.complaint_id, req.response, req.status_update)
    except Exception as e:
        return {"error": str(e)}


@app.post("/chat/reply")
def route_student_reply(req: StudentReplyRequest):
    try:
        return student_reply(req.complaint_id, req.message)
    except Exception as e:
        return {"error": str(e)}


@app.post("/onboard")
def route_onboard(req: OnboardRequest):
    try:
        data = {
            "uid":           req.uid,
            "displayName":   req.displayName,
            "email":         req.email,
            "role":          req.role,
            "rollNumber":    req.rollNumber,
            "roomNumber":    req.roomNumber,
            "contactNumber": req.contactNumber,
            "category":      req.category,
            "createdAt":     SERVER_TIMESTAMP,
        }
        db.collection("users").document(req.uid).set(data)

        if req.role == "admin" and req.category:
            db.collection("category_routing").document(req.category).set({
                "adminId":    req.uid,
                "adminEmail": req.email,
            })

        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.get("/admin/complaints/{admin_id}")
def route_get_admin_complaints(admin_id: str):
    try:
        docs = (
            db.collection("complaints")
            .where("assignedAdminId", "==", admin_id)
            .order_by("lastUpdated", direction="DESCENDING")
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception as e:
        return {"error": str(e)}


@app.get("/complaint/{complaint_id}/messages")
def route_get_messages(complaint_id: str):
    try:
        docs = (
            db.collection("complaints")
            .document(complaint_id)
            .collection("messages")
            .order_by("createdAt")
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception as e:
        return {"error": str(e)}


@app.get("/categories")
def route_get_categories():
    return {"categories": CATEGORIES}


# ── Email Diagnostic ─────────────────────────────────────────────────────
# Hit this endpoint from Render's shell or from curl/Postman to verify that
# GMAIL_USER + GMAIL_APP_PASSWORD are loaded correctly and SMTP works.
#
# Usage:
#   POST /test-email
#   { "to": "youremail@gmail.com" }
#
# Watch the Render logs for detailed output.
@app.post("/test-email")
def route_test_email(req: TestEmailRequest):
    import os
    gmail_user     = os.getenv("GMAIL_USER", "").strip()
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()

    # Report what Render actually sees — safe to log user, NOT password
    env_status = {
        "GMAIL_USER_set":      bool(gmail_user),
        "GMAIL_USER_value":    gmail_user,
        "APP_PASSWORD_set":    bool(gmail_password),
        "APP_PASSWORD_length": len(gmail_password),
        "APP_PASSWORD_is_16":  len(gmail_password) == 16,
    }
    print(f"[TEST-EMAIL] env_status={env_status}")

    if not gmail_user or not gmail_password:
        return {
            "success": False,
            "reason":  "Env vars missing on Render. Add GMAIL_USER and GMAIL_APP_PASSWORD in Environment settings.",
            "env_status": env_status,
        }

    if len(gmail_password) != 16:
        return {
            "success": False,
            "reason":  f"App password is {len(gmail_password)} chars — must be exactly 16. "
                       "Copy it from Google → Manage Account → Security → App Passwords. "
                       "Paste WITHOUT spaces.",
            "env_status": env_status,
        }

    try:
        import smtplib, ssl
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "[R3i] Test Email — SMTP working ✓"
        msg["From"]    = f"Project R3i <{gmail_user}>"
        msg["To"]      = req.to
        msg.attach(MIMEText(
            "<p>If you can read this, your R3i email setup is working correctly. 🎉</p>",
            "html"
        ))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, req.to, msg.as_string())

        print(f"[TEST-EMAIL] ✓ Test email sent to {req.to}")
        return {
            "success":    True,
            "message":    f"Test email sent to {req.to}. Check your inbox (and spam).",
            "env_status": env_status,
        }

    except smtplib.SMTPAuthenticationError as e:
        return {
            "success": False,
            "reason":  f"SMTPAuthenticationError: {e}. "
                       "Make sure 2-Step Verification is ON and you are using an App Password, "
                       "NOT your regular Gmail password. "
                       "Generate one at https://myaccount.google.com/apppasswords",
            "env_status": env_status,
        }
    except Exception as e:
        return {
            "success": False,
            "reason":  str(e),
            "env_status": env_status,
        }
