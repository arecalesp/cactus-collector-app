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
st.set_page_config(page_title="Cactus Manager (Final)", page_icon="🌵", layout="wide")

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

# --- 2. ฟังก์ชัน AI (ล็อคชื่อโมเดล gemini-1.5-flash) ---
def analyze_image(image):
    # ⚠️ บังคับใช้ชื่อนี้เท่านั้น (ห้ามเปลี่ยน)
    # เพราะเป็นตัวเดียวที่บัญชีใหม่ใช้ได้ฟรี 1,500 รูป/วัน
    model_name = 'gemini-1.5-flash'
    
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
        # ถ้ายัง Error ให้แสดงชื่อโมเดลที่พังออกมาดู
        return {"pot_number": "", "species": f"Error ({model_name}): {e}", "thai_name": ""}

# --- 3. ฟังก์ชัน Google Sheet (CRUD) ---
def get_sheet_service():
    return build('sheets', 'v4', credentials=creds)

def append_to_sheet(data_row):
    service = get_sheet_service()
    data_row.append("") # Note placeholder
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID, range="Sheet1!A:F",
        valueInputOption="USER_ENTERED", body={'values': [data_row]}
    ).execute()

def load_data_from_sheet():
    try:
        service = get_sheet_service()
        result = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Sheet1!A:F").execute()
        values = result.get('values', [])
        if not values: return pd.DataFrame()
        
        headers = ['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note']
        cleaned_data = []
        for row in values[1:]:
            while len(row) < len(headers): row.append("")
            cleaned_data.append(row[:len(headers)])
        return pd.DataFrame(cleaned_data, columns=headers)
    except:
        return pd.DataFrame(columns=['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note'])

def update_sheet_row(row_index, pot_no, species, thai, note):
    r = row_index + 2
    service = get_sheet_service()
    service.spreadsheets().values().update(spreadsheetId=SHEET_ID, range=f"Sheet1!B{r}:D{r}", valueInputOption="USER_ENTERED", body={'values': [[pot_no, species, thai]]}).execute()
    service.spreadsheets().values().update(spreadsheetId=SHEET_ID, range=f"Sheet1!F{r}", valueInputOption="USER_ENTERED", body={'values': [[note]]}).execute()

def delete_sheet_row(row_index):
    r = row_index + 2
    service = get_sheet_service()
    requests = [{"deleteDimension": {"range": {"sheetId": 0, "dimension": "ROWS", "startIndex": r-1, "endIndex": r}}}]
    service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()

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

with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True)
        
        # AI Auto Run
        if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
            with c2:
                with st.spinner('🤖 AI (Gemini 1.5 Flash) กำลังทำงาน...'):
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
                    
                    if st.form_submit_button("💾 บันทึก", type="primary"):
                        with st.spinner('กำลังย่อรูปและบันทึก...'):
                            # Resize
                            max_width = 1000
                            width, height = image.size
                            if width > max_width:
                                ratio = max_width / width
                                image = image.resize((max_width, int(height * ratio)))
                            
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            img_byte = io.BytesIO()
                            image.save(img_byte, format='JPEG', quality=85)
                            
                            link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                            today = str(datetime.today().date())
                            append_to_sheet([today, pot_no, species, thai, link])
                            
                            st.success("✅ บันทึกสำเร็จ!")
                            del st.session_state['ai_result']
                            del st.session_state['last_analyzed_file']
                            st.session_state['uploader_key'] += 1
                            time.sleep(1) 
                            st.rerun()

with tab2:
    st.header("จัดการข้อมูลแคคตัส")
    df = load_data_from_sheet()
    
    if not df.empty:
        view_mode = st.radio("มุมมอง:", ["📝 รายการ", "📊 ตารางรวม"], horizontal=True)
        st.divider()

        if "ตาราง" in view_mode:
            st.dataframe(df, use_container_width=True)
        else:
            for i in reversed(range(len(df))):
                row = df.iloc[i]
                with st.container(border=True):
                    cols = st.columns([1, 3])
                    with cols[0]:
                        if str(row.get('Image Link','')).startswith('http'):
                            st.image(row.get('Image Link'), use_container_width=True)
                        else: st.write("No Image")
                    with cols[1]:
                        with st.form(f"edit_form_{i}"):
                            c1, c2 = st.columns(2)
                            p = c1.text_input("เลขกระถาง", row.get('Pot No', ''))
                            t = c2.text_input("ชื่อไทย", row.get('Thai Name', ''))
                            s = st.text_input("ชื่อวิทย์", row.get('Species', ''))
                            n = st.text_area("หมายเหตุ", str(row.get('Note', ''))) if 'Note' in row else st.text_area("หมายเหตุ")
                            
                            c_btn1, c_btn2 = st.columns([1, 4])
                            if c_btn2.form_submit_button("บันทึกการแก้ไข"):
                                update_sheet_row(i, p, s, t, n)
                                st.toast("แก้ไขเรียบร้อย")
                                time.sleep(1)
                                st.rerun()
                        if st.button("ลบต้นนี้", key=f"del_{i}"):
                            delete_sheet_row(i)
                            st.rerun()
    else:
        st.info("ยังไม่มีข้อมูลในระบบ")
