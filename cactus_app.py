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

# --- 1. ตั้งค่าและเตรียมระบบ ---
st.set_page_config(page_title="Cactus Collector (Free Tier)", page_icon="🌵")

# ⚠️⚠️ แก้ชื่อ BUCKET ตรงนี้ให้เป็นชื่อที่คุณเพิ่งตั้ง (เช่น 'cactus-free-storage-2025') ⚠️⚠️
BUCKET_NAME = "cactus-free-storage-2025"

try:
    GEMINI_API_KEY = st.secrets["gemini_api_key"]
    SHEET_ID = st.secrets["sheet_id"]
    # ดึงข้อมูล Service Account จาก Secrets
    GCP_CREDS_DICT = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"Secret Error: กรุณาตั้งค่า Secrets ให้ครบถ้วน ({e})")
    st.stop()

# ตั้งค่า AI และ Google Cloud
genai.configure(api_key=GEMINI_API_KEY)
creds = service_account.Credentials.from_service_account_info(GCP_CREDS_DICT)

# --- 2. ฟังก์ชันอัปโหลดรูปขึ้น Cloud Storage (Free Tier Zone) ---
def upload_to_bucket(file_obj, filename):
    try:
        # เชื่อมต่อกับ Bucket
        client = storage.Client(credentials=creds, project=GCP_CREDS_DICT["project_id"])
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        
        # อัปโหลดไฟล์
        file_obj.seek(0)
        blob.upload_from_file(file_obj, content_type='image/jpeg')
        
        # คืนค่าเป็นลิงก์รูปภาพ (แบบ Public ดูได้เลย)
        return f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Upload Error: {e}"

# --- 3. ฟังก์ชันบันทึกข้อมูลลง Google Sheet ---
def append_to_sheet(data_row):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data_row]}
    sheet.values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:E", # A=Date, B=PotNo, C=Species, D=ThaiName, E=ImageLink
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

# --- 4. ฟังก์ชันให้ AI อ่านภาพ ---
def analyze_image(image):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = """
    Analyze this cactus image.
    1. Read sequence number on pot label (as integer string).
    2. Identify Species (Scientific Name).
    3. Identify Thai Name.
    Return JSON format: {"pot_number": "...", "species": "...", "thai_name": "..."}
    """
    response = model.generate_content([prompt, image])
    try:
        text = response.text.strip()
        # ล้าง format markdown ออกถ้ามี
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
    except:
        return {"pot_number": "", "species": "Unknown", "thai_name": "ไม่ทราบชื่อ"}

# --- 5. หน้าจอแอพพลิเคชัน ---
st.title("🌵 บันทึกข้อมูลแคคตัส (Free Zone)")
st.caption(f"Storage Bucket: {BUCKET_NAME} (US-Central1)")

uploaded_file = st.file_uploader("ถ่ายรูปหรือเลือกรูปภาพ", type=["jpg", "jpeg", "png"])

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="รูปที่เลือก", width=300)
    
    # ปุ่มกดให้ AI ทำงาน
    if st.button("🔍 วิเคราะห์ข้อมูล"):
        with st.spinner('AI กำลังอ่านป้ายชื่อและระบุสายพันธุ์...'):
            st.session_state['ai_result'] = analyze_image(image)
            st.success("วิเคราะห์เสร็จสิ้น!")

    # แบบฟอร์มบันทึกข้อมูล
    if 'ai_result' in st.session_state:
        data = st.session_state['ai_result']
        
        with st.form("save_data_form"):
            col1, col2 = st.columns(2)
            pot_no = col1.text_input("เลขกระถาง", data.get('pot_number'))
            species = col2.text_input("ชื่อวิทยาศาสตร์", data.get('species'))
            thai_name = st.text_input("ชื่อภาษาไทย", data.get('thai_name'))
            
            submit_btn = st.form_submit_button("💾 บันทึกข้อมูลและรูปภาพ")
            
            if submit_btn:
                with st.spinner('กำลังอัปโหลดรูปและบันทึกข้อมูล...'):
                    # 1. ตั้งชื่อไฟล์ให้ไม่ซ้ำ (ใช้เวลาปัจจุบันมาต่อท้าย)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"Cactus_{pot_no}_{timestamp}.jpg"
                    
                    # 2. แปลงรูปเตรียมอัปโหลด
                    img_byte = io.BytesIO()
                    image.save(img_byte, format='JPEG')
                    
                    # 3. อัปโหลดไป Bucket
                    img_link = upload_to_bucket(img_byte, filename)
                    
                    if "Upload Error" in img_link:
                        st.error(f"อัปโหลดรูปไม่ผ่าน: {img_link}")
                    else:
                        # 4. บันทึกลง Sheet
                        current_date = str(datetime.today().date())
                        append_to_sheet([current_date, pot_no, species, thai_name, img_link])
                        
                        st.success(f"✅ บันทึกต้นที่ {pot_no} เรียบร้อย!")
                        st.info(f"ลิงก์รูป: {img_link}") # โชว์ลิงก์ให้ดูเพื่อความชัวร์
                        
                        # ล้างค่า AI เพื่อเริ่มต้นใหม่
                        del st.session_state['ai_result']
