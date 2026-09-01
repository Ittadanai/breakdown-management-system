import datetime
import sqlite3
import pandas as pd
import requests
import streamlit as st

# --- ตั้งค่า LINE Messaging API ---
LINE_ACCESS_TOKEN = "/gRiogRyB9Yc549xgz6/M6Mxc3WLBfNW65zC7Tzev3I06I/Oa5VleMp8W9yWnGjjcrcRQ1q5sKXCqJq6WFylJV/KUB1o8ZxjzeRrzwRkO9kY6Y2l2OSQuFcW0ZKPj3SkyYTmaFlRoDygYvU3GVYgNwdB04t89/1O/w1cDnyilFU="
LINE_GROUP_ID = "C88ee91818e75720098992bf0cae49e82"


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
        response = requests.post(url, headers=headers, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending LINE message: {e}")
        return False


# --- 1. ตั้งค่าฐานข้อมูล SQLite ---
conn = sqlite3.connect("breakdown_db.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS breakdown_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_name TEXT,
    issue_description TEXT,
    reported_by TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    downtime_minutes REAL,
    status TEXT,
    action_taken TEXT
)
""")
conn.commit()


# --- 2. ฟังก์ชันจัดการข้อมูล ---
def create_ticket(machine, issue, reporter):
    start_time = datetime.datetime.now()
    cursor.execute(
        """
        INSERT INTO breakdown_logs (machine_name, issue_description, reported_by, start_time, status)
        VALUES (?, ?, ?, ?, ?)
    """,
        (machine, issue, reporter, start_time, "Pending"),
    )
    conn.commit()
    ticket_id = cursor.lastrowid

    # ส่ง LINE แจ้งเตือนเมื่อมีคนแจ้งงานใหม่
    line_msg = (
        f"แจ้งงาน Breakdown ใหม่!\n"
        f"ใบงาน: #{ticket_id}\n"
        f"เครื่องจักร: {machine}\n"
        f"อาการชำรุด: {issue}\n"
        f"ผู้แจ้งงาน: {reporter}\n"
        f"เวลาแจ้ง: {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_line_message(line_msg)


def close_ticket(ticket_id, team_name):
    end_time = datetime.datetime.now()
    cursor.execute(
        "SELECT machine_name, start_time FROM breakdown_logs WHERE id = ?",
        (ticket_id,),
    )
    row = cursor.fetchone()
    machine_name = row[0]
    start_time_str = row[1]
    start_time = datetime.datetime.strptime(
        start_time_str, "%Y-%m-%d %H:%M:%S.%f"
    )

    duration = end_time - start_time
    downtime_minutes = round(duration.total_seconds() / 60, 2)

    cursor.execute(
        """
        UPDATE breakdown_logs 
        SET end_time = ?, downtime_minutes = ?, status = ?, action_taken = ?
        WHERE id = ?
    """,
        (end_time, downtime_minutes, "Closed", f"ทีมที่แก้ไข: {team_name}", ticket_id),
    )
    conn.commit()

    # ส่ง LINE แจ้งเตือนเมื่อทีมช่างปิดงาน
    line_msg = (
        f"ปิดงาน Breakdown แล้ว\n"
        f"ใบงาน: #{ticket_id}\n"
        f"เครื่องจักร: {machine_name}\n"
        f"ทีมที่ทำการแก้ไข: {team_name}\n"
        f"เวลาที่ใช้ทั้งหมด (Downtime): {downtime_minutes} นาที\n"
        f"เวลาปิดงาน: {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_line_message(line_msg)


# --- 3. ส่วน Navigation & UI ---
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

    pending_count = pd.read_sql_query(
        "SELECT COUNT(*) FROM breakdown_logs WHERE status = 'Pending'", conn
    ).iloc[0, 0]
    closed_count = pd.read_sql_query(
        "SELECT COUNT(*) FROM breakdown_logs WHERE status = 'Closed'", conn
    ).iloc[0, 0]

    col1, col2 = st.columns(2)
    col1.metric("งานกำลังดำเนินการ (Pending)", f"{pending_count} รายการ")
    col2.metric("งานที่ปิดเสร็จสิ้น (Closed)", f"{closed_count} รายการ")

    st.write("")
    if st.button("กดที่นี่เพื่อแจ้ง Breakdown ใหม่"):
        navigate_to("report")
        st.rerun()

elif st.session_state.current_page == "report":
    st.title("ฟอร์มแจ้งเครื่องจักรขัดข้อง")
    with st.form(key="breakdown_form", clear_on_submit=True):
        machine_name = st.text_input("ชื่อเครื่องจักร / Line การผลิต *")
        reported_by = st.text_input("ชื่อผู้แจ้งงาน *")
        issue_description = st.text_area("รายละเอียดอาการชำรุด *")

        submit_btn = st.form_submit_button("บันทึกการแจ้ง Breakdown")

        if submit_btn:
            if machine_name and reported_by and issue_description:
                create_ticket(machine_name, issue_description, reported_by)
                st.success(
                    f"บันทึกการแจ้งงานเรียบร้อยแล้ว และส่ง LINE แจ้งเตือนเข้ากลุ่มแล้ว (เวลาเริ่มต้น: {datetime.datetime.now().strftime('%H:%M:%S')})"
                )
            else:
                st.error("กรุณากรอกข้อมูลที่มีเครื่องหมาย * ให้ครบถ้วน")

elif st.session_state.current_page == "pending":
    st.title("รายการ Breakdown ที่กำลังดำเนินการ")

    pending_df = pd.read_sql_query(
        "SELECT id, machine_name, issue_description, reported_by, start_time FROM breakdown_logs WHERE status = 'Pending'",
        conn,
    )

    if pending_df.empty:
        st.info("ไม่มีงาน Breakdown ค้างในระบบ")
    else:
        for idx, row in pending_df.iterrows():
            with st.expander(
                f"ID #{row['id']} - {row['machine_name']} (แจ้งเมื่อ: {row['start_time']} โดย {row['reported_by']})"
            ):
                st.write(f"**อาการชำรุด:** {row['issue_description']}")

                with st.form(key=f"close_form_{row['id']}"):
                    # ปรับช่องกรอกข้อมูลให้ระบุเฉพาะชื่อทีมที่เข้าแก้ไข
                    team_name = st.text_input("ทีมที่ทำการแก้ไข *", placeholder="เช่น Robot, Maintenance")
                    close_btn = st.form_submit_button("บันทึกการปิดงาน")

                    if close_btn:
                        if team_name:
                            close_ticket(row["id"], team_name)
                            st.success(
                                f"ปิดงาน ID #{row['id']} เรียบร้อยแล้ว และส่ง LINE สรุปผลเข้ากลุ่มแล้ว"
                            )
                            st.rerun()
                        else:
                            st.warning("กรุณาระบุชื่อทีมที่ทำการแก้ไขก่อนปิดงาน")

elif st.session_state.current_page == "history":
    st.title("ประวัติการซ่อมและสรุปเวลา Downtime")

    all_df = pd.read_sql_query(
        "SELECT * FROM breakdown_logs ORDER BY id DESC", conn
    )

    if not all_df.empty:
        closed_df = all_df[all_df["status"] == "Closed"]
        total_downtime = closed_df["downtime_minutes"].sum()

        col1, col2 = st.columns(2)
        col1.metric("จำนวนงานทั้งหมด", f"{len(all_df)} รายการ")
        col2.metric("เวลา Downtime รวมทั้งหมด", f"{total_downtime:.2f} นาที")

        st.divider()
        st.dataframe(all_df, use_container_width=True)
    else:
        st.write("ยังไม่มีข้อมูลในระบบ")