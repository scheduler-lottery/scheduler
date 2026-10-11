"""Reading class lists out of Canvas exports, spreadsheets, and pasted text."""

from conftest import CANVAS_ROSTER
from roster import describe, parse_roster


def names(result):
    return [s["display_name"] for s in result.students]


def test_canvas_student_report_keeps_only_names_and_emails():
    result = parse_roster(CANVAS_ROSTER.encode())
    assert not result.error
    assert names(result) == ["Alex Johnson", "Sam Lee", "Riya Patel"]  # Test Student dropped
    assert result.students[0] == {"name_key": "alex johnson", "display_name": "Alex Johnson", "email": "alex@school.edu",
                                  "name_pending": 0}
    assert set(result.students[0]) == {"name_key", "display_name", "email", "name_pending"}  # no grades anywhere
    assert "Overall course grade" in result.ignored_columns
    assert result.canvas_rows == 1 and result.skipped == 0


def test_canvas_class_roster_report():
    data = b"Student Name,Student ID,Email,Section\nAlex Johnson,101,alex@school.edu,Sec 1\nSam Lee,102,SAM@School.edu,Sec 1\n"
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee"]
    assert result.students[1]["email"] == "sam@school.edu"


def test_gradebook_export_flips_last_first_and_skips_points_possible():
    data = (
        b"Student,ID,SIS User ID,SIS Login ID,Section,Essay 1 (123)\n"
        b"    Points Possible,,,,,10\n"
        b"\"Johnson, Alex\",101,9001,ajohnson,Sec 1,9\n"
        b"\"Diaz, Maria\",102,9002,mdiaz,Sec 1,8\n"
        b"\"Student, Test\",103,,,Sec 1,\n"
    )
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Maria Diaz"]
    assert result.flipped
    assert result.email_column == ""  # login IDs here aren't emails
    assert result.canvas_rows == 2


def test_login_id_used_as_email_when_it_is_one():
    data = b"Student,ID,SIS Login ID\n\"Johnson, Alex\",101,alex@school.edu\n\"Lee, Sam\",102,sam@school.edu\n"
    result = parse_roster(data)
    assert [s["email"] for s in result.students] == ["alex@school.edu", "sam@school.edu"]


def test_first_and_last_name_columns():
    data = b"First Name,Last Name,E-mail\nAlex,Johnson,alex@school.edu\nSam,Lee,\n"
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee"]
    assert result.with_email == 1


def test_plain_name_column_in_last_first_order_is_flipped():
    data = b"Name\n\"Johnson, Alex\"\n\"Lee, Sam\"\n\"Smith, Jr.\"\n"
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee", "Smith, Jr."]


def test_suffixes_are_not_mistaken_for_first_names():
    quoted = b'Full name,Email\n"John Smith, Jr.",js@school.edu\n"Ana Diaz, III",ad@school.edu\nSam Lee,sl@school.edu\n'
    assert names(parse_roster(quoted)) == ["John Smith, Jr.", "Ana Diaz, III", "Sam Lee"]
    one_column = b"Full name\nJohn Smith, Jr.\nAna Diaz, III\nSam Lee\n"
    assert names(parse_roster(one_column)) == ["John Smith, Jr.", "Ana Diaz, III", "Sam Lee"]


def test_headerless_paste_where_only_some_lines_have_emails():
    result = parse_roster(b"Riya Patel\nAlex Johnson, alex@school.edu\n")
    assert names(result) == ["Riya Patel", "Alex Johnson"]
    assert result.with_email == 1


def test_pasted_names_without_a_header():
    data = "Alex Johnson, alex@school.edu\nSam Lee, sam@school.edu\nRiya Patel\n".encode()
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee", "Riya Patel"]
    assert result.with_email == 2


def test_one_name_per_line():
    assert names(parse_roster(b"Alex Johnson\nSam Lee\n\nRiya Patel\n")) == ["Alex Johnson", "Sam Lee", "Riya Patel"]


def test_people_page_paste_drops_teachers():
    data = (
        "Alex Johnson\tajohnson\t9001\tSec 1\tStudent\n"
        "Pat Professor\tpprof\t1\tSec 1\tTeacher\n"
        "Sam Lee\tslee\t9002\tSec 1\tStudent\n"
    ).encode()
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee"]
    assert result.non_students == 1


