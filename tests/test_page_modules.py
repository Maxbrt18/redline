"""The notebook and strength modules, checked without a browser.

The code is written as ES modules and the service assembles them into a single script
(`redline.service.page.bundle`). In that script everything shares one scope: a name missing
from an `import` would still work, and the import list would stop saying what a file depends
on. These tests hold the module contract:

- everything a module takes from another is imported, and every import is used;
- a module never reassigns what it imports (it goes through a function of the owning
  module, like `setTimer`), otherwise the code would stop working when loaded by a
  browser as real modules;
- an exported declaration declares a single name, so that the assembly sees it;
- no file is missing from the assembly lists.

The analysis is lexical (a minimal JS tokenizer): it knows strings, templates, comments and
regular expressions, not scope. A local variable named like an export of another module
therefore reads as a borrow: rename the local variable.
"""

from __future__ import annotations

import posixpath
import re

import pytest

from redline.service import page

IDENT = re.compile(r"[A-Za-z_$][\w$]*")
NUMBER = re.compile(r"\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?|\.\d+")
PUNCT = re.compile(r"\?\.|\?\?=?|\.\.\.|===|!==|\*\*=?|=>|<<=|>>>?=?|[-+*/%&|^<>!=]=|&&=?|\|\|=?|\+\+|--|<<|>>"
                   r"|[{}()\[\];,.<>+\-*/%&|^!~?:=@#]")
# After these tokens, a slash opens a regular expression, not a division.
REGEX_BEFORE = {"(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">",
                "~", "^", "=>", "&&", "||", "??", "==", "===", "!=", "!==", "+=", "-=",
                "return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw"}
ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&&=", "||=", "??=", "++", "--"}


