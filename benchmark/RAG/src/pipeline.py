import os
import json
import time
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent))

from adapters.base import BaseAdapter
from core.logger import get_logger
from core.vector_store import VikingStoreWrapper
from core.monitor import BenchmarkMonitor
from core.metrics import MetricsCalculator
from core.judge_util import llm_grader


class BenchmarkPipeline:
    def __init__(self, config, adapter: BaseAdapter, vector_db: VikingStoreWrapper, llm):
        self.config = config
        self.adapter = adapter
        self.db = vector_db
        self.llm = llm
        self.logger = get_logger()
        self.monitor = BenchmarkMonitor()
        
        self.output_dir = self.config['paths']['output_dir']
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
        self.generated_file = os.path.join(self.output_dir, "generated_answers.json")
        self.eval_file = os.path.join(self.output_dir, "qa_eval_detailed_results.json")
        self.report_file = os.path.join(self.output_dir, "benchmark_metrics_report.json")
        
        self.metrics_summary = {
            "insertion": {"time": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0},
            "deletion": {"time": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0}
        }

    def run_generation(self):
        """Step 1: Data Preparation"""
        self.logger.info(">>> Stage: Ingestion & Generation")
        skip_ingestion = self.config['execution'].get('skip_ingestion', False)
        doc_dir = self.config['paths'].get('doc_output_dir')
        if not doc_dir:
            doc_dir = os.path.join(self.output_dir, "docs")

        if skip_ingestion:
            self.logger.info(f"Skipping ingestion. Reusing existing vector index at: {self.db.store_path}")
            self.metrics_summary["insertion"] = {"time": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0}
        else:
            try:
                doc_info = self.adapter.data_prepare(doc_dir)
            except Exception as e:
                self.logger.exception(f"Data preparation failed: {e}")
                exit(1)

            ingest_workers = self.config['execution'].get('ingest_workers', 10)
            ingest_mode = self.config['execution'].get('ingest_mode', 'per_file')
            
            mode_desc = {
                'directory': 'Unified directory mode',
                'per_file': 'Per-file mode'
            }
            self.logger.info(f"Ingestion mode: {ingest_mode} ({mode_desc.get(ingest_mode, 'Unknown mode')})")
            self.logger.info(f"Number of documents: {len(doc_info)}")
            
            ingest_stats = self.db.ingest(
                doc_info, 
                max_workers=ingest_workers, 
                monitor=self.monitor,
                ingest_mode=ingest_mode
            )
            self.metrics_summary["insertion"] = ingest_stats
            self.logger.info(f"Insertion finished. Time: {ingest_stats['time']:.2f}s")

            self._update_report({
                "Insertion Efficiency (Total Dataset)": {
                    "Total Insertion Time (s)": self.metrics_summary["insertion"]["time"],
                    "Total Input Tokens": self.metrics_summary["insertion"]["input_tokens"],
                    "Total Output Tokens": self.metrics_summary["insertion"]["output_tokens"],
                    "Total Embedding Tokens": self.metrics_summary["insertion"].get("embedding_tokens", 0)
                }
            })
        
        samples = self.adapter.load_and_transform()    
        tasks = self._prepare_tasks(samples)
        results_map = {}
        max_workers = self.config['execution']['max_workers']
        task_errors = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(self._process_generation_task, task): task 
                for task in tasks
            }
            
            pbar = tqdm(total=len(tasks), desc="Generating Answers", unit="task")
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    res = future.result()
                    results_map[res['_global_index']] = res
                except Exception as e:
                    self.logger.error(f"Generation failed for task {task['id']}: {e}")
                    task_errors.append((task['id'], e))
                pbar.set_postfix(self.monitor.get_status_dict())
                pbar.update(1)
            pbar.close()

        if task_errors:
            first_id, first_err = task_errors[0]
            raise RuntimeError(
                f"Generation failed for {len(task_errors)} tasks; first failure task_id={first_id}: {type(first_err).__name__}: {first_err}"
            ) from first_err

        sorted_results = [results_map[i] for i in sorted(results_map.keys())]
        dataset_name = self.config.get('dataset_name', 'Unknown_Dataset')
        save_data = {
            "summary": {"dataset": dataset_name, "total_queries": len(sorted_results)},
            "results": sorted_results
        }
        total = len(sorted_results)
        if total > 0:
            self._update_report({
                    "Query Efficiency (Average Per Query)": {
                        "Average Retrieval Time (s)": sum(r['retrieval']['latency_sec'] for r in sorted_results) / total,
                        "Average Input Tokens": sum(r['token_usage']['total_input_tokens'] for r in sorted_results) / total,
                        "Average Output Tokens": sum(r['token_usage']['llm_output_tokens'] for r in sorted_results) / total,
                    }
                }
            )
        with open(self.generated_file, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)

    def run_evaluation(self):
        """Step 4: Evaluation"""
        self.logger.info(">>> Stage: Evaluation")

        if not os.path.exists(self.generated_file):
            self.logger.error("Generated answers file not found.")
            return

        with open(self.generated_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("results", [])

        eval_items = items
        eval_results_map = {}
        
        with ThreadPoolExecutor(max_workers=self.config['execution']['max_workers']) as executor:
            future_to_item = {
                executor.submit(self._process_evaluation_task, item): item 
                for item in eval_items
            }
            
            pbar = tqdm(total=len(eval_items), desc="Evaluating", unit="item")
            for future in as_completed(future_to_item):
                try:
                    res = future.result()
                    eval_results_map[res['_global_index']] = res
                except Exception as e:
                    self.logger.error(f"Evaluation failed: {e}")
                pbar.update(1)
            pbar.close()

        eval_records = list(eval_results_map.values())
        total = len(eval_records)

        with open(self.eval_file, "w", encoding="utf-8") as f:
            json.dump({"results": eval_records}, f, indent=2, ensure_ascii=False)

        if total > 0:
            self._update_report({
                "Dataset": self.config.get('dataset_name', 'Unknown_Dataset'),
                "Total Queries Evaluated": total,
                "Performance Metrics": {
                    "Average F1 Score": sum(r['metrics']['F1'] for r in eval_records) / total,
                    "Average Recall": sum(r['metrics']['Recall'] for r in eval_records) / total,
                    "Average Accuracy (Hit 0-4)": sum(r['metrics']['Accuracy'] for r in eval_records) / total,
                    "Average Accuracy (normalization)": (sum(r['metrics']['Accuracy'] for r in eval_records) / total)/4,
                }
            })

    def run_deletion(self):
        """Step 5: Cleanup"""
        self.logger.info(">>> Stage: Deletion")
        start_time = time.time()
        self.db.clear()
        duration = time.time() - start_time
        self.metrics_summary["deletion"] = {"time": duration, "input_tokens": 0, "output_tokens": 0}
        self.logger.info(f"Deletion finished. Time: {duration:.2f}s")

        self._update_report({
            "Deletion Efficiency (Total Dataset)": {
                "Total Deletion Time (s)": duration,
                "Total Input Tokens": 0,
                "Total Output Tokens": 0
            }
        })

    def _prepare_tasks(self, samples):
        tasks = []
        global_idx = 0
        max_queries = self.config['execution'].get('max_queries')
        for sample in samples:
            for qa in sample.qa_pairs:
                if max_queries is not None and global_idx >= max_queries:
                    break
                tasks.append({"id": global_idx, "sample_id": sample.sample_id, "qa": qa})
                global_idx += 1
            if max_queries is not None and global_idx >= max_queries:
                break
        return tasks

    def _process_generation_task(self, task):
        self.monitor.worker_start()
        try:
            qa = task['qa']
            
            t0 = time.time()
            # Get retrieval instruction from config, default to empty
            retrieval_instruction = self.config['execution'].get('retrieval_instruction', '')
            # Build enhanced query with instruction if provided
            if retrieval_instruction:
                enhanced_query = f"{retrieval_instruction} {qa.question}"
                self.logger.debug(f"[Query-{task['id']}] Using retrieval instruction: {retrieval_instruction}")
                self.logger.debug(f"[Query-{task['id']}] Enhanced query: {enhanced_query}")
            else:
                enhanced_query = qa.question
                self.logger.debug(f"[Query-{task['id']}] No retrieval instruction, using raw query")
            search_res = self.db.retrieve(query=enhanced_query, topk=self.config['execution']['retrieval_topk'])
            latency = time.time() - t0
            
            retrieved_texts = []
            retrieved_uris = []
            context_blocks = []
            
            for r in search_res.resources:
                retrieved_uris.append(r.uri)
                content = self.db.read_resource(r.uri) if getattr(r, 'level', 2) == 2 else f"{getattr(r, 'abstract', '')}\n{getattr(r, 'overview', '')}"
                retrieved_texts.append(content)
                clean = content[:8000]
                context_blocks.append(clean)
            
            recall = MetricsCalculator.check_recall(retrieved_texts, qa.evidence)
            
            full_prompt, meta = self.adapter.build_prompt(qa, context_blocks)
            
            ans_raw = self.llm.generate(full_prompt)

            ans = self.adapter.post_process_answer(qa, ans_raw, meta)

            in_tokens = self.db.count_tokens(full_prompt) + self.db.count_tokens(qa.question)
            out_tokens = self.db.count_tokens(ans)
            self.monitor.worker_end(tokens=in_tokens + out_tokens)
            
            self.logger.info(f"[Query-{task['id']}] Q: {qa.question[:30]}... | Recall: {recall:.2f} | Latency: {latency:.2f}s")

            return {
                "_global_index": task['id'], "sample_id": task['sample_id'], "question": qa.question,
                "gold_answers": qa.gold_answers, "category": str(qa.category), "evidence": qa.evidence,
                "retrieval": {"latency_sec": latency, "uris": retrieved_uris},
                "llm": {"final_answer": ans},
                "metrics": {"Recall": recall}, "token_usage": {"total_input_tokens": in_tokens, "llm_output_tokens": out_tokens}
            }
        except Exception:
            self.monitor.worker_end(success=False)
            raise

    def _process_evaluation_task(self, item):
        """
        Process a single evaluation task, computing F1 and Accuracy metrics.
        
        For multi-annotator scenarios (like Qasper dataset), a question may have multiple gold answers.
        Evaluation logic:
        - F1: Compute for each gold answer separately and take the maximum
        - Accuracy: Pass all gold answers to LLM at once for comprehensive judgment
        
        This correctly handles multi-annotator scenarios while maintaining compatibility with single-answer datasets (like Locomo).
        """
        ans, golds = item['llm']['final_answer'], item['gold_answers']
        
        f1 = max((MetricsCalculator.calculate_f1(ans, gt) for gt in golds), default=0.0)
        
        dataset_name = self.config.get('dataset_name', 'Unknown_Dataset')
        
        eval_record = {
            "score": 0.0,
            "reasoning": "",
            "prompt_type": ""
        }
        
        try:
            eval_res = llm_grader(
                self.llm.llm, 
                self.config['llm']['model'], 
                item['question'], 
                golds,
                ans,
                dataset_name=dataset_name
            )
            eval_record = eval_res
                
        except Exception as e:
            self.logger.error(f"Grader error: {e}")
            
        if MetricsCalculator.check_refusal(ans) and any(MetricsCalculator.check_refusal(gt) for gt in golds):
            f1 = 1.0
            eval_record["score"] = 4.0
            eval_record["reasoning"] = "System successfully identified Unanswerable/Refusal condition."
            eval_record["prompt_type"] = "Heuristic_Refusal_Check"

        acc = eval_record["score"]

        item["metrics"].update({"F1": f1, "Accuracy": acc})
        
        item["llm_evaluation"] = {
            "prompt_used": eval_record["prompt_type"],
            "reasoning": eval_record["reasoning"],
            "normalized_score": acc
        }

        detailed_info = (
            f"\n" + "="*60 +
            f"\n[Query ID]: {item['_global_index']}"
            f"\n[Question]: {item['question']}"
            f"\n[Retrieved URIs]: {item['retrieval'].get('uris', [])}"
            f"\n[LLM Answer]: {ans}"
            f"\n[Gold Answer]: {golds}"
            f"\n[Metrics]: {item['metrics']}"
            f"\n[LLM Judge Reasoning]: {eval_record['reasoning']}"
            f"\n" + "="*60
        )
        self.logger.info(detailed_info)
        return item

    def _update_report(self, data):
        """Read existing report, merge new data, and write back"""
        report = {}
        if os.path.exists(self.report_file):
            with open(self.report_file, "r", encoding="utf-8") as f:
                try:
                    report = json.load(f)
                except json.JSONDecodeError:
                    report = {}
        report.update(data)
        with open(self.report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)
        self.logger.info(f"Report updated -> {self.report_file}")
