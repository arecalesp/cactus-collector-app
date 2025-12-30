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

# --- 1. ตั้งค่าระบบ ---
st.set_page_config(page_title="Cactus Collector Pro", page_icon="🌵", layout="wide")

# ✅ ใส่ชื่อ Bucket ของคุณเรียบร้อยแล้ว
BUCKET_NAME = "cactus-free-storage-2025"

try:
    GEMINI_API_KEY = st.secrets["gemini_api_key"]
    SHEET_ID = st.secrets["sheet_id"]
    GCP_CREDS_DICT = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"Secret Error: {e}")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)
creds = service_account.Credentials.from_service_account_info(GCP_CREDS_DICT)

# --- 2. ฟังก์ชันต่างๆ ---

def fix_image_orientation(image):
    """แก้ปัญหารูปถ่ายจากมือถือแล้วตะแคง"""
    try:
        image = ImageOps.exif_transpose(image)
    except:
        pass
    return image

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

def append_to_sheet(data_row):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data_row]}
    sheet.values().append(
        spreadsheetId=SHEET_ID, range="Sheet1!A:E",
        valueInputOption="USER_ENTERED", body=body
    ).execute()

def get_all_cacti():
    """ดึงข้อมูลทั้งหมดจาก Google Sheet มาแสดงใน Dashboard"""
    try:
        service = build('sheets', 'v4', credentials=creds)
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SHEET_ID, range="Sheet1!A:E").execute()
        values = result.get('values', [])
        if not values:
            return pd.DataFrame()
        # ใช้แถวแรกเป็น Header
        df = pd.DataFrame(values[1:], columns=values[0])
        return df
    except Exception as e:
        st.error(f"อ่านข้อมูล Sheet ไม่ได้: {e}")
        return pd.DataFrame()

def analyze_image(image):
    # ✅ บัญชีใหม่ใช้รุ่นนี้ได้ชัวร์! ฟรี 1,500 รูป/วัน
    model_name = 'gemini-1.5-flash'
    
    try:
        model = genai.GenerativeModel(model_name)
        prompt = """
        Analyze this cactus image.
        1. Read sequence number on pot label (as integer string).
        2. Identify Species (Scientific Name).
        3. Identify Thai Name.
        Return JSON format: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
        
    except Exception as e:
        return {"pot_number": "", "species": f"Error ({model_name}): {e}", "thai_name": "เช็ค API Key ใน Secrets"}

# --- 3. ส่วนแสดงผล (UI) ---
st.title("🌵 Cactus Collector Pro")

# แบ่งหน้าจอเป็น 2 แท็บ
tab1, tab2 = st.tabs(["📸 เพิ่มต้นใหม่", "📊 Dashboard รายการทั้งหมด"])

# --- TAB 1: หน้าเพิ่มต้นไม้ ---
with tab1:
    uploaded_file = st.file_uploader("ถ่ายรูปหรือเลือกรูปภาพ", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        # โหลดภาพและแก้แนวตะแคงทันที
        image = Image.open(uploaded_file)
        image = fix_image_orientation(image)
        
        c1, c2 = st.columns([1, 2])
        with c1:
            st.image(image, caption="รูปปัจจุบัน", use_container_width=True)

        # Auto-Analyze: ถ้าเป็นรูปใหม่ ให้ AI ทำงานทันทีโดยไม่ต้องกดปุ่ม
        if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
            with c2:
                with st.spinner('🤖 AI กำลังสแกนหาเลขและระบุสายพันธุ์...'):
                    st.session_state.ai_result = analyze_image(image)
                    st.session_state.last_uploaded_file = uploaded_file.name

        # แสดงฟอร์มแก้ไขข้อมูล (จะโชว์หลังจาก AI ทำงานเสร็จ)
        if 'ai_result' in st.session_state:
            data = st.session_state.ai_result
            with c2:
                with st.form("save_form"):
                    st.subheader("📝 ตรวจสอบข้อมูล")
                    f_col1, f_col2 = st.columns(2)
                    pot_no = f_col1.text_input("เลขกระถาง", data.get('pot_number', ''))
                    species = f_col2.text_input("ชื่อวิทยาศาสตร์", data.get('species', ''))
                    thai_name = st.text_input("ชื่อภาษาไทย", data.get('thai_name', ''))
                    
                    submitted = st.form_submit_button("💾 ยืนยันและบันทึก")
                    
                    if submitted:
                        with st.spinner('กำลังอัปโหลดและบันทึก...'):
                            # 1. ตั้งชื่อไฟล์
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"Cactus_{pot_no}_{ts}.jpg"
                            
                            # 2. เตรียมรูป
                            img_byte = io.BytesIO()
                            image.save(img_byte, format='JPEG')
                            
                            # 3. อัปโหลดลง Bucket
                            img_link = upload_to_bucket(img_byte, filename)
                            
                            if "Upload Error" in img_link:
                                st.error(img_link)
                            else:
                                # 4. บันทึกลง Sheet
                                today = str(datetime.today().date())
                                append_to_sheet([today, pot_no, species, thai_name, img_link])
                                st.success(f"✅ บันทึกเบอร์ {pot_no} เรียบร้อย!")
                                
                                # ล้างค่า
                                del st.session_state['ai_result']
                                del st.session_state['last_uploaded_file']
                                st.rerun()

# --- TAB 2: Dashboard ---
with tab2:
    st.header("รายการแคคตัสทั้งหมด")
    if st.button("🔄 รีเฟรชข้อมูล"):
        st.rerun()
        
    df = get_all_cacti()
    
    if not df.empty:
        st.metric("จำนวนต้นไม้ทั้งหมด", f"{len(df)} ต้น")
        st.dataframe(df, use_container_width=True)
        
        st.subheader("🖼️ แกลเลอรี่")
        cols = st.columns(4)
        for index, row in df.iterrows():
            with cols[index % 4]:
                try:
                    if "http" in str(row.get('Image Link', '')):
                        st.image(row['Image Link'], caption=f"No.{row['Pot No.']}", use_container_width=True)
                except:
                    pass
    else:
        st.info("ยังไม่มีข้อมูล")
