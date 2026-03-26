#!/usr/bin/env python3
"""
Translates a CSV file from English to Chinese using deep-translator library.
"""

import csv
from deep_translator import GoogleTranslator
import time

def translate_text(text, target_lang='zh-CN'):
    """Translate text to target language."""
    if not text or text.strip() == '':
        return text

    retry_count = 0
    max_retries = 3

    while retry_count < max_retries:
        try:
            translator = GoogleTranslator(source='en', target=target_lang)
            translation = translator.translate(text)
            print(f"Translated: {text[:30]}... -> {translation[:30]}...")
            return translation
        except Exception as e:
            retry_count += 1
            print(f"Error translating text (attempt {retry_count} of {max_retries}): {text[:50]}...")
            print(f"Error: {e}")
            time.sleep(1)  # Wait 1 second before retrying

    return text

def translate_csv(input_file, output_file):
    """Translate CSV file from English to Chinese."""
    translated_rows = []

    print(f"Reading input file: {input_file}")

    with open(input_file, 'r', encoding='utf-8') as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames

        print(f"Found {len(fieldnames)} fields: {fieldnames}")

        for i, row in enumerate(reader, start=1):
            print(f"\nProcessing row {i}: {row.get('sample_id')}")
            translated_row = row.copy()

            # Translate question, answer, response, reasoning fields
            if 'question' in translated_row:
                translated_row['question'] = translate_text(translated_row['question'])
            if 'answer' in translated_row:
                translated_row['answer'] = translate_text(translated_row['answer'])
            if 'response' in translated_row:
                translated_row['response'] = translate_text(translated_row['response'])
            if 'reasoning' in translated_row:
                translated_row['reasoning'] = translate_text(translated_row['reasoning'])

            translated_rows.append(translated_row)

    print(f"\nTranslation completed for {len(translated_rows)} rows")

    # Translate fieldnames to Chinese
    translated_fieldnames = []
    for field in fieldnames:
        if field == 'sample_id':
            translated_fieldnames.append('样本ID')
        elif field == 'question':
            translated_fieldnames.append('问题')
        elif field == 'answer':
            translated_fieldnames.append('答案')
        elif field == 'response':
            translated_fieldnames.append('响应')
        elif field == 'token_usage':
            translated_fieldnames.append('令牌使用量')
        elif field == 'time_cost':
            translated_fieldnames.append('时间成本')
        elif field == 'iteration':
            translated_fieldnames.append('迭代次数')
        elif field == 'tools_used_names':
            translated_fieldnames.append('使用的工具名称')
        elif field == 'result':
            translated_fieldnames.append('结果')
        elif field == 'reasoning':
            translated_fieldnames.append('推理过程')
        else:
            translated_fieldnames.append(field)

    # Write translated CSV file
    print(f"Writing output file: {output_file}")

    with open(output_file, 'w', encoding='utf-8', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=translated_fieldnames)
        writer.writeheader()

        for row in translated_rows:
            translated_row = {}
            for original_field, translated_field in zip(fieldnames, translated_fieldnames):
                translated_row[translated_field] = row[original_field]
            writer.writerow(translated_row)

    print(f"Translated CSV file saved to: {output_file}")

if __name__ == "__main__":
    input_csv = "/Users/bytedance/workspace/openviking/result/locomo_result_multi_read_all.csv"
    output_csv = "/Users/bytedance/workspace/openviking/result/locomo_result_multi_read_all_cn.csv"

    translate_csv(input_csv, output_csv)
