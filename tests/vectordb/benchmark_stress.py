import argparse
import os
import random
import shutil
import threading
import time

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

# --- Configuration ---
DEFAULT_DIM = 128
DEFAULT_DB_PATH = "./benchmark_stress_db"
CATEGORIES = ["news", "sports", "finance", "tech", "entertainment"]
TAGS = ["hot", "new", "archived", "premium", "public"]


def calculate_mean(data):
    if not data:
        return 0.0
    return sum(data) / len(data)


class StressStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.insert_count = 0
        self.search_count = 0
        self.delete_count = 0
        self.insert_latency = []
        self.search_latency = []
        self.delete_latency = []
        self.start_time = time.time()

    def record_insert(self, lat):
        with self.lock:
            self.insert_count += 1
            self.insert_latency.append(lat)

    def record_search(self, lat):
        with self.lock:
            self.search_count += 1
            self.search_latency.append(lat)

    def record_delete(self, lat):
        with self.lock:
            self.delete_count += 1
            self.delete_latency.append(lat)

    def report(self):
        with self.lock:
            duration = time.time() - self.start_time
            print(f"\n--- Stress Test Report (Duration: {duration:.2f}s) ---")
            print(
                f"Insert: {self.insert_count} ops, {self.insert_count / duration:.2f} OPS, Avg Latency: {calculate_mean(self.insert_latency):.4f}s"
            )
            print(
                f"Search: {self.search_count} ops, {self.search_count / duration:.2f} OPS, Avg Latency: {calculate_mean(self.search_latency):.4f}s"
            )
            print(
                f"Delete: {self.delete_count} ops, {self.delete_count / duration:.2f} OPS, Avg Latency: {calculate_mean(self.delete_latency):.4f}s"
            )
            print(f"Total Ops: {self.insert_count + self.search_count + self.delete_count}")
            print("------------------------------------------------")


def generate_random_vector(dim):
    return [random.random() for _ in range(dim)]


def generate_random_sparse_vector():
    # Random sparse vector: few random terms with weights
    terms = ["term" + str(i) for i in range(100)]  # Vocabulary of 100 terms
    num_terms = random.randint(1, 10)
    selected = random.sample(terms, num_terms)
    return {term: random.random() for term in selected}


def setup_collection(path: str, dim: int, enable_sparse: bool):
    if os.path.exists(path):
        shutil.rmtree(path)

    fields = [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": dim},
        {"FieldName": "category", "FieldType": "string"},
        {"FieldName": "score", "FieldType": "float32"},
        {"FieldName": "is_active", "FieldType": "bool"},
        {"FieldName": "tags", "FieldType": "list<string>"},
    ]

    if enable_sparse:
        fields.append({"FieldName": "sparse_vec", "FieldType": "sparse_vector"})

    meta_data = {
        "CollectionName": "stress_collection",
        "Description": "A collection for violent stress testing",
        "Fields": fields,
    }

    col = get_or_create_local_collection(meta_data=meta_data, path=path)

    # Create multiple indexes to stress the index manager
    vector_index_config = {
        "IndexType": "flat_hybrid" if enable_sparse else "flat",
        "Distance": "l2",
        # "FieldName": "vector" # FieldName is not in Pydantic schema for VectorIndexConfig
    }
    if enable_sparse:
        vector_index_config["EnableSparse"] = True

    col.create_index("idx_vector", {"IndexName": "idx_vector", "VectorIndex": vector_index_config})

    # col.create_index("idx_category", {
    #     "IndexName": "idx_category",
    #     "ScalarIndex": ["category"]
    # })

    return col


def worker_insert(
    col: Collection,
    stats: StressStats,
    start_id: int,
    count: int,
    batch_size: int,
    dim: int,
    enable_sparse: bool,
    stop_event: threading.Event = None,
):
    current_id = start_id
    end_id = start_id + count

    while current_id < end_id:
        if stop_event and stop_event.is_set():
            break

        batch_data = []
        real_batch_size = min(batch_size, end_id - current_id)
        for i in range(real_batch_size):
            item = {
                "id": current_id + i,
                "vector": generate_random_vector(dim),
                "category": random.choice(CATEGORIES),
                "score": random.random(),
                "is_active": random.choice([True, False]),
                "tags": random.sample(TAGS, k=random.randint(1, 3)),
            }
            if enable_sparse:
                item["sparse_vec"] = generate_random_sparse_vector()
            batch_data.append(item)

        t0 = time.time()
        try:
            col.upsert_data(batch_data)
            stats.record_insert(time.time() - t0)
        except Exception as e:
            print(f"[Insert] Error: {e}")

        current_id += real_batch_size
        # time.sleep(0.01) # Small sleep to prevent total lock starvation if any


