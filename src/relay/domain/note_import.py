"""Turn an uploaded Markdown or HTML file into a log title + Markdown body.

Imported notes are ordinary logs: the knowledge page's 浏览 / 编辑 lenses already
work on those, so the job here is to *arrive* as Markdown rather than invent a
third format. Storing HTML would force the renderer to pass raw markup through,
which LOG-2 forbids (`html: false`) because a colleague-authored log is a stored
XSS vector with a human delivery mechanism.

Conversion lives in the domain so the use case and the tests share one parser —
two copies of "what is a heading" is how an import and a preview disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

#: Big enough for a long runbook; small enough that a mis-selected video does
#: not become a 25 MiB text column. Attachments have their own cap.
MAX_BYTES = 2 * 1024 * 1024

#: Matches ``log.title``'s ``String(500)``. Truncating here keeps a long ``<title>``
#: from becoming a flush-time IntegrityError the author cannot act on.
TITLE_MAX = 500

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
_HTML_SUFFIXES = {".html", ".htm"}
SUPPORTED_SUFFIXES = _MARKDOWN_SUFFIXES | _HTML_SUFFIXES

_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
_FRONTMATTER_TITLE = re.compile(r"^title:[ \t]*(.+)$", re.MULTILINE | re.IGNORECASE)
_ATX = re.compile(r"\A[ \t]{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?(?:\n+|$)")
_SETEXT = re.compile(r"\A[ \t]{0,3}(.+?)\n[ \t]{0,3}(=+|-+)[ \t]*(?:\n+|$)")

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "template"})
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
#: Anything not in this set is treated as a transparent wrapper (its children
#: render, the tag itself does not). That is how ``<section>`` / ``<article>``
#: / unknown tags still contribute their text.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "hr",
        "figure",
        "figcaption",
        "header",
        "footer",
        "main",
        "section",
        "article",
        "nav",
        "aside",
        "dt",
        "dd",
        "dl",
        "html",
        "body",
    }
)


class UnsupportedNoteFile(ValueError):
    """Wrong extension. The message names what *is* accepted."""


class NoteFileTooLarge(ValueError):
    """Over :data:`MAX_BYTES`. Distinct so the HTTP mapping can be 413, not 422."""


@dataclass(frozen=True, slots=True)
class ImportedNote:
    title: str
    body: str
    #: ``markdown`` or ``html`` — audit / UI copy, not a third ``LogFormat``.
    source: str


def parse_note(filename: str, data: bytes) -> ImportedNote:
    """Decode, sniff the extension, and return a log-ready title + Markdown body.

    The filename is part of the contract: a ``.md`` that happens to contain HTML
    is still Markdown (an author exporting from a wiki should not have their
    fences rewritten), and a ``.html`` of Markdown-looking text still goes
    through the HTML converter so scripts never survive as markup.
    """
    if len(data) > MAX_BYTES:
        raise NoteFileTooLarge(f"文件不能超过 {MAX_BYTES // (1024 * 1024)} MB。")

    suffix = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedNoteFile("只支持 Markdown（.md）和 HTML（.html）文件。")

    text = decode_bytes(data)
    fallback = _title_from_filename(filename)
    if suffix in _HTML_SUFFIXES:
        title, body = _from_html(text, fallback)
        return ImportedNote(title=_clip_title(title), body=body.strip(), source="html")
    title, body = _from_markdown(text, fallback)
    return ImportedNote(title=_clip_title(title), body=body.strip(), source="markdown")


def decode_bytes(data: bytes) -> str:
    """UTF-8 first, then GB18030 — a Windows export from a Chinese editor is
    the case UTF-8-only would turn into mojibake the author then "fixes" by
    deleting the note.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace")


def _title_from_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = Path(name).stem.strip()
    return stem or "未命名"


def _clip_title(title: str) -> str:
    clean = re.sub(r"\s+", " ", title).strip() or "未命名"
    if len(clean) <= TITLE_MAX:
        return clean
    return clean[: TITLE_MAX - 1].rstrip() + "…"


def _from_markdown(text: str, fallback: str) -> tuple[str, str]:
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    title: str | None = None

    matter = _FRONTMATTER.match(body)
    if matter:
        field = _FRONTMATTER_TITLE.search(matter.group(1))
        if field:
            title = field.group(1).strip().strip("\"'")
        body = body[matter.end() :]

    if title is None:
        atx = _ATX.match(body)
        if atx:
            title = atx.group(1).strip()
            body = body[atx.end() :]
        else:
            setext = _SETEXT.match(body)
            if setext:
                title = setext.group(1).strip()
                body = body[setext.end() :]

    return title or fallback, body


