#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that release bodies contain only the requested changelog section."""
from pathlib import Path
import subprocess
import sys
import unittest

from release_notes import extract_notes

PROJECT = Path(__file__).resolve().parents[1]
NOTES = """# Changelog

## v0.6.0-alpha

### Tested
- Current result.

## v0.5.0-alpha

- Previous change.

## v0.4.0-alpha

- Oldest change."""


class ReleaseNotesTest(unittest.TestCase):
    def test_first_middle_and_last_versions(self):
        for version, content in (
            ('0.6.0-alpha', '### Tested\n- Current result.'),
            ('0.5.0-alpha', '- Previous change.'),
            ('0.4.0-alpha', '- Oldest change.'),
        ):
            for tag in (version, 'v' + version):
                with self.subTest(tag=tag):
                    self.assertEqual(extract_notes(NOTES, tag), f'## v{version}\n\n{content}\n')

    def test_missing_or_partial_version(self):
        for tag in ('v0.7.0-alpha', 'v0.6.0', ''):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                extract_notes(NOTES, tag)

    def test_empty_section(self):
        for notes in ('## v0.6.0-alpha\n\n', '## v0.6.0-alpha\n\n## v0.5.0-alpha\n- Older.'):
            with self.subTest(notes=notes), self.assertRaises(ValueError):
                extract_notes(notes, 'v0.6.0-alpha')

    def test_duplicate_version(self):
        with self.assertRaises(ValueError):
            extract_notes(NOTES + '\n\n## v0.6.0-alpha\n- Duplicate.', 'v0.6.0-alpha')

    def test_unprefixed_heading_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_notes('## 0.6.0-alpha\n\n- Missing prefix.', 'v0.6.0-alpha')

    def test_repository_versions(self):
        notes = (PROJECT / 'RELEASE_NOTES.md').read_text(encoding='utf-8')
        for heading in (line for line in notes.splitlines() if line.startswith('## ')):
            with self.subTest(heading=heading):
                self.assertTrue(heading.startswith('## v'))
                body = extract_notes(notes, heading[3:])
                self.assertEqual([line for line in body.splitlines() if line.startswith('## ')], [heading])

    def test_cli(self):
        version = (PROJECT / 'VERSION').read_text().strip()
        command = [sys.executable, str(PROJECT / 'scripts/release_notes.py')]
        result = subprocess.run(command + ['v' + version], capture_output=True, text=True, cwd='/tmp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, extract_notes(
            (PROJECT / 'RELEASE_NOTES.md').read_text(encoding='utf-8'), version))
        result = subprocess.run(command + ['v999.0.0-missing'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('v999.0.0-missing', result.stderr)


if __name__ == '__main__':
    unittest.main()