def worker_search(
    col: Collection,
    stats: StressStats,
    duration: int,
    dim: int,
    max_id: int,
    enable_sparse: bool,
    stop_event: threading.Event = None,
):
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event and stop_event.is_set():
            break

        t0 = time.time()
        try:
            # 1. Pure Vector Search
            if enable_sparse:
                sparse_q = generate_random_sparse_vector()
                col.search_by_vector(
                    "idx_vector", generate_random_vector(dim), limit=10, sparse_vector=sparse_q
                )
            else:
                col.search_by_vector("idx_vector", generate_random_vector(dim), limit=10)

            # 2. Filtered Vector Search (Complex)
            filter_cond = {
                "category": {"in": [random.choice(CATEGORIES)]},
                "score": {"gt": 0.5},
                "is_active": {"eq": True},
            }
            if enable_sparse:
                sparse_q = generate_random_sparse_vector()
                col.search_by_vector(
                    "idx_vector",
                    generate_random_vector(dim),
                    limit=10,
                    filters=filter_cond,
                    sparse_vector=sparse_q,
                )
            else:
                col.search_by_vector(
                    "idx_vector", generate_random_vector(dim), limit=10, filters=filter_cond
                )

            # 3. Fetch Data
            random_ids = [random.randint(0, max_id) for _ in range(5)]
            col.fetch_data(random_ids)

            stats.record_search(time.time() - t0)
        except Exception as e:
            print(f"[Search] Error: {e}")

        # time.sleep(0.005)


def worker_delete(
    col: Collection,
    stats: StressStats,
    duration: int,
    max_id: int,
    stop_event: threading.Event = None,
):
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event and stop_event.is_set():
            break

        t0 = time.time()
        try:
            # Randomly delete a small batch
            ids_to_del = [random.randint(0, max_id) for _ in range(3)]
            col.delete_data(ids_to_del)
            stats.record_delete(time.time() - t0)
        except Exception as e:
            print(f"[Delete] Error: {e}")

        time.sleep(0.1)  # Delete less frequently than insert/search


def run_stress_test():
    parser = argparse.ArgumentParser(description="Violent Vectordb Stress Test")
    parser.add_argument("--path", type=str, default=DEFAULT_DB_PATH, help="DB Path")
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM, help="Vector Dimension")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--initial_data", type=int, default=10000, help="Initial data count")
    parser.add_argument("--insert_threads", type=int, default=4, help="Number of insert threads")
    parser.add_argument("--search_threads", type=int, default=8, help="Number of search threads")
    parser.add_argument("--delete_threads", type=int, default=2, help="Number of delete threads")
    parser.add_argument("--enable_sparse", action="store_true", help="Enable sparse vector support")

    args = parser.parse_args()

    print(
        f"=== Starting Stress Test (Dim={args.dim}, Duration={args.duration}s, Sparse={args.enable_sparse}) ==="
    )
    print(f"DB Path: {args.path}")

    col = setup_collection(args.path, args.dim, args.enable_sparse)
    stats = StressStats()

    # Preload data
    print(f"Preloading {args.initial_data} items...")
    worker_insert(
        col,
        stats,
        0,
        args.initial_data,
        batch_size=100,
        dim=args.dim,
        enable_sparse=args.enable_sparse,
    )
    print("Preload complete.")

    # Reset stats for the actual stress phase
    stats = StressStats()

    # Define ID ranges for inserts to avoid massive collisions, though collisions are also fun for testing
    # Let's make inserts append new data
    start_id_base = args.initial_data
    items_per_thread = 1000000  # Large enough to keep running

    threads = []

    # Stop Event
    stop_event = threading.Event()

    # Start Insert Threads
    for i in range(args.insert_threads):
        t_start = start_id_base + i * items_per_thread
        t = threading.Thread(
            target=worker_insert,
            args=(
                col,
                stats,
                t_start,
                items_per_thread,
                50,
                args.dim,
                args.enable_sparse,
                stop_event,
            ),
        )
        t.daemon = True
        t.start()
        threads.append(t)

    # Start Search Threads
    # They will query random IDs up to current approximate max.
    # We estimate max_id conservatively to avoid too many misses, but misses are okay.
    estimated_max_id = start_id_base + (items_per_thread * args.insert_threads)
    for _ in range(args.search_threads):
        t = threading.Thread(
            target=worker_search,
            args=(
                col,
                stats,
                args.duration,
                args.dim,
                estimated_max_id,
                args.enable_sparse,
                stop_event,
            ),
        )
        t.daemon = True
        t.start()
        threads.append(t)

    # Start Delete Threads
    for _ in range(args.delete_threads):
        t = threading.Thread(
            target=worker_delete, args=(col, stats, args.duration, estimated_max_id, stop_event)
        )
        t.daemon = True
        t.start()
        threads.append(t)

    # Monitor loop
    start_time = time.time()
    try:
        while time.time() - start_time < args.duration:
            time.sleep(1)
            if not any(t.is_alive() for t in threads):
                break
    except KeyboardInterrupt:
        print("\nInterrupted by user")

    print("\nStopping threads...")
    stop_event.set()

    # Wait for all threads to finish
    for t in threads:
        t.join(timeout=2.0)

    # Threads are daemon, will die when main exits, but let's give a quick report first
    stats.report()

    # Cleanup
    print("Cleaning up...")
    col.close()
    if os.path.exists(args.path):
        shutil.rmtree(args.path)
    print("Done.")


if __name__ == "__main__":
    run_stress_test()