# ------------------------------------------------------------------ HTML → MD


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    """Build a tiny element tree, dropping script/style rather than converting
    them. ``convert_charrefs`` is on so ``&nbsp;`` is a character, not a token
    the renderer would have to know about.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack = [self.root]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip:
            if tag not in _VOID_TAGS:
                self._skip += 1
            return
        if tag in _SKIP_TAGS:
            self._skip = 1
            return
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip:
            # Nested skipped tags were counted on the way in; count them out
            # so an inner </script> does not reopen the outer script's body.
            self._skip = max(0, self._skip - 1)
            return
        if tag in _VOID_TAGS:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self._stack[-1].children.append(data)


def _from_html(text: str, fallback: str) -> tuple[str, str]:
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    title = _html_title(builder.root) or fallback
    body_root = _find_tag(builder.root, "body") or builder.root
    markdown = _render_blocks(body_root.children).strip()
    # The page title and the first H1 are usually the same string. Leaving both
    # would show the heading twice: once in the log title, once in the body.
    markdown = _drop_leading_heading(markdown, title)
    return title, markdown


def _html_title(root: _Node) -> str | None:
    node = _find_tag(root, "title")
    if node is not None:
        text = _plain(node).strip()
        if text:
            return text
    heading = _find_tag(root, "h1")
    if heading is not None:
        text = _plain(heading).strip()
        if text:
            return text
    return None


def _find_tag(node: _Node, tag: str) -> _Node | None:
    if node.tag == tag:
        return node
    for child in node.children:
        if isinstance(child, _Node):
            found = _find_tag(child, tag)
            if found is not None:
                return found
    return None


def _drop_leading_heading(markdown: str, title: str) -> str:
    match = _ATX.match(markdown)
    if match and match.group(1).strip() == title:
        return markdown[match.end() :]
    match = _SETEXT.match(markdown)
    if match and match.group(1).strip() == title:
        return markdown[match.end() :]
    return markdown


def _plain(node: _Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            parts.append(_plain(child))
    return "".join(parts)


def _render_blocks(nodes: list[_Node | str], *, list_depth: int = 0) -> str:
    chunks: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            text = _collapse_ws(node)
            if text:
                chunks.append(text)
            continue
        rendered = _render_block(node, list_depth=list_depth)
        if rendered:
            chunks.append(rendered)
    return _join_blocks(chunks)


def _render_block(node: _Node, *, list_depth: int) -> str:
    tag = node.tag
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag[1])
        text = _render_inline(node.children).strip()
        return f"{'#' * level} {text}" if text else ""
    if tag == "p":
        return _render_inline(node.children).strip()
    if tag == "hr":
        return "---"
    if tag == "blockquote":
        inner = _render_blocks(node.children, list_depth=list_depth)
        if not inner:
            return ""
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    if tag == "pre":
        return _render_pre(node)
    if tag in {"ul", "ol"}:
        return _render_list(node, list_depth=list_depth)
    if tag == "li":
        # A stray <li> outside a list still has to produce something readable.
        return _render_list_item(node, ordered=False, index=1, list_depth=list_depth)
    if tag == "table":
        return _render_table(node)
    if tag in {"thead", "tbody", "tfoot"}:
        return _render_blocks(node.children, list_depth=list_depth)
    if tag == "tr":
        cells = [
            _render_inline(child.children).strip()
            for child in node.children
            if isinstance(child, _Node)
        ]
        return "| " + " | ".join(cells) + " |"
    if tag in {"th", "td"}:
        return _render_inline(node.children).strip()
    if tag == "figcaption":
        text = _render_inline(node.children).strip()
        return f"*{text}*" if text else ""
    if tag == "br":
        return ""
    if tag in {"head", "title"}:
        # Title is a field on the log, not a paragraph in the body.
        return ""
    if tag in _BLOCK_TAGS:
        return _render_blocks(node.children, list_depth=list_depth)
    # Inline tags that landed at block level (a bare <img>, a <span> wrapping
    # a sentence) still have to emit, not vanish.
    return _render_inline([node]).strip()


def _render_pre(node: _Node) -> str:
    language = ""
    code = node
    for child in node.children:
        if isinstance(child, _Node) and child.tag == "code":
            code = child
            language = _fence_language(child)
            break
    text = _raw_text(code).strip("\n")
    return f"```{language}\n{text}\n```"


def _fence_language(node: _Node) -> str:
    css = f"{node.attrs.get('class', '')} {node.attrs.get('className', '')}"
    for token in css.split():
        if token.startswith("language-"):
            return token.removeprefix("language-")
        if token.startswith("lang-"):
            return token.removeprefix("lang-")
    return ""


def _raw_text(node: _Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            parts.append(_raw_text(child))
    return "".join(parts)


def _render_list(node: _Node, *, list_depth: int) -> str:
    ordered = node.tag == "ol"
    items = [child for child in node.children if isinstance(child, _Node) and child.tag == "li"]
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(_render_list_item(item, ordered=ordered, index=index, list_depth=list_depth))
    return "\n".join(line for line in lines if line)


def _render_list_item(node: _Node, *, ordered: bool, index: int, list_depth: int) -> str:
    marker = f"{index}." if ordered else "-"
    indent = "  " * list_depth
    inline: list[_Node | str] = []
    nested: list[str] = []
    for child in node.children:
        if isinstance(child, _Node) and child.tag in {"ul", "ol"}:
            nested.append(_render_list(child, list_depth=list_depth + 1))
        elif isinstance(child, _Node) and child.tag in _BLOCK_TAGS and child.tag not in {"li"}:
            nested.append(_render_blocks([child], list_depth=list_depth + 1))
        else:
            inline.append(child)
    head = _render_inline(inline).strip()
    line = f"{indent}{marker} {head}".rstrip()
    if nested:
        extra = "\n".join(part for part in nested if part)
        return f"{line}\n{extra}" if extra else line
    return line


def _render_table(node: _Node) -> str:
    rows: list[list[str]] = []

    def walk(current: _Node) -> None:
        if current.tag == "tr":
            cells: list[str] = []
            for child in current.children:
                if isinstance(child, _Node) and child.tag in {"th", "td"}:
                    cells.append(_render_inline(child.children).strip().replace("|", r"\|"))
            if cells:
                rows.append(cells)
            return
        for child in current.children:
            if isinstance(child, _Node):
                walk(child)

    walk(node)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |"]
    # A header rule is what makes this a table rather than a list of pipes.
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_inline(nodes: list[_Node | str]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(_collapse_ws(node))
            continue
        tag = node.tag
        if tag == "br":
            parts.append("\n")
        elif tag in {"strong", "b"}:
            inner = _render_inline(node.children).strip()
            parts.append(f"**{inner}**" if inner else "")
        elif tag in {"em", "i"}:
            inner = _render_inline(node.children).strip()
            parts.append(f"*{inner}*" if inner else "")
        elif tag in {"s", "del"}:
            inner = _render_inline(node.children).strip()
            parts.append(f"~~{inner}~~" if inner else "")
        elif tag == "code":
            inner = _raw_text(node).strip()
            parts.append(f"`{inner}`" if inner else "")
        elif tag == "a":
            parts.append(_render_link(node))
        elif tag == "img":
            parts.append(_render_image(node))
        elif tag in _BLOCK_TAGS:
            # A block that landed inside a paragraph (invalid HTML) still has
            # to contribute its text rather than vanish.
            block = _render_block(node, list_depth=0)
            if block:
                parts.append("\n" + block + "\n")
        else:
            parts.append(_render_inline(node.children))
    return "".join(parts)


def _render_link(node: _Node) -> str:
    href = node.attrs.get("href", "").strip()
    text = _render_inline(node.children).strip() or href
    if not href or href.lower().startswith(("javascript:", "vbscript:", "data:")):
        return text
    return f"[{text}]({href})"


def _render_image(node: _Node) -> str:
    src = node.attrs.get("src", "").strip()
    alt = node.attrs.get("alt", "").strip()
    if not src or src.lower().startswith(("javascript:", "vbscript:", "data:")):
        return alt
    return f"![{alt}]({src})"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " "))


def _join_blocks(chunks: list[str]) -> str:
    joined = "\n\n".join(chunk.strip("\n") for chunk in chunks if chunk.strip())
    return re.sub(r"\n{3,}", "\n\n", joined)
