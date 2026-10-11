"""Private-only, locked, atomic simulated ledger storage."""
import fcntl
import json
import os
import uuid
from pathlib import Path
from .paper import PaperBook

PROJECT=Path(__file__).resolve().parents[1]
PRIVATE=PROJECT/'private'


def private_path(path):
    path=Path(path).resolve()
    if not path.is_relative_to(PRIVATE.resolve()) or path==PRIVATE.resolve():
        raise ValueError('模拟账本/回测输出只允许放在项目private目录，禁止写入公开site')
    return path


def atomic_json(path,data):
    path=private_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as f:
            os.chmod(temp,0o600)
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def transact(path,operation):
    path=private_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        # Malformed ledgers are errors, never replaced with an empty account.
        book=PaperBook(json.loads(path.read_text()) if path.exists() else None)
        result=operation(book)
        book.validate();atomic_json(path,book.state)
        return result