def test_semicolons_tabs_bom_and_windows_encoding():
    assert names(parse_roster("﻿Name;Email\nZoë Ångström;zoe@school.edu\n".encode("utf-8"))) == ["Zoë Ångström"]
    assert names(parse_roster("Name\tEmail\nAlex Johnson\talex@school.edu\n".encode())) == ["Alex Johnson"]
    assert names(parse_roster("Name,Email\nRenée Côté,renee@school.edu\n".encode("cp1252"))) == ["Renée Côté"]
    assert names(parse_roster("Name,Email\nAlex Johnson,a@school.edu\n".encode("utf-16"))) == ["Alex Johnson"]


def test_duplicates_and_bad_emails_are_reported():
    data = b"Name,Email\nAlex Johnson,alex@school.edu\nalex  johnson,other@school.edu\nSam Lee,not-an-email\n"
    result = parse_roster(data)
    # Two students with one name both stay, told apart by their emails.
    assert names(result) == ["Alex Johnson", "Alex Johnson (2)", "Sam Lee"]
    assert [s["email"] for s in result.students][:2] == ["alex@school.edu", "other@school.edu"]
    assert result.duplicates == [] and result.numbered == ["Alex Johnson (2)"]
    assert result.bad_emails == [("Sam Lee", "not-an-email")]
    message, tips = describe(result, "x.csv")
    assert "Added 3 students" in message
    assert any("Two students are named Alex Johnson" in t and "“Alex Johnson (2)”" in t for t in tips)
    assert any("“not-an-email” looks incomplete" in t and "Sam Lee" in t for t in tips)


def test_helpful_errors():
    assert "couldn't read that file as a spreadsheet" in parse_roster(b"PK\x03\x04rest-of-a-zip").error
    assert ".xls" in parse_roster(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1junk").error
    assert "PDF" in parse_roster(b"%PDF-1.7 junk").error
    assert "doesn't look like a class list" in parse_roster(b"\x00\x01\x02" * 100).error
    assert "empty" in parse_roster(b"   \n").error
    assert "column of names" in parse_roster(b"ID,Grade,Score\n1,2,3\n4,5,6\n").error


def test_describe_mentions_missing_emails():
    _message, tips = describe(parse_roster(b"Name\nAlex Johnson\n"))
    assert any("No email addresses" in t for t in tips)


def test_excel_paste_with_last_first_names_and_tabs():
    data = "Johnson, Alex\talex@school.edu\nLee, Sam\tsam@school.edu\n".encode()
    result = parse_roster(data)
    assert names(result) == ["Alex Johnson", "Sam Lee"]
    assert result.with_email == 2 and result.bad_emails == []


def test_mac_line_endings_and_mac_roman_and_french_headers():
    assert names(parse_roster(b"Name,Email\rAlex Johnson,a@school.edu\rSam Lee,s@school.edu\r")) == ["Alex Johnson", "Sam Lee"]
    mac = "Name,Email\nJosé Hernández,j@school.edu\nZoë Ångström,z@school.edu\n".encode("mac_roman")
    assert names(parse_roster(mac)) == ["José Hernández", "Zoë Ångström"]
    french = "Nom;Prénom;Courriel\nDupont;Marie;marie@ecole.fr\nMartin;Luc;luc@ecole.fr\n".encode("utf-8")
    result = parse_roster(french)
    assert names(result) == ["Marie Dupont", "Luc Martin"] and result.with_email == 2


def test_pasted_lines_find_the_email_wherever_it_is():
    data = b"Alex Johnson alex@school.edu\nLee, Sam; sam@school.edu\n<maria@school.edu> Maria Diaz\nonly@school.edu\n"
    result = parse_roster(data)
    assert [(s["display_name"], s["email"], s["name_pending"]) for s in result.students] == [
        ("Alex Johnson", "alex@school.edu", 0), ("Sam Lee", "sam@school.edu", 0), ("Maria Diaz", "maria@school.edu", 0),
        ("Only", "only@school.edu", 1)]  # an email alone: on the list, and they type their name later
    assert result.email_only == ["only@school.edu"] and result.flipped
    assert all("@" not in s["display_name"] for s in result.students)


def test_a_list_of_emails_alone_is_a_class_list():
    result = parse_roster(b'alex.johnson@school.edu, "Lee, Sam" <sam@school.edu>; riya.patel@school.edu')
    assert [(s["display_name"], s["email"], s["name_pending"]) for s in result.students] == [
        ("Alex Johnson", "alex.johnson@school.edu", 1), ("Sam Lee", "sam@school.edu", 0),
        ("Riya Patel", "riya.patel@school.edu", 1)]
    _message, tips = describe(result)
    assert any("added by email address alone" in t for t in tips)


def test_email_domain_typos_are_flagged():
    data = b"Name,Email\nA One,a@example.edu\nB Two,b@example.edu\nC Three,c@example.edu\nD Four,d@exmaple.edu\n"
    _message, tips = describe(parse_roster(data))
    assert any("exmaple.edu" in t and "did you mean" in t for t in tips)


def test_xlsx_workbooks_are_read_directly():
    import io
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}><si><t>Name</t></si><si><t>Email</t></si>"
                                           "<si><t>Ana Lima</t></si><si><t>ana@school.edu</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>"
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="inlineStr"><is><t>ana@school.edu</t></is></c></row>'
                   "</sheetData></worksheet>")
    result = parse_roster(buf.getvalue())
    assert [(s["display_name"], s["email"]) for s in result.students] == [("Ana Lima", "ana@school.edu")]


