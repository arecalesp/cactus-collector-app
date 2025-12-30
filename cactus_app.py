import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage
import io
import json
import time

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Collector (Auto)", page_icon="🌵")

BUCKET_NAME = "cactus-free-storage-2025" # ชื่อ Bucket เดิมของคุณ

# ตัวช่วยรีเซ็ตปุ่มอัปโหลด
if 'uploader_key' not in st.session_state:
    st.session_state['uploader_key'] = 0

try:
    GEMINI_API_KEY = st.secrets["gemini_api_key"]
    SHEET_ID = st.secrets["sheet_id"]
    GCP_CREDS_DICT = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"Secret Error: {e}")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
creds = service_account.Credentials.from_service_account_info(GCP_CREDS_DICT)

# --- 2. ฟังก์ชัน Cloud Storage ---
def upload_to_bucket(file_obj, filename):
    try:
        client = storage.Client(credentials=creds, project=GCP_CREDS_DICT["project_id"])
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        file_obj.seek(0)
        blob.upload_from_file(file_obj, content_type='image/jpeg')
        return f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Upload Error: {e}"

# --- 3. ฟังก์ชัน Google Sheet ---
def append_to_sheet(data_row):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data_row]}
    sheet.values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

# --- 4. ฟังก์ชัน AI (Auto Analyze) ---
def analyze_image(image):
    # เปลี่ยนมาใช้ 2.0 Flash (ฉลาดกว่า 1.5 Flash และบัญชีคุณน่าจะมีสิทธิ์เข้าถึง)
    model_name = 'gemini-2.0-flash'
    
    try:
        model = genai.GenerativeModel(model_name)
        # Prompt เน้นย้ำเรื่องชื่อวิทย์และเลขกระถาง
        prompt = """
        You are a Cactus expert. Look at the image directly.
        1. Find 'Sequence Number' on the tag (digits only).
        2. Identify 'Scientific Name' based on appearance (e.g. Astrophytum asterias, Mammillaria plumosa).
        3. Identify 'Thai Name' (e.g. แอสโตร, แมมขนนก).
        
        Return ONLY JSON: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
        
    except Exception as e:
        # Fallback: ถ้า 2.0 พัง ให้ถอยกลับไปใช้ตัว Flash ธรรมดา
        return {"pot_number": "", "species": f"AI Error: {e}", "thai_name": "โปรดระบุเอง"}

# --- 5. หน้าจอแอพ (ระบบ Auto) ---
st.title("🌵 บันทึกแคคตัส (Auto Mode)")

# ช่องอัปโหลด (มี key ไว้สำหรับรีเซ็ต)
uploaded_file = st.file_uploader(
    "เลือกรูปปุ๊บ วิเคราะห์ปั๊บ", 
    type=["jpg", "jpeg", "png"],
    key=f"uploader_{st.session_state['uploader_key']}"
)

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="ภาพต้นไม้", width=300)
    
    # --- ส่วนทำงานอัตโนมัติ (ไม่ต้องกดปุ่ม) ---
    # เช็คว่ารูปนี้ถูกวิเคราะห์ไปหรือยัง (โดยดูชื่อไฟล์)
    if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
        with st.spinner('🤖 AI กำลังทำงานอัตโนมัติ...'):
            st.session_state['ai_result'] = analyze_image(image)
            st.session_state['last_analyzed_file'] = uploaded_file.name # จำชื่อไฟล์ไว้ กันมันรันซ้ำ
            
    # แสดงฟอร์มเมื่อมีผลลัพธ์
    if 'ai_result' in st.session_state:
        data = st.session_state['ai_result']
        
        with st.form("save_form"):
            c1, c2 = st.columns(2)
            pot_no = c1.text_input("เลขกระถาง", data.get('pot_number'))
            species = c2.text_input("ชื่อวิทย์", data.get('species'))
            thai = st.text_input("ชื่อไทย", data.get('thai_name'))
            
            # ปุ่มบันทึก (กดแล้วจะล้างทุกอย่าง)
            submit = st.form_submit_button("💾 บันทึกข้อมูล")
            
            if submit:
                with st.spinner('กำลังบันทึกและรีเซ็ต...'):
                    # 1. อัปโหลด
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"Cactus_{pot_no}_{ts}.jpg"
                    img_byte = io.BytesIO()
                    image.save(img_byte, format='JPEG')
                    link = upload_to_bucket(img_byte, fname)
                    
                    # 2. ลง Sheet
                    today = str(datetime.today().date())
                    append_to_sheet([today, pot_no, species, thai, link])
                    
                    st.success(f"✅ บันทึกเบอร์ {pot_no} เรียบร้อย!")
                    
                    # 3. ล้างค่าทุกอย่าง + รีเซ็ตปุ่มอัปโหลด
                    if 'ai_result' in st.session_state: del st.session_state['ai_result']
                    if 'last_analyzed_file' in st.session_state: del st.session_state['last_analyzed_file']
                    
                    st.session_state['uploader_key'] += 1 # เปลี่ยน Key เพื่อเคลียร์รูปออกจากช่อง
                    time.sleep(1) 
                    st.rerun() # รีโหลดหน้าจอ
