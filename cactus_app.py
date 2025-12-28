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
import time

# --- Config & Setup ---
st.set_page_config(page_title="Cactus Collector AI (Bulk Mode)", page_icon="🌵", layout="wide")

# โหลด Secrets
try:
    GEMINI_API_KEY = st.secrets["gemini_api_key"]
    SHEET_ID = st.secrets["sheet_id"]
    DRIVE_FOLDER_ID = st.secrets["drive_folder_id"]
    GCP_CREDS_DICT = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"❌ ตั้งค่า Secrets ไม่ครบ: {e}")
    st.stop()

genai.configure(api_key=GEMINI_API_KEY)

# ตั้งค่า Google Drive/Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
creds = service_account.Credentials.from_service_account_info(GCP_CREDS_DICT, scopes=SCOPES)

def append_to_sheet(data_row):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data_row]}
    return sheet.values().append(
        spreadsheetId=SHEET_ID, range="Sheet1!A:E", valueInputOption="USER_ENTERED", body=body
    ).execute()

def upload_to_drive(file_obj, filename):
    service = build('drive', 'v3', credentials=creds)
    file_metadata = {'name': filename, 'parents': [DRIVE_FOLDER_ID]}
    file_obj.seek(0)
    media = MediaIoBaseUpload(file_obj, mimetype='image/jpeg', resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    return file.get('webViewLink')

def analyze_image(image):
    # รายชื่อโมเดลที่จะไล่ลองใช้ (เรียงลำดับความแม่นยำ -> ความเร็ว)
    model_candidates = [
        'gemini-1.5-flash',
        'gemini-1.5-pro',
        'gemini-1.0-pro-vision-latest'
    ]
    
    prompt = """
    Analyze this cactus image.
    1. Read the sequence number on the pot label/tag.
    2. Identify the Scientific Name.
    3. Identify the Thai Name.
    
    Return ONLY valid JSON:
    { "pot_number": "...", "species": "...", "thai_name": "..." }
    """

    last_error = ""
    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(model_name)
            # เพิ่ม delay นิดหน่อยเพื่อกัน Limit
            time.sleep(1) 
            response = model.generate_content([prompt, image])
            text = response.text.strip()
            # Clean Markdown
            if text.startswith("```json"): text = text[7:-3]
            elif text.startswith("```"): text = text[3:-3]
            return json.loads(text), None # Success
        except Exception as e:
            last_error = f"{model_name}: {str(e)}"
            print(f"Failed {model_name}: {e}")
            continue
            
    # ถ้าหลุดลูปมาแสดงว่าพังหมด
    return {"pot_number": "", "species": "Unknown", "thai_name": "Unknown"}, last_error

# --- UI Application ---
st.title("🌵 Cactus Collector (Batch Upload)")
st.info("อัปโหลดหลายรูปพร้อมกันได้เลย ระบบจะสแกนทีเดียว")

# 1. Upload Section
uploaded_files = st.file_uploader("เลือกรูปแคคตัสทั้งหมด", type=["jpg", "png"], accept_multiple_files=True)

# Session State สำหรับเก็บข้อมูลที่สแกนแล้ว
if 'scanned_data' not in st.session_state:
    st.session_state['scanned_data'] = []

if uploaded_files:
    # ปุ่มเริ่มสแกน (กดครั้งเดียว)
    if st.button(f"🔍 เริ่มสแกน AI ({len(uploaded_files)} รูป)"):
        st.session_state['scanned_data'] = [] # Reset
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"กำลังวิเคราะห์รูปที่ {i+1}/{len(uploaded_files)}: {uploaded_file.name}")
            
            image = Image.open(uploaded_file)
            ai_result, error_msg = analyze_image(image)
            
            # เก็บข้อมูลลง List
            st.session_state['scanned_data'].append({
                "file": uploaded_file,
                "img_obj": image,
                "data": ai_result,
                "error": error_msg,
                "id": i
            })
            progress_bar.progress((i + 1) / len(uploaded_files))
            
        status_text.text("✅ วิเคราะห์ครบแล้ว! กรุณาตรวจสอบข้อมูลด้านล่าง")

    # 2. Edit & Review Section
    if st.session_state['scanned_data']:
        with st.form("bulk_save_form"):
            st.subheader("📝 ตรวจสอบและแก้ไขก่อนบันทึก")
            
            # วนลูปแสดงผลลัพธ์ทีละรายการ
            valid_entries = [] # เก็บ Index ของรายการที่จะบันทึก
            
            for item in st.session_state['scanned_data']:
                idx = item['id']
                
                # ใช้ Expander เพื่อความสะอาดตา
                with st.expander(f"รูปที่ {idx+1}: {item['data'].get('pot_number', 'No Num')} - {item['data'].get('species', '?')}", expanded=True):
                    
                    # ถ้ามี Error จาก AI ให้โชว์สีแดง
                    if item['error']:
                        st.error(f"AI Warning: {item['error']}")
                    
                    col_img, col_form = st.columns([1, 3])
                    
                    with col_img:
                        st.image(item['img_obj'], use_container_width=True)
                    
                    with col_form:
                        # สร้าง Key ให้ไม่ซ้ำกันโดยใช้ index
                        p_num = st.text_input("เลขกระถาง", value=item['data'].get('pot_number', ''), key=f"pot_{idx}")
                        spec = st.text_input("พันธุ์ (Sci)", value=item['data'].get('species', ''), key=f"spec_{idx}")
                        thai = st.text_input("ชื่อไทย", value=item['data'].get('thai_name', ''), key=f"thai_{idx}")
                        
                        # อัปเดตข้อมูลกลับเข้าไปใน session_state แบบ Realtime (ผ่าน key)
                        item['final_data'] = {"pot": p_num, "spec": spec, "thai": thai}
            
            st.write("---")
            date_add = st.date_input("วันที่บันทึก", datetime.today())
            
            # ปุ่มบันทึกทั้งหมด
            if st.form_submit_button("💾 ยืนยันและบันทึกทั้งหมดลง Drive/Sheet"):
                progress_save = st.progress(0)
                status_save = st.empty()
                
                success_count = 0
                for i, item in enumerate(st.session_state['scanned_data']):
                    status_save.text(f"กำลังอัปโหลดรูปที่ {i+1}...")
                    
                    try:
                        final = item['final_data']
                        
                        # Prepare File Name
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        fname = f"Cactus_{final['pot']}_{timestamp}_{i}.jpg"
                        
                        # Upload Image
                        img_byte_arr = io.BytesIO()
                        item['img_obj'].save(img_byte_arr, format='JPEG')
                        link = upload_to_drive(img_byte_arr, fname)
                        
                        # Append Sheet
                        row = [str(date_add), final['pot'], final['spec'], final['thai'], link]
                        append_to_sheet(row)
                        success_count += 1
                        
                    except Exception as e:
                        st.error(f"บันทึกรูปที่ {i+1} ไม่สำเร็จ: {e}")
                    
                    progress_save.progress((i + 1) / len(st.session_state['scanned_data']))
                
                if success_count == len(st.session_state['scanned_data']):
                    st.success(f"🎉 บันทึกครบ {success_count} รายการเรียบร้อยแล้ว!")
                    st.session_state['scanned_data'] = [] # Clear
                else:
                    st.warning(f"บันทึกได้ {success_count} รายการ (มีบางรายการล้มเหลว)")
