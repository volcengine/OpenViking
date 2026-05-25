#!/usr/bin/env python3
"""Generate benchmark data for grep bm25 vs fs comparison.

Produces ~80,000 markdown files (~50KB each, ~4GB total) in a 4-level
directory tree:
  level0: 10 dirs
  level1: 10 dirs per level0  (100 total)
  level2: 10 dirs per level1  (1,000 total)
  level3: 8 dirs per level2   (8,000 total)
  files:  10 per level3 dir   (80,000 total)

Target keywords appear in ~1% of files each, simulating a realistic
large-scale codebase where bm25 recall dramatically reduces search scope.
"""

import os
import random

BASE_DIR = os.path.expanduser("~/.openviking/data/benchmark")

# Directory tree — each level has independent dir count
LEVEL0_DIRS = 10
LEVEL1_DIRS = 10  # per level0 dir
LEVEL2_DIRS = 10  # per level1 dir
LEVEL3_DIRS = 8  # per level2 dir (flexible)
FILES_PER_DIR = 10  # per level3 dir

# Total: 10 * 10 * 10 * 8 * 10 = 80,000 files
# Size:  80,000 * 50KB ≈ 4GB
# Each top-level dir: 10*10*8*10*50KB = 400MB

TARGET_FILE_SIZE = 50000  # ~50KB

TARGET_KEYWORDS = ["VikingDB", "FullText", "bm25", "search_by_keywords"]

FILLER_WORDS = [
    "configuration",
    "deployment",
    "architecture",
    "implementation",
    "performance",
    "optimization",
    "integration",
    "middleware",
    "authentication",
    "authorization",
    "encryption",
    "validation",
    "monitoring",
    "logging",
    "caching",
    "serialization",
    "concurrency",
    "scalability",
    "reliability",
    "observability",
    "throughput",
    "latency",
    "availability",
    "consistency",
    "partitioning",
    "replication",
    "failover",
    "loadbalancing",
    "containerization",
    "orchestration",
    "provisioning",
    "lifecycle",
]

random.seed(42)

total_files = LEVEL0_DIRS * LEVEL1_DIRS * LEVEL2_DIRS * LEVEL3_DIRS * FILES_PER_DIR
keyword_hit_count = max(1, total_files // 100)  # 1% = 800 files per keyword
file_indices = list(range(total_files))
keyword_files = {}
for kw in TARGET_KEYWORDS:
    chosen = random.sample(file_indices, keyword_hit_count)
    keyword_files[kw] = set(chosen)


def generate_section(title_level):
    """Generate a markdown section with realistic filler content."""
    prefix = "#" * title_level
    title_words = random.sample(FILLER_WORDS, 3)
    title = f"{prefix} {' '.join(title_words).title()}\n\n"

    paragraphs = []
    for _ in range(random.randint(2, 5)):
        sentences = []
        for _ in range(random.randint(3, 8)):
            words = random.choices(FILLER_WORDS, k=random.randint(8, 15))
            sentences.append(" ".join(words).capitalize() + ".")
        paragraphs.append(" ".join(sentences))

    return title + "\n\n".join(paragraphs) + "\n\n"


def generate_file(file_idx):
    """Generate a ~50KB markdown file with 3-5 h1 sections, each with 5-10 h2 sections."""
    parts = []
    num_h1 = random.randint(3, 5)
    for _ in range(num_h1):
        parts.append(generate_section(1))
        num_h2 = random.randint(5, 10)
        for _ in range(num_h2):
            parts.append(generate_section(2))

    # Inject target keyword if this file is selected
    for kw, indices in keyword_files.items():
        if file_idx in indices:
            injection = (
                f"\nThis module provides {kw} integration for advanced search capabilities. "
                f"The {kw} feature enables efficient keyword-based retrieval across large datasets.\n\n"
            )
            parts[2] = parts[2] + injection  # after first h1 + first h2

    content = "".join(parts)
    # Pad to target size if needed
    if len(content) < TARGET_FILE_SIZE:
        padding_parts = []
        while len("".join(padding_parts)) < TARGET_FILE_SIZE - len(content):
            words = random.choices(FILLER_WORDS, k=20)
            padding_parts.append(" ".join(words).capitalize() + ".\n")
        content += "\n\n## Appendix\n\n" + "".join(padding_parts)

    return content[:TARGET_FILE_SIZE]


print(f"Generating {total_files} markdown files under {BASE_DIR}...")
print(
    f"  Tree: level0={LEVEL0_DIRS} x level1={LEVEL1_DIRS} x level2={LEVEL2_DIRS} x level3={LEVEL3_DIRS}"
)
print(f"  Files per leaf dir: {FILES_PER_DIR}")
print(f"  Target keywords: {TARGET_KEYWORDS}")
print(
    f"  Each keyword appears in ~{keyword_hit_count} files out of {total_files} "
    f"(~{keyword_hit_count / total_files * 100:.1f}%)"
)
print(f"  Estimated total size: ~{total_files * TARGET_FILE_SIZE / 1e9:.1f} GB")

file_idx = 0
os.makedirs(BASE_DIR, exist_ok=True)

for i0 in range(LEVEL0_DIRS):
    d0 = os.path.join(BASE_DIR, f"level0_{i0:02d}")
    os.makedirs(d0, exist_ok=True)
    for i1 in range(LEVEL1_DIRS):
        d1 = os.path.join(d0, f"level1_{i1:02d}")
        os.makedirs(d1, exist_ok=True)
        for i2 in range(LEVEL2_DIRS):
            d2 = os.path.join(d1, f"level2_{i2:02d}")
            os.makedirs(d2, exist_ok=True)
            for i3 in range(LEVEL3_DIRS):
                d3 = os.path.join(d2, f"level3_{i3:02d}")
                os.makedirs(d3, exist_ok=True)
                for f in range(FILES_PER_DIR):
                    filepath = os.path.join(d3, f"doc_{f:04d}.md")
                    content = generate_file(file_idx)
                    with open(filepath, "w") as fh:
                        fh.write(content)
                    file_idx += 1
                    if file_idx % 10000 == 0:
                        print(f"  ... {file_idx} files written")

print(f"Done! {file_idx} files generated under {BASE_DIR}")
