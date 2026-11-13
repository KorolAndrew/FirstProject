"""Этап 5 - долговременная память Янки (SQLite, стандартная библиотека).

Что храним: предпочтения, решения, факты о проектах, историю задач, проблемы, итоги исследований.
Что НЕ храним: ключи, пароли, токены, болтовню («привет», «спасибо»), дубли.
Всё можно найти, посмотреть, поправить и удалить (data/yanka_memory.db).
"""
import datetime
import re
import sqlite3
import threading
from pathlib import Path

KINDS = {
    "preference": "💜 предпочтение", "decision": "✅ решение", "project": "📁 проект", "task": "📋 задача",
    "problem": "🐞 проблема", "research": "🔎 исследование", "fact": "📌 факт",
}

SECRET_RX = re.compile(
    r"(sk-[a-z0-9_\-]{8,}|ntn_[a-z0-9]{10,}|ghp_[a-z0-9]{20,}|xox[bpa]-[a-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY|парол\w*\s*[:=]|password\s*[:=]|api[_ -]?key\s*[:=]|token\s*[:=]|"
    r"\b[A-Za-z0-9_\-]{32,}\b)", re.I)
SMALLTALK_RX = re.compile(r"^\s*(привет|пока|спасибо|спасибки|ок|окей|ага|да|нет|хорошо|ура|\.)[\s!.)💕❤️🐑]*$", re.I)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _words(text):
    return set(re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower().replace("ё", "е")))


