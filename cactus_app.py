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
import gc # ตัวช่วยเคลียร์ RAM ป้องกันแอพเด้ง

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Manager (Ultimate)", page_icon="🌵", layout="wide")

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

# --- 2. Resource Caching (ลดภาระการเชื่อมต่อ ป้องกันหน้าขาว) ---
@st.cache_resource
def get_gcp_creds():
    return service_account.Credentials.from_service_account_info(GCP_CREDS_DICT)

@st.cache_resource
def get_storage_client():
    creds = get_gcp_creds()
    return storage.Client(credentials=creds, project=GCP_CREDS_DICT["project_id"])

@st.cache_resource
def get_sheet_service():
    creds = get_gcp_creds()
    return build('sheets', 'v4', credentials=creds)

genai.configure(api_key=GEMINI_API_KEY)

# --- 3. ฟังก์ชัน AI แบบ Brute Force (เอากลับมาแล้ว!) ---
def find_working_model():
    # ถ้าเคยหาเจอแล้ว ใช้ตัวเดิม
    if 'working_model_name' in st.session_state:
        return st.session_state['working_model_name']

    status_box = st.empty()
    status_box.info("📡 กำลังสแกนหาโมเดลทั้งหมดในบัญชี (Brute Force)...")

    try:
        # 1. ดึงรายชื่อโมเดลทั้งหมดที่มีสิทธิ์ใช้ (Full Scan)
        all_models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 2. จัดลำดับ: ลอง Flash ก่อน -> ตามด้วย Pro -> อื่นๆ (เอา exp ไว้ท้ายๆ)
        sorted_models = sorted(all_models, key=lambda x: ('flash' not in x.name, 'exp' in x.name))
        
        # 3. วนลูปเทสทีละตัว (Test Connection)
        for m in sorted_models:
            friendly_name = m.name.replace('models/', '')
            try:
                # ยิง Test จริงๆ ถ้าผ่านคือใช้ได้แน่นอน
                model = genai.GenerativeModel(m.name)
                model.generate_content("hi")
                
                # เจอแล้ว! จำค่าไว้
                st.session_state['working_model_name'] = friendly_name
                status_box.success(f"✅ จับสัญญาณได้ที่: {friendly_name}")
                time.sleep(1)
                status_box.empty()
                return friendly_name
            except:
                continue # ตัวนี้พัง ข้ามไปตัวถัดไป
                
    except Exception as e:
        st.error(f"System Error: {e}")

    # Fallback ถ้าไม่เจออะไรเลย
    status_box.error("❌ ไม่พบโมเดลที่ใช้ได้ แต่คุณยังกรอกข้อมูลเองได้")
    return None

def analyze_image(image):
    # เรียก Brute Force Scanner
    model_name = find_working_model()
    
    if not model_name:
        return {"pot_number": "", "species": "", "thai_name": ""}

    try:
        model = genai.GenerativeModel(model_name)
        prompt = """
        You are a Cactus expert. Look at the image directly.
        1. Find 'Sequence Number' on the tag (digits only).
