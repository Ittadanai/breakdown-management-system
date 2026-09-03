import datetime
from datetime import timezone, timedelta
import math
import threading
import time
import pandas as pd
import requests
import streamlit as st
from sqlalchemy import create_engine, text

# --- ตั้งค่า Timezone เป็นประเทศไทย (UTC+7) ---
THAILAND_TZ = timezone(timedelta(hours=7))

# --- ตั้งค่า LINE Messaging API ---
LINE_ACCESS_TOKEN = "/gRiogRyB9Yc549xgz6/M6Mxc3WLBfNW65zC7Tzev3I06I/Oa5VleMp8W9yWnGjjcrcRQ1q5sKXCqJq6WFylJV/KUB1o8ZxjzeRrzwRkO9kY6Y2l2OSQuFcW0ZKPj3SkyYTmaFlRoDygYvU3GVYgNwdB04t89/1O/w1cDnyilFU="
LINE_GROUP_ID = "C88ee91818e75720098992bf0cae49e82"

# --- กำหนดตัวเลือกสำหรับ Process Dropdown ---
PROCESS_OPTIONS = [
    "-- กรุณาเลือก Process --",
    "PT/ED",
    "PVC",
    "Dry sand",
    "Primer",
    "Moist sand",
    "Top coat",
    "T-UP",
    "Plastic",
]

# --- กำหนดตัวเลือกสำหรับ Effect Dropdown ---
EFFECT_OPTIONS = [
    "-- กรุณาเลือก Effect --",
    "ไม่หยุดการผลิต (Production will not Stop)",
    "หยุดการผลิต (Production Stop)",
    "รถเสียหาย (Body NCR)",
    "อื่นๆระบุ (Other)",
]


