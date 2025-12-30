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
st.set_page_config(page_title="Cactus Manager Stable", page_icon="🌵", layout="wide")

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

# --- 2. ฟังก์ชัน Google Sheet (CRUD + Auto Fix Columns) ---
def get_sheet_service():
    return build('sheets', 'v4', credentials=creds)

def append_to_sheet(data_row):
    service = get_sheet_service()
    sheet = service.spreadsheets()
    # เติม Note ว่างๆ ไปด้วยเสมอ
    data_row.append("") 
    body = {'values': [data_row]}
    sheet.values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:F",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

def load_data_from_sheet():
    try:
        service = get_sheet_service()
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SHEET_ID, range="Sheet1!A:F").execute()
        values = result.get('values', [])
        
        if not values: return pd.DataFrame()
        
        # Header มาตรฐาน
        headers = ['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note']
        
        # คลีนข้อมูล: เติมช่องว่างให้ครบทุกแถว ป้องกัน Error column mismatch
        cleaned_data = []
        # ข้ามแถว Header (values[0]) ไปเริ่มที่ values[1]
        for row in values[1:]:
            while len(row) < len(headers):
                row.append("")
            cleaned_data.append(row[:len(headers)])
            
        df = pd.DataFrame(cleaned_data, columns=headers)
        return df
    except Exception as e:
        # กรณีโหลดไม่ได้ ให้คืนค่าตารางว่างๆ แอพจะได้ไม่ขาว
        st.error(f"โหลดข้อมูลผิดพลาด: {e}")
        return pd.DataFrame(columns=['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note'])

def update_sheet_row(row_index, pot_no, species, thai, note):
    sheet_row = row_index + 2
    service = get_sheet_service()
    sheet = service.spreadsheets()
    
    # Update ข้อมูลหลัก
    sheet.values().update(
        spreadsheetId=SHEET_ID, range=f"Sheet1!B{sheet_row}:D{sheet_row}",
        valueInputOption="USER_ENTERED", body={'values': [[pot_no, species, thai]]}
    ).execute()
    
    # Update Note
    sheet.values().update(
        spreadsheetId=SHEET_ID, range=f"Sheet1!F{sheet_row}",
        valueInputOption="USER_ENTERED", body={'values': [[note]]}
    ).execute()

def delete_sheet_row(row_index):
    sheet_row = row_index + 2
    service = get_sheet_service()
    api_row_index = sheet_row - 1 
    requests = [{"deleteDimension": {"range": {"sheetId": 0, "dimension": "ROWS", "startIndex": api_row_index, "endIndex": api_row_index + 1}}}]
    service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()

# --- 3. ฟังก์ชัน AI & Cloud (Optimized) ---
def find_working_model():
    if 'working_model_name' in st.session_state: return st.session_state['working_model_name']
    try:
        # พยายามใช้รุ่น Flash ที่เสถียรที่สุดก่อน
        preferred = ['gemini-1.5-flash-002', 'gemini-1.5-flash', 'gemini-1.5-flash-001']
        available = [m.name for m in genai.list_models()]
        
        for p in preferred:
            if f"models/{p}" in available:
                st.session_state['working_model_name'] = p
                return p
        return 'gemini-1.5-flash' # Default fallback
    except:
        return 'gemini-1.5-flash'

