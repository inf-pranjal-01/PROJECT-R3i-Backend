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
    short_title:       str   # frontend sends back what it got from /chat/categorize

class RegisterRequest(BaseModel):
    student_id:  str
    raw_message: str
    # Frontend sends back exactly what it received from
    # /chat/categorize OR /chat/select-category — same shape either way
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
    category:      Optional[str] = ""   # admin only


# ── Routes ───────────────────────────────────────────────────────────────

# STEP 1 — LLM categorizes
#   high confidence → { low_confidence:false, category, short_title, message, buttons:["Yes","No"] }
#   low confidence  → { low_confidence:true, short_title, message, buttons:[7 categories] }
@app.post("/chat/categorize")
def route_categorize(req: CategorizeRequest):
    try:
        return categorize_complaint(req.message)
    except Exception as e:
        return {"error": str(e)}


# STEP 1B — User picked a category manually (low confidence path only)
# Frontend sends back: selected_category + short_title (from step 1 response)
# Returns same shape as high-confidence /chat/categorize → same Yes/No flow
@app.post("/chat/select-category")
def route_select_category(req: ManualCategoryRequest):
    try:
        return apply_manual_category(req.selected_category, req.short_title)
    except Exception as e:
        return {"error": str(e)}


# STEP 2A — Student clicked Yes → register complaint
# Enhancer runs on initial message before Firestore write
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


# STEP 2B — Student clicked No
@app.post("/chat/cancel")
def route_cancel():
    return cancel_registration()


# STEP 3A — Admin sends message or marks resolved
# status_update: "action" → student needs to respond
# status_update: "resolved" → chat is closed, both sides green
# Only admin ever calls this — student has no route to change status
@app.post("/admin/message")
def route_admin_message(req: AdminMessageRequest):
    try:
        return admin_send_message(req.complaint_id, req.response, req.status_update)
    except Exception as e:
        return {"error": str(e)}


# STEP 3B — Student replies to admin's message
# Enhancer runs. Admin sees enhanced version via Firestore onSnapshot.
@app.post("/chat/reply")
def route_student_reply(req: StudentReplyRequest):
    try:
        return student_reply(req.complaint_id, req.message)
    except Exception as e:
        return {"error": str(e)}


# ONBOARDING — Save user profile after Google login
# If admin, also writes into category_routing
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


# GET — All complaints assigned to an admin (for dashboard on load)
# onSnapshot handles live updates after first load
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


# GET — Messages subcollection for a complaint (for rendering chat history)
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


# GET — All categories (for frontend dropdowns, admin filters, etc.)
@app.get("/categories")
def route_get_categories():
    return {"categories": CATEGORIES}