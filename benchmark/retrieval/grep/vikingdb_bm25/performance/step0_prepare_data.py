#!/usr/bin/env python3
"""Step 0 (Performance): Prepare synthetic benchmark data for grep testing.

Reads a source text file (ai_wiki.txt), replicates it across configurable
directories and files, and injects target words at specified probabilities
for retrieval testing.

Directory layout:
  <output>/dir_000/wiki_000.txt ... dir_000/wiki_999.txt
  <output>/dir_001/wiki_000.txt ... dir_001/wiki_999.txt
  ...

Each dir_xxx contains 1000 files. Default: 200 directories (200,000 files).

Target words are injected by replacing a random word in the text.
All target words must NOT exist in the original source text.

Default target words and probabilities (15 words, 5 tiers):
  1%     : heliofract, prismcache, fluxkernel
  0.1%   : auroracode, kiteshade, glyphvector
  0.1%   : cortexmint, latticewave, spiralsync
  0.05%  : ripplehash, embertrace, novaframe
  0.01%  : zephyrloom, quartzrelay, nebulaindex

Usage:
  python3 step0_prepare_data.py
  python3 step0_prepare_data.py --num-dirs 50 --seed 42
  python3 step0_prepare_data.py --start-dir 100 --num-dirs 100  # append dir_100..dir_199
"""

from __future__ import annotations

import argparse
import os
import random
import re
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(SCRIPT_DIR, "..", "ai_wiki.txt")
DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/synthetic")
FILES_PER_DIR = 1000

TARGET_GROUPS: list[tuple[float, list[str]]] = [
    (0.01, ["heliofract", "prismcache", "fluxkernel"]),
    (0.001, ["auroracode", "kiteshade", "glyphvector"]),
    (0.001, ["cortexmint", "latticewave", "spiralsync"]),
    (0.0005, ["ripplehash", "embertrace", "novaframe"]),
    (0.0001, ["zephyrloom", "quartzrelay", "nebulaindex"]),
]


def verify_target_words(text: str) -> None:
    """Verify that no target word appears in the source text."""
    text_lower = text.lower()
    conflicts = []
    for prob, words in TARGET_GROUPS:
        for word in words:
            if word.lower() in text_lower:
                conflicts.append((word, prob))
    if conflicts:
        for word, prob in conflicts:
            print(f"  CONFLICT: target word '{word}' (p={prob}) found in source text!")
        raise ValueError(
            f"{len(conflicts)} target word(s) already exist in source text. "
            "Choose different target words."
        )


def inject_word(text: str, target: str) -> str:
    """Replace a random word in the text with the target word."""
    words = list(re.finditer(r"\b\w+\b", text))
    if not words:
        return text
    match = random.choice(words)
    return text[: match.start()] + target + text[match.end() :]


def generate_dataset(
    source_text: str,
    output_dir: str,
    num_dirs: int,
    start_dir: int = 0,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed + start_dir)
    total_files = num_dirs * FILES_PER_DIR
    injection_stats = {word: 0 for _prob, words in TARGET_GROUPS for word in words}

    end_dir = start_dir + num_dirs - 1 if num_dirs > 0 else start_dir
    print(f"  Generating {num_dirs} dirs x {FILES_PER_DIR} files = {total_files} files")
    print(f"  Directory range: dir_{start_dir:03d} .. dir_{end_dir:03d}")
    print(f"  Output: {output_dir}")
    print()

    t0 = time.monotonic()

    for offset in range(num_dirs):
        dir_idx = start_dir + offset
        dir_name = f"dir_{dir_idx:03d}"
        dir_path = os.path.join(output_dir, dir_name)
        os.makedirs(dir_path, exist_ok=True)

        for file_idx in range(FILES_PER_DIR):
            file_name = f"wiki_{file_idx:03d}.txt"
            file_path = os.path.join(dir_path, file_name)
            content = source_text

            for prob, words in TARGET_GROUPS:
                for word in words:
                    if rng.random() < prob:
                        content = inject_word(content, word)
                        injection_stats[word] += 1

            with open(file_path, "w") as f:
                f.write(content)

        if (offset + 1) % 10 == 0 or offset == num_dirs - 1:
            elapsed = time.monotonic() - t0
            print(f"  [{offset + 1}/{num_dirs}] dirs created  ({elapsed:.1f}s)")

    elapsed = time.monotonic() - t0
    return {
        "total_files": total_files,
        "start_dir": start_dir,
        "num_dirs": num_dirs,
        "files_per_dir": FILES_PER_DIR,
        "elapsed_s": round(elapsed, 1),
        "injection_stats": injection_stats,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Step 0 (Performance): Prepare synthetic benchmark data"
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE, help=f"Source text file (default: {DEFAULT_SOURCE})"
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT, help=f"Output directory (default: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--num-dirs", type=int, default=200, help="Number of directories (default: 200)"
    )
    parser.add_argument(
        "--start-dir",
        type=int,
        default=0,
        help="Starting directory index for append/scale-out runs (default: 0)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()

    source = os.path.normpath(os.path.expanduser(args.source))
    output = os.path.expanduser(args.output)

    print("=" * 80)
    print("Step 0 (Performance): Prepare Synthetic Benchmark Data")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Output:   {output}")
    print(f"  StartDir: {args.start_dir}")
    print(f"  Dirs:     {args.num_dirs}")
    print(f"  Seed:     {args.seed}")
    print()

    if not os.path.isfile(source):
        print(f"ERROR: Source file not found: {source}")
        return

    with open(source) as f:
        source_text = f.read()

    print(f"  Source text size: {len(source_text):,} chars")
    print()
    print("  Verifying target words against source text...")
    try:
        verify_target_words(source_text)
    except ValueError as e:
        print(f"ERROR: {e}")
        return
    print("  All target words verified OK.")
    print()
    print("  Target words and injection probabilities:")
    for prob, words in sorted(TARGET_GROUPS, key=lambda item: item[0], reverse=True):
        pct = f"{prob * 100:.3f}%"
        print(f"    {pct:>8s} : {', '.join(words)}")
    print()

    summary = generate_dataset(
        source_text, output, args.num_dirs, start_dir=args.start_dir, seed=args.seed
    )

    print()
    print("=" * 80)
    print("Summary:")
    print(f"  Total files:  {summary['total_files']:,}")
    print(f"  Start dir:    {summary['start_dir']}")
    print(f"  Directories:  {summary['num_dirs']}")
    print(f"  Elapsed:      {summary['elapsed_s']}s")
    print()
    print("  Target word injection counts:")
    total_files = summary["total_files"]
    for prob, words in sorted(TARGET_GROUPS, key=lambda item: item[0], reverse=True):
        for word in words:
            actual = summary["injection_stats"][word]
            expected = total_files * prob
            pct = actual / total_files * 100 if total_files > 0 else 0
            print(
                f"    {word:<18s}  actual={actual:>6d}  "
                f"expected~{expected:>8.1f}  "
                f"rate={pct:.3f}%  (target={prob * 100:.3f}%)"
            )

    print()
    print(f"Data ready at: {output}")
    print("Next: run step1_add_resource.py to import into OpenViking")


if __name__ == "__main__":
    main()
