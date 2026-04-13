# email_client.py
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────────────────────
# CORE SEND FUNCTION
# ──────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html: str):
    """Single send function. All email calls go through here."""

    # Read on every call — NOT at module load time.
    # On Render, module-level os.getenv() can fire before env vars are injected,
    # leaving both variables as None permanently and silently skipping all emails.
    gmail_user     = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    print(f"[EMAIL] Attempting send → to={to!r}  user_set={bool(gmail_user)}  pass_set={bool(gmail_password)}")

    if not gmail_user or not gmail_password:
        print("[EMAIL] Skipped — GMAIL_USER or GMAIL_APP_PASSWORD not set in environment")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Project R3i <{gmail_user}>"
        msg["To"]      = to
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to, msg.as_string())

        print(f"[EMAIL] Sent successfully → {to}")

    except Exception as e:
        # Email failure never crashes the main flow
        print(f"[EMAIL ERROR] {e}")


# ──────────────────────────────────────────────────────────────
# EMAIL TEMPLATES
# ──────────────────────────────────────────────────────────────

def email_admin_new_complaint(
    admin_email:      str,
    admin_name:       str,
    student_name:     str,
    tracking_id:      str,
    category:         str,
    short_title:      str,
    enhanced_message: str,
    room_number:      str,
    contact_number:   str,
    roll_number:      str,
):
    subject = f"[R3i] New Complaint Assigned — {tracking_id}"
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;
                border:1px solid #e5e7eb;border-radius:8px;">
      <h2 style="color:#7C3AED;">New Complaint Assigned to You</h2>
      <p>Hi <strong>{admin_name}</strong>,</p>
      <p>A new complaint has been registered under your department
         (<strong>{category}</strong>).</p>

      <table style="width:100%;border-collapse:collapse;margin:16px 0;">
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;width:140px;">Tracking ID</td>
            <td style="padding:8px;">{tracking_id}</td></tr>
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;">Title</td>
            <td style="padding:8px;">{short_title}</td></tr>
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;">Student</td>
            <td style="padding:8px;">{student_name}</td></tr>
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;">Roll Number</td>
            <td style="padding:8px;">{roll_number}</td></tr>
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;">Room</td>
            <td style="padding:8px;">{room_number}</td></tr>
        <tr><td style="padding:8px;background:#F3F4F6;font-weight:bold;">Contact</td>
            <td style="padding:8px;">{contact_number}</td></tr>
      </table>

      <div style="background:#F9FAFB;padding:12px;border-left:4px solid #7C3AED;margin:16px 0;">
        <strong>Complaint:</strong><br/>{enhanced_message}
      </div>

      <p style="color:#6B7280;">Please log in to the R3i dashboard to respond.</p>
    </div>
    """
    send_email(admin_email, subject, html)


def email_student_admin_replied(
    student_email:  str,
    student_name:   str,
    tracking_id:    str,
    short_title:    str,
    admin_response: str,
):
    subject = f"[R3i] Update on Your Complaint — {tracking_id}"
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;
                border:1px solid #e5e7eb;border-radius:8px;">
      <h2 style="color:#7C3AED;">Admin Has Responded to Your Complaint</h2>
      <p>Hi <strong>{student_name}</strong>,</p>
      <p>The admin has replied to your complaint
         <strong>{tracking_id} — {short_title}</strong>.</p>

      <div style="background:#FEF3C7;padding:12px;border-left:4px solid #F59E0B;margin:16px 0;">
        <strong>Admin's Response:</strong><br/>{admin_response}
      </div>

      <p style="color:#6B7280;">Please log in to the R3i portal to reply.</p>
    </div>
    """
    send_email(student_email, subject, html)


def email_student_resolved(
    student_email:  str,
    student_name:   str,
    tracking_id:    str,
    short_title:    str,
    admin_response: str,
):
    subject = f"[R3i] Complaint Resolved — {tracking_id}"
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;
                border:1px solid #e5e7eb;border-radius:8px;">
      <h2 style="color:#10B981;">Your Complaint Has Been Resolved</h2>
      <p>Hi <strong>{student_name}</strong>,</p>
      <p>Your complaint <strong>{tracking_id} — {short_title}</strong>
         has been marked as resolved.</p>

      <div style="background:#D1FAE5;padding:12px;border-left:4px solid #10B981;margin:16px 0;">
        <strong>Resolution Note:</strong><br/>{admin_response}
      </div>

      <p style="color:#6B7280;">
        The complaint is now closed. If the issue persists, you may submit a new complaint.
      </p>
    </div>
    """
    send_email(student_email, subject, html)


def email_admin_student_replied(
    admin_email:    str,
    admin_name:     str,
    student_name:   str,
    tracking_id:    str,
    short_title:    str,
    enhanced_reply: str,
):
    subject = f"[R3i] Student Replied — {tracking_id}"
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;
                border:1px solid #e5e7eb;border-radius:8px;">
      <h2 style="color:#7C3AED;">Student Has Replied to Your Message</h2>
      <p>Hi <strong>{admin_name}</strong>,</p>
      <p><strong>{student_name}</strong> has replied to complaint
         <strong>{tracking_id} — {short_title}</strong>.</p>

      <div style="background:#F3F4F6;padding:12px;border-left:4px solid #7C3AED;margin:16px 0;">
        <strong>Student's Reply:</strong><br/>{enhanced_reply}
      </div>

      <p style="color:#6B7280;">Please log in to the R3i dashboard to respond.</p>
    </div>
    """
    send_email(admin_email, subject, html)
