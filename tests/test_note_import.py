"""Note import: Markdown / HTML → a log-ready title + Markdown body.

No database. The converter is the thing that would drift from the editor if it
lived only in a use-case test that also asserts persistence.
"""

from __future__ import annotations

import pytest

from relay.domain.note_import import (
    MAX_BYTES,
    TITLE_MAX,
    NoteFileTooLarge,
    UnsupportedNoteFile,
    parse_note,
)


def test_markdown_uses_the_first_heading_as_title():
    note = parse_note("ignored.md", "# 网关限流\n\n当 QPS 超过 1000 时返回 429。".encode())
    assert note.title == "网关限流"
    assert "当 QPS 超过 1000" in note.body
    assert not note.body.startswith("#")
    assert note.source == "markdown"


def test_markdown_frontmatter_title_wins_over_the_heading():
    source = "---\ntitle: 限流手册\n---\n# Other\n\n正文\n"
    note = parse_note("doc.md", source.encode())
    assert note.title == "限流手册"
    assert "# Other" in note.body


def test_markdown_falls_back_to_the_filename():
    note = parse_note("rate-limit.md", b"just a paragraph, no heading")
    assert note.title == "rate-limit"
    assert note.body == "just a paragraph, no heading"


def test_uppercase_and_windows_paths_still_count_as_markdown():
    note = parse_note(r"C:\exports\Runbook.MD", b"no heading here")
    assert note.title == "Runbook"
    assert note.source == "markdown"


def test_html_converts_to_markdown_and_drops_a_duplicate_h1():
    html = """
    <html><head><title>网关限流</title></head>
    <body>
      <h1>网关限流</h1>
      <p>当 QPS 超过 <strong>1000</strong> 时返回 429。</p>
      <ul>
        <li>检查 <code>rate_limit</code></li>
        <li>看 <a href="https://example.com/docs">文档</a></li>
      </ul>
      <pre><code class="language-bash">curl -i /v1/chat</code></pre>
    </body></html>
    """
    note = parse_note("runbook.html", html.encode())
    assert note.title == "网关限流"
    assert note.source == "html"
    assert "**1000**" in note.body
    assert "- 检查 `rate_limit`" in note.body
    assert "[文档](https://example.com/docs)" in note.body
    assert "```bash" in note.body
    assert "curl -i /v1/chat" in note.body
    # Title already lives on the log; repeating the H1 would show it twice.
    assert not note.body.lstrip().startswith("# 网关限流")


def test_html_strips_script_and_javascript_hrefs():
    html = """
    <body>
      <p>安全段</p>
      <script>document.cookie</script>
      <p><a href="javascript:alert(1)">点我</a></p>
      <img src="data:text/html,xss" alt="坏图">
    </body>
    """
    note = parse_note("page.html", html.encode())
    assert "document.cookie" not in note.body
    assert "javascript:" not in note.body
    assert "data:" not in note.body
    assert "安全段" in note.body
    assert "点我" in note.body
    assert "坏图" in note.body


def test_html_tables_and_nested_lists():
    html = """
    <h1>对照</h1>
    <table>
      <tr><th>状态</th><th>含义</th></tr>
      <tr><td>429</td><td>限流</td></tr>
    </table>
    <ul>
      <li>外层
        <ul><li>内层</li></ul>
      </li>
    </ul>
    """
    note = parse_note("t.html", html.encode())
    assert note.title == "对照"
    assert "| 状态 | 含义 |" in note.body
    assert "| --- | --- |" in note.body
    assert "| 429 | 限流 |" in note.body
    assert "- 外层" in note.body
    assert "  - 内层" in note.body


def test_gb18030_html_decodes():
    html = "<html><head><title>限流</title></head><body><p>中文</p></body></html>"
    note = parse_note("gbk.htm", html.encode("gb18030"))
    assert note.title == "限流"
    assert "中文" in note.body


def test_an_unsupported_extension_is_refused():
    with pytest.raises(UnsupportedNoteFile):
        parse_note("notes.pdf", b"%PDF")


def test_an_oversize_file_is_refused():
    with pytest.raises(NoteFileTooLarge):
        parse_note("big.md", b"x" * (MAX_BYTES + 1))


def test_a_long_title_is_clipped():
    heading = "标" * (TITLE_MAX + 20)
    note = parse_note("x.md", f"# {heading}\n\nbody".encode())
    assert len(note.title) <= TITLE_MAX
    assert note.title.endswith("…")
