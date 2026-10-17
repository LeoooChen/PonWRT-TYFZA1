#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Extract one version from RELEASE_NOTES.md for a GitHub Release."""
import argparse
from pathlib import Path


def extract_notes(text, tag):
    version = tag.removeprefix('v')
    lines = text.splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if line.startswith('## ')]
    matches = [i for i in headings if lines[i][3:].strip() == version]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one changelog section for {tag}; found {len(matches)}')
    start = matches[0]
    end = next((i for i in headings if i > start), len(lines))
    if not ''.join(lines[start + 1:end]).strip():
        raise ValueError(f'Changelog section for {tag} is empty')
    return ''.join(lines[start:end]).strip() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='Release version, with or without a leading v')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'RELEASE_NOTES.md'
    try:
        notes = extract_notes(path.read_text(encoding='utf-8'), args.tag)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(notes, end='')


if __name__ == '__main__':
    main()
