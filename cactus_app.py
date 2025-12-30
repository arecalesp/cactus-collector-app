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
st.set_page_config(page_title="Cactus Manager Pro", page_icon="🌵", layout="wide")

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

# --- 2. ฟังก์ชัน Google Sheet (CRUD System) ---

def get_sheet_service():
    return build('sheets', 'v4', credentials=creds)

# 2.1 เพิ่มข้อมูลใหม่ (Create)
def append_to_sheet(data_row):
    service = get_sheet_service()
    sheet = service.spreadsheets()
    # เพิ่ม Note ว่างๆ ไปด้วยในคอลัมน์สุดท้าย
    data_row.append("") 
    body = {'values': [data_row]}
    sheet.values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:F", # ขยายถึง F (Note)
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

# 2.2 อ่านข้อมูล (Read)
# แก้ไขฟังก์ชันนี้ (เพื่อรองรับกรณีข้อมูลใน Sheet ยาวไม่เท่ากัน)
def load_data_from_sheet():
    try:
        service = get_sheet_service()
        sheet = service.spreadsheets()
        
        # อ่านข้อมูล A ถึง F
        result = sheet.values().get(spreadsheetId=SHEET_ID, range="Sheet1!A:F").execute()
        values = result.get('values', [])
        
        if not values: 
            return pd.DataFrame()
        
        # กำหนด Header มาตรฐานที่เราต้องการ (บังคับใช้ 6 ตัวนี้เสมอ)
        headers = ['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note']
        
        # ข้ามแถวแรก (Header ใน Sheet) แล้วเอาเฉพาะข้อมูล
        data_rows = values[1:]
        
        # ⚠️ สำคัญ: วนลูปเช็คทุกแถว ถ้าแถวไหนยาวไม่ครบ 6 ช่อง ให้เติม "" จนครบ
        # เพื่อกัน Error "columns passed..."
        cleaned_data = []
        for row in data_rows:
            # เติมช่องว่างจนกว่าจะครบตามจำนวน Header
            while len(row) < len(headers):
                row.append("")
            # ตัดส่วนเกินทิ้ง (เผื่อเกิน)
            cleaned_data.append(row[:len(headers)])
            
        # สร้าง DataFrame จากข้อมูลที่ "คลีน" แล้ว
        df = pd.DataFrame(cleaned_data, columns=headers)
        
        return df
        
    except Exception as e:
        st.error(f"โหลดข้อมูลไม่สำเร็จ: {e}")
        # กรณี Error ให้ส่งตารางเปล่ากลับไป แอพจะได้ไม่พัง
        return pd.DataFrame(columns=['Date', 'Pot No', 'Species', 'Thai Name', 'Image Link', 'Note'])

# 2.3 แก้ไขข้อมูล (Update)
def update_sheet_row(row_index, pot_no, species, thai, note):
    # row_index ใน sheet เริ่มที่ 1 แต่ข้อมูลเริ่มแถว 2 (เพราะแถว 1 คือ Header)
    # ดังนั้น row_index จาก DataFrame (เริ่ม 0) ต้อง +2 ถึงจะได้เลขแถวใน Sheet จริง
    sheet_row = row_index + 2
    
    service = get_sheet_service()
    sheet = service.spreadsheets()
    
    # อัปเดต 4 คอลัมน์: B(Pot), C(Species), D(Thai), F(Note)
    # เราจะยิง update ทีละเซลล์ หรือ update เป็น range ก็ได้ (เอาแบบ range ง่ายกว่า)
    
    # Update Pot, Species, Thai (Col B, C, D)
    range_main = f"Sheet1!B{sheet_row}:D{sheet_row}"
    body_main = {'values': [[pot_no, species, thai]]}
    sheet.values().update(
        spreadsheetId=SHEET_ID, range=range_main,
        valueInputOption="USER_ENTERED", body=body_main
    ).execute()
    
    # Update Note (Col F)
    range_note = f"Sheet1!F{sheet_row}"
    body_note = {'values': [[note]]}
    sheet.values().update(
        spreadsheetId=SHEET_ID, range=range_note,
        valueInputOption="USER_ENTERED", body=body_note
    ).execute()

# 2.4 ลบข้อมูล (Delete)
def delete_sheet_row(row_index):
    sheet_row = row_index + 2 # แปลงเป็น Index ของ Sheet
    # การลบแถวต้องใช้ batchUpdate และระบุ SheetId (ที่เป็นตัวเลข ไม่ใช่ String)
    # ปกติ Sheet1 มักจะมี sheetId = 0 แต่เพื่อความชัวร์ควรดึงมาก่อน (ในที่นี้สมมติเป็น 0)
    
    service = get_sheet_service()
    
    # คำสั่งลบแถว (StartIndex เป็น 0-based index ของ Sheet API... งงไหมครับ Google มันซับซ้อนตรงนี้)
    # สรุป: ถ้าจะลบแถวที่ 5 (ใน Excel) -> startIndex = 4
    api_row_index = sheet_row - 1 
    
    requests = [{
        "deleteDimension": {
            "range": {
                "sheetId": 0, # ⚠️ ถ้าคุณเปลี่ยนชื่อ Sheet1 หรือสร้างใหม่ ต้องแก้ ID นี้ (ปกติแผ่นแรกคือ 0)
                "dimension": "ROWS",
                "startIndex": api_row_index,
                "endIndex": api_row_index + 1
            }
        }
    }]
    
    service.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": requests}
    ).execute()

