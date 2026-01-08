from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv
import requests, base64, os, json, re
import uuid 
import datetime
from datetime import datetime
# ================= ENV =================
load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MAILEROO_API_KEY = os.getenv("MAILEROO_API_KEY")

if not OPENAI_API_KEY or not MAILEROO_API_KEY:
    raise RuntimeError("Missing API keys")

client = OpenAI(api_key=OPENAI_API_KEY)

# ================= APP =================
app = FastAPI(title="CityGuardian – Agentic Civic AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def safe_json_load(raw: str):
    if not raw:
        raise ValueError("Empty LLM response")

    raw = raw.strip()

    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()

    return json.loads(raw)

# ================= DEPARTMENTS =================
OFFICERS = [
    {
        "name": "Water Supply Department",
        "email": "shivamkillarikar007@gmail.com",
        "keywords": ["water", "leak", "pipe", "no water", "dirty water", "tanker"]
    },
    {
        "name": "Sewage & Drainage Department",
        "email": "shivamkillarikar22@gmail.com",
        "keywords": ["sewage", "drain", "gutter", "overflow", "blocked", "smell"]
    },
    {
        "name": "Roads & Traffic Department",
        "email": "aishanidolan@gmail.com",
        "keywords": ["road", "pothole", "traffic", "signal", "accident"]
    },
    {
        "name": "Electricity Department",
        "email": "adityakillarikar@gmail.com",
        "keywords": ["street light", "power cut", "wire", "pole", "shock"]
    },
]

DEFAULT_EMAIL = "shivamkillarikar22@gmail.com"
ALLOWED_EMAILS = {o["email"] for o in OFFICERS}

# ================= MAIL =================
def send_email_maileroo(subject, body, to_email, attachment=None):
    payload = {
    "from": {
        "address": "no-reply@ead86fd4bcfd6c15.maileroo.org",
        "display_name": "CityGuardian"
    },
    "to": [{"address": to_email}],
    "subject": subject,
    "text": body,
    "html": body.replace("\n", "<br>")
}


    if attachment:
        payload["attachments"] = [attachment]

    res = requests.post(
        "https://smtp.maileroo.com/api/v2/emails",
        headers={
            "Authorization": f"Bearer {MAILEROO_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15
    )

    if res.status_code not in [200, 201, 202]:
        raise RuntimeError(res.text)

# ================= AGENTS =================

def drafting_agent(name, email, complaint, location, category, urgency):
    prompt = f"""
You are an AI assistant writing official municipal emails.

Write a detailed, professional civic complaint email.

Rules:
- Minimum 3 paragraphs
- Formal tone
- Explain the problem clearly
- Mention public inconvenience
- Mention urgency politely
- End with a request for timely action

Citizen Name: {name}
Citizen Email: {email}

Complaint Category: {category}
Urgency Level: {urgency}

Complaint Description:
{complaint}

Location:
{location}
"""

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )

    return r.choices[0].message.content.strip()


def classification_agent(complaint: str):
    prompt = f"""
Classify the civic complaint.

Complaint:
{complaint}

Respond ONLY in JSON:
{{"category": "...", "urgency": "low|medium|high"}}
"""

    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        raw = r.choices[0].message.content
        return safe_json_load(raw)

    except Exception as e:
        print("⚠️ Classification failed:", e)
        return {
            "category": "general",
            "urgency": "medium"
        }


def keyword_router(complaint):
    tokens = set(re.findall(r"\b[a-z]+\b", complaint.lower()))
    best, score = None, 0

    for dept in OFFICERS:
        s = sum(1 for kw in dept["keywords"] if kw in tokens)
        if s > score:
            best, score = dept, s

    return best, score

def routing_agent(category: str, location: str):
    prompt = f"""
You are an AI routing agent.

Complaint category: {category}
Location: {location}

Available officers (USE ONLY THESE):
{OFFICERS}

Respond ONLY in JSON:
{{"name": "...", "email": "...", "reason": "..."}}
"""

    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        raw = r.choices[0].message.content
        data = safe_json_load(raw)

        if data["email"] not in ALLOWED_EMAILS:
            raise ValueError("Invalid email routed")

        return data

    except Exception as e:
        print("⚠️ Routing fallback:", e)
        return {
            "name": "General Grievance Cell",
            "email": DEFAULT_EMAIL,
            "reason": "AI routing failed, fallback applied"
        }


def verifier_agent(complaint, category, officer, reason):
    prompt = f"""
Verify this routing decision.

Complaint:
{complaint}

Category:
{category}

Chosen Department:
{officer}

Reason:
{reason}

Respond ONLY in JSON:
{{"approve": true|false, "confidence": 0-1}}
"""

    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        return safe_json_load(r.choices[0].message.content)

    except Exception as e:
        print("⚠️ Verifier failed:", e)
        return {"approve": False, "confidence": 0}





# ================= ROUTE =================
@app.post("/send-report")
async def send_report(
    name: str = Form(...),
    email: str = Form(...),
    complaint: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    image: UploadFile = File(None),
):
    attachment = None
    if image:
        img = await image.read()
        attachment = {
            "file_name": "complaint.jpg",
            "content": base64.b64encode(img).decode(),
            "type": image.content_type or "image/jpeg"
        }

    classification = classification_agent(complaint)
    report_id = str(uuid.uuid4())[:8] # Short unique ID
    n8n_data = {
        "ID": report_id,
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "name": name,
        "email": email,
        "issue": complaint,
        "category": classification['category'],
        "urgency": classification['urgency'],
        "location": f"{latitude}, {longitude}"
    }

    # 3. Trigger n8n Workflow
    try:
        # Replace with your actual n8n Webhook URL
        n8n_url = "https://shivam2212.app.n8n.cloud/webhook/city-report-intake"
        requests.post(n8n_url, json=n8n_data)
    except Exception as e:
        print(f"n8n trigger failed: {e}")

    # return {"status": "success", "id": report_id}

    dept, score = keyword_router(complaint)
    location_text = f"Latitude {latitude}, Longitude {longitude}"

    if dept and score > 0:
        routed_email = dept["email"]
        department_name = dept["name"]
        routing_reason = f"Matched {score} keywords"
    else:
        routed_email = DEFAULT_EMAIL
        department_name = "General Grievance Cell"
        routing_reason = "No strong keyword match"

    routing = routing_agent(
        classification["category"],
        location_text
    )

    email_body = drafting_agent(
    name=name,
    email=email,
    complaint=complaint,
    location=location_text,
    category=classification["category"],
    urgency=classification["urgency"]
)


    send_email_maileroo(
        subject="Civic Complaint Report (AI Routed)",
        body=email_body,
        to_email=routing["email"],
        attachment=attachment
    )

    # return {
    #     "status": "success",
    #     "department":department_name,
    #     "urgency": classification["urgency"],
    #     "routed email": routed_email
    # }
    return {
        "status": "success",
        "id": report_id,
        "department": department_name,
        "urgency": classification["urgency"],
        "routed_email": routing["email"]
    }


@app.get("/")
def health():
    return {"status": "CityGuardian backend running"}
