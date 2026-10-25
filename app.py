
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, send_file
)
import sqlite3
import os
import calendar
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

app = Flask(__name__)
app.secret_key = "smartattend_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")
TEMPLATE_NAME = "index.html"


# ==================================================
# DATABASE
# ==================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            course TEXT DEFAULT '',
            email TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ==================================================
# COMMON HELPERS
# ==================================================

def get_students(search=""):
    conn = get_db()

    if search:
        term = f"%{search}%"
        rows = conn.execute("""
            SELECT * FROM students
            WHERE name LIKE ?
               OR roll_no LIKE ?
               OR course LIKE ?
               OR email LIKE ?
            ORDER BY roll_no
        """, (term, term, term, term)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM students ORDER BY roll_no
        """).fetchall()

    conn.close()
    return rows


def get_subjects():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM subjects ORDER BY name"
    ).fetchall()
    conn.close()
    return rows



def get_attendance_summary(student_id, year=None, month=None):
    conn = get_db()

    query = """
        SELECT
            a.id,
            a.student_id,
            a.subject,
            a.attendance_date,
            a.status,
            s.name,
            s.roll_no
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        WHERE a.student_id = ?
    """
    params = [student_id]

    if year:
        query += " AND substr(a.attendance_date, 1, 4) = ?"
        params.append(str(year))

    if month:
        query += " AND substr(a.attendance_date, 6, 2) = ?"
        params.append(str(month).zfill(2))

    query += " ORDER BY a.attendance_date DESC, a.id DESC"

    records = conn.execute(query, params).fetchall()
    conn.close()

    total = len(records)

    present = sum(
        1 for row in records
        if str(row["status"]).lower() == "present"
    )

    absent = total - present
    percentage = round(present * 100 / total, 2) if total else 0

    return records, total, present, absent, percentage



def get_monthly_stats():
    conn = get_db()
    stats = []

    today = date.today()

    for offset in range(5, -1, -1):
        month_index = today.month - offset
        year = today.year

        while month_index <= 0:
            month_index += 12
            year -= 1

        month_text = f"{month_index:02d}"
        year_text = str(year)

        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN lower(status) = 'present'
                    THEN 1 ELSE 0 END) AS present,
                SUM(CASE WHEN lower(status) = 'absent'
                    THEN 1 ELSE 0 END) AS absent
            FROM attendance
            WHERE substr(attendance_date, 1, 4) = ?
              AND substr(attendance_date, 6, 2) = ?
        """, (year_text, month_text)).fetchone()

        stats.append({
            "label": calendar.month_abbr[month_index],
            "total": row["total"] or 0,
            "present": row["present"] or 0,
            "absent": row["absent"] or 0
        })

    conn.close()
    return stats


def render_page(page, **kwargs):
    """Render every section through the same index.html file."""
    return render_template(
        TEMPLATE_NAME,
        page=page,
        today=date.today().isoformat(),
        **kwargs
    )


# ==================================================
# DASHBOARD
# ==================================================

