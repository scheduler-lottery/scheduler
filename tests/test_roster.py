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
