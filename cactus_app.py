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
st.set_page_config(page_title="Cactus Manager (Fast Fix)", page_icon="🌵", layout="wide")

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

# --- 2. ฟังก์ชัน AI (Fast Check) ---
def find_working_model():
    if 'working_model_name' in st.session_state:
        return st.session_state['working_model_name']

    # 1. ลองยิงตัวยอดฮิตก่อน (เร็ว ไม่ต้องรอโหลด list)
    fast_candidates = [
        'gemini-1.5-flash', 
        'gemini-1.5-flash-001',
        'gemini-1.5-flash-002',
        'gemini-pro'
    ]
    
    status = st.empty()
    status.info("⚡ กำลังเชื่อมต่อ AI (Fast Connect)...")
    
    for name in fast_candidates:
        try:
            model = genai.GenerativeModel(name)
            model.generate_content("hi")
            st.session_state['working_model_name'] = name
            status.empty()
            return name
        except:
            continue
            
    # 2. ถ้าตัวฮิตใช้ไม่ได้ ค่อยเสียเวลาโหลด List ทั้งหมด (Brute Force เต็มรูปแบบ)
    status.warning("⚠️ กำลังสแกนหาโมเดลสำรอง (อาจนานนิดนึง)...")
    try:
        all_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m_name in all_models:
            name = m_name.replace('models/', '')
            try:
                genai.GenerativeModel(name).generate_content("hi")
                st.session_state['working_model_name'] = name
                status.empty()
                return name
            except:
                continue
    except:
        pass
        
    status.error("❌ ไม่พบโมเดลที่ใช้ได้ แต่คุณยังกรอกข้อมูลเองได้ครับ")
    return None

def analyze_image(image):
    model_name = find_working_model()
    
    # ถ้าหาโมเดลไม่เจอ ให้คืนค่าว่างเพื่อให้ฟอร์มเด้งขึ้นมาให้คนกรอก
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
        return {"pot_number": "", "species": f"AI Error: {e}", "thai_name": ""}

# --- 3. Google Services ---
def get_sheet_service(): return build('sheets', 'v4', credentials=creds)

def append_to_sheet(data_row):
    service = get_sheet_service()
    data_row.append("") 
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
tab1, tab2 = st.tabs(["📸 บันทึกข้อมูล", "🛠️ จัดการข้อมูล"])

# === TAB 1: Scan (Fast & Responsive) ===
with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        # ย่อรูปทันที
        original_image = Image.open(uploaded_file)
        original_image = ImageOps.exif_transpose(original_image)
        max_width = 800
        w, h = original_image.size
        if w > max_width:
            ratio = max_width / w
            image = original_image.resize((max_width, int(h * ratio)))
        else:
            image = original_image

        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True, caption="รูปพร้อมใช้งาน")
        
        # ส่วนแสดงผล AI
        with c2:
            # 1. เช็คสถานะการทำงาน
            if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
                status_box = st.info("🤖 AI กำลังเริ่มงาน... (ถ้าเงียบนานเกิน 10 วิ แสดงว่าเน็ตช้า)")
                with st.spinner('กำลังวิเคราะห์...'):
                    st.session_state['ai_result'] = analyze_image(image)
                    st.session_state['last_analyzed_file'] = uploaded_file.name
                status_box.empty() # ลบข้อความเมื่อเสร็จ
            
            # 2. แสดงฟอร์ม (Form จะแสดงเสมอ แม้ AI พัง ก็จะแสดงฟอร์มเปล่า)
            if 'ai_result' in st.session_state:
                data = st.session_state['ai_result']
                
                # ถ้า AI มี Error ให้แจ้งเตือน แต่ยังให้กรอกได้
                if "Error" in str(data.get('species', '')):
                    st.warning(f"AI อ่านไม่ได้ ({data.get('species')}) -> กรอกเองได้เลยครับ")

                with st.form("save_form"):
                    f_c1, f_c2 = st.columns(2)
                    pot_no = f_c1.text_input("เลขกระถาง", data.get('pot_number'))
                    species = f_c2.text_input("ชื่อวิทย์", data.get('species'))
                    thai = st.text_input("ชื่อไทย", data.get('thai_name'))
                    
                    if st.form_submit_button("💾 บันทึก", type="primary"):
                        try:
                            status_save = st.info("⏳ กำลังอัปโหลด...")
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            img_byte = io.BytesIO()
                            image.save(img_byte, format='JPEG', quality=80)
                            
                            link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                            
                            if "Error" in link:
                                st.error(link)
                            else:
                                today = str(datetime.today().date())
                                append_to_sheet([today, pot_no, species, thai, link])
                                status_save.success("✅ เรียบร้อย!")
                                del st.session_state['ai_result']
                                del st.session_state['last_analyzed_file']
                                st.session_state['uploader_key'] += 1
                                time.sleep(1) 
                                st.rerun()
                        except Exception as e:
                            st.error(f"Save Failed: {e}")

# === TAB 2: Dashboard ===
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
                            curr = row.get('Note', '') if 'Note' in row else ""
                            n = st.text_area("หมายเหตุ", str(curr))
                            
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
        st.info("ยังไม่มีข้อมูล")
