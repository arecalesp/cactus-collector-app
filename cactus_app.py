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
st.set_page_config(page_title="Cactus Manager (Final Combined)", page_icon="🌵", layout="wide")

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

# --- 2. ฟังก์ชัน AI แบบ Brute Force (ตัวจริง) ---
def find_working_model():
    # ถ้าเคยหาเจอแล้ว และมันยังทำงานได้ ก็ใช้ตัวเดิม
    if 'working_model_name' in st.session_state:
        return st.session_state['working_model_name']

    status_container = st.empty()
    status_container.warning("⚠️ กำลังสแกนหาโมเดลที่ใช้งานได้ (Brute Force Mode)...")
    
    try:
        # 1. ดึงรายชื่อทั้งหมด
        all_models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 2. จัดลำดับ: ลอง Flash ก่อน -> ตามด้วย Pro -> และอื่นๆ
        # (เทคนิค: เอา exp ไว้ท้ายๆ แต่ไม่ตัดทิ้ง เผื่อจำเป็นต้องใช้)
        sorted_models = sorted(all_models, key=lambda x: ('flash' not in x.name, 'exp' in x.name))
        
        # 3. ไล่ยิง Test ทีละตัว
        for m in sorted_models:
            friendly_name = m.name.replace('models/', '')
            try:
                # ยิง Test จริงๆ ถ้าผ่านคือใช้ได้แน่นอน
                model = genai.GenerativeModel(m.name)
                response = model.generate_content("test")
                
                if response.text:
                    st.session_state['working_model_name'] = friendly_name
                    status_container.success(f"✅ เชื่อมต่อสำเร็จ: {friendly_name}")
                    time.sleep(1)
                    status_container.empty()
                    return friendly_name
            except:
                continue # ตัวนี้พัง ไปตัวถัดไป
                
    except Exception as e:
        st.error(f"System Error: {e}")

    # ถ้าไม่เจอเลย ให้ลองเสี่ยงดวงกับตัว Default
    return 'gemini-1.5-flash'

def analyze_image(image):
    # เรียกใช้ฟังก์ชันค้นหา
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
        # ถ้าตัวที่เคยเลือกเกิดพัง ให้ล้างค่าทิ้งเพื่อให้รอบหน้าสแกนใหม่
        if 'working_model_name' in st.session_state:
            del st.session_state['working_model_name']
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

# --- 3. ฟังก์ชัน Google Sheet (CRUD System) ---
def get_sheet_service():
    return build('sheets', 'v4', credentials=creds)

def append_to_sheet(data_row):
    service = get_sheet_service()
    data_row.append("") # เติม Note ว่างๆ
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
            # Auto-Fill: เติมช่องว่างให้ครบตามจำนวน Header
            while len(row) < len(headers): row.append("")
            cleaned_data.append(row[:len(headers)])
        return pd.DataFrame(cleaned_data, columns=headers)
    except:
        return pd.DataFrame(columns=['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note'])

def update_sheet_row(row_index, pot_no, species, thai, note):
    r = row_index + 2
    service = get_sheet_service()
    # Update ข้อมูลหลัก
    service.spreadsheets().values().update(spreadsheetId=SHEET_ID, range=f"Sheet1!B{r}:D{r}", valueInputOption="USER_ENTERED", body={'values': [[pot_no, species, thai]]}).execute()
    # Update Note
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

# --- 4. ส่วนแสดงผล UI (Tabs) ---
tab1, tab2 = st.tabs(["📸 บันทึกข้อมูล", "🛠️ จัดการข้อมูล (Dashboard)"])

# === TAB 1: Scan & Save (Auto Resize + Brute Force AI) ===
with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        # 1. จัดการรูปภาพทันที (Auto Rotate + Resize)
        original_image = Image.open(uploaded_file)
        original_image = ImageOps.exif_transpose(original_image)