@app.route("/")
def home():
    conn = get_db()
    today = date.today().isoformat()

    total_students = conn.execute(
        "SELECT COUNT(*) FROM students"
    ).fetchone()[0]

    total_subjects = conn.execute(
        "SELECT COUNT(*) FROM subjects"
    ).fetchone()[0]

    total_records = conn.execute(
        "SELECT COUNT(*) FROM attendance"
    ).fetchone()[0]

    present_records = conn.execute("""
        SELECT COUNT(*) FROM attendance
        WHERE lower(status) = 'present'
    """).fetchone()[0]

    absent_records = conn.execute("""
        SELECT COUNT(*) FROM attendance
        WHERE lower(status) = 'absent'
    """).fetchone()[0]

    today_records = conn.execute("""
        SELECT COUNT(*) FROM attendance
        WHERE attendance_date = ?
    """, (today,)).fetchone()[0]

    today_present = conn.execute("""
        SELECT COUNT(*) FROM attendance
        WHERE attendance_date = ?
          AND lower(status) = 'present'
    """, (today,)).fetchone()[0]

    recent_rows = conn.execute("""
        SELECT
            a.id,
            a.subject,
            a.attendance_date,
            a.status,
            s.name,
            s.name AS student_name,
            s.roll_no
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        ORDER BY a.attendance_date DESC, a.id DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    rate = (
        round(present_records * 100 / total_records, 2)
        if total_records else 0
    )

    return render_page(
        "dashboard",
        total=total_students,
        total_students=total_students,
        total_subjects=total_subjects,
        total_records=total_records,
        today_records=today_records,
        today_present=today_present,
        present=present_records,
        absent=absent_records,
        rate=rate,
        monthly_stats=get_monthly_stats(),
        recent=recent_rows
    )


# ==================================================
# STUDENTS: LIST, SEARCH, ADD, EDIT, DELETE
# ==================================================

@app.route("/students", methods=["GET", "POST"])
def students():
    if request.method == "POST":
        action = request.form.get("action", "add").lower()

        conn = get_db()

        try:
            if action == "delete":
                student_id = request.form.get("student_id", "")

                if student_id.isdigit():
                    conn.execute(
                        "DELETE FROM attendance WHERE student_id = ?",
                        (int(student_id),)
                    )
                    conn.execute(
                        "DELETE FROM students WHERE id = ?",
                        (int(student_id),)
                    )
                    conn.commit()
                    flash("Student delete ho gaya.", "success")
                else:
                    flash("Invalid student ID.", "error")

            elif action == "edit":
                student_id = request.form.get("student_id", "")
                roll_no = request.form.get("roll_no", "").strip()
                name = request.form.get("name", "").strip()
                course = request.form.get("course", "").strip()
                email = request.form.get("email", "").strip()

                if not student_id.isdigit() or not roll_no or not name:
                    flash("Student ID, roll number aur name required hain.", "error")
                else:
                    conn.execute("""
                        UPDATE students
                        SET roll_no = ?, name = ?, course = ?, email = ?
                        WHERE id = ?
                    """, (
                        roll_no, name, course, email, int(student_id)
                    ))
                    conn.commit()
                    flash("Student details update ho gayi.", "success")

            else:
                roll_no = request.form.get("roll_no", "").strip()
                name = request.form.get("name", "").strip()
                course = request.form.get("course", "").strip()
                email = request.form.get("email", "").strip()

                if not roll_no or not name:
                    flash("Roll number aur student name required hain.", "error")
                else:
                    conn.execute("""
                        INSERT INTO students (roll_no, name, course, email)
                        VALUES (?, ?, ?, ?)
                    """, (roll_no, name, course, email))
                    conn.commit()
                    flash("Student successfully add ho gaya!", "success")

        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Ye roll number pehle se exist karta hai.", "error")
        except sqlite3.Error:
            conn.rollback()
            flash("Database error. Details check karke dobara try karo.", "error")
        finally:
            conn.close()

        return redirect(url_for("students"))

    search = request.args.get("search", "").strip()

    return render_page(
        "students",
        students=get_students(search),
        search=search
    )


# Support old delete URL if an existing button uses it.
@app.route("/students/delete/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    conn = get_db()
    conn.execute(
        "DELETE FROM attendance WHERE student_id = ?", (student_id,)
    )
    conn.execute(
        "DELETE FROM students WHERE id = ?", (student_id,)
    )
    conn.commit()
    conn.close()

    flash("Student delete ho gaya.", "success")
    return redirect(url_for("students"))


# ==================================================
# SUBJECTS: LIST, ADD, EDIT, DELETE
# ==================================================

@app.route("/subjects", methods=["GET", "POST"])
def subjects():
    if request.method == "POST":
        action = request.form.get("action", "add").lower()
        conn = get_db()

        try:
            if action == "delete":
                subject_id = request.form.get("subject_id", "")

                if subject_id.isdigit():
                    subject = conn.execute(
                        "SELECT name FROM subjects WHERE id = ?",
                        (int(subject_id),)
                    ).fetchone()

                    if subject:
                        conn.execute(
                            "DELETE FROM attendance WHERE subject = ?",
                            (subject["name"],)
                        )
                        conn.execute(
                            "DELETE FROM subjects WHERE id = ?",
                            (int(subject_id),)
                        )
                        conn.commit()

                    flash("Subject delete ho gaya.", "success")
                else:
                    flash("Invalid subject ID.", "error")

            elif action == "edit":
                subject_id = request.form.get("subject_id", "")
                name = request.form.get("name", "").strip()

                if not subject_id.isdigit() or not name:
                    flash("Subject ID aur name required hain.", "error")
                else:
                    old_subject = conn.execute(
                        "SELECT name FROM subjects WHERE id = ?",
                        (int(subject_id),)
                    ).fetchone()

                    if old_subject:
                        conn.execute("""
                            UPDATE subjects SET name = ? WHERE id = ?
                        """, (name, int(subject_id)))

                        # Keep existing attendance linked to the renamed subject.
                        conn.execute("""
                            UPDATE attendance SET subject = ?
                            WHERE subject = ?
                        """, (name, old_subject["name"]))

                        conn.commit()
                        flash("Subject update ho gaya.", "success")
                    else:
                        flash("Subject nahi mila.", "error")

            else:
                name = request.form.get("name", "").strip()

                if not name:
                    flash("Subject ka naam enter karo.", "error")
                else:
                    conn.execute(
                        "INSERT INTO subjects (name) VALUES (?)", (name,)
                    )
                    conn.commit()
                    flash("Subject add ho gaya!", "success")

        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Ye subject pehle se added hai.", "error")
        except sqlite3.Error:
            conn.rollback()
            flash("Database error. Dobara try karo.", "error")
        finally:
            conn.close()

        return redirect(url_for("subjects"))

    return render_page("subjects", subjects=get_subjects())


@app.route("/subjects/delete/<int:subject_id>", methods=["POST"])
def delete_subject(subject_id):
    conn = get_db()

    subject = conn.execute(
        "SELECT name FROM subjects WHERE id = ?", (subject_id,)
    ).fetchone()

    if subject:
        conn.execute(
            "DELETE FROM attendance WHERE subject = ?",
            (subject["name"],)
        )
        conn.execute(
            "DELETE FROM subjects WHERE id = ?", (subject_id,)
        )
        conn.commit()

    conn.close()
    flash("Subject delete ho gaya.", "success")
    return redirect(url_for("subjects"))


# ==================================================
# ATTENDANCE: MARK AND VIEW HISTORY
# ==================================================

@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    if request.method == "POST":
        attendance_date = request.form.get(
            "attendance_date", date.today().isoformat()
        ).strip()
        subject = request.form.get("subject", "").strip()

        try:
            datetime.strptime(attendance_date, "%Y-%m-%d")
        except ValueError:
            flash("Attendance date sahi select karo.", "error")
            return redirect(url_for("attendance"))

        conn = get_db()

        subject_exists = conn.execute(
            "SELECT id FROM subjects WHERE name = ?", (subject,)
        ).fetchone()

        if not subject_exists:
            conn.close()
            flash("Pehle valid subject select karo.", "error")
            return redirect(url_for("attendance"))

        all_students = conn.execute(
            "SELECT id FROM students ORDER BY roll_no"
        ).fetchall()

        if not all_students:
            conn.close()
            flash("Pehle Students section mein students add karo.", "error")
            return redirect(url_for("attendance"))

        # Supports either a single-student form or a whole-class form.
        single_student_id = request.form.get("student_id", "").strip()
        saved_count = 0

        try:
            if single_student_id.isdigit():
                student_ids = [int(single_student_id)]
            else:
                student_ids = [row["id"] for row in all_students]

            valid_ids = {row["id"] for row in all_students}

            for student_id in student_ids:
                if student_id not in valid_ids:
                    continue

                status = request.form.get(
                    f"status_{student_id}",
                    request.form.get("status", "Absent")
                ).strip().title()

                if status not in ("Present", "Absent"):
                    status = "Absent"

                existing = conn.execute("""
                    SELECT id FROM attendance
                    WHERE student_id = ?
                      AND subject = ?
                      AND attendance_date = ?
                """, (
                    student_id, subject, attendance_date
                )).fetchone()

                if existing:
                    conn.execute("""
                        UPDATE attendance SET status = ? WHERE id = ?
                    """, (status, existing["id"]))
                else:
                    conn.execute("""
                        INSERT INTO attendance
                        (student_id, subject, attendance_date, status)
                        VALUES (?, ?, ?, ?)
                    """, (student_id, subject, attendance_date, status))

                saved_count += 1

            conn.commit()

        except sqlite3.Error:
            conn.rollback()
            conn.close()
            flash("Attendance save nahi hui. Dobara try karo.", "error")
            return redirect(url_for("attendance"))

        conn.close()
        flash(f"Attendance save ho gayi! Records: {saved_count}", "success")
        return redirect(url_for("attendance"))

    selected_date = request.args.get(
        "attendance_date", date.today().isoformat()
    ).strip()
    selected_subject = request.args.get("subject", "").strip()

    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()

    conn = get_db()

    history = conn.execute("""
        SELECT
            a.id,
            a.student_id,
            a.subject,
            a.attendance_date,
            a.status,
            s.name,
            s.name AS student_name,
            s.roll_no,
            s.course,
            s.email
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        ORDER BY a.attendance_date DESC, a.id DESC
        LIMIT 100
    """).fetchall()

    existing_rows = conn.execute("""
        SELECT student_id, status
        FROM attendance
        WHERE attendance_date = ? AND subject = ?
    """, (selected_date, selected_subject)).fetchall()

    existing_records = {
        row["student_id"]: row["status"] for row in existing_rows
    }

    conn.close()

    return render_page(
        "attendance",
        students=get_students(),
        subjects=get_subjects(),
        history=history,
        existing_records=existing_records,
        selected_date=selected_date,
        selected_subject=selected_subject
    )


# ==================================================
# REPORTS: VIEW FILTERED STUDENT HISTORY
# ==================================================

@app.route("/reports")
def reports():
    conn = get_db()

    student_list = conn.execute(
        "SELECT * FROM students ORDER BY roll_no"
    ).fetchall()

    student_id = request.args.get("student_id", "").strip()
    year = request.args.get("year", "").strip()
    month = request.args.get("month", "").strip()

    selected_student = None
    records = []
    total = present = absent = 0
    percentage = 0

    if student_id.isdigit():
        selected_student = conn.execute(
            "SELECT * FROM students WHERE id = ?",
            (int(student_id),)
        ).fetchone()

        if selected_student:
            records, total, present, absent, percentage = (
                get_attendance_summary(
                    int(student_id),
                    year if year else None,
                    month if month else None
                )
            )

    years_rows = conn.execute("""
        SELECT DISTINCT substr(attendance_date, 1, 4) AS year
        FROM attendance
        WHERE attendance_date IS NOT NULL
        ORDER BY year DESC
    """).fetchall()

    conn.close()

    years = [row["year"] for row in years_rows if row["year"]]

    return render_page(
        "reports",
        students=student_list,
        records=records,
        totals={
            "total": total,
            "present": present,
            "absent": absent,
            "percentage": percentage
        },
        selected_student=selected_student,
        selected_year=year,
        selected_month=month,
        years=years
    )


# ==================================================
# PDF REPORT DOWNLOAD
# ==================================================

@app.route("/reports/pdf", methods=["GET", "POST"])
def reports_pdf():
    # Accept parameters from either a GET link or a POST form.
    student_id = (
        request.values.get("student_id", "").strip()
    )
    year = request.values.get("year", "").strip()
    month = request.values.get("month", "").strip()

    if not student_id.isdigit():
        flash("PDF download karne ke liye Reports mein student select karo.", "error")
        return redirect(url_for(
            "reports", year=year, month=month
        ))

    conn = get_db()

    student = conn.execute(
        "SELECT * FROM students WHERE id = ?",
        (int(student_id),)
    ).fetchone()

    conn.close()

    if not student:
        flash("Selected student nahi mila.", "error")
        return redirect(url_for("reports"))

    records, total, present, absent, percentage = get_attendance_summary(
        int(student_id),
        year if year else None,
        month if month else None
    )

    # Create PDF in memory; no extra file is needed.
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(
        "<b>SmartAttend - Student Attendance Report</b>",
        styles["Title"]
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        f"<b>Student:</b> {escape(str(student['name']))}",
        styles["Normal"]
    ))
    story.append(Paragraph(
        f"<b>Roll Number:</b> {escape(str(student['roll_no']))}",
        styles["Normal"]
    ))
    story.append(Paragraph(
        f"<b>Course:</b> {escape(str(student['course'] or '-'))}",
        styles["Normal"]
    ))
    story.append(Paragraph(
        f"<b>Email:</b> {escape(str(student['email'] or '-'))}",
        styles["Normal"]
    ))

    if year and month:
        period = f"{month}/{year}"
    elif year:
        period = year
    elif month:
        period = f"All years, month {month}"
    else:
        period = "All available dates"

    story.append(Paragraph(
        f"<b>Report period:</b> {escape(period)}",
        styles["Normal"]
    ))
    story.append(Spacer(1, 15))

    summary_data = [
        ["Total Lectures", "Present", "Absent", "Attendance %"],
        [str(total), str(present), str(absent), f"{percentage}%"]
    ]

    summary_table = Table(
        summary_data,
        colWidths=[150, 120, 120, 150]
    )
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8B78C6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("PADDING", (0, 0), (-1, -1), 8)
    ]))

    story.append(summary_table)
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "<b>Subject-wise Summary</b>", styles["Heading2"]
    ))

    subject_summary = {}

    for record in records:
        subject_name = record["subject"] or "Unspecified"

        if subject_name not in subject_summary:
            subject_summary[subject_name] = {
                "total": 0, "present": 0, "absent": 0
            }

        subject_summary[subject_name]["total"] += 1

        if str(record["status"]).lower() == "present":
            subject_summary[subject_name]["present"] += 1
        else:
            subject_summary[subject_name]["absent"] += 1

    subject_data = [
        ["Subject", "Total", "Present", "Absent", "Attendance %"]
    ]

    for subject_name, values in subject_summary.items():
        count = values["total"]
        subject_rate = (
            round(values["present"] * 100 / count, 2) if count else 0
        )
        subject_data.append([
            Paragraph(escape(str(subject_name)), styles["Normal"]),
            str(count),
            str(values["present"]),
            str(values["absent"]),
            f"{subject_rate}%"
        ])

    if len(subject_data) == 1:
        subject_data.append(["No records", "0", "0", "0", "0%"])

    subject_table = Table(
        subject_data,
        colWidths=[220, 90, 90, 90, 130],
        repeatRows=1
    )
    subject_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8B78C6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 7)
    ]))

    story.append(subject_table)
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "<b>Date-wise Attendance History</b>",
        styles["Heading2"]
    ))

    history_data = [["Date", "Subject", "Status"]]

    for record in records:
        history_data.append([
            str(record["attendance_date"] or "-"),
            Paragraph(
                escape(str(record["subject"] or "-")),
                styles["Normal"]
            ),
            str(record["status"] or "-").title()
        ])

    if len(history_data) == 1:
        history_data.append(["No attendance records", "-", "-"])

    history_table = Table(
        history_data,
        colWidths=[150, 300, 150],
        repeatRows=1
    )
    history_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8B78C6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 7)
    ]))

    story.append(history_table)

    try:
        doc.build(story)
        buffer.seek(0)

        safe_roll = "".join(
            c for c in str(student["roll_no"])
            if c.isalnum() or c in ("-", "_")
        ) or str(student["id"])

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"SmartAttend_{safe_roll}_Report.pdf",
            mimetype="application/pdf"
        )

    except Exception:
        app.logger.exception("PDF generation failed")
        flash("PDF banate waqt error aaya. Terminal ka error check karo.", "error")
        return redirect(url_for(
            "reports",
            student_id=student_id,
            year=year,
            month=month
        ))


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":
    app.run(debug=True)
