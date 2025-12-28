import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import json

# --- Config & Setup ---
st.set_page_config(page_title="Cactus Collector AI", page_icon="🌵")

# โหลด Secrets
try:
    GEMINI_API_KEY = st.secrets["gemini_api_key"]
    SHEET_ID = st.secrets["sheet_id"]
    DRIVE_FOLDER_ID = st.secrets["drive_folder_id"]
    GCP_CREDS_DICT = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"กรุณาตั้งค่า secrets.toml ให้ครบถ้วน: {e}")
    st.stop()

# ตั้งค่า Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ตั้งค่า Google APIs (Drive & Sheets)
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file'
]
creds = service_account.Credentials.from_service_account_info(
    GCP_CREDS_DICT, scopes=SCOPES
)

# ฟังก์ชันเชื่อมต่อ Google Sheets
def append_to_sheet(data_row):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data_row]}
    result = sheet.values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:E", # สมมติว่าเก็บข้อมูลที่ Sheet1
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()
    return result

# ฟังก์ชันอัปโหลดรูปไป Google Drive
def upload_to_drive(file_obj, filename):
    service = build('drive', 'v3', credentials=creds)
    
    file_metadata = {
        'name': filename,
        'parents': [DRIVE_FOLDER_ID]
    }
    
    # รีเซ็ต pointer ของไฟล์
    file_obj.seek(0)
    
    media = MediaIoBaseUpload(file_obj, mimetype='image/jpeg', resumable=True)
    
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink'
    ).execute()
    
    return file.get('webViewLink')

# ฟังก์ชันเรียก AI (Gemini)
def analyze_image(image):
    model = genai.GenerativeModel('gemini-1.5-flash') # ใช้รุ่น Flash เพื่อความเร็ว
    
    prompt = """
    Analyze this image of a cactus in a pot.
    1. Identify the number written on the pot label/tag (it is a sequence number). If not found, return empty string.
    2. Identify the cactus species (Scientific Name).
    3. Provide the common Thai name for this species (ชื่อภาษาไทย).
    
    Return the result strictly in JSON format with these keys:
    {
        "pot_number": "...",
        "species": "...",
        "thai_name": "..."
    }
    """
    
    response = model.generate_content([prompt, image])
    try:
        # พยายามแกะ JSON จาก Text (บางทีโมเดลอาจตอบ Markdown ```json ... ```)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3]
        return json.loads(text)
    except:
        return {"pot_number": "", "species": "Unknown", "thai_name": "ไม่ทราบชื่อ"}

# --- UI Application ---
st.title("🌵 บันทึกข้อมูลแคคตัส (AI Scanner)")
st.write("อัปโหลดรูปแคคตัสที่มีป้ายหมายเลข เพื่อบันทึกลงระบบ")

# อัปโหลดรูปภาพ
uploaded_file = st.file_uploader("ถ่ายรูปหรือเลือกรูปภาพ", type=["jpg", "jpeg", "png"])

if uploaded_file is not None:
    # แสดงรูปภาพ
    image = Image.open(uploaded_file)
    st.image(image, caption="รูปที่เลือก", use_container_width=True)
    
    # ปุ่มกดเพื่อให้ AI วิเคราะห์
    if st.button("🔍 ให้ AI อ่านข้อมูล"):
        with st.spinner('กำลังวิเคราะห์รูปภาพด้วย AI...'):
            ai_data = analyze_image(image)
            st.session_state['ai_result'] = ai_data
            st.success("วิเคราะห์เสร็จสิ้น!")

    # ถ้ามีผลลัพธ์จาก AI แล้ว ให้แสดงแบบฟอร์ม
    if 'ai_result' in st.session_state:
        data = st.session_state['ai_result']
        
        with st.form("cactus_form"):
            st.subheader("ตรวจสอบและแก้ไขข้อมูล")
            
            col1, col2 = st.columns(2)
            with col1:
                pot_number = st.text_input("หมายเลขกระถาง", value=data.get('pot_number', ''))
            with col2:
                # วันที่ปัจจุบัน
                date_added = st.date_input("วันที่เพิ่ม", datetime.today())
            
            species = st.text_input("ชื่อวิทยาศาสตร์ (Species)", value=data.get('species', ''))
            thai_name = st.text_input("ชื่อภาษาไทย", value=data.get('thai_name', ''))
            
            submitted = st.form_submit_button("💾 บันทึกข้อมูล")
            
            if submitted:
                with st.spinner('กำลังบันทึกลง Drive และ Sheets...'):
                    try:
                        # 1. เตรียมชื่อไฟล์
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        file_name = f"Cactus_{pot_number}_{timestamp}.jpg"
                        
                        # 2. Upload รูป
                        # ต้องแปลง PIL Image กลับเป็น BytesIO เพื่อ Upload
                        img_byte_arr = io.BytesIO()
                        image.save(img_byte_arr, format='JPEG')
                        drive_link = upload_to_drive(img_byte_arr, file_name)
                        
                        # 3. Save ลง Sheets
                        # ลำดับคอลัมน์: [วันที่, หมายเลข, สายพันธุ์, ชื่อไทย, ลิงก์รูป]
                        row_data = [
                            str(date_added),
                            pot_number,
                            species,
                            thai_name,
                            drive_link
                        ]
                        append_to_sheet(row_data)
                        
                        st.success(f"บันทึกข้อมูลต้นที่ {pot_number} เรียบร้อยแล้ว!")
                        # Clear session state เพื่อเริ่มต้นใหม่ถ้าต้องการ
                        del st.session_state['ai_result']
                        
                    except Exception as e:
                        st.error(f"เกิดข้อผิดพลาด: {e}")
