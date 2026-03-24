#!/usr/bin/env python3
from datasets import load_dataset

print("Loading Qasper dataset...")
try:
    dataset = load_dataset("allenai/qasper")
    print("Dataset loaded successfully!")
    print("Splits:", dataset.keys())
    
    for split in dataset.keys():
        print(f"\n{split} split: {len(dataset[split])} samples")
        print("First sample keys:", list(dataset[split][0].keys()))
except Exception as e:
    print(f"Error: {e}")