def test_match_name_finds_people_typed_any_which_way():
    from roster import match_name
    from util import fold
    rows = [{"name_key": fold(n), "display_name": n, "email": e, "is_test": 0} for n, e in (
        ("José Hernández", "jose@x.edu"), ("Sam Lee", "sam@x.edu"), ("Jonas Müller", "jm@x.edu"),
        ("William Ortiz", "wo@x.edu"))]
    for typed, expected in (("jose hernandez", "José Hernández"), ("Lee, Sam", "Sam Lee"), ("SAM@x.edu", "Sam Lee"),
                            ("Muller Jonas", "Jonas Müller")):
        row, _ = match_name(typed, rows)
        assert row and row["display_name"] == expected, typed
    for typed, suggested in (("Jon Mull", "Jonas Müller"), ("Bill Ortiz", "William Ortiz"), ("Sam", "Sam Lee")):
        row, suggestions = match_name(typed, rows)
        assert row is None and suggestions[0]["display_name"] == suggested, typed
    assert match_name("Zed Nobody", rows) == (None, [])


def test_a_pasted_student_with_an_incomplete_email_is_kept():
    result = parse_roster(b"Sam Lee, sam.lee@example\nMaria Diaz, maria.diaz@example.edu\n")
    assert [(s["display_name"], s["email"]) for s in result.students] == [
        ("Sam Lee", ""), ("Maria Diaz", "maria.diaz@example.edu")]
    assert result.bad_emails == [("Sam Lee", "sam.lee@example")]


def test_files_that_are_not_lists_get_plain_reasons():
    import io
    import zipfile
    word = io.BytesIO()
    with zipfile.ZipFile(word, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    numbers = io.BytesIO()
    with zipfile.ZipFile(numbers, "w") as z:
        z.writestr("Index/Document.iwa", "x")
    for data, says in ((word.getvalue(), "Word document"), (numbers.getvalue(), "Apple Numbers"),
                       (b"\x89PNG\r\n\x1a\nxxxx", "picture"), (b"\xff\xd8\xff\xe0xxxx", "picture"),
                       (b"\xd0\xcf\x11\xe0" + "EncryptedPackage".encode("utf-16-le"), "password-protected")):
        error = parse_roster(data).error
        assert says in error and "paste" in error, says


def test_the_class_list_can_be_on_another_excel_tab():
    import io
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr">'
                   '<is><t>Exported from Canvas</t></is></c></row></sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><sheetData>'
                   '<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="inlineStr"><is><t>Email</t></is></c></row>'
                   '<row r="2"><c r="A2" t="inlineStr"><is><t>Ana Lima</t></is></c><c r="B2" t="inlineStr"><is><t>ana@x.edu</t></is></c></row>'
                   '</sheetData></worksheet>')
    result = parse_roster(buf.getvalue())
    assert [s["display_name"] for s in result.students] == ["Ana Lima"] and result.tab == 2
    assert any("tab 2" in t for t in describe(result)[1])