# --- 3. ฟังก์ชันอื่นๆ (AI, Cloud Storage) ---
def find_working_model():
    if 'working_model_name' in st.session_state: return st.session_state['working_model_name']
    try:
        all_models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        sorted_models = sorted(all_models, key=lambda x: ('flash' not in x.name, 'exp' in x.name))
        for m in sorted_models:
            friendly = m.name.replace('models/', '')
            if '2.0' in friendly or 'exp' in friendly: continue
            try:
                genai.GenerativeModel(m.name).generate_content("hi")
                st.session_state['working_model_name'] = friendly
                return friendly
            except: continue
        return 'gemini-pro'
    except: return 'gemini-1.5-flash'

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
        return f"Upload Error: {e}"

# --- 4. ส่วนแสดงผล UI ---
tab1, tab2 = st.tabs(["📸 บันทึกข้อมูล", "🛠️ จัดการข้อมูล (Dashboard)"])

# === TAB 1: Scan ===
with tab1:
    st.header("บันทึกต้นไม้ใหม่")
    uploaded_file = st.file_uploader("เลือกรูปภาพ", type=["jpg", "png"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
        c1, c2 = st.columns([1, 2])
        with c1: st.image(image, use_container_width=True)
        
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
                    if st.form_submit_button("💾 บันทึก"):
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        img_byte = io.BytesIO()
                        image.save(img_byte, format='JPEG') 
                        link = upload_to_bucket(img_byte, f"Cactus_{pot_no}_{ts}.jpg")
                        today = str(datetime.today().date())
                        # ส่ง 5 ค่า (Note จะถูกเติมเป็นค่าว่างในฟังก์ชัน)
                        append_to_sheet([today, pot_no, species, thai, link])
                        st.success("บันทึกแล้ว!")
                        del st.session_state['ai_result']
                        del st.session_state['last_analyzed_file']
                        st.session_state['uploader_key'] += 1
                        time.sleep(1) 
                        st.rerun()

# === TAB 2: Dashboard (List View & Table View) ===
with tab2:
    st.header("จัดการข้อมูลแคคตัส")
    
    # โหลดข้อมูล
    df = load_data_from_sheet()
    
    if df.empty:
        st.info("ยังไม่มีข้อมูลครับ")
    else:
        # ปุ่มเลือก View
        view_mode = st.radio("เลือกมุมมอง:", ["📝 List View (แก้ไข/ลบ)", "📊 Table View (ดูภาพรวม)"], horizontal=True)
        st.divider()

        # --- VIEW 1: TABLE VIEW (ดูง่ายๆ) ---
        if "Table" in view_mode:
            st.dataframe(df, use_container_width=True)
            st.caption("*หากต้องการแก้ไขข้อมูล ให้เปลี่ยนไปที่ List View")

        # --- VIEW 2: LIST VIEW (เครื่องมือจัดการ) ---
        else:
            # วนลูปข้อมูลแสดงทีละการ์ด (เรียงจากล่าสุดไปเก่าสุด จะได้หาง่ายๆ)
            # ใช้ reversed index เพื่อให้คนล่าสุดอยู่บน
            for i in reversed(range(len(df))):
                row = df.iloc[i]
                
                # กรอบสำหรับแต่ละต้น
                with st.container(border=True):
                    cols = st.columns([1, 3])
                    
                    # รูปภาพ (ซ้าย)
                    with cols[0]:
                        img_link = row.get('Image Link', '')
                        if str(img_link).startswith('http'):
                            st.image(img_link, use_container_width=True)
                        else:
                            st.write("ไม่มีรูป")
                    
                    # ข้อมูลและการแก้ไข (ขวา)
                    with cols[1]:
                        st.write(f"**Date:** {row.get('Date', '-')}")
                        
                        # สร้าง Form ย่อยสำหรับแต่ละแถว เพื่อให้กด Save แยกกันได้
                        with st.form(f"edit_form_{i}"):
                            c_edit1, c_edit2 = st.columns(2)
                            new_pot = c_edit1.text_input("เลขกระถาง", row.get('Pot No', ''))
                            new_thai = c_edit2.text_input("ชื่อไทย", row.get('Thai Name', ''))
                            new_species = st.text_input("ชื่อวิทย์", row.get('Species', ''))
                            
                            # ช่องหมายเหตุ (Note)
                            current_note = row.get('Note', '') if 'Note' in row else ""
                            new_note = st.text_area("📝 หมายเหตุ", current_note, placeholder="เช่น ผสมเกสรแล้ว, หน่อเริ่มมา, ฯลฯ")
                            
                            c_btn1, c_btn2 = st.columns([1, 4])
                            
                            # ปุ่ม Save
                            if c_btn2.form_submit_button("💾 บันทึกการแก้ไข", type="primary"):
                                update_sheet_row(i, new_pot, new_species, new_thai, new_note)
                                st.toast(f"อัปเดตต้นที่ {new_pot} เรียบร้อย!")
                                time.sleep(1)
                                st.rerun()
                        
                        # ปุ่มลบ (อยู่นอก Form เพื่อความปลอดภัย กันมือกดพลาด)
                        if st.button("🗑️ ลบต้นนี้ทิ้ง", key=f"del_{i}"):
                            delete_sheet_row(i)
                            st.error("ลบข้อมูลเรียบร้อย!")
                            time.sleep(1)
                            st.rerun()
