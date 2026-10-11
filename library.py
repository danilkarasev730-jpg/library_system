

import re
import sqlite3
import threading
from datetime import date, timedelta


# ----------------------------------------
# ЧАСТЬ 1. СВОИ ОШИБКИ
# Чтобы отличать ошибки библиотеки от обычных
# ----------------------------------------

class LibraryError(Exception):
    pass


class ValidationError(LibraryError):
    pass


# ----------------------------------------
# ЧАСТЬ 2. ПРОВЕРКИ (RegEx)
# Проверяем правильность ISBN, имени, номера билета
# ----------------------------------------

ISBN_RE = re.compile(r"^\d{13}$|^\d{9}[\dXx]$")
NAME_RE = re.compile(r"^[А-ЯЁA-Z][а-яёa-z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-z\-]+){1,2}$")
CARD_RE = re.compile(r"^LIB-\d{6}$")


def check_isbn(isbn):
    clean = isbn.replace("-", "")
    if not ISBN_RE.match(clean):
        raise ValidationError("Некорректный ISBN: " + isbn)
    return clean


def check_card(card):
    if not CARD_RE.match(card):
        raise ValidationError("Некорректный номер билета: " + card)
    return card


# ----------------------------------------
# ЧАСТЬ 3. КЛАССЫ — простые "контейнеры" данных
# ----------------------------------------

class Reader:
    # Читатель: у студента 5 книг / 14 дней, у сотрудника 10 книг / 30 дней
    def __init__(self, id, name, card, category, blocked=False, fine=0.0):
        self.id = id
        self.name = name
        self.card_number = card
        self.category = category
        self.is_blocked = blocked
        self.total_fine = fine

    def max_books(self):
        return 5 if self.category == "student" else 10

    def loan_days(self):
        return 14 if self.category == "student" else 30

    def __str__(self):
        return "Читатель: {} ({}), штраф {} руб.".format(
            self.name, self.card_number, self.total_fine)


class Book:
    # Книга: может быть available (свободна) или issued (выдана)
    def __init__(self, id, title, author, isbn, status="available"):
        self.id = id
        self.title = title
        self.author = author
        self.isbn = isbn
        self.status = status

    def is_available(self):
        return self.status == "available"

    def __str__(self):
        return "«{}» — {} [{}]".format(self.title, self.author, self.status)


class Loan:
    # Выдача книги: хранит даты и считает просрочку
    def __init__(self, id, book_id, reader_id, issue_date, due_date,
                 return_date=None, renewals=0, status="active"):
        self.id = id
        self.book_id = book_id
        self.reader_id = reader_id
        self.issue_date = issue_date
        self.due_date = due_date
        self.return_date = return_date
        self.renewals = renewals
        self.status = status

    def days_overdue(self):
        # Сколько дней просрочки (если не просрочено — 0)
        d = (date.today() - self.due_date).days
        return d if d > 0 else 0

    def calculate_fine(self):
        # Штраф: 10 рублей за каждый день просрочки
        return round(self.days_overdue() * 10.0, 2)


# ----------------------------------------
# ЧАСТЬ 4. БАЗА ДАННЫХ (SQLite)
# Хранит читателей, книги, выдачи, штрафы
# ----------------------------------------

