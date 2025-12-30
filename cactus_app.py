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
import gc
import re

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Manager (Link Rebuilder)", page_icon="🌵", layout="wide")

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

# --- 2. Caching ---
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

# --- 3. ฟังก์ชันสร้างลิงก์ใหม่ (Link Rebuilder) ---
def rebuild_clean_link(dirty_link):
    dirty_link = str(dirty_link).strip()
    
    # 1. ใช้ Regex หาเฉพาะชื่อไฟล์ (Cactus_xxxx.jpg)
    # หาคำว่า Cactus_ ตามด้วยตัวเลขและวันที่ และจบด้วย .jpg/.png
    match = re.search(r'(Cactus_.*?\.jpg)', dirty_link, re.IGNORECASE)
    
    if match:
        filename = match.group(1)
        # สร้างลิงก์ใหม่เองเลย เพื่อความชัวร์ 100%
        return f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        
    # ถ้าหาชื่อไฟล์ไม่เจอ ให้คืนค่าเดิมไป (เผื่อเป็นรูปอื่น)
    return dirty_link

# --- 4. AI (Gemini 2.0 Flash Exp + 1.5 Pro) ---
def find_working_model():
    if 'working_model_name' in st.session_state:
        return st.session_state['working_model_name']
    
    # เลี่ยง 1.5-flash ตามคำสั่ง / ใช้ 2.0-exp หรือ 1.5-pro แทน
    candidates = [
        'gemini-2.0-flash-exp',   # ตัวใหม่ล่าสุด (ฉลาดเท่า 2.5)
        'gemini-1.5-pro',         # ตัว Pro (ฉลาดแต่ช้ากว่านิดนึง)
        'gemini-1.5-pro-002'
    ]
    
    status_box = st.empty()
    
    for name in candidates:
        try:
            genai.GenerativeModel(name).generate_content("hi")
            st.session_state['working_model_name'] = name
            status_box.success(f"✅ ใช้โมเดล: {name}")
            time.sleep(1)
            status_box.empty()
            return name
        except:
            continue
            
    status_box.error("❌ ไม่พบโมเดล Pro/Exp ที่ใช้ได้ (Quota อาจเต็มทุกตัว)")
    return None

def analyze_image(image):
    model_name = find_working_model()
    if not model_name: return {"pot_number": "", "species": "Quota Exceeded", "thai_name": ""}
    
    try:
        model = genai.GenerativeModel(model_name)
        prompt = """
        You are a cactus expert. Identify:
        1. Sequence Number (digits only).
        2. Scientific Name.
        3. Thai Name.
        Response JSON ONLY: {"pot_number": "...", "species": "...", "thai_name": "..."}
        """
        response = model.generate_content([prompt, image])
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        return json.loads(text)
    except Exception as e:
        return {"pot_number": "", "species": f"Error: {e}", "thai_name": ""}

# --- 5. Services ---
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
        cleaned_data = [row[:6] + [""] * (6 - len(row)) for row in values[1:]]
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
        client = get_storage_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        file_obj.seek(0)
        blob.upload_from_file(file_obj, content_type='image/jpeg')
        try: blob.make_public()
        except: pass
        return f"[https://storage.googleapis.com/](https://storage.googleapis.com/){BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Error: {e}"

# --- 6. UI ---
tab1, tab2 = st.tabs(["📸 บันทึก", "🛠️ จัดการ"])

with tab1:
    st.header(f"บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        original_image = Image.open(uploaded_file)
        original_image = ImageOps.exif_transpose(original_image)
        max_width = 700
        w, h = original_image.size
        if w > max_width:
            ratio = max_width / w
            image = original_image.resize((max_width, int(h * ratio)))
        else:
            image = original_image.copy()
        
        original_image.close()
        gc.collect()

        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True)
        
        if 'last_analyzed_file' not in st.session_state or st.session_state['last_analyzed_file'] != uploaded_file.name:
            with c2:
                with st.spinner('🤖 AI (Exp/Pro) กำลังทำงาน...'):
                    st.session_state['ai_result'] = analyze_image(image)
                    st.session_state['last_analyzed_file'] = uploaded_file.name
                
        if 'ai_result' in st.session_state:
            data = st.session_state['ai_result']
            with c2:
                if "Error" in str(data.get('species', '')):
                    st.warning(f"{data.get('species')}")

                with st.form("save_form"):
                    f_c1, f_c2 = st.columns(2)
                    pot_no = f_c1.text_input("เลขกระถาง", data.get('pot_number'))
                    species = f_c2.text_input("ชื่อวิทย์", data.get('species'))
                    thai = st.text_input("ชื่อไทย", data.get('thai_name'))
                    
                    if st.form_submit_button("💾 บันทึก", type="primary"):
                        try:
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            img_byte = io.BytesIO()
                            image.save(img_byte, format='JPEG', quality=70)
                            link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                            
                            if "Error" in link:
                                st.error(f"Upload Failed: {link}")
                            else:
                                today = str(datetime.today().date())
                                append_to_sheet([today, pot_no, species, thai, link])
                                st.success("✅ บันทึกสำเร็จ!")
                                del st.session_state['ai_result']
                                del st.session_state['last_analyzed_file']
                                st.session_state['uploader_key'] += 1
                                image.close()
                                img_byte.close()
                                gc.collect()
                                time.sleep(1)
                                st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

with tab2:
    st.header("จัดการข้อมูลแคคตัส")
    if st.button("🔄 รีเฟรช"): st.rerun()
    
    df = load_data_from_sheet()
    
    if not df.empty:
        view = st.radio("มุมมอง", ["รายการ", "ตาราง"], horizontal=True)
        st.divider()

        if view == "ตาราง":
            st.dataframe(df, use_container_width=True)
        else:
            for i in reversed(range(len(df))):
                row = df.iloc[i]
                with st.container(border=True):
                    cols = st.columns([1, 3])
                    
                    with cols[0]:
                        raw_link = row.get('Image Link', '')
                        
                        # 🔥 สร้างลิงก์ใหม่จากชื่อไฟล์ (แก้ปัญหาลิงก์ขยะ 100%)
                        clean_link = rebuild_clean_link(raw_link)
                        
                        if "http" in clean_link:
                            st.markdown(
                                f'<img src="{clean_link}" style="width:100%; max-width:200px; border-radius:8px; border:1px solid #ccc;">', 
                                unsafe_allow_html=True
                            )
                            st.caption(f"File: {clean_link.split('/')[-1]}")
                            st.markdown(f"[🔗 เปิดรูป]({clean_link})")
                        else: 
                            st.warning("No Image")
                    
                    with cols[1]:
                        with st.form(f"edit_{i}"):
                            c1, c2 = st.columns(2)
                            p = c1.text_input("เลข", row.get('Pot No', ''))
                            t = c2.text_input("ชื่อไทย", row.get('Thai Name', ''))
                            s = st.text_input("ชื่อวิทย์", row.get('Species', ''))
                            n = st.text_area("Note", str(row.get('Note', '')))
                            
                            col_b1, col_b2 = st.columns([1, 4])
                            if col_b2.form_submit_button("บันทึกแก้ไข"):
                                update_sheet_row(i, p, s, t, n)
                                st.toast("Saved")
                                time.sleep(1)
                                st.rerun()
                        if st.button("ลบ", key=f"del_{i}"):
                            delete_sheet_row(i)
                            st.rerun()
    else:
        st.info("No Data")