def test_a_zip_from_canvas_course_analytics_works():
    import io
    import zipfile

    from conftest import CANVAS_ROSTER

    def zipped(files):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name, text in files.items():
                z.writestr(name, text)
        return out.getvalue()

    grades = "Assignment Name,Due Date,Average Score,Points Possible\\nEssay 1,2026-10-01,88,100\\n"
    result = parse_roster(zipped({"course_grade.csv": grades, "students.csv": CANVAS_ROSTER, "__MACOSX/._x.csv": "x"}))
    assert not result.error and names(result) == ["Alex Johnson", "Sam Lee", "Riya Patel"] and result.with_email == 3
    # Only the grades: no class list in it, and the message says where the list is.
    result = parse_roster(zipped({"course_grade.csv": grades}))
    assert "no class list in it" in result.error and "Students tab" in result.error


def test_canvas_api_text_pasted_from_the_browser_keeps_only_names_and_emails():
    data = ('while(1);[{"id":1,"name":"Alex Johnson","sortable_name":"Johnson, Alex","login_id":"abc1",'
            '"email":"alex@u.school.edu"},{"id":2,"name":"Sam Lee","login_id":"sam@u.school.edu"},'
            '{"id":3,"name":"Riya Patel","login_id":"rp3"}]').encode()
    result = parse_roster(data)
    assert [(s["display_name"], s["email"]) for s in result.students] == [
        ("Alex Johnson", "alex@u.school.edu"), ("Sam Lee", "sam@u.school.edu"), ("Riya Patel", "")]
    assert all(set(s) == {"name_key", "display_name", "email", "name_pending"} for s in result.students)
    assert "no students in it" in parse_roster(b"[]").error


def _zipped(files):
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


CANVAS_GRADES = (
    "Assignment Name,Points Possible,Average\nReaction Paper 1,10,8.7\nMidterm Memo,50,41.2\nPresentation,20,\n"
)
# Canvas's Course Analytics student file: nine students, each with their own Student ID. Two Sam Riveras and two
# Chen Wus, one of each with no email; two Alex Kims, told apart by their emails.
SAME_NAMES = (
    "Student Name,Student ID,Student SIS ID,Email,Section Name\n"
    "Alex Kim,1001,S1001,akim1@u.school.edu,LAW 540 SEC 20\n"
    "Alex Kim,1002,S1002,akim2@u.school.edu,LAW 540 SEC 20\n"
    "Jordan Lee,1003,S1003,,LAW 540 SEC 20\n"
    "Maya Patel,1004,S1004,mpatel@u.school.edu,LAW 540 SEC 20\n"
    "Sam Rivera,1005,S1005,,LAW 540 SEC 20\n"
    "Sam Rivera,1006,S1006,srivera2@u.school.edu,LAW 540 SEC 20\n"
    "Chen Wu,1007,S1007,cwu@u.school.edu,LAW 540 SEC 20\n"
    "Chen Wu,1008,S1008,,LAW 540 SEC 20\n"
    "Priya Nguyen,1009,S1009,pnguyen@u.school.edu,LAW 540 SEC 20\n"
)


def test_students_who_share_a_name_are_all_kept_when_their_ids_differ():
    result = parse_roster(_zipped({"course_grade.csv": CANVAS_GRADES, "students.csv": SAME_NAMES}))
    assert not result.error
    assert [(s["display_name"], s["email"]) for s in result.students] == [
        ("Alex Kim", "akim1@u.school.edu"), ("Alex Kim (2)", "akim2@u.school.edu"), ("Jordan Lee", ""),
        ("Maya Patel", "mpatel@u.school.edu"),
        ("Sam Rivera", "srivera2@u.school.edu"), ("Sam Rivera (2)", ""),  # the plain name goes to the one with an email
        ("Chen Wu", "cwu@u.school.edu"), ("Chen Wu (2)", ""),
        ("Priya Nguyen", "pnguyen@u.school.edu")]
    assert result.pin_twins == ["Sam Rivera (2)", "Chen Wu (2)"] and result.repeated == [] and result.merged == []
    # The IDs only told them apart: none is kept.
    assert all(set(s) == {"name_key", "display_name", "email", "name_pending"} for s in result.students)
    assert "Student ID" in result.ignored_columns and result.sections == {"LAW 540 SEC 20": 9}
    message, tips = describe(result, "students.csv")
    assert "Added 9 students" in message and "6 with email addresses" in message
    assert not any("listed twice" in t for t in tips)
    assert any("the one with no email is “Sam Rivera (2)”" in t and "sign in with that name" in t for t in tips)
    assert any("Two students are named Alex Kim, so they're listed as “Alex Kim” and “Alex Kim (2)”" in t for t in tips)
    no_email = next(t for t in tips if "had no email address" in t)
    assert "3 students had no email address: Jordan Lee, Sam Rivera (2) and Chen Wu (2)." in no_email
    assert "make up their own PIN (4 to 8 digits) the first time they open your link" in no_email
    assert "You don't hand out PINs" in no_email