def tokens(src: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    _scan(src, 0, out, inside=False)
    return out


def _scan(src: str, pos: int, out: list, inside: bool) -> int:
    """Up to the end, or up to the } closing a ${...} when `inside`."""
    depth, end = 0, len(src)
    while pos < end:
        char = src[pos]
        if char in " \t\r\n":
            pos += 1
        elif src.startswith("//", pos):
            newline = src.find("\n", pos)
            pos = end if newline < 0 else newline
        elif src.startswith("/*", pos):
            pos = src.index("*/", pos) + 2
        elif char in "'\"":
            stop = pos + 1
            while src[stop] != char:
                stop += 2 if src[stop] == "\\" else 1
            out.append(("str", src[pos:stop + 1], pos))
            pos = stop + 1
        elif char == "`":
            out.append(("tmpl", "`", pos))
            stop = pos + 1
            while src[stop] != "`":
                if src[stop] == "\\":
                    stop += 2
                elif src.startswith("${", stop):
                    stop = _scan(src, stop + 2, out, inside=True)
                else:
                    stop += 1
            pos = stop + 1
        elif char == "}" and inside and depth == 0:
            return pos + 1
        elif match := IDENT.match(src, pos):
            out.append(("id", match.group(), pos))
            pos = match.end()
        elif match := NUMBER.match(src, pos):
            out.append(("num", match.group(), pos))
            pos = match.end()
        elif char == "/" and (not out or (out[-1][0] in ("punct", "id") and out[-1][1] in REGEX_BEFORE)):
            stop, in_class = pos + 1, False
            while src[stop] != "/" or in_class:
                if src[stop] == "\\":
                    stop += 1
                elif src[stop] in "[]":
                    in_class = src[stop] == "["
                stop += 1
            pos = stop + 1
            while pos < end and src[pos].isalpha():
                pos += 1
            out.append(("regex", "", pos))
        else:
            match = PUNCT.match(src, pos)
            assert match, f"unexpected character {char!r} at {pos}"
            value = match.group()
            depth += (value == "{") - (value == "}")
            out.append(("punct", value, pos))
            pos = match.end()
    return pos


def references(toks):
    """Identifiers used as variables: neither a property nor an object key."""
    for index, (kind, value, _) in enumerate(toks):
        if kind != "id":
            continue
        before = toks[index - 1][1] if index else None
        after = toks[index + 1][1] if index + 1 < len(toks) else None
        if before in (".", "?.") or (after == ":" and before in ("{", ",")):
            continue
        yield value, index


def declared_at_top(toks) -> list[str]:
    names, depth, index = [], 0, 0
    while index < len(toks):
        kind, value, _ = toks[index]
        if kind == "punct" and value in "([{":
            depth += 1
        elif kind == "punct" and value in ")]}":
            depth -= 1
        elif depth == 0 and value in ("function", "class") and kind == "id":
            names.append(toks[index + 1 + (toks[index + 1][1] == "*")][1])
        elif depth == 0 and value in ("const", "let") and kind == "id":
            names.append(toks[index + 1][1])
            inner, cursor = 0, index + 2
            while cursor < len(toks) and not (inner == 0 and toks[cursor][1] == ";"):
                if toks[cursor][1] in "([{" and toks[cursor][0] == "punct":
                    inner += 1
                elif toks[cursor][1] in ")]}" and toks[cursor][0] == "punct":
                    inner -= 1
                elif inner == 0 and toks[cursor][1] == "," and toks[cursor + 2][1] in ("=", ";", ","):
                    names.append(toks[cursor + 1][1])
                cursor += 1
        index += 1
    return names


IMPORT = re.compile(r"^import \{([^}]*)\} from '([^']+)';\n", re.MULTILINE)
BUNDLES = {"notebook": page.NOTEBOOK_SCRIPT}


def modules(names):
    """{module: (source without imports, tokens, names declared at level 0, imports)}."""
    found = {}
    for name in names:
        source = (page.PAGE_DIR / name).read_text(encoding="utf-8")
        imported = {}
        for listed, target in IMPORT.findall(source):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            for item in listed.split(","):
                imported[item.strip()] = resolved
        body = IMPORT.sub("", source)
        toks = tokens(body)
        found[name] = (body, toks, declared_at_top(toks), imported)
    return found


@pytest.mark.parametrize("bundle", BUNDLES)
def test_each_page_assembles_into_one_script(bundle):
    script = page.bundle(BUNDLES[bundle])
    assert script.startswith("(() => {\n'use strict';") and "\nimport " not in script


@pytest.mark.parametrize("bundle", BUNDLES)
def test_a_module_imports_exactly_what_it_borrows(bundle):
    found = modules(BUNDLES[bundle])
    owner = {declared: name for name, (_, _, names, _) in found.items() for declared in names}
    problems = []
    for name, (_, toks, names, imported) in found.items():
        used = {value for value, _ in references(toks)}
        for borrowed in sorted(used - set(names)):
            if borrowed in owner and owner[borrowed] != name and imported.get(borrowed) != owner[borrowed]:
                problems.append(f"{name} uses {borrowed} without importing it from {owner[borrowed]}")
        problems += [f"{name} imports {item} without using it" for item in imported if item not in used]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("bundle", BUNDLES)
def test_a_module_never_reassigns_an_import(bundle):
    problems = []
    for name, (_, toks, _, imported) in modules(BUNDLES[bundle]).items():
        for value, index in references(toks):
            after = toks[index + 1][1] if index + 1 < len(toks) else None
            before = toks[index - 1][1] if index else None
            if value in imported and (after in ASSIGN or before in ("++", "--")):
                problems.append(f"{name} reassigns {value} (imported from {imported[value]})")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("bundle", BUNDLES)
def test_an_exported_declaration_declares_one_name(bundle):
    problems = []
    for name in BUNDLES[bundle]:
        for line in (page.PAGE_DIR / name).read_text(encoding="utf-8").splitlines():
            if re.match(r"export (const|let) ", line) and len(declared_at_top(tokens(line + ";"))) > 1:
                problems.append(f"{name} : {line[:80]}")
    assert not problems, "\n".join(problems)


def test_every_module_and_stylesheet_is_assembled():
    listed = {*page.NOTEBOOK_SCRIPT, *page.NOTEBOOK_STYLE}
    on_disk = {str(path.relative_to(page.PAGE_DIR)) for folder in ("shared", "notebook", "strength")
               for pattern in ("*.js", "*.css") for path in (page.PAGE_DIR / folder).rglob(pattern)}
    on_disk.discard("notebook/sw.js")  # the service worker, served as is by its route
    assert on_disk == listed


def test_a_broken_import_stops_the_service_instead_of_blanking_the_page(tmp_path, monkeypatch):
    (tmp_path / "a.js").write_text("import {missing} from './b.js';\nmissing();\n")
    (tmp_path / "b.js").write_text("export function present() {}\n")
    monkeypatch.setattr(page, "PAGE_DIR", tmp_path)
    page.bundle.cache_clear()
    with pytest.raises(ValueError, match="missing"):
        page.bundle(("b.js", "a.js"))
    page.bundle.cache_clear()
