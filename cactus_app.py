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
import re # เพิ่ม Library สำหรับกรองคำ

# --- 1. ตั้งค่าพื้นฐาน ---
st.set_page_config(page_title="Cactus Manager (Cleaner)", page_icon="🌵", layout="wide")

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

# --- 3. ฟังก์ชันล้างลิงก์เสีย (พระเอกของงานนี้) ---
def clean_image_link(raw_link):
    raw_link = str(raw_link).strip()
    
    # ถ้าลิงก์ดูปกติอยู่แล้ว ก็คืนค่าเดิม
    if raw_link.startswith("http") and "[" not in raw_link:
        return raw_link
        
    # ถ้าลิงก์เละ (มี [] หรือขยะปนมา) ให้ใช้ Regex สกัดหา Pattern ของ URL
    # หาข้อความที่ขึ้นต้นด้วย https:// และจบด้วย .jpg หรือ .jpeg หรือ .png
    match = re.search(r'(https://storage\.googleapis\.com/.*?\.(?:jpg|jpeg|png))', raw_link, re.IGNORECASE)
    
    if match:
        return match.group(1) # คืนค่าเฉพาะลิงก์ที่สะอาดแล้ว
    
    # ถ้ากู้ไม่ได้จริงๆ ให้ลองตัดส่วน Bucket Name มาต่อใหม่ (ท่าไม้ตายแก้ขยะ)
    if BUCKET_NAME in raw_link:
        try:
            # ตัดเอาเฉพาะส่วนหลังชื่อ Bucket
            part = raw_link.split(BUCKET_NAME)[-1]
            # ลบเครื่องหมาย / หรือ \ ที่เกินมาด้านหน้า
            clean_path = part.lstrip("/").lstrip("\\")
            return f"https://storage.googleapis.com/{BUCKET_NAME}/{clean_path}"
        except:
            pass
            
    return raw_link

# --- 4. AI (Hardcoded Gemini 2.5 Only) ---
def analyze_image(image):
    model_name = 'gemini-2.5-flash' # บังคับตัวนี้ตัวเดียว
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
        return {"pot_number": "", "species": f"Error ({model_name}): {e}", "thai_name": ""}

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
                with st.spinner('🤖 AI (Gemini 2.5) กำลังทำงาน...'):
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
                        # 🔥 เรียกใช้ฟังก์ชันล้างลิงก์ที่นี่ 🔥
                        img_link = clean_image_link(raw_link)
                        
                        if "http" in img_link:
                            st.markdown(
                                f'<img src="{img_link}" style="width:100%; max-width:200px; border-radius:8px; border:1px solid #ccc;">', 
                                unsafe_allow_html=True
                            )
                            # แสดงลิงก์ให้ดูด้วย (เผื่อเช็คว่าสะอาดจริงไหม)
                            st.caption(f"Clean Link: {img_link[-20:]}...") 
                            st.markdown(f"[🔗 เปิดรูปเต็ม]({img_link})")
                        else: 
                            st.warning("No Image")
                            st.caption(f"Raw Data: {str(raw_link)[:30]}...") # Debug ดูขยะ
                    
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
