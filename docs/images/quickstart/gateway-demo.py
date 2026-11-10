"""Fictional OpenViking gateway trial. Uses Python 3 standard library only."""
import argparse
import json
import os
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=['capture', 'recall'])
parser.add_argument('--language', choices=['zh', 'en'], default='zh')
parser.add_argument('--run', default='v1', help='Use a fresh value for another experiment')
args = parser.parse_args()
base = os.environ.get('GATEWAY_URL', 'http://127.0.0.1:1933/gateway').rstrip('/')
key = os.environ.get('GATEWAY_KEY')
model = os.environ.get('GATEWAY_MODEL')
if not key or not model:
    parser.error('Set GATEWAY_KEY and GATEWAY_MODEL first.')

def ask(messages, session):
    request = urllib.request.Request(
        base + '/v1/chat/completions',
        data=json.dumps({'model': model, 'messages': messages}).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'X-OpenViking-Session': session},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'Gateway returned HTTP {exc.code}; inspect its request log.') from None
    except (urllib.error.URLError, TimeoutError):
        raise SystemExit('Gateway request could not complete; check the URL and service log.') from None
    message = result['choices'][0]['message']
    print(message.get('content', ''))
    return {k: v for k, v in message.items() if k in {'role', 'content', 'reasoning_content'}}

if args.phase == 'capture':
    fact = ('请记住虚构山岚项目的约定：项目评审固定检查四项风险，所有报告使用 Markdown 格式，文件名为 mist-review.md。以后准备该项目的评审时请沿用。'
            if args.language == 'zh' else
            'Remember the fictional Mist project conventions: reviews check exactly four risks, all reports use Markdown, and the filename is mist-review.md. Follow these conventions for future project reviews.')
    messages = [{'role': 'user', 'content': fact}]
    session = 'gateway-mist-a-' + args.run
    messages.append(ask(messages, session))
    messages.append({'role': 'user', 'content': '谢谢。' if args.language == 'zh' else 'Thank you.'})
    ask(messages, session)
else:
    question = ('请按已保存的山岚项目约定，准备下次评审的检查清单，并给出交付文件名。'
                if args.language == 'zh' else
                'Use the saved Mist project conventions to prepare the next review checklist and give the delivery filename.')
    ask([{'role': 'user', 'content': question}], 'gateway-mist-b-' + args.run)
