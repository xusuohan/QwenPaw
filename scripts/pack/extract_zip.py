#!/usr/bin/env python3
"""Extract a zip archive with Windows long-path support."""

import os
import sys
import zipfile


def extract(archive: str, dest: str) -> None:
    if sys.platform == "win32":
        dest = os.path.abspath(dest)
        if not dest.startswith("\\\\?\\"):
            dest = "\\\\?\\" + dest

    with zipfile.ZipFile(archive, "r") as z:
        entries = z.namelist()
        total = len(entries)
        print(f"[extract_zip] Extracting {total} entries...")

        for i, entry in enumerate(entries):
            if (i + 1) % 1000 == 0:
                print(f"[extract_zip] Progress: {i + 1}/{total}")

            # Convert forward slashes to backslashes - required for \\?\ prefix
            if sys.platform == "win32":
                entry_path = entry.replace("/", "\\")
            else:
                entry_path = entry

            target = os.path.join(dest, entry_path)

            if entry.endswith("/"):
                os.makedirs(target, exist_ok=True)
            else:
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                # Manual extract with stream (z.extract fails with \\?\ prefix + /).
                # 1 MB buffer is the sweet spot on Windows: large enough to
                # amortize per-write syscall/MFT overhead, small enough to
                # stay in the L2 cache for the common small-file case.
                with z.open(entry) as src, open(target, "wb") as dst:
                    buf = src.read(1 << 20)
                    while buf:
                        dst.write(buf)
                        buf = src.read(1 << 20)

        print(f"[extract_zip] Complete: {total} entries extracted")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <archive.zip> <dest_dir>", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])
