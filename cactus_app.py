import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image, ImageOps # ตัวช่วยแก้รูปกลับหัว
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage
import io
import json
import time

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Collector (Final)", page_icon="🌵")

BUCKET_NAME = "cactus-free-storage-2025" 

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

# --- 2. ฟังก์ชันค้นหาโมเดลอัตโนมัติ (แก้ปัญหา 404 ถาวร) ---
def get_best_available_model():
    try:
        # ดึงรายชื่อโมเดลทั้งหมดที่บัญชีนี้ใช้ได้จริง
        available_models = [m.name for m in genai.list_models()]
        
        # รายชื่อโมเดลที่เราอยากได้ (เรียงตามความเก่ง)
        preferred_order = [
            'models/gemini-1.5-flash',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-flash-001',
            'models/gemini-1.5-flash-002',
            'models/gemini-2.0-flash-exp',
            'models/gemini-flash-1.5',
            'models/gemini-pro',       # รุ่นเก่าแต่ชัวร์
            'models/gemini-1.0-pro'
        ]
        
        # วนลูปหา: ตัวไหนเจอในบัญชีคุณ หยิบตัวนั้นเลย
        for model in preferred_order:
            if model in available_models:
                # ตัดคำว่า models/ ออกเพื่อให้ library ใช้งานได้
                return model.replace('models/', '')
        
        # ถ้าไม่เจอในลิสต์ข้างบนเลย ให้ใช้ตัวแรกสุดที่รองรับการสร้างข้อความ
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                return m.name.replace('models/', '')
                
    except Exception as e:
        return 'gemini-1.5-flash' # Fallback สุดท้าย
        
    return 'gemini-1.5-flash'

# --- 3. ฟังก์ชัน Cloud Storage ---
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

# --- 4. ฟังก์ชัน Google Sheet ---
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

# --- 5. ฟังก์ชัน AI (Auto Mode) ---
def analyze_image(image):
    # เรียกฟังก์ชันหาโมเดลอัตโนมัติ
    model_name = get_best_available_model()
    
    # (Optional) แสดงชื่อโมเดลที่ระบบเลือกให้ (เอาไว้เช็คได้)
    # st.toast(f"Using Model: {model_name}") 
    
    try:
        model = genai.GenerativeModel(model_name)
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
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": f"Model: {model_name}"}

# --- 6. หน้าจอแอพ ---
st.title("🌵 บันทึกแคคตัส (Final Fixed)")

uploaded_file = st.file_uploader(
    "เลือกรูปภาพ (แก้กลับหัวอัตโนมัติ)", 
    type=["jpg", "jpeg", "png"],
    key=f"uploader_{st.session_state['uploader_key']}"
)

if uploaded_file is not None:
    # 1. แก้รูปกลับหัวทันที
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image)
    
    st.image(image, caption="รูปภาพ", width=300)
    
    # 2. AI ทำงานอัตโนมัติ (Auto Run)
    if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
        with st.spinner('กำลังค้นหาโมเดลและวิเคราะห์...'):
            st.session_state['ai_result'] = analyze_image(image)
            st.session_state['last_analyzed_file'] = uploaded_file.name
            
    # 3. แสดงฟอร์ม
    if 'ai_result' in st.session_state:
        data = st.session_state['ai_result']
        
        with st.form("save_form"):
            c1, c2 = st.columns(2)
            pot_no = c1.text_input("เลขกระถาง", data.get('pot_number'))
            species = c2.text_input("ชื่อวิทย์", data.get('species'))
            thai = st.text_input("ชื่อไทย", data.get('thai_name'))
            
            submit = st.form_submit_button("💾 บันทึกข้อมูล")
            
            if submit:
                with st.spinner('กำลังบันทึก...'):
                    # Save
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"Cactus_{pot_no}_{ts}.jpg"
                    img_byte = io.BytesIO()
                    image.save(img_byte, format='JPEG') # เซฟรูปที่แก้หมุนแล้ว
                    
                    link = upload_to_bucket(img_byte, fname)
                    
                    today = str(datetime.today().date())
                    append_to_sheet([today, pot_no, species, thai, link])
                    
                    st.success(f"✅ บันทึกเสร็จสิ้น!")
                    
                    # Reset
                    if 'ai_result' in st.session_state: del st.session_state['ai_result']
                    if 'last_analyzed_file' in st.session_state: del st.session_state['last_analyzed_file']
                    
                    st.session_state['uploader_key'] += 1
                    time.sleep(1) 
                    st.rerun()