# --- ฟังก์ชันส่งการแจ้งเตือนเข้ากลุ่ม LINE ---
def send_line_message(message_text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
    }
    payload = {
        "to": LINE_GROUP_ID,
        "messages": [{"type": "text", "text": message_text}],
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending LINE message: {e}")
        return False


from sqlalchemy.engine import URL

# --- 1. ตั้งค่าฐานข้อมูล Supabase (PostgreSQL) ผ่าน SQLAlchemy ---
@st.cache_resource
def get_db_engine():
    connection_url = URL.create(
        drivername="postgresql+psycopg2",
        username=st.secrets["postgres"]["user"],
        password=st.secrets["postgres"]["password"],
        host=st.secrets["postgres"]["host"],
        port=int(st.secrets["postgres"]["port"]),
        database=st.secrets["postgres"]["dbname"],
    )
    return create_engine(connection_url, pool_pre_ping=True)

engine = get_db_engine()

# สร้างตาราง breakdown_logs บน Supabase อัตโนมัติหากยังไม่มี
def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS breakdown_logs (
            id SERIAL PRIMARY KEY,
            machine_name TEXT,
            issue_description TEXT,
            reported_by TEXT,
            start_time TEXT,
            end_time TEXT,
            downtime_minutes INTEGER,
            status TEXT,
            action_taken TEXT,
            team_name TEXT,
            effect TEXT,
            last_notified_step INTEGER DEFAULT 0
        );
        """))

init_db()


# --- 2. ฟังก์ชันตรวจสอบและแจ้งเตือนงาน Breakdown ที่ค้างอยู่ (Background Worker) ---
def check_pending_breakdowns():
    """ฟังก์ชันเบื้องหลัง คอยเช็กงานค้างเพื่อส่งเตือน 30 นาที, 1 ชม. และทุกๆ 1 ชม."""
    while True:
        try:
            with engine.connect() as bg_conn:
                result = bg_conn.execute(text(
                    "SELECT id, machine_name, issue_description, reported_by, start_time, effect, last_notified_step FROM breakdown_logs WHERE status = 'Pending'"
                ))
                pending_rows = result.fetchall()

                now_dt = datetime.datetime.now(THAILAND_TZ)

                for row in pending_rows:
                    (
                        ticket_id,
                        machine,
                        issue,
                        reporter,
                        start_time_str,
                        effect,
                        last_step,
                    ) = row
                    if last_step is None:
                        last_step = 0

                    try:
                        start_dt = datetime.datetime.strptime(
                            str(start_time_str)[:16], "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=THAILAND_TZ)
                        elapsed_minutes = math.floor(
                            (now_dt - start_dt).total_seconds() / 60
                        )

                        target_step = 0
                        reminder_label = ""

                        # กำหนดเงื่อนไขการแจ้งเตือน
                        if elapsed_minutes >= 30 and last_step < 1 and elapsed_minutes < 60:
                            target_step = 1
                            reminder_label = "แจ้งเตือน: ปัญหายังไม่ถูกแก้ไขผ่านไปแล้ว 30 นาที!"
                        elif elapsed_minutes >= 60:
                            hours_passed = elapsed_minutes // 60
                            current_calculated_step = 1 + hours_passed

                            if current_calculated_step > last_step:
                                target_step = current_calculated_step
                                reminder_label = f"แจ้งเตือนด่วน: ปัญหายังไม่ถูกแก้ไขผ่านไปแล้ว {hours_passed} ชั่วโมง!"

                        # หากเข้าเงื่อนไขส่งเตือนรอบใหม่
                        if target_step > 0 and reminder_label:
                            line_msg = (
                                f"{reminder_label}\n"
                                f"Process: {machine}\n"
                                f"Problem Detail: {issue}\n"
                                f"Effect: {effect if effect else '-'}\n"
                                f"เวลาแจ้งเริ่มต้น: {start_time_str}"
                            )

                            if send_line_message(line_msg):
                                with engine.begin() as update_conn:
                                    update_conn.execute(
                                        text("UPDATE breakdown_logs SET last_notified_step = :target_step WHERE id = :ticket_id"),
                                        {"target_step": target_step, "ticket_id": ticket_id}
                                    )

                    except Exception as ex:
                        print(f"Error processing ticket {ticket_id}: {ex}")

        except Exception as e:
            print(f"Error in background checker: {e}")

        # เช็กทุกๆ 60 วินาที
        time.sleep(60)


# เริ่มการทำงานเบื้องหลัง (Background Thread) เพียงครั้งเดียว
if not any(
    thread.name == "BreakdownReminderThread"
    for thread in threading.enumerate()
):
    reminder_thread = threading.Thread(
        target=check_pending_breakdowns,
        name="BreakdownReminderThread",
        daemon=True,
    )
    reminder_thread.start()


# --- 3. ฟังก์ชันจัดการข้อมูลหลัก ---
def create_ticket(machine, issue, reporter, effect):
    now_dt = datetime.datetime.now(THAILAND_TZ)
    start_time_str = now_dt.strftime("%Y-%m-%d %H:%M")

    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO breakdown_logs (machine_name, issue_description, reported_by, start_time, status, effect, last_notified_step)
            VALUES (:machine, :issue, :reporter, :start_time, 'Pending', :effect, 0)
            """),
            {
                "machine": machine,
                "issue": issue,
                "reporter": reporter,
                "start_time": start_time_str,
                "effect": effect,
            }
        )

    line_msg = (
        f"แจ้งงาน Breakdown ใหม่!\n"
        f"Process: {machine}\n"
        f"ปัญหาที่พบ (Problem Detail): {issue}\n"
        f"ผลกระทบ (Effect): {effect}\n"
        f"ผู้แจ้งปัญหา (info. by): {reporter}\n"
        f"เวลาแจ้ง: {start_time_str}"
    )
    send_line_message(line_msg)