def test_two_students_with_one_name_and_no_emails_both_stay():
    data = ("Student Name,Student ID,Email\nAna Lopez,9900,\nPat Morgan,9902,\nCara Diaz,9903,\nPat Morgan,9904,\n"
            + "".join(f"Student {i},{i},\n" for i in range(228))).encode()  # 232 rows
    result = parse_roster(data)
    assert len(result.students) == 232
    pats = [s for s in result.students if s["name_key"].startswith("pat morgan")]
    assert [(s["display_name"], s["email"]) for s in pats] == [("Pat Morgan", ""), ("Pat Morgan (2)", "")]
    assert result.pin_twins == ["Pat Morgan (2)"]
    tips = " ".join(describe(result)[1])
    assert ("Two students are named Pat Morgan and the list has no email for them, so they're listed as "
            "“Pat Morgan” and “Pat Morgan (2)” and sign in with a PIN. Tell each one which name is theirs") in tips


def test_one_student_listed_twice_with_the_same_id_or_email_is_kept_once():
    # The gradebook export lists a student in two sections twice, with one ID.
    data = (
        b"Student,ID,SIS User ID,SIS Login ID,Section,Essay 1 (123)\n"
        b"    Points Possible,,,,,10\n"
        b"\"Diaz, Maria\",102,9002,mdiaz,Sec 1,8\n"
        b"\"Diaz, Maria\",102,9002,mdiaz,Sec 2,8\n"
        b"\"Kim, Alex\",103,9003,akim,Sec 1,9\n"
    )
    result = parse_roster(data)
    assert names(result) == ["Maria Diaz", "Alex Kim"] and result.repeated == ["Maria Diaz"]
    assert result.pin_twins == [] and result.numbered == []
    assert "Maria Diaz was listed twice, with the same email or ID both times; we kept one." in describe(result)[1]
    # The same email twice, with different IDs: still one person.
    result = parse_roster(b"Name,ID,Email\nSam Lee,1,sam@x.edu\nSam Lee,2,sam@x.edu\n")
    assert names(result) == ["Sam Lee"] and result.repeated == ["Sam Lee"]
    # The same ID with the email on only one row: one person, with that email.
    result = parse_roster(b"Name,ID,Email\nSam Lee,1,\nSam Lee,1,sam@x.edu\n")
    assert [(s["display_name"], s["email"]) for s in result.students] == [("Sam Lee", "sam@x.edu")]


def test_a_typed_list_with_one_name_twice_keeps_one_and_says_it_cant_tell():
    result = parse_roster(b"Sam Lee\nSam Lee, sam@school.edu\nRiya Patel\n")
    assert [(s["display_name"], s["email"]) for s in result.students] == [("Sam Lee", "sam@school.edu"),
                                                                          ("Riya Patel", "")]
    assert result.merged == ["Sam Lee"] and result.repeated == []
    _message, tips = describe(result)
    assert not any("listed twice" in t for t in tips)
    assert any(t.startswith("Sam Lee is on the list twice without separate emails, so we can't tell if that's one "
                            "student or two. We kept one.") and "“Sam X. Lee”" in t for t in tips)


