#!/usr/bin/env python3
r"""
Transform a posted-by.log (ag/grep -n output over "### Post by USER on DATE"
headings) into a simple, git-friendly lookup structure: one small Markdown
file per username, each line a clickable link to that user's post — meant
to be browsed straight on GitHub, where it renders as a bullet list.

Input line shape (same as the HTML-generating script):

    Ubuntu_Gamers_Arena/thread_452699_&quot;Defcon&quot;_for_linux.md:62:### Post by Feba on 2007-06-08

Output: one Markdown list item appended per matching post, to a
sharded-by-first-letter path so the whole tree stays git/GitHub friendly
(no single directory with tens of thousands of files):

    <out>/<shard>/<username>.md

where <shard> is the username's first character, lowercased (non-letters
bucket into "_"), e.g.:

    by-user/f/Feba.md

Each line in that file looks like — the user's own thread-starting post
(post #1) as a linked top-level bullet, their followups nested under it as
"comment N" links:

    - 2007-10-27 [How do I get my PHP working with Apache2?](https://ubuntuforums.popey.com/showthread.php?t=593321#post-1) — General Help <!--t:593321-->
      - 2007-10-27 [comment 2](https://ubuntuforums.popey.com/showthread.php?t=593321#post-2) <!--t:593321-->
      - 2007-10-27 [comment 3](https://ubuntuforums.popey.com/showthread.php?t=593321#post-3) <!--t:593321-->

If the user only ever commented in a thread (no post #1 of their own to
anchor on), an unlinked placeholder line is synthesized as the top-level
entry instead, using the same title/forum, so the followups still have
something to nest under:

    - 2007-10-27 How do I get my PHP working with Apache2? — General Help <!--t:593321-->
      - 2007-10-27 [comment 2](https://ubuntuforums.popey.com/showthread.php?t=593321#post-2) <!--t:593321-->

The trailing `<!--t:THREAD_ID-->` on every line is an HTML comment —
invisible when rendered on GitHub — that lets the "does a top-level entry
already exist for this thread" check work by reading only the last line
already written (a `tail -n 1`-style read, done fresh before each write;
no in-memory state kept across lines). Without it, a synthesized
placeholder line (which has no post URL to extract a thread id from)
couldn't be recognized as already covering that thread.

Because that check only looks at the very last line, it only catches
CONSECUTIVE followups in the same thread — if a user's posts in thread A
are interleaved with a post in thread B in between, the second thread-A
followup won't see thread A as the last line and will synthesize another
placeholder rather than nesting under the first.

Rule of thumb: post #1 -> unindented, linked. Post >1 -> indented 2
spaces, always; preceded by a synthesized unindented placeholder if no
top-level entry for that thread exists yet.

Title and forum/category: by default these are guessed from the thread's
path alone (directory name -> forum, filename -> title; both HTML-entity
decoded) — no archive access needed. Pass --archive-path pointing at the
exported .md archive to instead pull the authoritative `title:` and
`forum:` fields from each thread's frontmatter, e.g.:

    ---
    title: "HELP! I deleted /var/* !!!"
    date: 2007-10-27
    forum: General Help
    ---

Each referenced file is opened at most once regardless of how many posts
in it are being recorded, and cached. Falls back to the path-derived guess
for any file missing a given frontmatter field, or if --archive-path isn't
given at all.

Usage:

    python3 user_index.py --full-log posted-by.log

    # pull titles/forums from frontmatter, and restrict to one username
    python3 user_index.py --full-log posted-by.log \
        --archive-path /path/to/export --user Feba

    # re-running for the same user without duplicating entries: --clean
    # deletes that user's existing .md file first (requires --user, since
    # it needs to know which specific file to remove)
    python3 user_index.py --full-log posted-by.log --user Feba --clean

    # batch runs over many usernames: --skip-existing exits immediately
    # (without reading the log) if that user already has a file, so a
    # re-run of the batch only processes users that don't have one yet
    for u in Feba erlguta Kirboosy; do
        python3 user_index.py --full-log posted-by.log --user "$u" --skip-existing
    done

Post numbering: same approach as the HTML-generating script — a single
"current thread" counter, reset to 0 whenever the file named on this log
line differs from the last one. Correct as long as ag/grep keep each
file's matches adjacent in the log (the normal case); a file reappearing
non-contiguously prints a warning to stderr rather than silently
mis-numbering.

Deliberately simple I/O: each line is a plain open-append-close (no
persistent file-handle pool) — same tradeoff discussed for the HTML
version. Fine for a one-off transform; if you're doing this repeatedly
over the full multi-hundred-MB log, a pooled version would be faster.
"""

