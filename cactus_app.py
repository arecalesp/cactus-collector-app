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
        2. Identify 'Scientific Name'.
        3. Identify 'Thai Name'.
        Return ONLY JSON: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
    except Exception as e:
        # ถ้าตัวที่เคยดี ดัน Error กลางทาง ให้ล้างค่าทิ้งเพื่อให้รอบหน้าสแกนใหม่
        if 'working_model_name' in st.session_state:
            del st.session_state['working_model_name']
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

# --- 4. Google Services (Optimized) ---
def append_to_sheet(data_row):
    service = get_sheet_service()
    data_row.append("") # Note Placeholder
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
        # Auto-Fill Columns
        cleaned_data = [row + [""] * (6 - len(row)) for row in values[1:]]
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
        client = get_storage_client() # ใช้ Cached Client
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        file_obj.seek(0)
        blob.upload_from_file(file_obj, content_type='image/jpeg')
        return f"[https://storage.googleapis.com/](https://storage.googleapis.com/){BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Error: {e}"

# --- 5. UI Application ---
tab1, tab2 = st.tabs(["📸 บันทึกข้อมูล", "🛠️ จัดการข้อมูล (Dashboard)"])

# === TAB 1: Scan & Save ===
with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        # 1. จัดการรูปภาพทันที (Memory Guard)
        original_image = Image.open(uploaded_file)
        original_image = ImageOps.exif_transpose(original_image)
        
        # Resize เหลือ 800px ทันทีเพื่อลดการกิน RAM
        max_width = 800
        w, h = original_image.size
        if w > max_width:
            ratio = max_width / w
            image = original_image.resize((max_width, int(h * ratio)))
        else:
            image = original_image.copy()
            
        # เคลียร์รูปต้นฉบับทิ้งจาก RAM
        original_image.close()
        gc.collect() 

        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True)
        
        # 2. AI Auto Run
        if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
            with c2:
                with st.spinner('🤖 AI กำลังทำงาน...'):
                    st.session_state['ai_result'] = analyze_image(image)
                    st.session_state['last_analyzed_file'] = uploaded_file.name
                
        # 3. Form บันทึก
        if 'ai_result' in st.session_state:
            data = st.session_state['ai_result']
            with c2:
                # ถ้า AI Error ให้แจ้งเตือนเบาๆ
                if "Error" in str(data.get('species', '')):
                    st.warning(f"AI ขัดข้อง ({data.get('species')}) -> กรอกเองได้เลยครับ")

                with st.form("save_form"):
                    f_c1, f_c2 = st.columns(2)
                    pot_no = f_c1.text_input("เลขกระถาง", data.get('pot_number'))
                    species = f_c2.text_input("ชื่อวิทย์", data.get('species'))
                    thai = st.text_input("ชื่อไทย", data.get('thai_name'))
                    
                    if st.form_submit_button("💾 บันทึก", type="primary"):
                        try:
                            # Feedback UI
                            progress = st.progress(0, text="เริ่มกระบวนการ...")
                            
                            # เตรียมรูป
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            img_byte = io.BytesIO()
                            image.save(img_byte, format='JPEG', quality=75) # ลด Quality เล็กน้อย
                            
                            # Upload
                            progress.progress(40, text="กำลังอัปโหลด...")
                            link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                            
                            if "Error" in link:
                                st.error(link)
                            else:
                                # Save Sheet
                                progress.progress(80, text="กำลังบันทึกข้อมูล...")
                                today = str(datetime.today().date())
                                append_to_sheet([today, pot_no, species, thai, link])
                                
                                progress.progress(100, text="เสร็จสิ้น!")
                                st.success("✅ บันทึกสำเร็จ!")
                                
                                # Reset & Cleanup
                                del st.session_state['ai_result']
                                del st.session_state['last_analyzed_file']
                                st.session_state['uploader_key'] += 1
                                
                                # เคลียร์ขยะใน RAM ทิ้งท้าย
                                image.close()
                                img_byte.close()
                                gc.collect()
                                
                                time.sleep(1) 
                                st.rerun()
                        except Exception as e:
                            st.error(f"Save Error: {e}")

# === TAB 2: Dashboard ===
with tab2:
    st.header("จัดการข้อมูลแคคตัส")
    if st.button("🔄 รีเฟรชข้อมูล"): st.rerun()
        
    df = load_data_from_sheet()
    
    if not df.empty:
        view_mode = st.radio("มุมมอง:", ["📝 รายการ", "📊 ตารางรวม"], horizontal=True)
        st.divider()

        if "ตาราง" in view_mode:
            st.dataframe(df, use_container_width=True)
        else:
            # List View
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
                            curr = row.get('Note', '') if 'Note' in row else ""
                            n = st.text_area("หมายเหตุ", str(curr))
                            
                            col_b1, col_b2 = st.columns([1, 4])
                            if col_b2.form_submit_button("บันทึกการแก้ไข"):
                                update_sheet_row(i, p, s, t, n)
                                st.toast("แก้ไขเรียบร้อย")
                                time.sleep(1)
                                st.rerun()
                        if st.button("🗑️ ลบรายการนี้", key=f"del_{i}"):
                            delete_sheet_row(i)
                            st.rerun()
    else:
        st.info("ยังไม่มีข้อมูลในระบบ")
