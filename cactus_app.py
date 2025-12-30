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
st.set_page_config(page_title="Cactus Collector AI (Pro)", page_icon="🌵")

# ⚠️ ชื่อ BUCKET ของคุณ (อันเดิมที่ใช้ได้แล้ว)
BUCKET_NAME = "cactus-free-storage-2025" 

# สร้าง Key สำหรับรีเซ็ตปุ่มอัปโหลด
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

# --- 2. ฟังก์ชันอัปโหลดไป Cloud Storage ---
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

# --- 3. ฟังก์ชันบันทึกลง Sheet ---
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

# --- 4. ฟังก์ชัน AI (อัปเกรดเป็นรุ่น Pro และจูน Prompt) ---
def analyze_image(image):
    # เปลี่ยนเป็นรุ่น Pro เพื่อความแม่นยำเรื่องสายพันธุ์
    model_name = 'gemini-1.5-pro' 
    
    try:
        model = genai.GenerativeModel(model_name)
        # ปรับ Prompt ให้เน้นแคคตัสโดยเฉพาะ
        prompt = """
        You are a botanist expert in Cactaceae (Cactus). 
        Analyze this image carefully:
        1. Identify the 'Sequence Number' written on the pot label/tag (return as string).
        2. Identify the 'Scientific Name' based on visual traits (ribs, spines, shape, dots). 
           Focus on genera like Astrophytum, Mammillaria, Gymnocalycium, Coryphantha, etc.
        3. Provide the common 'Thai Name' if known (e.g., แอสโตร, ยิมโน, แมมขนนก).
        
        Return ONLY JSON: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
        
    except Exception as e:
        # ถ้า Pro มีปัญหา ให้ถอยกลับไปใช้ Flash
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

# --- 5. หน้าจอแอพ (UI) ---
st.title("🌵 บันทึกแคคตัส (Smart Mode)")

# ใช้ Key เพื่อสั่งให้ปุ่มอัปโหลดรีเซ็ตตัวเองได้
uploaded_file = st.file_uploader(
    "ถ่ายรูป/เลือกรูปภาพ", 
    type=["jpg", "jpeg", "png"],
    key=f"uploader_{st.session_state['uploader_key']}" 
)

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="รูปปัจจุบัน", width=300)
    
    # ปุ่มวิเคราะห์
    if st.button("🔍 วิเคราะห์สายพันธุ์"):
        with st.spinner('กำลังส่องกล้องดูหนาม...'):
            st.session_state['ai_result'] = analyze_image(image)
            st.success("เรียบร้อย!")

    # แสดงผลและบันทึก
    if 'ai_result' in st.session_state:
        data = st.session_state['ai_result']
        with st.form("save_form"):
            c1, c2 = st.columns(2)
            pot_no = c1.text_input("เลขกระถาง", data.get('pot_number'))
            species = c2.text_input("ชื่อวิทย์ (แก้ได้)", data.get('species'))
            thai = st.text_input("ชื่อไทย (แก้ได้)", data.get('thai_name'))
            
            submit = st.form_submit_button("💾 บันทึกและเริ่มต้นใหม่")
            
            if submit:
                with st.spinner('กำลังบันทึก...'):
                    # 1. อัปโหลด
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"Cactus_{pot_no}_{ts}.jpg"
                    img_byte = io.BytesIO()
                    image.save(img_byte, format='JPEG')
                    
                    link = upload_to_bucket(img_byte, fname)
                    
                    # 2. ลง Sheet
                    today = str(datetime.today().date())
                    append_to_sheet([today, pot_no, species, thai, link])
                    
                    st.success(f"บันทึกต้นที่ {pot_no} แล้ว!")
                    
                    # 3. เคลียร์ค่า เตรียมต้นต่อไป (UX Fix)
                    del st.session_state['ai_result']
                    st.session_state['uploader_key'] += 1 # เปลี่ยน Key เพื่อล้างรูป
                    time.sleep(1) # หน่วงนิดนึงให้คนเห็น Success message
                    st.rerun() # รีโหลดหน้าจอใหม่