def test_a_list_from_canvas_keeps_students_who_share_a_name():
    from roster import parse_people
    result = parse_people([("Sam Rivera", ""), ("Sam Rivera", ""), ("Ana Lee", "ana@x.edu")])
    assert names(result) == ["Sam Rivera", "Sam Rivera (2)", "Ana Lee"] and result.pin_twins == ["Sam Rivera (2)"]
    assert all(set(s) == {"name_key", "display_name", "email", "name_pending"} for s in result.students)
    assert result.ignored_columns == [] and result.name_column == ""
    result = parse_people([("Sam Rivera", ""), ("Sam Rivera", "sam@x.edu")])
    assert [(s["display_name"], s["email"]) for s in result.students] == [("Sam Rivera", "sam@x.edu"),
                                                                          ("Sam Rivera (2)", "")]
    # One person twice (the same email) is still one.
    result = parse_people([("Sam Rivera", "sam@x.edu"), ("Sam Rivera", "sam@x.edu")])
    assert names(result) == ["Sam Rivera"] and result.repeated == ["Sam Rivera"]


def test_a_pasted_list_of_canvas_enrollments_counts_a_two_section_student_once():
    import json
    enrollments = [{"type": "StudentEnrollment", "user": {"id": uid, "name": name, "login_id": f"u{uid}"}}
                   for uid, name in ((7, "Sam Rivera"), (7, "Sam Rivera"), (8, "Sam Rivera"), (9, "Ana Lee"))]
    result = parse_roster(json.dumps(enrollments).encode())
    assert names(result) == ["Sam Rivera", "Sam Rivera (2)", "Ana Lee"] and result.repeated == []


def test_canvas_grades_file_is_refused_but_the_gradebook_export_is_read():
    from roster import CANVAS_GRADES_FILE
    assert parse_roster(CANVAS_GRADES.encode()).error == CANVAS_GRADES_FILE
    assert "the one with “student” in its name" in CANVAS_GRADES_FILE and "nothing was added" in CANVAS_GRADES_FILE
    assert parse_roster(b"Assignment,Due Date,Average Score\nEssay 1,2026-10-01,88\n").error == CANVAS_GRADES_FILE
    # The gradebook export: a Student heading, and "Points Possible" is a row under it.
    gradebook = parse_roster(b"Student,ID,SIS User ID,SIS Login ID,Section,Essay 1 (123)\n"
                             b"    Points Possible,,,,,10\n\"Johnson, Alex\",101,9001,ajohnson,Sec 1,9\n")
    assert not gradebook.error and names(gradebook) == ["Alex Johnson"] and gradebook.canvas_rows == 1
    # Canvas's ZIP: the student file is used, the grades file passed over.
    result = parse_roster(_zipped({"course_grade.csv": CANVAS_GRADES, "students.csv": CANVAS_ROSTER}))
    assert not result.error and names(result) == ["Alex Johnson", "Sam Lee", "Riya Patel"]
    assert "no class list in it" in parse_roster(_zipped({"course_grade.csv": CANVAS_GRADES})).error


def test_a_heading_row_is_never_a_student():
    for data, expected in (
        (b"Students,Year,Email\nAlex Kim,2L,alex@x.edu\nSam Lee,3L,sam@x.edu\n", ["Alex Kim", "Sam Lee"]),
        (b"Student Names,Email\nAlex Kim,alex@x.edu\nSam Lee,sam@x.edu\n", ["Alex Kim", "Sam Lee"]),
        (b"Names\nAlex Kim\nSam Lee\n", ["Alex Kim", "Sam Lee"]),
        (b"Participant,Email\nAlex Kim,alex@x.edu\n", ["Alex Kim"]),
        (b"Participant,Netid,Year\nAlex Kim,ak1,2L\nSam Lee,sl2,3L\n", ["Alex Kim", "Sam Lee"]),
        (b"ID,Learner,Year\n1,Alex Kim,2L\n2,Sam Lee,3L\n", ["Alex Kim", "Sam Lee"]),
        (b"Attendee,E-mail Addr,Year\nAlex Kim,alex@x.edu,2L\nSam Lee,sam@x.edu,3L\n", ["Alex Kim", "Sam Lee"]),
    ):
        result = parse_roster(data)
        assert names(result) == expected and result.bad_emails == [], data
    result = parse_roster(b"Students,Year,Email\nAlex Kim,2L,alex@x.edu\n")
    assert result.name_column == "Students" and result.ignored_columns == ["Year"]
    assert "no students under them" in parse_roster(b"ID,Learner,Year\n").error


