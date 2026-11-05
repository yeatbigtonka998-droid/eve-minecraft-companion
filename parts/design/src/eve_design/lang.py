"""The Eve building language: a tiny declarative command form, parsed by hand (CP-55).

    # comment
    cabin = Build(style="cozy_cabin", seed=42)
    cabin.walls(h=4, frame="stripped_spruce_log")
    cabin.roof("gable", ridge="east_west", overhang=1)

Grammar (one statement per line):
    program   := header { call }
    header    := NAME '=' 'Build' '(' args ')'
    call      := NAME '.' NAME '(' args ')'        -- NAME must be the header's name
    args      := [ arg { ',' arg } ]               -- positional args before keyword args
    arg       := literal | NAME '=' literal
    literal   := INT | STRING | 'True' | 'False' | 'true' | 'false'

There are no expressions, variables, loops, attribute chains, imports or calls other than the above, so a
program cannot reach Python, the host, files or the network; it can only name commands that the compiler's
fixed table defines. Size limits bound parsing; the compiler bounds time, writes and dimensions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_SOURCE_BYTES = 64 * 1024
MAX_LINE_CHARS = 400
MAX_STATEMENTS = 200
MAX_ARGS = 16
MAX_INT_DIGITS = 5
MAX_STRING = 64

_TOKEN = re.compile(r'[ \t]*(?:(?P<INT>-?[0-9]+)|(?P<STR>"[^"\\\n]*")|(?P<NAME>[A-Za-z_][A-Za-z0-9_]*)|(?P<OP>[=().,]))')
_BOOLS = {"True": True, "False": False, "true": True, "false": False}


class LangError(ValueError):
    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"line {line}, col {col}: {msg}")
        self.line, self.col, self.msg = line, col, msg


@dataclass
class Call:
    name: str
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    line: int = 0


@dataclass
class Program:
    var: str
    header: Call
    calls: list[Call]


def _tokens(text: str, ln: int) -> list[tuple[str, object, int]]:
    out, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise LangError(ln, pos + 1, f"unexpected character {text[pos:].lstrip()[:1]!r}")
        kind = m.lastgroup
        raw = m.group(kind)
        col = m.start(kind) + 1
        if kind == "INT":
            if len(raw.lstrip("-")) > MAX_INT_DIGITS:
                raise LangError(ln, col, "integer literal too long")
            val = int(raw)
        elif kind == "STR":
            if len(raw) - 2 > MAX_STRING:
                raise LangError(ln, col, f"string longer than {MAX_STRING} characters")
            val = raw[1:-1]
        else:
            val = raw
        out.append((kind, val, col))
        pos = m.end()
    return out


class _Line:
    def __init__(self, toks, ln):
        self.t, self.i, self.ln = toks, 0, ln

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("EOL", None, (self.t[-1][2] + 1 if self.t else 1))

    def take(self, kind, val=None, what=None):
        k, v, c = self.peek()
        if k != kind or (val is not None and v != val):
            raise LangError(self.ln, c, f"expected {what or val or kind}, got {v if v is not None else 'end of line'!r}")
        self.i += 1
        return v

    def literal(self):
        k, v, c = self.peek()
        if k in ("INT", "STR"):
            self.i += 1
            return v
        if k == "NAME" and v in _BOOLS:
            self.i += 1
            return _BOOLS[v]
        raise LangError(self.ln, c, f"expected a number, string or True/False, got {v if v is not None else 'end of line'!r}")

    def args(self) -> tuple[list, dict]:
        args, kwargs = [], {}
        self.take("OP", "(")
        if self.peek()[:2] == ("OP", ")"):
            self.i += 1
            return args, kwargs
        while True:
            k, v, c = self.peek()
            nk = self.t[self.i + 1] if self.i + 1 < len(self.t) else None
            if k == "NAME" and v not in _BOOLS and nk and nk[:2] == ("OP", "="):
                self.i += 2
                if v in kwargs:
                    raise LangError(self.ln, c, f"argument {v!r} given twice")
                kwargs[v] = self.literal()
            else:
                if kwargs:
                    raise LangError(self.ln, c, "positional argument after keyword argument")
                args.append(self.literal())
            if len(args) + len(kwargs) > MAX_ARGS:
                raise LangError(self.ln, c, f"more than {MAX_ARGS} arguments")
            k, v, c = self.peek()
            if (k, v) == ("OP", ")"):
                self.i += 1
                return args, kwargs
            self.take("OP", ",", "',' or ')'")

    def end(self):
        k, v, c = self.peek()
        if k != "EOL":
            raise LangError(self.ln, c, f"unexpected {v!r} after statement")


def parse(source: str | bytes) -> Program:
    if isinstance(source, bytes):
        if len(source) > MAX_SOURCE_BYTES:
            raise LangError(1, 1, f"program larger than {MAX_SOURCE_BYTES} bytes")
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError:
            raise LangError(1, 1, "program is not UTF-8 text") from None
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise LangError(1, 1, f"program larger than {MAX_SOURCE_BYTES} bytes")
    var, header, calls = None, None, []
    for ln, raw in enumerate(source.splitlines(), 1):
        if len(raw) > MAX_LINE_CHARS:
            raise LangError(ln, 1, f"line longer than {MAX_LINE_CHARS} characters")
        text = _strip_comment(raw)
        if not text.strip():
            continue
        if not text.isascii():
            raise LangError(ln, 1, "only ASCII characters are allowed")
        p = _Line(_tokens(text, ln), ln)
        name = p.take("NAME", what="a name")
        if header is None:
            p.take("OP", "=", "'=' (a program starts with: name = Build(...))")
            p.take("NAME", "Build", "Build")
            a, kw = p.args()
            p.end()
            var, header = name, Call("Build", a, kw, ln)
            continue
        if name != var:
            raise LangError(ln, 1, f"unknown name {name!r} (only {var!r} exists)")
        p.take("OP", ".", "'.'")
        cmd = p.take("NAME", what="a command name")
        a, kw = p.args()
        p.end()
        calls.append(Call(cmd, a, kw, ln))
        if len(calls) > MAX_STATEMENTS:
            raise LangError(ln, 1, f"more than {MAX_STATEMENTS} statements")
    if header is None:
        raise LangError(1, 1, "empty program (expected: name = Build(...))")
    return Program(var, header, calls)


def _strip_comment(raw: str) -> str:
    inside = False
    for i, ch in enumerate(raw):
        if ch == '"':
            inside = not inside
        elif ch == "#" and not inside:
            return raw[:i]
    return raw