import argparse
import html
import re
import sys
from pathlib import Path

LOG_LINE_RE = re.compile(r"^(?P<file>.+):(?P<line>\d+):(?P<content>.*)$")
POST_RE = re.compile(r"^###\s*Post by\s+(?P<user>.+)\s+on\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")
THREAD_ID_RE = re.compile(r"thread_(\d+)_")


def thread_id_for_name(name: str):
    m = THREAD_ID_RE.search(name)
    return m.group(1) if m else None


def title_from_filename(rel_file: str) -> str:
    stem = Path(rel_file).stem
    title_raw = re.sub(r"^thread_\d+_", "", stem)
    title_raw = title_raw.replace("_", " ").strip()
    return html.unescape(title_raw)


def category_from_path(rel_file: str) -> str:
    """The forum/category a thread belongs to, guessed from its directory component."""
    parent = Path(rel_file).parent
    return html.unescape(str(parent).replace("_", " ").strip()) if str(parent) != "." else ""


FRONTMATTER_FIELD_RE = re.compile(r'^(?P<key>title|forum):\s*"?(?P<value>.*?)"?\s*$')


def frontmatter_fields(path: Path) -> dict:
    """Read `title:` and `forum:` out of a thread file's frontmatter, if present."""
    fields = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            in_frontmatter = False
            for i, line in enumerate(f):
                line = line.rstrip("\n")
                if i == 0 and line.strip() == "---":
                    in_frontmatter = True
                    continue
                if not in_frontmatter:
                    break
                if line.strip() == "---":
                    break
                m = FRONTMATTER_FIELD_RE.match(line)
                if m:
                    fields[m.group("key")] = m.group("value")
    except OSError:
        pass
    return fields


def sanitize_component(name: str) -> str:
    """Safe as a filesystem path segment: no '/', '..', or NUL."""
    safe = re.sub(r"[\\/\x00]+", "_", name)
    if safe in ("", ".", ".."):
        safe = "_"
    return safe


def shard_for(username: str) -> str:
    ch = username[:1].lower()
    return ch if ch.isalnum() and ch.isascii() else "_"


def md_link_text(s: str) -> str:
    """Escape characters that would break Markdown `[link text]` syntax."""
    return s.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def build_url(base_url: str, thread_id: str, post_index: int) -> str:
    return f"{base_url}?t={thread_id}#post-{post_index}"


LAST_LINE_THREAD_ID_RE = re.compile(r"<!--t:(\d+)-->")


