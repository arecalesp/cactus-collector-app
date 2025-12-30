import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image, ImageOps # เพิ่ม ImageOps เพื่อแก้รูปกลับหัว
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage
import io
import json
import time

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Collector (Fix Rotation)", page_icon="🌵")

BUCKET_NAME = "cactus-free-storage-2025" # ชื่อ Bucket เดิมของคุณ

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

# --- 4. ฟังก์ชัน AI ---
def analyze_image(image):
    # ลองระบุเวอร์ชัน 002 (ตัวล่าสุดที่เสถียร) แทนการใช้ชื่อ Alias
    # ถ้าตัวนี้ไม่ได้ จะลองถอยไปรุ่น gemini-pro (รุ่น 1.0)
    model_name = 'gemini-1.5-flash-002'
    
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
        # กรณีฉุกเฉิน: ใช้รุ่น Pro 1.0 (รุ่นเก่าแต่ชัวร์)
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": "เปลี่ยน model_name เป็น gemini-pro ดูครับ"}

# --- 5. หน้าจอแอพ ---
st.title("🌵 บันทึกแคคตัส (Auto + Fix Rotation)")

uploaded_file = st.file_uploader(
    "เลือกรูปปุ๊บ วิเคราะห์ปั๊บ", 
    type=["jpg", "jpeg", "png"],
    key=f"uploader_{st.session_state['uploader_key']}"
)

if uploaded_file is not None:
    # เปิดรูปและแก้ Orientation ทันที
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image) # <--- บรรทัดนี้แก้รูปนอนเป็นตั้ง
    
    st.image(image, caption="ภาพต้นไม้ (แก้ทิศทางแล้ว)", width=300)
    
    # AI Auto Run
    if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
        with st.spinner('🤖 AI กำลังทำงาน...'):
            st.session_state['ai_result'] = analyze_image(image)
            st.session_state['last_analyzed_file'] = uploaded_file.name
            
    # Form
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
                    
                    # เซฟรูปแบบที่หมุนถูกต้องแล้วลง Cloud
                    image.save(img_byte, format='JPEG') 
                    
                    link = upload_to_bucket(img_byte, fname)
                    
                    today = str(datetime.today().date())
                    append_to_sheet([today, pot_no, species, thai, link])
                    
                    st.success(f"✅ บันทึกเรียบร้อย!")
                    
                    # Reset
                    if 'ai_result' in st.session_state: del st.session_state['ai_result']
                    if 'last_analyzed_file' in st.session_state: del st.session_state['last_analyzed_file']
                    
                    st.session_state['uploader_key'] += 1
                    time.sleep(1) 
                    st.rerun()