def close_ticket(ticket_id, team_name, action_detail):
    now_dt = datetime.datetime.now(THAILAND_TZ)
    end_time_str = now_dt.strftime("%Y-%m-%d %H:%M")

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT machine_name, start_time FROM breakdown_logs WHERE id = :ticket_id"),
            {"ticket_id": ticket_id}
        )
        row = result.fetchone()

    machine_name = row[0]
    start_time_val = str(row[1])

    try:
        start_dt = datetime.datetime.strptime(
            start_time_val[:16], "%Y-%m-%d %H:%M"
        )
        duration = now_dt.replace(tzinfo=None) - start_dt
        downtime_minutes = math.ceil(duration.total_seconds() / 60)
        if downtime_minutes < 0:
            downtime_minutes = 0
    except Exception:
        downtime_minutes = 0

    with engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE breakdown_logs 
            SET end_time = :end_time, downtime_minutes = :downtime, status = 'Closed', action_taken = :action, team_name = :team
            WHERE id = :ticket_id
            """),
            {
                "end_time": end_time_str,
                "downtime": downtime_minutes,
                "action": action_detail,
                "team": team_name,
                "ticket_id": ticket_id,
            }
        )

    line_msg = (
        f"ปิดงาน Breakdown แล้ว\n"
        f"Process: {machine_name}\n"
        f"ทีมที่ทำการแก้ไข: {team_name}\n"
        f"วิธีการแก้ไข: {action_detail}\n"
        f"เวลาที่ใช้ทั้งหมด (Downtime): {downtime_minutes} นาที\n"
        f"เวลาปิดงาน: {end_time_str}"
    )
    send_line_message(line_msg)


# --- 4. ส่วน Navigation & UI ---
st.set_page_config(page_title="Breakdown Management System", layout="wide")

if "current_page" not in st.session_state:
    st.session_state.current_page = "home"


def navigate_to(page_name):
    st.session_state.current_page = page_name


nav_col1, nav_col2, nav_col3, nav_col4 = st.columns(4)

with nav_col1:
    if st.button("หน้าแรก", use_container_width=True):
        navigate_to("home")
        st.rerun()

with nav_col2:
    if st.button("แจ้ง Breakdown", use_container_width=True):
        navigate_to("report")
        st.rerun()

with nav_col3:
    if st.button("รายการรอปิดงาน", use_container_width=True):
        navigate_to("pending")
        st.rerun()

with nav_col4:
    if st.button("ประวัติและสรุปผล", use_container_width=True):
        navigate_to("history")
        st.rerun()

st.divider()

if st.session_state.current_page == "home":
    st.title("ระบบบริหารจัดการงาน Breakdown")
    st.write("เลือกรายการเมนูด้านบนเพื่อเริ่มต้นใช้งาน")

    pending_count = pd.read_sql("SELECT COUNT(*) FROM breakdown_logs WHERE status = 'Pending'", con=engine).iloc[0, 0]
    closed_count = pd.read_sql("SELECT COUNT(*) FROM breakdown_logs WHERE status = 'Closed'", con=engine).iloc[0, 0]

    col1, col2 = st.columns(2)
    col1.metric("งานกำลังดำเนินการ (Pending)", f"{pending_count} รายการ")
    col2.metric("งานที่ปิดเสร็จสิ้น (Closed)", f"{closed_count} รายการ")

    st.write("")
    if st.button("กดที่นี่เพื่อแจ้ง Breakdown ใหม่"):
        navigate_to("report")
        st.rerun()

elif st.session_state.current_page == "report":
    st.title("ฟอร์มแจ้งเครื่องจักรขัดข้อง")

    selected_process = st.selectbox(
        "Process *", PROCESS_OPTIONS, key="process_select"
    )
    reported_by = st.text_input(
        "ชื่อผู้แจ้งปัญหา (info. by) *", key="reporter_input"
    )
    issue_description = st.text_area(
        "ปัญหาที่พบ (Problem Detail) *", key="issue_input"
    )

    selected_effect = st.selectbox(
        "ผลกระทบ (Effect) *", EFFECT_OPTIONS, key="effect_select"
    )

    other_effect_detail = ""
    if selected_effect == "อื่นๆระบุ (Other)":
        other_effect_detail = st.text_input(
            "ระบุรายละเอียด Effect อื่นๆ *",
            placeholder="เช่น ปรับเปลี่ยนแผนการพ่นสี",
            key="other_effect_input",
        )

    st.write("")
    if st.button(
        "บันทึกการแจ้ง Breakdown", use_container_width=True, type="primary"
    ):
        final_process = (
            selected_process if selected_process != PROCESS_OPTIONS[0] else ""
        )

        final_effect = ""
        if selected_effect == "อื่นๆระบุ (Other)":
            final_effect = (
                f"อื่นๆ: {other_effect_detail}" if other_effect_detail else ""
            )
        elif selected_effect != EFFECT_OPTIONS[0]:
            final_effect = selected_effect

        if final_process and reported_by and issue_description and final_effect:
            create_ticket(
                final_process, issue_description, reported_by, final_effect
            )
            now_th = datetime.datetime.now(THAILAND_TZ)
            st.success(
                f"บันทึกการแจ้งงานเรียบร้อยแล้ว และส่ง LINE แจ้งเตือนเข้ากลุ่มแล้ว (เวลาเริ่มต้น: {now_th.strftime('%Y-%m-%d %H:%M')})"
            )
        else:
            st.error(
                "กรุณากรอกข้อมูลและเลือกตัวเลือกที่มีเครื่องหมาย * ให้ครบถ้วน"
            )

elif st.session_state.current_page == "pending":
    st.title("รายการ Breakdown ที่กำลังดำเนินการ")

    pending_df = pd.read_sql(
        "SELECT id, machine_name, issue_description, reported_by, start_time, effect FROM breakdown_logs WHERE status = 'Pending'",
        con=engine,
    )

    if pending_df.empty:
        st.info("ไม่มีงาน Breakdown ค้างในระบบ")
    else:
        for idx, row in pending_df.iterrows():
            formatted_start = str(row["start_time"])[:16]
            effect_txt = row["effect"] if row["effect"] else "-"

            with st.expander(
                f"Process: {row['machine_name']} (แจ้งเมื่อ: {formatted_start} โดย {row['reported_by']})"
            ):
                st.write(
                    f"**ปัญหาที่พบ (Problem Detail):** {row['issue_description']}"
                )
                st.write(f"**ผลกระทบ (Effect):** {effect_txt}")

                with st.form(key=f"close_form_{row['id']}"):
                    team_name = st.text_input(
                        "ทีมที่ทำการแก้ไข *",
                        placeholder="เช่น Robot, Maintenance",
                    )
                    action_detail = st.text_area(
                        "รายละเอียดการแก้ไข (Detail) *",
                        placeholder="เช่น เปลี่ยนซีลกระบอกสูบ, รีเซ็ตโปรแกรมการพ่นสี",
                    )
                    close_btn = st.form_submit_button("บันทึกการปิดงาน")

                    if close_btn:
                        if team_name and action_detail:
                            close_ticket(row["id"], team_name, action_detail)
                            st.success(
                                f"ปิดงาน Process: {row['machine_name']} เรียบร้อยแล้ว และส่ง LINE สรุปผลเข้ากลุ่มแล้ว"
                            )
                            st.rerun()
                        else:
                            st.warning(
                                "กรุณากรอกชื่อทีมและรายละเอียดการแก้ไขให้ครบถ้วน"
                            )

elif st.session_state.current_page == "history":
    st.title("Record Downtime")

    all_df = pd.read_sql(
        "SELECT machine_name, issue_description, reported_by, start_time, end_time, downtime_minutes, status, team_name, action_taken, effect FROM breakdown_logs ORDER BY id DESC",
        con=engine,
    )

    if not all_df.empty:
        all_df["Date"] = all_df["start_time"].apply(
            lambda x: str(x)[:10] if pd.notna(x) and len(str(x)) >= 10 else ""
        )
        all_df["Start_time"] = all_df["start_time"].apply(
            lambda x: str(x)[11:16] if pd.notna(x) and len(str(x)) >= 16 else ""
        )
        all_df["Finish_time"] = all_df["end_time"].apply(
            lambda x: str(x)[11:16] if pd.notna(x) and len(str(x)) >= 16 else ""
        )

        def clean_data(row):
            action = (
                str(row["action_taken"])
                if row["action_taken"] is not None
                else ""
            )
            team = (
                str(row["team_name"]) if row["team_name"] is not None else ""
            )

            if "ทีมที่แก้ไข:" in action and " | รายละเอียด:" in action:
                parts = action.split(" | รายละเอียด: ")
                extracted_team = parts[0].replace("ทีมที่แก้ไข: ", "").strip()
                extracted_action = parts[1].strip() if len(parts) > 1 else ""
                return pd.Series([extracted_team, extracted_action])
            elif "ทีมที่แก้ไข:" in action:
                extracted_team = action.replace("ทีมที่แก้ไข: ", "").strip()
                return pd.Series([extracted_team, ""])
            else:
                return pd.Series([team, action])

        all_df[["team_name", "action_taken"]] = all_df.apply(
            clean_data, axis=1
        )

        rename_dict = {
            "machine_name": "Process",
            "issue_description": "Problem Detail",
            "downtime_minutes": "Downtime (minutes)",
            "effect": "Effect",
            "reported_by": "info. by",
            "team_name": "Correct by (Team)",
            "action_taken": "Detail",
        }
        all_df = all_df.rename(columns=rename_dict)

        ordered_columns = [
            "Date",
            "Process",
            "Start_time",
            "Finish_time",
            "Downtime (minutes)",
            "Problem Detail",
            "Effect",
            "info. by",
            "Correct by (Team)",
            "Detail",
            "status",
        ]

        all_df = all_df[ordered_columns]

        st.dataframe(all_df, use_container_width=True)
    else:
        st.write("ยังไม่มีข้อมูลในระบบ")