def last_line_thread_id(path: Path, chunk_size: int = 4096):
    """
    Equivalent of `tail -n 1 path`, then pull the thread id back out of its
    trailing `<!--t:ID-->` marker (if any) — used to tell whether this
    entry continues the same thread as the user's last recorded post,
    without keeping any in-memory state across lines. Reads backward from
    the end in chunks rather than the whole file, so this stays cheap even
    for a prolific user's file.
    """
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return None
            data = b""
            pos = file_size
            while pos > 0:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data
                if data.count(b"\n") > 1:
                    break
            lines = [ln for ln in data.split(b"\n") if ln.strip()]
            if not lines:
                return None
            last_line = lines[-1].decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None
    m = LAST_LINE_THREAD_ID_RE.search(last_line)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full-log", required=True, type=Path, help="Path to a posted-by.log covering ALL users")
    ap.add_argument("--user", default=None, help="Restrict to one username (default: every user in the log)")
    ap.add_argument("--out", type=Path, default=Path("./by-user"), help="Output directory [default: ./by-user]")
    ap.add_argument(
        "--archive-path",
        type=Path,
        default=None,
        help="Path to the exported .md archive, for authoritative title:/forum: frontmatter "
        "(default: guess both from the thread's path, no archive access)",
    )
    ap.add_argument(
        "--base-url",
        default="https://ubuntuforums.popey.com/showthread.php",
        help="Thread URL base (thread id and #post-N are appended as ?t=<id>#post-<n>) "
        "[default: https://ubuntuforums.popey.com/showthread.php]",
    )
    ap.add_argument(
        "--clean",
        action="store_true",
        help="Delete the existing <out>/<shard>/<user>.md for --user before starting, "
        "so re-running doesn't append duplicate entries onto a prior run. Requires --user.",
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="If <out>/<shard>/<user>.md already exists for --user, exit immediately without "
        "reading the log at all — for batch runs where a user having a file already means "
        "they're done. Requires --user. Mutually exclusive with --clean.",
    )
    args = ap.parse_args()

    if args.clean and args.skip_existing:
        ap.error("--clean and --skip-existing are mutually exclusive")

    if args.clean:
        if not args.user:
            ap.error("--clean requires --user (it needs a specific username to know which file to delete)")
        user_dir = sanitize_component(args.user)
        clean_path = args.out / shard_for(user_dir) / f"{user_dir}.md"
        if clean_path.exists():
            clean_path.unlink()
            print(f"--clean: removed {clean_path}", file=sys.stderr)

    if args.skip_existing:
        if not args.user:
            ap.error("--skip-existing requires --user (it needs a specific username to know which file to check)")
        user_dir = sanitize_component(args.user)
        existing_path = args.out / shard_for(user_dir) / f"{user_dir}.md"
        if existing_path.exists():
            print(f"--skip-existing: {existing_path} already exists, skipping {args.user}", file=sys.stderr)
            return

    curr_file = None
    curr_count = 0
    seen_files = set()
    total = 0
    users_touched = set()
    frontmatter_cache = {}

    with args.full_log.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = LOG_LINE_RE.match(raw.rstrip("\n"))
            if not m:
                continue  # e.g. ag's trailing --stats summary lines
            post_m = POST_RE.match(m.group("content").strip())
            if not post_m:
                continue

            rel_file = m.group("file")
            if rel_file != curr_file:
                if curr_file is not None:
                    seen_files.add(curr_file)
                if rel_file in seen_files:
                    print(
                        f"warning: {rel_file} reappeared non-contiguously in the log; "
                        "post numbering for it may be wrong",
                        file=sys.stderr,
                    )
                curr_file = rel_file
                curr_count = 0
            curr_count += 1
            post_index = curr_count

            user = post_m.group("user")
            if args.user and user != args.user:
                continue

            thread_id = thread_id_for_name(rel_file)
            if not thread_id:
                continue

            title = title_from_filename(rel_file)
            category = category_from_path(rel_file)
            if args.archive_path is not None:
                fields = frontmatter_cache.get(rel_file)
                if fields is None:
                    fields = frontmatter_fields(args.archive_path / rel_file)
                    frontmatter_cache[rel_file] = fields
                title = fields.get("title") or title
                category = fields.get("forum") or category

            url = build_url(args.base_url, thread_id, post_index)
            date = post_m.group("date")

            user_dir = sanitize_component(user)
            path = args.out / shard_for(user_dir) / f"{user_dir}.md"
            path.parent.mkdir(parents=True, exist_ok=True)

            lines_to_write = []
            if post_index == 1:
                # The user's own thread-starting post: always a top-level entry.
                lines_to_write.append(
                    f"- {date} [{md_link_text(title)}]({url}) — {category} <!--t:{thread_id}-->\n"
                )
            else:
                # A followup. If the user has no top-level entry for this thread
                # yet (checked via the last line already written), synthesize an
                # unlinked parent line first so this entry has something to nest
                # under.
                if last_line_thread_id(path) != thread_id:
                    lines_to_write.append(
                        f"- {date} {md_link_text(title)} — {category} <!--t:{thread_id}-->\n"
                    )
                lines_to_write.append(f"  - {date} [comment {post_index}]({url}) <!--t:{thread_id}-->\n")

            with path.open("a", encoding="utf-8") as out_f:
                out_f.writelines(lines_to_write)

            total += 1
            users_touched.add(user)

    print(f"Wrote {total} lines across {len(users_touched)} user(s) under {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