def analyze_image(image):
    model_name = find_working_model()
    try:
        model = genai.GenerativeModel(model_name)
        prompt = """
        You are a Cactus expert. Look at the image directly.
        1. Find 'Sequence Number' on the tag (digits only).
        2. Identify 'Scientific Name'.
        3. Identify 'Thai Name'.
        Return ONLY JSON: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
    except Exception as e:
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

def upload_to_bucket(file_obj, filename):
    try:
        client = storage.Client(credentials=creds, project=GCP_CREDS_DICT["project_id"])
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        file_obj.seek(0)
        blob.upload_from_file(file_obj, content_type='image/jpeg')
        return f"[https://storage.googleapis.com/](https://storage.googleapis.com/){BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Error: {e}"

# --- 4. UI ---
tab1, tab2 = st.tabs(["📸 บันทึกข้อมูล", "🛠️ จัดการข้อมูล (Dashboard)"])

# === TAB 1: บันทึก (แก้ไขหน้าขาว) ===
with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        # เปิดรูปและกลับหัวให้ถูกต้อง
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
        
        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True)
        
        # AI Run
        if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
            with c2:
                with st.spinner('🤖 AI กำลังทำงาน...'):
                    st.session_state['ai_result'] = analyze_image(image)
                    st.session_state['last_analyzed_file'] = uploaded_file.name
                
        if 'ai_result' in st.session_state:
            data = st.session_state['ai_result']
            with c2:
                with st.form("save_form"):
                    f_c1, f_c2 = st.columns(2)
                    pot_no = f_c1.text_input("เลขกระถาง", data.get('pot_number'))
                    species = f_c2.text_input("ชื่อวิทย์", data.get('species'))
                    thai = st.text_input("ชื่อไทย", data.get('thai_name'))
                    
                    submit = st.form_submit_button("💾 บันทึก", type="primary")
                    
                    if submit:
                        try:
                            with st.spinner('กำลังย่อรูปและบันทึก... (ห้ามปิดหน้าจอ)'):
                                # 1. Resize Image (แก้ปัญหาหน้าขาว/Memory เต็ม)
                                # ย่อให้ด้านกว้างไม่เกิน 1000px (ไฟล์จะเล็กลงมากแต่ยังชัด)
                                max_width = 1000
                                width, height = image.size
                                if width > max_width:
                                    ratio = max_width / width
                                    new_height = int(height * ratio)
                                    image = image.resize((max_width, new_height))
                                
                                # 2. Prepare Upload
                                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                img_byte = io.BytesIO()
                                image.save(img_byte, format='JPEG', quality=85) # Quality 85 ช่วยลดขนาดไฟล์
                                
                                # 3. Upload to Cloud
                                link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                                
                                # เช็ค Error จากการอัปโหลด
                                if "Error" in link:
                                    st.error(f"อัปโหลดไม่ผ่าน: {link}")
                                    st.stop() # หยุดการทำงานไม่ให้ไปต่อ

                                # 4. Save to Sheet
                                today = str(datetime.today().date())
                                append_to_sheet([today, pot_no, species, thai, link])
                                
                                st.success("✅ บันทึกสำเร็จ!")
                                
                                # 5. Reset State
                                del st.session_state['ai_result']
                                del st.session_state['last_analyzed_file']
                                st.session_state['uploader_key'] += 1
                                
                                time.sleep(1) 
                                st.rerun()
                                
                        except Exception as e:
                            st.error(f"เกิดข้อผิดพลาดร้ายแรง: {e}")

# === TAB 2: Dashboard ===
with tab2:
    st.header("จัดการข้อมูลแคคตัส")
    df = load_data_from_sheet()
    
    if df.empty:
        st.info("ยังไม่มีข้อมูล หรือโหลดข้อมูลไม่สำเร็จ")
    else:
        view_mode = st.radio("เลือกมุมมอง:", ["📝 List View (แก้ไข/ลบ)", "📊 Table View"], horizontal=True)
        st.divider()

        if "Table" in view_mode:
            st.dataframe(df, use_container_width=True)
        else:
            # List View
            for i in reversed(range(len(df))):
                row = df.iloc[i]
                with st.container(border=True):
                    cols = st.columns([1, 3])
                    with cols[0]:
                        img_link = row.get('Image Link', '')
                        if str(img_link).startswith('http'):
                            st.image(img_link, use_container_width=True)
                        else: st.write("No Image")
                    
                    with cols[1]:
                        with st.form(f"edit_form_{i}"):
                            c_e1, c_e2 = st.columns(2)
                            new_pot = c_e1.text_input("เลขกระถาง", row.get('Pot No', ''))
                            new_thai = c_e2.text_input("ชื่อไทย", row.get('Thai Name', ''))
                            new_species = st.text_input("ชื่อวิทย์", row.get('Species', ''))
                            
                            # Note (Check for key existence)
                            curr_note = row.get('Note', '') if 'Note' in row else ""
                            new_note = st.text_area("หมายเหตุ", str(curr_note))
                            
                            c_b1, c_b2 = st.columns([1, 4])
                            if c_b2.form_submit_button("บันทึกการแก้ไข"):
                                update_sheet_row(i, new_pot, new_species, new_thai, new_note)
                                st.toast("แก้ไขเรียบร้อย")
                                time.sleep(1)
                                st.rerun()
                                
                        if st.button("ลบต้นนี้", key=f"del_{i}"):
                            delete_sheet_row(i)
                            st.rerun()
