import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image, ImageOps
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage
import io
import json
import time

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Collector (Auto-Fix)", page_icon="🌵")

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

# --- 2. ฟังก์ชัน "ผู้รอดชีวิต" (หาโมเดลที่ใช้งานได้จริง) ---
def find_working_model():
    # ถ้าเคยหาเจอแล้ว ให้ใช้ตัวเดิม ไม่ต้องหาใหม่ให้เสียเวลา
    if 'working_model_name' in st.session_state:
        return st.session_state['working_model_name']

    status_text = st.empty()
    status_text.warning("กำลังสแกนหาโมเดลที่ใช้งานได้... (อาจใช้เวลาสักครู่)")
    
    try:
        # 1. ดึงรายชื่อโมเดลทั้งหมดที่มี
        all_models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # จัดลำดับ: เอาพวก Flash ขึ้นก่อน (เพราะเร็วและประหยัด)
        # แต่กรองพวก Experimental ออกถ้าทำได้
        sorted_models = sorted(all_models, key=lambda x: ('flash' not in x.name, 'exp' in x.name))
        
        # 2. วนลูปเทสทีละตัว
        for m in sorted_models:
            model_name = m.name
            friendly_name = model_name.replace('models/', '')
            
            # ข้ามพวก 2.0 / 2.5 / exp ที่เราเรารู้ว่ามีปัญหา (ลองข้ามดูก่อน)
            if '2.0' in friendly_name or '2.5' in friendly_name or 'exp' in friendly_name:
                continue

            try:
                # ลองยิงคำถามสั้นๆ เพื่อเช็คของ
                test_model = genai.GenerativeModel(model_name)
                response = test_model.generate_content("test")
                
                if response.text:
                    # ถ้าตอบกลับมาได้ แสดงว่าตัวนี้แหละ! ผู้ถูกเลือก!
                    st.session_state['working_model_name'] = friendly_name
                    status_text.success(f"เจอแล้ว! ใช้โมเดล: {friendly_name}")
                    time.sleep(1)
                    status_text.empty()
                    return friendly_name
            except:
                continue # ตัวนี้พัง ไปตัวต่อไป
        
        # ถ้าวนลูปพวก Stable แล้วไม่เจอเลย... เอ้า! ยอมใช้พวก exp ก็ได้ (ไม้ตายก้นกุฏิ)
        for m in sorted_models:
             model_name = m.name.replace('models/', '')
             try:
                test_model = genai.GenerativeModel(model_name)
                test_model.generate_content("test")
                st.session_state['working_model_name'] = model_name
                return model_name
             except:
                continue

    except Exception as e:
        st.error(f"System Error: {e}")
    
    status_text.error("ไม่พบโมเดลที่ใช้งานได้เลยในบัญชีนี้ (กรุณาสร้าง API Key ใหม่ในโปรเจกต์ใหม่)")
    return None

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

# --- 5. ฟังก์ชัน AI (ใช้ตัวที่หาเจอ) ---
def analyze_image(image):
    model_name = find_working_model()
    
    if not model_name:
        return {"pot_number": "", "species": "Account Error", "thai_name": "เปลี่ยน API Key เถอะครับ"}

    try:
        model = genai.GenerativeModel(model_name)
        prompt = """
        You are a Cactus expert. Look at the image directly.
        1. Find 'Sequence Number' on the tag (digits only).
        2. Identify 'Scientific Name' based on appearance (e.g. Astrophytum asterias).
        3. Identify 'Thai Name' (e.g. แอสโตร).
        Return ONLY JSON: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
    except Exception as e:
        # ถ้าตัวที่เคยเทสผ่าน ดันมาตายตอนใช้วิเคราะห์รูป ให้ล้างค่าทิ้งแล้วหาใหม่รอบหน้า
        if 'working_model_name' in st.session_state:
            del st.session_state['working_model_name']
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

# --- 6. หน้าจอแอพ ---
st.title("🌵 บันทึกแคคตัส (Self-Healing)")

uploaded_file = st.file_uploader(
    "เลือกรูปภาพ", 
    type=["jpg", "jpeg", "png"],
    key=f"uploader_{st.session_state['uploader_key']}"
)

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image)
    st.image(image, caption="รูปภาพ", width=300)
    
    # Auto Run
    if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
        # เรียก AI ทำงาน (มันจะไปสแกนหาโมเดลเอง)
        with st.spinner('🤖 AI กำลังตรวจสอบระบบและวิเคราะห์...'):
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
            
            if st.form_submit_button("💾 บันทึกข้อมูล"):
                with st.spinner('กำลังบันทึก...'):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"Cactus_{pot_no}_{ts}.jpg"
                    img_byte = io.BytesIO()
                    image.save(img_byte, format='JPEG') 
                    link = upload_to_bucket(img_byte, fname)
                    
                    today = str(datetime.today().date())
                    append_to_sheet([today, pot_no, species, thai, link])
                    
                    st.success(f"✅ บันทึกเสร็จสิ้น!")
                    
                    if 'ai_result' in st.session_state: del st.session_state['ai_result']
                    if 'last_analyzed_file' in st.session_state: del st.session_state['last_analyzed_file']
                    st.session_state['uploader_key'] += 1
                    time.sleep(1) 
                    st.rerun()
