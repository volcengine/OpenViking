# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VLM processor for image and table understanding."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Union

from openviking.prompts import render_prompt
from openviking_cli.utils.extractor import ImageInfo, TableInfo
from openviking_cli.utils.llm import parse_json_from_response
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VLMResult:
    """VLM understanding result with L0/L1/L2 content."""

    abstract: str  # L0: Concise description
    overview: str  # L1: Detailed understanding
    detail_text: str  # L2: Full text replacement
    meta: Dict[str, Any] = None

    def __post_init__(self):
        if self.meta is None:
            self.meta = {}


@dataclass
class DocumentAnalysisResult:
    """Batch document analysis result."""

    document: Dict[str, Any]
    images: List[VLMResult]
    tables: List[VLMResult]
    sections: List[Dict[str, str]]


class VLMProcessor:
    """Processes images and tables using VLM with batch support."""

    def __init__(
        self,
        max_images_per_call: int = 10,
        max_sections_per_call: int = 20,
    ):
        """Initialize VLM processor."""
        self.max_images_per_call = max_images_per_call
        self.max_sections_per_call = max_sections_per_call

    def _get_vlm(self):
        """Get VLM singleton."""
        from openviking_cli.utils.config import get_openviking_config

        return get_openviking_config().vlm

    async def understand_image(
        self,
        image: Union[Path, bytes],
        context: str = "",
        instruction: str = "",
    ) -> VLMResult:
        """Understand a single image using VLM."""
        prompt = render_prompt(
            "vision.image_understanding",
            {
                "instruction": instruction or "Understand image content",
                "context": context[:500] if context else "No context",
            },
        )

        try:
            response = await self._get_vlm().get_vision_completion_async(
                prompt=prompt,
                images=[image],
            )

            data = parse_json_from_response(response)
            if data:
                return VLMResult(
                    abstract=data.get("abstract", "[Image]"),
                    overview=data.get("overview", ""),
                    detail_text=data.get("detail_text", "[Image content]"),
                )

        except Exception as e:
            logger.error(f"Error understanding image: {e}")

        return VLMResult(
            abstract="[Image]",
            overview="Image understanding failed",
            detail_text="[Image content]",
        )

    async def understand_table(
        self,
        table: TableInfo,
        instruction: str = "",
    ) -> VLMResult:
        """Understand a table, prioritizing raw data if available."""
        if table.has_structured_data():
            return self._understand_table_from_data(table, instruction)

        # Fallback: Use VLM
        return await self._understand_table_from_image(table, instruction)

    def _understand_table_from_data(
        self,
        table: TableInfo,
        instruction: str = "",
    ) -> VLMResult:
        """Generate VLMResult from structured table data."""
        raw_data = table.raw_data

        # Generate abstract from first row (usually headers)
        headers = raw_data[0] if raw_data else []
        abstract = f"Table: {', '.join(str(h) for h in headers[:3] if h)}..."

        # Generate overview
        overview_parts = []
        overview_parts.append(f"Table contains {table.rows} rows and {table.cols} columns of data.")

        if headers:
            overview_parts.append(f"Column names: {', '.join(str(h) for h in headers if h)}")

        # Add sample data
        if len(raw_data) > 1:
            overview_parts.append(f"Contains {len(raw_data) - 1} data records.")

        overview = " ".join(overview_parts)

        # Generate detail text (markdown table)
        detail_lines = []
        if headers:
            detail_lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            detail_lines.append("|" + "|".join(["---"] * len(headers)) + "|")

        for row in raw_data[1:]:
            detail_lines.append("| " + " | ".join(str(c) for c in row) + " |")

        detail_text = "\n".join(detail_lines)

        return VLMResult(
            abstract=abstract[:100],
            overview=overview[:500],
            detail_text=detail_text,
            meta={"source": "raw_data", "rows": table.rows, "cols": table.cols},
        )

    async def _understand_table_from_image(
        self,
        table: TableInfo,
        instruction: str = "",
    ) -> VLMResult:
        """Understand table from image using VLM."""
        prompt = render_prompt(
            "vision.table_understanding",
            {
                "instruction": instruction or "Understand table content",
                "context": table.context[:500] if table.context else "No context",
            },
        )

        try:
            response = await self._get_vlm().get_vision_completion_async(
                prompt=prompt,
                images=[table.path],
            )

            data = parse_json_from_response(response)
            if data:
                return VLMResult(
                    abstract=data.get("abstract", "[Table]"),
                    overview=data.get("overview", ""),
                    detail_text=data.get("detail_text", "[Table content]"),
                    meta={"source": "vlm"},
                )

        except Exception as e:
            logger.error(f"Error understanding table: {e}")

        return VLMResult(
            abstract="[Table]",
            overview="Table understanding failed",
            detail_text="[Table content]",
        )

    async def understand_page(
        self,
        image: Union[Path, bytes],
        page_num: int,
        instruction: str = "",
    ) -> VLMResult:
        """Understand a page image (for image-only PDFs)."""
        prompt = render_prompt(
            "vision.page_understanding",
            {
                "instruction": instruction or "Understand document content",
                "page_num": page_num,
            },
        )

        try:
            response = await self._get_vlm().get_vision_completion_async(
                prompt=prompt,
                images=[image],
            )

            data = parse_json_from_response(response)
            if data:
                return VLMResult(
                    abstract=data.get("abstract", f"Page {page_num}"),
                    overview=data.get("overview", ""),
                    detail_text=data.get("detail_text", f"[Page {page_num} content]"),
                    meta={
                        "page_num": page_num,
                        "has_title": data.get("has_title", False),
                        "title": data.get("title", ""),
                    },
                )

        except Exception as e:
            logger.error(f"Error understanding page {page_num}: {e}")

        return VLMResult(
            abstract=f"Page {page_num}",
            overview="Page understanding failed",
            detail_text=f"[Page {page_num} content]",
        )

    async def batch_understand_pages(
        self,
        images: List[Union[Path, bytes]],
        instruction: str = "",
        batch_size: int = 5,
        max_concurrency: int = 3,
    ) -> List[VLMResult]:
        """Batch understand multiple pages with concurrent processing."""
        import asyncio

        if not images:
            return []

        # Create batch tasks with semaphore for concurrency control
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process_batch(batch_start: int, batch: List) -> tuple:
            async with semaphore:
                results = await self._batch_understand_pages_single_call(
                    batch, instruction, batch_start
                )
                return batch_start, results

        # Run all batches concurrently
        tasks = [
            process_batch(i, images[i : i + batch_size]) for i in range(0, len(images), batch_size)
        ]
        batch_results = await asyncio.gather(*tasks)

        # Sort by batch_start and flatten
        batch_results.sort(key=lambda x: x[0])
        return [r for _, results in batch_results for r in results]

    async def _batch_understand_pages_single_call(
        self,
        images: List[Union[Path, bytes]],
        instruction: str,
        start_index: int,
    ) -> List[VLMResult]:
        """Single VLM call to understand multiple page images."""
        prompt = render_prompt(
            "vision.page_understanding_batch",
            {
                "page_count": len(images),
                "instruction": instruction or "Understand document content",
            },
        )

        response = await self._get_vlm().get_vision_completion_async(
            prompt=prompt,
            images=images,
        )

        data = parse_json_from_response(response)
        if not data or "pages" not in data:
            raise ValueError("Invalid VLM response: missing 'pages' field")

        results = []
        for page_info in data["pages"]:
            idx = page_info.get("index", len(results))
            page_num = start_index + idx + 1
            results.append(
                VLMResult(
                    abstract=page_info.get("abstract", f"Page {page_num}"),
                    overview=page_info.get("overview", ""),
                    detail_text=page_info.get("detail_text", ""),
                    meta={
                        "page_num": page_num,
                        "has_title": page_info.get("has_title", False),
                        "title": page_info.get("title", ""),
                        "semantic_name": page_info.get("semantic_name", f"page_{page_num}"),
                    },
                )
            )

        if len(results) != len(images):
            raise ValueError(f"VLM returned {len(results)} results, expected {len(images)}")

        return results

    async def batch_analyze_document(
        self,
        title: str,
        reason: str,
        instruction: str,
        content_preview: str,
        images: List[ImageInfo],
        tables: List[TableInfo],
        sections: List[Dict[str, str]],
        meta: Dict[str, Any] = None,
    ) -> DocumentAnalysisResult:
        """Batch analyze document with unified LLM call."""
        # Filter tables that need VLM (no raw data)
        vlm_tables = [t for t in tables if not t.has_structured_data()]

        # Check if we have images to process
        has_images = len(images) > 0 or len(vlm_tables) > 0

        if has_images:
            return await self._batch_analyze_with_vision(
                title, reason, instruction, content_preview, images, vlm_tables, sections, meta
            )
        else:
            return await self._batch_analyze_text_only(
                title, reason, instruction, content_preview, tables, sections, meta
            )

    async def _batch_analyze_with_vision(
        self,
        title: str,
        reason: str,
        instruction: str,
        content_preview: str,
        images: List[ImageInfo],
        tables: List[TableInfo],
        sections: List[Dict[str, str]],
        meta: Dict[str, Any],
    ) -> DocumentAnalysisResult:
        """Batch analyze with VLM support."""
        # Prepare images section
        images_section = ""
        if images:
            images_section = "\n".join(
                f"Image {i + 1}: Located on page {img.page + 1}"
                for i, img in enumerate(images[: self.max_images_per_call])
            )
        else:
            images_section = "No images require analysis"

        # Prepare tables section
        tables_section = ""
        if tables:
            tables_section = "\n".join(
                f"Table {i + 1}: Located on page {tbl.page + 1}"
                for i, tbl in enumerate(tables[: self.max_images_per_call])
            )
        else:
            tables_section = "No tables require analysis"

        # Prepare sections list
        sections_list = ""
        if sections:
            sections_list = "\n".join(
                f"Section {i + 1}: {sec.get('title', 'Untitled')}"
                for i, sec in enumerate(sections[: self.max_sections_per_call])
            )
        else:
            sections_list = "No sections require analysis"

        prompt = render_prompt(
            "vision.unified_analysis",
            {
                "title": title or "Unknown document",
                "instruction": instruction or "Understand document content",
                "reason": reason or "User added",
                "content_preview": content_preview[:2000]
                if content_preview
                else "No content preview",
                "image_count": len(images),
                "images_section": images_section,
                "table_count": len(tables),
                "tables_section": tables_section,
                "section_count": len(sections),
                "sections_list": sections_list,
            },
        )

        # Collect all images for VLM call
        all_images = []
        for img in images[: self.max_images_per_call]:
            all_images.append(img.path)
        for tbl in tables[: self.max_images_per_call]:
            all_images.append(tbl.path)

        try:
            if all_images:
                response = await self._get_vlm().get_vision_completion_async(
                    prompt=prompt,
                    images=all_images,
                )
            else:
                response = await self._get_vlm().get_completion_async(
                    prompt=prompt,
                )

            data = parse_json_from_response(response)
            if data:
                return self._parse_batch_result(data, images, tables, sections)

        except Exception as e:
            logger.error(f"Error in batch analysis: {e}")

        # Return default result on failure
        return DocumentAnalysisResult(
            document={"abstract": title, "overview": "", "meta_extracted": {}},
            images=[VLMResult("[Image]", "", "[Image content]") for _ in images],
            tables=[VLMResult("[Table]", "", "[Table content]") for _ in tables],
            sections=[{"abstract": s.get("title", ""), "overview": ""} for s in sections],
        )

    async def _batch_analyze_text_only(
        self,
        title: str,
        reason: str,
        instruction: str,
        content_preview: str,
        tables: List[TableInfo],
        sections: List[Dict[str, str]],
        meta: Dict[str, Any],
    ) -> DocumentAnalysisResult:
        """Batch analyze without VLM (text-only)."""
        # For tables with raw data, generate from data
        table_results = []
        for table in tables:
            if table.has_structured_data():
                result = self._understand_table_from_data(table, instruction)
            else:
                result = VLMResult(
                    "[Table]", "Cannot parse table (VLM not available)", "[Table content]"
                )
            table_results.append(result)

        # Simplified prompt for text-only analysis
        simplified_prompt = f"""Please analyze the following document and generate summary and section information.

Title: {title}
Reason for adding: {reason}
Processing instruction: {instruction}

Content preview:
{content_preview[:3000]}

Section list:
{chr(10).join(f"- {s.get('title', 'Untitled')}" for s in sections[: self.max_sections_per_call])}

Please output in JSON format:
{{
    "document": {{
        "abstract": "Document summary (no more than 100 characters)",
        "overview": "Document overview (no more than 500 characters)"
    }},
    "sections": [
        {{"index": 0, "abstract": "Section summary", "overview": "Section use case"}}
    ]
}}"""

        try:
            response = await self._get_vlm().get_completion_async(
                prompt=simplified_prompt,
            )

            data = parse_json_from_response(response)
            if data:
                doc_data = data.get("document", {})
                section_data = data.get("sections", [])

                return DocumentAnalysisResult(
                    document={
                        "abstract": doc_data.get("abstract", title),
                        "overview": doc_data.get("overview", ""),
                        "meta_extracted": {},
                    },
                    images=[],
                    tables=table_results,
                    sections=[
                        {
                            "abstract": s.get("abstract", ""),
                            "overview": s.get("overview", ""),
                        }
                        for s in section_data
                    ],
                )

        except Exception as e:
            logger.error(f"Error in text-only analysis: {e}")

        return DocumentAnalysisResult(
            document={"abstract": title, "overview": "", "meta_extracted": {}},
            images=[],
            tables=table_results,
            sections=[{"abstract": s.get("title", ""), "overview": ""} for s in sections],
        )

    def _parse_batch_result(
        self,
        data: Dict[str, Any],
        images: List[ImageInfo],
        tables: List[TableInfo],
        sections: List[Dict[str, str]],
    ) -> DocumentAnalysisResult:
        """Parse batch analysis result."""
        # Parse document info
        doc_data = data.get("document", {})
        document = {
            "abstract": doc_data.get("abstract", ""),
            "overview": doc_data.get("overview", ""),
            "meta_extracted": doc_data.get("meta_extracted", {}),
        }

        # Parse image results
        image_results = []
        image_data = data.get("images", [])
        for i, _ in enumerate(images):
            if i < len(image_data):
                img_info = image_data[i]
                result = VLMResult(
                    abstract=img_info.get("abstract", "[Image]"),
                    overview=img_info.get("overview", ""),
                    detail_text=img_info.get("detail_text", "[Image content]"),
                )
            else:
                result = VLMResult("[Image]", "", "[Image content]")
            image_results.append(result)

        # Parse table results
        table_results = []
        table_data = data.get("tables", [])
        for i, _ in enumerate(tables):
            if i < len(table_data):
                tbl_info = table_data[i]
                result = VLMResult(
                    abstract=tbl_info.get("abstract", "[Table]"),
                    overview=tbl_info.get("overview", ""),
                    detail_text=tbl_info.get("detail_text", "[Table content]"),
                )
            else:
                result = VLMResult("[Table]", "", "[Table content]")
            table_results.append(result)

        # Parse section results
        section_results = []
        section_data = data.get("sections", [])
        for i, sec in enumerate(sections):
            if i < len(section_data):
                sec_info = section_data[i]
                result = {
                    "abstract": sec_info.get("abstract", ""),
                    "overview": sec_info.get("overview", ""),
                }
            else:
                result = {"abstract": sec.get("title", ""), "overview": ""}
            section_results.append(result)

        return DocumentAnalysisResult(
            document=document,
            images=image_results,
            tables=table_results,
            sections=section_results,
        )

    async def filter_meaningful_images(
        self,
        images: List[tuple],  # [(image_data: bytes, context: str), ...]
        document_title: str = "",
        batch_size: int = 5,
    ) -> List[dict]:
        """Batch filter images to determine if they are meaningful."""
        if not images:
            return []

        results = []

        # Process in batches
        for batch_start in range(0, len(images), batch_size):
            batch = images[batch_start : batch_start + batch_size]
            batch_results = await self._filter_image_batch(batch, document_title)
            results.extend(batch_results)

        return results

    async def _filter_image_batch(
        self,
        batch: List[tuple],  # [(image_data: bytes, context: str), ...]
        document_title: str,
    ) -> List[dict]:
        """Filter a batch of images."""
        if len(batch) == 1:
            # Single image - use simple prompt
            return await self._filter_single_image(batch[0], document_title)

        # Multiple images - use batch prompt
        images_info = []
        image_data_list = []

        for i, (_img_data, context) in enumerate(batch):
            images_info.append(
                f"Image {i + 1}: Surrounding text: {context[:100] if context else 'No context'}"
            )
            image_data_list.append(_img_data)

        prompt = render_prompt(
            "vision.batch_filtering",
            {
                "document_title": document_title or "Unknown document",
                "image_count": len(batch),
                "images_info": "\n".join(images_info),
            },
        )

        try:
            response = await self._get_vlm().get_vision_completion_async(
                prompt=prompt,
                images=image_data_list,
            )

            data = parse_json_from_response(response)
            if data and "results" in data:
                batch_results = []
                for i, (_img_data, _context) in enumerate(batch):
                    if i < len(data["results"]):
                        result = data["results"][i]
                        batch_results.append(
                            {
                                "is_meaningful": result.get("is_meaningful", True),
                                "reason": result.get("reason", ""),
                                "image_type": result.get("image_type", "Unknown"),
                            }
                        )
                    else:
                        # Missing result, keep image by default
                        batch_results.append(
                            {
                                "is_meaningful": True,
                                "reason": "Result parsing incomplete",
                                "image_type": "Unknown",
                            }
                        )
                return batch_results

        except Exception as e:
            logger.error(f"Error filtering image batch: {e}")

        # On error, keep all images by default
        return [
            {"is_meaningful": True, "reason": "Filtering failed", "image_type": "Unknown"}
            for _ in batch
        ]

    async def _filter_single_image(
        self,
        image_info: tuple,  # (image_data: bytes, context: str)
        document_title: str,
    ) -> List[dict]:
        """Filter a single image."""
        img_data, context = image_info

        prompt = render_prompt(
            "vision.image_filtering",
            {
                "document_title": document_title or "Unknown document",
                "context": context[:500] if context else "No context",
            },
        )

        try:
            response = await self._get_vlm().get_vision_completion_async(
                prompt=prompt,
                images=[img_data],
            )

            data = parse_json_from_response(response)
            if data:
                return [
                    {
                        "is_meaningful": data.get("is_meaningful", True),
                        "reason": data.get("reason", ""),
                        "image_type": data.get("image_type", "Unknown"),
                    }
                ]

        except Exception as e:
            logger.error(f"Error filtering image: {e}")

        # On error, keep image by default
        return [{"is_meaningful": True, "reason": "Filtering failed", "image_type": "Unknown"}]