class Database:
    def __init__(self, path="library.db"):
        self.path = path
        self._create()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _create(self):
        # Создаём таблицы при первом запуске
        c = self._conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS readers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, card_number TEXT UNIQUE,
                category TEXT, is_blocked INTEGER DEFAULT 0,
                total_fine REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS books(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT, author TEXT, isbn TEXT UNIQUE,
                status TEXT DEFAULT 'available');
            CREATE TABLE IF NOT EXISTS loans(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER, reader_id INTEGER,
                issue_date TEXT, due_date TEXT, return_date TEXT,
                renewals INTEGER DEFAULT 0, status TEXT DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS fines(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reader_id INTEGER, loan_id INTEGER, amount REAL,
                reason TEXT, paid INTEGER DEFAULT 0);
        """)
        c.commit()
        c.close()

    # ---- РАБОТА С ЧИТАТЕЛЯМИ ----
    def add_reader(self, r):
        c = self._conn()
        cur = c.execute(
            "INSERT INTO readers(name,card_number,category,is_blocked,total_fine) "
            "VALUES(?,?,?,?,?)",
            (r.name, r.card_number, r.category, int(r.is_blocked), r.total_fine))
        c.commit()
        rid = cur.lastrowid
        c.close()
        return rid

    def get_reader(self, id):
        c = self._conn()
        row = c.execute("SELECT * FROM readers WHERE id=?", (id,)).fetchone()
        c.close()
        if not row:
            return None
        return Reader(row["id"], row["name"], row["card_number"],
                      row["category"], bool(row["is_blocked"]), row["total_fine"])

    def get_reader_by_card(self, card):
        c = self._conn()
        row = c.execute("SELECT * FROM readers WHERE card_number=?", (card,)).fetchone()
        c.close()
        if not row:
            return None
        return Reader(row["id"], row["name"], row["card_number"],
                      row["category"], bool(row["is_blocked"]), row["total_fine"])

    def update_reader(self, r):
        c = self._conn()
        c.execute("UPDATE readers SET is_blocked=?, total_fine=? WHERE id=?",
                  (int(r.is_blocked), r.total_fine, r.id))
        c.commit()
        c.close()

    # ---- РАБОТА С КНИГАМИ ----
    def add_book(self, b):
        c = self._conn()
        cur = c.execute("INSERT INTO books(title,author,isbn,status) VALUES(?,?,?,?)",
                        (b.title, b.author, b.isbn, b.status))
        c.commit()
        bid = cur.lastrowid
        c.close()
        return bid

    def find_book(self, isbn):
        c = self._conn()
        row = c.execute("SELECT * FROM books WHERE isbn=?", (isbn,)).fetchone()
        c.close()
        if not row:
            return None
        return Book(row["id"], row["title"], row["author"], row["isbn"], row["status"])

    def update_book(self, b):
        c = self._conn()
        c.execute("UPDATE books SET status=? WHERE id=?", (b.status, b.id))
        c.commit()
        c.close()

    def list_books(self):
        c = self._conn()
        rows = c.execute("SELECT * FROM books").fetchall()
        c.close()
        return [Book(r["id"], r["title"], r["author"], r["isbn"], r["status"]) for r in rows]

    # ---- РАБОТА С ВЫДАЧАМИ ----
    def add_loan(self, l):
        c = self._conn()
        cur = c.execute(
            "INSERT INTO loans(book_id,reader_id,issue_date,due_date,status) "
            "VALUES(?,?,?,?,?)",
            (l.book_id, l.reader_id, l.issue_date.isoformat(),
             l.due_date.isoformat(), l.status))
        c.commit()
        lid = cur.lastrowid
        c.close()
        return lid

    def update_loan(self, l):
        c = self._conn()
        c.execute(
            "UPDATE loans SET return_date=?, renewals=?, status=? WHERE id=?",
            (l.return_date.isoformat() if l.return_date else None,
             l.renewals, l.status, l.id))
        c.commit()
        c.close()

    def get_loan(self, id):
        c = self._conn()
        row = c.execute("SELECT * FROM loans WHERE id=?", (id,)).fetchone()
        c.close()
        if not row:
            return None
        return Loan(row["id"], row["book_id"], row["reader_id"],
                    date.fromisoformat(row["issue_date"]),
                    date.fromisoformat(row["due_date"]),
                    date.fromisoformat(row["return_date"]) if row["return_date"] else None,
                    row["renewals"], row["status"])

    def active_loans_by_reader(self, reader_id):
        c = self._conn()
        rows = c.execute(
            "SELECT * FROM loans WHERE reader_id=? AND status='active'",
            (reader_id,)).fetchall()
        c.close()
        return [Loan(r["id"], r["book_id"], r["reader_id"],
                     date.fromisoformat(r["issue_date"]),
                     date.fromisoformat(r["due_date"]),
                     None, r["renewals"], r["status"]) for r in rows]

    def active_loan_for_book(self, book_id):
        c = self._conn()
        row = c.execute(
            "SELECT * FROM loans WHERE book_id=? AND status='active' "
            "ORDER BY id DESC LIMIT 1", (book_id,)).fetchone()
        c.close()
        if not row:
            return None
        return Loan(row["id"], row["book_id"], row["reader_id"],
                    date.fromisoformat(row["issue_date"]),
                    date.fromisoformat(row["due_date"]),
                    None, row["renewals"], row["status"])

    # ---- РАБОТА СО ШТРАФАМИ ----
    def add_fine(self, reader_id, loan_id, amount, reason):
        c = self._conn()
        c.execute("INSERT INTO fines(reader_id,loan_id,amount,reason) VALUES(?,?,?,?)",
                  (reader_id, loan_id, amount, reason))
        c.commit()
        c.close()

    def pay_all_fines(self, reader_id):
        # Оплатить все штрафы и разблокировать читателя
        c = self._conn()
        row = c.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM fines "
            "WHERE reader_id=? AND paid=0", (reader_id,)).fetchone()
        total = row["s"]
        c.execute("UPDATE fines SET paid=1 WHERE reader_id=? AND paid=0", (reader_id,))
        c.execute("UPDATE readers SET total_fine=0, is_blocked=0 WHERE id=?", (reader_id,))
        c.commit()
        c.close()
        return total


# ----------------------------------------
# ЧАСТЬ 5. ГЛАВНАЯ ЛОГИКА
# Здесь происходит выдача, возврат, штрафы
# ----------------------------------------

class LibraryService:
    MAX_FINE_BLOCK = 500.0      # блокируем если штраф больше 500
    MAX_OVERDUE_BLOCK = 30      # блокируем если просрочка больше 30 дней
    MAX_RENEWALS = 2            # максимум продлений
    DAMAGE_FEE = 1000.0         # штраф за порчу книги

    def __init__(self, db):
        self.db = db

    # ---- ВЫДАЧА КНИГИ ----
    def issue_book(self, card_number, isbn):
        # 1. Проверяем данные регуляркой
        try:
            card = check_card(card_number)
            isbn_clean = check_isbn(isbn)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}

        # 2. Ищем читателя
        reader = self.db.get_reader_by_card(card)
        if not reader:
            return {"ok": False, "error": "Читатель не найден"}

        # 3. Проверяем блокировку
        self._update_block(reader)
        if reader.is_blocked:
            return {"ok": False, "error": "Читатель заблокирован. Штраф: {:.2f} руб.".format(reader.total_fine)}

        # 4. Ищем книгу
        book = self.db.find_book(isbn_clean)
        if not book:
            return {"ok": False, "error": "Книга не найдена"}
        if not book.is_available():
            return {"ok": False, "error": "Книга уже выдана"}

        # 5. Проверяем лимит книг
        active = self.db.active_loans_by_reader(reader.id)
        if len(active) >= reader.max_books():
            return {"ok": False, "error": "Достигнут лимит: {} книг".format(reader.max_books())}

        # 6. Проверяем просрочки
        for l in active:
            if l.days_overdue() > 0:
                return {"ok": False, "error": "Есть просроченные книги. Погасите долг."}

        # 7. Оформляем выдачу
        loan = Loan(None, book.id, reader.id, date.today(),
                    date.today() + timedelta(days=reader.loan_days()))
        loan_id = self.db.add_loan(loan)

        book.status = "issued"
        self.db.update_book(book)

        return {"ok": True, "message": "Книга выдана", "loan_id": loan_id,
                "reader": reader.name, "book": book.title,
                "due_date": loan.due_date.isoformat()}

    # ---- ВОЗВРАТ КНИГИ ----
    def return_book(self, isbn, damaged=False):
        # 1. Проверяем ISBN
        try:
            isbn_clean = check_isbn(isbn)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}

        # 2. Ищем книгу
        book = self.db.find_book(isbn_clean)
        if not book:
            return {"ok": False, "error": "Книга не найдена"}

        # 3. Ищем активную выдачу
        loan = self.db.active_loan_for_book(book.id)
        if not loan:
            return {"ok": False, "error": "Активная выдача не найдена"}

        reader = self.db.get_reader(loan.reader_id)
        fine_sum = 0.0

        # 4. Штраф за просрочку
        overdue = loan.calculate_fine()
        if overdue > 0:
            self.db.add_fine(reader.id, loan.id, overdue, "overdue")
            fine_sum += overdue

        # 5. Штраф за порчу
        if damaged:
            self.db.add_fine(reader.id, loan.id, self.DAMAGE_FEE, "damage")
            fine_sum += self.DAMAGE_FEE

        # 6. Обновляем читателя (и блокируем если надо)
        reader.total_fine += fine_sum
        if reader.total_fine > self.MAX_FINE_BLOCK:
            reader.is_blocked = True
        self.db.update_reader(reader)

        # 7. Закрываем выдачу
        loan.return_date = date.today()
        loan.status = "returned"
        self.db.update_loan(loan)

        # 8. Освобождаем книгу
        book.status = "available"
        self.db.update_book(book)

        return {"ok": True, "message": "Возврат завершён", "fine": fine_sum,
                "reader_blocked": reader.is_blocked, "total_fine": reader.total_fine}

    # ---- ПРОДЛЕНИЕ ----
    def renew_loan(self, loan_id):
        loan = self.db.get_loan(loan_id)
        if not loan or loan.status != "active":
            return {"ok": False, "error": "Активная выдача не найдена"}
        if loan.renewals >= self.MAX_RENEWALS:
            return {"ok": False, "error": "Лимит продлений: {}".format(self.MAX_RENEWALS)}

        reader = self.db.get_reader(loan.reader_id)
        loan.due_date = loan.due_date + timedelta(days=reader.loan_days())
        loan.renewals += 1
        self.db.update_loan(loan)
        return {"ok": True, "message": "Продлено", "due_date": loan.due_date.isoformat()}

    # ---- ОПЛАТА ШТРАФА ----
    def pay_fine(self, reader_id):
        reader = self.db.get_reader(reader_id)
        if not reader:
            return {"ok": False, "error": "Читатель не найден"}
        total = self.db.pay_all_fines(reader_id)
        return {"ok": True, "message": "Оплачено", "paid": total}

    # ---- ВНУТРЕННЕЕ: проверить, не надо ли заблокировать ----
    def _update_block(self, reader):
        loans = self.db.active_loans_by_reader(reader.id)
        max_overdue = 0
        for l in loans:
            if l.days_overdue() > max_overdue:
                max_overdue = l.days_overdue()

        # assert — проверяем, что данные не сломались
        assert max_overdue >= 0, "Просрочка не может быть отрицательной"
        assert reader.total_fine >= 0, "Штраф не может быть отрицательным"

        if max_overdue > self.MAX_OVERDUE_BLOCK or reader.total_fine > self.MAX_FINE_BLOCK:
            reader.is_blocked = True
            self.db.update_reader(reader)


# ----------------------------------------
# ЧАСТЬ 6. НАПОМИНАНИЯ (через поток с таймером)
# Через N секунд выведет сообщение
# ----------------------------------------

class Notifier:
    def __init__(self, delay=2):
        self.delay = delay
        self.timers = []

    def remind(self, message):
        def _do():
            print("[НАПОМИНАНИЕ] " + message)
        t = threading.Timer(self.delay, _do)
        t.start()
        self.timers.append(t)

    def wait(self):
        for t in self.timers:
            t.join()


# ----------------------------------------
# ЧАСТЬ 7. ДЕМО — показываем, как всё работает
# ----------------------------------------

def demo():
    print("=" * 60)
    print("СИСТЕМА УПРАВЛЕНИЯ БИБЛИОТЕКОЙ — ДЕМО")
    print("=" * 60)

    # Удаляем старую базу, чтобы начать с чистого листа
    import os
    if os.path.exists("library.db"):
        os.remove("library.db")

    db = Database("library.db")
    service = LibraryService(db)
    notifier = Notifier(delay=1)

    # Шаг 1: добавляем книги
    print("\n[1] Добавляем книги:")
    b1 = Book(None, "Война и мир", "Л. Толстой", "9783161484100")
    b2 = Book(None, "Преступление и наказание", "Ф. Достоевский", "9785170884285")
    b1.id = db.add_book(b1)
    b2.id = db.add_book(b2)
    print("  ", b1)
    print("  ", b2)

    # Шаг 2: регистрируем читателей
    print("\n[2] Регистрируем читателей:")
    r1 = Reader(None, "Иван Петров", "LIB-000001", "student")
    r2 = Reader(None, "Мария Сидорова", "LIB-000002", "staff")
    r1.id = db.add_reader(r1)
    r2.id = db.add_reader(r2)
    print("  ", r1)
    print("  ", r2)

    # Шаг 3: выдаём книгу Ивану
    print("\n[3] Выдаём книгу Ивану:")
    print("  ", service.issue_book("LIB-000001", "9783161484100"))
    notifier.remind("Верните книгу «Война и мир» через 14 дней!")

    # Шаг 4: Мария пытается взять ту же книгу
    print("\n[4] Мария пытается взять ту же книгу:")
    print("  ", service.issue_book("LIB-000002", "9783161484100"))

    # Шаг 5: Иван возвращает в срок
    print("\n[5] Иван возвращает книгу в срок:")
    print("  ", service.return_book("9783161484100", damaged=False))

    # Шаг 6: просрочка + порча
    print("\n[6] Иван берёт книгу и возвращает с просрочкой и порчей:")
    service.issue_book("LIB-000001", "9785170884285")
    # Искусственно сдвигаем дату — как будто 5 дней назад надо было вернуть
    conn = sqlite3.connect("library.db")
    conn.execute("UPDATE loans SET due_date=? WHERE status='active'",
                 ((date.today() - timedelta(days=5)).isoformat(),))
    conn.commit()
    conn.close()
    print("  ", service.return_book("9785170884285", damaged=True))

    # Шаг 7: пробуем выдать книгу заблокированному
    print("\n[7] Пробуем выдать книгу заблокированному читателю:")
    print("  ", service.issue_book("LIB-000001", "9783161484100"))

    # Шаг 8: оплата штрафа
    print("\n[8] Иван оплачивает штраф:")
    print("  ", service.pay_fine(r1.id))

    # Шаг 9: снова выдаём
    print("\n[9] После оплаты снова выдаём книгу:")
    print("  ", service.issue_book("LIB-000001", "9783161484100"))

    # Шаг 10: список всех книг
    print("\n[10] Текущий фонд:")
    for b in db.list_books():
        print("  ", b)

    print("\nОжидаем напоминания...")
    notifier.wait()

    print("\n" + "=" * 60)
    print("ДЕМО ЗАВЕРШЕНО")
    print("=" * 60)


# Запуск
if __name__ == "__main__":
    demo()