def similar(a, b):
    # грубая основа слова (первые 5 букв) - чтобы «отвечать» и «отвечали» совпадали
    wa, wb = {w[:5] for w in _words(a)}, {w[:5] for w in _words(b)}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class Memory:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "yanka_memory.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mem(
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, project TEXT DEFAULT '',
                source TEXT DEFAULT '', created TEXT, updated TEXT, pinned INTEGER DEFAULT 0, uses INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS mem_project ON mem(project);
        """)
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(text, content='mem', content_rowid='id', "
                            "tokenize='unicode61 remove_diacritics 2')")
            self.db.executescript("""
                CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON mem BEGIN
                  INSERT INTO mem_fts(rowid, text) VALUES (new.id, new.text); END;
                CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON mem BEGIN
                  INSERT INTO mem_fts(mem_fts, rowid, text) VALUES('delete', old.id, old.text); END;
                CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE OF text ON mem BEGIN
                  INSERT INTO mem_fts(mem_fts, rowid, text) VALUES('delete', old.id, old.text);
                  INSERT INTO mem_fts(rowid, text) VALUES (new.id, new.text); END;
            """)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # SQLite без FTS5 - ищем через LIKE
        self.db.commit()

    # --- правила «что запоминать» ---
    @staticmethod
    def check(text, kind):
        """None - можно сохранить, иначе причина отказа."""
        t = (text or "").strip()
        if kind not in KINDS:
            return f"неизвестный вид памяти: {kind}"
        if len(t) < 8 or SMALLTALK_RX.match(t):
            return "слишком коротко или просто болтовня"
        if SECRET_RX.search(t):
            return "похоже на ключ или пароль - такое не запоминаю"
        if len(t) > 2000:
            return "слишком длинно - сохрани кратко"
        return None

    # --- CRUD ---
    def add(self, kind, text, project="", source="", pinned=False):
        """Возвращает (id, 'added'|'updated'|'skipped', причина)."""
        why = self.check(text, kind)
        if why:
            return None, "skipped", why
        text = re.sub(r"\s+", " ", text.strip())
        with self._lock:
            for r in self.db.execute("SELECT id, text FROM mem WHERE kind=? AND project=?", (kind, project)):
                if similar(r["text"], text) >= 0.65:
                    self.db.execute("UPDATE mem SET text=?, updated=?, source=? WHERE id=?",
                                    (text, now(), source, r["id"]))
                    self.db.commit()
                    return r["id"], "updated", "похожая запись обновлена"
            cur = self.db.execute("INSERT INTO mem(kind, text, project, source, created, updated, pinned) "
                                  "VALUES (?,?,?,?,?,?,?)", (kind, text, project, source, now(), now(), int(pinned)))
            self.db.commit()
            return cur.lastrowid, "added", ""

    def get(self, mid):
        r = self.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None

    def edit(self, mid, text=None, kind=None, pinned=None, project=None):
        cur = self.get(mid)
        if not cur:
            raise KeyError(mid)
        text = cur["text"] if text is None else text
        kind = cur["kind"] if kind is None else kind
        why = self.check(text, kind)
        if why:
            raise ValueError(why)
        with self._lock:
            self.db.execute("UPDATE mem SET text=?, kind=?, pinned=?, project=?, updated=? WHERE id=?",
                            (text.strip(), kind, int(cur["pinned"] if pinned is None else pinned),
                             cur["project"] if project is None else project, now(), mid))
            self.db.commit()
        return self.get(mid)

    def delete(self, mid):
        with self._lock:
            n = self.db.execute("DELETE FROM mem WHERE id=?", (mid,)).rowcount
            self.db.commit()
        return n > 0

    def list(self, kind=None, project=None, limit=200):
        q, a = "SELECT * FROM mem WHERE 1=1", []
        if kind:
            q += " AND kind=?"
            a.append(kind)
        if project is not None:
            q += " AND project IN (?, '')"
            a.append(project)
        q += " ORDER BY pinned DESC, updated DESC LIMIT ?"
        return [dict(r) for r in self.db.execute(q, a + [limit])]

    def search(self, query, project=None, kinds=None, limit=10):
        words = sorted(_words(query), key=len, reverse=True)[:8]
        if not words:
            return []
        rows = []
        if self.fts:
            # префиксный поиск - русские окончания («проекта», «проекту»)
            match = " OR ".join(f'"{w[:max(4, len(w) - 2)]}"*' for w in words)
            sql = ("SELECT mem.*, bm25(mem_fts) AS score FROM mem_fts JOIN mem ON mem.id = mem_fts.rowid "
                   "WHERE mem_fts MATCH ?")
            args = [match]
        else:
            sql = "SELECT mem.*, 0 AS score FROM mem WHERE (" + " OR ".join("lower(text) LIKE ?" for _ in words) + ")"
            args = [f"%{w[:max(4, len(w) - 2)]}%" for w in words]
        if project is not None:
            sql += " AND mem.project IN (?, '')"
            args.append(project)
        if kinds:
            sql += " AND mem.kind IN (" + ",".join("?" * len(kinds)) + ")"
            args += list(kinds)
        sql += " ORDER BY mem.pinned DESC, score LIMIT ?"
        rows = [dict(r) for r in self.db.execute(sql, args + [limit])]
        return rows

    def recall(self, text, project=None, max_chars=1200):
        """Кусочек памяти для подсказки модели: закреплённое + найденное по теме, не длиннее max_chars."""
        seen, out, size = set(), [], 0
        pinned = [r for r in self.list(project=project, limit=50) if r["pinned"]]
        for r in pinned + self.search(text, project=project, limit=12):
            if r["id"] in seen:
                continue
            line = f"- {KINDS[r['kind']]}: {r['text']}"
            if size + len(line) > max_chars:
                break
            seen.add(r["id"])
            out.append(line)
            size += len(line) + 1
        if seen:
            with self._lock:
                self.db.execute(f"UPDATE mem SET uses = uses + 1 WHERE id IN ({','.join('?' * len(seen))})", list(seen))
                self.db.commit()
        return "\n".join(out)

    # --- выжимка из разговора (через локальную модель) ---
    def extract(self, hub, messages, project="", model=None):
        """Просит модель на ПК выписать только важное (предпочтения, решения, проблемы). Возвращает добавленные id."""
        import json
        convo = "\n".join(f"{m['role']}: {str(m.get('content', ''))[:600]}" for m in messages[-12:])
        prompt = ("Выпиши из разговора только то, что стоит помнить долго: предпочтения Яны, принятые решения, "
                  "проблемы и их причины, итоги исследований. Не выписывай болтовню, ключи и пароли. "
                  "Ответь ТОЛЬКО JSON-массивом: [{\"kind\": \"preference|decision|problem|research|fact|task\", "
                  "\"text\": \"кратко, одним предложением\"}]. Если нечего - [].\n\n" + convo)
        r = hub.chat("lmstudio", [{"role": "user", "content": prompt}], model=model, max_tokens=400, temperature=0,
                     reason="память: выжимка разговора")
        m = re.search(r"\[.*\]", r["text"], re.S)
        added = []
        try:
            items = json.loads(m.group(0)) if m else []
        except ValueError:
            items = []
        for it in items[:8]:
            if isinstance(it, dict):
                mid, status, _ = self.add(str(it.get("kind", "fact")), str(it.get("text", "")), project, "auto")
                if status == "added":
                    added.append(mid)
        return added