def test_a_first_line_of_only_punctuation_is_no_trouble():
    for divider in ("-----", "—", "*", "=========="):
        result = parse_roster(f"{divider}\nAlex Kim\nSam Lee\n".encode())
        assert not result.error and names(result) == ["Alex Kim", "Sam Lee"], divider
    # "#" heads a column of numbers: the row is headings, not a student named “Learner”.
    result = parse_roster(b"#,Learner,Year\n1,Alex Kim,2L\n2,Sam Lee,3L\n")
    assert not result.error and names(result) == ["Alex Kim", "Sam Lee"]


def test_a_divider_line_pasted_on_the_class_page_is_read(prof):
    sid = prof.create_sheet()
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "-----\nAlex Kim\nSam Lee\n"})
    assert r.status_code == 302
    r = prof.upload(sid, "#,Learner,Year\n1,Alex Kim,2L\n2,Sam Lee,3L\n3,Riya Patel,2L\n", "my class.csv")
    assert r.status_code == 302 and "Riya Patel" in prof.get(r.headers["Location"]).get_data(as_text=True)


def test_lists_without_headings_keep_their_first_row():
    assert names(parse_roster(b"Riya Patel\nAlex Johnson, alex@school.edu\n")) == ["Riya Patel", "Alex Johnson"]
    assert names(parse_roster(b"Alex Johnson\nSam Lee\n")) == ["Alex Johnson", "Sam Lee"]
    assert names(parse_roster(b"Alex Kim,2L,Torts\nSam Lee,3L,Contracts\n")) == ["Alex Kim", "Sam Lee"]
    people_page = b"Alex Johnson\tajohnson\t9001\tSec 1\tStudent\nSam Lee\tslee\t9002\tSec 1\tStudent\n"
    assert names(parse_roster(people_page)) == ["Alex Johnson", "Sam Lee"]


def test_the_no_email_notes_say_who_picks_a_pin_and_name_no_browser_button():
    _message, tips = describe(parse_roster(b"Name\nAlex Johnson\nSam Lee\n"))
    note = next(t for t in tips if "No email addresses" in t)
    assert "Get my class list from Canvas" not in note
    assert "“Class changed in Canvas? Update from Canvas”" in note and "emails are filled in" in note
    _message, tips = describe(parse_roster(b"Name,Email\nAlex Johnson,alex@x.edu\nSam Lee,\n"))
    assert ("Sam Lee had no email address, so they'll make up their own PIN (4 to 8 digits) the first time they "
            "open your link. You don't hand out PINs.") in " ".join(tips)
    many = "Name,Email\nAlex Johnson,alex@x.edu\n" + "".join(f"Student {c},\n" for c in "ABCDE")
    _message, tips = describe(parse_roster(many.encode()))
    assert any(t.startswith("5 students had no email address: Student A, Student B, Student C and 2 more.")
               for t in tips)
    # An incomplete address has a note of its own.
    _message, tips = describe(parse_roster(b"Name,Email\nAlex Johnson,alex@x.edu\nSam Lee,sam@x\n"))
    assert not any("had no email address" in t for t in tips) and any("“sam@x” looks incomplete" in t for t in tips)


def test_an_incomplete_email_is_reported_on_the_same_name_student_it_was_on():
    # The one with the good email gets the plain name, so the note follows the other to “Sam Lee (2)”.
    result = parse_roster(b"Name,ID,Email\nSam Lee,1,sam@x\nSam Lee,2,sl@x.edu\n")
    assert [(s["display_name"], s["email"]) for s in result.students] == [("Sam Lee", "sl@x.edu"), ("Sam Lee (2)", "")]
    assert result.bad_emails == [("Sam Lee (2)", "sam@x")]
    tips = describe(result)[1]
    assert any(t.startswith("Sam Lee (2)'s email “sam@x” looks incomplete") for t in tips)
    assert not any("had no email address" in t for t in tips)  # the note above says it already


def test_a_student_listed_three_times_is_named_once():
    result = parse_roster(b"Name,ID\nMaria Diaz,1\nMaria Diaz,1\nMaria Diaz,1\nAl Bo,2\n")
    assert names(result) == ["Maria Diaz", "Al Bo"]
    assert ("Maria Diaz was listed more than once, with the same email or ID each time; we kept one."
            in describe(result)[1])
    _message, tips = describe(parse_roster(b"Sam Lee\nSam Lee\nSam Lee\nAl Bo\n"))
    assert any(t.startswith("Sam Lee is on the list more than once without separate emails, so we can't tell if "
                            "that's one student or more.") for t in tips)
